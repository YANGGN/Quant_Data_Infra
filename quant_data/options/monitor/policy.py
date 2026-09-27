"""Strict monitor configuration and market-clock policy."""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json, re

NY = ZoneInfo("America/New_York")
CADENCES = frozenset((5, 10, 15, 30))
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,11}\Z")
LIMITS = {"max_symbols":3000,"watchlist_max":100,"max_requests_per_cycle":2500,
    "max_response_bytes_per_cycle":268435456,"max_seconds_per_cycle":240,
    "max_requests_per_session":60000,"max_response_bytes_per_session":8589934592,
    "max_workers":3,"retries":0,"baseline_min_sessions":20,"retain_sessions":90}

def load_policy(path: Path) -> dict:
    value=json.loads(path.read_text())
    if value.get("contract")!="quant_data.options_monitor.v1" or value.get("schema_version")!=1:
        raise ValueError("monitor_policy_contract")
    if any(value.get(k)!=v for k,v in LIMITS.items()) or value.get("cadences")!=[5,10,15,30]:
        raise ValueError("monitor_policy_bounds")
    daily=value.get("daily_history")
    if daily is not None and daily!={"enabled":True,"source_store":"data/options.sqlite","derivation_version":"options_daily_chart_v1","max_points_per_cycle":500,"max_site_posts_per_cycle":1,"clock":"existing_out_of_session_cycles"}:
        raise ValueError("monitor_daily_history_binding")
    return value

def load_universe(path: Path) -> dict[str,dict]:
    value=json.loads(path.read_text());members=value.get("members")
    if value.get("schema_version")!=1 or not isinstance(members,list) or not 1<=len(members)<=3000:
        raise ValueError("monitor_universe_shape")
    result={}
    for member in members:
        if not isinstance(member,dict) or set(member)!={"symbol","name","group","cadence_minutes"}:
            raise ValueError("monitor_universe_member")
        symbol=member["symbol"]
        if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol) or symbol in result:
            raise ValueError("monitor_universe_symbol")
        if not isinstance(member["name"],str) or not 1<=len(member["name"])<=100:
            raise ValueError("monitor_universe_name")
        if member["group"] not in ("market","sector","equity") or member["cadence_minutes"] not in CADENCES:
            raise ValueError("monitor_universe_tier")
        result[symbol]=member
    return result

def validate_watchlist(config:dict,universe:dict[str,dict])->dict[str,dict]:
    if config.get("schema_version")!=1 or not isinstance(config.get("watchlist"),list) or len(config["watchlist"])>100:
        raise ValueError("site_watchlist_contract")
    result={}
    for row in config["watchlist"]:
        if not isinstance(row,dict) or set(row)!={"symbol","cadence_minutes","alerts_enabled"}:
            raise ValueError("site_watch_shape")
        symbol=row["symbol"]
        if symbol not in universe or symbol in result or row["cadence_minutes"] not in CADENCES or type(row["alerts_enabled"]) is not bool:
            raise ValueError("site_watch_scope")
        result[symbol]=row
    return result

def market_window(day:date,calendar_rows:list[dict],*,extended:bool=False)->tuple[datetime,datetime]|None:
    if day.weekday()>=5:return None
    if len(calendar_rows)!=1 or not isinstance(calendar_rows[0],dict):
        raise ValueError("calendar_ambiguous")
    row=calendar_rows[0]
    if "date" in row and row["date"]!=day.isoformat():raise ValueError("calendar_date_mismatch")
    kind=row.get("type")
    if kind in ("full_close","weekend"):return None
    if kind not in ("open","early_close"):raise ValueError("calendar_status_unknown")
    def clock(value):
        if not isinstance(value,str):raise ValueError("calendar_time_missing")
        return time.fromisoformat(value)
    opening,closing=clock(row.get("open")),clock(row.get("close"))
    if opening!=time(9,30) or closing not in (time(13),time(16)):
        raise ValueError("calendar_time_unknown")
    if kind=="early_close" and closing!=time(13):raise ValueError("calendar_early_close")
    if kind=="open" and closing!=time(16):raise ValueError("calendar_regular_close")
    if extended:closing=(datetime.combine(day,closing)+timedelta(minutes=15)).time()
    return datetime.combine(day,opening,NY),datetime.combine(day,closing,NY)

def previous_session(day:date,calendar_years:dict[int,list[dict]])->date|None:
    """Use explicit full-close rows for every traversed year; never assume a holiday."""
    candidate=day-timedelta(days=1)
    for _ in range(10):
        rows=calendar_years.get(candidate.year)
        if rows is None:return None
        if not isinstance(rows,list) or any(
            not isinstance(r,dict) or r.get("type") not in ("full_close","early_close")
            or not isinstance(r.get("date"),str) or not r["date"].startswith(str(candidate.year)+"-")
            for r in rows):
            return None
        closed={r["date"] for r in rows if r["type"]=="full_close"}
        if candidate.weekday()<5 and candidate.isoformat() not in closed:return candidate
        candidate-=timedelta(days=1)
    return None

def slot_for(now:datetime,cadence:int,opening:datetime,closing:datetime)->datetime|None:
    if now.tzinfo is None or cadence not in CADENCES:raise ValueError("monitor_clock")
    local=now.astimezone(NY)
    if local<opening or local>closing:return None
    elapsed=int((local-opening).total_seconds()//60)
    slot=opening+timedelta(minutes=(elapsed//cadence)*cadence)
    return slot if slot<=closing else None
