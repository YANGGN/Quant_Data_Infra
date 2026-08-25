"""Typed public adapter for the current Stage 10 ticker universe."""

from __future__ import annotations

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.market.stage10_series import (
    CURRENCY_SEGMENT,
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
    PRICE_VARIANT,
    PROVIDER,
    Stage10AvailableTickerQuery,
    Stage10DailyPriceRepository,
)
from quant_data.registry import Registry

from .arguments import Stage10AvailableTickerArgumentsV1
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, RecordV1, fields_from_mapping


TOOL_NAME = "market.get_available_ticker"
OPERATION_VERSION = "1.0.0"


def invoke_stage10_available_tickers(
    name: str,
    arguments: Stage10AvailableTickerArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Return every bounded current ticker with a retrievable Stage 10 price."""

    if name != TOOL_NAME:
        raise LookupError("Stage 10 available-ticker operation is not registered")
    if not isinstance(arguments, Stage10AvailableTickerArgumentsV1):
        raise ValidationError("Stage 10 available tickers require typed arguments")
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=0,
        operations=arguments.limit,
    )
    selection = Stage10DailyPriceRepository(
        context.store_map, registry
    ).list_available_tickers(Stage10AvailableTickerQuery(limit=arguments.limit))
    records = tuple(
        RecordV1(
            record_type="stage10_available_ticker",
            fields=fields_from_mapping(
                {
                    "asset_type": item.asset_type,
                    "currency_segment": CURRENCY_SEGMENT,
                    "display_name": item.display_name,
                    "exchange_code": item.exchange_code,
                    "instrument_id": item.instrument_id,
                    "price_variant": PRICE_VARIANT,
                    "provider": PROVIDER,
                    "ticker": item.ticker,
                }
            ),
        )
        for item in selection.tickers
    )
    warning_codes = ["current_universe_not_point_in_time"]
    if selection.truncated:
        warning_codes.append("result_truncated")
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="stage10_available_ticker_inventory",
                message=(
                    "Every returned ticker has at least one current retrievable "
                    "Stage 10 daily-price row."
                ),
                metrics=fields_from_mapping(
                    {
                        "availability_scope": (
                            "current_retrievable_stage10_daily_price"
                        ),
                        "currency_segment": CURRENCY_SEGMENT,
                        "migration_count": len(selection.migration_ids),
                        "price_variant": PRICE_VARIANT,
                        "provider": PROVIDER,
                        "returned_count": len(records),
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=(
                    "This is the current stored ticker universe, not a "
                    "historical point-in-time universe."
                    if code == "current_universe_not_point_in_time"
                    else "The available-ticker result reached the requested limit."
                ),
            )
            for code in warning_codes
        ),
        lineage=(
            LineageRef(
                dataset_id=IDENTITY_DATASET_ID,
                store_role="market",
                semantic_id=selection.semantic_id,
            ),
            LineageRef(
                dataset_id=DATASET_ID,
                store_role="market",
                semantic_id=selection.semantic_id,
            ),
            LineageRef(
                dataset_id=EVIDENCE_DATASET_ID,
                store_role="market",
                semantic_id=selection.semantic_id,
            ),
        ),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=None if selection.truncated else len(records),
            has_more=selection.truncated,
        ),
    )


__all__ = (
    "OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_stage10_available_tickers",
)
