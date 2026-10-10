"""Additive classification lookup, using the existing public result contract."""
from .classification_contracts import TOOL, MAX_RECORDS, schema
from ..market.classification_registry import DATASET


def entry():
    from .catalog import _build_forward_pe_entry, _versioned_schema_id
    from .results import query_result_schema
    result = _build_forward_pe_entry()
    graph = f"tool_platform.{TOOL}.v1"
    result.update(id=TOOL, family="market", owner="market", handler=graph, operation_graph_id=graph,
        stores=["market"], datasets=[DATASET],
        description="Read saved FMP-only sector/industry classifications for ticker batches or the full retained selected universe, with exact sector/industry filters and a local publication cutoff.",
        input_type="MarketClassificationsArgumentsV1", input_schema=schema(),
        input_schema_id=_versioned_schema_id(TOOL, "input", version="1.0.0"),
        output_schema_id=_versioned_schema_id(TOOL, "output", version="1.0.0"),
        output_schema=query_result_schema(TOOL, {"type": "object", "additionalProperties": False, "properties": {}, "required": []}),
        examples=[{}, {"tickers": ["AAPL", "NVDA"]}, {"sectors": ["Technology"], "industries": ["Semiconductors"]}],
        assumptions=["fmp_only_no_fallback", "host_selected_market_store", "immutable_read_under_physical_store_lock",
            "full_universe_means_retained_classification_population", "exact_selected_ticker_spelling",
            "exact_case_sensitive_labels_or_within_each_filter_and_between_filters", "unknown_tickers_remain_explicit_even_with_filters",
            "publication_cutoff_not_historical_effective_date", "per_record_version_and_evidence_lineage"],
        workload_bounds={"max_rows": MAX_RECORDS, "max_series": 1, "max_operations": 5000000,
                         "max_request_bytes": 1048576, "max_response_bytes": 8388608},
        availability_policy={"modes": ["latest", "as_of"], "point_in_time_default": "not_established",
                             "availability_basis": "local_classification_publication"},
        contracts={"availability": "local_classification_publication", "point_in_time": "not_established", "returns": "not_applicable"})
    result["output_schema"]["properties"]["series"]["maxItems"] = 0
    result["output_schema"]["properties"]["records"]["maxItems"] = MAX_RECORDS
    return result
