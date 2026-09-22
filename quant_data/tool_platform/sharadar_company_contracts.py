"""Explicit Sharadar-first company tools; frozen SEC versions remain available."""
from __future__ import annotations
import copy
from collections.abc import Mapping
from dataclasses import dataclass, fields
import re
from quant_data.errors import ValidationError
from quant_data.market.collection_universe import _utc

TOOLS = ("company.get_fundamentals", "company.get_share_count_history")
KINDS = {name: name.rsplit(".", 1)[1] + "_sharadar_v3" for name in TOOLS}
VERSION = "3.0.0"
DATASETS = ("company.sharadar.sf1", "company.sharadar.evidence",
            "market.collection.membership", "market.collection.provider_mappings")
DIMENSIONS = ("ARQ", "ARY", "ART", "MRQ", "MRY", "MRT")
SHARE_FIELDS = ("sharesbas", "shareswa", "shareswadil")

def schema(kind):
    if kind not in KINDS.values():
        raise ValidationError("Unknown Sharadar company contract")
    return {"type": "object", "additionalProperties": False,
        "required": ["symbol"], "properties": {
            "symbol": {"type": "string", "minLength": 1, "maxLength": 32},
            "dimension": {"type": "string", "enum": list(DIMENSIONS)},
            "mode": {"type": "string", "enum": ["latest", "as_of"]},
            "as_of": {"type": "string", "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}

@dataclass(frozen=True)
class SharadarCompanyArgumentsV3(Mapping):
    kind: str
    symbol: str
    dimension: str = "ARQ"
    mode: str = "latest"
    as_of: str | None = None
    limit: int = 20

    def __iter__(self):
        return iter(f.name for f in fields(self) if f.name != "kind")
    def __len__(self):
        return 5
    def __getitem__(self, key):
        if key not in tuple(self):
            raise KeyError(key)
        return getattr(self, key)

def parse(kind, value):
    spec = schema(kind)
    if (not isinstance(value, Mapping) or set(value) - set(spec["properties"])
        or not set(spec["required"]) <= set(value)):
        raise ValidationError("Sharadar company arguments differ from their contract")
    if not isinstance(value["symbol"], str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,31}", value["symbol"]):
        raise ValidationError("Use one exact selected-universe symbol")
    dimension, mode, limit = value.get("dimension", "ARQ"), value.get("mode", "latest"), value.get("limit", 20)
    if (dimension not in DIMENSIONS or mode not in ("latest", "as_of")
        or type(limit) is not int or not 1 <= limit <= 100):
        raise ValidationError("Sharadar dimension, mode or limit is invalid")
    if (mode == "as_of") != ("as_of" in value):
        raise ValidationError("as_of mode requires a cutoff; latest mode omits it")
    at = _utc(value["as_of"]) if "as_of" in value else None
    return SharadarCompanyArgumentsV3(kind, value["symbol"], dimension, mode, at, limit)

def add_policies(policies):
    from .results import query_result_schema
    result = copy.deepcopy(list(policies))
    for policy in result:
        name = policy["tool"]
        if name not in TOOLS:
            continue
        base = next(v for v in policy["variants"] if v["version"] == "2.0.0")
        variant = copy.deepcopy(base)
        graph = f"tool_platform.{name}.v3"
        variant.update(version=VERSION, operation_version=VERSION, handler=graph,
            operation_graph_id=graph,
            compatibility={"status": "successor_breaking_v2", "predecessor": "2.0.0"},
            input_type="SharadarCompanyArgumentsV3", input_kind=KINDS[name],
            input_schema_id=f"urn:quant-data:tool:{name}:input:{VERSION}",
            output_schema_id=f"urn:quant-data:tool:{name}:output:{VERSION}",
            input_schema=schema(KINDS[name]),
            output_schema=query_result_schema(name, {"type": "object", "additionalProperties": False, "properties": {}, "required": []}),
            stores=["market", "company"], datasets=list(DATASETS),
            description="Routine Sharadar fundamentals by selected symbol, with source-native values, separate AR/MR dimensions and local-capture cutoffs."
                if name == TOOLS[0] else "Read source-native Sharadar sharesbas, shareswa and shareswadil with their original row provenance.",
            examples=[{"symbol": "AAPL", "dimension": "ARQ", "limit": 20}],
            assumptions=["preferred_source_sharadar", "no_implicit_sec_or_fmp_fallback",
                "source_native_fields_no_metric_equivalence", "one_explicit_ar_or_mr_dimension",
                "local_capture_as_of_not_historical_first_release",
                "evidenced_permanent_subject_mapping", "values_and_missingness_in_json_scalar_fields"],
            workload_bounds={"max_rows": 101, "max_series": 1, "max_operations": 5000000,
                "max_request_bytes": 1048576, "max_response_bytes": 8388608},
            availability_policy={"modes": ["latest", "as_of"], "point_in_time_default": "latest"})
        variant["contracts"].update(availability="retained_local_capture",
            point_in_time="local_capture_cutoff_with_separate_ar_mr_dimensions", returns="not_applicable")
        policy["variants"].append(variant)
    return tuple(result)

def remove_policies(policies):
    result = copy.deepcopy(list(policies))
    for policy in result:
        if policy["tool"] in TOOLS:
            policy["variants"] = [v for v in policy["variants"] if v["version"] != VERSION]
    return tuple(result)
