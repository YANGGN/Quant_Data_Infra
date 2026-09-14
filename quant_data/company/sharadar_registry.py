"""Company-owned Nasdaq Data Link SF1 evidence and observed row transitions."""
import copy
import hashlib
from pathlib import Path
from .equibles_registry import COLLECTOR as BASE_COLLECTOR
VERSION="2.77.0"
PREDECESSOR_VERSION="2.76.0"
PREDECESSOR_SHA256="d8d63b5c84f78afaf2a7e53fb38945d99c5f0228c07b78093473b9ad431dc227"
DATASET_IDS=("company.sharadar.evidence","company.sharadar.sf1")
MIGRATION_ID="company:0012_sharadar_sf1"
MIGRATION_RESOURCE="quant_data/migrations/company/0012_sharadar_sf1.sql"
EVIDENCE_TABLES=("company_sharadar_artifacts","company_sharadar_schema_versions",
    "company_sharadar_captures","company_sharadar_capture_artifacts","company_sharadar_identity_assertions")
CANONICAL_TABLES=("company_sharadar_sf1_observations","company_sharadar_sf1_versions",
    "company_sharadar_sf1_membership","company_sharadar_sf1_heads","company_sharadar_scope_heads",
    "company_sharadar_quality_findings")
COLLECTOR=copy.deepcopy(BASE_COLLECTOR)
COLLECTOR.update(id="nasdaq.company.sharadar_sf1",handler="company.sharadar_sf1",
    configuration_env=["SHARADAR_API_KEY"],input_datasets=["market.collection.membership","market.collection.provider_mappings"],
    output_datasets=list(DATASET_IDS),
    semantic_identity={"includes":["request_scope","normalization_version","schema_contract","full_source_rows","predecessor_transition"],
        "excludes":["api_key","wire_order","pagination_tokens","capture_time"]},
    workload_bounds={"max_requests":101,"max_rows":100000,"max_bytes":268435456,"max_seconds":7200})

def add_declarations(raw,project_root):
    if raw["registry_version"]!=PREDECESSOR_VERSION or any(d["id"] in DATASET_IDS for d in raw["datasets"]):
        raise ValueError("Sharadar declarations require the exact collection predecessor")
    template=next(d for d in raw["datasets"] if d["id"]=="company.equibles.transcripts")
    for identifier,layer,tables in ((DATASET_IDS[0],"evidence",EVIDENCE_TABLES),(DATASET_IDS[1],"canonical",CANONICAL_TABLES)):
        dataset=copy.deepcopy(template)
        dataset.update(id=identifier,layer=layer,collector_ids=[COLLECTOR["id"]],
            physical={"relations":[{"kind":"table","name":n} for n in tables]},
            identity={"stable_fields":["channel","table","key_contract_id","source_key_json"],
                "version_fields":["capture_id","version_id","predecessor_version_id"]})
        dataset["temporal"].update(observation_fields=["source_datekey","reportperiod","calendardate"],
            observation_precision="date",availability_fields=["available_at"],history_basis="local_capture")
        dataset["quality_contract"].update(units="source_native_no_conversion",
            rules=["lossless_source_bytes","exact_decimal_strings","metadata_pinned_keys",
                "ar_mr_dimensions_distinct","local_capture_cutoff","observed_transition_replay",
                "no_absence_deletion","definition_state_explicit"])
        raw["datasets"].append(dataset)
    raw["collectors"].append(copy.deepcopy(COLLECTOR))
    resource=Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists():resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"),{
        "id":MIGRATION_ID,"store":"company","ordinal":12,"dependencies":["company:0011_transcript_analysis"],
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Append Nasdaq Data Link SF1 original response bytes, metadata-pinned source observations and locally observed AR/MR row transitions with exact replay, capture cutoffs and complete partition membership; keep FMP and SEC histories independent."})
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION
