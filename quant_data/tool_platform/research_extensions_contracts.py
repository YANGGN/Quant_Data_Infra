"""Opt-in peer comparison and guidance timeline over retained data."""
from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
import copy
from quant_data.errors import ValidationError
from quant_data.schema import validate_schema

PEERS = "company.compare_peers"
GUIDANCE = "company.get_guidance_history"
KINDS = {PEERS: "peer_comparison_v1", GUIDANCE: "guidance_timeline_v2"}
VERSIONS = {PEERS: "1.0.0", GUIDANCE: "2.0.0"}
MAX_RECORDS = 2000

def schema(kind):
    if kind == KINDS[PEERS]:
        from .retained_research_contracts import schema as base, KINDS as kinds
        value = copy.deepcopy(base(kinds["company.screen_fundamentals"]))
        props = value["properties"]
        symbols = props.pop("symbols")
        props.update(symbol=symbols["items"], peers={**symbols, "maxItems": 19},
                     group_by={"type": "string", "enum": ["industry", "sector"]})
        value["required"] = ["symbol"]
        return value
    if kind == KINDS[GUIDANCE]:
        from .transcript_research_contracts import schema as base, KINDS as kinds, HISTORY
        value = copy.deepcopy(base(kinds[HISTORY]))
        value["properties"].pop("sections")
        return value
    raise ValidationError("Unknown research extension contract")

@dataclass(frozen=True)
class ResearchExtensionArguments(Mapping):
    kind: str
    values: Mapping
    def __iter__(self): return iter(self.values)
    def __len__(self): return len(self.values)
    def __getitem__(self, key): return self.values[key]
    @property
    def limit(self): return MAX_RECORDS

def parse(kind, public):
    validate_schema(public, schema(kind))
    if kind == KINDS[PEERS]:
        from .retained_research_contracts import parse as base, KINDS as kinds
        values = dict(public)
        symbol = values.pop("symbol")
        peers = values.pop("peers", None)
        group = values.pop("group_by", "industry")
        if peers is not None and symbol in peers:
            raise ValidationError("The anchor symbol must not be repeated in peers")
        values["symbols"] = [symbol, *(peers or [])]
        values = dict(base(kinds["company.screen_fundamentals"], values))
        values.pop("symbols")
        values.update(symbol=symbol, group_by=group)
        if peers is not None: values["peers"] = tuple(sorted(peers))
    else:
        from .transcript_research_contracts import parse as base, KINDS as kinds, HISTORY
        values = dict(base(kinds[HISTORY], dict(public, sections=["guidance"])))
        values.pop("sections")
    return ResearchExtensionArguments(kind, MappingProxyType(values))
