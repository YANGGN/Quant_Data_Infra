"""Selected daily-price clock refresh through the existing finite source queue.

The existing 18:00 New York timer owns invocation. No catch-up, retries, timer
installation, binding activation, or history acquisition occurs in this module.
"""
from dataclasses import dataclass,replace
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import time
from ..errors import ConflictError,ValidationError
from ..json_codec import dumps_strict,loads_strict
from ..market.collection_bindings import load_bindings,pin_binding
from ..market.stage12_incremental import Stage12BIncrementalCollector
from ..market.stage12_scope import load_stage12_market_v1_scope
from ..market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from ..stores import StoreMap
from .collection_plan import ProviderBudget,build_plan
from .collection_prices import PriceWindow,price_units,SelectedPricePublisher
from .collection_targets import selected_market_rows
from .price_fetch_policy import current_price_targets
from .collection_queue import run_queue,atomic,_response,_read,BoundedForwardClock
from .collection_transport import host_fetch
from .equibles_transcript_backfill import private_directory,job_lock

ROOT=Path('/home/volatility/Python_Projects/Quant_Data_Infra')
MAX_SECONDS=1755
MAX_REQUESTS=6600
MAX_BYTES=160*1024*1024
LIVE_ACTIVATION=Path('data/.operations/collection/daily-prices/live-activation.json')
HISTORY_COMPLETION=Path('data/.operations/collection/daily-prices/history-completion.json')

@dataclass(frozen=True)
class SelectedPriceRefreshReport:
    result:dict
    def mapping(self):
        from .fetch_run_summary import record_report
        record_report('quant-data-market-close.timer',self.result)
        return self.result

def price_population_sha256(selection):
    """Price membership identity remains stable across unrelated binding changes."""
    import hashlib
    from dataclasses import asdict
    return hashlib.sha256(dumps_strict({'binding':selection.binding.id,
        'membership':selection.membership_snapshot_id,'mapping':selection.mapping_id,
        'retained':[asdict(r) for r in selection.retained]}).encode()).hexdigest()

def _history_selection(selection,activation,*,stores,cutoff):
    """Recognize issuer-only revisions without rewriting historical approval."""
    if activation.get('price_population_sha256')==price_population_sha256(selection):
        return selection
    message='Daily-price history approval differs; price identities must be unchanged'
    if stores is None or cutoff is None or not isinstance(activation.get('at'),str):
        raise ConflictError(message)
    from ..market.collection_universe import _utc
    from ..market.collection_pins import validate_pinned_collection
    if _utc(activation['at'])>_utc(cutoff):
        raise ConflictError(message)
    validate_pinned_collection(stores,selection,cutoff=cutoff)
    approved=pin_binding(stores,selection.binding,cutoff=activation['at'])
    validate_pinned_collection(stores,approved,cutoff=activation['at'])
    # The mapping snapshot ID includes company identifiers. Only those fields
    # and explanatory notes may differ; price subjects and scope stay exact.
    def prices(subjects):
        return tuple(replace(s,cik=None,reason='') for s in subjects)
    if (selection.binding.id!='daily_prices' or selection.gaps or approved.gaps
        or approved.membership_snapshot_id!=selection.membership_snapshot_id
        or approved.retained!=selection.retained
        or prices(approved.subjects)!=prices(selection.subjects)
        or price_population_sha256(approved)!=activation.get('price_population_sha256')):
        raise ConflictError(message)
    return approved

def require_history_completion(project_root,selection,*,stores=None,cutoff=None):
    """Verify original history evidence, allowing evidenced issuer-only changes."""
    import hashlib
    from .equibles_transcript_backfill import read_file
    activation=loads_strict(read_file(project_root/LIVE_ACTIVATION,65536),max_bytes=65536)
    body=read_file(project_root/HISTORY_COMPLETION,16*1024*1024)
    result=loads_strict(body,max_bytes=16*1024*1024)
    selection=_history_selection(selection,activation,stores=stores,cutoff=cutoff)
    if activation.get('contract')=='quant_data.selected_price_live_activation.v2':
        from .collection_price_quarantine import validate_quarantined_completion
        return validate_quarantined_completion(project_root,selection,activation,result,body)
    if (activation.get('contract')!='quant_data.selected_price_live_activation.v1'
        or activation.get('history_sha256')!=hashlib.sha256(body).hexdigest()
        or activation.get('price_population_sha256')!=price_population_sha256(selection)
        or result.get('price_population_sha256')!=price_population_sha256(selection)
        or result.get('outcome')!='complete' or result.get('unattempted_symbols')!=0
        or result.get('failed_symbols')!=0
        or result.get('completed_symbols')!=len({s.provider_symbol for s in selection.eligible})
        or result.get('canonical_audit_verified') is not True):
        raise ConflictError('Expanded daily prices require verified history completion for this selection')

class RollingPricePublisher:
    """Bind actual provider dates inside the unchanged seven-day price contract.

    Request identity excludes session calendars. Original bodies are preserved;
    neither weekdays nor missing exchange sessions are fabricated.
    """
    def __init__(self,*,stores,selection,cutoff,collector,requests):
        self.kw=dict(stores=stores,selection=selection,cutoff=cutoff,collector=collector)
        self.requests=requests;self.calendar=None;self.publisher=None
    def __call__(self,**kwargs):
        body=kwargs['body'];unit=kwargs['unit']
        rows=loads_strict(body,max_bytes=65536)
        if not isinstance(rows,list) or len(rows)>5 or any(not isinstance(r,dict) or not isinstance(r.get('date'),str) for r in rows):
            raise ValidationError('Daily refresh requires at most five original daily price rows')
        dates=tuple(sorted(r['date'] for r in rows)) if rows else (dict(unit.parameters)['to'],)
        if dates!=self.calendar:
            requests=tuple((u,replace(w,sessions=dates)) for u,w in self.requests)
            self.publisher=SelectedPricePublisher(**self.kw,requests=requests)
            self.calendar=dates
        return self.publisher(**kwargs)

def run_current_prices(*,root,stores,selection,collector,fetch,cutoff,session,monotonic=time.monotonic,utcnow=lambda:datetime.now(timezone.utc)):
    started=monotonic();deadline=started+MAX_SECONDS
    utcnow=BoundedForwardClock(utcnow,deadline=deadline,monotonic=monotonic)
    rows=selected_market_rows(stores,selection,cutoff=cutoff)
    rows,excluded=current_price_targets(rows,session=session)
    window_start=(date.fromisoformat(session)-timedelta(days=6)).isoformat()
    windows=tuple(PriceWindow(r['provider_symbol'],window_start,session,(session,)) for r in rows)
    requests=price_units(stores,selection,windows=windows,mode='incremental',observation_window=cutoff,cutoff=cutoff)
    # Observation identity is fixed to this session, so repeated CLI entry does
    # not create fresh requests. The outer completed-start guard also forbids it.
    requests=tuple((replace(u,observation_window=session+'T22:00:00.000000Z'),w) for u,w in requests)
    if len(requests)>MAX_REQUESTS:raise ValidationError('Selected daily-price scope exceeds its bound')
    sentinel=next((pair for pair in requests if pair[0].subject=='AAPL'),None)
    if sentinel is None:raise ValidationError('Selected daily-price scope lacks AAPL')
    publisher=RollingPricePublisher(stores=stores,selection=selection,cutoff=cutoff,collector=collector,requests=requests)
    ordered=(sentinel,)+tuple(p for p in requests if p is not sentinel)
    chunks=(ordered[:1],)+tuple(ordered[i:i+1000] for i in range(1,len(ordered),1000))
    totals={'requests':0,'received_bytes':0,'published':0,'reused':0};reports=[];outcome='complete'
    for index,chunk in enumerate(chunks):
        remaining=int(deadline-monotonic());byte_remaining=MAX_BYTES-totals['received_bytes']
        if remaining<45 or byte_remaining<65536:outcome='invocation_budget';break
        budget=ProviderBudget('fmp',len(chunk),byte_remaining,min(remaining,1755),250)
        plan=build_plan(selections=(selection,),units=tuple(u for u,_ in chunk),budgets=(budget,),created_at=cutoff)
        def publish(**kwargs):return publisher(**kwargs,deadline=deadline,monotonic=monotonic)
        report=run_queue(root=root/'queue',plan=plan,provider='fmp',fetch=fetch,publish=publish,
            secret_values=getattr(fetch,'secret_values',()),deadline=deadline,monotonic=monotonic,utcnow=utcnow)
        reports.append(report)
        for key in totals:totals[key]+=report.get(key,0)
        atomic(root/'progress.json',{'scope':selection.scope_sha256,'session':session,'planned':len(ordered),**totals,'batches':reports},replace=True)
        if report['outcome'] not in ('complete','complete_with_gaps'):
            outcome=report['outcome'];break
        if index==0:
            retained=_response(root/'queue',sentinel[0].unit_id,sentinel[0])
            if retained is None or retained[0]['status']!=200:outcome='sentinel_unavailable';break
            from ..json_codec import loads_strict
            if loads_strict(retained[1],max_bytes=65536)==[]:outcome='no_market_session';break
    if outcome=='complete' and any(r.get('failed_units') for r in reports):outcome='complete_with_gaps'
    successful=skipped=0
    for u,w in ordered:
        path=root/'queue/publications'/(u.unit_id+'.json')
        if not path.exists():continue
        publication=_read(path)
        if publication.get('request_id')!=u.request_id:raise ConflictError('Daily-price status receipt differs')
        if publication['result'].get('coverage')=='empty':skipped+=1
        else:successful+=1
    failed=len({g['unit_id'] for report in reports for g in report.get('failed_units',[])})
    unattempted=len(ordered)-successful-skipped-failed
    if outcome=='no_market_session':skipped+=unattempted;unattempted=0
    symbol_counts={'unit':'symbols','successful':successful,'failed':failed,'partial':0,'skipped':skipped,'unattempted':unattempted}
    return {'contract':'quant_data.selected_price_refresh.v1','outcome':outcome,'session_date':session,
        'selected_members':len(selection.subjects),'eligible_selected_members':len(selection.eligible),
        'identity_gaps':len(selection.gaps),'retained_etfs_indexes':len(selection.retained),'planned_requests':len(ordered),
        'excluded_symbols':list(excluded),
        'window_start':window_start,'window_end':session,'maximum_calendar_days':7,
        'maximum_seconds':MAX_SECONDS,'maximum_bytes':MAX_BYTES,'symbol_counts':symbol_counts,**totals,'batches':reports}

def run_selected_market_close_live():
    from .stage12e_market_close import scheduled_session_date
    now=datetime.now(timezone.utc);session=scheduled_session_date(now);cutoff=now.isoformat()
    stores=StoreMap.four_explicit(**{r:ROOT/'data'/(r+'.sqlite') for r in ('market','macro','company','news')})
    binding=load_bindings(ROOT/'config/collection_bindings.json')['daily_prices']
    if binding.mode!='active':raise ConflictError('Selected daily-price binding is not active')
    root=ROOT/'data/.operations/collection/daily-prices/current'/session
    private_directory(root)
    with job_lock(root):
        if (root/'result.json').exists():
            from ..json_codec import loads_strict
            return SelectedPriceRefreshReport(loads_strict((root/'result.json').read_bytes(),max_bytes=4*1024*1024))
        if (root/'started.json').exists():raise ConflictError('This daily-price clock invocation already started; manual reconciliation is required')
        selection=pin_binding(stores,binding,cutoff=cutoff)
        require_history_completion(ROOT,selection,stores=stores,cutoff=cutoff)
        selected_market_rows(stores,selection,cutoff=cutoff,require_active=True)
        collector=Stage12BIncrementalCollector._for_selected_market_prices(stores=stores,selection=selection,cutoff=cutoff,
            scope=load_stage12b_incremental_market_v1_scope(ROOT/'config/stage12b_incremental_market_v1_scope.json'),
            stage12a_scope=load_stage12_market_v1_scope(ROOT/'config/stage12_market_v1_scope.json'))
        atomic(root/'started.json',{'at':cutoff,'scope':selection.scope_sha256,'maximum_seconds':MAX_SECONDS,'maximum_requests':len(selection.eligible)+len(selection.retained),'maximum_bytes':MAX_BYTES,'retries':0})
        report=run_current_prices(root=root,stores=stores,selection=selection,collector=collector,
            fetch=host_fetch(ROOT,'fmp',environment={}),cutoff=cutoff,session=session)
        atomic(root/'result.json',report)
        return SelectedPriceRefreshReport(report)
