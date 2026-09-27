"""Deterministic SPY selection and compact full/sample/remainder research summaries."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib, json, math, statistics
from zoneinfo import ZoneInfo
from .universe import TIER1_ETFS

VERSION = "theta_spy_compact_v1"
TARGET_DTE = (1,2,3,7,14,30,60,90,180,365)
TARGET_MONEYNESS = tuple(Decimal(str(x)) for x in (.8,.9,.95,.975,.99,1,1.01,1.025,1.05,1.1,1.2))
DTE_LABELS = ("0","1-7","8-30","31-90","91-180","181-365","366+")
MONEY_LABELS = ("lt80","80-90","90-95","95-99","99-101","101-105","105-110","110-120","120+")
NY = ZoneInfo("America/New_York")

def canonical_json(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)

def decimal(value):
    if value is None or isinstance(value,bool): return None
    try: result=Decimal(str(value))
    except InvalidOperation: return None
    return result if result.is_finite() else None

def integer(value):
    x=decimal(value)
    return int(x) if x is not None and x>=0 and x==int(x) else None

def key(row,symbol="SPY"):
    if symbol not in TIER1_ETFS or row.get("symbol")!=symbol: raise ValueError("unexpected_option_root")
    expiry=date.fromisoformat(row["expiration"]).isoformat()
    strike=decimal(row.get("strike"))
    right={"CALL":"C","PUT":"P","C":"C","P":"P"}.get(row.get("right"))
    if strike is None or strike<=0 or right is None: raise ValueError("invalid_contract_identity")
    return (symbol,expiry,format(strike.normalize(),"f"),right)

def contract_id(k):
    return "|".join(k)

def stamp(value):
    if not value: return None
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None: raise ValueError("naive_provider_timestamp")
    return parsed

def spread(row):
    bid,ask=decimal(row.get("bid")),decimal(row.get("ask"))
    if bid is None or ask is None or bid<=0 or ask<bid: return None
    return float((ask-bid)/((ask+bid)/2))

def dte_bucket(days):
    for upper,label in zip((0,7,30,90,180,365),DTE_LABELS):
        if days<=upper:return label
    return "366+"

def money_bucket(ratio):
    for bound,label in zip((.8,.9,.95,.99,1.01,1.05,1.1,1.2),MONEY_LABELS):
        if ratio<Decimal(str(bound)):return label
    return "120+"

def roundness(strike):
    value=Decimal(strike)
    if value%10==0:return "multiple_10"
    if value%5==0:return "multiple_5_only"
    return "other"

def known_sum(values):
    clean=[x for x in values if x is not None]
    return sum(clean) if clean or not values else None

def select(rows,spot,session,*,symbol="SPY"):
    """Fixed anchors plus two round and two activity additions; maximum 300."""
    grouped=defaultdict(dict)
    for k,row in rows.items():
        dte=(date.fromisoformat(k[1])-session).days
        if 1<=dte<=730: grouped[k[1]].setdefault(k[2],{})[k[3]]=row
    eligible=[expiry for expiry,strikes in grouped.items()
              if any("C" in rights for rights in strikes.values()) and any("P" in rights for rights in strikes.values())]
    chosen_expiries=defaultdict(list)
    for target in TARGET_DTE:
        if eligible:
            expiry=min(eligible,key=lambda e:(abs((date.fromisoformat(e)-session).days-target),e))
            chosen_expiries[expiry].append(target)
    chosen={}
    manifest=[]
    for expiry,targets in sorted(chosen_expiries.items()):
        strikes=grouped[expiry]
        levels={}
        def add(value,reason):
            levels.setdefault(value,[]).append(reason)
        for target in TARGET_MONEYNESS:
            s=min(strikes,key=lambda x:(abs(Decimal(x)-spot*target),Decimal(x)))
            if abs(Decimal(s)/spot-target)<=Decimal(".025"):
                add(s,"anchor:"+str(target))
        candidates=[s for s in strikes if s not in levels and Decimal(s)%5==0
                    and Decimal(".8")<=Decimal(s)/spot<=Decimal("1.2")]
        for side in (-1,1):
            side_candidates=[s for s in candidates if (Decimal(s)-spot)*side>0]
            if side_candidates:
                s=min(side_candidates,key=lambda x:(abs(Decimal(x)-spot),Decimal(x)%10!=0,Decimal(x)))
                add(s,"round");candidates.remove(s)
        round_used=sum("round" in reasons for reasons in levels.values())
        for s in sorted(candidates,key=lambda x:(abs(Decimal(x)-spot),Decimal(x)%10!=0,Decimal(x)))[:2-round_used]:
            add(s,"round")
        for metric in ("volume","oi"):
            scores=[]
            for s,rights in strikes.items():
                if s in levels or set(rights)!={"C","P"}:continue
                vals=[integer(rights[r].get(metric)) for r in ("C","P")]
                if None in vals or sum(vals)<=0:continue
                quotes=[spread(rights[r]) for r in ("C","P")]
                good=sum(v is not None for v in quotes)
                width=sum(v for v in quotes if v is not None) if good else math.inf
                scores.append((-sum(vals),-good,width,abs(Decimal(s)-spot),Decimal(s),s))
            if scores:add(min(scores)[-1],"leader:"+metric)
        if len(levels)>15:raise AssertionError("strike_cap")
        for s,reasons in sorted(levels.items(),key=lambda x:Decimal(x[0])):
            for right,row in strikes[s].items():
                k=(symbol,expiry,s,right)
                chosen[k]=list(reasons)
            manifest.append({"expiration":expiry,"dte_targets":targets,"strike":s,
                "actual_moneyness":str(Decimal(s)/spot),"reasons":reasons})
    if len(chosen)>300:raise AssertionError("contract_cap")
    return chosen,manifest

def metric_stats(items):
    volumes=[r["volume"] for _,r in items]
    oi=[r["oi"] for _,r in items]
    widths=sorted(w for _,r in items if (w:=spread(r)) is not None)
    result={"contracts":len(items),"volume":known_sum(volumes),"volume_missing":volumes.count(None),
            "open_interest":known_sum(oi),"oi_missing":oi.count(None),
            "trade_count":known_sum([integer(r.get("count")) for _,r in items]),
            "traded_contracts":sum(v is not None and v>0 for v in volumes),
            "valid_two_sided_quotes":len(widths),
            "relative_spread_median":statistics.median(widths) if widths else None,
            "relative_spread_p90":widths[max(0,math.ceil(.9*len(widths))-1)] if widths else None,
            "spread_quantile_method":"unweighted median and nearest-rank p90",
            "zero_bid":sum(decimal(r.get("bid"))==0 for _,r in items),
            "crossed":sum(decimal(r.get("bid")) is not None and decimal(r.get("ask")) is not None
                          and decimal(r["bid"])>decimal(r["ask"]) for _,r in items),
            "missing_quote":sum(decimal(r.get("bid")) is None or decimal(r.get("ask")) is None for _,r in items),
            "bid_size_sum":known_sum([integer(r.get("bid_size")) for _,r in items]),
            "ask_size_sum":known_sum([integer(r.get("ask_size")) for _,r in items]),
            "quote_age_state":"not_observable_from_eod_trade_timestamp"}
    for label,values in (("volume",volumes),("oi",oi)):
        known=[v for v in values if v is not None]
        total=sum(known)
        result[label+"_hhi"]=sum((v/total)**2 for v in known) if total>0 else None
        result[label+"_top5_share"]=sum(sorted(known,reverse=True)[:5])/total if total>0 else None
    result["roundness"]={}
    for kind in ("multiple_10","multiple_5_only","other"):
        members=[r for k,r in items if roundness(k[2])==kind]
        result["roundness"][kind]={"contracts":len(members),
            "volume":known_sum([r["volume"] for r in members]),
            "oi":known_sum([r["oi"] for r in members]),
            "volume_missing":sum(r["volume"] is None for r in members),
            "oi_missing":sum(r["oi"] is None for r in members)}
    return result

def summarize(rows,selected):
    summaries,leaders=[],[]
    for population in ("full","selected","remainder"):
        items=[(k,r) for k,r in sorted(rows.items()) if population=="full" or ((k in selected)==(population=="selected"))]
        for right in ("C","P"):
            subset=[(k,r) for k,r in items if k[3]==right]
            groups=[("overall","all",subset)]
            groups += [("dte",label,[(k,r) for k,r in subset if r["dte_bucket"]==label]) for label in DTE_LABELS]
            groups += [("moneyness",label,[(k,r) for k,r in subset if r["moneyness_bucket"]==label]) for label in MONEY_LABELS]
            for dimension,bucket,members in groups:
                summaries.append({"population":population,"right":right,"dimension":dimension,
                                  "bucket":bucket,"stats":metric_stats(members)})
            if population!="selected":
                for metric in ("volume","oi"):
                    total=sum(r[metric] for _,r in subset if r[metric] is not None)
                    rankable=[(k,r) for k,r in subset if r[metric] is not None and r[metric]>0]
                    for rank,(k,r) in enumerate(sorted(rankable,key=lambda x:(-x[1][metric],x[0]))[:5],1):
                        leaders.append({"population":population,"right":right,"metric":metric,"rank":rank,
                            "contract_id":contract_id(k),"expiration":k[1],"strike":k[2],"value":r[metric],
                            "share":r[metric]/total if total else None})
    return summaries,leaders

def build_capture(session_text,greeks,oi,receipts,*,previous_session,spot_reference=None,symbol="SPY"):
    if symbol not in TIER1_ETFS:raise ValueError("unexpected_option_root")
    session=date.fromisoformat(session_text)
    if date.fromisoformat(previous_session)>=session:raise ValueError("invalid_previous_session")
    cutoff=datetime.combine(session,time(23,59,59,999999),NY)
    if not greeks:raise ValueError("missing_eod_greeks")
    latest_oi={}
    rejected_oi=0
    for row in oi:
        k=key(row,symbol); ts=stamp(row.get("timestamp"))
        if ts is None or ts>cutoff or ts.astimezone(NY).date()!=session:
            rejected_oi+=1;continue
        prior=latest_oi.get(k)
        if prior is None or ts>stamp(prior["timestamp"]):latest_oi[k]=row
        elif ts==stamp(prior["timestamp"]) and canonical_json(prior)!=canonical_json(row):
            raise ValueError("conflicting_oi_duplicate")
    normalized={}
    spot_counts=Counter()
    ignored_expired=0
    for source in greeks:
        k=key(source,symbol)
        if date.fromisoformat(k[1])<session:
            ignored_expired+=1;continue
        if k in normalized:
            if canonical_json(normalized[k]["source"])!=canonical_json(source):
                raise ValueError("conflicting_eod_duplicate")
            continue
        # Plain EOD has a report-created time; combined Greeks expose a last-trade
        # timestamp. An old trade is valid, a future trade/report is not.
        created=stamp(source.get("created"))
        event=stamp(source.get("timestamp"))
        last_trade=stamp(source.get("last_trade"))
        if created is not None:
            if created>cutoff or created.astimezone(NY).date()!=session:
                raise ValueError("eod_report_session")
        elif event is None:
            raise ValueError("missing_eod_time")
        if any(ts is not None and ts>cutoff for ts in (event,last_trade)):
            raise ValueError("future_eod_time")
        spot=decimal(source.get("underlying_price"))
        observed=stamp(source.get("underlying_timestamp"))
        iv=decimal(source.get("implied_vol"))
        reference_valid=spot is not None and spot>0 and observed is not None and observed.astimezone(NY).date()==session and observed<=cutoff
        if iv is not None and iv>0 and not reference_valid:
            raise ValueError("invalid_greek_reference_time")
        if reference_valid:
            spot_counts[spot]+=1
        row=dict(source)
        row.update(source=source,volume=integer(source.get("volume")),
            oi=integer(latest_oi[k].get("open_interest")) if k in latest_oi else None,
            oi_source=latest_oi.get(k),oi_effective_date=previous_session if k in latest_oi else None,
            deliverable_state="provider_root_only_unverified",
            dte=(date.fromisoformat(k[1])-session).days)
        normalized[k]=row
    reference_check=None
    if spot_counts:
        spot,count=sorted(spot_counts.items(),key=lambda item:(-item[1],item[0]))[0]
        if count/sum(spot_counts.values())<.9:raise ValueError("inconsistent_underlying_reference")
        used_reference=None
    else:
        if not spot_reference:raise ValueError("missing_contemporaneous_spot")
        if spot_reference.get("metadata",{}).get("provider_symbol",symbol)!=symbol:
            raise ValueError("fallback_reference_symbol")
        spot=decimal(spot_reference["value"])
        if spot is None or spot<=0:raise ValueError("invalid_fallback_reference")
        if spot_reference["observation"]["period_start"]!=session_text:
            raise ValueError("fallback_reference_date")
        # A split-adjusted close is never silently relabelled as an as-traded quote.
        # Validate near-expiry put/call midpoint compatibility; retain proxy status.
        paired=defaultdict(dict)
        for k,row in normalized.items():
            if 1<=row["dte"]<=14 and abs(Decimal(k[2])/spot-1)<=Decimal(".05") and spread(row) is not None:
                paired[(k[1],k[2])][k[3]]=row
        parity=defaultdict(list)
        for (expiry,strike),rights in paired.items():
            if set(rights)!={"C","P"}:continue
            mid=lambda r:(Decimal(r["bid"])+Decimal(r["ask"]))/2
            parity[expiry].append(Decimal(strike)+mid(rights["C"])-mid(rights["P"]))
        usable=[expiry for expiry,values in parity.items() if len(values)>=3]
        if not usable:raise ValueError("fallback_reference_not_crosschecked")
        expiry=min(usable);estimate=statistics.median(parity[expiry])
        if abs(estimate/spot-1)>Decimal(".01"):raise ValueError("fallback_reference_basis_mismatch")
        reference_check={"expiry":expiry,"pairs":len(parity[expiry]),"parity_median":str(estimate),
            "relative_gap":str(abs(estimate/spot-1)),"status":"compatibility_proxy_not_raw_basis_proof"}
        used_reference=spot_reference
    for k,row in normalized.items():
        row["dte_bucket"]=dte_bucket(row["dte"])
        row["moneyness_bucket"]=money_bucket(Decimal(k[2])/spot)
    selected,manifest=select(normalized,spot,session,symbol=symbol)
    summaries,leaders=summarize(normalized,selected)
    details=[]
    for k,reasons in sorted(selected.items()):
        row=normalized[k]
        source=row["source"]
        valid_quote=spread(source) is not None
        iv=decimal(source.get("implied_vol"))
        iv_error=decimal(source.get("iv_error"))
        details.append({"contract_id":contract_id(k),"expiration":k[1],"strike":k[2],"right":k[3],
            "reasons":reasons,"volume":row["volume"],"oi":row["oi"],
            "bid":source.get("bid"),"ask":source.get("ask"),"implied_vol":source.get("implied_vol"),
            "iv_quality":"usable_root_proxy" if valid_quote and iv is not None and iv>0 and iv_error is not None and 0<=iv_error<=Decimal(".1") else "excluded",
            "deliverable_state":row["deliverable_state"],"trade_prices_state":"present" if integer(source.get("count")) else "no_eligible_trade",
            "eod_greeks":source,"open_interest":row["oi_source"],"oi_effective_date":row["oi_effective_date"]})
    digest=hashlib.sha256()
    for k,row in sorted(normalized.items()):
        digest.update(canonical_json({"key":k,"eod":row["source"],"oi":row["oi_source"]}).encode())
        digest.update(b"\n")
    source_semantic=digest.hexdigest()
    has_greeks=any(r["source"].get("implied_vol") is not None for r in normalized.values())
    policy={"selection":VERSION if symbol=="SPY" else "theta_etf_compact_v1","max_contracts":300,"max_strike_gap":".025",
            "greeks_version":"1","underlyer_use_nbbo":True,"annual_dividend":"provider_default",
            "rate_type":"sofr" if has_greeks else None,"greeks_state":"provider" if has_greeks else "unavailable",
            "spot_basis":"theta_underlying_reference" if used_reference is None else "retained_fmp_split_only_close_compatibility_proxy",
            "quantiles":"unweighted_median_nearest_rank_p90",
            "identity_scope":f"exact_theta_{symbol}_root; deliverables unverified",
            "retention":"selected_extracted_fields_and_summary_only",
            "source_replayable":False,"historical_vintage":"current_provider_history_not_point_in_time_revisions"}
    semantic=hashlib.sha256(canonical_json({"session":session_text,"previous_session":previous_session,
        "spot":str(spot),"source":source_semantic,"policy":policy,"reference":used_reference}).encode()).hexdigest()
    totals={(s["population"],s["right"]):s["stats"] for s in summaries if s["dimension"]=="overall"}
    coverage={}
    for field in ("volume","open_interest"):
        full=sum(totals[("full",r)][field] or 0 for r in ("C","P"))
        sampled=sum(totals[("selected",r)][field] or 0 for r in ("C","P"))
        coverage[field+"_captured_share"]=sampled/full if full else None
    coverage.update(greeks_contracts=sum(r["source"].get("implied_vol") is not None for r in normalized.values()),eod_contracts=len(normalized),selected_contracts=len(details),
        oi_matched=sum(r["oi"] is not None for r in normalized.values()),
        ignored_expired=ignored_expired,rejected_oi_time=rejected_oi,
        expected_catalogue_count=None,chain_completeness="observed_complete_streams_catalogue_unverified")
    return {"symbol":symbol,"session":session_text,"semantic_sha256":semantic,"spot":str(spot),
        "policy":policy,"coverage":coverage,"selection":manifest,"details":details,
        "summaries":summaries,"leaders":leaders,"receipts":receipts,
        "source_semantic_sha256":source_semantic,"underlying_reference":used_reference,
        "underlying_reference_check":reference_check,"availability_state":"source_release_time_unverified",
        "captured_at":max((r["captured_at"] for r in receipts),default=None)}
