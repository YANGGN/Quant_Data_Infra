"""Company-owned raw transcript evidence and its fixed private collector."""
import copy
import hashlib
from pathlib import Path
from .fmp_research_registry import COMPANY_COLLECTOR

DATASET_IDS = ("company.equibles.transcripts",)
MIGRATION_ID = "company:0010_equibles_transcripts"
MIGRATION_RESOURCE = "quant_data/migrations/company/0010_equibles_transcripts.sql"
COLLECTOR = copy.deepcopy(COMPANY_COLLECTOR)
COLLECTOR.update(id="equibles.company.transcripts", handler="company.equibles_transcripts",
    configuration_env=["EQUIBLES_API_KEY"], input_datasets=["market.stage10.instruments"],
    output_datasets=list(DATASET_IDS),
    semantic_identity={"includes": ["request_scope", "normalization_version", "provider", "frozen_instrument_binding", "event_id", "ordered_raw_pages"],
                       "excludes": ["api_key", "captured_at", "http_headers"]},
    workload_bounds={"max_requests": 100, "max_rows": 10000, "max_bytes": 8388608, "max_seconds": 3600})


def add_declarations(raw, project_root):
    if any(d["id"] in DATASET_IDS for d in raw["datasets"]):
        raise ValueError("Equibles declarations already exist")
    dataset = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "company.fmp.analyst_evidence"))
    dataset.update(id=DATASET_IDS[0], collector_ids=[COLLECTOR["id"]],
        physical={"relations": [{"kind": "table", "name": n} for n in
                  ("company_equibles_transcripts", "company_equibles_transcript_pages")]},
        identity={"stable_fields": ["provider_symbol_at_capture", "instrument_id", "event_id", "semantic_identity"],
                  "version_fields": ["capture_id"]})
    dataset["quality_contract"]["rules"] = ["raw_bytes_and_content_identity", "complete_contiguous_turn_pages",
        "local_capture_availability", "unverified_source_call_date", "frozen_provider_ticker_binding"]
    raw["datasets"].append(dataset)
    raw["collectors"].append(copy.deepcopy(COLLECTOR))
    resource = project_root / MIGRATION_RESOURCE
    if not resource.exists():
        resource = Path(__file__).resolve().parents[2] / MIGRATION_RESOURCE
    migration = {"id": MIGRATION_ID, "store": "company", "ordinal": 10,
        "dependencies": ["company:0009_fmp_analyst_history"], "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(), "reconstruction_state": "fixture_validated",
        "semantic_scope": "Append complete Equibles raw transcript captures and exact page bytes with local capture availability; retain source-native event metadata and frozen market instrument association."}
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"), migration)
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = "2.74.0"
