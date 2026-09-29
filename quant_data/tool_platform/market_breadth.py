"""Breadth over an explicit cohort and complete cash-session windows."""
from datetime import date,timedelta
from decimal import Decimal
import math
from quant_data.errors import ValidationError,StoreUnavailableError
from quant_data.market.etf_calculations import sessions,CALENDAR_COVERAGE_START,CALENDAR_ID
from .retained_research_common import cutoff,result,number
from .retained_research_sources import price_series,series_source

def fraction(count,eligible):
    return Decimal(100)*count/eligible if eligible else None

def calculate(prices,days,start,windows,high_low_window,leadership_window,checkpoint=lambda:None):
    out=[];streak={s:0 for s in prices};members={}
    for index,day in enumerate(days):
        checkpoint()
        counts=dict(advances=0,declines=0,unchanged=0,advance_decline_eligible=0,new_highs=0,new_lows=0,high_low_eligible=0)
        ma={str(w):dict(above=0,eligible=0) for w in windows};performance={}
        for symbol,values in sorted(prices.items()):
            close=number(values.get(day));prior=number(values.get(days[index-1])) if index else None
            member=dict(symbol=symbol,session=day,close=close,advance=None,above_ma={},new_high=None,new_low=None,
                        leadership_return=None,leader=False,leader_streak_sessions=0,missing_features=[])
            if close is not None and prior is not None and close>0 and prior>0:
                direction=1 if close>prior else -1 if close<prior else 0
                counts["advance_decline_eligible"]+=1
                counts["advances" if direction>0 else "declines" if direction<0 else "unchanged"]+=1
                member["advance"]=direction
            else: member["missing_features"].append("advance_decline")
            for window in windows:
                selected=days[max(0,index-window+1):index+1]
                sample=[number(values.get(d)) for d in selected]
                if len(sample)==window and all(v is not None and v>0 for v in sample):
                    above=close>sum(sample)/window;ma[str(window)]["eligible"]+=1;ma[str(window)]["above"]+=above
                    member["above_ma"][str(window)]=above
                else:
                    member["above_ma"][str(window)]=None;member["missing_features"].append("ma_"+str(window))
            past=[number(values.get(d)) for d in days[max(0,index-high_low_window):index]]
            if close is not None and close>0 and len(past)==high_low_window and all(v is not None and v>0 for v in past):
                member["new_high"]=close>max(past);member["new_low"]=close<min(past)
                counts["high_low_eligible"]+=1;counts["new_highs"]+=member["new_high"];counts["new_lows"]+=member["new_low"]
            else: member["missing_features"].append("high_low")
            path=[number(values.get(d)) for d in days[max(0,index-leadership_window):index+1]]
            if len(path)==leadership_window+1 and all(v is not None and v>0 for v in path):
                member["leadership_return"]=close/path[0]-1;performance[symbol]=member["leadership_return"]
            else: member["missing_features"].append("leadership")
            members[symbol]=member
        if performance:
            ordered=sorted(performance.values(),reverse=True)
            threshold=ordered[max(1,math.ceil(len(ordered)*.2))-1]
            for symbol in members:
                leader=symbol in performance and performance[symbol]>=threshold
                streak[symbol]=streak[symbol]+1 if leader else 0
                members[symbol].update(leader=leader,leader_streak_sessions=streak[symbol],
                    leadership_eligible_members=len(performance))
        else:
            streak={s:0 for s in prices}
        if day<start:continue
        for cell in ma.values():cell["percent_above"]=fraction(cell["above"],cell["eligible"])
        out.append(dict(session=day,requested_members=len(prices),**counts,
            percent_advancing=fraction(counts["advances"],counts["advance_decline_eligible"]),
            net_advances=counts["advances"]-counts["declines"] if counts["advance_decline_eligible"] else None,
            advance_decline_ratio=Decimal(counts["advances"])/counts["declines"] if counts["declines"] else None,
            ratio_reason="no_declines" if counts["advance_decline_eligible"] and not counts["declines"] else
                "no_eligible_members" if not counts["advance_decline_eligible"] else None,
            moving_averages=ma,leaders=sorted(s for s,r in members.items() if r["leader"]),
            leadership_eligible_members=len(performance),calendar_id=CALENDAR_ID))
    return out,list(members.values())

def invoke(args,context,registry):
    at=cutoff(args,context);windows=args.get("ma_windows",(20,50,200))
    highlow=args.get("high_low_window",252);lead=args.get("leadership_window",20)
    end=min(args["end_date"],at[:10])
    start=max(CALENDAR_COVERAGE_START,date.fromisoformat(args["start_date"])-timedelta(days=2*max(*windows,highlow,lead)+20))
    days=[d.isoformat() for d in sessions(start,date.fromisoformat(end))]
    inputs={};sources=[]
    for symbol in args["symbols"]:
        try:
            series=price_series(symbol,start.isoformat(),end,at,context,registry)
            bound=series.metadata.get("adjustment_status")=="split_adjusted_excluding_distributions"
            inputs[symbol]={o.period_end:o.value for o in series.observations} if bound else {}
            sources.append(("breadth_member_source",dict(symbol=symbol,status="available" if bound else "price_basis_unavailable",source=series_source(series))))
        except StoreUnavailableError:
            inputs[symbol]={};sources.append(("breadth_member_source",dict(symbol=symbol,status="market_store_unavailable")))
        except ValidationError as exc:
            if "instrument is unavailable" not in str(exc):raise
            inputs[symbol]={};sources.append(("breadth_member_source",dict(symbol=symbol,status="identity_unavailable_at_cutoff")))
    daily,members=calculate(inputs,days,args["start_date"],windows,highlow,lead,context.checkpoint)
    return result("market.get_breadth",args,at,[*sources,*[("market_breadth_day",r) for r in daily],
        *[("breadth_latest_member",r) for r in members]],established=any(r["advance_decline_eligible"] for r in daily),
        warnings=(("explicit_cohort","Universe membership is the caller's explicit cohort. Historical membership and survivorship neutrality are not established. Percentages use eligible members; leader ties are included at the top-quintile cutoff."),))
