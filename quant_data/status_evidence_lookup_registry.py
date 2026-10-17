"""Exact predecessor projection for remaining retained-data evidence lookup indexes."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .errors import RegistryError

VERSION = "2.101.0"
PREDECESSOR_VERSION = "2.100.0"
MIGRATION_IDS = (
    "company:0022_status_output_lookup",
    "news:0012_status_outcome_lookup",
)


def _render(raw):
    return (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()


def predecessor_profile(registry):
    if (registry.schema_version, registry.registry_version) != ("1.9.0", VERSION):
        return registry
    from .tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256

    if (
        registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", VERSION)]
        or hashlib.sha256(_render(registry.raw)).hexdigest() != registry.source_sha256
        or {s.id: list(s.migration_order) for s in registry.stores}
        != {s["id"]: s["migration_order"] for s in registry.raw["stores"]}
        or {m.id: (m.sha256, m.resource, m.ordinal) for m in registry.migrations}
        != {m["id"]: (m["sha256"], m["resource"], m["ordinal"])
            for m in registry.raw["migrations"]}
    ):
        raise RegistryError("Status evidence lookup registry source or parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = PREDECESSOR_VERSION
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] not in MIGRATION_IDS]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"]
                                    if m not in MIGRATION_IDS]
    expected = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", PREDECESSOR_VERSION)]
    if hashlib.sha256(_render(raw)).hexdigest() != expected:
        raise RegistryError("Status evidence lookup predecessor projection drifted")
    return replace(
        registry, registry_version=PREDECESSOR_VERSION, raw=raw, source_sha256=expected,
        migrations=tuple(m for m in registry.migrations if m.id not in MIGRATION_IDS),
        stores=tuple(replace(s, migration_order=tuple(
            m for m in s.migration_order if m not in MIGRATION_IDS
        )) for s in registry.stores),
    )


def add_declarations(raw, project_root):
    """Prepare the index-only successor without publishing a registry bundle."""
    if (raw["schema_version"], raw["registry_version"]) != ("1.9.0", PREDECESSOR_VERSION):
        raise ValueError("Status evidence lookups require registry 2.100.0")
    for migration_id, dependency in zip(MIGRATION_IDS, (
        "company:0021_status_run_lookup",
        "news:0011_status_evidence_lookup",
    )):
        role, name = migration_id.split(":")
        resource = f"quant_data/migrations/{role}/{name}.sql"
        raw["migrations"].append({
            "id": migration_id, "store": role, "ordinal": int(name[:4]),
            "dependencies": [dependency], "resource": resource,
            "sha256": hashlib.sha256((Path(project_root) / resource).read_bytes()).hexdigest(),
            "reconstruction_state": "fixture_validated",
            "semantic_scope": "Cover dataset output membership and news outcome metadata for retained-data readers; preserve evidence, capture ordering and all existing migrations.",
        })
        next(s for s in raw["stores"] if s["id"] == role)["migration_order"].append(migration_id)
    raw["registry_version"] = VERSION
