from __future__ import annotations

import shutil
from dataclasses import replace
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import quant_data.market.stage12_incremental as stage12_incremental
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.stage12_incremental import (
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
)
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, StoreWriteLock, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STAGE12A_SCOPE = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
_STAGE12B_SCOPE = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "stage12b" / "daily_price_complete.json"
_TIME = "2026-08-15T01:00:00Z"


class Stage12BIncrementalCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at="2026-08-15T00:00:00Z")
        self._seed_instrument()
        self.stage12a = load_stage12_market_v1_scope(_STAGE12A_SCOPE)
        self.scope = load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE)
        self.collector = Stage12BIncrementalCollector(
            fixture_root=self.root,
            market_store=self.stores.market,
            scope=self.scope,
            stage12a_scope=self.stage12a,
            stage12a_scope_source=_STAGE12A_SCOPE,
        )
        self.body = _FIXTURE.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_instrument(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "stage12b_seed_run",
                    "market.stage10.instruments",
                    "a" * 64,
                    "fixture.seed",
                    "{}",
                    _TIME,
                    "fixture",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type, display_name,
                    exchange_code, currency_segment, first_trade_date,
                    identity_seed_sha256, captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', 'AAPL', 'equity', 'AAPL fixture', 'XNAS',
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                ("stage12b_aapl", "b" * 64, _TIME, "stage12b_seed_run"),
            )

    @staticmethod
    def _request(*, captured_at: str = _TIME, from_date: str = "2026-08-10") -> Stage12BFixtureRequest:
        return Stage12BFixtureRequest(
            symbol="AAPL",
            from_date=from_date,
            to_date="2026-08-12",
            session_dates=("2026-08-10", "2026-08-11", "2026-08-12"),
            captured_at=captured_at,
        )

    @staticmethod
    def _response(
        body: bytes, *, elapsed_seconds: int | float = 1
    ) -> Stage12BFixtureResponse:
        return Stage12BFixtureResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=body,
            elapsed_seconds=elapsed_seconds,
        )

    def _counts(self) -> tuple[int, int, int]:
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "stage10_daily_price_captures",
                    "stage10_daily_price_versions",
                    "stage10_daily_prices",
                )
            )

    @staticmethod
    def _counts_at(path: Path) -> tuple[int, int, int]:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "stage10_daily_price_captures",
                    "stage10_daily_price_versions",
                    "stage10_daily_prices",
                )
            )

    def test_prepare_is_parse_only_and_reordered_semantic_replay_is_zero_write(self) -> None:
        before_prepare = mutation_fingerprint(self.stores)
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        self.assertEqual(mutation_fingerprint(self.stores), before_prepare)
        first = self.collector.publish(prepared)
        self.assertEqual((first.outcome, first.written_versions), ("published", 3))
        self.assertEqual(self._counts(), (1, 3, 3))

        reordered = json.loads(self.body)
        reordered.reverse()
        replay_before = mutation_fingerprint(self.stores)
        replay = self.collector.prepare(
            self._request(captured_at="2026-08-15T02:00:00+00:00"),
            self._response(json.dumps(reordered, separators=(",", ":")).encode("utf-8")),
        )
        receipt = self.collector.publish(replay)
        self.assertEqual((receipt.outcome, receipt.written_versions), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), replay_before)
        self.assertEqual(self._counts(), (1, 3, 3))

    def test_single_session_request_publishes_one_row(self) -> None:
        session_date = "2026-08-12"
        request = Stage12BFixtureRequest(
            symbol="AAPL",
            from_date=session_date,
            to_date=session_date,
            session_dates=(session_date,),
            captured_at=_TIME,
        )
        row = next(
            item for item in json.loads(self.body) if item["date"] == session_date
        )
        body = json.dumps([row], separators=(",", ":")).encode("utf-8")

        receipt = self.collector.publish(
            self.collector.prepare(request, self._response(body))
        )

        self.assertEqual((receipt.outcome, receipt.written_versions), ("published", 1))
        self.assertEqual(self._counts(), (1, 1, 1))

    def test_correction_appends_one_version_and_moves_current_pointer(self) -> None:
        self.collector.publish(self.collector.prepare(self._request(), self._response(self.body)))
        corrected = json.loads(self.body)
        corrected[1].update({"high": 109, "close": 108, "change": 7, "changePercent": 7, "vwap": 106})
        receipt = self.collector.publish(
            self.collector.prepare(
                self._request(captured_at="2026-08-15T03:00:00Z"),
                self._response(json.dumps(corrected, separators=(",", ":")).encode("utf-8")),
            )
        )
        self.assertEqual((receipt.outcome, receipt.written_versions), ("published", 1))
        self.assertEqual(self._counts(), (2, 4, 3))
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            row = connection.execute(
                """
                SELECT version.correction_sequence, version.close_value
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id = current.current_version_id
                WHERE current.trade_date = '2026-08-11'
                """
            ).fetchone()
        self.assertEqual((int(row[0]), str(row[1])), (2, "108"))


    def test_changed_key_requires_strictly_later_capture_and_availability(self) -> None:
        def corrected_body(close_value: int) -> bytes:
            rows = json.loads(self.body)
            open_value = int(rows[1]["open"])
            rows[1].update(
                {
                    "high": close_value + 1,
                    "close": close_value,
                    "change": close_value - open_value,
                    "changePercent": close_value - open_value,
                    "vwap": close_value - 2,
                }
            )
            return json.dumps(rows, separators=(",", ":")).encode("utf-8")

        self.collector.publish(
            self.collector.prepare(self._request(), self._response(self.body))
        )
        self.collector.publish(
            self.collector.prepare(
                self._request(captured_at="2026-08-15T03:00:00Z"),
                self._response(corrected_body(108)),
            )
        )
        for captured_at, close_value in (
            ("2026-08-15T03:00:00Z", 109),
            ("2026-08-15T02:00:00Z", 110),
        ):
            with self.subTest(captured_at=captured_at):
                before = mutation_fingerprint(self.stores)
                prepared = self.collector.prepare(
                    self._request(captured_at=captured_at),
                    self._response(corrected_body(close_value)),
                )
                with self.assertRaises(ConflictError):
                    self.collector.publish(prepared)
                self.assertEqual(mutation_fingerprint(self.stores), before)

        later = self.collector.publish(
            self.collector.prepare(
                self._request(captured_at="2026-08-15T04:00:00Z"),
                self._response(corrected_body(109)),
            )
        )
        self.assertEqual((later.outcome, later.written_versions), ("published", 1))
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            rows = tuple(
                connection.execute(
                    """
                    SELECT correction_sequence, captured_at, available_at, close_value
                    FROM stage10_daily_price_versions
                    WHERE instrument_id = 'stage12b_aapl'
                      AND trade_date = '2026-08-11'
                    ORDER BY correction_sequence
                    """
                )
            )
        self.assertEqual(
            tuple((int(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows),
            (
                (1, "2026-08-15T01:00:00.000000Z", "2026-08-15T01:00:00.000000Z", "103"),
                (2, "2026-08-15T03:00:00.000000Z", "2026-08-15T03:00:00.000000Z", "108"),
                (3, "2026-08-15T04:00:00.000000Z", "2026-08-15T04:00:00.000000Z", "109"),
            ),
        )

    def test_elapsed_time_bound_rejects_nonfinite_negative_and_over_limit(self) -> None:
        before = mutation_fingerprint(self.stores)
        for elapsed_seconds, expected in (
            (float("nan"), ValidationError),
            (-1, ValidationError),
            (31, ResourceLimitError),
        ):
            with self.subTest(elapsed_seconds=elapsed_seconds):
                with self.assertRaises(expected):
                    self.collector.prepare(
                        self._request(),
                        self._response(self.body, elapsed_seconds=elapsed_seconds),
                    )
                self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_exported_collector_revalidates_the_closed_scope_object(self) -> None:
        forged = replace(
            self.scope,
            collector=replace(self.scope.collector, max_seconds=999),
        )
        before = mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector(
                fixture_root=self.root,
                market_store=self.stores.market,
                scope=forged,
                stage12a_scope=self.stage12a,
                stage12a_scope_source=_STAGE12A_SCOPE,
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)


    def test_exported_collector_rejects_nonfixture_symlink_and_hardlink_paths(self) -> None:
        before = mutation_fingerprint(self.stores)
        common = {
            "scope": self.scope,
            "stage12a_scope": self.stage12a,
            "stage12a_scope_source": _STAGE12A_SCOPE,
        }
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector(
                fixture_root=PROJECT_ROOT,
                market_store=self.stores.market,
                **common,
            )
        approved = self.root / "approved"
        approved.mkdir()
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector(
                fixture_root=approved,
                market_store=self.stores.market,
                **common,
            )
        alias = approved / "market.sqlite"
        alias.symlink_to(self.stores.market)
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector(
                fixture_root=approved,
                market_store=alias,
                **common,
            )
        hard_alias = approved / "hard-linked-market.sqlite"
        hard_alias.hardlink_to(self.stores.market)
        with self.assertRaises(ConflictError):
            Stage12BIncrementalCollector(
                fixture_root=approved,
                market_store=hard_alias,
                **common,
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_prepared_publication_rejects_same_path_inode_replacement(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        original_inode = self.stores.market.stat().st_ino
        replacement = self.root / "replacement.sqlite"
        shutil.copy2(self.stores.market, replacement)
        replacement.replace(self.stores.market)
        self.assertNotEqual(self.stores.market.stat().st_ino, original_inode)
        with self.assertRaises(ConflictError):
            self.collector.publish(prepared)
        self.assertEqual(self._counts(), (0, 0, 0))

    def test_prepared_publication_rechecks_identity_after_lock_acquisition(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        market_store = self.stores.market
        parked_original = self.root / "parked-original.sqlite"
        replacement = self.root / "lock-time-replacement.sqlite"
        shutil.copy2(market_store, replacement)

        class SwapOnEnterLock:
            def __init__(inner_self, path: Path, **kwargs: object) -> None:
                inner_self._lock = StoreWriteLock(path, **kwargs)

            def __enter__(inner_self) -> StoreWriteLock:
                acquired = inner_self._lock.__enter__()
                market_store.replace(parked_original)
                replacement.replace(market_store)
                return acquired

            def __exit__(
                inner_self,
                exc_type: object,
                exc: object,
                traceback: object,
            ) -> object:
                return inner_self._lock.__exit__(exc_type, exc, traceback)

        with mock.patch(
            "quant_data.market.stage12_incremental.StoreWriteLock",
            SwapOnEnterLock,
        ), mock.patch(
            "quant_data.market.stage12_incremental.sqlite3.connect",
            side_effect=AssertionError("SQLite opened before the under-lock identity check"),
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)

        self.assertEqual(self._counts_at(parked_original), (0, 0, 0))
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))


    def test_parse_failure_empty_and_rebound_raw_bytes_leave_store_unchanged(self) -> None:
        before = mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            self.collector.prepare(
                self._request(),
                Stage12BFixtureResponse(status=500, media_type="application/json", body=self.body, elapsed_seconds=1),
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)
        empty = self.collector.prepare(self._request(), self._response(b"[]"))
        self.assertEqual(self.collector.publish(empty).outcome, "empty")
        self.assertEqual(mutation_fingerprint(self.stores), before)

        self.collector.publish(self.collector.prepare(self._request(), self._response(self.body)))
        rebound_before = mutation_fingerprint(self.stores)
        rebound = self.collector.prepare(
            self._request(from_date="2026-08-09"),
            self._response(self.body),
        )
        with self.assertRaises(ConflictError):
            self.collector.publish(rebound)
        self.assertEqual(mutation_fingerprint(self.stores), rebound_before)

    def test_canonical_market_close_publishes_scheduled_identity_outside_stage12a(self) -> None:
        self.assertNotIn("IWM", {item.symbol for item in self.stage12a.roster})
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "stage12e_iwm_seed_run",
                    "market.stage10.instruments",
                    "c" * 64,
                    "fixture.seed",
                    "{}",
                    _TIME,
                    "fixture",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type, display_name,
                    exchange_code, currency_segment, first_trade_date,
                    identity_seed_sha256, captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', 'IWM', 'etf', 'IWM fixture', 'ARCX',
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                ("stage12e_iwm", "d" * 64, _TIME, "stage12e_iwm_seed_run"),
            )

        scheduled = {
            "AAPL": ("stage12b_aapl", "equity"),
            "IWM": ("stage12e_iwm", "etf"),
        }
        universe_sha256 = stage12_incremental._sha256_json(
            [
                {
                    "asset_type": "equity",
                    "instrument_id": "stage12b_aapl",
                    "symbol": "AAPL",
                },
                {
                    "asset_type": "etf",
                    "instrument_id": "stage12e_iwm",
                    "symbol": "IWM",
                },
            ]
        )
        common = {
            "scope": self.scope,
            "stage12a_scope": self.stage12a,
            "schedule_authority_sha256": "e" * 64,
            "scheduled_instruments": scheduled,
            "scheduled_code_version": "stage12e-test",
        }
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector._for_canonical_market_close(
                **common,
                scheduled_universe_sha256="0" * 64,
            )

        with mock.patch.object(
            stage12_incremental, "_CANONICAL_PROJECT_ROOT", self.root
        ), mock.patch.object(
            stage12_incremental, "_CANONICAL_MARKET_STORE", self.stores.market
        ):
            collector = Stage12BIncrementalCollector._for_canonical_market_close(
                **common,
                scheduled_universe_sha256=universe_sha256,
            )
        rows = json.loads(self.body)
        for row in rows:
            row["symbol"] = "IWM"
        request = Stage12BFixtureRequest(
            symbol="IWM",
            from_date="2026-08-10",
            to_date="2026-08-12",
            session_dates=("2026-08-10", "2026-08-11", "2026-08-12"),
            captured_at=_TIME,
        )
        first = collector.publish(
            collector.prepare(
                request,
                self._response(json.dumps(rows, separators=(",", ":")).encode("utf-8")),
            )
        )
        self.assertEqual((first.outcome, first.written_versions), ("published", 3))

        rows.reverse()
        replay = collector.publish(
            collector.prepare(
                Stage12BFixtureRequest(
                    symbol="IWM",
                    from_date="2026-08-10",
                    to_date="2026-08-12",
                    session_dates=("2026-08-10", "2026-08-11", "2026-08-12"),
                    captured_at="2026-08-15T02:00:00Z",
                ),
                self._response(json.dumps(rows, separators=(",", ":")).encode("utf-8")),
            )
        )
        self.assertEqual((replay.outcome, replay.written_versions), ("unchanged", 0))
        self.assertEqual(self._counts(), (1, 3, 3))

        with read_connection(self.stores, StoreRole.MARKET) as connection:
            provenance = connection.execute(
                """
                SELECT run.command, run.code_version, capture.scope_manifest_sha256,
                       capture.request_scope_json, artifact.source_reference
                FROM stage10_daily_price_captures AS capture
                JOIN ingestion_runs AS run ON run.run_id = capture.run_id
                JOIN ingestion_artifacts AS artifact ON artifact.artifact_id = capture.artifact_id
                WHERE capture.capture_id = ?
                """,
                (first.capture_id,),
            ).fetchone()
        assert provenance is not None
        scope = json.loads(str(provenance["request_scope_json"]))
        self.assertEqual(
            (
                str(provenance["command"]),
                str(provenance["code_version"]),
                str(provenance["scope_manifest_sha256"]),
                str(provenance["source_reference"]),
            ),
            (
                "stage12e.market_close",
                "stage12e-test",
                universe_sha256,
                "fmp.scheduled.historical-price-eod.full",
            ),
        )
        self.assertEqual(
            (
                scope["scheduled_instrument_id"],
                scope["scheduled_asset_type"],
                scope["scheduled_universe_sha256"],
            ),
            ("stage12e_iwm", "etf", universe_sha256),
        )

    def test_commit_failure_rolls_back_all_fixture_publication_rows(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        before = mutation_fingerprint(self.stores)
        with mock.patch.object(self.collector, "_commit", side_effect=sqlite3.OperationalError("injected")):
            with self.assertRaises(sqlite3.OperationalError):
                self.collector.publish(prepared)
        self.assertEqual(mutation_fingerprint(self.stores), before)
        self.assertEqual(self._counts(), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
