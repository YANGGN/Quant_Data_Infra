"""Additive SF1 indicator definition history, independent of the SF1 fact schema."""
import copy,hashlib
from pathlib import Path
VERSION="2.79.0"
PREDECESSOR_SHA256="3fb931847ebedc3c156ee761ee017678822aa5eed40c569edab70496857cd1f9"
DATASET_IDS=("company.sharadar.definition_evidence","company.sharadar.definitions")
COLLECTOR_ID="nasdaq.company.sharadar_definitions"
MIGRATION_ID="company:0014_sharadar_sf1_definitions"
MIGRATION_RESOURCE="quant_data/migrations/company/0014_sharadar_sf1_definitions.sql"
TABLES=("company_sharadar_definition_captures","company_sharadar_definition_capture_artifacts",
        "company_sharadar_definition_versions","company_sharadar_definition_membership")
def declaration(raw):
    c=copy.deepcopy(next(c for c in raw["collectors"] if c["id"]=="nasdaq.company.sharadar_sf1"))
    c.update(id=COLLECTOR_ID,handler="company.sharadar_definitions",input_datasets=[],
        output_datasets=["company.sharadar.evidence",*DATASET_IDS],
        workload_bounds={"max_requests":11,"max_rows":10000,"max_bytes":33554432,"max_seconds":600})
    return c

def add_declarations(raw,project_root):
    if raw["registry_version"]!="2.78.0":raise ValueError("Definitions require exact registry 2.78")
    base=next(d for d in raw["datasets"] if d["id"]=="company.sharadar.sf1")
    for identifier,layer,tables in ((DATASET_IDS[0],"evidence",TABLES[:2]),(DATASET_IDS[1],"canonical",TABLES[2:])):
        d=copy.deepcopy(base);d.update(id=identifier,layer=layer,collector_ids=[COLLECTOR_ID],
            physical={"relations":[{"kind":"table","name":t} for t in tables]},
            identity={"stable_fields":["channel","table","source_key_json"],"version_fields":["capture_id","version_id"]})
        d["temporal"].update(observation_fields=["available_at"],observation_precision="datetime")
        d["quality_contract"].update(rules=["complete_cursor_snapshot","original_metadata_and_rows",
            "local_capture_cutoff","observed_definition_versions","absence_is_snapshot_membership_only"])
        raw["datasets"].append(d)
    c=declaration(raw)
    raw["collectors"].append(c)
    next(d for d in raw["datasets"] if d["id"]=="company.sharadar.evidence")["collector_ids"].append(COLLECTOR_ID)
    resource=Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists():resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"),{
        "id":MIGRATION_ID,"store":"company","ordinal":14,"dependencies":["company:0013_fmp_research_exact_capture"],
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Append original Nasdaq INDICATORS metadata and complete SF1 definition snapshots with exact replay, observed version lineage and local capture cutoffs; preserve prior SF1 fact evidence and all earlier migration bytes."})
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION

