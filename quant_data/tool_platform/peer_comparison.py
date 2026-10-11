"""Transparent FMP peer selection with retained fundamental comparisons."""
from quant_data.json_codec import loads_strict
from .research_extensions_contracts import PEERS
from .retained_research_common import cutoff, compose, unpack, result
from .fundamental_screen import rank_rows
from quant_data.errors import StoreUnavailableError
from quant_data.market.collection_bindings import load_bindings, pin_binding
from quant_data.stores import acquire_write_session
from .sharadar_company_access import BINDINGS_PATH

def peer_links(context, at):
    # The established source-aware fundamental service joins providers through
    # explicit mappings of the same selected membership. Sharadar permatickers
    # need not carry an FMP instrument_id; do not invent one or join by ticker alone.
    with acquire_write_session(context.store_map, ("market",), timeout_seconds=1):
        bindings = load_bindings(BINDINGS_PATH)
        selections = [pin_binding(context.store_map, bindings[name], cutoff=at)
                      for name in ("fmp_statements", "sharadar_fundamentals")]
    fmp, sharadar = selections
    if fmp.membership_snapshot_id != sharadar.membership_snapshot_id: return {}
    by_symbol = {s.source_symbol:s for s in fmp.eligible}
    links = {}
    for subject in sharadar.eligible:
        other = by_symbol.get(subject.source_symbol)
        if other is None or not other.instrument_id: continue
        if (subject.instrument_id and subject.instrument_id != other.instrument_id
            or subject.cik and other.cik and subject.cik != other.cik): continue
        links[subject.source_symbol] = dict(instrument_id=other.instrument_id, cik=other.cik,
            membership_snapshot_id=fmp.membership_snapshot_id, fmp_mapping_id=fmp.mapping_id,
            sharadar_mapping_id=sharadar.mapping_id, provider_subject=subject.provider_subject,
            provider_symbol=subject.provider_symbol,
            identity_link_status="evidenced" if subject.instrument_id and subject.cik else "partial",
            basis="same_selected_membership_with_explicit_provider_mappings")
    return links

def matched_link(link, classification, source):
    if not link or not source: return None
    if (link["membership_snapshot_id"] != classification["membership_snapshot_id"]
        or link["instrument_id"] != classification["instrument_id"]
        or link["provider_subject"] != source.get("provider_subject")
        or link["provider_symbol"] != source.get("provider_symbol")
        or link["cik"] and classification.get("source_cik") and link["cik"] != classification["source_cik"]):
        return None
    return link

def invoke(args, context, registry):
    args = dict(args)
    at = cutoff(args, context)
    group = args["group_by"]
    # A bounded full classification lookup also makes oversized cohorts visible:
    # never silently rank an arbitrary first page as the full peer group.
    source = compose("market.get_classifications", "1.0.0", {"as_of": at}, context, registry)
    classifications = {v["ticker"]: v for r in source.records
        if r.record_type == "instrument_classification" for v in [unpack(r)]}
    anchor = classifications.get(args["symbol"])
    label = anchor.get(group) if anchor else None
    candidates = sorted(t for t, v in classifications.items() if label and v.get(group) == label)
    meta = dict(anchor_symbol=args["symbol"], group_by=group, group_label=label,
        classification_provider="fmp", candidate_symbols=candidates, candidate_count=len(candidates),
        selection="explicit_peers" if "peers" in args else "complete_retained_classification_group",
        rank_scope="selected_comparable_rows_only", max_comparison_symbols=20,
        historical_effective_classifications="not_established", provider_requests=0)
    if not label:
        meta["status"] = "anchor_classification_unavailable"
        return result(PEERS, args, at, [("peer_selection", meta)], established=False, lineage=source.lineage)
    requested = [args["symbol"], *args["peers"]] if "peers" in args else candidates
    if len(requested) > 20:
        meta["status"] = "explicit_peer_selection_required"
        return result(PEERS, args, at, [("peer_selection", meta)], established=False, lineage=source.lineage)
    matched = [t for t in requested if t in candidates]
    excluded = [("peer_exclusion", dict(symbol=t, reason="classification_unavailable" if t not in classifications
        else "outside_anchor_classification_group", classification=classifications.get(t)))
        for t in requested if t not in candidates]
    screen_args = {k: v for k, v in args.items() if k not in ("symbol", "peers", "group_by")}
    screen_args.update(symbols=matched, as_of=at)
    screen = compose("company.screen_fundamentals", "1.0.0", screen_args, context, registry)
    try:
        links = peer_links(context, at)
    except StoreUnavailableError:
        links = {}
    rows = []
    for record in screen.records:
        row = {k[:-5] if k.endswith("_json") else k: loads_strict(v) if k.endswith("_json") else v
               for k, v in unpack(record).items()}
        symbol = row["symbol"]
        classification = classifications[symbol]
        link = matched_link(links.get(symbol), classification, row.get("source"))
        row.update(classification=classification, is_anchor=symbol == args["symbol"],
                   cross_source_identity=link["basis"] if link else "not_established", identity_link=link)
        if row["status"] == "available" and not link:
            row.update(status="cross_source_identity_unavailable", cohort=None)
        # A requested period mismatch must never enter the comparison rankings.
        if row["status"] != "available": row["cohort"] = None
        row.pop("rank_cohort_size", None)
        rows.append(row)
    rank_by = args.get("rank_by", "revenue_growth_yoy")
    monetary = rank_by in ("revenue", "forward_eps_proxy")
    for row in rows:
        row["fundamental_cohort"] = row.get("cohort")
        if row.get("cohort"):
            row["cohort"] = dict(row["cohort"])
            if not monetary:
                row["cohort"]["currency"] = "not_applicable_dimensionless_metric"
    ranked = rank_rows(rows, args.get("criteria", ()), rank_by, args.get("ascending", False))
    for row in ranked:
        row["ranking_cohort"] = row["cohort"]
        row["cohort"] = row.pop("fundamental_cohort")
        row["ranking_status"] = ("ranked" if row["rank"] is not None else
            "source_or_identity_unavailable" if row["status"] != "available" else
            "metric_missing" if row["metrics"].get(rank_by) is None else "currency_unavailable")
    meta.update(status="compared", selected_symbols=matched, excluded_count=len(excluded),
                available_count=sum(r["status"] == "available" for r in rows))
    return result(PEERS, args, at, [("peer_selection", meta), *excluded,
        *(("peer_comparison_row", r) for r in ranked)], lineage=source.lineage,
        established=meta["available_count"] > 0,
        warnings=(("peer_comparability", "FMP labels are not official GICS. Ranks require linked identities and matching dimension, calendar period and metric date where applicable. Money-value ranks require known matching currency; dimensionless ratios do not. No currency conversion is inferred."),))
