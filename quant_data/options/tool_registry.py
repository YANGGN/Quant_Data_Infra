"""Exact predecessor for the additive Theta agent-tools compatibility lane."""
import copy
import hashlib
import json
from dataclasses import replace
from quant_data.errors import RegistryError
from quant_data.tool_platform.theta_contracts import TOOLS

# Filled from the reviewed predecessor declaration, never inferred from a new bundle.
PREDECESSOR_CATALOG = {'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.36.0', 'sha256': '0de4996ce0dba2de8e7d52def462df96e9fe71417525730d3ca499edc8035ced'}


def predecessor_profile(registry):
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.93.0"):
        return registry
    from quant_data.tool_platform.generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda raw: (json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.93.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or tuple(dict(t) for t in registry.tools) != tuple(registry.raw["tools"])
        or tuple(dict(p) for p in registry.tool_version_policies) != tuple(registry.raw["tool_versions"])):
        raise RegistryError("Theta tool registry source or parsed bindings drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.92.0"
    raw["tools"] = [t for t in raw["tools"] if t["id"] not in TOOLS]
    raw["presentation_order"]["tools"] = [t for t in raw["presentation_order"]["tools"] if t not in TOOLS]
    raw["tool_version_schema_catalog"] = copy.deepcopy(PREDECESSOR_CATALOG)
    expected = _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.92.0")]
    if hashlib.sha256(render(raw)).hexdigest() != expected:
        raise RegistryError("Theta tool predecessor projection drifted")
    return replace(registry, registry_version="2.92.0", raw=raw, source_sha256=expected,
        tools=tuple(t for t in registry.tools if t["id"] not in TOOLS))
