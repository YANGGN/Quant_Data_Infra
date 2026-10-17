"""Offline coverage for the additive Stage 10 history-window publisher."""

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
    CapturedFmpStage10Response,
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_price_response,
    parse_fmp_stage10_universe_response,
)
from quant_data.market.stage10_history_importer import Stage10HistoryImporter
from quant_data.market.stage10_history_windows import (
    STAGE10_HISTORY_WINDOWS,
    capture_fmp_stage10_window,
    prepare_fmp_stage10_window_capture,
)
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.market.stage10_window_importer import Stage10HistoryWindowImporter
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"
_MIGRATION_TIME = "2026-08-12T12:00:00Z"
_CAPTURE_TIME = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)


def _symbols(required: tuple[str, ...], prefix: str, count: int) -> tuple[str, ...]:
    if len(required) > count:
        raise AssertionError("invalid test roster")
    return required + tuple(
        f"{prefix}{number:03d}" for number in range(1, count - len(required) + 1)
    )


def _universe_capture(path: str, symbols: tuple[str, ...]):
    return parse_fmp_stage10_universe_response(
        endpoint_path=path,
        body=json.dumps(
            [{"symbol": symbol, "name": f"Company {symbol}"} for symbol in symbols],
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _price_row(symbol: str, trade_date: str, *, close: int = 11) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": trade_date,
        "open": 10,
        "high": 12,
        "low": 9,
        "close": close,
        "volume": 1_000,
        "change": close - 10,
        "changePercent": close - 10,
        "vwap": 10,
    }


def _body(rows: list[dict[str, object]], *, compact: bool = True) -> bytes:
    if compact:
        return json.dumps(rows, separators=(",", ":")).encode("utf-8")
    return json.dumps(rows, indent=1, sort_keys=True).encode("utf-8")


class _Transport:
    """One injected response; it deliberately has no network capability."""

    def __init__(self, response: CapturedFmpStage10Response) -> None:
        self.response = response
        self.calls = 0

    def get(self, **_kwargs: object) -> CapturedFmpStage10Response:
        self.calls += 1
        return self.response


class Stage10HistoryWindowImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self._root = Path(self._temporary.name)
        self.store_map = explicit_store_map(self._root / "stores")
        self.registry = stage10_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        self.scope = load_stage10_market_scope(_SCOPE_PATH)
        initialize_all(self.store_map, self.registry, applied_at=_MIGRATION_TIME)
        self._legacy_importer = Stage10HistoryImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=lambda: _CAPTURE_TIME,
        )
        self._publish_scope()
        self.importer = Stage10HistoryWindowImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=lambda: _CAPTURE_TIME,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _scope_captures() -> dict[str, object]:
        return {
            "sp500_current": _universe_capture(
                FMP_STAGE10_SP500_CONSTITUENT_PATH,
                _symbols(("AAPL", "MSFT", "NVDA"), "S", 500),
            ),
            "nasdaq100_current": _universe_capture(
                FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
                _symbols(
                    (
                        "ALAB",
                        "CRWV",
                        "LITE",
                        "NBIS",
                        "RKLB",
                        "SNDK",
                        "SPCX",
                        "TER",
                        "WMT",
                    ),
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
        candidates = self._legacy_importer.prepare_universe_publications(
            self._scope_captures()  # type: ignore[arg-type]
        )
        for candidate in candidates:
            self.assertEqual(self._legacy_importer.publish_prepared(candidate).outcome, "succeeded")

    @staticmethod
    def _window_capture(
        rows: list[dict[str, object]], *, compact: bool = True
    ):
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        transport = _Transport(
            CapturedFmpStage10Response(200, "application/json", _body(rows, compact=compact))
        )
        capture = capture_fmp_stage10_window(prepared, "offline-key", transport)
        if transport.calls != 1:
            raise AssertionError("window fixture did not make exactly one injected call")
        return capture

    @staticmethod
    def _legacy_capture(rows: list[dict[str, object]]):
        return parse_fmp_stage10_price_response(
            symbol="AAPL",
            body=_body(rows, compact=False),
        )

    def test_window_capture_binds_range_scope_and_raw_lineage(self) -> None:
        capture = self._window_capture(
            [_price_row("AAPL", "1990-01-03"), _price_row("AAPL", "1990-01-02")]
        )
        before = mutation_fingerprint(self.store_map)
        prepared = self.importer.prepare_window_capture(capture)
        self.assertEqual(mutation_fingerprint(self.store_map), before)
        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
        ):
            receipt = self.importer.publish_prepared(prepared)
        self.assertEqual((receipt.outcome, receipt.written_count), ("succeeded", 3))

        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            raw = connection.execute(
                """
                SELECT request_scope_json, request_scope_sha256, response_sha256,
                       response_bytes, earliest_trade_date, latest_trade_date,
                       row_count, semantic_identity
                FROM stage10_daily_price_captures
                """
            ).fetchone()
            self.assertIsNotNone(raw)
            assert raw is not None
            request_scope = json.loads(raw["request_scope_json"])
            self.assertEqual(
                request_scope,
                {
                    "provider": "fmp",
                    "endpoint_path": "/stable/historical-price-eod/full",
                    "symbol": "AAPL",
                    "window_id": "1990-1994",
                    "start_date": "1990-01-01",
                    "end_date": "1994-12-31",
                    "from": "1990-01-01",
                    "to": "1994-12-31",
                    "interval": "daily",
                    "history_policy": "explicit_inclusive_five_year_windows_from_1990",
                    "price_variant": "fmp_full_eod_v1",
                    "currency_policy": "provider_declared_no_conversion",
                    "volume_policy": "provider_value_nonnegative_zero_allowed",
                    "scope_manifest_sha256": self.scope.manifest_sha256,
                    "target_profile_id": self.scope.target_profile_id,
                },
            )
            self.assertEqual(raw["response_sha256"], capture.raw_bytes_sha256)
            self.assertEqual(raw["response_bytes"], capture.raw_bytes)
            self.assertEqual(
                (raw["earliest_trade_date"], raw["latest_trade_date"], raw["row_count"]),
                ("1990-01-02", "1990-01-03", 2),
            )
            versions = tuple(
                connection.execute(
                    """
                    SELECT trade_date, source_row, correction_sequence
                    FROM stage10_daily_price_versions
                    ORDER BY trade_date
                    """
                )
            )
            self.assertEqual(
                tuple((row["trade_date"], row["source_row"], row["correction_sequence"]) for row in versions),
                (("1990-01-02", 2, 1), ("1990-01-03", 1, 1)),
            )
            artifact = connection.execute(
                """
                SELECT content_sha256, source_reference, request_scope_json
                FROM ingestion_artifacts WHERE run_id=?
                """,
                (receipt.run_id,),
            ).fetchone()
            self.assertEqual(
                (artifact["content_sha256"], artifact["source_reference"]),
                (capture.raw_bytes_sha256, "fmp/stable/historical-price-eod/full"),
            )
            self.assertEqual(json.loads(artifact["request_scope_json"]), request_scope)

    def test_exact_replay_is_no_write_and_unchanged_overlap_retains_raw_capture(self) -> None:
        rows = [_price_row("AAPL", "1990-01-02"), _price_row("AAPL", "1990-01-03")]
        legacy = self._legacy_importer.publish_prepared(
            self._legacy_importer.prepare_price_capture(self._legacy_capture(rows))
        )
        self.assertEqual(legacy.outcome, "succeeded")
        capture = self._window_capture(rows)
        first = self.importer.publish_prepared(self.importer.prepare_window_capture(capture))
        # The window evidence is immutable and retained even though its values
        # overlap exactly with the prior full-history canonical facts.
        self.assertEqual((first.outcome, first.written_count), ("succeeded", 1))
        before_replay = mutation_fingerprint(self.store_map)
        replay = self.importer.publish_prepared(self.importer.prepare_window_capture(capture))
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.store_map), before_replay)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_captures").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_versions").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_prices").fetchone()[0],
                2,
            )

    def test_changed_window_overlap_appends_one_correction_and_keeps_raw_captures(self) -> None:
        initial = self._window_capture(
            [_price_row("AAPL", "1990-01-02"), _price_row("AAPL", "1990-01-03")]
        )
        first = self.importer.publish_prepared(self.importer.prepare_window_capture(initial))
        self.assertEqual((first.outcome, first.written_count), ("succeeded", 3))
        corrected = self._window_capture(
            [
                _price_row("AAPL", "1990-01-02"),
                _price_row("AAPL", "1990-01-03", close=12),
            ]
        )
        second = self.importer.publish_prepared(self.importer.prepare_window_capture(corrected))
        self.assertEqual((second.outcome, second.written_count), ("succeeded", 2))
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_captures").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage10_daily_price_versions").fetchone()[0],
                3,
            )
            versions = tuple(
                connection.execute(
                    """
                    SELECT version_id, correction_sequence, supersedes_version_id,
                           close_value, capture_id
                    FROM stage10_daily_price_versions AS version
                    JOIN stage10_instruments AS instrument
                      ON instrument.instrument_id=version.instrument_id
                    WHERE instrument.provider_symbol='AAPL' AND trade_date='1990-01-03'
                    ORDER BY correction_sequence
                    """
                )
            )
            self.assertEqual(len(versions), 2)
            self.assertEqual(
                tuple((row["correction_sequence"], row["close_value"]) for row in versions),
                ((1, "11"), (2, "12")),
            )
            self.assertEqual(versions[1]["supersedes_version_id"], versions[0]["version_id"])
            self.assertNotEqual(versions[0]["capture_id"], versions[1]["capture_id"])
            current = connection.execute(
                """
                SELECT current.current_version_id
                FROM stage10_daily_prices AS current
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=current.instrument_id
                WHERE instrument.provider_symbol='AAPL' AND current.trade_date='1990-01-03'
                """
            ).fetchone()
            self.assertEqual(current["current_version_id"], versions[1]["version_id"])

    def test_empty_unpublished_and_foreign_candidates_fail_without_write(self) -> None:
        empty = self._window_capture([])
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.prepare_window_capture(empty)
        self.assertEqual(mutation_fingerprint(self.store_map), before)

        foreign_prepared = prepare_fmp_stage10_window_capture(
            "ZZZZ", STAGE10_HISTORY_WINDOWS[0]
        )
        foreign_capture = capture_fmp_stage10_window(
            foreign_prepared,
            "offline-key",
            _Transport(
                CapturedFmpStage10Response(
                    200,
                    "application/json",
                    _body([_price_row("ZZZZ", "1990-01-02")]),
                )
            ),
        )
        with self.assertRaises(ValidationError):
            self.importer.prepare_window_capture(foreign_capture)
        self.assertEqual(mutation_fingerprint(self.store_map), before)

        candidate = self.importer.prepare_window_capture(
            self._window_capture([_price_row("AAPL", "1990-01-02")])
        )
        other = Stage10HistoryWindowImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=lambda: _CAPTURE_TIME,
        )
        with self.assertRaises(ValidationError):
            other.publish_prepared(candidate)
        self.assertEqual(mutation_fingerprint(self.store_map), before)


if __name__ == "__main__":
    unittest.main()
