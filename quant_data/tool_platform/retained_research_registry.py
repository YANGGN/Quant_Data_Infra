"""Exact projection of the retained research addition to reviewed registry 2.93."""
import copy
from dataclasses import replace
import hashlib
import json
from quant_data.errors import RegistryError
from .retained_research_contracts import NEW_TOOLS,SUCCESSORS
PREDECESSOR_CATALOG = {'resource': 'quant_data/generated/tool_contract_schemas_v2.json', 'schema_id': 'quant_data.tool_contract_catalog.v2', 'schema_version': '2.37.0', 'sha256': '04ea6c08c621744411239dce5e01856984c9a0fd5a0b14750954007168b7716d'}
PREDECESSOR_SHA256 = "6ee19b30575fc4b2613c0583fd38c36aa1780fdb25bf51e1233a0b61b097316c"

def predecessor_profile(registry):
    if (registry.schema_version,registry.registry_version)!=("1.9.0","2.94.0"):return registry
    from .generate import _REVIEWED_REGISTRY_SOURCE_SHA256
    render=lambda raw:(json.dumps(raw,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    if (registry.source_sha256!=_REVIEWED_REGISTRY_SOURCE_SHA256[("1.9.0","2.94.0")]
        or hashlib.sha256(render(registry.raw)).hexdigest()!=registry.source_sha256
        or [dict(t) for t in registry.tools]!=registry.raw["tools"]
        or [dict(p) for p in registry.tool_version_policies]!=registry.raw["tool_versions"]
        or {d.id:list(d.tool_ids) for d in registry.datasets}!={d["id"]:d["tool_ids"] for d in registry.raw["datasets"]}):
        raise RegistryError("Retained research registry declarations drifted")
    raw=copy.deepcopy(dict(registry.raw))
    raw["registry_version"]="2.93.0"
    raw["tools"]=[t for t in raw["tools"] if t["id"] not in NEW_TOOLS]
    raw["tool_versions"]=[p for p in raw["tool_versions"] if p["tool"] not in SUCCESSORS]
    raw["presentation_order"]["tools"]=[t["id"] for t in raw["tools"]]
    raw["tool_version_schema_catalog"]=copy.deepcopy(PREDECESSOR_CATALOG)
    references={d["id"]:[] for d in raw["datasets"]}
    for entry in [*raw["tools"],*[v for p in raw["tool_versions"] for v in p["variants"]]]:
        for dataset in entry["datasets"]:
            if entry["id"] not in references[dataset]:references[dataset].append(entry["id"])
    for d in raw["datasets"]:d["tool_ids"]=references[d["id"]]
    if hashlib.sha256(render(raw)).hexdigest()!=PREDECESSOR_SHA256:
        raise RegistryError("Retained research predecessor bytes differ")
    return replace(registry,registry_version="2.93.0",raw=raw,source_sha256=PREDECESSOR_SHA256,
        tools=tuple(raw["tools"]),tool_version_policies=tuple(raw["tool_versions"]),
        datasets=tuple(replace(d,tool_ids=tuple(references[d.id])) for d in registry.datasets))
