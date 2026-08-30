"""Typed store-free adapter for Stage 10 technical indicators."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from quant_data.contracts import (
    LineageRef,
    Observation,
    TimeSeries,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.stage10_series import (
    CURRENCY_SEGMENT,
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
    PRICE_VARIANT,
    PROVIDER,
)
from quant_data.temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)

from .arguments import (
    Stage10TechnicalIndicatorArgumentsV2,
    Stage10TechnicalIndicatorArgumentsV21,
    Stage10TechnicalIndicatorArgumentsV22,
    Stage10TechnicalIndicatorArgumentsV23,
    Stage10TechnicalIndicatorArgumentsV24,
    Stage10TechnicalIndicatorArgumentsV25,
    Stage10TechnicalIndicatorArgumentsV26,
    Stage10TechnicalIndicatorArgumentsV27,
)
from .context import ToolExecutionContext
from .market_prices import stage10_market_price_series_schema
from .market_volume import stage10_market_volume_series_schema
from .results import DiagnosticV1, QueryResult, RecordV1, fields_from_mapping
from .technical_indicators import (
    MAX_TECHNICAL_INDICATOR_OBSERVATIONS,
    TECHNICAL_INDICATORS,
    TECHNICAL_INDICATORS_V2,
    TECHNICAL_INDICATORS_V21,
    TECHNICAL_INDICATORS_V22,
    TECHNICAL_INDICATORS_V23,
    TECHNICAL_INDICATORS_V24,
    TECHNICAL_INDICATORS_V25,
    TECHNICAL_INDICATORS_V26,
    TECHNICAL_INDICATORS_V27,
    TechnicalIndicatorComponent,
    calculate_technical_indicator,
)


TOOL_NAME = "market.technical_indicators"
OPERATION_VERSION = "2.0.0"
OPERATION_VERSION_V21 = "2.1.0"
OPERATION_VERSION_V22 = "2.2.0"
OPERATION_VERSION_V23 = "2.3.0"
OPERATION_VERSION_V24 = "2.4.0"
OPERATION_VERSION_V25 = "2.5.0"
OPERATION_VERSION_V26 = "2.6.0"
OPERATION_VERSION_V27 = "2.7.0"
TRANSFORMATION_ID = "technical_indicator_v2"
TRANSFORMATION_ID_V21 = "technical_indicator_v2_1"
TRANSFORMATION_ID_V22 = "technical_indicator_v2_2"
TRANSFORMATION_ID_V23 = "technical_indicator_v2_3"
TRANSFORMATION_ID_V24 = "technical_indicator_v2_4"
TRANSFORMATION_ID_V25 = "technical_indicator_v2_5"
TRANSFORMATION_ID_V26 = "technical_indicator_v2_6"
TRANSFORMATION_ID_V27 = "technical_indicator_v2_7"
MAX_OUTPUT_VALUES = 200_000
_STAGE10_MIGRATION_ID = "market:0010_stage10_market_history"
_HEX = frozenset("0123456789abcdef")
_ROLE_ORDER = ("open", "high", "low", "close", "volume")
_PRICE_ROLES = frozenset(("open", "high", "low", "close"))
_HIGH_LOW_INDICATORS = frozenset(
    (
        "true_range",
        "average_true_range",
        "donchian_channels",
        "stochastic_oscillator",
        "kdj",
        "average_directional_index",
        "accumulation_distribution",
        "supertrend_ai",
        "swing_structure_forecast",
        "wavetrend_crosses",
        "parabolic_sar",
    )
)
_LOW_ONLY_INDICATORS = frozenset(("williams_vix_fix",))
_VOLUME_INDICATORS = frozenset(
    ("on_balance_volume", "accumulation_distribution")
)
_THREE_COMPONENT_INDICATORS = frozenset(
    (
        "macd",
        "bollinger_bands",
        "donchian_channels",
        "average_directional_index",
        "kdj",
    )
)
_FOUR_COMPONENT_INDICATORS = frozenset(
    ("williams_vix_fix", "wavetrend_crosses")
)
_FIVE_COMPONENT_INDICATORS = frozenset(("supertrend_ai",))
_TEN_COMPONENT_INDICATORS = frozenset(("swing_structure_forecast",))
_PERCENT_COMPONENTS = frozenset(
    (
        "rate_of_change",
        "relative_strength_index",
        "percent_k",
        "percent_d",
        "percent_j",
        "williams_vix_fix",
        "upper_band",
        "range_high",
        "range_low",
        "plus_di",
        "minus_di",
        "adx",
        "forecast_percent",
        "forecast_standard_deviation",
    )
)
_VOLUME_COMPONENTS = frozenset(
    ("on_balance_volume", "accumulation_distribution")
)
_DIMENSIONLESS_COMPONENTS = frozenset(
    (
        "trend",
        "performance_index",
        "target_factor",
        "swing_direction",
        "wavetrend",
        "wavetrend_signal",
        "wavetrend_difference",
        "wavetrend_cross_signal",
    )
)
_BAR_COUNT_COMPONENTS = frozenset(
    ("forecast_duration_bars", "forecast_origin_age_bars")
)


def _strict(
    properties: Mapping[str, dict[str, Any]],
    required: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties if required is None else required),
        "properties": dict(properties),
    }


def _text() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _nullable_text() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _strings(max_items: int = 100) -> dict[str, Any]:
    return {"type": "array", "maxItems": max_items, "items": _text()}


def _types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    raw = schema.get("type")
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    raise AssertionError("Invalid Stage 10 schema type")


def _merge_schema(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a strict structural union for the local schema subset."""

    types = tuple(dict.fromkeys((*_types(left), *_types(right))))
    merged: dict[str, Any] = {
        "type": types[0] if len(types) == 1 else list(types)
    }
    choices: list[Any] = []
    for source in (left, right):
        if "const" in source:
            choices.append(source["const"])
        if "enum" in source:
            choices.extend(source["enum"])
    if choices:
        unique = list(dict.fromkeys(choices))
        if len(unique) == 1:
            merged["const"] = unique[0]
        else:
            merged["enum"] = unique
    if "object" in types:
        left_properties = left.get("properties", {})
        right_properties = right.get("properties", {})
        names = sorted(set(left_properties) | set(right_properties))
        merged["properties"] = {
            name: (
                _merge_schema(left_properties[name], right_properties[name])
                if name in left_properties and name in right_properties
                else copy.deepcopy(
                    left_properties.get(name, right_properties.get(name))
                )
            )
            for name in names
        }
        merged["required"] = sorted(
            set(left.get("required", ())) & set(right.get("required", ()))
        )
        if (
            left.get("additionalProperties") is False
            and right.get("additionalProperties") is False
        ):
            merged["additionalProperties"] = False
    if "array" in types:
        left_items = left.get("items")
        right_items = right.get("items")
        if isinstance(left_items, Mapping) and isinstance(right_items, Mapping):
            merged["items"] = _merge_schema(left_items, right_items)
        elif isinstance(left_items, Mapping):
            merged["items"] = copy.deepcopy(left_items)
        elif isinstance(right_items, Mapping):
            merged["items"] = copy.deepcopy(right_items)
        maximums = tuple(
            item
            for item in (left.get("maxItems"), right.get("maxItems"))
            if isinstance(item, int)
        )
        minimums = tuple(
            item
            for item in (left.get("minItems"), right.get("minItems"))
            if isinstance(item, int)
        )
        if maximums:
            merged["maxItems"] = max(maximums)
        if minimums:
            merged["minItems"] = min(minimums)
    if left.get("format") == right.get("format") and "format" in left:
        merged["format"] = left["format"]
    for name, chooser in (
        ("minLength", min),
        ("maxLength", max),
        ("minimum", min),
        ("maximum", max),
    ):
        values = tuple(
            item
            for item in (left.get(name), right.get(name))
            if isinstance(item, (int, Decimal)) and not isinstance(item, bool)
        )
        if values:
            merged[name] = chooser(values)
    return merged


def stage10_technical_indicator_input_series_schema() -> dict[str, Any]:
    """Return the OHLCV structural union accepted by the v2 adapter."""

    return _merge_schema(
        stage10_market_price_series_schema(),
        stage10_market_volume_series_schema(),
    )


def stage10_technical_indicator_output_series_schema(
    *,
    indicators: Sequence[str] = TECHNICAL_INDICATORS_V2,
    transformation_id: str = TRANSFORMATION_ID,
    operation_version: str = OPERATION_VERSION,
    parameter_max_items: int = 5,
    parameter_value_types: Sequence[str] = ("integer", "number"),
    representations: Sequence[str] = (
        "price",
        "volume",
        "percentage",
        "z_score",
    ),
) -> dict[str, Any]:
    """Return the exact derived scalar TimeSeries schema."""

    parameter = _strict(
        {"name": _text(), "value": {"type": list(parameter_value_types)}}
    )
    unit = {
        "type": "string",
        "enum": [
            "provider_native_currency",
            "provider_native_volume",
            "percent",
            "dimensionless",
        ],
    }
    representation = {
        "type": "string",
        "enum": list(representations),
    }
    scale = {"type": "string", "enum": ["1", "100"]}
    dimensions = _strict(
        {
            "instrument_id": _text(),
            "provider": {"type": "string", "const": PROVIDER},
            "transformation": {
                "type": "string",
                "const": transformation_id,
            },
            "indicator": {
                "type": "string",
                "enum": list(indicators),
            },
            "component": _text(),
        }
    )
    observation = _strict(
        {
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "value": {"type": ["number", "null"]},
            "missing_reason": {"type": ["string", "null"]},
            "unit": unit,
            "value_representation": representation,
            "scale": scale,
            "vintage_at": _text(),
            "available_at": _text(),
            "available_precision": {"type": "string", "const": "datetime"},
            "captured_at": _text(),
            "captured_precision": {"type": "string", "const": "datetime"},
            "version_id": _text(),
            "evidence_id": _text(),
            "snapshot_id": _text(),
            "run_id": _text(),
            "dimensions": dimensions,
            "quality_flags": _strings(),
        }
    )
    metadata = _strict(
        {
            "instrument_id": _text(),
            "provider_symbol": _text(),
            "asset_type": _text(),
            "display_name": _nullable_text(),
            "exchange_code": _nullable_text(),
            "provider": {"type": "string", "const": PROVIDER},
            "frequency": {"type": "string", "const": "daily"},
            "unit": unit,
            "value_representation": representation,
            "scale": scale,
            "indicator": {
                "type": "string",
                "enum": list(indicators),
            },
            "component": _text(),
            "parameters": {
                "type": "array",
                "maxItems": parameter_max_items,
                "items": parameter,
            },
            "source_observation_fields": _strings(5),
            "source_price_variant": {
                "type": "string",
                "const": PRICE_VARIANT,
            },
            "availability_basis": {
                "type": "string",
                "const": "derived_from_local_capture",
            },
            "derived_availability_rule": {
                "type": "string",
                "const": "cumulative_latest_exact_source_time",
            },
            "session_calendar_status": {
                "type": "string",
                "const": "not_established",
            },
            "adjustment_status": {
                "type": "string",
                "const": "not_established",
            },
            "volume_unit_status": {
                "type": "string",
                "const": "provider_native_not_normalized",
            },
            "transformation": {
                "type": "string",
                "const": transformation_id,
            },
            "transformation_version": {
                "type": "string",
                "const": operation_version,
            },
        }
    )
    audit = _strict(
        {
            "mode": {"type": "string", "enum": ["latest", "as_of"]},
            "requested_mode": {
                "type": "string",
                "enum": ["latest", "as_of"],
            },
            "actual_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "cutoff": _nullable_text(),
            "cutoff_precision": _nullable_text(),
            "date_only_policy": {
                "type": "string",
                "enum": ["completed_date", "calendar_date_inclusive"],
            },
            "availability_basis": {
                "type": "string",
                "const": "derived_from_local_capture",
            },
            "period_range_rule": {"type": "string", "const": "trade_date"},
            "requested_start_date": _nullable_text(),
            "requested_end_date": _nullable_text(),
            "indicator": {
                "type": "string",
                "enum": list(indicators),
            },
            "component": _text(),
            "parameters": {
                "type": "array",
                "maxItems": parameter_max_items,
                "items": parameter,
            },
            "source_series_ids": _strings(5),
            "source_lineage_digests": _strings(5),
            "source_observation_fields": _strings(5),
            "input_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
            },
            "selected_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
            },
            "missing_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10000,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
            },
            "point_in_time_status": {
                "type": "string",
                "enum": ["safe", "not_applicable"],
            },
            "point_in_time_scope": {
                "type": "string",
                "enum": [
                    "retained_local_captures",
                    "current_stored_knowledge",
                ],
            },
            "unsafe_reasons": _strings(),
            "derived_availability_rule": {
                "type": "string",
                "const": "cumulative_latest_exact_source_time",
            },
            "truncated": {"type": "boolean", "const": False},
        }
    )
    provenance = _strict(
        {
            "transformation_id": {
                "type": "string",
                "const": transformation_id,
            },
            "operation_version": {
                "type": "string",
                "const": operation_version,
            },
            "source_dataset_ids": _strings(3),
            "source_series_ids": _strings(5),
            "source_lineage_digests": _strings(5),
            "input_selection_sha256": _text(),
            "store_role": {"type": "string", "const": "derived"},
        }
    )
    return _strict(
        {
            "contract": {
                "type": "string",
                "const": "quant_data.timeseries",
            },
            "contract_version": {"type": "string", "const": "1.0.0"},
            "series_id": _text(),
            "metadata": metadata,
            "observations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_TECHNICAL_INDICATOR_OBSERVATIONS,
                "items": observation,
            },
            "warnings": _strings(),
            "audit": audit,
            "provenance": provenance,
            "truncated": {"type": "boolean", "const": False},
            "lineage_digest": _text(),
        }
    )


def stage10_technical_indicator_output_series_schema_v21() -> dict[str, Any]:
    """Return the v2.1 derived-series schema including SuperTrend AI."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V21,
        transformation_id=TRANSFORMATION_ID_V21,
        operation_version=OPERATION_VERSION_V21,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
        ),
    )


def stage10_technical_indicator_output_series_schema_v22() -> dict[str, Any]:
    """Return the v2.2 schema including the causal swing forecast."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V22,
        transformation_id=TRANSFORMATION_ID_V22,
        operation_version=OPERATION_VERSION_V22,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )


def stage10_technical_indicator_output_series_schema_v23() -> dict[str, Any]:
    """Return the v2.3 schema including KDJ."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V23,
        transformation_id=TRANSFORMATION_ID_V23,
        operation_version=OPERATION_VERSION_V23,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )


def stage10_technical_indicator_output_series_schema_v24() -> dict[str, Any]:
    """Return the v2.4 schema including Williams Vix Fix."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V24,
        transformation_id=TRANSFORMATION_ID_V24,
        operation_version=OPERATION_VERSION_V24,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )


def stage10_technical_indicator_output_series_schema_v25() -> dict[str, Any]:
    """Return the v2.5 schema including WaveTrend crosses."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V25,
        transformation_id=TRANSFORMATION_ID_V25,
        operation_version=OPERATION_VERSION_V25,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )



def stage10_technical_indicator_output_series_schema_v26() -> dict[str, Any]:
    """Return the v2.6 schema including Parabolic SAR."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V26,
        transformation_id=TRANSFORMATION_ID_V26,
        operation_version=OPERATION_VERSION_V26,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )


def stage10_technical_indicator_output_series_schema_v27() -> dict[str, Any]:
    """Return the v2.7 schema including a rolling regression line."""

    return stage10_technical_indicator_output_series_schema(
        indicators=TECHNICAL_INDICATORS_V27,
        transformation_id=TRANSFORMATION_ID_V27,
        operation_version=OPERATION_VERSION_V27,
        parameter_max_items=6,
        parameter_value_types=("integer", "number", "string"),
        representations=(
            "price",
            "volume",
            "percentage",
            "z_score",
            "regime",
            "ratio",
            "factor",
            "bar_count",
        ),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _example_series() -> TimeSeries:
    symbol = "AAPL"
    instrument_id = "example_stage10_aapl"
    dates = (
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
        "2026-08-14",
    )
    values = tuple(
        Decimal(value) for value in ("100", "101", "102", "101", "103")
    )
    captured_at = "2026-08-15T01:00:00Z"
    identity = {
        "instrument_id": instrument_id,
        "provider_symbol": symbol,
        "asset_type": "equity",
        "display_name": "AAPL example",
        "exchange_code": "XNAS",
        "currency_segment": CURRENCY_SEGMENT,
        "identity_seed_sha256": _digest({"instrument_id": instrument_id}),
        "captured_at": captured_at,
        "captured_precision": "datetime",
        "run_id": "example-stage10-run",
    }
    observations = tuple(
        Observation(
            period_start=day,
            period_end=day,
            value=values[index],
            missing_reason=None,
            unit="provider_native_currency",
            value_representation="price",
            scale="1",
            vintage_at=captured_at,
            available_at=captured_at,
            available_precision="datetime",
            captured_at=captured_at,
            captured_precision="datetime",
            version_id=f"example-price-version-{index}",
            evidence_id=f"example-price-evidence-{index}",
            snapshot_id=f"example-price-snapshot-{index}",
            run_id=f"example-price-run-{index}",
            dimensions={
                "instrument_id": instrument_id,
                "provider": PROVIDER,
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": "close",
            },
            quality_flags=(
                "availability_basis:local_capture",
                "adjustment_semantics:not_established",
                "session_calendar:not_established",
            ),
        )
        for index, day in enumerate(dates)
    )
    return TimeSeries(
        series_id="example:stage10_price:aapl:close",
        metadata={
            "instrument_id": instrument_id,
            "provider_symbol": symbol,
            "asset_type": "equity",
            "display_name": "AAPL example",
            "exchange_code": "XNAS",
            "provider": PROVIDER,
            "frequency": "daily",
            "unit": "provider_native_currency",
            "value_representation": "price",
            "scale": "1",
            "price_variant": PRICE_VARIANT,
            "currency_segment": CURRENCY_SEGMENT,
            "observation_field": "close",
            "availability_basis": "local_capture",
            "horizon_basis": "observed_rows",
            "session_calendar_status": "not_established",
            "adjustment_status": "not_established",
        },
        observations=observations,
        warnings=(
            "adjustment_and_total_return_semantics_not_established",
            "local_capture_availability",
            "session_calendar_not_established",
        ),
        audit={
            "mode": "latest",
            "requested_mode": "latest",
            "actual_mode": "latest",
            "cutoff": None,
            "cutoff_precision": None,
            "date_only_policy": "completed_date",
            "availability_basis": "local_capture",
            "period_range_rule": "trade_date",
            "requested_start_date": dates[0],
            "requested_end_date": dates[-1],
            "provider": PROVIDER,
            "price_variant": PRICE_VARIANT,
            "currency_segment": CURRENCY_SEGMENT,
            "observation_field": "close",
            "horizon_basis": "observed_rows",
            "session_calendar_status": "not_established",
            "point_in_time_status": "not_applicable",
            "point_in_time_scope": "current_stored_knowledge",
            "unsafe_reasons": [],
            "limit": len(dates),
            "selected_count": len(dates),
            "missing_count": 0,
            "truncated": False,
        },
        provenance={
            "dataset_id": DATASET_ID,
            "evidence_dataset_id": EVIDENCE_DATASET_ID,
            "identity_dataset_id": IDENTITY_DATASET_ID,
            "store_role": "market",
            "registry_revision": "2.48.0",
            "store_receipt": {
                "migration_ids": [_STAGE10_MIGRATION_ID],
                "selected_instrument_identity": identity,
                "sha256": _digest(
                    {
                        "series_id": "example:stage10_price:aapl:close",
                        "selected_instrument_identity": identity,
                    }
                ),
            },
        },
        truncated=False,
    )


def stage10_technical_indicator_example() -> dict[str, Any]:
    """Return a schema-valid close-series example with JSON scalar values."""

    payload = _example_series().to_primitive()
    for observation in payload["observations"]:
        value = observation["value"]
        if isinstance(value, Decimal):
            observation["value"] = int(value)
    return payload


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(
            f"Technical-indicator input {field} is invalid"
        )
    return value


def _sha256(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    if len(text) != 64 or any(character not in _HEX for character in text):
        raise ValidationError(
            f"Technical-indicator input {field} is invalid"
        )
    return text


def _require_values(
    source: Mapping[str, Any],
    expected: Mapping[str, Any],
    section: str,
) -> None:
    for name, value in expected.items():
        if source.get(name) != value:
            raise ValidationError(
                f"Technical-indicator input {section} contract is invalid"
            )


def _validate_temporal(
    series: TimeSeries,
) -> tuple[DateOnlyPolicy, TemporalValue | None]:
    audit = series.audit
    mode = audit.get("mode")
    if (
        mode not in {"latest", "as_of"}
        or audit.get("requested_mode") != mode
        or audit.get("actual_mode") != mode
    ):
        raise ValidationError(
            "Technical-indicator input query mode is contradictory"
        )
    try:
        policy = DateOnlyPolicy(audit.get("date_only_policy"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Technical-indicator input date-only policy is invalid"
        ) from exc
    cutoff_raw = audit.get("cutoff")
    cutoff_precision = audit.get("cutoff_precision")
    if mode == "latest":
        if (
            cutoff_raw is not None
            or cutoff_precision is not None
            or audit.get("point_in_time_status") != "not_applicable"
            or audit.get("point_in_time_scope")
            != "current_stored_knowledge"
        ):
            raise ValidationError(
                "Technical-indicator latest input is contradictory"
            )
        return policy, None
    if not isinstance(cutoff_raw, str):
        raise ValidationError(
            "Technical-indicator as-of input lacks a cutoff"
        )
    cutoff = TemporalValue.parse(
        cutoff_raw, pointer="/series/audit/cutoff"
    )
    if (
        cutoff_precision != cutoff.precision.value
        or audit.get("point_in_time_status") != "safe"
        or audit.get("point_in_time_scope")
        != "retained_local_captures"
    ):
        raise ValidationError(
            "Technical-indicator as-of input is contradictory"
        )
    return policy, cutoff


def _validate_receipt(
    series: TimeSeries,
    policy: DateOnlyPolicy,
    cutoff: TemporalValue | None,
) -> tuple[tuple[str, ...], Mapping[str, Any]]:
    provenance = series.provenance
    _require_values(
        provenance,
        {
            "dataset_id": DATASET_ID,
            "evidence_dataset_id": EVIDENCE_DATASET_ID,
            "identity_dataset_id": IDENTITY_DATASET_ID,
            "store_role": "market",
        },
        "provenance",
    )
    _nonempty(provenance.get("registry_revision"), "registry revision")
    receipt = provenance.get("store_receipt")
    if not isinstance(receipt, Mapping):
        raise ValidationError(
            "Technical-indicator input store receipt is invalid"
        )
    migration_ids = receipt.get("migration_ids")
    if (
        isinstance(migration_ids, (str, bytes))
        or not isinstance(migration_ids, (list, tuple))
        or _STAGE10_MIGRATION_ID not in migration_ids
        or any(not isinstance(item, str) or not item for item in migration_ids)
    ):
        raise ValidationError(
            "Technical-indicator input migration receipt is invalid"
        )
    _sha256(receipt.get("sha256"), "store receipt digest")
    identity = receipt.get("selected_instrument_identity")
    if not isinstance(identity, Mapping):
        raise ValidationError(
            "Technical-indicator input identity receipt is invalid"
        )
    for field in (
        "instrument_id",
        "provider_symbol",
        "asset_type",
        "display_name",
        "exchange_code",
    ):
        if identity.get(field) != series.metadata.get(field):
            raise ValidationError(
                "Technical-indicator input identity is inconsistent"
            )
    if identity.get("currency_segment") != CURRENCY_SEGMENT:
        raise ValidationError(
            "Technical-indicator input identity is inconsistent"
        )
    _sha256(identity.get("identity_seed_sha256"), "identity seed digest")
    _nonempty(identity.get("run_id"), "identity run")
    if identity.get("captured_precision") != "datetime":
        raise ValidationError(
            "Technical-indicator input identity timing is invalid"
        )
    captured = TemporalValue.parse(
        _nonempty(identity.get("captured_at"), "identity capture"),
        pointer=(
            "/series/provenance/store_receipt/"
            "selected_instrument_identity/captured_at"
        ),
    )
    if captured.precision is not TemporalPrecision.DATETIME:
        raise ValidationError(
            "Technical-indicator input identity timing is invalid"
        )
    if cutoff is not None and not availability_at_or_before(
        captured, cutoff, policy
    ).included:
        raise ValidationError(
            "Technical-indicator input identity is unavailable at the cutoff"
        )
    return tuple(migration_ids), identity


def _validate_series(series: TimeSeries, role: str) -> None:
    series.validate_lineage()
    if series.truncated:
        raise ValidationError(
            "Technical indicators reject truncated OHLCV inputs"
        )
    if not series.observations:
        raise ValidationError(
            "Technical indicators require at least one observed bar"
        )
    if len(series.observations) > MAX_TECHNICAL_INDICATOR_OBSERVATIONS:
        raise ResourceLimitError(
            "Technical-indicator input exceeds the observation limit"
        )
    metadata = series.metadata
    audit = series.audit
    if role in _PRICE_ROLES:
        _require_values(
            metadata,
            {
                "provider": PROVIDER,
                "frequency": "daily",
                "unit": "provider_native_currency",
                "value_representation": "price",
                "scale": "1",
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": role,
                "availability_basis": "local_capture",
                "horizon_basis": "observed_rows",
                "session_calendar_status": "not_established",
                "adjustment_status": "not_established",
            },
            "price metadata",
        )
        _require_values(
            audit,
            {
                "availability_basis": "local_capture",
                "period_range_rule": "trade_date",
                "provider": PROVIDER,
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": role,
                "horizon_basis": "observed_rows",
                "session_calendar_status": "not_established",
            },
            "price audit",
        )
        expected_dimensions = {
            "instrument_id": metadata.get("instrument_id"),
            "provider": PROVIDER,
            "price_variant": PRICE_VARIANT,
            "currency_segment": CURRENCY_SEGMENT,
            "observation_field": role,
        }
        unit = "provider_native_currency"
        representation = "price"
    else:
        _require_values(
            metadata,
            {
                "provider": PROVIDER,
                "frequency": "daily",
                "unit": "provider_native_volume",
                "value_representation": "volume",
                "scale": "1",
                "data_variant": PRICE_VARIANT,
                "observation_field": "volume",
                "availability_basis": "local_capture",
                "volume_unit_status": "provider_native_not_normalized",
                "volume_adjustment_status": "not_established",
                "session_calendar_status": "not_established",
            },
            "volume metadata",
        )
        _require_values(
            audit,
            {
                "availability_basis": "local_capture",
                "period_range_rule": "trade_date",
                "provider": PROVIDER,
                "data_variant": PRICE_VARIANT,
                "observation_field": "volume",
                "volume_unit_status": "provider_native_not_normalized",
                "volume_adjustment_status": "not_established",
                "session_calendar_status": "not_established",
            },
            "volume audit",
        )
        expected_dimensions = {
            "instrument_id": metadata.get("instrument_id"),
            "provider": PROVIDER,
            "data_variant": PRICE_VARIANT,
            "observation_field": "volume",
        }
        unit = "provider_native_volume"
        representation = "volume"
    for field in ("instrument_id", "provider_symbol", "asset_type"):
        _nonempty(metadata.get(field), f"metadata {field}")
    unsafe = audit.get("unsafe_reasons")
    if not isinstance(unsafe, (list, tuple)) or unsafe:
        raise ValidationError(
            "Technical-indicator input unsafe reasons are invalid"
        )
    limit = audit.get("limit")
    count = audit.get("selected_count")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 10_000
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(series.observations)
        or audit.get("missing_count") != 0
        or audit.get("truncated") is not False
    ):
        raise ValidationError(
            "Technical-indicator input selection counts are invalid"
        )
    start_raw = audit.get("requested_start_date")
    end_raw = audit.get("requested_end_date")
    start = (
        None
        if start_raw is None
        else parse_date(start_raw, pointer="/series/audit/requested_start_date")
    )
    end = (
        None
        if end_raw is None
        else parse_date(end_raw, pointer="/series/audit/requested_end_date")
    )
    if start is not None and end is not None and end < start:
        raise ValidationError(
            "Technical-indicator input date range is invalid"
        )
    policy, cutoff = _validate_temporal(series)
    _validate_receipt(series, policy, cutoff)
    previous = None
    for index, observation in enumerate(series.observations):
        pointer = f"/series/observations/{index}"
        period_start = parse_date(
            observation.period_start, pointer=f"{pointer}/period_start"
        )
        period_end = parse_date(
            observation.period_end, pointer=f"{pointer}/period_end"
        )
        if (
            period_start != period_end
            or (previous is not None and period_start <= previous)
            or (start is not None and period_start < start)
            or (end is not None and period_end > end)
        ):
            raise ValidationError(
                "Technical-indicator input observation grid is invalid"
            )
        previous = period_start
        if (
            not isinstance(observation.value, Decimal)
            or not observation.value.is_finite()
            or observation.missing_reason is not None
            or observation.unit != unit
            or observation.value_representation != representation
            or observation.scale != "1"
            or dict(observation.dimensions) != expected_dimensions
        ):
            raise ValidationError(
                "Technical-indicator input observation contract is invalid"
            )
        if role == "volume" and observation.value < 0:
            raise ValidationError(
                "Technical-indicator input volume must be nonnegative"
            )
        for field, value in (
            ("vintage identity", observation.vintage_at),
            ("version identity", observation.version_id),
            ("evidence identity", observation.evidence_id),
            ("snapshot identity", observation.snapshot_id),
            ("run identity", observation.run_id),
        ):
            _nonempty(value, field)
        if (
            observation.available_precision != "datetime"
            or observation.captured_precision != "datetime"
            or not isinstance(observation.available_at, str)
            or observation.available_at != observation.captured_at
            or observation.vintage_at != observation.captured_at
        ):
            raise ValidationError(
                "Technical-indicator input observation timing is invalid"
            )
        available = TemporalValue.parse(
            observation.available_at, pointer=f"{pointer}/available_at"
        )
        if available.precision is not TemporalPrecision.DATETIME:
            raise ValidationError(
                "Technical-indicator input observation timing is invalid"
            )
        if cutoff is not None and not availability_at_or_before(
            available, cutoff, policy
        ).included:
            raise ValidationError(
                "Technical-indicator input is unavailable at the cutoff"
            )
        required_flags = {
            "availability_basis:local_capture",
            "session_calendar:not_established",
        }
        if not required_flags.issubset(set(observation.quality_flags)):
            raise ValidationError(
                "Technical-indicator input quality flags are invalid"
            )


def _selection_contract(series: TimeSeries) -> tuple[Any, ...]:
    audit = series.audit
    receipt = series.provenance["store_receipt"]
    return (
        series.metadata.get("instrument_id"),
        series.metadata.get("provider_symbol"),
        series.metadata.get("asset_type"),
        series.metadata.get("display_name"),
        series.metadata.get("exchange_code"),
        series.provenance.get("registry_revision"),
        audit.get("mode"),
        audit.get("requested_mode"),
        audit.get("actual_mode"),
        audit.get("cutoff"),
        audit.get("cutoff_precision"),
        audit.get("date_only_policy"),
        audit.get("requested_start_date"),
        audit.get("requested_end_date"),
        audit.get("point_in_time_status"),
        audit.get("point_in_time_scope"),
        tuple(receipt["migration_ids"]),
        tuple(sorted(dict(receipt["selected_instrument_identity"]).items())),
    )


def _validate_inputs(
    values: Sequence[TimeSeries],
    indicator: str,
    limit: int,
) -> tuple[tuple[str, TimeSeries], ...]:
    if isinstance(values, (str, bytes)) or not 1 <= len(values) <= 5:
        raise ValidationError(
            "Technical indicators require one through five typed series"
        )
    if not all(isinstance(item, TimeSeries) for item in values):
        raise ValidationError(
            "Technical indicators require typed TimeSeries inputs"
        )
    by_role: dict[str, TimeSeries] = {}
    for series in values:
        role = series.metadata.get("observation_field")
        if role not in _ROLE_ORDER:
            raise ValidationError(
                "Technical-indicator input field is unsupported"
            )
        role = str(role)
        if role in by_role:
            raise ValidationError(
                "Technical-indicator input fields must be unique"
            )
        by_role[role] = series
    if "close" not in by_role:
        raise ValidationError(
            "Technical indicators require the close series"
        )
    if (
        indicator in _HIGH_LOW_INDICATORS
        and not {"high", "low"}.issubset(by_role)
    ):
        raise ValidationError(
            f"{indicator} requires high, low, and close series"
        )
    if indicator in _LOW_ONLY_INDICATORS and "low" not in by_role:
        raise ValidationError(
            f"{indicator} requires low and close series"
        )
    if indicator in _VOLUME_INDICATORS and "volume" not in by_role:
        raise ValidationError(f"{indicator} requires a volume series")
    ordered = tuple(
        (role, by_role[role]) for role in _ROLE_ORDER if role in by_role
    )
    for role, series in ordered:
        _validate_series(series, role)
        if len(series.observations) > limit:
            raise ResourceLimitError(
                "Technical-indicator input exceeds its declared limit"
            )
    baseline = ordered[0][1]
    grid = tuple(
        (item.period_start, item.period_end) for item in baseline.observations
    )
    row_identities = tuple(
        (
            item.version_id,
            item.evidence_id,
            item.snapshot_id,
            item.run_id,
        )
        for item in baseline.observations
    )
    for _, candidate in ordered[1:]:
        if (
            _selection_contract(candidate) != _selection_contract(baseline)
            or tuple(
                (item.period_start, item.period_end)
                for item in candidate.observations
            )
            != grid
            or tuple(
                (
                    item.version_id,
                    item.evidence_id,
                    item.snapshot_id,
                    item.run_id,
                )
                for item in candidate.observations
            )
            != row_identities
        ):
            raise ValidationError(
                "Technical-indicator inputs do not share one Stage 10 selection"
            )
    if {"open", "high", "low", "close"}.issubset(by_role):
        rows = zip(
            by_role["open"].observations,
            by_role["high"].observations,
            by_role["low"].observations,
            by_role["close"].observations,
            strict=True,
        )
        for index, (opened, high, low, close) in enumerate(rows):
            assert (
                opened.value is not None
                and high.value is not None
                and low.value is not None
                and close.value is not None
            )
            if (
                high.value < low.value
                or high.value < max(opened.value, close.value)
                or low.value > min(opened.value, close.value)
            ):
                raise ValidationError(
                    f"Technical-indicator OHLC bar {index} is inconsistent"
                )
    return ordered


def _component_contract(component: str) -> tuple[str, str, str]:
    if component in _VOLUME_COMPONENTS:
        return "provider_native_volume", "volume", "1"
    if component in _PERCENT_COMPONENTS:
        return "percent", "percentage", "100"
    if component == "rolling_z_score":
        return "dimensionless", "z_score", "1"
    if component in _BAR_COUNT_COMPONENTS:
        return "dimensionless", "bar_count", "1"
    if component in _DIMENSIONLESS_COMPONENTS:
        representation = {
            "trend": "regime",
            "performance_index": "ratio",
            "target_factor": "factor",
            "swing_direction": "regime",
            "wavetrend": "ratio",
            "wavetrend_signal": "ratio",
            "wavetrend_difference": "ratio",
            "wavetrend_cross_signal": "regime",
        }[component]
        return "dimensionless", representation, "1"
    return "provider_native_currency", "price", "1"


def _cumulative_available_times(
    ordered: Sequence[tuple[str, TimeSeries]],
) -> tuple[str, ...]:
    """Build the conservative source-prefix availability grid in linear time."""

    if not ordered:
        raise ValidationError(
            "Technical-indicator availability requires input series"
        )
    row_count = len(ordered[0][1].observations)
    source_prefixes: list[tuple[tuple[datetime, str], ...]] = []
    for _, series in ordered:
        if len(series.observations) != row_count:
            raise ValidationError(
                "Technical-indicator input period grids do not align"
            )
        latest_raw: str | None = None
        latest_value: datetime | None = None
        prefix: list[tuple[datetime, str]] = []
        for observation in series.observations:
            parsed = TemporalValue.parse(
                observation.available_at or "",
                pointer="/series/observations/available_at",
            )
            if (
                parsed.precision is not TemporalPrecision.DATETIME
                or not isinstance(parsed.value, datetime)
            ):
                raise ValidationError(
                    "Technical-indicator input timing is invalid"
                )
            if latest_value is None or parsed.value > latest_value:
                latest_value = parsed.value
                latest_raw = observation.available_at
            assert latest_raw is not None
            prefix.append((latest_value, latest_raw))
        source_prefixes.append(tuple(prefix))

    combined: list[str] = []
    for index in range(row_count):
        latest_raw = None
        latest_value = None
        for prefix in source_prefixes:
            candidate_value, candidate_raw = prefix[index]
            if latest_value is None or candidate_value > latest_value:
                latest_value = candidate_value
                latest_raw = candidate_raw
        assert latest_raw is not None
        combined.append(latest_raw)
    return tuple(combined)


def _quality_flags(
    ordered: Sequence[tuple[str, TimeSeries]],
    index: int,
    missing_reason: str | None,
    transformation_id: str,
) -> tuple[str, ...]:
    flags = {f"transformation:{transformation_id}"}
    for _, series in ordered:
        flags.add(f"source_lineage:{series.lineage_digest}")
        flags.update(series.observations[index].quality_flags)
    if missing_reason is not None:
        flags.add(f"missing_reason:{missing_reason}")
    if len(flags) > 100:
        raise ResourceLimitError(
            "Technical-indicator output flags exceed the contract limit"
        )
    return tuple(sorted(flags))


def _warning_codes(
    ordered: Sequence[tuple[str, TimeSeries]],
    kernel_warnings: Sequence[str],
) -> tuple[str, ...]:
    codes = {
        "indicator_output_is_descriptive_not_a_trading_signal",
        "raw_price_adjustment_semantics_not_established",
    }
    for _, series in ordered:
        codes.update(
            item
            for item in series.warnings
            if isinstance(item, str) and item
        )
    codes.update(
        item
        for item in kernel_warnings
        if isinstance(item, str) and item
    )
    if any(role == "volume" for role, _ in ordered):
        codes.add("provider_native_volume_not_normalized")
    if len(codes) > 100:
        raise ResourceLimitError(
            "Technical-indicator warnings exceed the contract limit"
        )
    return tuple(sorted(codes))


def _derived_series(
    component: TechnicalIndicatorComponent,
    indicator: str,
    parameters: tuple[tuple[str, int | Decimal | str], ...],
    ordered: tuple[tuple[str, TimeSeries], ...],
    available_times: tuple[str, ...],
    selection_digest: str,
    warnings: tuple[str, ...],
    limit: int,
    operation_version: str,
    transformation_id: str,
) -> TimeSeries:
    anchor = dict(ordered)["close"]
    unit, representation, scale = _component_contract(component.name)
    source_fields = tuple(role for role, _ in ordered)
    source_ids = tuple(series.series_id for _, series in ordered)
    source_digests = tuple(series.lineage_digest for _, series in ordered)
    parameter_items = tuple(
        {"name": name, "value": value} for name, value in parameters
    )
    token = _digest(
        {
            "selection": selection_digest,
            "indicator": indicator,
            "component": component.name,
            "parameters": parameter_items,
        }
    )
    series_id = f"technical_indicator:{token}"
    observations: list[Observation] = []
    if len(available_times) != len(anchor.observations):
        raise ValidationError(
            "Technical-indicator availability grid does not align"
        )
    for index, (source, point) in enumerate(
        zip(anchor.observations, component.points, strict=True)
    ):
        available_at = available_times[index]
        identity = _digest(
            {
                "series_id": series_id,
                "period_start": source.period_start,
                "period_end": source.period_end,
                "value": point.value,
                "missing_reason": point.missing_reason,
                "available_at": available_at,
            }
        )
        observations.append(
            Observation(
                period_start=source.period_start,
                period_end=source.period_end,
                value=point.value,
                missing_reason=point.missing_reason,
                unit=unit,
                value_representation=representation,
                scale=scale,
                vintage_at=available_at,
                available_at=available_at,
                available_precision="datetime",
                captured_at=available_at,
                captured_precision="datetime",
                version_id=f"technical-indicator-version:{identity}",
                evidence_id=f"technical-indicator-evidence:{identity}",
                snapshot_id=f"technical-indicator-snapshot:{identity}",
                run_id=f"technical-indicator-run:{selection_digest}",
                dimensions={
                    "instrument_id": str(anchor.metadata["instrument_id"]),
                    "provider": PROVIDER,
                    "transformation": transformation_id,
                    "indicator": indicator,
                    "component": component.name,
                },
                quality_flags=_quality_flags(
                    ordered,
                    index,
                    point.missing_reason,
                    transformation_id,
                ),
            )
        )
    missing_count = sum(point.value is None for point in component.points)
    return TimeSeries(
        series_id=series_id,
        metadata={
            "instrument_id": anchor.metadata["instrument_id"],
            "provider_symbol": anchor.metadata["provider_symbol"],
            "asset_type": anchor.metadata["asset_type"],
            "display_name": anchor.metadata["display_name"],
            "exchange_code": anchor.metadata["exchange_code"],
            "provider": PROVIDER,
            "frequency": "daily",
            "unit": unit,
            "value_representation": representation,
            "scale": scale,
            "indicator": indicator,
            "component": component.name,
            "parameters": parameter_items,
            "source_observation_fields": source_fields,
            "source_price_variant": PRICE_VARIANT,
            "availability_basis": "derived_from_local_capture",
            "derived_availability_rule": (
                "cumulative_latest_exact_source_time"
            ),
            "session_calendar_status": "not_established",
            "adjustment_status": "not_established",
            "volume_unit_status": "provider_native_not_normalized",
            "transformation": transformation_id,
            "transformation_version": operation_version,
        },
        observations=tuple(observations),
        warnings=warnings,
        audit={
            "mode": anchor.audit["mode"],
            "requested_mode": anchor.audit["requested_mode"],
            "actual_mode": anchor.audit["actual_mode"],
            "cutoff": anchor.audit["cutoff"],
            "cutoff_precision": anchor.audit["cutoff_precision"],
            "date_only_policy": anchor.audit["date_only_policy"],
            "availability_basis": "derived_from_local_capture",
            "period_range_rule": "trade_date",
            "requested_start_date": anchor.audit["requested_start_date"],
            "requested_end_date": anchor.audit["requested_end_date"],
            "indicator": indicator,
            "component": component.name,
            "parameters": parameter_items,
            "source_series_ids": source_ids,
            "source_lineage_digests": source_digests,
            "source_observation_fields": source_fields,
            "input_count": len(ordered),
            "selected_count": len(observations),
            "missing_count": missing_count,
            "limit": limit,
            "point_in_time_status": anchor.audit[
                "point_in_time_status"
            ],
            "point_in_time_scope": anchor.audit["point_in_time_scope"],
            "unsafe_reasons": anchor.audit["unsafe_reasons"],
            "derived_availability_rule": (
                "cumulative_latest_exact_source_time"
            ),
            "truncated": False,
        },
        provenance={
            "transformation_id": transformation_id,
            "operation_version": operation_version,
            "source_dataset_ids": (
                DATASET_ID,
                EVIDENCE_DATASET_ID,
                IDENTITY_DATASET_ID,
            ),
            "source_series_ids": source_ids,
            "source_lineage_digests": source_digests,
            "input_selection_sha256": selection_digest,
            "store_role": "derived",
        },
        truncated=False,
    )


def invoke_stage10_technical_indicator(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> QueryResult:
    """Calculate one explicit indicator over compatible Stage 10 OHLCV."""

    if name != TOOL_NAME:
        raise LookupError(
            "Technical-indicator operation is not registered"
        )
    if (
        context.tool_version == OPERATION_VERSION
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV2
        operation_version = OPERATION_VERSION
        transformation_id = TRANSFORMATION_ID
    elif (
        context.tool_version == OPERATION_VERSION_V21
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_1"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV21
        operation_version = OPERATION_VERSION_V21
        transformation_id = TRANSFORMATION_ID_V21
    elif (
        context.tool_version == OPERATION_VERSION_V22
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_2"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV22
        operation_version = OPERATION_VERSION_V22
        transformation_id = TRANSFORMATION_ID_V22
    elif (
        context.tool_version == OPERATION_VERSION_V23
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_3"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV23
        operation_version = OPERATION_VERSION_V23
        transformation_id = TRANSFORMATION_ID_V23
    elif (
        context.tool_version == OPERATION_VERSION_V24
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_4"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV24
        operation_version = OPERATION_VERSION_V24
        transformation_id = TRANSFORMATION_ID_V24
    elif (
        context.tool_version == OPERATION_VERSION_V25
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_5"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV25
        operation_version = OPERATION_VERSION_V25
        transformation_id = TRANSFORMATION_ID_V25
    elif (
        context.tool_version == OPERATION_VERSION_V26
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_6"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV26
        operation_version = OPERATION_VERSION_V26
        transformation_id = TRANSFORMATION_ID_V26
    elif (
        context.tool_version == OPERATION_VERSION_V27
        and context.operation_graph_id
        == "tool_platform.market.technical_indicators.v2_7"
    ):
        argument_type = Stage10TechnicalIndicatorArgumentsV27
        operation_version = OPERATION_VERSION_V27
        transformation_id = TRANSFORMATION_ID_V27
    else:
        raise LookupError(
            "Selected technical-indicator operation graph is invalid"
        )
    if not isinstance(arguments, argument_type):
        raise ValidationError(
            "Technical-indicator arguments use the wrong contract"
        )
    context.checkpoint()
    ordered = _validate_inputs(
        arguments.series, arguments.indicator, arguments.limit
    )
    role_series = dict(ordered)
    bars = len(role_series["close"].observations)
    output_count = (
        10
        if arguments.indicator in _TEN_COMPONENT_INDICATORS
        else 5
        if arguments.indicator in _FIVE_COMPONENT_INDICATORS
        else 4
        if arguments.indicator in _FOUR_COMPONENT_INDICATORS
        else 3
        if arguments.indicator in _THREE_COMPONENT_INDICATORS
        else 2
        if arguments.indicator == "stochastic_oscillator"
        else 1
    )
    if bars * output_count > MAX_OUTPUT_VALUES:
        raise ResourceLimitError(
            "Technical-indicator output exceeds the value limit"
        )
    effective_window = max(
        (
            item
            for item in (
                arguments.window,
                arguments.fast_window,
                arguments.slow_window,
                arguments.signal_window,
                getattr(arguments, "percentile_window", None),
            )
            if item is not None
        ),
        default=1,
    )
    if arguments.indicator == "wavetrend_crosses":
        effective_window = max(effective_window, 4)
    if arguments.indicator == "supertrend_ai":
        minimum_factor = getattr(arguments, "minimum_factor", None)
        maximum_factor = getattr(arguments, "maximum_factor", None)
        factor_step = getattr(arguments, "factor_step", None)
        if not all(
            isinstance(item, Decimal)
            for item in (minimum_factor, maximum_factor, factor_step)
        ):
            raise ValidationError(
                "SuperTrend AI factor controls use the wrong contract"
            )
        assert isinstance(minimum_factor, Decimal)
        assert isinstance(maximum_factor, Decimal)
        assert isinstance(factor_step, Decimal)
        if factor_step <= 0 or minimum_factor > maximum_factor:
            raise ValidationError("SuperTrend AI factor grid is invalid")
        factor_count = int(
            (maximum_factor - minimum_factor) // factor_step
        ) + 1
        partition_bound = (
            (factor_count + 1) * (factor_count + 2) // 2
        )
        assignment_count = min(1_001, partition_bound)
        operation_count = bars * (
            output_count + factor_count * assignment_count
        )
    elif arguments.indicator == "swing_structure_forecast":
        operation_count = bars * (
            len(ordered)
            + output_count
            + int(getattr(arguments, "sample_count", 0) or 0)
        )
    else:
        operation_count = bars * (
            len(ordered) + output_count * effective_window
        )
    context.budget.require(
        rows=max(bars, arguments.limit),
        series=len(ordered),
        operations=operation_count,
    )
    result = calculate_technical_indicator(
        indicator=arguments.indicator,
        close=tuple(
            item.value for item in role_series["close"].observations
        ),
        high=(
            tuple(item.value for item in role_series["high"].observations)
            if "high" in role_series
            else ()
        ),
        low=(
            tuple(item.value for item in role_series["low"].observations)
            if "low" in role_series
            else ()
        ),
        volume=(
            tuple(item.value for item in role_series["volume"].observations)
            if "volume" in role_series
            else ()
        ),
        window=arguments.window,
        fast_window=arguments.fast_window,
        slow_window=arguments.slow_window,
        signal_window=arguments.signal_window,
        standard_deviation_multiplier=(
            arguments.standard_deviation_multiplier
        ),
        minimum_factor=getattr(arguments, "minimum_factor", None),
        maximum_factor=getattr(arguments, "maximum_factor", None),
        factor_step=getattr(arguments, "factor_step", None),
        performance_memory=getattr(arguments, "performance_memory", None),
        cluster=getattr(arguments, "cluster", None),
        sample_count=getattr(arguments, "sample_count", None),
        aggregation_method=getattr(
            arguments, "aggregation_method", None
        ),
        percentile_window=getattr(
            arguments, "percentile_window", None
        ),
        percentile_high_factor=getattr(
            arguments, "percentile_high_factor", None
        ),
        percentile_low_factor=getattr(
            arguments, "percentile_low_factor", None
        ),
        start=getattr(arguments, "start", None),
        increment=getattr(arguments, "increment", None),
        maximum=getattr(arguments, "maximum", None),
    )
    selection_digest = _digest(
        {
            "indicator": result.indicator,
            "parameters": [
                {"name": name, "value": value}
                for name, value in result.parameters
            ],
            "sources": [
                {
                    "observation_field": role,
                    "series_id": series.series_id,
                    "lineage_digest": series.lineage_digest,
                }
                for role, series in ordered
            ],
        }
    )
    warning_codes = _warning_codes(ordered, result.warnings)
    available_times = _cumulative_available_times(ordered)
    outputs = tuple(
        _derived_series(
            component,
            result.indicator,
            result.parameters,
            ordered,
            available_times,
            selection_digest,
            warning_codes,
            arguments.limit,
            operation_version,
            transformation_id,
        )
        for component in result.components
    )
    records = tuple(
        RecordV1(
            "technical_indicator_component",
            fields_from_mapping(
                {
                    "component": component.name,
                    "first_established_index": next(
                        (
                            index
                            for index, point in enumerate(component.points)
                            if point.value is not None
                        ),
                        None,
                    ),
                    "missing_count": sum(
                        point.value is None for point in component.points
                    ),
                    "observation_count": len(component.points),
                }
            ),
        )
        for component in result.components
    )
    lineage = tuple(
        LineageRef(
            dataset_id=dataset_id,
            store_role="market",
            semantic_id=series.lineage_digest,
        )
        for _, series in ordered
        for dataset_id in (
            DATASET_ID,
            EVIDENCE_DATASET_ID,
            IDENTITY_DATASET_ID,
        )
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        series=outputs,
        diagnostics=(
            DiagnosticV1(
                code="stage10_technical_indicator_quality",
                message=(
                    "Typed OHLCV inputs and deterministic indicator outputs "
                    "passed their compatibility audit."
                ),
                metrics=fields_from_mapping(
                    {
                        "bar_count": bars,
                        "indicator": result.indicator,
                        "input_series_count": len(ordered),
                        "output_series_count": len(outputs),
                        "point_in_time_status": outputs[0].audit[
                            "point_in_time_status"
                        ],
                        "selection_sha256": selection_digest,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Technical-indicator warning: {code}.",
            )
            for code in warning_codes
        ),
        lineage=lineage,
        truncation=TruncationV1(
            applied=False,
            limit=arguments.limit,
            returned_count=bars,
            total_known_count=bars,
            has_more=False,
        ),
    )


__all__ = (
    "MAX_OUTPUT_VALUES",
    "OPERATION_VERSION",
    "OPERATION_VERSION_V21",
    "OPERATION_VERSION_V22",
    "OPERATION_VERSION_V23",
    "OPERATION_VERSION_V24",
    "OPERATION_VERSION_V25",
    "OPERATION_VERSION_V26",
    "OPERATION_VERSION_V27",
    "TOOL_NAME",
    "invoke_stage10_technical_indicator",
    "stage10_technical_indicator_example",
    "stage10_technical_indicator_input_series_schema",
    "stage10_technical_indicator_output_series_schema",
    "stage10_technical_indicator_output_series_schema_v21",
    "stage10_technical_indicator_output_series_schema_v22",
    "stage10_technical_indicator_output_series_schema_v23",
    "stage10_technical_indicator_output_series_schema_v24",
    "stage10_technical_indicator_output_series_schema_v25",
    "stage10_technical_indicator_output_series_schema_v26",
    "stage10_technical_indicator_output_series_schema_v27",
)
