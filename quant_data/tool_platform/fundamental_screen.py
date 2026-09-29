"""Transparent fundamental criteria and within-cohort ranks."""
from collections import defaultdict
from datetime import date,timedelta
from quant_data.errors import ValidationError,StoreUnavailableError
from quant_data.json_codec import loads_strict
from .retained_research_common import cutoff,compose,unpack,number,result,digest
from .retained_research_sources import valuation_context,price_series
from quant_data.market.collection_bindings import load_bindings,pin_binding
from quant_data.stores import acquire_write_session
from .sharadar_company_access import BINDINGS_PATH
from .retained_research_sources import series_source

def linked_instrument(context,symbol,at,source):
    if not source:return None
    with acquire_write_session(context.store_map,("market",),timeout_seconds=1):
        selection=pin_binding(context.store_map,load_bindings(BINDINGS_PATH)["sharadar_fundamentals"],cutoff=at)
    subject=next((s for s in selection.eligible if s.source_symbol==symbol),None)
    return subject.instrument_id if subject and subject.provider_subject==source.get("provider_subject") else None


METRICS=("revenue","grossmargin","netmargin","roe","roa","de","pe","pb","ps","evsales","evebitda",
         "revenue_growth_yoy","forward_pe_proxy","forward_eps_proxy","price_return_21_sessions")

def project(rows,metrics):
    if not rows: return dict(status="no_retained_fundamentals",metrics={m:None for m in metrics},missingness={m:"no_source_row" for m in metrics},source=None,cohort=None)
    rows=sorted(rows,key=lambda r:(r["reportperiod"],r["source_datekey"],r["available_at"]),reverse=True)
    latest=rows[0];values=loads_strict(latest["values_json"]);native_missing=loads_strict(latest["missingness_json"])
    currency=values.get("currency")
    output={m:number(values.get(m)) for m in metrics};missing={m:native_missing.get(m) or "source_metric_missing" for m in metrics if output[m] is None}
    if "revenue_growth_yoy" in metrics:
        prior=[r for r in rows[1:] if r["dimension"]==latest["dimension"] and
            int(r["calendardate"][:4])==int(latest["calendardate"][:4])-1 and r["calendardate"][4:]==latest["calendardate"][4:]]
        if len(prior)==1:
            old=loads_strict(prior[0]["values_json"]);a,b=number(old.get("revenue")),number(values.get("revenue"))
            span=(date.fromisoformat(latest["reportperiod"])-date.fromisoformat(prior[0]["reportperiod"])).days
            if currency and old.get("currency")==currency and a is not None and a>0 and b is not None and 350<=span<=380:
                output["revenue_growth_yoy"]=b/a-1;missing.pop("revenue_growth_yoy",None)
            else: missing["revenue_growth_yoy"]="incompatible_or_nonpositive_prior_year_revenue"
        else: missing["revenue_growth_yoy"]="unique_prior_year_period_unavailable"
    return dict(status="available",metrics=output,missingness=missing,
        source={k:latest.get(k) for k in ("capture_id","version_id","observation_id","provider_subject","provider_symbol",
            "available_at","reportperiod","calendardate","source_datekey","dimension","schema_id")},
        cohort=dict(dimension=latest["dimension"],calendar_date=latest["calendardate"],currency=currency),
        growth_prior_source=({k:prior[0].get(k) for k in ("version_id","capture_id","reportperiod")}
            if "revenue_growth_yoy" in metrics and output.get("revenue_growth_yoy") is not None else None))

def rank_rows(rows,criteria,rank_by,ascending):
    groups=defaultdict(list)
    for row in rows:
        exclusions=[]
        for item in criteria:
            value=row["metrics"].get(item["metric"])
            if value is None: exclusions.append(dict(metric=item["metric"],reason=row["missingness"].get(item["metric"],"missing_value")))
            elif ("minimum" in item and value<number(item["minimum"])) or ("maximum" in item and value>number(item["maximum"])):
                exclusions.append(dict(metric=item["metric"],reason="outside_requested_bounds"))
        row.update(passes=not exclusions and row["status"]=="available",exclusions=exclusions,rank=None,percentile=None)
        if row["metrics"].get(rank_by) is not None and row.get("cohort") and row["cohort"].get("currency"):
            groups[digest(row["cohort"])].append(row)
    for group in groups.values():
        ordered=sorted(group,key=lambda r:(r["metrics"][rank_by] if ascending else -r["metrics"][rank_by],r["symbol"]))
        previous=None;rank=0
        for i,row in enumerate(ordered):
            value=row["metrics"][rank_by]
            if i==0 or value!=previous: rank=i+1
            previous=value
            row.update(rank=rank,rank_cohort_size=len(group),percentile=100*(len(group)-rank)/(len(group)-1) if len(group)>1 else None)
    return sorted(rows,key=lambda r:(not r["passes"],digest(r.get("cohort")),r["rank"] or 10000,r["symbol"]))

def invoke(args,context,registry):
    at=cutoff(args,context);metrics=tuple(dict.fromkeys([*args.get("metrics",("revenue_growth_yoy","netmargin","de","pe")),
        args.get("rank_by","revenue_growth_yoy"),*(c["metric"] for c in args.get("criteria",()))]))
    rows=[]
    for symbol in args["symbols"]:
        context.checkpoint()
        try:
            source=compose("company.get_fundamentals","3.0.0",dict(symbol=symbol,dimension=args.get("dimension","ARQ"),
                mode="as_of",as_of=at,limit=20),context,registry)
            raw=[unpack(r) for r in source.records]
            if args.get("calendar_date"): raw=[r for r in raw if r["calendardate"]<=args["calendar_date"]]
            row=project(raw,metrics)
            if args.get("calendar_date") and row["cohort"] and row["cohort"]["calendar_date"]!=args["calendar_date"]:
                row.update(status="requested_period_unavailable")
        except StoreUnavailableError:
            row=project([],metrics);row["status"]="source_unavailable"
        except ValidationError as exc:
            if "outside the selected membership" not in str(exc): raise
            row=project([],metrics);row["status"]="outside_selected_membership"
        row["symbol"]=symbol
        extras=("forward_pe_proxy","forward_eps_proxy","price_return_21_sessions")
        instrument=None
        if any(m in metrics for m in extras):
            try:instrument=linked_instrument(context,symbol,at,row.get("source"))
            except StoreUnavailableError:pass
            for m in extras:
                if m in metrics:row["missingness"][m]="cross_source_identity_unavailable"
        if instrument and any(m in metrics for m in ("forward_pe_proxy","forward_eps_proxy")):
            valuation=valuation_context(symbol,dict(end_date=at[:10]),at,context,registry)
            summary=next((v for _,v in valuation if v.get("record_kind")=="forward_pe_summary"),{})
            # Symbol matches are insufficient if the saved publication lacks its identity.
            days=[v for _,v in valuation if v.get("record_kind")=="forward_pe_day"]
            if days and summary.get("instrument_id")==instrument:
                day=max(days,key=lambda r:r["trade_date"])
                for field,key in (("forward_pe_proxy","forward_pe"),("forward_eps_proxy","forward_eps")):
                    if field in metrics:
                        row["metrics"][field]=number(day.get(key))
                        if row["metrics"][field] is not None: row["missingness"].pop(field,None)
                row["valuation_source"]=dict(instrument_id=summary["instrument_id"],snapshot_id=summary.get("snapshot_id"),
                    snapshot_cutoff=summary.get("snapshot_cutoff"),trade_date=day["trade_date"],
                    estimate_cutoff=day.get("estimate_cutoff"),interpretation="reconstructed_proxy",
                    cross_source_identity="evidenced_instrument_link_metric_equivalence_not_asserted")
                if args.get("rank_by") in ("forward_pe_proxy","forward_eps_proxy"):
                    row["cohort"]["metric_date"]=day["trade_date"]
        if instrument and "price_return_21_sessions" in metrics:
            start=(date.fromisoformat(at[:10])-timedelta(days=60)).isoformat()
            try:
                prices=price_series(symbol,start,at[:10],at,context,registry,instrument_id=instrument)
                obs=prices.observations[-22:]
                if len(obs)==22 and all(o.value and o.value>0 for o in obs) and prices.metadata.get("adjustment_status")=="split_adjusted_excluding_distributions":
                    row["metrics"]["price_return_21_sessions"]=obs[-1].value/obs[0].value-1
                    row["missingness"].pop("price_return_21_sessions",None)
                    row["price_source"]=dict(instrument_id=prices.metadata["instrument_id"],
                        lineage_digest=prices.lineage_digest,start_date=obs[0].period_end,end_date=obs[-1].period_end,
                        basis="21_observed_sessions_calendar_completeness_not_established",source=series_source(prices))
                    if args.get("rank_by")=="price_return_21_sessions":row["cohort"]["metric_date"]=obs[-1].period_end
            except StoreUnavailableError: pass
            except ValidationError as exc:
                if "instrument is unavailable" not in str(exc): raise
        rows.append(row)
    ranked=rank_rows(rows,args.get("criteria",()),args.get("rank_by","revenue_growth_yoy"),args.get("ascending",False))
    return result("company.screen_fundamentals",args,at,[("fundamental_screen_row",r) for r in ranked],
        established=any(r["status"]=="available" for r in rows),
        warnings=(("cohort_ranking","Ranks use the explicit supplied cohort and matching dimension, calendar period and currency. They are not a whole-market ranking; vendor metrics and reconstructed valuation proxies retain separate meanings."),))
