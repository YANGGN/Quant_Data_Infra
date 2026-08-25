"""Immutable typed input contracts for the Stage 5 tool platform.

Public JSON remains a boundary representation.  This module is the single
source for the typed non-legacy input shapes: the dataclass field declarations
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


def _typed_field(contract: _InputField, **kwargs: Any) -> Any:
    """Attach one immutable input declaration to a dataclass field."""

    return dataclasses.field(metadata={"schema": contract}, **kwargs)


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
class Stage10AvailableTickerArgumentsV1(_ArgumentMapping):
    """List current Stage 10 tickers that have retrievable price rows."""

    INPUT_KIND: ClassVar[str] = "stage10_available_ticker_v1"

    limit: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=10_000,
        ),
        default=10_000,
    )



@dataclass(frozen=True, slots=True)
class Stage10MarketPriceArgumentsV1(_ArgumentMapping):
    """Read one ticker's canonical Stage 10 OHLC price series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_price_v1"

    ticker: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketReturnArgumentsV2(_ArgumentMapping):
    """Typed v2 contract for one Stage 10 close-to-close return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_return_v2"

    identifier: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    identifier_kind: str = _typed_field(
        _InputField(
            types=("string",), enum=("instrument_id", "provider_symbol")
        )
    )
    start_date: str = _typed_field(_InputField(types=("string",)))
    end_date: str = _typed_field(_InputField(types=("string",)))
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    method: str = _typed_field(
        _InputField(types=("string",), enum=("simple", "log"))
    )
    horizon: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=252)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketDescribeArgumentsV2(_ArgumentMapping):
    """Describe one exact Stage 10 market-return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_describe_v2"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketAlignArgumentsV2(_ArgumentMapping):
    """Align one to twenty compatible Stage 10 market-return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_align_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    join: str = _typed_field(
        _InputField(types=("string",), enum=("inner", "outer"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketCorrelationArgumentsV2(_ArgumentMapping):
    """Correlate exactly two compatible, complete Stage 10 return samples."""

    INPUT_KIND: ClassVar[str] = "stage10_market_correlation_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=2, form="series_array")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionArgumentsV2(_ArgumentMapping):
    """Fit contemporaneous OLS to compatible trailing Stage 10 returns."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(types=("string",), enum=("classical_homoskedastic",))
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRollingRegressionArgumentsV2(_ArgumentMapping):
    """Fit the same OLS kernel over fixed contiguous return windows."""

    INPUT_KIND: ClassVar[str] = "stage10_market_rolling_regression_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    window: int = _typed_field(
        _InputField(types=("integer",), minimum=3, maximum=5_000)
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(types=("string",), enum=("classical_homoskedastic",))
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )

@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionArgumentsV21(_ArgumentMapping):
    """Fit OLS with explicit robust inference and residual diagnostics."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_v2_1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "classical_homoskedastic",
                "hc1",
                "hc3",
                "newey_west_hac_bartlett",
            ),
        )
    )
    hac_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    diagnostic_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=18)
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRollingRegressionArgumentsV21(_ArgumentMapping):
    """Fit robust OLS and diagnostics over fixed contiguous windows."""

    INPUT_KIND: ClassVar[str] = "stage10_market_rolling_regression_v2_1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    window: int = _typed_field(
        _InputField(types=("integer",), minimum=3, maximum=5_000)
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "classical_homoskedastic",
                "hc1",
                "hc3",
                "newey_west_hac_bartlett",
            ),
        )
    )
    hac_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    diagnostic_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=18)
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )



@dataclass(frozen=True, slots=True)
class Stage10MarketStationarityArgumentsV2(_ArgumentMapping):
    """Run one fixed-lag constant-only ADF test over trailing returns."""

    INPUT_KIND: ClassVar[str] = "stage10_market_stationarity_v2"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketStationarityArgumentsV21(_ArgumentMapping):
    """Run fixed-lag ADF and level-KPSS over one trailing return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_stationarity_v2_1"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    adf_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    kpss_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketStructuralBreakArgumentsV2(_ArgumentMapping):
    """Run a Chow test at one caller-declared first post-break row."""

    INPUT_KIND: ClassVar[str] = "stage10_market_structural_breaks_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    break_index: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=4_999)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionModelSuiteArgumentsV3(_ArgumentMapping):
    """Run one explicit fixed-specification multivariate econometric model."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_model_suite_v3"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=5, form="series_array")
    )
    analysis: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "engle_granger_cointegration",
                "vector_autoregression",
                "granger_causality",
            ),
        )
    )
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    lag_order: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=4)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    source_index: int = _typed_field(
        _InputField(types=("integer",), minimum=-1, maximum=4)
    )
    target_index: int = _typed_field(
        _InputField(types=("integer",), minimum=-1, maximum=4)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
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
    Stage10AvailableTickerArgumentsV1,
    Stage10MarketPriceArgumentsV1,
    Stage10MarketReturnArgumentsV2,
    Stage10MarketDescribeArgumentsV2,
    Stage10MarketAlignArgumentsV2,
    Stage10MarketCorrelationArgumentsV2,
    Stage10MarketRegressionArgumentsV2,
    Stage10MarketRollingRegressionArgumentsV2,
    Stage10MarketRegressionArgumentsV21,
    Stage10MarketRollingRegressionArgumentsV21,
    Stage10MarketStationarityArgumentsV2,
    Stage10MarketStationarityArgumentsV21,
    Stage10MarketStructuralBreakArgumentsV2,
    Stage10MarketRegressionModelSuiteArgumentsV3,
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
    declared = _declared_fields(argument_type)
    expected = {field.name for field, _ in declared}
    required = {field.name for field, contract in declared if contract.required}
    actual = set(value)
    missing = required - actual
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

    if argument_type is Stage10AvailableTickerArgumentsV1:
        limit = _validated_limit(
            mapping.get("limit", 10_000),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"limit": limit}),
        )

    if argument_type is Stage10MarketPriceArgumentsV1:
        ticker = _validated_string(
            mapping["ticker"],
            _field_contract(argument_type, "ticker"),
            "/ticker",
        )
        if not ticker.strip():
            raise _validation_error(
                "/ticker", "min_length", "Ticker cannot be blank"
            )
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "ticker": ticker,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "limit": limit,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            ),
        )

    if argument_type is Stage10MarketReturnArgumentsV2:
        identifier = _validated_string(
            mapping["identifier"],
            _field_contract(argument_type, "identifier"),
            "/identifier",
        )
        if not identifier.strip():
            raise _validation_error(
                "/identifier", "min_length", "Identifier cannot be blank"
            )
        enum_values: dict[str, str] = {}
        for field_name in (
            "identifier_kind",
            "mode",
            "date_only_policy",
            "method",
        ):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        start_date = _optional_calendar_date(mapping["start_date"], "/start_date")
        end_date = _optional_calendar_date(mapping["end_date"], "/end_date")
        assert start_date is not None and end_date is not None
        if parse_date(end_date, pointer="/end_date") < parse_date(
            start_date, pointer="/start_date"
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        horizon = _validated_limit(
            mapping["horizon"],
            _field_contract(argument_type, "horizon"),
            "/horizon",
        )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "identifier": identifier,
                    "identifier_kind": enum_values["identifier_kind"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "method": enum_values["method"],
                    "horizon": horizon,
                    "limit": limit,
                }
            ),
        )

    if argument_type in {
        Stage10MarketDescribeArgumentsV2,
        Stage10MarketAlignArgumentsV2,
        Stage10MarketCorrelationArgumentsV2,
        Stage10MarketRegressionArgumentsV2,
        Stage10MarketRollingRegressionArgumentsV2,
        Stage10MarketRegressionArgumentsV21,
        Stage10MarketRollingRegressionArgumentsV21,
        Stage10MarketStationarityArgumentsV2,
        Stage10MarketStationarityArgumentsV21,
        Stage10MarketStructuralBreakArgumentsV2,
        Stage10MarketRegressionModelSuiteArgumentsV3,
    }:
        series_contract = _field_contract(argument_type, "series")
        raw_series = _validated_series_material(
            mapping["series"], series_contract, "/series"
        )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        values: dict[str, Any] = {"limit": limit}
        if argument_type is Stage10MarketAlignArgumentsV2:
            join_contract = _field_contract(argument_type, "join")
            join = _validated_string(mapping["join"], join_contract, "/join")
            if join not in join_contract.enum:
                raise _validation_error("/join", "enum", "Unsupported join policy")
            values["join"] = join
        if argument_type in {
            Stage10MarketRegressionArgumentsV2,
            Stage10MarketRollingRegressionArgumentsV2,
            Stage10MarketRegressionArgumentsV21,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            if not isinstance(mapping["intercept"], bool):
                raise _validation_error("/intercept", "type", "Expected a boolean")
            values["intercept"] = mapping["intercept"]
            for field_name in ("covariance", "confidence_level"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
        if argument_type in {
            Stage10MarketRegressionArgumentsV21,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            values["hac_lag"] = _validated_limit(
                mapping["hac_lag"],
                _field_contract(argument_type, "hac_lag"),
                "/hac_lag",
            )
            values["diagnostic_lag"] = _validated_limit(
                mapping["diagnostic_lag"],
                _field_contract(argument_type, "diagnostic_lag"),
                "/diagnostic_lag",
            )
            if (
                values["covariance"] != "newey_west_hac_bartlett"
                and values["hac_lag"] != 0
            ):
                raise _validation_error(
                    "/hac_lag",
                    "only_for_newey_west",
                    "hac_lag must be zero unless Newey-West HAC is selected",
                )
        if argument_type in {
            Stage10MarketRollingRegressionArgumentsV2,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            values["window"] = _validated_limit(
                mapping["window"],
                _field_contract(argument_type, "window"),
                "/window",
            )
        if argument_type is Stage10MarketRollingRegressionArgumentsV21:
            if values["hac_lag"] >= values["window"]:
                raise _validation_error(
                    "/hac_lag",
                    "range",
                    "hac_lag must be smaller than the rolling window",
                )
            minimum_diagnostic_window = max(
                8, 2 * int(values["diagnostic_lag"]) + 1
            )
            if values["window"] < minimum_diagnostic_window:
                raise _validation_error(
                    "/window",
                    "minimum",
                    "window is too short for the fixed diagnostic lag",
                )
        if argument_type in {
            Stage10MarketStationarityArgumentsV2,
            Stage10MarketStationarityArgumentsV21,
        }:
            for field_name in ("deterministic", "significance"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
            lag_fields = (
                ("lag",)
                if argument_type is Stage10MarketStationarityArgumentsV2
                else ("adf_lag", "kpss_lag")
            )
            for field_name in lag_fields:
                values[field_name] = _validated_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )
        if argument_type is Stage10MarketStructuralBreakArgumentsV2:
            if not isinstance(mapping["intercept"], bool):
                raise _validation_error(
                    "/intercept",
                    "type",
                    "Expected a boolean",
                )
            values["intercept"] = mapping["intercept"]
            values["break_index"] = _validated_limit(
                mapping["break_index"],
                _field_contract(argument_type, "break_index"),
                "/break_index",
            )
            significance_contract = _field_contract(
                argument_type,
                "significance",
            )
            significance = _validated_string(
                mapping["significance"],
                significance_contract,
                "/significance",
            )
            if significance not in significance_contract.enum:
                raise _validation_error(
                    "/significance",
                    "enum",
                    "Unsupported significance",
                )
            values["significance"] = significance
            if values["break_index"] >= limit:
                raise _validation_error(
                    "/break_index",
                    "range",
                    "break_index must be smaller than limit",
                )
        if argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
            for field_name in ("analysis", "deterministic", "significance"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
            values["lag_order"] = _validated_limit(
                mapping["lag_order"],
                _field_contract(argument_type, "lag_order"),
                "/lag_order",
            )
            for field_name in ("source_index", "target_index"):
                values[field_name] = _validated_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )

            analysis = values["analysis"]
            source_index = values["source_index"]
            target_index = values["target_index"]
            if analysis == "engle_granger_cointegration":
                if len(raw_series) != 2:
                    raise _validation_error(
                        "/series",
                        "item_count",
                        "Engle-Granger requires exactly two ordered series",
                    )
                if source_index != -1 or target_index != -1:
                    raise _validation_error(
                        "/source_index",
                        "only_for_granger_causality",
                        "Engle-Granger uses series[0] on series[1]; both indices must be -1",
                    )
                if limit < 21:
                    raise _validation_error(
                        "/limit",
                        "minimum",
                        "Engle-Granger requires an observation limit of at least 21",
                    )
            elif analysis == "vector_autoregression":
                if values["lag_order"] < 1:
                    raise _validation_error(
                        "/lag_order", "minimum", "VAR lag_order must be at least one"
                    )
                if source_index != -1 or target_index != -1:
                    raise _validation_error(
                        "/source_index",
                        "only_for_granger_causality",
                        "VAR does not select source or target indices; both must be -1",
                    )
            else:
                if values["lag_order"] < 1:
                    raise _validation_error(
                        "/lag_order",
                        "minimum",
                        "Granger-causality lag_order must be at least one",
                    )
                if source_index < 0 or target_index < 0:
                    raise _validation_error(
                        "/source_index",
                        "required_for_granger_causality",
                        "Granger causality requires source_index and target_index",
                    )
                if source_index == target_index:
                    raise _validation_error(
                        "/target_index",
                        "distinct",
                        "Granger source and target indices must be distinct",
                    )
                if source_index >= len(raw_series) or target_index >= len(raw_series):
                    raise _validation_error(
                        "/source_index",
                        "range",
                        "Granger source and target indices must identify supplied series",
                    )

        return _PreparedArguments(
            argument_type,
            MappingProxyType(values),
            raw_series=raw_series,
            source_rows=max(
                (_raw_observation_count(item) for item in raw_series),
                default=0,
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
    if prepared.argument_type is Stage10AvailableTickerArgumentsV1:
        return Stage10AvailableTickerArgumentsV1(**dict(values))
    if prepared.argument_type is Stage10MarketPriceArgumentsV1:
        return Stage10MarketPriceArgumentsV1(**dict(values))
    if prepared.argument_type is Stage10MarketReturnArgumentsV2:
        return Stage10MarketReturnArgumentsV2(**dict(values))

    decoded = tuple(
        _decode_one(
            raw,
            decode_series,
            f"/series/{index}"
            if prepared.argument_type
            in {
                Stage10MarketAlignArgumentsV2,
                Stage10MarketCorrelationArgumentsV2,
                Stage10MarketRegressionArgumentsV2,
                Stage10MarketRollingRegressionArgumentsV2,
                Stage10MarketRegressionArgumentsV21,
                Stage10MarketRollingRegressionArgumentsV21,
                Stage10MarketStructuralBreakArgumentsV2,
                Stage10MarketRegressionModelSuiteArgumentsV3,
                MultiSeriesArguments,
                ResearchSeriesArguments,
            }
            else "/series",
        )
        for index, raw in enumerate(prepared.raw_series)
    )
    if prepared.argument_type is Stage10MarketDescribeArgumentsV2:
        return Stage10MarketDescribeArgumentsV2(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketAlignArgumentsV2:
        return Stage10MarketAlignArgumentsV2(series=decoded, **dict(values))
    if prepared.argument_type is Stage10MarketCorrelationArgumentsV2:
        return Stage10MarketCorrelationArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRegressionArgumentsV2:
        return Stage10MarketRegressionArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRollingRegressionArgumentsV2:
        return Stage10MarketRollingRegressionArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRegressionArgumentsV21:
        return Stage10MarketRegressionArgumentsV21(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRollingRegressionArgumentsV21:
        return Stage10MarketRollingRegressionArgumentsV21(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV2:
        return Stage10MarketStationarityArgumentsV2(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV21:
        return Stage10MarketStationarityArgumentsV21(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketStructuralBreakArgumentsV2:
        return Stage10MarketStructuralBreakArgumentsV2(
            series=decoded,
            **dict(values),
        )
    if prepared.argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
        return Stage10MarketRegressionModelSuiteArgumentsV3(
            series=decoded,
            **dict(values),
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
    available_ticker_names = {
        item.name
        for item, _ in _declared_fields(Stage10AvailableTickerArgumentsV1)
    }
    market_price_names = {
        item.name for item, _ in _declared_fields(Stage10MarketPriceArgumentsV1)
    }
    market_return_names = {
        item.name for item, _ in _declared_fields(Stage10MarketReturnArgumentsV2)
    }
    market_describe_names = {
        item.name for item, _ in _declared_fields(Stage10MarketDescribeArgumentsV2)
    }
    market_align_names = {
        item.name for item, _ in _declared_fields(Stage10MarketAlignArgumentsV2)
    }
    market_correlation_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketCorrelationArgumentsV2)
    }
    market_regression_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRegressionArgumentsV2)
    }
    market_rolling_regression_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRollingRegressionArgumentsV2)
    }
    market_regression_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRegressionArgumentsV21)
    }
    market_rolling_regression_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRollingRegressionArgumentsV21)
    }
    market_stationarity_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketStationarityArgumentsV2)
    }
    market_stationarity_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketStationarityArgumentsV21)
    }
    market_structural_break_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10MarketStructuralBreakArgumentsV2
        )
    }
    market_model_suite_v3_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10MarketRegressionModelSuiteArgumentsV3
        )
    }

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
    if names <= available_ticker_names:
        return Stage10AvailableTickerArgumentsV1.INPUT_KIND
    if names == market_price_names:
        return Stage10MarketPriceArgumentsV1.INPUT_KIND
    if names == market_return_names:
        return Stage10MarketReturnArgumentsV2.INPUT_KIND
    if names == market_align_names:
        return Stage10MarketAlignArgumentsV2.INPUT_KIND
    if names == market_describe_names:
        raw_series = mapping.get("series")
        return (
            Stage10MarketCorrelationArgumentsV2.INPUT_KIND
            if isinstance(raw_series, (list, tuple))
            else Stage10MarketDescribeArgumentsV2.INPUT_KIND
        )
    if names == market_correlation_names:
        return Stage10MarketCorrelationArgumentsV2.INPUT_KIND
    if names == market_regression_names:
        return Stage10MarketRegressionArgumentsV2.INPUT_KIND
    if names == market_rolling_regression_names:
        return Stage10MarketRollingRegressionArgumentsV2.INPUT_KIND
    if names == market_stationarity_names:
        return Stage10MarketStationarityArgumentsV2.INPUT_KIND
    if names == market_stationarity_v21_names:
        return Stage10MarketStationarityArgumentsV21.INPUT_KIND
    if names == market_structural_break_names:
        return Stage10MarketStructuralBreakArgumentsV2.INPUT_KIND
    if names == market_model_suite_v3_names:
        return Stage10MarketRegressionModelSuiteArgumentsV3.INPUT_KIND
    if names == market_regression_v21_names:
        return Stage10MarketRegressionArgumentsV21.INPUT_KIND
    if names == market_rolling_regression_v21_names:
        return Stage10MarketRollingRegressionArgumentsV21.INPUT_KIND
    if names == search_names:
        return SearchArguments.INPUT_KIND
    raise _validation_error("/arguments", "shape", "Arguments do not match a registered input shape")


def preflight_dimensions(
    public: Mapping[str, Any], *, input_kind: str | None = None
) -> dict[str, int]:
    """Derive deterministic workload dimensions without decoding TimeSeries.

    Rows cover the larger of a bounded result limit and supplied observation
    material.  Multi-series work is conservatively quadratic in its series
    count, matching the platform's correlation/alignment workload ceiling.
    """

    prepared = _prepared(input_kind or _inferred_input_kind(public), public)
    rows = max(int(prepared.values["limit"]), prepared.source_rows)
    series = len(prepared.raw_series)
    operations = rows * max(series, 1) ** 2
    if prepared.argument_type in {
        Stage10MarketRollingRegressionArgumentsV2,
        Stage10MarketRollingRegressionArgumentsV21,
    }:
        operations *= int(prepared.values["window"])
    if prepared.argument_type in {
        Stage10MarketRegressionArgumentsV21,
        Stage10MarketRollingRegressionArgumentsV21,
    }:
        operations *= 1 + max(
            int(prepared.values["hac_lag"]),
            int(prepared.values["diagnostic_lag"]),
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV21:
        operations *= 1 + int(prepared.values["adf_lag"])
        operations += rows * int(prepared.values["kpss_lag"])
    if prepared.argument_type is Stage10MarketStructuralBreakArgumentsV2:
        operations *= 3
    if prepared.argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
        lag_order = int(prepared.values["lag_order"])
        if prepared.values["analysis"] == "engle_granger_cointegration":
            operations = rows * (lag_order + 2) ** 2
        else:
            parameter_count = 1 + series * lag_order
            operations = (
                rows * parameter_count**2
                + series * rows * parameter_count
                + series * parameter_count**2
            )
            if prepared.values["analysis"] == "granger_causality":
                restricted_count = parameter_count - lag_order
                operations += rows * restricted_count**2

    return {"rows": rows, "series": series, "operations": operations}


__all__ = [
    "ArgumentParameter",
    "SearchArguments",
    "QueryArguments",
    "Stage10AvailableTickerArgumentsV1",
    "Stage10MarketPriceArgumentsV1",
    "Stage10MarketReturnArgumentsV2",
    "Stage10MarketDescribeArgumentsV2",
    "Stage10MarketAlignArgumentsV2",
    "Stage10MarketCorrelationArgumentsV2",
    "Stage10MarketRegressionArgumentsV2",
    "Stage10MarketRollingRegressionArgumentsV2",
    "Stage10MarketStationarityArgumentsV2",
    "Stage10MarketStationarityArgumentsV21",
    "Stage10MarketStructuralBreakArgumentsV2",
    "Stage10MarketRegressionModelSuiteArgumentsV3",
    "SingleSeriesArguments",
    "Stage10MarketRegressionArgumentsV21",
    "Stage10MarketRollingRegressionArgumentsV21",
    "MultiSeriesArguments",
    "ResearchSeriesArguments",
    "input_schema",
    "parse_arguments",
    "public_arguments",
    "preflight_dimensions",
]
