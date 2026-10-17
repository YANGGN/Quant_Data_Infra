"""Bounded five-year price windows for selected securities.

A successful response covers the requested provider window. It does not prove
that the provider returned every exchange session or pre-listing history.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date,datetime,time
from decimal import Decimal
from zoneinfo import ZoneInfo
from ..errors import ValidationError,ConflictError,ResourceLimitError
from ..json_codec import loads_strict
from ..market.collection_universe import _utc
from .collection_plan import AcquisitionUnit,validate_unit
from .collection_targets import selected_market_rows
from .collection_prices import SelectedPricePublisher

MAX_BYTES=16*1024*1024
MAX_ROWS=1830
MAX_SECONDS=45

@dataclass(frozen=True)
class HistoryPriceWindow:
    symbol:str
    start:str
    end:str
    @property
    def sessions(self):return ()

def validate_window(window,cutoff):
    if not isinstance(window,HistoryPriceWindow):raise ValidationError('A typed historical price window is required')
    try:start=date.fromisoformat(window.start);end=date.fromisoformat(window.end)
    except (ValueError,TypeError):raise ValidationError('Historical price dates are invalid') from None
    clock=datetime.fromisoformat(_utc(cutoff).replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York'))
    if (start.isoformat()!=window.start or end.isoformat()!=window.end or not 0<=(end-start).days<1830
        or start<date(1900,1,1) or end>clock.date() or (end==clock.date() and clock.time()<time(18))):
        raise ValidationError('Historical prices require completed dates, within the 1900 search boundary and five years')
    return window

def history_price_units(stores,selection,*,windows,cutoff,require_active=False):
    available={r['provider_symbol'] for r in selected_market_rows(stores,selection,cutoff=cutoff,require_active=require_active)}
    return _history_price_units(selection,windows=windows,cutoff=cutoff,available=available)

class PinnedHistoryPlanner:
    """Resolve the population once, before the independent writer starts."""
    def __init__(self,stores,selection,*,cutoff):
        self.selection=selection;self.cutoff=cutoff
        self.available=frozenset(r['provider_symbol'] for r in selected_market_rows(stores,selection,cutoff=cutoff))
    def units(self,windows):
        return _history_price_units(self.selection,windows=windows,cutoff=self.cutoff,available=self.available)

def _history_price_units(selection,*,windows,cutoff,available):
    members={}
    for s in selection.eligible:members.setdefault(s.provider_symbol,set()).add(s.source_symbol)
    if not isinstance(windows,tuple) or len(windows)>40000:raise ResourceLimitError('Historical price window count exceeds its bound')
    units={}
    for w in windows:
        validate_window(w,cutoff)
        if w.symbol not in available:raise ValidationError('Historical price symbol is outside its pinned selection')
        u=validate_unit(AcquisitionUnit('daily_prices','fmp','historical-price-eod/full',w.symbol,
            tuple(sorted({'symbol':w.symbol,'from':w.start,'to':w.end}.items())),
            'historical_backfill','selected-price-history.v1',tuple(sorted(members.get(w.symbol,()))),selection.scope_sha256,
            max_response_bytes=MAX_BYTES,max_rows=MAX_ROWS,timeout_seconds=MAX_SECONDS))
        if u.unit_id in units:raise ConflictError('Historical price windows repeat')
        units[u.unit_id]=(u,w)
    return tuple(units[k] for k in sorted(units))

class SelectedPriceHistoryPublisher(SelectedPricePublisher):
    def __init__(self,*,stores,selection,cutoff,collector,requests,resolve_retained=None):
        expected=history_price_units(stores,selection,windows=tuple(w for _,w in requests),cutoff=cutoff)
        if tuple(sorted(requests,key=lambda x:x[0].unit_id))!=expected:
            raise ConflictError('Historical price publication differs from its exact manifest')
        collector._bind_selected_prices(stores=stores,selection=selection,cutoff=cutoff)
        collector._selected_price_history=frozenset((w.symbol,w.start,w.end) for _,w in requests)
        collector._scheduled_code_version='selected-price-history.v1'
        self.collector=collector;self.binding_token=collector._owner
        self.stores=stores;self.selection=selection;self.cutoff=cutoff
        self.requests={u.unit_id:(u,w) for u,w in requests};self.resolve_retained=resolve_retained

def history_request_scope(collector,request):
    from ..market.stage12_incremental import _captured_at_text,_FMP_ENDPOINT,STAGE12B_PRICE_VARIANT
    if (request.symbol,request.from_date,request.to_date) not in collector._selected_price_history or request.session_dates!=():
        raise ValidationError('Historical price request differs from its bound windows')
    captured=_captured_at_text(request.captured_at)
    validate_window(HistoryPriceWindow(request.symbol,request.from_date,request.to_date),captured)
    identity=collector._scheduled_instruments.get(request.symbol)
    if identity is None:raise ConflictError('Historical price identity is missing')
    return {'endpoint_path':_FMP_ENDPOINT,'from':request.from_date,'to':request.to_date,
        'price_variant':STAGE12B_PRICE_VARIANT,'provider':'fmp','symbol':request.symbol,
        'scheduled_instrument_id':identity[0],'scheduled_asset_type':identity[1],
        'selected_price_history_contract':'v1','coverage_basis':'complete_provider_window',
        **({'missing_price_repair_contract':'v1'} if getattr(collector,'_selected_price_missing_only',False) else {})},captured

def _parse_history_row(collector,value,*,index,expected_symbol):
    if not isinstance(value,Mapping) or 'changePercent' not in value or value['changePercent'] is not None:
        return collector._parse_row(value,index=index,expected_symbol=expected_symbol)
    # Only this historical adapter accepts a literal missing ancillary percent.
    # Required OHLCV, other ancillary fields and every legacy parser stay strict.
    from ..market.stage12_incremental import _Row,_ROW_KEYS,_issue,_require_text,_date_text,_number,_volume
    @dataclass(frozen=True,slots=True)
    class HistoricalNullPercentRow(_Row):
        change_percent:Decimal|None
    pointer=f'/response/body/{index}'
    if set(value)!=_ROW_KEYS:raise _issue(pointer,'shape','FMP fixture row keys differ from the reviewed exact schema')
    symbol=_require_text(value['symbol'],pointer=f'{pointer}/symbol',maximum=32)
    if symbol!=expected_symbol:raise _issue(f'{pointer}/symbol','symbol','FMP fixture row symbol differs from the request')
    trade_date=_date_text(value['date'],pointer=f'{pointer}/date')
    open_value=_number(value['open'],pointer=f'{pointer}/open',nonnegative=True)
    high_value=_number(value['high'],pointer=f'{pointer}/high',nonnegative=True)
    low_value=_number(value['low'],pointer=f'{pointer}/low',nonnegative=True)
    close_value=_number(value['close'],pointer=f'{pointer}/close',nonnegative=True)
    if high_value<max(open_value,low_value,close_value) or low_value>min(open_value,high_value,close_value):
        raise _issue(pointer,'ohlc','FMP fixture OHLC values are internally inconsistent')
    return HistoricalNullPercentRow(symbol=symbol,trade_date=trade_date,open_value=open_value,high_value=high_value,
        low_value=low_value,close_value=close_value,volume=_volume(value['volume'],pointer=f'{pointer}/volume'),
        change=_number(value['change'],pointer=f'{pointer}/change'),change_percent=None,
        vwap=_number(value['vwap'],pointer=f'{pointer}/vwap',nonnegative=True))

def parse_history_response(collector,scope,response):
    if isinstance(response.elapsed_seconds,bool) or not isinstance(response.elapsed_seconds,(int,float,Decimal)):
        raise ValidationError('Historical response elapsed time is invalid')
    elapsed=Decimal(str(response.elapsed_seconds))
    if not elapsed.is_finite() or not 0<=elapsed<=MAX_SECONDS:raise ResourceLimitError('Historical response exceeds its deadline')
    if type(response.status) is not int or response.status!=200 or not isinstance(response.media_type,str) or response.media_type.split(';',1)[0].strip().lower()!='application/json':
        raise ValidationError('Historical response must be a successful JSON response')
    if not isinstance(response.body,bytes) or len(response.body)>MAX_BYTES:raise ResourceLimitError('Historical price response exceeds its byte bound')
    payload=loads_strict(response.body,max_bytes=MAX_BYTES)
    if not isinstance(payload,list):raise ValidationError('Historical response must be a list')
    if len(payload)>MAX_ROWS:raise ResourceLimitError('Historical response exceeds its row bound')
    rows=tuple(sorted((_parse_history_row(collector,value,index=i,expected_symbol=scope['symbol']) for i,value in enumerate(payload)),key=lambda r:r.trade_date))
    dates=tuple(r.trade_date for r in rows)
    if len(dates)!=len(set(dates)) or any(d<scope['from'] or d>scope['to'] for d in dates):
        raise ValidationError('Historical rows repeat or leave their requested window')
    return rows
