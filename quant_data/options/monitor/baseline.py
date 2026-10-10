"""Historical RTH volume seeds. One compact, complete ticker-day; never live data."""
from __future__ import annotations
from datetime import date
from .model import contract, nonnegative, timestamp
from .policy import NY, CADENCES

VERSION = "theta_5m_volume_v1"

def expirations(rows, symbol, session):
    result=set()
    for row in rows:
        expiry,_,_=contract(row,symbol)
        if expiry<session:raise ValueError("historical_expired_contract")
        result.add(expiry)
    return sorted(result)

def aggregate_expiration(rows, symbol, session, expiration, close_minute):
    """OHLC timestamps are bar starts. Empty/failed responses never prove coverage."""
    if not rows:raise ValueError("historical_expiration_empty")
    if close_minute not in (780,960):raise ValueError("historical_close")
    bins={m:[m,0,0] for m in range(570,close_minute,5)};seen=set()
    for row in rows:
        key=contract(row,symbol)
        if key[0]!=expiration:raise ValueError("historical_expiration_scope")
        stamp=timestamp(row.get("timestamp"))
        if stamp is None:raise ValueError("historical_timestamp")
        local=stamp.astimezone(NY);minute=local.hour*60+local.minute
        if local.date().isoformat()!=session or local.second or local.microsecond or minute%5:
            raise ValueError("historical_bar_alignment")
        # The endpoint may include the upper bound; it belongs to the next bar.
        if minute==close_minute:continue
        if minute not in bins:raise ValueError("historical_bar_outside_session")
        identity=(*key,minute)
        if identity in seen:raise ValueError("historical_duplicate_bar")
        seen.add(identity)
        volume=nonnegative(row.get("volume"))
        if volume is None or int(volume)!=volume:raise ValueError("historical_volume")
        bins[minute][1 if key[2]=="C" else 2]+=int(volume)
    if not seen:raise ValueError("historical_no_regular_bars")
    return list(bins.values())

def validate_day(day):
    if day.get("version")!=VERSION:raise ValueError("historical_version")
    date.fromisoformat(day["session"])
    close=day["close_minute"]
    if close not in (780,960) or len(day["bins"])!=(close-570)//5:raise ValueError("historical_bins")
    for expected,row in zip(range(570,close,5),day["bins"]):
        if len(row)!=3 or row[0]!=expected or any(type(x) is not int or x<0 for x in row):
            raise ValueError("historical_bins")
    return day

def seed_interval(day, cadence, end_minute):
    validate_day(day)
    if cadence not in CADENCES or end_minute%5 or end_minute-cadence<570 or end_minute>day["close_minute"]:return None
    rows=day["bins"][(end_minute-cadence-570)//5:(end_minute-570)//5]
    calls=sum(x[1] for x in rows);puts=sum(x[2] for x in rows)
    return {"session":day["session"],"coverage":"complete","quality_flags":[],
        "interval_minutes":cadence,"interval_volume":calls+puts,
        "interval_call_volume":calls,"interval_put_volume":puts,"_seed_end_minute":end_minute}

def seed_aligned(start, end, cadence, end_minute):
    """Fixed historical windows match only within one minute of both endpoints."""
    def minute(stamp):
        local=stamp.astimezone(NY)
        return local.hour*60+local.minute+local.second/60+local.microsecond/60000000
    return abs(minute(end)-end_minute)<=1 and abs(minute(start)-(end_minute-cadence))<=1

def completed_sessions(as_of, years, count=30):
    from .policy import previous_session
    day=date.fromisoformat(as_of);result=[]
    for _ in range(count):
        day=previous_session(day,years)
        if day is None:raise ValueError("historical_calendar_missing")
        special=next((r for r in years[day.year] if r["date"]==day.isoformat()),None)
        result.append({"session":day.isoformat(),"close_minute":780 if special and special["type"]=="early_close" else 960})
    return result


def seed_cumulative(day,end_minute):
    """Sum only completed half-open five-minute RTH bars, never end-of-day scale."""
    validate_day(day)
    if type(end_minute) is not int or end_minute%5 or not 570<end_minute<=day["close_minute"]:
        return None
    rows=day["bins"][:(end_minute-570)//5]
    return {"symbol":day["symbol"],"session":day["session"],
        "coverage":"complete","quality_flags":[],
        "cumulative_volume":sum(r[1]+r[2] for r in rows),
        "_cumulative_end_minute":end_minute}
