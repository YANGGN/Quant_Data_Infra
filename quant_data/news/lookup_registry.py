"""Exact predecessor projection for the retained-news metadata index."""
import copy
import hashlib
import json
from dataclasses import replace
from ..errors import RegistryError

MIGRATION_ID = "news:0010_current_news_capture_lookup"
RESOURCE = "quant_data/migrations/news/0010_current_news_capture_lookup.sql"
VERSION = "2.92.0"


def predecessor_profile(registry):
    if (registry.schema_version, registry.registry_version) != ("1.9.0", VERSION):
        return registry
    from ..tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    def render(raw):
        return (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + chr(10)).encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", VERSION)]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or {s.id:list(s.migration_order) for s in registry.stores} !=
           {s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.sha256,m.resource,m.ordinal) for m in registry.migrations} !=
           {m["id"]:(m["sha256"],m["resource"],m["ordinal"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Retained-news registry source or parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.91.0"
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"] if m != MIGRATION_ID]
    expected = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.91.0")]
    if hashlib.sha256(render(raw)).hexdigest() != expected:
        raise RegistryError("Retained-news predecessor projection drifted")
    return replace(registry, registry_version="2.91.0", raw=raw, source_sha256=expected,
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID))
                     for s in registry.stores))

def add_declarations(raw, project_root):
    """Prepare the index-only successor without publishing a registry bundle."""
    from pathlib import Path
    if raw["registry_version"] != "2.91.0":
        raise ValueError("Retained-news lookup requires registry 2.91.0")
    raw["migrations"].append({
        "id": MIGRATION_ID, "store": "news", "ordinal": 10,
        "dependencies": ["news:0009_website_source_extension"], "resource": RESOURCE,
        "sha256": hashlib.sha256((Path(project_root) / RESOURCE).read_bytes()).hexdigest(),
        "reconstruction_state": "fixture_validated",
        "semantic_scope": "Add a retained-news capture metadata lookup index; preserve source facts, capture availability, version selection and all existing migrations.",
    })
    next(s for s in raw["stores"] if s["id"] == "news")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = VERSION
