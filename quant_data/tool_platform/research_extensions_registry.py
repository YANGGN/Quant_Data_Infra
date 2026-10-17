"""Exact predecessor proof for opt-in research extensions; no data declarations change."""
import copy
from dataclasses import replace
import hashlib
import json
from quant_data.errors import RegistryError
from .research_extensions_contracts import PEERS
from .research_extensions_catalog import SUCCESSORS

PREDECESSOR_SHA256 = "24c27b5189319eab448204ada3db1755214afc153a805f01ed8a275895f5eb4f"
PREDECESSOR_CATALOG = {"resource":"quant_data/generated/tool_contract_schemas_v2.json",
    "schema_id":"quant_data.tool_contract_catalog.v2","schema_version":"2.40.0",
    "sha256":"e6e62f830d79c91ffb0fe2a9afc652fecb2a24e63aa8c2096297e994b4599629"}

def predecessor_profile(registry):
    from ..status_market_lookup_registry import predecessor_profile as market_lookup_predecessor
    registry = market_lookup_predecessor(registry)
    from ..status_evidence_lookup_registry import predecessor_profile as evidence_lookup_predecessor
    registry = evidence_lookup_predecessor(registry)
    from ..status_run_lookup_registry import predecessor_profile as run_lookup_predecessor
    registry = run_lookup_predecessor(registry)
    from ..status_lookup_registry import predecessor_profile as status_lookup_predecessor
    registry = status_lookup_predecessor(registry)
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.98.0"): return registry
    from .generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)+chr(10)).encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.98.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or [dict(t) for t in registry.tools] != registry.raw["tools"]
        or [dict(p) for p in registry.tool_version_policies] != registry.raw["tool_versions"]
        or {d.id:list(d.tool_ids) for d in registry.datasets} != {d["id"]:d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Research extension declarations drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.97.0"
    raw["tools"] = [t for t in raw["tools"] if t["id"] != PEERS]
    raw["tool_versions"] = [p for p in raw["tool_versions"] if p["tool"] not in SUCCESSORS]
    raw["presentation_order"]["tools"] = [t for t in raw["presentation_order"]["tools"] if t != PEERS]
    raw["tool_version_schema_catalog"] = copy.deepcopy(PREDECESSOR_CATALOG)
    links = {d["id"]:[] for d in raw["datasets"]}
    for entry in raw["tools"]:
        for d in entry["datasets"]: links[d].append(entry["id"])
    for policy in raw["tool_versions"]:
        for entry in policy["variants"]:
            for d in entry["datasets"]:
                if entry["id"] not in links[d]: links[d].append(entry["id"])
    for d in raw["datasets"]: d["tool_ids"] = links[d["id"]]
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("Research extension predecessor bytes differ")
    return replace(registry, registry_version="2.97.0", raw=raw, source_sha256=PREDECESSOR_SHA256,
        tools=tuple(t for t in registry.tools if t["id"] != PEERS),
        tool_version_policies=tuple(p for p in registry.tool_version_policies if p["tool"] not in SUCCESSORS),
        datasets=tuple(replace(d, tool_ids=tuple(links[d.id])) for d in registry.datasets))
