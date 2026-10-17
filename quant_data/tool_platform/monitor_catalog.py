"""Catalog declarations for the additive intraday monitor readers."""
from .monitor_contracts import TOOLS, KINDS, MAX_RECORDS, schema


def entries():
    from .catalog import _build_forward_pe_entry, _versioned_schema_id
    from .results import query_result_schema
    result = []
    for name in TOOLS:
        entry = _build_forward_pe_entry()
        graph = f"tool_platform.{name}.v1"
        entry.update(id=name, family="options", owner="market", handler=graph, operation_graph_id=graph,
            description=("Read the latest saved intraday Options Monitor aggregates for explicit symbols."
                if name == TOOLS[0] else "Read saved intraday Options Monitor history for one symbol and up to seven calendar days.")
                + " Fixed derived source; no provider calls, daily-store or legacy fallback.",
            input_type="Monitor" + "".join(p.title() for p in name.split(".")[1].split("_")) + "ArgumentsV1",
            input_schema_id=_versioned_schema_id(name, "input", version="1.0.0"), input_schema=schema(KINDS[name]),
            output_schema_id=_versioned_schema_id(name, "output", version="1.0.0"),
            output_schema=query_result_schema(name, {"type":"object","additionalProperties":False,"properties":{},"required":[]}),
            examples=[{"symbols": ["SPY", "AAPL"]} if name == TOOLS[0] else
                {"symbol": "SPY", "start_date": "2026-09-28", "end_date": "2026-09-29"}],
            assumptions=["host_fixed_optional_monitor_store", "immutable_read_under_physical_store_lock",
                "monitor_source_record_carries_derived_lineage", "no_provider_requests_or_fallback",
                "saved_periodic_aggregates_not_streaming_or_full_chain", "capture_cutoff_not_public_availability",
                "json_suffixed_fields_are_structured_records", "current_monitor_universe_only",
                "freshness_age_is_not_market_calendar_health"],
            workload_bounds={"max_rows":MAX_RECORDS,"max_series":1,"max_operations":5000000,
                "max_request_bytes":1048576,"max_response_bytes":8388608},
            availability_policy={"modes":["latest","as_of"],"point_in_time_default":"not_established",
                "date_bounds":"snapshot_optional_session_history_max_7_inclusive_calendar_days"},
            contracts={"availability":"local_capture_time_only","point_in_time":"not_established",
                "returns":"not_applicable"})
        entry["output_schema"]["properties"]["series"]["maxItems"] = 0
        entry["output_schema"]["properties"]["records"]["maxItems"] = MAX_RECORDS
        result.append(entry)
    return tuple(result)
