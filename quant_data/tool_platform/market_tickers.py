"""Typed public adapter for the current Stage 10 ticker universe."""

from __future__ import annotations

import base64
import binascii

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage10_series import (
    CURRENCY_SEGMENT,
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
    PRICE_VARIANT,
    PROVIDER,
    Stage10AvailableTickerQuery,
    Stage10DailyPriceRepository,
    Stage10InstrumentSearchQuery,
)
from quant_data.registry import Registry

from .arguments import (
    Stage10AvailableTickerArgumentsV1,
    Stage10MarketInstrumentSearchArgumentsV2,
)
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


INSTRUMENT_SEARCH_TOOL_NAME = "market.search_instruments"
INSTRUMENT_SEARCH_OPERATION_VERSION = "2.0.0"
_CURSOR_VERSION = 1
_CURSOR_MAX_LENGTH = 1_024


def _encode_stage10_market_instrument_cursor(
    *,
    query: str,
    asset_type: str | None,
    provider_symbol: str,
    instrument_id: str,
) -> str:
    material = {
        "version": _CURSOR_VERSION,
        "query": query,
        "asset_type": asset_type,
        "provider_symbol": provider_symbol,
        "instrument_id": instrument_id,
    }
    return (
        base64.urlsafe_b64encode(dumps_strict(material).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )


def _decode_stage10_market_instrument_cursor(
    cursor: object,
    *,
    query: str,
    asset_type: str | None,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > _CURSOR_MAX_LENGTH
    ):
        raise ValidationError(
            "Stage 10 instrument search cursor must be null or a bounded string"
        )
    try:
        encoded = cursor.encode("ascii")
        padded = encoded + b"=" * ((4 - len(encoded) % 4) % 4)
        payload = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValidationError(
            "Stage 10 instrument search cursor is malformed"
        ) from exc
    if base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii") != cursor:
        raise ValidationError("Stage 10 instrument search cursor is not canonical")
    try:
        material = loads_strict(payload, max_bytes=2_048)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValidationError(
            "Stage 10 instrument search cursor is malformed"
        ) from exc
    if (
        not isinstance(material, dict)
        or set(material)
        != {
            "version",
            "query",
            "asset_type",
            "provider_symbol",
            "instrument_id",
        }
        or isinstance(material["version"], bool)
        or material["version"] != _CURSOR_VERSION
        or material["query"] != query
        or material["asset_type"] != asset_type
    ):
        raise ValidationError(
            "Stage 10 instrument search cursor does not match the selected query"
        )
    provider_symbol = material["provider_symbol"]
    instrument_id = material["instrument_id"]
    if (
        not isinstance(provider_symbol, str)
        or not provider_symbol
        or len(provider_symbol) > 200
        or not isinstance(instrument_id, str)
        or not instrument_id
        or len(instrument_id) > 200
    ):
        raise ValidationError("Stage 10 instrument search cursor key is invalid")
    return provider_symbol, instrument_id


def invoke_stage10_market_instrument_search(
    name: str,
    arguments: Stage10MarketInstrumentSearchArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Search the retained FMP identity universe with opaque keyset pagination."""

    if name != INSTRUMENT_SEARCH_TOOL_NAME:
        raise LookupError("Stage 10 instrument-search operation is not registered")
    if not isinstance(arguments, Stage10MarketInstrumentSearchArgumentsV2):
        raise ValidationError(
            "Stage 10 instrument search requires typed v2 arguments"
        )
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=0,
        operations=arguments.limit,
    )
    initial_query = Stage10InstrumentSearchQuery(
        query=arguments.query,
        asset_type=arguments.asset_type,
        limit=arguments.limit,
    )
    after = _decode_stage10_market_instrument_cursor(
        arguments.cursor,
        query=initial_query.query,
        asset_type=initial_query.asset_type,
    )
    selection = Stage10DailyPriceRepository(
        context.store_map,
        registry,
    ).search_instruments(
        Stage10InstrumentSearchQuery(
            query=initial_query.query,
            asset_type=initial_query.asset_type,
            limit=initial_query.limit,
            after=after,
        )
    )
    records = tuple(
        RecordV1(
            record_type="stage10_market_instrument",
            fields=fields_from_mapping(item.receipt_mapping()),
        )
        for item in selection.instruments
    )
    next_cursor = (
        _encode_stage10_market_instrument_cursor(
            query=initial_query.query,
            asset_type=initial_query.asset_type,
            provider_symbol=selection.instruments[-1].ticker,
            instrument_id=selection.instruments[-1].instrument_id,
        )
        if selection.truncated
        else None
    )
    warning_codes = ["current_universe_not_point_in_time"]
    if selection.truncated:
        warning_codes.append("result_truncated")
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        warnings=tuple(
            WarningV1(
                code=code,
                message=(
                    "This is the retained current FMP instrument universe, not "
                    "a historical point-in-time universe."
                    if code == "current_universe_not_point_in_time"
                    else "The instrument-search result reached the requested limit."
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
        ),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=initial_query.limit,
            returned_count=len(records),
            total_known_count=None,
            has_more=selection.truncated,
            next_cursor=next_cursor,
        ),
    )



__all__ = (
    "OPERATION_VERSION",
    "TOOL_NAME",
    "invoke_stage10_available_tickers",
    "INSTRUMENT_SEARCH_OPERATION_VERSION",
    "INSTRUMENT_SEARCH_TOOL_NAME",
    "invoke_stage10_market_instrument_search",
)
