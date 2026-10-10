"""Additive market-owned FMP classification declaration and exact predecessor."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from ..errors import RegistryError

VERSION = "2.96.0"
PREDECESSOR = "2.95.0"
PREDECESSOR_SHA256 = "61e06644d980ebf993ab44196c3794c457bba8cf6fe2b8fa582cc925081dc9ea"
DATASET = "market.instrument_classifications"
MIGRATION_ID = "market:0013_fmp_classifications"
RESOURCE = "quant_data/migrations/market/0013_fmp_classifications.sql"


def render(raw):
    return (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()


def add_declarations(raw, project_root):
    if raw["registry_version"] != PREDECESSOR:
        raise ValueError("Classifications require registry 2.95.0")
    item = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "market.stage10.instruments"))
    item.update(id=DATASET, collector_ids=[], tool_ids=[], revision_policy="append_version",
        physical={"relations":[{"kind":"table","name":"market_classification_versions"},
                               {"kind":"view","name":"market_instrument_classifications"}]},
        identity={"stable_fields":["instrument_id"],"version_fields":["version_id"]})
    item["temporal"].update(observation_fields=["source_captured_at"], observation_precision="datetime",
                            availability_fields=["available_at"])
    item["quality_contract"]["rules"] = ["fmp_only", "complete_selected_universe", "source_profile_identity",
        "immutable_company_evidence", "publication_cutoff", "zero_write_semantic_replay"]
    raw["datasets"].append(item)
    raw["migrations"].append({"id":MIGRATION_ID,"store":"market","ordinal":13,
        "dependencies":["market:0012_collection_universes"],"resource":RESOURCE,
        "sha256":hashlib.sha256((Path(project_root)/RESOURCE).read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Add FMP-only versioned classifications for production instrument identities from retained company profile evidence; preserve fixture classifications and all earlier migrations."})
    next(s for s in raw["stores"] if s["id"] == "market")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = VERSION


def predecessor_profile(registry):
    from ..tool_platform.classification_registry import predecessor_profile as tool_predecessor
    registry = tool_predecessor(registry)
    if (registry.schema_version,registry.registry_version) != ("1.9.0",VERSION):
        return registry
    from ..tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0",VERSION)]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or {s.id:list(s.migration_order) for s in registry.stores} != {s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.sha256,m.resource,m.ordinal) for m in registry.migrations} !=
           {m["id"]:(m["sha256"],m["resource"],m["ordinal"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Classification registry source or parsed bindings drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]=PREDECESSOR
    raw["datasets"]=[d for d in raw["datasets"] if d["id"] != DATASET]
    raw["migrations"]=[m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for s in raw["stores"]: s["migration_order"]=[m for m in s["migration_order"] if m != MIGRATION_ID]
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("Classification predecessor bytes differ")
    return replace(registry,registry_version=PREDECESSOR,raw=raw,source_sha256=PREDECESSOR_SHA256,
        datasets=tuple(d for d in registry.datasets if d.id != DATASET),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))
