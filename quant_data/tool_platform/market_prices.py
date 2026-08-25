"""Typed public adapter for canonical Stage 10 OHLC price series."""

from __future__ import annotations

from typing import Any

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.market.stage10_series import (
    CURRENCY_SEGMENT,
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
    OHLC_FIELDS,
    PRICE_VARIANT,
    PROVIDER,
    Stage10DailyPriceQuery,
    Stage10DailyPriceRepository,
)
from quant_data.registry import Registry

from .arguments import Stage10MarketPriceArgumentsV1
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, fields_from_mapping


TOOL_NAME = "market.get_price_series"
OPERATION_VERSION = "1.0.0"


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _text_schema() -> dict[str, Any]:
    return {"type": "string"}


def _nullable_text_schema() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _string_array_schema(max_items: int) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": _text_schema(),
    }


def stage10_market_price_series_schema() -> dict[str, Any]:
    """Return the exact composable scalar-price TimeSeries schema."""

    field_schema = {"type": "string", "enum": list(OHLC_FIELDS)}
    dimensions = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider": {"type": "string", "const": PROVIDER},
            "price_variant": {"type": "string", "const": PRICE_VARIANT},
            "currency_segment": {
                "type": "string",
                "const": CURRENCY_SEGMENT,
            },
            "observation_field": field_schema,
        }
    )
    observation = _strict_object(
        {
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "value": {"type": "number"},
            "missing_reason": {"type": "null"},
            "unit": {
                "type": "string",
                "const": "provider_native_currency",
            },
            "value_representation": {"type": "string", "const": "price"},
            "scale": {"type": "string", "const": "1"},
            "vintage_at": _text_schema(),
            "available_at": _text_schema(),
            "available_precision": {"type": "string", "const": "datetime"},
            "captured_at": _text_schema(),
            "captured_precision": {"type": "string", "const": "datetime"},
            "version_id": _text_schema(),
            "evidence_id": _text_schema(),
            "snapshot_id": _text_schema(),
            "run_id": _text_schema(),
            "dimensions": dimensions,
            "quality_flags": _string_array_schema(100),
        }
    )
    metadata = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider_symbol": _text_schema(),
            "asset_type": _text_schema(),
            "display_name": _nullable_text_schema(),
            "exchange_code": _nullable_text_schema(),
            "provider": {"type": "string", "const": PROVIDER},
            "frequency": {"type": "string", "const": "daily"},
            "unit": {
                "type": "string",
                "const": "provider_native_currency",
            },
            "value_representation": {"type": "string", "const": "price"},
            "scale": {"type": "string", "const": "1"},
            "price_variant": {"type": "string", "const": PRICE_VARIANT},
            "currency_segment": {
                "type": "string",
                "const": CURRENCY_SEGMENT,
            },
            "observation_field": field_schema,
            "availability_basis": {
                "type": "string",
                "const": "local_capture",
            },
            "horizon_basis": {"type": "string", "const": "observed_rows"},
            "session_calendar_status": {
                "type": "string",
                "const": "not_established",
            },
            "adjustment_status": {
                "type": "string",
                "const": "not_established",
            },
        }
    )
    audit = _strict_object(
        {
            "mode": {"type": "string", "enum": ["latest", "as_of"]},
            "requested_mode": {
                "type": "string",
                "enum": ["latest", "as_of"],
            },
            "actual_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "cutoff": _nullable_text_schema(),
            "cutoff_precision": _nullable_text_schema(),
            "date_only_policy": {
                "type": "string",
                "enum": ["completed_date", "calendar_date_inclusive"],
            },
            "availability_basis": {
                "type": "string",
                "const": "local_capture",
            },
            "period_range_rule": {"type": "string", "const": "trade_date"},
            "requested_start_date": _nullable_text_schema(),
            "requested_end_date": _nullable_text_schema(),
            "provider": {"type": "string", "const": PROVIDER},
            "price_variant": {"type": "string", "const": PRICE_VARIANT},
            "currency_segment": {
                "type": "string",
                "const": CURRENCY_SEGMENT,
            },
            "observation_field": field_schema,
            "horizon_basis": {"type": "string", "const": "observed_rows"},
            "session_calendar_status": {
                "type": "string",
                "const": "not_established",
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
            "unsafe_reasons": _string_array_schema(100),
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            "selected_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10000,
            },
            "missing_count": {"type": "integer", "const": 0},
            "truncated": {"type": "boolean"},
        }
    )
    selected_identity = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider_symbol": _text_schema(),
            "asset_type": _text_schema(),
            "display_name": _nullable_text_schema(),
            "exchange_code": _nullable_text_schema(),
            "currency_segment": {
                "type": "string",
                "const": CURRENCY_SEGMENT,
            },
            "identity_seed_sha256": _text_schema(),
            "captured_at": _text_schema(),
            "captured_precision": {
                "type": "string",
                "const": "datetime",
            },
            "run_id": _text_schema(),
        }
    )
    store_receipt = _strict_object(
        {
            "migration_ids": _string_array_schema(100),
            "selected_instrument_identity": selected_identity,
            "sha256": _text_schema(),
        }
    )
    provenance = _strict_object(
        {
            "dataset_id": {"type": "string", "const": DATASET_ID},
            "evidence_dataset_id": {
                "type": "string",
                "const": EVIDENCE_DATASET_ID,
            },
            "identity_dataset_id": {
                "type": "string",
                "const": IDENTITY_DATASET_ID,
            },
            "store_role": {"type": "string", "const": "market"},
            "registry_revision": _text_schema(),
            "store_receipt": store_receipt,
        }
    )
    return _strict_object(
        {
            "contract": {
                "type": "string",
                "const": "quant_data.timeseries",
            },
            "contract_version": {"type": "string", "const": "1.0.0"},
            "series_id": _text_schema(),
            "metadata": metadata,
            "observations": {
                "type": "array",
                "maxItems": 10000,
                "items": observation,
            },
            "warnings": _string_array_schema(100),
            "audit": audit,
            "provenance": provenance,
            "truncated": {"type": "boolean"},
            "lineage_digest": _text_schema(),
        }
    )


def invoke_stage10_market_price(
    name: str,
    arguments: Stage10MarketPriceArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Execute the additive read-only OHLC price-series operation."""

    if name != TOOL_NAME:
        raise LookupError("Stage 10 market-price operation is not registered")
    if not isinstance(arguments, Stage10MarketPriceArgumentsV1):
        raise ValidationError("Stage 10 market prices require typed arguments")
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=4,
        operations=arguments.limit * 4,
    )
    query = Stage10DailyPriceQuery(
        identifier=arguments.ticker,
        identifier_kind="provider_symbol",
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        mode=arguments.mode,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        limit=arguments.limit,
    )
    series = Stage10DailyPriceRepository(
        context.store_map, registry
    ).get_ohlc_series(query)
    if tuple(item.metadata["observation_field"] for item in series) != OHLC_FIELDS:
        raise ValidationError("Stage 10 OHLC series order is inconsistent")
    first = series[0]
    if any(
        len(item.observations) != len(first.observations)
        or item.truncated != first.truncated
        for item in series[1:]
    ):
        raise ValidationError("Stage 10 OHLC series selection is inconsistent")
    warning_codes = tuple(
        sorted({code for item in series for code in item.warnings})
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        series=series,
        diagnostics=(
            DiagnosticV1(
                code="stage10_market_price_quality",
                message="The Stage 10 OHLC series passed its typed quality audit.",
                metrics=fields_from_mapping(
                    {
                        "field_count": 4,
                        "observation_count": len(first.observations),
                        "point_in_time_status": first.audit[
                            "point_in_time_status"
                        ],
                        "requested_end_date": arguments.end_date,
                        "requested_start_date": arguments.start_date,
                        "ticker": first.metadata["provider_symbol"],
                        "truncated": first.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Stage 10 price series warning: {code}.",
            )
            for code in warning_codes
        ),
        lineage=tuple(
            LineageRef(
                dataset_id=DATASET_ID,
                store_role="market",
                semantic_id=item.lineage_digest,
            )
            for item in series
        ),
        truncation=TruncationV1(
            applied=first.truncated,
            limit=arguments.limit,
            returned_count=len(first.observations),
            total_known_count=None,
            has_more=first.truncated,
        ),
    )


__all__ = (
    "OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_stage10_market_price",
    "stage10_market_price_series_schema",
)
