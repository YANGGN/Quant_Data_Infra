"""Additive bounded statement-history collector using the existing FMP tables."""
import copy
import hashlib
from pathlib import Path
VERSION="2.78.0"
PREDECESSOR_SHA256="7a65056ff30c31b21c0952b7d40048e0ae5b09257dce60ce182b764cf4ac4853"
COLLECTOR_ID="fmp.company.statement_history"
IDENTITY_COLLECTOR_ID="local.market.selected_instruments"
COLLECTOR_IDS=(COLLECTOR_ID,IDENTITY_COLLECTOR_ID)
DATASET_IDS=("company.fmp.research_evidence","company.fmp.research_inputs")
MIGRATION_ID="company:0013_fmp_research_exact_capture"
MIGRATION_RESOURCE="quant_data/migrations/company/0013_fmp_research_exact_capture.sql"
MAX_ROWS=1000
MAX_BYTES=8*1024*1024

def declaration(raw):
    c=copy.deepcopy(next(c for c in raw["collectors"] if c["id"]=="fmp.company.research_inputs"))
    c.update(id=COLLECTOR_ID,handler="company.fmp_statement_history",version="1.0.0",
        input_datasets=["market.collection.membership","market.collection.provider_mappings","fixture.company.issuers"],
        workload_bounds={"max_requests":1,"max_rows":MAX_ROWS,"max_bytes":MAX_BYTES,"max_seconds":30})
    return c

def identity_declaration(raw):
    c=copy.deepcopy(next(c for c in raw["collectors"] if c["id"]=="local.market.collection_manifest"))
    c.update(id=IDENTITY_COLLECTOR_ID,handler="market.selected_instruments",
        input_datasets=["market.collection.membership"],
        output_datasets=["market.collection.evidence","market.collection.provider_mappings","market.stage10.instruments"])
    return c

def add_declarations(raw,project_root=None):
    if raw["registry_version"]!="2.77.0" or any(c["id"]==COLLECTOR_ID for c in raw["collectors"]):
        raise ValueError("Statement history requires exact registry 2.77")
    raw["collectors"].append(declaration(raw))
    raw["collectors"].append(identity_declaration(raw))
    for d in raw["datasets"]:
        if d["id"] in DATASET_IDS:d["collector_ids"].append(COLLECTOR_ID)
        if d["id"] in ("market.collection.evidence","market.collection.provider_mappings","market.stage10.instruments"):
            d["collector_ids"].append(IDENTITY_COLLECTOR_ID)
    resource=Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists():resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"),{
        "id":MIGRATION_ID,"store":"company","ordinal":13,"dependencies":["company:0012_sharadar_sf1"],
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Replace only the FMP research revision capture guard with exact UTC microsecond comparisons while preserving offset-bearing evidence and all prior migration bytes."})
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION
