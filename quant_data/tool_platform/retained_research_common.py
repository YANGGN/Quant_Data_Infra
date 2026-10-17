"""Shared bounded composition helpers for retained-data research tools."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.json_codec import dumps_strict, loads_strict
from .results import QueryResult, RecordV1, fields_from_mapping, research_envelope

MAX_RECORDS = 2000

def instant(value):
    try:
        if not isinstance(value, str) or "T" not in value: raise ValueError()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("Use an offset-aware ISO capture-time instant") from exc

def cutoff(args, context):
    now = instant(context.clock.instant())
    at = instant(args["as_of"]) if args.get("as_of") else now
    if at > now: raise ValidationError("Capture cutoff cannot be in the future")
    context.checkpoint()
    return at

def digest(value):
    return hashlib.sha256(dumps_strict(value).encode()).hexdigest()

def unpack(record):
    return {f.name: f.value for f in record.fields}

def record(kind, values):
    fields = {}
    for key, value in values.items():
        if isinstance(value, (dict, list, tuple)):
            key, value = key + "_json", dumps_strict(value)
        elif isinstance(value, float):
            value = Decimal(str(value))
        fields[key] = value
    return RecordV1(kind, fields_from_mapping(fields))

def number(value):
    if value is None or isinstance(value, bool): return None
    try:
        out = Decimal(str(value))
        return out if out.is_finite() and abs(out) < Decimal("1e100") else None
    except (ValueError, ArithmeticError):
        return None

def result(name, args, at, rows, *, lineage=(), warnings=(), established=None):
    if len(rows) > MAX_RECORDS: raise ResourceLimitError("Research result exceeds 2000 records; narrow the request")
    records = tuple(record(kind, fields) for kind, fields in rows)
    parameters = dict(query_json=dumps_strict(args), capture_cutoff=at,
                      input_result_digest=digest([r.to_primitive() for r in records]), calculation_version="retained_research_v1")
    envelope = research_envelope(name, dict(as_of=at, parameters=[
        dict(name=k, value=v) for k,v in sorted(parameters.items())]), (), (),
        unsafe_reasons=("historical_public_availability_not_established",))
    contract = replace(envelope.contract,
        availability_policy="retained_local_capture_cutoff",
        execution_policy="bounded_read_only_composition_no_provider_calls",
        temporal=replace(envelope.contract.temporal, availability_basis="local_capture_time_only"),
        sample={"output_record_count": len(records)})
    material = contract.to_primitive(); material.pop("analysis_id")
    envelope = replace(envelope, contract=replace(contract, analysis_id=digest(material)))
    return QueryResult(tool=name, status="ok" if (bool(rows) if established is None else established) else "not_established",
        records=records, lineage=tuple(lineage),
        warnings=(WarningV1("retained_capture_semantics",
            "Cutoffs select retained local knowledge. Historical public availability and an atomic cross-store snapshot are not established."),
            *(WarningV1(code, message) for code, message in warnings)),
        truncation=TruncationV1(False, MAX_RECORDS, len(records), len(records), False),
        research_contract=envelope)

def compose(name, version, public, context, registry):
    """Use the registered typed primitive and the parent's fixed host budget/deadline."""
    from .arguments import parse_arguments
    from .operations import invoke_operation
    declaration = registry.tool(name, version)
    # The dispatcher normally resolves the kind; reuse its authoritative resolver.
    from quant_data.boundary.dispatcher import _STAGE5_INPUT_KINDS, _VERSIONED_INPUT_KINDS
    from .price_basis_versions import PRICE_BASIS_VERSIONS
    pair = PRICE_BASIS_VERSIONS.get(name)
    input_version = pair[0] if pair and pair[1] == version else version
    kind = _VERSIONED_INPUT_KINDS.get((name, input_version), _STAGE5_INPUT_KINDS[name])
    def no_series(value):
        raise ValidationError("This composition does not accept caller series")
    typed = parse_arguments(kind, public, no_series)
    nested = replace(context, tool_name=name, tool_version=version,
        operation_graph_id=declaration["operation_graph_id"], operation_version=declaration["operation_version"])
    return invoke_operation(name, typed, nested, registry)
