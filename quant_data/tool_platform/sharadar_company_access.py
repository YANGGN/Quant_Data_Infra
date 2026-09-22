"""Read-only routine company fundamentals from the retained Sharadar archive."""
from pathlib import Path
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.collection_bindings import load_bindings, pin_binding
from quant_data.market.collection_universe import _utc
from quant_data.company.fundamental_sources import read_selected_fundamentals
from quant_data.stores import acquire_write_session
from quant_data.contracts import LineageRef, WarningV1, TruncationV1
from .results import QueryResult, DiagnosticV1, records_from_mappings, fields_from_mapping
from .sharadar_company_contracts import TOOLS, KINDS, SHARE_FIELDS, SharadarCompanyArgumentsV3, parse

BINDINGS_PATH = Path(__file__).resolve().parents[2] / "config/collection_bindings.json"

def invoke_sharadar_company(name, arguments, context, registry):
    if name not in TOOLS or not isinstance(arguments, SharadarCompanyArgumentsV3) or arguments.kind != KINDS[name]:
        raise ValidationError("Sharadar company tools require their typed v3 arguments")
    arguments = parse(arguments.kind, {k: arguments[k] for k in arguments if arguments[k] is not None})
    now = _utc(context.clock.instant())
    cutoff = arguments.as_of or now
    if cutoff > now:
        raise ValidationError("Company cutoff cannot be in the future")
    context.checkpoint()
    # Pins and archive reads share resolved physical locks with their publishers.
    with acquire_write_session(context.store_map, ("market", "company"), timeout_seconds=1):
        binding = load_bindings(BINDINGS_PATH)["sharadar_fundamentals"]
        selection = pin_binding(context.store_map, binding, cutoff=cutoff)
        result = read_selected_fundamentals(context, registry, source_symbol=arguments.symbol,
            selections={"sharadar_fundamentals": selection}, knowledge_cutoff=cutoff,
            sources=("sharadar",), limit_per_source=arguments.limit,
            sharadar_dimensions=(arguments.dimension,))
    source = result["sources"]["sharadar"]
    rows = source["rows"]
    records, lineage = [], []
    for row in rows:
        values, missing = row["values"], row["missingness"]
        if name == TOOLS[1]:
            values = {key: values.get(key) for key in SHARE_FIELDS}
            missing = {key: missing.get(key, "not_supplied" if values[key] is None else None) for key in SHARE_FIELDS}
        records.append({
            "source": "sharadar", "symbol": arguments.symbol, "provider_symbol": source["provider_symbol"],
            "provider_subject": source["provider_subject"], "dimension": row["dimension"],
            "reportperiod": row["reportperiod"], "calendardate": row["calendardate"],
            "source_datekey": row["source_datekey"], "available_at": row["available_at"],
            "ingested_at": row["ingested_at"], "capture_id": row["capture_id"],
            "observation_id": row["observation_id"], "version_id": row["version_id"],
            "delivery_channel": row["delivery_channel"], "normalization_version": row["normalization_version"],
            "values_json": dumps_strict(values), "missingness_json": dumps_strict(missing),
            "source_key_json": dumps_strict(row["source_key"]), "schema_id": row["schema_id"]})
        lineage.append(LineageRef(dataset_id="company.sharadar.sf1", store_role="company",
            semantic_id=row["observation_id"], canonical_version_id=row["version_id"], evidence_id=row["capture_id"]))
    context.checkpoint()
    count = len(records)
    return QueryResult(tool=name, status="ok" if rows else "not_established",
        records=records_from_mappings("sharadar_fundamental" if name == TOOLS[0] else "sharadar_share_count", tuple(records)),
        lineage=tuple(lineage), warnings=(WarningV1("sharadar_source_semantics",
            "Values retain Sharadar definitions. AR/MR dimensions are distinct; local capture does not prove historical first-release completeness. No SEC/FMP fallback or metric equivalence is asserted."),),
        diagnostics=(DiagnosticV1("preferred_company_source", "Read routine fundamentals from the local Sharadar archive.",
            fields_from_mapping({"preferred_source": "sharadar", "source_policy_version": "1.0.0",
                "fallback": "none", "dimension": arguments.dimension, "mode": arguments.mode,
                "knowledge_cutoff": cutoff, "availability_basis": "local_capture",
                "mapping_id": source["mapping_id"], "membership_snapshot_id": result["membership_snapshot_id"],
                "identity_link_status": result["identity_link_status"], "status": source["status"],
                "reason": source.get("reason"), "metric_equivalence": "not_asserted"})),),
        truncation=TruncationV1(source["truncated"], arguments.limit, count,
            None if source["truncated"] else count, source["truncated"]))
