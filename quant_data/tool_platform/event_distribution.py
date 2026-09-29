"""Retrospective distributions over retained earnings or macro events."""
from datetime import date,datetime,time,timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo
from quant_data.errors import ValidationError,ResourceLimitError,StoreUnavailableError
from quant_data.market.etf_calculations import sessions,CALENDAR_COVERAGE_START,CALENDAR_COVERAGE_END,CALENDAR_ID
from quant_data.options.universe import TIER1_ETFS
from .analytics import event_study
from .earnings_research import earnings_rows
from .retained_research_common import cutoff,compose,unpack,number,result
from .retained_research_sources import price_series,series_source

def aligned_index(event,days):
    """First full regular session; date-only and during-session events use next day."""
    if len(event)==10:
        return next((i for i,d in enumerate(days) if d>event),None),"date_only_next_session"
    timestamp=datetime.fromisoformat(event.replace("Z","+00:00"))
    if timestamp.tzinfo is None:
        return next((i for i,d in enumerate(days) if d>timestamp.date().isoformat()),None),"source_date_next_session_timezone_unverified"
    local=timestamp.astimezone(ZoneInfo("America/New_York"))
    day=local.date().isoformat()
    return next((i for i,d in enumerate(days) if d>day or (d==day and local.time()<time(9,30))),None),"first_full_session_after_timestamp"

def response(event,days,prices,horizon):
    index,alignment=aligned_index(event,days)
    base=dict(horizon_sessions=horizon,alignment=alignment,cumulative_return=None,missing_sessions=[])
    if index is None or index==0 or index+horizon>len(days):
        return dict(base,status="not_established",reason="event_window_outside_sample")
    selected=days[index-1:index+horizon]
    missing=[d for d in selected if number(prices.get(d)) is None]
    if missing: return dict(base,status="not_established",reason="missing_prices_in_event_window",missing_sessions=missing)
    values=[number(prices[d]) for d in selected]
    if any(v<=0 for v in values): return dict(base,status="not_established",reason="nonpositive_close")
    returns=[b/a-1 for a,b in zip(values,values[1:])]
    calculated=event_study(returns,event_index=0,pre=0,post=horizon-1)
    return dict(base,status="available",reason=None,baseline_session=selected[0],first_response_session=selected[1],
        final_session=selected[-1],cumulative_return=calculated["cumulative_return"])

def summary(values):
    numeric=sorted(number(v) for v in values if number(v) is not None)
    n=len(numeric)
    return dict(sample_size=n,mean=sum(numeric)/n if n else None,median=median(numeric) if n else None,
        minimum=numeric[0] if n else None,maximum=numeric[-1] if n else None,
        positive_fraction=Decimal(sum(v>0 for v in numeric))/n if n else None,
        p10=numeric[int((n-1)*.1)] if n else None,p90=numeric[int((n-1)*.9)] if n else None,
        quantile_method="lower_order_statistic")

def invoke(args,context,registry):
    at=cutoff(args,context);symbol=args["symbol"];start=args["start_date"];end=args["end_date"]
    horizons=args.get("horizons",(1,5,20))
    if args.get("event_source","earnings")=="earnings":
        source=earnings_rows(context,(symbol,),start,end,at)
        events=[dict(event_at=r["event_date"],event_id=r["observation_version_id"],source=r,
                     eligible=r["date_status"]=="reported_actual_present") for r in source]
    else:
        value=compose("macro.get_release_calendar","2.0.0",dict(mode="as_of",as_of=at,
            date_only_policy="completed_date",limit=201,start_date=start,end_date=end,event_name=args["event_name"]),context,registry)
        if value.truncation.has_more or len(value.records)>200: raise ResourceLimitError("Select at most 200 events")
        events=[dict(event_at=r["event_at"],event_id=r["event_version_id"],source=r,
                     eligible=r.get("actual_json") not in (None,"null")) for record in value.records for r in [unpack(record)]
                if r["event_name"]==args["event_name"]]
    if len(events)>200: raise ResourceLimitError("Select at most 200 events")
    low=max(CALENDAR_COVERAGE_START,date.fromisoformat(start)-timedelta(days=60))
    high=min(CALENDAR_COVERAGE_END,date.fromisoformat(at[:10]),date.fromisoformat(end)+timedelta(days=max(horizons)*2+10))
    if low>high or date.fromisoformat(start)<CALENDAR_COVERAGE_START:
        raise ValidationError("Event response calendar supports 2024 through 2026 only")
    days=[d.isoformat() for d in sessions(low,high)]
    prices=price_series(symbol,low.isoformat(),high.isoformat(),at,context,registry)
    if prices.metadata.get("adjustment_status")!="split_adjusted_excluding_distributions" and prices.observations:
        raise ValidationError("Event returns require the established split-adjusted close binding")
    for event in events:
        if args.get("event_source","earnings")=="earnings" and event["source"].get("instrument_id")!=prices.metadata.get("instrument_id"):
            event.update(eligible=False, exclusion_reason="event_price_identity_mismatch")
    p={o.period_end:o.value for o in prices.observations}
    try:
        volume=price_series(symbol,low.isoformat(),high.isoformat(),at,context,registry,volume=True)
        v={o.period_end:o.value for o in volume.observations}
    except StoreUnavailableError: v={}
    option_days={};option_status="outside_theta_etf_scope" if args.get("include_options",True) else "not_requested"
    if symbol in TIER1_ETFS and args.get("include_options",True):
        try:
            cursor=low
            while cursor<=high:
                last=min(high,cursor+timedelta(days=365))
                option=compose("options.get_daily_history","1.0.0",dict(symbol=symbol,start_date=cursor.isoformat(),end_date=last.isoformat(),as_of=at),context,registry)
                option_days.update({r["session"]:r for record in option.records if record.record_type=="theta_daily" for r in [unpack(record)]})
                cursor=last+timedelta(days=1)
            option_status="retained_theta"
        except StoreUnavailableError: option_status="theta_store_unavailable"
    rows=[("event_response_price_source",series_source(prices))]
    outcomes=[]
    for event in events:
        for horizon in horizons:
            r=response(event["event_at"],days,p,horizon) if event["eligible"] else dict(
                horizon_sessions=horizon,status="not_established",reason=event.get("exclusion_reason","reported_actual_unavailable"),cumulative_return=None)
            r.update(event_id=event["event_id"],event_at=event["event_at"],source=event["source"],symbol=symbol,
                option_status=option_status,volume_ratio_to_prior20=None,atm_iv30_change=None)
            if r["status"]=="available":
                i=days.index(r["first_response_session"])
                baseline=[number(v.get(d)) for d in days[max(0,i-20):i]]
                after=[number(v.get(d)) for d in days[i:i+horizon]]
                if len(baseline)==20 and all(x is not None for x in baseline+after) and sum(baseline)>0:
                    r["volume_ratio_to_prior20"]=(sum(after)/len(after))/(sum(baseline)/20)
                before_iv=option_days.get(r["baseline_session"],{})
                after_iv=option_days.get(r["final_session"],{})
                a,b=number(before_iv.get("atm_iv_30")),number(after_iv.get("atm_iv_30"))
                if a is not None and b is not None: r["atm_iv30_change"]=b-a
                r["option_capture_ids"]=[before_iv.get("source_capture_id"),after_iv.get("source_capture_id")]
                r["option_sources"]=[{k:point.get(k) for k in ("symbol","session","source_capture_id",
                    "source_captured_at","source_semantic_sha256","derivation_version","quality_flags_json")}
                    for point in (before_iv,after_iv)]
                r["price_version_ids"]=[o.version_id for o in prices.observations if r["baseline_session"]<=o.period_end<=r["final_session"]]
            outcomes.append(r);rows.append(("event_response",r))
    for horizon in horizons:
        chosen=[r for r in outcomes if r["horizon_sessions"]==horizon]
        windows=[(r["first_response_session"],r["final_session"]) for r in chosen if r["status"]=="available"]
        overlap=sum(1 for i,(a,b) in enumerate(windows) for c,d in windows[i+1:] if a<=d and c<=b)
        for metric in ("cumulative_return","volume_ratio_to_prior20","atm_iv30_change"):
            rows.append(("event_response_distribution",dict(horizon_sessions=horizon,metric=metric,
                **summary([r.get(metric) for r in chosen]),selected_events=len(chosen),
                excluded_events=sum(r.get(metric) is None for r in chosen),overlapping_window_pairs=overlap,calendar_id=CALENDAR_ID)))
    return result("research.get_event_response_distribution",args,at,rows,established=bool(events),
        warnings=(("retrospective_event_analysis","Daily responses use the first full session after each event. Date-only and timezone-unverified releases use the source date and skip same-day response; overlapping windows are dependent. Results are descriptive, not causal, abnormal returns, or a point-in-time strategy."),))
