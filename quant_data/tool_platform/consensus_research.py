"""Retained consensus snapshots and adjacent comparable-capture revisions."""
from collections import defaultdict
from quant_data.company.retained_analyst_reader import read_analyst, provenance
from .retained_research_common import cutoff, number, result, digest

METRICS = ("epsAvg", "revenueAvg", "ebitdaAvg", "ebitAvg", "netIncomeAvg")
UNKNOWN = {None, "", "SOURCE_UNSPECIFIED", "not_established"}

def snapshots(args, context, at):
    rows = read_analyst(context,args["symbol"],"analyst-estimates",at,
                        period=args.get("period","quarter"),history=True,since=args.get("since"))
    return [r for r in rows if args["start_date"] <= r["target_period_end"] <= args["end_date"]]

def compare(left, right, metric):
    a,b = number(left["payload"].get(metric)),number(right["payload"].get(metric))
    reason = None
    if (left["issuer_id"], left["instrument_id"], left["request_period"],left["target_period_end"]) != (
        right["issuer_id"],right["instrument_id"],right["request_period"],right["target_period_end"]):
        reason = "incompatible_identity_or_period"
    elif left["currency"] != right["currency"]: reason = "currency_changed"
    elif left["currency"] in UNKNOWN: reason = "currency_unspecified"
    elif metric == "epsAvg" and (left["estimate_basis"] != right["estimate_basis"] or left["estimate_basis"] in UNKNOWN):
        reason = "estimate_basis_not_comparable"
    elif a is None or b is None: reason = "missing_numeric_estimate"
    delta = b-a if reason is None else None
    return dict(metric=metric, previous_value=a, current_value=b, change=delta,
        change_pct=(delta/abs(a)*100) if delta is not None and a else None,
        percent_reason="zero_baseline" if reason is None and a==0 else reason,
        direction=None if delta is None else "up" if delta>0 else "down" if delta<0 else "unchanged",
        comparison_status="comparable_source_values" if reason is None else "not_established",
        exclusion_reason=reason, previous_analyst_count=left["payload"].get(
            "numAnalystsEps" if metric=="epsAvg" else "numAnalystsRevenue"),
        current_analyst_count=right["payload"].get("numAnalystsEps" if metric=="epsAvg" else "numAnalystsRevenue"),
        analyst_population="aggregate_composition_not_identified")

def revisions(args, context, registry):
    at = cutoff(args,context)
    source = snapshots(args,context,at)
    grouped=defaultdict(list)
    for row in source:
        grouped[(row["issuer_id"],row["instrument_id"],row["request_period"],row["target_period_end"])].append(row)
    rows=[]
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda r:(r["capture_observed_at"],r["capture_id"]))
        for before,after in zip(values,values[1:]):
            if args.get("since") and after["capture_observed_at"] <= args["since"]: continue
            for metric in METRICS:
                if metric not in before["payload"] and metric not in after["payload"]: continue
                fields=dict(symbol=args["symbol"],period=key[2],target_period_end=key[3],
                    previous_source=provenance(before), current_source=provenance(after),
                    **compare(before,after,metric))
                fields["change_id"]=digest([before["capture_id"],after["capture_id"],metric])
                rows.append(("estimate_revision",fields))
    rows.append(("revision_coverage",dict(symbol=args["symbol"],retained_capture_count=len({r["capture_id"] for r in source}),
        retained_target_periods=len(grouped),comparisons=len(rows),availability="saved_capture_history_only")))
    return result("company.get_estimate_revisions",args,at,rows,established=len(rows)>1)

def history(args, context, registry):
    at=cutoff(args,context)
    source=snapshots(args,context,at)
    rows=[("consensus_snapshot",dict(**provenance(r),source_payload=r["payload"])) for r in source
          if not args.get("since") or r["capture_observed_at"]>args["since"]]
    if args.get("include_analyst_context",True):
        for endpoint in ("grades-consensus","price-target-consensus","price-target-summary","grades","price-target"):
            values=read_analyst(context,args["symbol"],endpoint,at)
            values.sort(key=lambda r:(r["source_time_raw"] or "",r["natural_identity"]),reverse=True)
            rows.extend(("analyst_context",dict(**provenance(r),source_payload=r["payload"])) for r in values[:20])
            if len(values)>20:
                rows.append(("analyst_context_coverage",dict(endpoint=endpoint,returned_count=20,total_known_count=len(values),
                    omitted_count=len(values)-20,ordering="source_time_desc",truncated=True)))
    return result("company.get_consensus_history",args,at,rows)
