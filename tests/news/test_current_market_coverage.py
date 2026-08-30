from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError, ResourceLimitError
from quant_data.news.current_market_coverage import (
    ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST,
    CURRENT_MARKET_NEWS_MAX_SYMBOLS,
    read_current_market_news_coverage,
)
from quant_data.stores import StoreMap, StoreRole, writer_connection


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _install_market(store_map: StoreMap, rows: tuple[tuple[str, str, str], ...]) -> None:
    with writer_connection(
        store_map, StoreRole.MARKET, create_parent=True
    ) as connection:
        connection.executescript(
            """
            CREATE TABLE store_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                store_role TEXT NOT NULL
            ) STRICT;
            INSERT INTO store_metadata (singleton, store_role) VALUES (1, 'market');
            CREATE TABLE stage10_instruments (
                instrument_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                currency_segment TEXT NOT NULL
            ) STRICT;
            """
        )
        connection.executemany(
            """
            INSERT INTO stage10_instruments (
                instrument_id, provider, provider_symbol, asset_type, currency_segment
            ) VALUES (?, 'fmp', ?, ?, 'provider_native')
            """,
            rows,
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class CurrentMarketCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.stores = _stores(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_all_supported_symbols_and_partitions_alpaca_batches(self) -> None:
        _install_market(
            self.stores,
            (
                ("id-spx", "SPX", "index"),
                ("id-aapl", "AAPL", "equity"),
                ("id-spy", "SPY", "etf"),
            ),
        )

        coverage = read_current_market_news_coverage(self.stores)

        self.assertEqual(coverage.all_symbols, ("AAPL", "SPX", "SPY"))
        self.assertEqual(coverage.alpaca_symbols, ("AAPL", "SPY"))
        self.assertEqual(coverage.unsupported_index_symbols, ("SPX",))
        self.assertEqual(coverage.alpaca_symbol_batches, (("AAPL", "SPY"),))
        self.assertEqual(len(coverage.sha256), 64)

    def test_rejects_more_than_the_fixed_800_symbol_bound(self) -> None:
        _install_market(
            self.stores,
            tuple(
                (f"id-{index:04d}", f"S{index:04d}", "equity")
                for index in range(CURRENT_MARKET_NEWS_MAX_SYMBOLS + 1)
            ),
        )

        with self.assertRaises(ResourceLimitError):
            read_current_market_news_coverage(self.stores)

    def test_rejects_ambiguous_provider_symbols(self) -> None:
        _install_market(
            self.stores,
            (
                ("id-one", "AAPL", "equity"),
                ("id-two", "AAPL", "etf"),
            ),
        )

        with self.assertRaises(ConflictError):
            read_current_market_news_coverage(self.stores)

    def test_batches_are_at_most_fifty_symbols(self) -> None:
        _install_market(
            self.stores,
            tuple(
                (f"id-{index:03d}", f"S{index:03d}", "equity")
                for index in range(ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST + 1)
            ),
        )

        coverage = read_current_market_news_coverage(self.stores)

        self.assertEqual(
            tuple(len(batch) for batch in coverage.alpaca_symbol_batches),
            (ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST, 1),
        )


if __name__ == "__main__":
    unittest.main()
