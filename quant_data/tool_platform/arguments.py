"""Immutable typed input contracts for the Stage 5 tool platform.

Public JSON remains a boundary representation.  This module is the single
source for the five non-legacy input shapes: the dataclass field declarations
drive generated schemas, and the same declarations are used to construct the
immutable mappings passed to operations.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar

from quant_data.contracts import TimeSeries
from quant_data.errors import Issue, ValidationError
from quant_data.json_codec import ensure_number_within_limits
from quant_data.temporal import TemporalValue, parse_date


Scalar = str | int | Decimal | bool | None


@dataclass(frozen=True, slots=True)
class _InputField:
    """Schema-visible constraints declared beside one typed dataclass field."""

    types: tuple[str, ...] = ()
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    enum: tuple[str, ...] = ()
    item: _InputField | None = None
    form: str = "scalar"


def _typed_field(contract: _InputField) -> Any:
    """Attach one immutable input declaration to a dataclass field."""

    return dataclasses.field(metadata={"schema": contract})


class _ArgumentMapping(Mapping[str, Any]):
    """A frozen dataclass that retains the ordinary Mapping operation API."""

    __slots__ = ()

    def __getitem__(self, key: str) -> Any:
        if key not in self._field_names():
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._field_names())

    def __len__(self) -> int:
        return len(self._field_names())

    @classmethod
    def _field_names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in dataclasses.fields(cls))


@dataclass(frozen=True, slots=True)
class ArgumentParameter(_ArgumentMapping):
    """One immutable scalar parameter accepted by a composable operation."""

    name: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=100)
    )
    value: Scalar = _typed_field(
        _InputField(types=("string", "number", "integer", "boolean", "null"))
    )


@dataclass(frozen=True, slots=True)
class SearchArguments(_ArgumentMapping):
    """Typed contract for a bounded store-backed search."""

    INPUT_KIND: ClassVar[str] = "search"

    query: str = _typed_field(_InputField(types=("string",), max_length=500))
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=500)
    )


@dataclass(frozen=True, slots=True)
class QueryArguments(_ArgumentMapping):
    """Typed contract for a generic registered query."""

    INPUT_KIND: ClassVar[str] = "query"

    identifiers: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=200),
            form="array",
        )
    )
    mode: str = _typed_field(
        _InputField(
            types=("string",), enum=("latest", "as_of", "first_release")
        )
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"))
    )
    end_date: str | None = _typed_field(_InputField(types=("string", "null")))
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class SingleSeriesArguments(_ArgumentMapping):
    """Typed contract for one directly composable ``TimeSeries`` input."""

    INPUT_KIND: ClassVar[str] = "single_series"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class MultiSeriesArguments(_ArgumentMapping):
    """Typed contract for a bounded sequence of composable ``TimeSeries`` values."""

    INPUT_KIND: ClassVar[str] = "multi_series"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class ResearchSeriesArguments(_ArgumentMapping):
    """Typed multi-series contract with explicit research safety inputs."""

    INPUT_KIND: ClassVar[str] = "research"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    unsafe_ok: bool = _typed_field(_InputField(types=("boolean",)))


_ARGUMENT_TYPES: tuple[type[_ArgumentMapping], ...] = (
    SearchArguments,
    QueryArguments,
    SingleSeriesArguments,
    MultiSeriesArguments,
    ResearchSeriesArguments,
)


@dataclass(frozen=True, slots=True)
class _PreparedArguments:
    """Schema-shaped, decoder-free material validated before typed construction."""

    argument_type: type[_ArgumentMapping]
    values: Mapping[str, Any]
    raw_series: tuple[Any, ...] = ()
    source_rows: int = 0


def _argument_type_for(input_kind: str) -> type[_ArgumentMapping]:
    if not isinstance(input_kind, str):
        raise ValidationError("Tool input kind must be a string")
    for argument_type in _ARGUMENT_TYPES:
        if argument_type.INPUT_KIND == input_kind:
            return argument_type
    raise ValidationError("Unsupported Tool Platform input kind")


def _declared_fields(
    argument_type: type[_ArgumentMapping],
) -> tuple[tuple[dataclasses.Field[Any], _InputField], ...]:
    result: list[tuple[dataclasses.Field[Any], _InputField]] = []
    for declared in dataclasses.fields(argument_type):
        contract = declared.metadata.get("schema")
        if not isinstance(contract, _InputField):  # pragma: no cover - module invariant
            raise AssertionError(f"{argument_type.__name__}.{declared.name} lacks a schema contract")
        result.append((declared, contract))
    return tuple(result)


def _field_contract(
    argument_type: type[_ArgumentMapping], name: str
) -> _InputField:
    for declared, contract in _declared_fields(argument_type):
        if declared.name == name:
            return contract
    raise AssertionError(f"{argument_type.__name__} has no {name!r} field")


def _copy_series_schema(series_schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(series_schema, Mapping):
        raise ValidationError("TimeSeries schema must be an object")
    return copy.deepcopy(dict(series_schema))


def _schema_for_field(
    contract: _InputField, series_schema: Mapping[str, Any]
) -> dict[str, Any]:
    if contract.form == "series":
        return _copy_series_schema(series_schema)
    if contract.form == "series_array":
        schema: dict[str, Any] = {
            "type": "array",
            "items": _copy_series_schema(series_schema),
        }
        if contract.min_items is not None:
            schema["minItems"] = contract.min_items
        if contract.max_items is not None:
            schema["maxItems"] = contract.max_items
        return schema
    if contract.form == "parameters":
        schema = {
            "type": "array",
            "items": _object_schema(ArgumentParameter, series_schema),
        }
        if contract.max_items is not None:
            schema["maxItems"] = contract.max_items
        return schema

    schema = {
        "type": contract.types[0]
        if len(contract.types) == 1
        else list(contract.types)
    }
    if contract.minimum is not None:
        schema["minimum"] = contract.minimum
    if contract.maximum is not None:
        schema["maximum"] = contract.maximum
    if contract.min_length is not None:
        schema["minLength"] = contract.min_length
    if contract.max_length is not None:
        schema["maxLength"] = contract.max_length
    if contract.min_items is not None:
        schema["minItems"] = contract.min_items
    if contract.max_items is not None:
        schema["maxItems"] = contract.max_items
    if contract.enum:
        schema["enum"] = list(contract.enum)
    if contract.form == "array":
        if contract.item is None:  # pragma: no cover - module invariant
            raise AssertionError("Array field lacks an item declaration")
        schema["items"] = _schema_for_field(contract.item, series_schema)
    return schema


def _object_schema(
    argument_type: type[_ArgumentMapping], series_schema: Mapping[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for declared, contract in _declared_fields(argument_type):
        properties[declared.name] = _schema_for_field(contract, series_schema)
        if contract.required:
            required.append(declared.name)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def input_schema(input_kind: str, series_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one public JSON Schema from its immutable typed declaration.

    ``series_schema`` is deliberately supplied by the registry's typed
    ``TimeSeries`` contract.  It is copied into the composed schema without
    re-declaring (or weakening) its observation bound here.
    """

    return _object_schema(_argument_type_for(input_kind), series_schema)


def _validation_error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError("Tool arguments are invalid", issues=(Issue(pointer, rule, message),))


def _require_mapping(value: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _validation_error(pointer, "type", "Expected an object")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    argument_type: type[_ArgumentMapping],
    pointer: str,
) -> None:
    expected = {declared.name for declared, _ in _declared_fields(argument_type)}
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    issues: list[Issue] = []
    for name in sorted(missing, key=str):
        issues.append(Issue(f"{pointer}/{name}", "required", "Required field is missing"))
    for name in sorted(extra, key=str):
        suffix = str(name) if isinstance(name, str) else "field"
        issues.append(Issue(f"{pointer}/{suffix}", "additional_properties", "Unknown field"))
    if issues:
        raise ValidationError("Tool arguments are invalid", issues=issues)


def _validated_string(value: Any, contract: _InputField, pointer: str) -> str:
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected a string")
    if contract.min_length is not None and len(value) < contract.min_length:
        raise _validation_error(pointer, "min_length", "String is too short")
    if contract.max_length is not None and len(value) > contract.max_length:
        raise _validation_error(pointer, "max_length", "String exceeds the supported length")
    return value


def _validated_limit(value: Any, contract: _InputField, pointer: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _validation_error(pointer, "type", "Expected an integer")
    if contract.minimum is not None and value < contract.minimum:
        raise _validation_error(pointer, "minimum", "Number is below the supported limit")
    if contract.maximum is not None and value > contract.maximum:
        raise _validation_error(pointer, "maximum", "Number exceeds the supported limit")
    return value


def _validated_scalar(value: Any, pointer: str) -> Scalar:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        ensure_number_within_limits(value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _validation_error(pointer, "finite", "Numeric values must be finite")
        ensure_number_within_limits(value)
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _validation_error(pointer, "finite", "Numeric values must be finite")
        ensure_number_within_limits(value)
        return value
    raise _validation_error(pointer, "type", "Expected a JSON scalar value")


def _optional_temporal(value: Any, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected an ISO date or aware datetime")
    TemporalValue.parse(value, pointer=pointer)
    return value


def _optional_calendar_date(value: Any, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected a calendar date")
    parse_date(value, pointer=pointer)
    return value


def _validated_parameters(
    value: Any, argument_type: type[_ArgumentMapping], pointer: str
) -> tuple[ArgumentParameter, ...]:
    contract = _field_contract(argument_type, "parameters")
    if not isinstance(value, (list, tuple)):
        raise _validation_error(pointer, "type", "Expected an array")
    if contract.max_items is not None and len(value) > contract.max_items:
        raise _validation_error(pointer, "max_items", "Array exceeds the supported item limit")

    names: set[str] = set()
    parameters: list[ArgumentParameter] = []
    name_contract = _field_contract(ArgumentParameter, "name")
    for index, item in enumerate(value):
        item_pointer = f"{pointer}/{index}"
        mapping = _require_mapping(item, item_pointer)
        _require_exact_fields(mapping, ArgumentParameter, item_pointer)
        name = _validated_string(mapping["name"], name_contract, f"{item_pointer}/name")
        if not name.strip():
            raise _validation_error(
                f"{item_pointer}/name", "min_length", "Parameter names cannot be blank"
            )
        if name in names:
            raise _validation_error(
                f"{item_pointer}/name", "unique", "Parameter names must be unique"
            )
        names.add(name)
        parameters.append(
            ArgumentParameter(
                name=name,
                value=_validated_scalar(mapping["value"], f"{item_pointer}/value"),
            )
        )
    return tuple(parameters)


def _validated_series_material(
    value: Any,
    contract: _InputField,
    pointer: str,
) -> tuple[Any, ...]:
    many = contract.form == "series_array"
    if many:
        if not isinstance(value, (list, tuple)):
            raise _validation_error(pointer, "type", "Expected an array of TimeSeries values")
        if contract.min_items is not None and len(value) < contract.min_items:
            raise _validation_error(pointer, "min_items", "At least one TimeSeries is required")
        if contract.max_items is not None and len(value) > contract.max_items:
            raise _validation_error(pointer, "max_items", "Array exceeds the supported item limit")
        raw_series = tuple(value)
    else:
        raw_series = (value,)

    for index, raw in enumerate(raw_series):
        if not isinstance(raw, (TimeSeries, Mapping)):
            item_pointer = f"{pointer}/{index}" if many else pointer
            raise _validation_error(item_pointer, "type", "Expected a TimeSeries object")
    return raw_series


def _raw_observation_count(value: Any) -> int:
    if isinstance(value, TimeSeries):
        return len(value.observations)
    if isinstance(value, Mapping):
        observations = value.get("observations")
        if isinstance(observations, (list, tuple)):
            return len(observations)
    return 0


def _prepared(input_kind: str, public: Mapping[str, Any]) -> _PreparedArguments:
    argument_type = _argument_type_for(input_kind)
    mapping = _require_mapping(public, "/arguments")
    _require_exact_fields(mapping, argument_type, "/arguments")

    if argument_type is SearchArguments:
        query = _validated_string(
            mapping["query"], _field_contract(argument_type, "query"), "/query"
        )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"query": query, "as_of": as_of, "limit": limit}),
        )

    if argument_type is QueryArguments:
        identifiers_contract = _field_contract(argument_type, "identifiers")
        raw_identifiers = mapping["identifiers"]
        if not isinstance(raw_identifiers, (list, tuple)):
            raise _validation_error("/identifiers", "type", "Expected an array")
        if (
            identifiers_contract.max_items is not None
            and len(raw_identifiers) > identifiers_contract.max_items
        ):
            raise _validation_error(
                "/identifiers", "max_items", "Array exceeds the supported item limit"
            )
        assert identifiers_contract.item is not None  # module invariant
        identifiers = tuple(
            _validated_string(item, identifiers_contract.item, f"/identifiers/{index}")
            for index, item in enumerate(raw_identifiers)
        )
        if any(not identifier.strip() for identifier in identifiers):
            raise _validation_error(
                "/identifiers", "min_length", "Identifiers cannot be blank"
            )

        mode = _validated_string(
            mapping["mode"], _field_contract(argument_type, "mode"), "/mode"
        )
        mode_contract = _field_contract(argument_type, "mode")
        if mode not in mode_contract.enum:
            raise _validation_error("/mode", "enum", "Unsupported query mode")
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if mode == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if mode != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )

        start_date = _optional_calendar_date(mapping["start_date"], "/start_date")
        end_date = _optional_calendar_date(mapping["end_date"], "/end_date")
        if start_date is not None and end_date is not None:
            if parse_date(end_date, pointer="/end_date") < parse_date(
                start_date, pointer="/start_date"
            ):
                raise _validation_error(
                    "/end_date", "range", "end_date cannot precede start_date"
                )

        parameters = _validated_parameters(mapping["parameters"], argument_type, "/parameters")
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "identifiers": identifiers,
                    "mode": mode,
                    "as_of": as_of,
                    "start_date": start_date,
                    "end_date": end_date,
                    "parameters": parameters,
                    "limit": limit,
                }
            ),
        )

    series_contract = _field_contract(argument_type, "series")
    raw_series = _validated_series_material(mapping["series"], series_contract, "/series")
    parameters = _validated_parameters(mapping["parameters"], argument_type, "/parameters")
    limit = _validated_limit(
        mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
    )
    values: dict[str, Any] = {"parameters": parameters, "limit": limit}
    if argument_type is ResearchSeriesArguments:
        values["as_of"] = _optional_temporal(mapping["as_of"], "/as_of")
        if not isinstance(mapping["unsafe_ok"], bool):
            raise _validation_error("/unsafe_ok", "type", "Expected a boolean")
        values["unsafe_ok"] = mapping["unsafe_ok"]
    return _PreparedArguments(
        argument_type,
        MappingProxyType(values),
        raw_series=raw_series,
        source_rows=max((_raw_observation_count(item) for item in raw_series), default=0),
    )


def _decode_one(
    raw: Any, decode_series: Callable[[Any], TimeSeries], pointer: str
) -> TimeSeries:
    if isinstance(raw, TimeSeries):
        result = raw
    else:
        if not callable(decode_series):
            raise _validation_error(pointer, "decoder", "TimeSeries decoder is unavailable")
        try:
            result = decode_series(raw)
        except ValidationError:
            raise
        except Exception as exc:
            raise _validation_error(pointer, "series", "TimeSeries input is invalid") from exc
    if not isinstance(result, TimeSeries):
        raise _validation_error(pointer, "type", "TimeSeries decoder returned an invalid value")
    result.validate_lineage()
    return result


def parse_arguments(
    input_kind: str,
    public: Mapping[str, Any],
    decode_series: Callable[[Any], TimeSeries],
) -> _ArgumentMapping:
    """Validate public material and return an immutable typed Mapping.

    JSON Schema validation normally precedes this function.  It nevertheless
    rechecks every scalar/container type and semantic cross-field rule so an
    in-process caller cannot bypass validation.  Series cardinality is checked
    before any decoder invocation.
    """

    prepared = _prepared(input_kind, public)
    values = prepared.values
    if prepared.argument_type is SearchArguments:
        return SearchArguments(**dict(values))
    if prepared.argument_type is QueryArguments:
        return QueryArguments(**dict(values))

    decoded = tuple(
        _decode_one(
            raw,
            decode_series,
            f"/series/{index}"
            if prepared.argument_type in {MultiSeriesArguments, ResearchSeriesArguments}
            else "/series",
        )
        for index, raw in enumerate(prepared.raw_series)
    )
    if prepared.argument_type is SingleSeriesArguments:
        return SingleSeriesArguments(series=decoded[0], **dict(values))
    if prepared.argument_type is MultiSeriesArguments:
        return MultiSeriesArguments(series=decoded, **dict(values))
    if prepared.argument_type is ResearchSeriesArguments:
        return ResearchSeriesArguments(series=decoded, **dict(values))
    raise AssertionError("Unsupported typed argument declaration")  # pragma: no cover


def _public_value(value: Any, pointer: str) -> Any:
    if isinstance(value, TimeSeries):
        value.validate_lineage()
        return _public_value(value.to_primitive(), pointer)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise _validation_error(pointer, "key_type", "JSON object keys must be strings")
            result[name] = _public_value(item, f"{pointer}/{name}")
        return result
    if isinstance(value, (list, tuple)):
        return [_public_value(item, f"{pointer}/{index}") for index, item in enumerate(value)]
    return _validated_scalar(value, pointer)


def public_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh JSON-native representation without mutating ``arguments``."""

    mapping = _require_mapping(arguments, "/arguments")
    series_value = mapping.get("series")
    if isinstance(series_value, (list, tuple)) and len(series_value) > 20:
        raise _validation_error(
            "/series",
            "max_items",
            "Array exceeds the supported TimeSeries item limit",
        )
    return _public_value(mapping, "/arguments")


def _inferred_input_kind(public: Mapping[str, Any]) -> str:
    mapping = _require_mapping(public, "/arguments")
    names = set(mapping)
    research_names = {item.name for item, _ in _declared_fields(ResearchSeriesArguments)}
    multi_names = {item.name for item, _ in _declared_fields(MultiSeriesArguments)}
    single_names = {item.name for item, _ in _declared_fields(SingleSeriesArguments)}
    query_names = {item.name for item, _ in _declared_fields(QueryArguments)}
    search_names = {item.name for item, _ in _declared_fields(SearchArguments)}
    if names == research_names:
        return ResearchSeriesArguments.INPUT_KIND
    if names == multi_names:
        raw_series = mapping.get("series")
        return (
            MultiSeriesArguments.INPUT_KIND
            if isinstance(raw_series, (list, tuple))
            else SingleSeriesArguments.INPUT_KIND
        )
    if names == single_names:
        return SingleSeriesArguments.INPUT_KIND
    if names == query_names:
        return QueryArguments.INPUT_KIND
    if names == search_names:
        return SearchArguments.INPUT_KIND
    raise _validation_error("/arguments", "shape", "Arguments do not match a registered input shape")


def preflight_dimensions(public: Mapping[str, Any]) -> dict[str, int]:
    """Derive deterministic workload dimensions without decoding TimeSeries.

    Rows cover the larger of a bounded result limit and supplied observation
    material.  Multi-series work is conservatively quadratic in its series
    count, matching the platform's correlation/alignment workload ceiling.
    """

    prepared = _prepared(_inferred_input_kind(public), public)
    rows = max(int(prepared.values["limit"]), prepared.source_rows)
    series = len(prepared.raw_series)
    operations = rows * max(series, 1) ** 2
    return {"rows": rows, "series": series, "operations": operations}


__all__ = [
    "ArgumentParameter",
    "SearchArguments",
    "QueryArguments",
    "SingleSeriesArguments",
    "MultiSeriesArguments",
    "ResearchSeriesArguments",
    "input_schema",
    "parse_arguments",
    "public_arguments",
    "preflight_dimensions",
]
