"""Immutable typed Stage 5 query-result contracts."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping, Sequence

from quant_data.contracts import (
    ExclusionV1,
    LineageRef,
    ResearchContractV1,
    TemporalQuery,
    TimeSeries,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict

Scalar = str | Decimal | int | bool | None

_QUERY_RESULT_CONTRACT = "quant_data.query_result"
_QUERY_RESULT_VERSION = "1.0.0"
_RESEARCH_CONTRACT = "quant_data.research_contract"
_RESEARCH_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class _ResultField:
    """One schema-visible constraint declared beside an immutable output field."""

    types: tuple[str, ...] = ()
    form: str = "scalar"
    required: bool = True
    order: int = 0
    const: str | None = None
    enum: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    item: _ResultField | None = None
    object_type: type[Any] | None = None


@dataclass(frozen=True, slots=True)
class _StaticResultField:
    """A public constant emitted by an immutable result contract."""

    name: str
    declaration: _ResultField


def _result_field(declaration: _ResultField, **kwargs: Any) -> Any:
    """Attach a schema declaration to one immutable dataclass field."""

    return dataclasses.field(metadata={"result_schema": declaration}, **kwargs)


def _validate_scalar(value: Scalar) -> None:
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValidationError("Result fields must be finite")
    if isinstance(value, float):
        raise ValidationError("Result fields must not use binary floats")
    if not isinstance(value, (str, Decimal, int, bool, type(None))):
        raise ValidationError("Result fields must be JSON scalar values")


@dataclass(frozen=True, slots=True)
class FieldV1:
    name: str = _result_field(
        _ResultField(
            types=("string",),
            min_length=1,
            max_length=100,
            order=1,
        )
    )
    value: Scalar = _result_field(
        _ResultField(
            types=("string", "number", "integer", "boolean", "null"),
            order=2,
        )
    )

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValidationError("Result field name is invalid")
        _validate_scalar(self.value)

    def to_primitive(self) -> dict[str, Scalar]:
        return {"name": self.name, "value": self.value}


def fields_from_mapping(value: Mapping[str, Scalar]) -> tuple[FieldV1, ...]:
    return tuple(FieldV1(name, value[name]) for name in sorted(value))


@dataclass(frozen=True, slots=True)
class RecordV1:
    record_type: str = _result_field(
        _ResultField(
            types=("string",),
            min_length=1,
            max_length=100,
            order=1,
        )
    )
    fields: tuple[FieldV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=100,
            item=_ResultField(form="object", object_type=FieldV1),
            order=2,
        )
    )

    def __post_init__(self) -> None:
        if not self.record_type or len(self.record_type) > 100:
            raise ValidationError("Record type is invalid")
        names = tuple(item.name for item in self.fields)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValidationError("Record fields must be uniquely and stably ordered")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "fields": [item.to_primitive() for item in self.fields],
        }


@dataclass(frozen=True, slots=True)
class MatrixV1:
    name: str = _result_field(
        _ResultField(
            types=("string",),
            min_length=1,
            max_length=100,
            order=1,
        )
    )
    row_labels: tuple[str, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=10_000,
            item=_ResultField(types=("string",), min_length=1),
            order=2,
        )
    )
    column_labels: tuple[str, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=100,
            item=_ResultField(types=("string",), min_length=1),
            order=3,
        )
    )
    values: tuple[tuple[Scalar, ...], ...] = _result_field(
        _ResultField(
            form="array",
            max_items=10_000,
            item=_ResultField(
                form="array",
                max_items=100,
                item=_ResultField(
                    types=("string", "number", "integer", "boolean", "null")
                ),
            ),
            order=4,
        )
    )

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValidationError("Matrix name is invalid")
        if (
            len(self.row_labels) > 10_000
            or len(self.column_labels) > 100
            or len(self.row_labels) * len(self.column_labels) > 100_000
        ):
            raise ValidationError("Matrix exceeds its typed hard limit")
        for label_set in (self.row_labels, self.column_labels):
            if (
                any(not isinstance(label, str) or not label for label in label_set)
                or len(label_set) != len(set(label_set))
            ):
                raise ValidationError("Matrix labels must be unique nonempty strings")
        if len(self.values) != len(self.row_labels):
            raise ValidationError("Matrix row count does not match its labels")
        for row in self.values:
            if len(row) != len(self.column_labels):
                raise ValidationError("Matrix values must be rectangular")
            for value in row:
                _validate_scalar(value)

    def to_primitive(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_labels": list(self.row_labels),
            "column_labels": list(self.column_labels),
            "values": [list(row) for row in self.values],
        }


@dataclass(frozen=True, slots=True)
class DiagnosticV1:
    code: str = _result_field(
        _ResultField(
            types=("string",),
            min_length=1,
            max_length=100,
            order=1,
        )
    )
    message: str = _result_field(
        _ResultField(
            types=("string",),
            min_length=1,
            max_length=500,
            order=2,
        )
    )
    metrics: tuple[FieldV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=100,
            item=_ResultField(form="object", object_type=FieldV1),
            order=3,
        ),
        default=(),
    )

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValidationError("Diagnostic code and message are required")
        names = tuple(item.name for item in self.metrics)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValidationError("Diagnostic metrics must be uniquely ordered")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "metrics": [item.to_primitive() for item in self.metrics],
        }


@dataclass(frozen=True, slots=True)
class ResearchEnvelopeV1:
    status: str = _result_field(
        _ResultField(
            types=("string",),
            enum=("not_applicable", "declared"),
            order=1,
        ),
        default="not_applicable",
    )
    contract: ResearchContractV1 | None = _result_field(
        _ResultField(form="object", object_type=ResearchContractV1, order=2),
        default=None,
    )

    def __post_init__(self) -> None:
        if self.status == "not_applicable" and self.contract is not None:
            raise ValidationError("Non-research results cannot carry a research contract")
        if self.status == "declared" and not isinstance(self.contract, ResearchContractV1):
            raise ValidationError("Research results require a typed research contract")
        if self.status not in {"not_applicable", "declared"}:
            raise ValidationError("Research-contract status is invalid")

    def to_primitive(self) -> dict[str, Any]:
        contract = self.contract.to_primitive() if self.contract is not None else {
            "contract": _RESEARCH_CONTRACT,
            "contract_version": _RESEARCH_CONTRACT_VERSION,
            "analysis_id": None,
            "analysis_kind": None,
            "model_id": None,
            "model_version": None,
            "parameters": [],
            "temporal": {
                "mode": "latest",
                "cutoff": None,
                "date_only_policy": "completed_date",
                "availability_basis": "not_applicable",
                "point_in_time_status": "not_applicable",
                "unsafe_reasons": [],
            },
            "point_in_time_status": "not_applicable",
            "unsafe_reasons": [],
            "vintage_policy": None,
            "availability_policy": None,
            "execution_policy": None,
            "sample": [],
            "exclusions": [],
            "uncertainty": [],
            "input_lineage": [],
        }
        return {
            "status": self.status,
            "contract": contract,
        }


@dataclass(frozen=True, slots=True)
class QueryResult:
    CONTRACT: ClassVar[str] = _QUERY_RESULT_CONTRACT
    CONTRACT_VERSION: ClassVar[str] = _QUERY_RESULT_VERSION
    _PUBLIC_STATIC_FIELDS: ClassVar[tuple[_StaticResultField, ...]] = (
        _StaticResultField(
            "contract",
            _ResultField(
                types=("string",),
                const=CONTRACT,
                order=1,
            ),
        ),
        _StaticResultField(
            "contract_version",
            _ResultField(
                types=("string",),
                const=CONTRACT_VERSION,
                order=2,
            ),
        ),
    )

    tool: str = _result_field(_ResultField(form="tool", order=3))
    status: str = _result_field(
        _ResultField(
            types=("string",),
            enum=("ok", "not_established"),
            order=4,
        ),
        default="ok",
    )
    records: tuple[RecordV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=10_000,
            item=_ResultField(form="object", object_type=RecordV1),
            order=5,
        ),
        default=(),
    )
    series: tuple[TimeSeries, ...] = _result_field(
        _ResultField(form="series_array", max_items=20, order=6),
        default=(),
    )
    matrices: tuple[MatrixV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=20,
            item=_ResultField(form="object", object_type=MatrixV1),
            order=7,
        ),
        default=(),
    )
    diagnostics: tuple[DiagnosticV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=100,
            item=_ResultField(form="object", object_type=DiagnosticV1),
            order=8,
        ),
        default=(),
    )
    warnings: tuple[WarningV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=100,
            item=_ResultField(form="object", object_type=WarningV1),
            order=9,
        ),
        default=(),
    )
    exclusions: tuple[ExclusionV1, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=1_000,
            item=_ResultField(form="object", object_type=ExclusionV1),
            order=10,
        ),
        default=(),
    )
    lineage: tuple[LineageRef, ...] = _result_field(
        _ResultField(
            form="array",
            max_items=1_000,
            item=_ResultField(form="object", object_type=LineageRef),
            order=11,
        ),
        default=(),
    )
    truncation: TruncationV1 = _result_field(
        _ResultField(form="object", object_type=TruncationV1, order=12),
        default_factory=lambda: TruncationV1(False, 1, 0, 0, False),
    )
    research_contract: ResearchEnvelopeV1 = _result_field(
        _ResultField(form="object", object_type=ResearchEnvelopeV1, order=13),
        default_factory=ResearchEnvelopeV1,
    )

    def __post_init__(self) -> None:
        if not self.tool:
            raise ValidationError("Query result requires a public tool name")
        if self.status not in {"ok", "not_established"}:
            raise ValidationError("Query result status is invalid")
        if not all(isinstance(item, RecordV1) for item in self.records):
            raise ValidationError("Query result records must use typed contracts")
        for item in self.series:
            if not isinstance(item, TimeSeries):
                raise ValidationError("Query result series must be typed TimeSeries values")
            item.validate_lineage()
        if not all(isinstance(item, MatrixV1) for item in self.matrices):
            raise ValidationError("Query result matrices must use typed contracts")
        if not all(isinstance(item, DiagnosticV1) for item in self.diagnostics):
            raise ValidationError("Query result diagnostics must use typed contracts")
        if not all(isinstance(item, WarningV1) for item in self.warnings):
            raise ValidationError("Query result warnings must use typed contracts")
        if not all(isinstance(item, ExclusionV1) for item in self.exclusions):
            raise ValidationError("Query result exclusions must use typed contracts")
        if not all(isinstance(item, LineageRef) for item in self.lineage):
            raise ValidationError("Query result lineage must use typed contracts")
        if not isinstance(self.truncation, TruncationV1):
            raise ValidationError("Query result truncation must use a typed contract")
        if not isinstance(self.research_contract, ResearchEnvelopeV1):
            raise ValidationError("Query result research envelope must be typed")
        if (
            len(self.records) > 10_000
            or len(self.series) > 20
            or len(self.matrices) > 20
            or len(self.diagnostics) > 100
            or len(self.warnings) > 100
            or len(self.exclusions) > 1_000
            or len(self.lineage) > 1_000
        ):
            raise ValidationError("Query result exceeds its typed hard limit")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT,
            "contract_version": self.CONTRACT_VERSION,
            "tool": self.tool,
            "status": self.status,
            "records": [item.to_primitive() for item in self.records],
            "series": [item.to_primitive() for item in self.series],
            "diagnostics": [item.to_primitive() for item in self.diagnostics],
            "matrices": [item.to_primitive() for item in self.matrices],
            "warnings": [item.to_primitive() for item in self.warnings],
            "exclusions": [item.to_primitive() for item in self.exclusions],
            "lineage": [item.to_primitive() for item in self.lineage],
            "truncation": self.truncation.to_primitive(),
            "research_contract": self.research_contract.to_primitive(),
        }

def research_envelope(
    tool: str,
    arguments: Mapping[str, Any],
    inputs: Sequence[TimeSeries],
    lineage: tuple[LineageRef, ...],
    *,
    exclusions: tuple[ExclusionV1, ...] = (),
    point_in_time_status: str = "not_established",
    unsafe_reasons: tuple[str, ...] = ("point_in_time_semantics_not_established",),
) -> ResearchEnvelopeV1:
    """Build one deterministic, path-free research contract from typed inputs."""

    raw_parameters = arguments.get("parameters", ())
    if not isinstance(raw_parameters, (list, tuple)):
        raise ValidationError("Research parameters must be a bounded field list")
    parameters: dict[str, Scalar] = {}
    for item in raw_parameters:
        if isinstance(item, Mapping):
            name, value = item.get("name"), item.get("value")
        else:
            name, value = getattr(item, "name", None), getattr(item, "value", None)
        if not isinstance(name, str) or not name or name in parameters:
            raise ValidationError("Research parameter names must be unique")
        _validate_scalar(value)
        parameters[name] = value

    cutoff = arguments.get("as_of")
    if cutoff is not None and (not isinstance(cutoff, str) or not cutoff):
        raise ValidationError("Research as_of must be a nonempty temporal string")
    if point_in_time_status not in {
        "safe", "unsafe", "not_applicable", "not_established"
    }:
        raise ValidationError("Research point-in-time status is invalid")
    reasons = () if point_in_time_status in {"safe", "not_applicable"} else tuple(unsafe_reasons)
    if point_in_time_status in {"unsafe", "not_established"} and not reasons:
        raise ValidationError("Unsafe or unestablished research requires reasons")
    if point_in_time_status == "unsafe" and not bool(arguments.get("unsafe_ok", False)):
        raise ValidationError("Unsafe research requires explicit unsafe_ok authorization")

    temporal = TemporalQuery(
        mode="as_of" if cutoff is not None else "latest",
        cutoff=cutoff,
        date_only_policy="completed_date",
        availability_basis="source_evidenced",
        point_in_time_status=point_in_time_status,
        unsafe_reasons=reasons,
    )
    sample = {
        "input_observation_count": sum(len(item.observations) for item in inputs),
        "input_series_count": len(inputs),
    }
    uncertainty = {"status": "not_established"}
    material = {
        "analysis_kind": tool,
        "model_id": f"quant_data.forward_reconstructed.{tool}",
        "model_version": "1.0.0",
        "parameters": dict(sorted(parameters.items())),
        "temporal": temporal.to_primitive(),
        "vintage_policy": "input_series_versions",
        "availability_policy": "source_evidenced_or_not_established",
        "execution_policy": "offline_fixture_read_only",
        "sample": sample,
        "exclusions": [item.to_primitive() for item in exclusions],
        "uncertainty": uncertainty,
        "input_lineage": [item.to_primitive() for item in lineage],
    }
    contract = ResearchContractV1(
        analysis_id=hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest(),
        analysis_kind=tool,
        model_id=material["model_id"],
        model_version=material["model_version"],
        parameters=parameters,
        temporal=temporal,
        point_in_time_status=point_in_time_status,
        vintage_policy=material["vintage_policy"],
        availability_policy=material["availability_policy"],
        execution_policy=material["execution_policy"],
        unsafe_reasons=reasons,
        sample=sample,
        exclusions=exclusions,
        uncertainty=uncertainty,
        input_lineage=lineage,
    )
    return ResearchEnvelopeV1("declared", contract)




def _declared_result_fields(
    contract_type: type[Any],
) -> tuple[tuple[dataclasses.Field[Any], _ResultField], ...]:
    """Return field declarations attached to a typed result contract."""

    declared = dataclasses.fields(contract_type)
    external = _EXTERNAL_RESULT_FIELD_DECLARATIONS.get(contract_type)
    if external is not None:
        if len(declared) != len(external):  # pragma: no cover - module invariant
            raise AssertionError(
                f"{contract_type.__name__} external schema declaration is stale"
            )
        return tuple(zip(declared, external, strict=True))

    result: list[tuple[dataclasses.Field[Any], _ResultField]] = []
    for field_definition in declared:
        declaration = field_definition.metadata.get("result_schema")
        if not isinstance(declaration, _ResultField):  # pragma: no cover
            raise AssertionError(
                f"{contract_type.__name__}.{field_definition.name} lacks a result schema declaration"
            )
        result.append((field_definition, declaration))
    return tuple(result)


def _static_result_fields(
    contract_type: type[Any],
) -> tuple[_StaticResultField, ...]:
    local = getattr(contract_type, "_PUBLIC_STATIC_FIELDS", None)
    if local is not None:
        return tuple(local)
    return _EXTERNAL_STATIC_RESULT_FIELDS.get(contract_type, ())


def _schema_for_result_field(
    declaration: _ResultField,
    *,
    series_schema: Mapping[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    """Generate one strict JSON Schema declaration from a typed field."""

    if declaration.form == "tool":
        return {"type": "string", "const": tool_name}
    if declaration.form == "series_array":
        result: dict[str, Any] = {
            "type": "array",
            "items": copy.deepcopy(dict(series_schema)),
        }
        if declaration.min_items is not None:
            result["minItems"] = declaration.min_items
        if declaration.max_items is not None:
            result["maxItems"] = declaration.max_items
        return result
    if declaration.form == "object":
        if declaration.object_type is None:  # pragma: no cover - module invariant
            raise AssertionError("Object result field lacks its typed contract")
        return _object_schema(
            declaration.object_type,
            series_schema=series_schema,
            tool_name=tool_name,
        )
    if declaration.form == "array":
        if declaration.item is None:  # pragma: no cover - module invariant
            raise AssertionError("Array result field lacks an item declaration")
        result = {"type": "array"}
        if declaration.min_items is not None:
            result["minItems"] = declaration.min_items
        if declaration.max_items is not None:
            result["maxItems"] = declaration.max_items
        result["items"] = _schema_for_result_field(
            declaration.item,
            series_schema=series_schema,
            tool_name=tool_name,
        )
        return result
    if declaration.form != "scalar":  # pragma: no cover - module invariant
        raise AssertionError(f"Unsupported result field form {declaration.form!r}")
    if not declaration.types:  # pragma: no cover - module invariant
        raise AssertionError("Scalar result field lacks JSON types")

    result = {
        "type": declaration.types[0]
        if len(declaration.types) == 1
        else list(declaration.types)
    }
    if declaration.const is not None:
        result["const"] = declaration.const
    if declaration.enum:
        result["enum"] = list(declaration.enum)
    if declaration.minimum is not None:
        result["minimum"] = declaration.minimum
    if declaration.maximum is not None:
        result["maximum"] = declaration.maximum
    if declaration.min_length is not None:
        result["minLength"] = declaration.min_length
    if declaration.max_length is not None:
        result["maxLength"] = declaration.max_length
    if declaration.min_items is not None:
        result["minItems"] = declaration.min_items
    if declaration.max_items is not None:
        result["maxItems"] = declaration.max_items
    return result


def _object_schema(
    contract_type: type[Any],
    *,
    series_schema: Mapping[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    """Generate required/properties from one immutable typed declaration."""

    declarations: list[tuple[str, _ResultField]] = [
        (item.name, item.declaration) for item in _static_result_fields(contract_type)
    ]
    declarations.extend(
        (field_definition.name, declaration)
        for field_definition, declaration in _declared_result_fields(contract_type)
    )
    if len({name for name, _ in declarations}) != len(declarations):
        raise AssertionError(f"{contract_type.__name__} declares duplicate public fields")
    ordered = sorted(
        enumerate(declarations),
        key=lambda item: (item[1][1].order, item[0]),
    )

    properties: dict[str, Any] = {}
    required: list[str] = []
    for _, (name, declaration) in ordered:
        properties[name] = _schema_for_result_field(
            declaration,
            series_schema=series_schema,
            tool_name=tool_name,
        )
        if declaration.required:
            required.append(name)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_EXTERNAL_RESULT_FIELD_DECLARATIONS: Mapping[
    type[Any], tuple[_ResultField, ...]
] = MappingProxyType(
    {
        WarningV1: (
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=100,
                order=1,
            ),
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=500,
                order=2,
            ),
        ),
        ExclusionV1: (
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=100,
                order=1,
            ),
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=500,
                order=3,
            ),
            _ResultField(types=("string", "null"), order=2),
        ),
        LineageRef: (
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=200,
                order=1,
            ),
            _ResultField(
                types=("string",),
                enum=("market", "macro", "company", "news"),
                order=2,
            ),
            _ResultField(
                types=("string",),
                min_length=1,
                max_length=300,
                order=3,
            ),
            _ResultField(types=("string", "null"), order=4),
            _ResultField(types=("string", "null"), order=5),
            _ResultField(types=("string", "null"), order=6),
        ),
        TruncationV1: (
            _ResultField(types=("boolean",), order=1),
            _ResultField(
                types=("integer",),
                minimum=1,
                maximum=10_000,
                order=2,
            ),
            _ResultField(
                types=("integer",),
                minimum=0,
                maximum=10_000,
                order=3,
            ),
            _ResultField(types=("integer", "null"), order=4),
            _ResultField(types=("boolean",), order=5),
            _ResultField(types=("string", "null"), order=6),
        ),
        TemporalQuery: (
            _ResultField(
                types=("string",),
                enum=("latest", "as_of", "first_release"),
                order=1,
            ),
            _ResultField(types=("string", "null"), order=2),
            _ResultField(
                types=("string",),
                enum=("completed_date", "calendar_date_inclusive"),
                order=3,
            ),
            _ResultField(types=("string",), min_length=1, order=4),
            _ResultField(
                types=("string",),
                enum=("safe", "unsafe", "not_applicable", "not_established"),
                order=5,
            ),
            _ResultField(
                form="array",
                max_items=100,
                item=_ResultField(types=("string",), min_length=1),
                order=6,
            ),
        ),
        ResearchContractV1: (
            _ResultField(types=("string", "null"), order=3),
            _ResultField(types=("string", "null"), order=4),
            _ResultField(types=("string", "null"), order=5),
            _ResultField(types=("string", "null"), order=6),
            _ResultField(
                form="array",
                max_items=100,
                item=_ResultField(form="object", object_type=FieldV1),
                order=7,
            ),
            _ResultField(form="object", object_type=TemporalQuery, order=8),
            _ResultField(
                types=("string",),
                enum=("safe", "unsafe", "not_applicable", "not_established"),
                order=9,
            ),
            _ResultField(types=("string", "null"), order=11),
            _ResultField(types=("string", "null"), order=12),
            _ResultField(types=("string", "null"), order=13),
            _ResultField(
                form="array",
                max_items=100,
                item=_ResultField(types=("string",), min_length=1),
                order=10,
            ),
            _ResultField(
                form="array",
                max_items=100,
                item=_ResultField(form="object", object_type=FieldV1),
                order=14,
            ),
            _ResultField(
                form="array",
                max_items=1_000,
                item=_ResultField(form="object", object_type=ExclusionV1),
                order=15,
            ),
            _ResultField(
                form="array",
                max_items=100,
                item=_ResultField(form="object", object_type=FieldV1),
                order=16,
            ),
            _ResultField(
                form="array",
                max_items=1_000,
                item=_ResultField(form="object", object_type=LineageRef),
                order=17,
            ),
        ),
    }
)

_EXTERNAL_STATIC_RESULT_FIELDS: Mapping[
    type[Any], tuple[_StaticResultField, ...]
] = MappingProxyType(
    {
        ResearchContractV1: (
            _StaticResultField(
                "contract",
                _ResultField(
                    types=("string",),
                    const=_RESEARCH_CONTRACT,
                    order=1,
                ),
            ),
            _StaticResultField(
                "contract_version",
                _ResultField(
                    types=("string",),
                    const=_RESEARCH_CONTRACT_VERSION,
                    order=2,
                ),
            ),
        ),
    }
)


def query_result_schema(name: str, series_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Generate the exact public output schema from immutable typed contracts."""

    return _object_schema(
        QueryResult,
        series_schema=series_schema,
        tool_name=name,
    )


def records_from_mappings(
    record_type: str,
    values: Iterable[Mapping[str, Scalar]],
) -> tuple[RecordV1, ...]:
    return tuple(
        RecordV1(record_type, fields_from_mapping(value)) for value in values
    )


__all__ = (
    "DiagnosticV1",
    "FieldV1",
    "MatrixV1",
    "QueryResult",
    "RecordV1",
    "ResearchEnvelopeV1",
    "Scalar",
    "fields_from_mapping",
    "query_result_schema",
    "records_from_mappings",
    "research_envelope",
)
