"""Private retained structured transcript storage, separate from the legacy schema."""
import copy
import hashlib
import json
from pathlib import Path

VERSION = "2.83.0"
PREDECESSOR_SHA256 = "1d541b532e535124ea45d2af991541348319f7d7ced937114c39aaffc2ccdfc2"
DATASET = "company.transcript.structured"
MIGRATION_ID = "company:0018_structured_transcripts"
MIGRATION_RESOURCE = "quant_data/migrations/company/0018_structured_transcripts.sql"
TABLES = ("company_structured_transcript_outputs", "company_structured_transcript_assessments")

def add_declarations(raw, project_root):
    rendered = (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True)+chr(10)).encode()
    if raw["registry_version"] != "2.82.0" or hashlib.sha256(rendered).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("Structured transcripts require exact registry 2.82")
    dataset = copy.deepcopy(next(d for d in raw["datasets"] if d["id"]=="company.transcript.analysis"))
    dataset.update(id=DATASET, collector_ids=[],
        physical={"relations":[{"kind":"table","name":name} for name in TABLES]},
        identity={"stable_fields":["capture_id","request_identity"],"version_fields":["analysis_id","assessment_id"]})
    dataset["quality_contract"]["rules"] = ["derived_model_draft_not_fact", "immutable_original_output",
        "independent_versioned_assessments", "source_capture_lineage", "local_model_availability",
        "unreviewed_and_flagged_outputs_retained", "review_is_not_human_verification"]
    raw["datasets"].append(dataset)
    resource = Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists(): resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"), {
        "id":MIGRATION_ID,"store":"company","ordinal":18,"dependencies":["company:0017_sharadar_direct"],
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Retain immutable structured transcript drafts and separate automatic/model assessments with source lineage, model availability and zero-write exact replay; preserve all source and legacy analysis tables."})
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION
