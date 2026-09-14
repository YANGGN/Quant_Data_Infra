"""Add indexes for the existing SEC completion trigger; no data or guard changes."""
import hashlib
import json
from pathlib import Path

VERSION = "2.80.0"
PREDECESSOR_SHA256 = "b027cd7cdbe9d1f17293249f8564712d7e703ecfbfb3d7b651848ed5f7a2ae22"
MIGRATION_ID = "company:0015_sec_completion_indexes"
MIGRATION_RESOURCE = "quant_data/migrations/company/0015_sec_completion_indexes.sql"


def add_declarations(raw, project_root):
    source = (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()
    if raw["registry_version"] != "2.79.0" or hashlib.sha256(source).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("SEC completion indexes require exact registry 2.79")
    resource = Path(project_root) / MIGRATION_RESOURCE
    if not resource.exists():
        resource = Path(__file__).resolve().parents[2] / MIGRATION_RESOURCE
    declaration = {
        "id": MIGRATION_ID,
        "store": "company",
        "ordinal": 15,
        "dependencies": ["company:0014_sharadar_sf1_definitions"],
        "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state": "fixture_validated",
        "semantic_scope": "Add nonunique run and fact-membership lookup indexes for existing SEC ingestion completion checks; preserve all data, constraints, trigger bodies, and earlier migration bytes.",
    }
    raw["migrations"].insert(
        next(i for i, m in enumerate(raw["migrations"]) if m["store"] == "news"),
        declaration,
    )
    next(s for s in raw["stores"] if s["id"] == "company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = VERSION
