"""Add lookup indexes for existing FMP research publication; no data or guard changes."""
import hashlib
import json
from pathlib import Path

VERSION = "2.81.0"
PREDECESSOR_SHA256 = "da3e31a19f62016e34fa201b73b49f72d43ba9bbea715b1aa482382fc1490246"
MIGRATION_ID = "company:0016_fmp_research_lookup_indexes"
MIGRATION_RESOURCE = "quant_data/migrations/company/0016_fmp_research_lookup_indexes.sql"


def add_declarations(raw, project_root):
    source = (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()
    if raw["registry_version"] != "2.80.0" or hashlib.sha256(source).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("FMP research indexes require exact registry 2.80")
    resource = Path(project_root) / MIGRATION_RESOURCE
    if not resource.exists():
        resource = Path(__file__).resolve().parents[2] / MIGRATION_RESOURCE
    declaration = {
        "id": MIGRATION_ID,
        "store": "company",
        "ordinal": 16,
        "dependencies": ["company:0015_sec_completion_indexes"],
        "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state": "fixture_validated",
        "semantic_scope": "Add nonunique natural-identity/capture and issuer lookup indexes for existing FMP research replay and revision checks; preserve all data, constraints, trigger bodies, and earlier migration bytes.",
    }
    raw["migrations"].insert(
        next(i for i, m in enumerate(raw["migrations"]) if m["store"] == "news"),
        declaration,
    )
    next(s for s in raw["stores"] if s["id"] == "company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = VERSION
