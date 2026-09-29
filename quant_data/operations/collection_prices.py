"""Selected price manifests and the existing versioned price publisher.

Windows are explicit and bounded to the existing seven-day parser contract.
Historical gaps and current-session maintenance use different observation units.
"""
from ..ingestion import PublicationDeferred

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import hashlib
import time as runtime_time
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..market.collection_universe import _utc
from ..market.stage12_incremental import Stage12BFixtureRequest, Stage12BFixtureResponse
from .collection_targets import selected_market_rows
from .collection_plan import AcquisitionUnit, validate_unit, reusable

@dataclass(frozen=True)
class PriceWindow:
    symbol: str
    start: str
    end: str
    sessions: tuple[str, ...]

def price_units(stores, selection, *, windows, mode, observation_window, cutoff, require_active=False):
    rows = selected_market_rows(stores, selection, cutoff=cutoff, require_active=require_active)
    available = {r["provider_symbol"] for r in rows}
    members = {}
    for s in selection.eligible:
        members.setdefault(s.provider_symbol, set()).add(s.source_symbol)
    clock=datetime.fromisoformat(_utc(cutoff).replace("Z","+00:00")).astimezone(ZoneInfo("America/New_York"))
    units = {}
    for w in windows:
        if not isinstance(w, PriceWindow) or w.symbol not in available:
            raise ValidationError("Price window is outside the pinned selection")
        try:
            start, end = date.fromisoformat(w.start), date.fromisoformat(w.end)
            sessions = tuple(date.fromisoformat(x) for x in w.sessions)
        except (ValueError, TypeError) as exc:
            raise ValidationError("Price window dates are invalid") from exc
        if (start.isoformat()!=w.start or end.isoformat()!=w.end or not isinstance(w.sessions, tuple)
            or not 0 <= (end-start).days < 7 or not 1 <= len(sessions) <= 5
            or tuple(sorted(set(sessions))) != sessions or any(x<start or x>end for x in sessions)
            or any(x.isoformat()!=s for x,s in zip(sessions,w.sessions))
            or end > clock.date() or (end == clock.date() and clock.time() < time(18))):
            raise ValidationError("Price windows require explicit completed dates within seven days")
        u = validate_unit(AcquisitionUnit("daily_prices","fmp","historical-price-eod/full",w.symbol,
            tuple(sorted({"symbol":w.symbol,"from":w.start,"to":w.end}.items())),
            mode,observation_window,tuple(sorted(members.get(w.symbol,()))),selection.scope_sha256,
            max_response_bytes=65536,max_rows=5,timeout_seconds=45))
        old=units.get(u.unit_id)
        if old is not None and old[1]!=w:
            raise ConflictError("One price request has conflicting session calendars")
        units[u.unit_id]=(u,w)
    return tuple(units[k] for k in sorted(units))

class SelectedPricePublisher:
    """Queue callback, with exact request windows and original evidence reuse."""
    def __init__(self, *, stores, selection, cutoff, collector, requests, resolve_retained=None):
        expected=price_units(stores,selection,windows=tuple(w for _,w in requests),
            mode=requests[0][0].mode if requests else "historical_backfill",
            observation_window=requests[0][0].observation_window if requests else "historical_backfill",
            cutoff=cutoff)
        if tuple(sorted(requests,key=lambda x:x[0].unit_id)) != expected:
            raise ConflictError("Price publisher requests differ from their pinned manifest")
        collector._bind_selected_prices(stores=stores,selection=selection,cutoff=cutoff)
        self.collector=collector
        self.binding_token=collector._owner
        self.stores=stores
        self.selection=selection
        self.requests={u.unit_id:(u,w) for u,w in requests}
        self.cutoff=cutoff
        self.resolve_retained=resolve_retained

    def __call__(self, *, unit, retained, receipt, body, deadline=None, monotonic=runtime_time.monotonic):
        def check_deadline():
            if deadline is not None and monotonic()>=deadline:
                raise PublicationDeferred("Selected price publication exceeded its invocation deadline")
        check_deadline()
        if self.collector._owner is not self.binding_token:
            raise ConflictError("Price publisher collector was rebound to another selection")
        known=self.requests.get(unit.unit_id)
        if known is None or known[0]!=unit:
            raise ConflictError("Price publication is outside its exact request manifest")
        if retained is not None:
            if reusable(unit,(retained,),cutoff=self.cutoff) is None or self.resolve_retained is None:
                raise ConflictError("Price reuse requires verified original evidence")
            body=self.resolve_retained(retained)
            if not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=retained.content_sha256:
                raise ConflictError("Retained price bytes differ")
            if retained.published:
                return {"outcome":"reused","capture_id":retained.capture_id,"coverage":retained.coverage}
            captured=retained.captured_at
        else:
            if (not isinstance(receipt,dict) or receipt.get("request_id")!=unit.request_id
                or receipt.get("unit_id")!=unit.unit_id or receipt.get("status")!=200
                or not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=receipt.get("content_sha256")):
                raise ConflictError("Price response differs from its charged request")
            captured=receipt["captured_at"]
        selected_market_rows(self.stores,self.selection,cutoff=captured)
        w=known[1]
        prepared=self.collector.prepare(Stage12BFixtureRequest(w.symbol,w.start,w.end,w.sessions,captured),
            Stage12BFixtureResponse(200,"application/json",body,0))
        check_deadline()
        result=self.collector.publish(prepared,deadline=deadline,monotonic=monotonic)
        if result.outcome=="empty":
            # A complete HTTP request is not a complete price history.
            return {"outcome":"succeeded","coverage":"empty","written_versions":0}
        return {"outcome":"unchanged" if result.outcome=="unchanged" else "succeeded",
            "coverage":"complete_request","capture_id":result.capture_id,"written_versions":result.written_versions}
