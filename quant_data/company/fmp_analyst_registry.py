"""Additive company-only declarations for the authorized analyst population."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
from .fmp_research_registry import COMPANY_COLLECTOR

DATASET_IDS = ("company.fmp.analyst_evidence", "company.fmp.analyst_observations")
MIGRATION_ID = "company:0009_fmp_analyst_history"
MIGRATION_RESOURCE = "quant_data/migrations/company/0009_fmp_analyst_history.sql"
COLLECTOR = copy.deepcopy(COMPANY_COLLECTOR)
COLLECTOR.update(id="fmp.company.analyst_history", handler="company.fmp_analyst_history",
    output_datasets=list(DATASET_IDS),
    workload_bounds={"max_requests": 1, "max_rows": 5000, "max_bytes": 1048576, "max_seconds": 30})

def add_declarations(raw: dict, project_root: Path):
    if any(d["id"] in DATASET_IDS for d in raw["datasets"]):
        raise ValueError("Analyst declarations already exist")
    evidence = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "company.fmp.research_evidence"))
    observations = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "company.fmp.research_inputs"))
    evidence.update(id=DATASET_IDS[0], tool_ids=[], collector_ids=[COLLECTOR["id"]],
        physical={"relations": [{"kind": "table", "name": "company_fmp_analyst_captures"}]},
        identity={"stable_fields": ["issuer_id", "endpoint", "semantic_identity"], "version_fields": ["capture_id"]})
    observations.update(id=DATASET_IDS[1], tool_ids=[], collector_ids=[COLLECTOR["id"]],
        physical={"relations": [{"kind": "table", "name": "company_fmp_analyst_observation_versions"},
                                {"kind": "table", "name": "company_fmp_analyst_capture_membership"}]},
        identity={"stable_fields": ["natural_identity"], "version_fields": ["observation_version_id", "version_sequence"]})
    observations["temporal"].update(observation_fields=["target_period_end", "source_event_date"],
                                    availability_fields=["captured_at"], availability_precision="datetime")
    observations["quality_contract"]["rules"] = ["local_capture_cutoff", "distinct_target_event_snapshot",
                                                 "source_native_units", "append_only_revision_lineage", "exact_semantic_replay"]
    raw["datasets"].extend((evidence, observations))
    raw["collectors"].append(copy.deepcopy(COLLECTOR))
    resource = project_root / MIGRATION_RESOURCE
    # Generating historical projections in temporary roots uses the installed immutable resource.
    if not resource.exists():
        resource = Path(__file__).resolve().parents[2] / MIGRATION_RESOURCE
    migration = {"id": MIGRATION_ID, "store": "company", "ordinal": 9,
        "dependencies": ["company:0008_fmp_research_inputs"], "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(), "reconstruction_state": "fixture_validated",
        "semantic_scope": "Add immutable analyst captures, source-native observation versions and capture membership; distinguish forecast periods, source events and undated current snapshots using local capture availability."}
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"), migration)
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = "2.73.0"
