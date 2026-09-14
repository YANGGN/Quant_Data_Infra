"""Exact additive registry declarations for private collection manifests."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
from ..company.fmp_research_registry import COMPANY_COLLECTOR

VERSION = "2.76.0"
PREDECESSOR_VERSION = "2.75.0"
PREDECESSOR_SHA256 = "7f5f2eb8f8a3470a7192a327b966a6d993332049c90564baa7a13379b30eadae"
DATASET_IDS = ("market.collection.evidence","market.collection.membership","market.collection.provider_mappings")
MIGRATION_ID = "market:0012_collection_universes"
MIGRATION_RESOURCE = "quant_data/migrations/market/0012_collection_universes.sql"
COLLECTOR = copy.deepcopy(COMPANY_COLLECTOR)
COLLECTOR.update(id="local.market.collection_manifest",handler="market.collection_manifest",
    network=False,configuration_env=[],input_datasets=[],output_datasets=list(DATASET_IDS),
    semantic_identity={"includes":["request_scope","normalization_version","normalized_members","predecessor_snapshot"],
                       "excludes":["captured_at","source_row_order"]},
    workload_bounds={"max_requests":0,"max_rows":5000,"max_bytes":67108864,"max_seconds":60})

def add_declarations(raw,project_root):
    if raw["registry_version"] != PREDECESSOR_VERSION:
        raise ValueError("Collection registry needs the exact predecessor version")
    if any(d["id"] in DATASET_IDS for d in raw["datasets"]):
        raise ValueError("Collection declarations already exist")
    templates=("market.stage10.source_evidence","market.stage10.universe_snapshots")
    # Use the existing market source contract as a shape template only.
    evidence=copy.deepcopy(next(d for d in raw["datasets"] if d["id"]==templates[0]))
    canonical=copy.deepcopy(next(d for d in raw["datasets"] if d["id"]=="market.stage10.instruments"))
    for dataset,identifier,layer,tables in (
        (evidence,DATASET_IDS[0],"evidence",("market_collection_artifacts","market_collection_identity_evidence")),
        (canonical,DATASET_IDS[1],"canonical",("market_collection_universes","market_collection_snapshots",
                                            "market_collection_members","market_collection_heads"))):
        dataset.update(id=identifier,layer=layer,collector_ids=[COLLECTOR["id"]],
            physical={"relations":[{"kind":"table","name":t} for t in tables]},
            tool_ids=[],dashboard_ids=[],export_ids=[])
        dataset["identity"]={"stable_fields":["universe_id","source_symbol"],"version_fields":["snapshot_id","artifact_id"]}
        dataset["temporal"].update(observation_fields=["source_label_date"],observation_precision="date",
            availability_fields=["captured_at"],availability_precision="datetime",vintage_modes=["latest","as_of"])
        dataset["quality_contract"].update(rules=["immutable_source_bytes","source_symbols_not_provider_mappings",
            "complete_snapshot","local_capture_cutoff","observed_transition_replay"],units="source_native_no_conversion")
        raw["datasets"].append(dataset)
    mapping=copy.deepcopy(canonical)
    mapping.update(id=DATASET_IDS[2],physical={"relations":[{"kind":"table","name":t} for t in
        ("market_collection_mapping_snapshots","market_collection_provider_mappings","market_collection_mapping_heads")]},
        identity={"stable_fields":["membership_snapshot_id","provider","source_symbol"],"version_fields":["mapping_id"]})
    mapping["temporal"].update(observation_fields=["captured_at"],observation_precision="datetime")
    mapping["quality_contract"]["rules"]=["evidence_backed_assertions","complete_mapping_snapshot","unresolved_members_explicit","provider_specific_symbols","local_capture_cutoff"]
    raw["datasets"].append(mapping)
    raw["collectors"].append(copy.deepcopy(COLLECTOR))
    resource=Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists():
        resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    migration={"id":MIGRATION_ID,"store":"market","ordinal":12,
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "dependencies":["market:0011_option_raw_evidence"],"reconstruction_state":"fixture_validated",
        "semantic_scope":"Append immutable collection CSV evidence, complete source-symbol membership snapshots and locally observed transitions without changing existing instrument or universe contracts."}
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]!="market"),migration)
    next(s for s in raw["stores"] if s["id"]=="market")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION
