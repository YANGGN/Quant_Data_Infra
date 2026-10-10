"""Exact predecessor projection for the additive FMP classification reader."""
import copy
from dataclasses import replace
import hashlib
import json

from quant_data.errors import RegistryError
from .classification_contracts import TOOL

PREDECESSOR_SHA256 = "77ae8d98df1c90a08697655669eca68c1d28e1788370a1a587d81e07cc27be20"
PREDECESSOR_CATALOG = {"resource": "quant_data/generated/tool_contract_schemas_v2.json",
    "schema_id": "quant_data.tool_contract_catalog.v2", "schema_version": "2.39.0",
    "sha256": "4eaa6007c71fe8c4e634bfd5aae0bb6e9790e093dca261bca677d83ac41d7bcf"}


def predecessor_profile(registry):
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.97.0"):
        return registry
    from .generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.97.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or [dict(t) for t in registry.tools] != registry.raw["tools"]
        or [dict(p) for p in registry.tool_version_policies] != registry.raw["tool_versions"]
        or {d.id: list(d.tool_ids) for d in registry.datasets} != {d["id"]: d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Classification tool declarations drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.96.0"
    raw["tools"] = [t for t in raw["tools"] if t["id"] != TOOL]
    raw["presentation_order"]["tools"] = [t for t in raw["presentation_order"]["tools"] if t != TOOL]
    raw["tool_version_schema_catalog"] = copy.deepcopy(PREDECESSOR_CATALOG)
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = [t for t in dataset["tool_ids"] if t != TOOL]
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("Classification tool predecessor bytes differ")
    return replace(registry, registry_version="2.96.0", raw=raw, source_sha256=PREDECESSOR_SHA256,
        tools=tuple(t for t in registry.tools if t["id"] != TOOL),
        datasets=tuple(replace(d, tool_ids=tuple(t for t in d.tool_ids if t != TOOL)) for d in registry.datasets))
