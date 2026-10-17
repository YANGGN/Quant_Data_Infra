"""Inspect incomplete historical windows and plan clean provider subranges.

Original rejected bytes remain evidence. This module never filters a response
into the canonical publisher: each clean subrange requires its own original,
strictly validated provider response. A nonempty rejected window is not an
empty historical boundary.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date,timedelta
import hashlib
from ..errors import ValidationError,ResourceLimitError
from ..json_codec import loads_strict
from ..market.stage12_incremental import _ROW_KEYS,_date_text,_require_text
from .collection_price_windows import HistoryPriceWindow,MAX_BYTES,MAX_ROWS,_parse_history_row,parse_history_response
from .selected_price_pipeline import RowsOnly

POLICY='quarantine_invalid_price_dates.v1'
@dataclass(frozen=True)
class WindowInspection:
    raw_rows:int
    valid_rows:int
    excluded:tuple
    clean_windows:tuple
    empty_ranges:tuple
    response_sha256:str

def inspect_window(window,response):
    scope={'symbol':window.symbol,'from':window.start,'to':window.end}
    # Reuse the strict response envelope/bounds without allowing invalid rows.
    if type(response.status) is not int or response.status!=200 or not isinstance(response.media_type,str) or response.media_type.split(';',1)[0].strip().lower()!='application/json':
        raise ValidationError('Quarantine requires a retained successful JSON response')
    if not isinstance(response.body,bytes) or len(response.body)>MAX_BYTES:raise ResourceLimitError('Historical response exceeds its byte bound')
    payload=loads_strict(response.body,max_bytes=MAX_BYTES)
    if not isinstance(payload,list) or len(payload)>MAX_ROWS:raise ValidationError('Historical response is not a bounded row list')
    dates=[];errors={};parsed={}
    for index,value in enumerate(payload):
        if not isinstance(value,dict) or set(value)!=_ROW_KEYS:raise ValidationError('Unknown historical row shape cannot be quarantined')
        if _require_text(value['symbol'],pointer='/symbol',maximum=32)!=window.symbol:raise ValidationError('Historical symbol mismatch cannot be quarantined')
        day=_date_text(value['date'],pointer='/date')
        if not window.start<=day<=window.end:raise ValidationError('Historical date is outside its request')
        dates.append(day)
        try:parsed[index]=_parse_history_row(RowsOnly(),value,index=index,expected_symbol=window.symbol)
        except ValidationError as error:errors.setdefault(day,set()).add(str(error))
    for day,count in Counter(dates).items():
        if count>1:errors.setdefault(day,set()).add('Duplicate provider date; all competing rows quarantined')
    if not errors:
        rows=parse_history_response(RowsOnly(),scope,response)
        return WindowInspection(len(payload),len(rows),(),(window,) if rows else (),() if rows else (window,),hashlib.sha256(response.body).hexdigest())
    # The same strict empty envelope validates timeout type/value constraints.
    from dataclasses import replace
    parse_history_response(RowsOnly(),scope,replace(response,body=b'[]'))
    excluded=tuple({'date':day,'source_rows':[i+1 for i,d in enumerate(dates) if d==day],'reasons':sorted(errors[day])} for day in sorted(errors))
    good_dates={day for day in dates if day not in errors};clean=[];empty=[];start=date.fromisoformat(window.start)
    def add(end):
        nonlocal start
        if start>end:return
        child=HistoryPriceWindow(window.symbol,start.isoformat(),end.isoformat())
        (clean if any(child.start<=d<=child.end for d in good_dates) else empty).append(child)
    for day in sorted(errors):
        cut=date.fromisoformat(day);add(cut-timedelta(days=1));start=cut+timedelta(days=1)
    add(date.fromisoformat(window.end))
    return WindowInspection(len(payload),len(good_dates),excluded,tuple(clean),tuple(empty),hashlib.sha256(response.body).hexdigest())


# Fixed host artifacts; no caller-selected paths or connection access.
from pathlib import Path
QUALITY_RECEIPT=Path('data/.operations/collection/daily-prices/history-quarantine.json')
AUDIT_RECEIPT=Path('data/.operations/collection/daily-prices/history-canonical-audit.json')

def validate_quarantined_completion(project_root,selection,activation,result,body):
    from ..errors import ConflictError
    from .selected_price_refresh import price_population_sha256
    from .equibles_transcript_backfill import read_file
    def require(ok):
        if not ok:raise ConflictError('Expanded daily prices require fully resolved history and evidenced quarantine')
    population=price_population_sha256(selection)
    symbols={s.provider_symbol for s in selection.eligible}
    require(not selection.gaps and activation.get('window_calendar_days')==7
        and activation.get('history_sha256')==hashlib.sha256(body).hexdigest()
        and activation.get('price_population_sha256')==population
        and result.get('contract')=='quant_data.selected_price_history_completion.v2'
        and result.get('price_population_sha256')==population
        and result.get('outcome')=='complete_with_quarantine'
        and result.get('finished_symbols')==len(symbols)
        and result.get('unattempted_symbols')==0 and result.get('failed_symbols')==0
        and result.get('canonical_audit_verified') is True)
    states=result.get('symbol_states')
    require(isinstance(states,dict) and set(states)==symbols)
    require(all(isinstance(s,dict) and s.get('status') in ('complete','complete_with_quarantine')
        and isinstance(s.get('boundary'),dict) and s['boundary'].get('evidence')=='empty_provider_window_before_listing_or_retained_history' for s in states.values()))
    quarantined={symbol for symbol,state in states.items() if state['status']=='complete_with_quarantine'}
    require(bool(quarantined) and result.get('quarantined_symbols')==len(quarantined)
        and result.get('completed_symbols')==len(symbols)-len(quarantined))
    quality_body=read_file(project_root/QUALITY_RECEIPT,8*1024*1024)
    require(hashlib.sha256(quality_body).hexdigest()==result.get('quarantine_sha256'))
    quality=loads_strict(quality_body,max_bytes=8*1024*1024)
    require(isinstance(quality,dict) and quality.get('contract')=='quant_data.price_history_quarantine.v1'
        and quality.get('policy')==POLICY and quality.get('price_population_sha256')==population
        and quality.get('unresolved_windows')==0)
    windows=quality.get('windows');require(isinstance(windows,list) and 1<=len(windows)<=980)
    seen=set();count=0
    for window in windows:
        require(isinstance(window,dict) and window.get('symbol') in quarantined
            and window.get('resolution')=='clean_subranges_published'
            and isinstance(window.get('response_sha256'),str) and len(window['response_sha256'])==64)
        excluded=window.get('excluded');require(isinstance(excluded,list) and 1<=len(excluded)<=MAX_ROWS)
        for entry in excluded:
            require(isinstance(entry,dict) and isinstance(entry.get('date'),str)
                and window['from']<=entry['date']<=window['to']
                and isinstance(entry.get('source_rows'),list) and bool(entry['source_rows'])
                and all(type(i) is int and 1<=i<=MAX_ROWS for i in entry['source_rows']))
            key=(window['symbol'],entry['date']);seen.add(key)
        count+=len(excluded)
    require({w['symbol'] for w in windows}==quarantined
        and quality.get('quarantined_dates')==len(seen)
        and result.get('quarantined_dates')==len(seen))
    audit_body=read_file(project_root/AUDIT_RECEIPT,4*1024*1024)
    require(hashlib.sha256(audit_body).hexdigest()==result.get('canonical_audit_sha256'))
    audit=loads_strict(audit_body,max_bytes=4*1024*1024)
    require(isinstance(audit,dict) and audit.get('price_population_sha256')==population)
    coverage=audit.get('coverage');require(isinstance(coverage,list) and len(coverage)==len(selection.subjects))
    require({r.get('source_symbol') for r in coverage}=={s.source_symbol for s in selection.subjects}
        and all(r.get('status')=='resolved' and type(r.get('price_rows')) is int and r['price_rows']>0 for r in coverage))
