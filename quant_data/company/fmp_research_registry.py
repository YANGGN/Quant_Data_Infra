"""Reviewed declarations for the September 6 additive FMP research lane."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path

COMPANY_COLLECTOR = {
    "id": "fmp.company.research_inputs", "version": "1.0.0",
    "handler": "company.fmp_research_inputs", "network": True,
    "configuration_env": ["FMP_API_KEY"],
    "input_datasets": ["market.stage10.instruments", "fixture.company.issuers"],
    "output_datasets": ["company.fmp.research_evidence", "company.fmp.research_inputs"],
    "mutation_policy": {"mode": "append_versions_and_capture_membership", "unchanged": "zero_persistent_writes"},
    "physical_locks": "derived_from_output_store_paths",
    "retry_policy": {"backoff": "none_single_attempt", "honor_retry_after": False, "max_attempts": 1, "transient_classes": []},
    "schedule_eligibility": {"mode": "manual_only"},
    "semantic_identity": {"includes": ["request_scope", "normalization_version", "normalized_records"],
                          "excludes": ["api_key", "captured_at", "http_headers", "source_row_order", "raw_response_identity"]},
    "workload_bounds": {"max_requests": 1, "max_rows": 100, "max_bytes": 1048576, "max_seconds": 30},
}
PRICE_COLLECTOR = {
    **copy.deepcopy(COMPANY_COLLECTOR),
    "id": "fmp.market.research_gap_repair",
    "handler": "market.research_gap_repair",
    "input_datasets": ["market.stage10.instruments"],
    "output_datasets": ["market.stage10.source_evidence", "market.stage10.daily_prices"],
    "workload_bounds": {"max_requests": 3, "max_rows": 34, "max_bytes": 3145728, "max_seconds": 90},
}
COLLECTORS = (COMPANY_COLLECTOR, PRICE_COLLECTOR)
DATASET_IDS = ("company.fmp.research_evidence", "company.fmp.research_inputs")
MIGRATION_ID = "company:0008_fmp_research_inputs"
MIGRATION_RESOURCE = "quant_data/migrations/company/0008_fmp_research_inputs.sql"

def add_declarations(raw: dict, project_root: Path) -> None:
    if any(item["id"] in DATASET_IDS for item in raw["datasets"]):
        raise ValueError("FMP research declarations already exist")
    evidence = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "fixture.company.expectation_evidence"))
    evidence.update(id=DATASET_IDS[0], collector_ids=[COMPANY_COLLECTOR["id"]],
        tool_ids=["company.get_research_inputs"], dashboard_ids=[], export_ids=[],
        physical={"relations": [{"kind": "table", "name": "company_fmp_research_snapshots"}]},
        identity={"stable_fields": ["issuer_id", "endpoint", "request_scope", "semantic_identity"],
                  "version_fields": ["snapshot_id"]})
    evidence["temporal"].update(availability_fields=["captured_at"], availability_precision="datetime")
    evidence["quality_contract"]["rules"] = ["content_identity", "issuer_scope", "capture_cutoff", "source_row_pointer"]
    rows = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "fixture.company.fundamentals"))
    rows.update(id=DATASET_IDS[1], collector_ids=[COMPANY_COLLECTOR["id"]],
        tool_ids=["company.get_research_inputs"], dashboard_ids=[], export_ids=[],
        physical={"relations": [{"kind": "table", "name": "company_fmp_research_rows"}]},
        identity={"stable_fields": ["issuer_id", "endpoint", "request_period", "period_end", "fiscal_year", "fiscal_period", "event_date"],
                  "version_fields": ["research_row_id", "snapshot_id"]})
    rows["temporal"].update(availability_fields=["captured_at"], availability_precision="datetime",
                            observation_fields=["period_end"], vintage_modes=["latest", "as_of"])
    rows["quality_contract"].update(rules=["annual_quarter_context", "native_currency", "capture_cutoff", "source_lineage"],
                                    units="source_native_no_conversion")
    raw["datasets"].extend([evidence, rows])
    raw["collectors"].extend(copy.deepcopy(COLLECTORS))
    for dataset in raw["datasets"]:
        if dataset["id"] in PRICE_COLLECTOR["output_datasets"]:
            dataset["collector_ids"].append(PRICE_COLLECTOR["id"])
    migration = {"id": MIGRATION_ID, "ordinal": 8, "store": "company",
        "dependencies": ["company:0007_filing_issuer_view"], "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256((project_root / MIGRATION_RESOURCE).read_bytes()).hexdigest(),
        "reconstruction_state": "fixture_validated",
        "semantic_scope": "Add immutable FMP research evidence and context-preserving company statement, segment, current estimate and earnings rows; preserve prior SEC facts."}
    index = next(i for i,m in enumerate(raw["migrations"]) if m["store"] == "news")
    raw["migrations"].insert(index, migration)
    next(s for s in raw["stores"] if s["id"] == "company")["migration_order"].append(MIGRATION_ID)
