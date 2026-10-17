"""Missing-session plans over the existing source-specific price versions."""
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from ..errors import ValidationError, ResourceLimitError
from ..stores import quiet_immutable_read_connection
from ..market.collection_universe import _utc
from ..market.stage12b_scope import STAGE12B_PRICE_VARIANT
from .collection_prices import PriceWindow
from .collection_targets import selected_market_rows


@dataclass(frozen=True)
class PriceCalendar:
    symbol: str
    sessions: tuple[str, ...]


def plan_missing_price_sessions(stores, selection, *, calendars, cutoff, max_units):
    """Calendars and finite history ranges are explicit; holidays are never guessed."""
    if type(max_units) is not int or not 1 <= max_units <= 200000:
        raise ResourceLimitError("Price-history unit allocation is invalid")
    if not isinstance(calendars, tuple) or not 1 <= len(calendars) <= 6600:
        raise ResourceLimitError("Price-history calendar allocation is invalid")
    if any(not isinstance(item, PriceCalendar) for item in calendars):
        raise ValidationError("Price history requires explicit per-security calendars")
    if len({item.symbol for item in calendars}) != len(calendars):
        raise ValidationError("Price-history calendars repeat a security")
    rows=selected_market_rows(stores,selection,cutoff=cutoff)
    instruments={r["provider_symbol"]:r["instrument_id"] for r in rows}
    total=0
    clock=datetime.fromisoformat(_utc(cutoff).replace("Z","+00:00")).astimezone(ZoneInfo("America/New_York"))
    for item in calendars:
        if item.symbol not in instruments or not isinstance(item.sessions,tuple) or not 1 <= len(item.sessions) <= 5000:
            raise ValidationError("Price-history sessions are outside the selected bound")
        try:
            valid=tuple(date.fromisoformat(value).isoformat() for value in item.sessions)
        except (ValueError,TypeError):
            raise ValidationError("Price-history session date is invalid") from None
        if valid!=item.sessions or tuple(sorted(set(valid)))!=valid:
            raise ValidationError("Price-history sessions must be unique and sorted")
        last=date.fromisoformat(valid[-1])
        if last>clock.date() or (last==clock.date() and clock.time()<time(18)):
            raise ValidationError("Price-history sessions must be completed by the cutoff")
        total+=len(valid)
    if total>2000000:
        raise ResourceLimitError("Price-history calendar inventory exceeds two million dates")
    windows=[];coverage=[];at=_utc(cutoff)
    with quiet_immutable_read_connection(stores,"market") as c:
        c.create_function("collection_capture_utc",1,_utc,deterministic=True)
        for item in sorted(calendars,key=lambda item:item.symbol):
            found=set()
            for offset in range(0,len(item.sessions),500):
                dates=item.sessions[offset:offset+500]
                query=("SELECT DISTINCT trade_date FROM stage10_daily_price_versions WHERE instrument_id=? "
                    "AND provider='fmp' AND price_variant=? AND currency_segment='provider_native' "
                    "AND collection_capture_utc(available_at)<=? AND collection_capture_utc(captured_at)<=? "
                    "AND trade_date IN ("+",".join("?" for _ in dates)+") LIMIT ?")
                known=c.execute(query,(instruments[item.symbol],STAGE12B_PRICE_VARIANT,at,at,*dates,len(dates)+1)).fetchall()
                found.update(row[0] for row in known)
            group=[]
            def flush():
                if group:
                    if len(windows)>=max_units:
                        raise ResourceLimitError("Missing price history exceeds its explicit unit allocation")
                    windows.append(PriceWindow(item.symbol,group[0],group[-1],tuple(group)))
                    group.clear()
            for session in item.sessions:
                if session in found:
                    flush()
                else:
                    if group and (len(group)==5 or (date.fromisoformat(session)-date.fromisoformat(group[0])).days>=7):
                        flush()
                    group.append(session)
            flush()
            coverage.append({"symbol":item.symbol,"requested_sessions":len(item.sessions),
                "existing_sessions":len(found),"missing_sessions":len(item.sessions)-len(found)})
    return {"windows":tuple(windows),"coverage":tuple(coverage),"provider_requests":0,
        "canonical_writes":0,"calendar_basis":"explicit_per_security",
        "price_variant":STAGE12B_PRICE_VARIANT,"selection_sha256":selection.scope_sha256}
