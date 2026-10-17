from __future__ import annotations

import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.fmp_bulk_daily_prices import (
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_price_response,
    parse_fmp_stage10_universe_response,
)
from quant_data.market.stage10_history_importer import Stage10HistoryImporter
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"
_MIGRATION_TIME = "2026-08-12T12:00:00Z"
_CAPTURE_TIME = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)


def _symbols(required: tuple[str, ...], prefix: str, count: int) -> tuple[str, ...]:
    if len(required) > count:
        raise AssertionError("Synthetic constituent count is invalid")
    return required + tuple(
        f"{prefix}{index:03d}" for index in range(1, count - len(required) + 1)
    )


def _universe_capture(
    path: str,
    symbols: tuple[str, ...],
    *,
    names: dict[str, str] | None = None,
):
    resolved_names = names or {}
    body = json.dumps(
        [
            {
                "symbol": symbol,
                "name": resolved_names.get(symbol, f"Company {symbol}"),
            }
            for symbol in symbols
        ],
        separators=(",", ":"),
    ).encode("utf-8")
    return parse_fmp_stage10_universe_response(endpoint_path=path, body=body)


def _price_row(symbol: str, trade_date: str, *, close: int = 102) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": trade_date,
        "open": 100,
        "high": 105,
        "low": 99,
        "close": close,
        "volume": 1_000,
        "change": close - 100,
        "changePercent": close - 100,
        "vwap": 102,
    }


def _price_capture(symbol: str, *, corrected: bool = False):
    rows = [
        _price_row(symbol, "1993-01-29"),
        _price_row(symbol, "1993-02-01", close=104 if corrected else 102),
    ]
    return parse_fmp_stage10_price_response(
        symbol=symbol,
        body=json.dumps(rows, separators=(",", ":")).encode("utf-8"),
    )


class Stage10HistoryImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.store_map = explicit_store_map(self.root / "stores")
        self.registry = stage10_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        self.scope = load_stage10_market_scope(SCOPE_PATH)
        initialize_all(self.store_map, self.registry, applied_at=_MIGRATION_TIME)
        self.importer = Stage10HistoryImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=lambda: _CAPTURE_TIME,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _captures() -> dict[str, object]:
        return {
            "sp500_current": _universe_capture(
                FMP_STAGE10_SP500_CONSTITUENT_PATH,
                _symbols(("AAPL", "MSFT", "NVDA"), "S", 500),
            ),
            "nasdaq100_current": _universe_capture(
                FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
                _symbols(
                    ("ALAB", "CRWV", "LITE", "NBIS", "RKLB", "SNDK", "SPCX", "TER", "WMT"),
                    "N",
                    100,
                ),
            ),
            "dow30_current": _universe_capture(
                FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
                _symbols(("AAPL", "MSFT", "JPM"), "D", 30),
            ),
        }

    def _publish_scope(self) -> None:
        before_prepare = mutation_fingerprint(self.store_map)
        candidates = self.importer.prepare_universe_publications(self._captures())
        self.assertEqual(mutation_fingerprint(self.store_map), before_prepare)
        self.assertEqual(len(candidates), 5)
        for candidate in candidates:
            receipt = self.importer.publish_prepared(candidate)
            self.assertEqual(receipt.outcome, "succeeded")

    def test_complete_current_scope_publishes_five_snapshots_and_excludes_russell(self) -> None:
        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
        ):
            self._publish_scope()
        replay_before = mutation_fingerprint(self.store_map)
        replay_candidates = self.importer.prepare_universe_publications(self._captures())
        replay_receipts = tuple(
            self.importer.publish_prepared(candidate)
            for candidate in replay_candidates
        )
        self.assertEqual(
            tuple((receipt.outcome, receipt.written_count) for receipt in replay_receipts),
            (("unchanged", 0),) * 5,
        )
        self.assertEqual(mutation_fingerprint(self.store_map), replay_before)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            snapshots = tuple(
                connection.execute(
                    "SELECT universe_id, member_count FROM stage10_universe_snapshots ORDER BY universe_id"
                )
            )
            self.assertEqual(
                tuple((row["universe_id"], row["member_count"]) for row in snapshots),
                (
                    ("curated_etfs", 95),
                    ("dow30_current", 30),
                    ("major_indexes", 15),
                    ("nasdaq100_current", 100),
                    ("sp500_current", 500),
                ),
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_scope_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_universe_captures").fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_instruments").fetchone()[0],
                738,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT first_trade_date FROM stage10_instruments WHERE provider_symbol='DRAM'"
                ).fetchone()[0],
                "2026-04-02",
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM stage10_instruments WHERE provider_symbol='^RUT'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM stage10_universes WHERE universe_id LIKE '%russell%'"
                ).fetchone()[0],
                0,
            )

    def test_full_history_price_replay_is_total_no_write_and_correction_versions(self) -> None:
        self._publish_scope()
        original = _price_capture("AAPL")
        before_prepare = mutation_fingerprint(self.store_map)
        prepared = self.importer.prepare_price_capture(original)
        self.assertEqual(mutation_fingerprint(self.store_map), before_prepare)
        first = self.importer.publish_prepared(prepared)
        self.assertEqual(first.outcome, "succeeded")
        after_first = mutation_fingerprint(self.store_map)
        replay = self.importer.publish_prepared(prepared)
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.store_map), after_first)

        corrected = self.importer.publish_prepared(
            self.importer.prepare_price_capture(_price_capture("AAPL", corrected=True))
        )
        self.assertEqual(corrected.outcome, "succeeded")
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_captures").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_versions").fetchone()[0],
                3,
            )
            current = connection.execute(
                """
                SELECT version.close_value, version.correction_sequence
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id=current.current_version_id
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=current.instrument_id
                WHERE instrument.provider_symbol='AAPL' AND current.trade_date='1993-02-01'
                """
            ).fetchone()
            self.assertEqual((current["close_value"], current["correction_sequence"]), ("104", 2))
            coverage = connection.execute(
                """
                SELECT earliest_trade_date, latest_trade_date, row_count
                FROM stage10_daily_price_captures
                ORDER BY captured_at, capture_id LIMIT 1
                """
            ).fetchone()
            self.assertEqual(
                tuple(coverage),
                ("1993-01-29", "1993-02-01", 2),
            )

    def test_price_history_rejects_future_rows_and_prelisting_etf_rows(self) -> None:
        self._publish_scope()
        before = mutation_fingerprint(self.store_map)
        hostile = (
            ("AAPL", ("2026-08-12", "2026-08-13")),
            ("DRAM", ("2026-04-01", "2026-04-02")),
        )
        for symbol, dates in hostile:
            body = json.dumps(
                [_price_row(symbol, trade_date) for trade_date in dates],
                separators=(",", ":"),
            ).encode("utf-8")
            capture = parse_fmp_stage10_price_response(symbol=symbol, body=body)
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValidationError):
                    self.importer.prepare_price_capture(capture)
        self.assertEqual(mutation_fingerprint(self.store_map), before)

    def test_unpublished_or_russell_prices_and_foreign_candidates_fail_without_write(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.prepare_price_capture(_price_capture("SPY"))
        self.assertEqual(mutation_fingerprint(self.store_map), before)

        self._publish_scope()
        after_scope = mutation_fingerprint(self.store_map)
        for symbol in ("^RUT", "IWM"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValidationError):
                    self.importer.prepare_price_capture(_price_capture(symbol))
        self.assertEqual(mutation_fingerprint(self.store_map), after_scope)

        candidate = self.importer.prepare_price_capture(_price_capture("AAPL"))
        foreign = Stage10HistoryImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=lambda: _CAPTURE_TIME,
        )
        with self.assertRaises(ValidationError):
            foreign.publish_prepared(candidate)
        self.assertEqual(mutation_fingerprint(self.store_map), after_scope)

    def test_incomplete_current_constituent_capture_is_rejected_before_any_write(self) -> None:
        captures = self._captures()
        captures["sp500_current"] = _universe_capture(
            FMP_STAGE10_SP500_CONSTITUENT_PATH,
            _symbols(("MSFT", "NVDA"), "S", 500),
        )
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.prepare_universe_publications(captures)  # type: ignore[arg-type]
        self.assertEqual(mutation_fingerprint(self.store_map), before)

    def test_provider_constituent_capture_cannot_reintroduce_iwm(self) -> None:
        captures = self._captures()
        captures["sp500_current"] = _universe_capture(
            FMP_STAGE10_SP500_CONSTITUENT_PATH,
            _symbols(("AAPL", "MSFT", "NVDA", "IWM"), "S", 500),
        )
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.prepare_universe_publications(captures)
        self.assertEqual(mutation_fingerprint(self.store_map), before)

    def test_cross_universe_name_variation_does_not_redefine_symbol_identity(self) -> None:
        captures = self._captures()
        captures["sp500_current"] = _universe_capture(
            FMP_STAGE10_SP500_CONSTITUENT_PATH,
            _symbols(("AAPL", "MSFT", "NVDA"), "S", 500),
            names={"AAPL": "Apple Inc."},
        )
        captures["nasdaq100_current"] = _universe_capture(
            FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
            _symbols(
                (
                    "AAPL", "ALAB", "CRWV", "LITE", "NBIS",
                    "RKLB", "SNDK", "SPCX", "TER", "WMT",
                ),
                "N",
                100,
            ),
            names={"AAPL": "Apple Incorporated"},
        )
        captures["dow30_current"] = _universe_capture(
            FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
            _symbols(("AAPL", "MSFT", "JPM"), "D", 30),
            names={"AAPL": "Apple"},
        )
        candidates = self.importer.prepare_universe_publications(
            captures  # type: ignore[arg-type]
        )
        self.assertEqual(len(candidates), 5)


if __name__ == "__main__":
    unittest.main()
