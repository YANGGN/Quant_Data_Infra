"""Additive declarations; older contracts remain byte-identical."""
from .retained_research_contracts import *

def example(name):
    props=schema(KINDS[name])["properties"]
    value={}
    if "symbols" in props:value["symbols"]=["SPY","QQQ"] if name=="market.get_breadth" else ["AAPL"]
    if "symbol" in props:value["symbol"]="SPY" if name=="options.compare_implied_realized" else "AAPL"
    if "start_date" in props:value.update(start_date="2026-09-01",end_date="2026-09-28")
    if name in ("company.get_consensus_history","company.get_estimate_revisions"):value.update(start_date="2026-01-01",end_date="2027-12-31",period="quarter")
    if name=="company.compare_transcripts":value.update(before_capture_id="equibles_transcript_"+"a"*32,after_capture_id="equibles_transcript_"+"b"*32)
    if name=="research.get_watchlist_changes":value.update(since="2026-09-21T00:00:00Z",as_of="2026-09-28T00:00:00Z")
    if name=="company.screen_fundamentals":value.update(criteria=[dict(metric="netmargin",minimum=0)],rank_by="netmargin")
    if name=="data.get_collection_plan":value={"limit":100}
    return value

def entry(name):
    from .catalog import _build_forward_pe_entry,_family
    from .results import query_result_schema
    version=VERSIONS[name];graph=f"tool_platform.{name}.v{version[0]}"
    item=_build_forward_pe_entry()
    item.update(id=name,family=_family(name),owner="market" if name.startswith(("options.","market.")) else "company" if name.startswith("company.") else "research",
        version=version,operation_version=version,handler=graph,operation_graph_id=graph,
        description=DESCRIPTIONS[TOOLS.index(name)],stores=list(stores_for(name)),datasets=list(datasets_for(name)),
        compatibility={"status":"successor_breaking_v2" if name in SUCCESSORS else "additive_native_v1",
                       "predecessor":"1.0.0" if name in SUCCESSORS else None},
        input_type="RetainedResearchArgumentsV"+version[0],input_schema_id=f"urn:quant-data:tool:{name}:input:{version}",
        output_schema_id=f"urn:quant-data:tool:{name}:output:{version}",input_schema=schema(KINDS[name]),
        output_schema=query_result_schema(name,{"type":"object","additionalProperties":False,"properties":{},"required":[]}),
        examples=[example(name)],
        assumptions=["host_selected_existing_immutable_readers","bounded_explicit_cohort_and_dates",
            "local_capture_cutoff_not_historical_publication","no_provider_or_model_requests_or_scheduler_actions",
            "json_suffixed_fields_are_documented_structured_records","cross_store_reads_are_not_an_atomic_snapshot",
            "theta_inputs_only_from_optional_options_sqlite_no_alpaca_fallback"],
        workload_bounds={"max_rows":10000,"max_series":20,"max_operations":5000000,"max_request_bytes":1048576,"max_response_bytes":8388608},
        availability_policy={"modes":["latest","as_of"] if name!="data.get_collection_plan" else ["saved_state"],
            "point_in_time_default":"not_established"},
        contracts={"availability":"retained_capture_cutoff","point_in_time":"historical_public_availability_not_established",
            "returns":"split_adjusted_close_excluding_distributions_where_used"})
    item["output_schema"]["properties"]["series"]["maxItems"]=0
    item["output_schema"]["properties"]["records"]["maxItems"]=MAX_RECORDS
    return item

def new_entries():return tuple(entry(name) for name in NEW_TOOLS)
def policies():
    return tuple(dict(tool=name,default_version="1.0.0",selector_field="tool_version",variants=[entry(name)],
        deprecations=[dict(version="1.0.0",code="tool_version_deprecated",
            message="Version 1 remains available with its frozen fixture semantics; select version 2 for retained production inputs.",
            replacement=dict(tool=name,version="2.0.0"),removal=dict(status="not_scheduled",milestone=None))]) for name in SUCCESSORS)
