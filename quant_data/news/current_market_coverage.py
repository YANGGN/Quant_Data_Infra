"""Read the bounded current market universe for company-news collection.

This is deliberately a read-only bridge: Stage 10 remains the authority for
the ticker universe, while the generic news collector remains the only writer
to the news store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection


CURRENT_MARKET_NEWS_MAX_SYMBOLS = 800
ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST = 50
_SUPPORTED_ASSET_TYPES = frozenset({"equity", "etf", "index"})
_ALPACA_ASSET_TYPES = frozenset({"equity", "etf"})


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CurrentMarketNewsInstrument:
    """One provider-native Stage 10 identity retained for current news."""

    instrument_id: str
    symbol: str
    asset_type: str


@dataclass(frozen=True, slots=True)
class CurrentMarketNewsCoverage:
    """A deterministic bounded snapshot of the current Stage 10 universe."""

    instruments: tuple[CurrentMarketNewsInstrument, ...]
    sha256: str

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.instruments)

    @property
    def alpaca_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.symbol for item in self.instruments if item.asset_type in _ALPACA_ASSET_TYPES
        )

    @property
    def unsupported_index_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.instruments if item.asset_type == "index")

    @property
    def alpaca_symbol_batches(self) -> tuple[tuple[str, ...], ...]:
        symbols = self.alpaca_symbols
        return tuple(
            symbols[index : index + ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST]
            for index in range(0, len(symbols), ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST)
        )


def _validate_row(row: Mapping[str, object]) -> CurrentMarketNewsInstrument:
    instrument_id = row.get("instrument_id")
    symbol = row.get("provider_symbol")
    asset_type = row.get("asset_type")
    if (
        not isinstance(instrument_id, str)
        or not instrument_id
        or len(instrument_id) > 256
        or any(character.isspace() for character in instrument_id)
        or not isinstance(symbol, str)
        or not symbol
        or len(symbol) > 64
        or symbol != symbol.upper()
        or any(character.isspace() for character in symbol)
        or not isinstance(asset_type, str)
        or asset_type not in _SUPPORTED_ASSET_TYPES
    ):
        raise ValidationError("Current market news coverage contains an invalid Stage 10 identity")
    return CurrentMarketNewsInstrument(
        instrument_id=instrument_id,
        symbol=symbol,
        asset_type=asset_type,
    )


def read_current_market_news_coverage(store_map: StoreMap) -> CurrentMarketNewsCoverage:
    """Read at most 800 current FMP-native equity, ETF, and index identities.

    The function opens the market store only through the quiet immutable
    reader.  It does not query a caller-selected database, mutate either
    store, or attempt an Alpaca request for the unsupported index symbols.
    """

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Current market news coverage requires an explicit store map")
    with quiet_immutable_read_connection(store_map, StoreRole.MARKET) as connection:
        relation = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='stage10_instruments'
            """
        ).fetchone()
        if relation is None:
            raise StoreUnavailableError("Market store lacks the Stage 10 instrument model")
        rows = tuple(
            dict(row)
            for row in connection.execute(
                """
                SELECT instrument_id, provider_symbol, asset_type
                FROM stage10_instruments
                WHERE provider='fmp'
                  AND currency_segment='provider_native'
                  AND asset_type IN ('equity', 'etf', 'index')
                ORDER BY provider_symbol COLLATE BINARY, instrument_id COLLATE BINARY
                LIMIT ?
                """,
                (CURRENT_MARKET_NEWS_MAX_SYMBOLS + 1,),
            )
        )
    if len(rows) > CURRENT_MARKET_NEWS_MAX_SYMBOLS:
        raise ResourceLimitError("Current market news coverage exceeds its 800-symbol bound")
    instruments = tuple(_validate_row(row) for row in rows)
    if not instruments:
        raise StoreUnavailableError("Current market news coverage is empty")
    symbols = [item.symbol for item in instruments]
    instrument_ids = [item.instrument_id for item in instruments]
    if len(symbols) != len(set(symbols)) or len(instrument_ids) != len(set(instrument_ids)):
        raise ConflictError("Current market news coverage is ambiguous")
    material = [
        {
            "asset_type": item.asset_type,
            "instrument_id": item.instrument_id,
            "symbol": item.symbol,
        }
        for item in instruments
    ]
    return CurrentMarketNewsCoverage(instruments=instruments, sha256=_sha256_json(material))


__all__ = (
    "ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST",
    "CURRENT_MARKET_NEWS_MAX_SYMBOLS",
    "CurrentMarketNewsCoverage",
    "CurrentMarketNewsInstrument",
    "read_current_market_news_coverage",
)
