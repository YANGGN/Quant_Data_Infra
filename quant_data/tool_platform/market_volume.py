"""Typed public adapter for canonical Stage 10 daily volume."""

from __future__ import annotations

from typing import Any

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.market.stage10_series import (
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
    PRICE_VARIANT,
    PROVIDER,
    Stage10DailyPriceQuery,
    Stage10DailyPriceRepository,
)
from quant_data.registry import Registry

from .arguments import Stage10MarketVolumeArgumentsV1
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, fields_from_mapping


TOOL_NAME = "market.get_volume_series"
OPERATION_VERSION = "1.0.0"


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _text() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _nullable_text() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _strings(max_items: int = 100) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": _text(),
    }


def stage10_market_volume_series_schema() -> dict[str, Any]:
    """Return the strict provider-native volume TimeSeries schema."""

    dimensions = _strict(
        {
            "instrument_id": _text(),
            "provider": {"type": "string", "const": PROVIDER},
            "data_variant": {"type": "string", "const": PRICE_VARIANT},
            "observation_field": {"type": "string", "const": "volume"},
        }
    )
    observation = _strict(
        {
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "value": {"type": "number", "minimum": 0},
            "missing_reason": {"type": "null"},
            "unit": {"type": "string", "const": "provider_native_volume"},
            "value_representation": {"type": "string", "const": "volume"},
            "scale": {"type": "string", "const": "1"},
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
    identity = _strict(
        {
            "instrument_id": _text(),
            "provider_symbol": _text(),
            "asset_type": _text(),
            "display_name": _nullable_text(),
            "exchange_code": _nullable_text(),
            "currency_segment": _text(),
            "identity_seed_sha256": _text(),
            "captured_at": _text(),
            "captured_precision": {"type": "string", "const": "datetime"},
            "run_id": _text(),
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
            "unit": {"type": "string", "const": "provider_native_volume"},
            "value_representation": {"type": "string", "const": "volume"},
            "scale": {"type": "string", "const": "1"},
            "data_variant": {"type": "string", "const": PRICE_VARIANT},
            "observation_field": {"type": "string", "const": "volume"},
            "availability_basis": {"type": "string", "const": "local_capture"},
            "volume_unit_status": {
                "type": "string",
                "const": "provider_native_not_normalized",
            },
            "volume_adjustment_status": {
                "type": "string",
                "const": "not_established",
            },
            "session_calendar_status": {
                "type": "string",
                "const": "not_established",
            },
        }
    )
    audit = _strict(
        {
            "mode": {"type": "string", "enum": ["latest", "as_of"]},
            "requested_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "actual_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "cutoff": _nullable_text(),
            "cutoff_precision": _nullable_text(),
            "date_only_policy": {
                "type": "string",
                "enum": ["completed_date", "calendar_date_inclusive"],
            },
            "availability_basis": {"type": "string", "const": "local_capture"},
            "period_range_rule": {"type": "string", "const": "trade_date"},
            "requested_start_date": _nullable_text(),
            "requested_end_date": _nullable_text(),
            "provider": {"type": "string", "const": PROVIDER},
            "data_variant": {"type": "string", "const": PRICE_VARIANT},
            "observation_field": {"type": "string", "const": "volume"},
            "volume_unit_status": {
                "type": "string",
                "const": "provider_native_not_normalized",
            },
            "volume_adjustment_status": {
                "type": "string",
                "const": "not_established",
            },
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
                "enum": ["retained_local_captures", "current_stored_knowledge"],
            },
            "unsafe_reasons": _strings(),
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            "selected_count": {"type": "integer", "minimum": 0, "maximum": 10000},
            "missing_count": {"type": "integer", "const": 0},
            "truncated": {"type": "boolean"},
        }
    )
    provenance = _strict(
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
            "registry_revision": _text(),
            "store_receipt": _strict(
                {
                    "migration_ids": _strings(),
                    "selected_instrument_identity": identity,
                    "sha256": _text(),
                }
            ),
        }
    )
    return _strict(
        {
            "contract": {"type": "string", "const": "quant_data.timeseries"},
            "contract_version": {"type": "string", "const": "1.0.0"},
            "series_id": _text(),
            "metadata": metadata,
            "observations": {
                "type": "array",
                "maxItems": 10000,
                "items": observation,
            },
            "warnings": _strings(),
            "audit": audit,
            "provenance": provenance,
            "truncated": {"type": "boolean"},
            "lineage_digest": _text(),
        }
    )


def invoke_stage10_market_volume(
    name: str,
    arguments: Stage10MarketVolumeArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Execute the additive read-only provider-volume operation."""

    if name != TOOL_NAME:
        raise LookupError("Stage 10 market-volume operation is not registered")
    if not isinstance(arguments, Stage10MarketVolumeArgumentsV1):
        raise ValidationError("Stage 10 market volume requires typed arguments")
    context.checkpoint()
    context.budget.require(rows=arguments.limit, series=1, operations=arguments.limit)
    series = Stage10DailyPriceRepository(
        context.store_map, registry
    ).get_volume_series(
        Stage10DailyPriceQuery(
            identifier=arguments.ticker,
            identifier_kind="provider_symbol",
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        )
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        series=(series,),
        diagnostics=(
            DiagnosticV1(
                code="stage10_market_volume_quality",
                message=(
                    "The Stage 10 provider-native volume series passed its "
                    "typed quality audit."
                ),
                metrics=fields_from_mapping(
                    {
                        "observation_count": len(series.observations),
                        "point_in_time_status": series.audit[
                            "point_in_time_status"
                        ],
                        "requested_end_date": arguments.end_date,
                        "requested_start_date": arguments.start_date,
                        "ticker": series.metadata["provider_symbol"],
                        "truncated": series.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Stage 10 volume series warning: {code}.",
            )
            for code in series.warnings
        ),
        lineage=tuple(
            LineageRef(
                dataset_id=dataset_id,
                store_role="market",
                semantic_id=series.lineage_digest,
            )
            for dataset_id in (DATASET_ID, EVIDENCE_DATASET_ID, IDENTITY_DATASET_ID)
        ),
        truncation=TruncationV1(
            applied=series.truncated,
            limit=arguments.limit,
            returned_count=len(series.observations),
            total_known_count=None,
            has_more=series.truncated,
        ),
    )


__all__ = (
    "OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_stage10_market_volume",
    "stage10_market_volume_series_schema",
)
