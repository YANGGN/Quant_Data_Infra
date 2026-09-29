"""Finite, missingness-aware snapshot calculations. Raw contracts remain in memory."""
from __future__ import annotations
from datetime import date, datetime
from hashlib import sha256
from statistics import median
import json, math
from .policy import NY

def number(value):
    if value is None or isinstance(value,bool):return None
    try:out=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return out if math.isfinite(out) else None

def nonnegative(value):
    out=number(value)
    return out if out is not None and out>=0 else None

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)

def timestamp(value):
    if not isinstance(value,str):return None
    try:out=datetime.fromisoformat(value)
    except ValueError:return None
    return out if out.tzinfo is not None else None

def contract(row,symbol):
    if row.get("symbol",symbol)!=symbol:raise ValueError("snapshot_cross_symbol")
    expiry=date.fromisoformat(row["expiration"])
    strike=number(row.get("strike"))
    right={"CALL":"C","PUT":"P","C":"C","P":"P"}.get(row.get("right"))
    if strike is None or strike<=0 or right is None:raise ValueError("snapshot_identity")
    return (expiry.isoformat(),format(strike,".8f").rstrip("0").rstrip("."),right)

def aggregate(symbol,session,captured_at,rows,*,oi_rows=None,iv_rows=None,oi_effective_session=None):
    if not isinstance(rows,list) or len(rows)>200000:raise ValueError("snapshot_row_cap")
    day=date.fromisoformat(session)
    when=timestamp(captured_at)
    if when is None or when.astimezone(NY).date()!=day:raise ValueError("snapshot_time")
    values={};seen={};missing=0;count_missing=0;bad_time=0;prior_session_rows=0;active_rows=0
    for row in rows:
        key=contract(row,symbol)
        if key in seen:
            if canonical(row)!=canonical(seen[key]):raise ValueError("snapshot_duplicate_conflict")
            continue
        seen[key]=row
        if date.fromisoformat(key[0])<day:continue
        stamp=timestamp(row.get("timestamp") or row.get("created"))
        if stamp is None or stamp>when:
            bad_time+=1;continue
        if stamp.astimezone(NY).date()<day:
            # A latest OHLC from an older session contributes no current-session
            # activity. Preserve its identity so a first trade today does not
            # manufacture a contract-population change; never carry its volume.
            values[key]={**row,"volume":0,"count":0}
            prior_session_rows+=1
            continue
        active_rows+=1
        volume=nonnegative(row.get("volume"))
        if volume is None or int(volume)!=volume:missing+=1
        trades=nonnegative(row.get("count"))
        if trades is None or int(trades)!=trades:count_missing+=1
        values[key]=row
    keys=sorted(values)
    fingerprint=sha256(canonical(keys).encode()).hexdigest()
    totals={"C":0,"P":0};trades=0;volume_rank=[];zero=0;near=0
    for key in keys:
        row=values[key];vol=nonnegative(row.get("volume"));cnt=nonnegative(row.get("count"))
        if vol is not None:totals[key[2]]+=int(vol)
        if cnt is not None:trades+=int(cnt)
        dte=(date.fromisoformat(key[0])-day).days
        if vol is not None:
            if dte==0:zero+=vol
            if 0<=dte<=7:near+=vol
            volume_rank.append((vol,key))
    complete=active_rows>0 and missing==0 and bad_time==0
    total=sum(totals.values()) if complete else None
    leaders=[]
    if total:
        for vol,key in sorted(volume_rank,key=lambda x:(-x[0],x[1]))[:5]:
            if vol>0:leaders.append({"expiration":key[0],"strike":float(key[1]),"right":key[2],
                                      "volume":int(vol),"share":vol/total})
    oi_total=None;oi_effective=None;oi_fingerprint=None
    if oi_rows is not None:
        if not isinstance(oi_rows,list) or len(oi_rows)>200000:raise ValueError("oi_row_cap")
        mapping={};invalid=0;report_dates=set()
        for row in oi_rows:
            key=contract(row,symbol);val=nonnegative(row.get("open_interest"));stamp=timestamp(row.get("timestamp"))
            if key in mapping or val is None or int(val)!=val or stamp is None or stamp>when:
                invalid+=1;continue
            mapping[key]=int(val);report_dates.add(stamp.astimezone(NY).date().isoformat())
        if not invalid and set(mapping)==set(keys) and report_dates=={session}:
            oi_total=sum(mapping.values())
            if oi_effective_session and date.fromisoformat(oi_effective_session)<day:
                oi_effective=oi_effective_session
            oi_fingerprint=sha256(canonical(sorted(mapping)).encode()).hexdigest()
    iv=iv_summary(iv_rows or [],day,when,symbol=symbol) if iv_rows is not None else {}
    flags=[]
    if not complete:flags.append("incomplete_volume")
    if bad_time:flags.append("future_row")
    if prior_session_rows:flags.append("prior_session_ohlc_excluded")
    if oi_rows is not None and oi_total is None:flags.append("incompatible_oi_coverage")
    return {"symbol":symbol,"session":session,"captured_at":when.isoformat(),
        "call_volume":totals["C"] if complete else None,"put_volume":totals["P"] if complete else None,
        "trade_count":trades if not count_missing and complete else None,
        "zero_dte_share":zero/total if total else None,"near_term_share":near/total if total else None,
        "top5_share":sum(x["volume"] for x in leaders)/total if total else None,
        "leaders":leaders,"open_interest":oi_total,"oi_effective_date":oi_effective,
        "volume_oi":total/oi_total if total is not None and oi_total and oi_effective else None,
        "underlying_price":iv.get("underlying_price"),"atm_iv_7":iv.get("atm_iv_7"),
        "atm_iv_30":iv.get("atm_iv_30"),"atm_iv_90":iv.get("atm_iv_90"),
        "skew":iv.get("skew"),"relative_spread":iv.get("relative_spread"),
        "coverage":"complete" if complete else "incomplete",
        "quality_flags":flags+iv.get("quality_flags",[]),
        "contract_count":len(keys),"active_contract_count":active_rows,
        "_fingerprint":fingerprint,"_oi_fingerprint":oi_fingerprint,"_contracts":len(keys)}

def iv_summary(rows,day,when,*,symbol):
    """Fresh same-symbol quotes; 30-day skew pairs rights at each maturity."""
    candidates={};spreads=[];spots=[];delta_pairs={}
    for row in rows:
        if row.get("symbol")!=symbol:
            raise ValueError("iv_cross_symbol")
        try:expiry=date.fromisoformat(row["expiration"])
        except (KeyError,TypeError,ValueError):continue
        dte=(expiry-day).days;iv=number(row.get("implied_vol"));spot=number(row.get("underlying_price"))
        ref=timestamp(row.get("underlying_timestamp"));stamp=timestamp(row.get("timestamp"))
        strike=number(row.get("strike"));right={"CALL":"C","PUT":"P","C":"C","P":"P"}.get(row.get("right"))
        if dte<=0 or iv is None or iv<=0 or spot is None or spot<=0 or strike is None or right is None:
            continue
        if ref is None or ref.astimezone(NY).date()!=day or ref>when or (when-ref).total_seconds()>600:continue
        if stamp is None or stamp.astimezone(NY).date()!=day or stamp>when or (when-stamp).total_seconds()>600:continue
        bid=number(row.get("bid"));ask=number(row.get("ask"))
        if bid is None or ask is None or bid<=0 or ask<bid:continue
        spreads.append((ask-bid)/((ask+bid)/2));spots.append(spot)
        error=abs(strike/spot-1)
        current=candidates.get(dte)
        if current is None or error<current[0]:candidates[dte]=(error,iv)
        delta=number(row.get("delta"))
        if delta is not None and ((right=="C" and .20<=delta<=.30) or
                                  (right=="P" and -.30<=delta<=-.20)):
            rights=delta_pairs.setdefault(dte,{})
            distance=abs(abs(delta)-.25)
            selected=rights.get(right)
            if selected is None or distance<selected[0]:rights[right]=(distance,iv)
    out={"quality_flags":[]}
    if not candidates:
        out["quality_flags"].append("iv_unavailable");return out
    if max(spots)-min(spots)>max(spots)*.01:
        out["quality_flags"].append("underlying_inconsistent");return out
    out["underlying_price"]=median(spots)
    out["relative_spread"]=median(spreads)
    def fixed_variance(points,target):
        lower=max((d for d in points if d<=target),default=None)
        upper=min((d for d in points if d>=target),default=None)
        if lower is None or upper is None:return None
        if lower==upper:return points[lower]
        lv=points[lower]**2*lower;uv=points[upper]**2*upper
        variance=lv+(uv-lv)*(target-lower)/(upper-lower)
        return math.sqrt(max(variance/target,0))
    atm={d:value[1] for d,value in candidates.items()}
    for target in (7,30,90):out[f"atm_iv_{target}"]=fixed_variance(atm,target)
    paired={d:rights for d,rights in delta_pairs.items() if set(rights)=={"C","P"}}
    call_curve={d:rights["C"][1] for d,rights in paired.items()}
    put_curve={d:rights["P"][1] for d,rights in paired.items()}
    call_30=fixed_variance(call_curve,30)
    put_30=fixed_variance(put_curve,30)
    out["skew"]=(put_30-call_30)*100 if call_30 is not None and put_30 is not None else None
    if out["skew"] is None:out["quality_flags"].append("skew_coverage")
    out["quality_flags"].append("provider_trade_price_iv_proxy")
    return out

INTERVAL_BLOCKING=frozenset(("incomplete_volume","future_row","volume_correction",
    "contract_coverage_lost","coverage_comparison_unavailable","interval_time_invalid",
    "interval_session_changed"))
BASELINE_BLOCKING=INTERVAL_BLOCKING|{"interval_gap","cadence_changed"}

def baseline_eligible(observation,cadence):
    minutes=number(observation.get("interval_minutes"))
    value=nonnegative(observation.get("interval_volume"))
    return (observation.get("coverage")=="complete" and value is not None and
            minutes is not None and abs(minutes-cadence)<=1 and
            not BASELINE_BLOCKING.intersection(observation.get("quality_flags",())))

def derive(current,previous,baselines,anchor,*,cadence):
    """Full-chain cumulative deltas; only matched intervals feed alert baselines."""
    out={k:v for k,v in current.items() if not k.startswith("_")}
    out["quality_flags"]=list(dict.fromkeys(current["quality_flags"]))
    out["_fingerprint"]=current["_fingerprint"]
    out["cadence_minutes"]=cadence
    out["status"]="no_data" if current.get("_contracts",0)==0 else (
        "incomplete" if current["coverage"]!="complete" else "baseline_building")
    out.update(interval_volume=None,interval_minutes=None,interval_start=None,
               interval_call_volume=None,interval_put_volume=None,
               relative_volume=None,acceleration=None,baseline_sessions=0,
               put_call_ratio=None,iv_change=None)
    call,put=current["call_volume"],current["put_volume"]
    if call is not None and put is not None and call>0:out["put_call_ratio"]=put/call
    if anchor is not None and current["atm_iv_30"] is not None and anchor["atm_iv_30"] is not None:
        out["iv_change"]=(current["atm_iv_30"]-anchor["atm_iv_30"])*100
    if previous is None:return out
    if current["symbol"]!=previous["symbol"] or current["session"]!=previous["session"]:
        out["quality_flags"].append("interval_session_changed");return out
    if current["coverage"]!="complete" or previous["coverage"]!="complete":return out
    start=timestamp(previous["captured_at"]);end=timestamp(current["captured_at"])
    if start is None or end is None or start>=end or start.astimezone(NY).date().isoformat()!=current["session"]:
        out["quality_flags"].append("interval_time_invalid");return out
    minutes=(end-start).total_seconds()/60
    if previous.get("cadence_minutes") is not None and previous["cadence_minutes"]!=cadence:
        out["quality_flags"].append("cadence_changed")
    changed=current["_fingerprint"]!=previous["_fingerprint"]
    if changed and "contract_universe_changed" not in out["quality_flags"]:
        out["quality_flags"].append("contract_universe_changed")
    # Compact coverage counts catch net row loss without retaining raw contracts.
    # A successful full-chain response remains the provider completeness premise;
    # counts cannot prove the absence of simultaneous additions and omissions.
    counts=[(current.get(k),previous.get(k)) for k in ("contract_count","active_contract_count")]
    if any(a is not None and b is not None and a<b for a,b in counts):
        out["quality_flags"].append("contract_coverage_lost");return out
    if changed and any(a is None or b is None for a,b in counts):
        out["quality_flags"].append("coverage_comparison_unavailable");return out
    prior_call,prior_put=previous["call_volume"],previous["put_volume"]
    if any(nonnegative(v) is None for v in (call,put,prior_call,prior_put)):
        out["quality_flags"].append("incomplete_volume");return out
    if call<prior_call or put<prior_put:
        out["quality_flags"].append("volume_correction");return out
    calls=call-prior_call;puts=put-prior_put;delta=calls+puts
    out.update(interval_volume=delta,interval_call_volume=calls,interval_put_volume=puts,
               interval_start=start.isoformat(),interval_minutes=minutes)
    if abs(minutes-cadence)>1:out["quality_flags"].append("interval_gap")
    if not baseline_eligible(out,cadence):return out
    eligible=[b["interval_volume"] for b in baselines if baseline_eligible(b,cadence)]
    out["baseline_sessions"]=len(eligible)
    if len(eligible)>=20:
        typical=median(eligible[-60:])
        out["relative_volume"]=delta/typical if typical>0 else None
        out["status"]="ok"
    if baseline_eligible(previous,cadence) and previous["interval_volume"]>0 and abs(previous["interval_minutes"]-minutes)<=1:
        out["acceleration"]=delta/previous["interval_volume"]
    return out

def alert_for(observation,last_alert_at,*,enabled):
    blocking=BASELINE_BLOCKING|{"underlying_inconsistent"}
    if not enabled or observation["coverage"]!="complete" or blocking.intersection(observation["quality_flags"]):return None
    if observation["relative_volume"] is None or observation["relative_volume"]<3 or (observation["interval_volume"] or 0)<500:
        return None
    triggers=[]
    if observation["acceleration"] is not None and observation["acceleration"]>=3:triggers.append("acceleration")
    if observation["iv_change"] is not None and observation["iv_change"]>=2:triggers.append("IV rise")
    if observation["top5_share"] is not None and observation["top5_share"]>=.6:triggers.append("concentration")
    if not triggers:return None
    now=datetime.fromisoformat(observation["captured_at"])
    if last_alert_at and (now-datetime.fromisoformat(last_alert_at)).total_seconds()<1800:return None
    raw=observation["symbol"]+"|"+observation["session"]+"|"+observation["captured_at"]
    return {"id":sha256(raw.encode()).hexdigest()[:24],"symbol":observation["symbol"],
            "detected_at":observation["captured_at"],"severity":"high","title":"Unusual options activity",
            "reason":f'{observation["relative_volume"]:.1f}x matched interval baseline; '+", ".join(triggers)}
