"""Additive private company profile evidence and derived descriptions."""
import copy
import hashlib
from pathlib import Path

VERSION = "2.91.0"
EVIDENCE = "company.profile_evidence"
DATASET = "company.short_descriptions"
COLLECTOR = "fmp.company.short_descriptions"
MIGRATION_ID = "company:0019_short_descriptions"
RESOURCE = "quant_data/migrations/company/0019_short_descriptions.sql"

def add_declarations(raw, project_root):
    if raw["registry_version"] != "2.90.0":
        raise ValueError("Short descriptions require registry 2.90.0")
    for dataset_id, layer, relations in (
        (EVIDENCE, "evidence", (("table", "company_profile_evidence"),)),
        (DATASET, "derived", (("table", "company_short_description_versions"),
                             ("view", "company_short_descriptions"))),
    ):
        item = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "company.transcript.structured"))
        item.update(id=dataset_id, layer=layer, collector_ids=[COLLECTOR], tool_ids=[],
                    physical={"relations": [{"kind": kind, "name": name} for kind, name in relations]},
                    identity={"stable_fields": ["instrument_id"] if layer == "derived" else ["content_sha256", "source_pointer"],
                              "version_fields": ["version_id"] if layer == "derived" else ["evidence_id"]},
                    revision_policy="append_version" if layer == "derived" else "immutable_capture")
        item["quality_contract"]["rules"] = [
            "source_profile_identity", "immutable_raw_evidence", "local_capture_cutoff",
            "explicit_extractive_method", "zero_write_semantic_replay"]
        if layer == "evidence":
            item["temporal"]["availability_fields"] = ["captured_at"]
            item["temporal"]["observation_fields"] = ["captured_at"]
        raw["datasets"].append(item)
    collector = collector_declaration(raw)
    raw["collectors"].append(collector)
    entry = {"id": MIGRATION_ID, "store": "company", "ordinal": 19,
             "dependencies": ["company:0018_structured_transcripts"],
             "resource": RESOURCE,
             "sha256": hashlib.sha256((Path(project_root) / RESOURCE).read_bytes()).hexdigest(),
             "reconstruction_state": "fixture_validated",
             "semantic_scope": "Add immutable profile evidence and versioned derived short descriptions with source identity and availability; preserve all earlier migrations and source records."}
    raw["migrations"].insert(next(i for i, m in enumerate(raw["migrations"]) if m["store"] == "news"), entry)
    next(s for s in raw["stores"] if s["id"] == "company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = VERSION

def predecessor_profile(registry):
    """Remove this private increment and restore the exact accepted 2.90 bytes."""
    from dataclasses import replace
    import json
    from ..errors import RegistryError
    from ..tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    from ..news.lookup_registry import predecessor_profile as news_predecessor
    registry = news_predecessor(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", VERSION):
        return registry
    render = lambda raw: (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", VERSION)]
            or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256):
        raise RegistryError("Short-description registry source drifted")
    if ([dict(c) for c in registry.collectors] != registry.raw["collectors"]
        or {s.id:list(s.migration_order) for s in registry.stores} != {s["id"]:s["migration_order"] for s in registry.raw["stores"]}
        or {m.id:(m.sha256,m.resource,m.ordinal) for m in registry.migrations}
           != {m["id"]:(m["sha256"],m["resource"],m["ordinal"]) for m in registry.raw["migrations"]}):
        raise RegistryError("Short-description parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.90.0"
    raw["datasets"] = [d for d in raw["datasets"] if d["id"] not in (DATASET, EVIDENCE)]
    raw["collectors"] = [c for c in raw["collectors"] if c["id"] != COLLECTOR]
    raw["migrations"] = [m for m in raw["migrations"] if m["id"] != MIGRATION_ID]
    for store in raw["stores"]:
        store["migration_order"] = [m for m in store["migration_order"] if m != MIGRATION_ID]
    expected = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.90.0")]
    if hashlib.sha256(render(raw)).hexdigest() != expected:
        raise RegistryError("Short-description predecessor projection drifted")
    return replace(registry, registry_version="2.90.0", raw=raw, source_sha256=expected,
        datasets=tuple(d for d in registry.datasets if d.id not in (DATASET, EVIDENCE)),
        collectors=tuple(c for c in registry.collectors if c["id"] != COLLECTOR),
        migrations=tuple(m for m in registry.migrations if m.id != MIGRATION_ID),
        stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order if m != MIGRATION_ID)) for s in registry.stores))

def collector_declaration(raw):
    collector = copy.deepcopy(next(c for c in raw["collectors"] if c["id"] == "fmp.company.research_inputs"))
    collector.update(id=COLLECTOR, handler="company.short_descriptions",
                     input_datasets=["market.collection.membership", "market.collection.provider_mappings"],
                     output_datasets=[EVIDENCE, DATASET],
                     workload_bounds={"max_bytes": 33554432, "max_requests": 516,
                                      "max_rows": 2248, "max_seconds": 1800})
    return collector
