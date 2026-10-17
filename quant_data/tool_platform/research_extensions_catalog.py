"""Additive peer tool and explicit guidance/options successors."""
from .research_extensions_contracts import PEERS, GUIDANCE, KINDS, VERSIONS, MAX_RECORDS, schema
from .monitor_contracts import TOOLS as MONITOR_TOOLS
SUCCESSORS = (GUIDANCE, *MONITOR_TOOLS)

def entry(name):
    from .catalog import _build_forward_pe_entry
    from .results import query_result_schema
    from .retained_research_contracts import datasets_for
    from .transcript_contracts import RAW_DATASET, STRUCTURED_DATASET
    if name in MONITOR_TOOLS:
        from .monitor_catalog import entries
        item = next(e for e in entries() if e["id"] == name)
        version = "2.0.0"
        item["description"] += " Version 2 also exposes saved cumulative volume, relative volume, expected volume, baseline count/minute and quality flags; older observations remain explicitly missing."
        item["assumptions"] += ["cumulative_fields_only_if_saved_no_recalculation", "missing_cumulative_values_not_zero"]
    else:
        version = VERSIONS[name]
        item = _build_forward_pe_entry()
        item.update(id=name, family="company", owner="company",
            stores=["market", "company"] if name == PEERS else ["company"],
            datasets=list(dict.fromkeys((*datasets_for("company.screen_fundamentals"), "market.instrument_classifications"))) if name == PEERS else [RAW_DATASET, STRUCTURED_DATASET],
            description=("Compare an anchor with its saved FMP industry or sector peers using retained fundamentals and explicit comparable cohorts. Oversized groups require explicit peers; no hidden sampling."
                if name == PEERS else "Read a dated timeline of saved management guidance drafts, gaps, source citations and compatible changes across consecutive calls."),
            input_type="ResearchExtensionArguments", input_schema=schema(KINDS[name]),
            output_schema=query_result_schema(name, {"type":"object", "additionalProperties":False, "properties":{}, "required":[]}),
            examples=[{"symbol":"MU","peers":["NVDA","AMD"],"rank_by":"netmargin"}] if name == PEERS else [{"ticker":"MU","limit":8}],
            assumptions=["host_selected_immutable_readers", "no_provider_or_model_calls", "no_schema_changes",
                "local_availability_cutoff_not_historical_public_knowledge", "explicit_missingness_and_source_lineage",
                "json_suffixed_fields_are_structured_records"],
            workload_bounds={"max_rows":10000,"max_series":20,"max_operations":5000000,"max_request_bytes":1048576,"max_response_bytes":8388608},
            availability_policy={"modes":["latest","as_of"],"point_in_time_default":"not_established"},
            contracts={"availability":"retained_local_knowledge","point_in_time":"not_established","returns":"existing_metric_basis_where_used"})
        item["output_schema"]["properties"]["series"]["maxItems"] = 0
        item["output_schema"]["properties"]["records"]["maxItems"] = MAX_RECORDS
    graph = f"tool_platform.{name}.v{version[0]}"
    item.update(version=version, operation_version=version, handler=graph, operation_graph_id=graph,
        input_schema_id=f"urn:quant-data:tool:{name}:input:{version}",
        output_schema_id=f"urn:quant-data:tool:{name}:output:{version}",
        compatibility={"status":"successor_breaking_v2" if name in SUCCESSORS else "additive_native_v1",
            "predecessor":"1.0.0" if name in SUCCESSORS else None})
    return item

def policies():
    return tuple(dict(tool=n, default_version="1.0.0", selector_field="tool_version",
                      variants=[entry(n)], deprecations=[dict(version="1.0.0", code="tool_version_deprecated",
                          message="Version 1 remains supported with its original fields; explicitly select version 2 for the new retained research projection.",
                          replacement=dict(tool=n, version="2.0.0"), removal=dict(status="not_scheduled", milestone=None))]) for n in SUCCESSORS)
