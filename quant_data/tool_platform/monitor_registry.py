"""Exact predecessor projection for the additive monitor tool contracts."""
import copy
from dataclasses import replace
import hashlib
import json
from quant_data.errors import RegistryError
from .monitor_contracts import TOOLS

PREDECESSOR_SHA256 = "dabb4d90d9644a837af214435d315b73ce457742d909e2c58bf90cbbb49beb6c"
PREDECESSOR_CATALOG = {'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.38.0', 'sha256': '008b92524e994ae0439f004fe573606b5239824c9855cb1d90932cb0c5e93e27'}


def predecessor_profile(registry):
    if (registry.schema_version, registry.registry_version) != ("1.9.0", "2.95.0"):
        return registry
    from .generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render = lambda value: (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True) + "\n").encode()
    if (registry.source_sha256 != _REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0", "2.95.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest() != registry.source_sha256
        or [dict(t) for t in registry.tools] != registry.raw["tools"]
        or [dict(p) for p in registry.tool_version_policies] != registry.raw["tool_versions"]):
        raise RegistryError("Monitor tool registry declarations drifted")
    raw = copy.deepcopy(dict(registry.raw))
    raw["registry_version"] = "2.94.0"
    raw["tools"] = [t for t in raw["tools"] if t["id"] not in TOOLS]
    raw["presentation_order"]["tools"] = [t for t in raw["presentation_order"]["tools"] if t not in TOOLS]
    raw["tool_version_schema_catalog"] = copy.deepcopy(PREDECESSOR_CATALOG)
    if hashlib.sha256(render(raw)).hexdigest() != PREDECESSOR_SHA256:
        raise RegistryError("Monitor tool predecessor bytes differ")
    return replace(registry, registry_version="2.94.0", raw=raw, source_sha256=PREDECESSOR_SHA256,
        tools=tuple(t for t in registry.tools if t["id"] not in TOOLS))
