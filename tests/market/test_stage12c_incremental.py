from __future__ import annotations

import inspect
import json
import shutil
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.market.stage12c_incremental import (
    Stage12CIncrementalCollector,
    Stage12CLiveRequest,
    Stage12CLiveResponse,
)
from quant_data.market.stage12c_scope import load_stage12c_market_gap_v1_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, StoreWriteLock, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STAGE12A_SCOPE = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
_STAGE12B_SCOPE = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
_STAGE12C_SCOPE = PROJECT_ROOT / "config" / "stage12c_market_gap_v1_scope.json"
_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "stage12c" / "daily_price_complete.json"
_TIME = "2026-08-15T01:00:00Z"


class Stage12CIncrementalCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.stores = explicit_store_map(self.root / "data")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at="2026-08-15T00:00:00Z")
        self._seed_instrument()
        self.stage12a = load_stage12_market_v1_scope(_STAGE12A_SCOPE)
        self.stage12b = load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE)
        self.scope = load_stage12c_market_gap_v1_scope(_STAGE12C_SCOPE)
        self.collector = Stage12CIncrementalCollector(
            project_root=self.root,
            market_store=self.stores.market,
            scope=self.scope,
            stage12a_scope=self.stage12a,
            stage12b_scope=self.stage12b,
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
                    "stage12c_seed_run",
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
                ("stage12c_aapl", "b" * 64, _TIME, "stage12c_seed_run"),
            )

    @staticmethod
    def _request(*, symbol: str = "AAPL") -> Stage12CLiveRequest:
        return Stage12CLiveRequest(
            symbol=symbol,
            from_date="2026-08-13",
            to_date="2026-08-14",
        )

    @staticmethod
    def _response(body: bytes, *, captured_at: str = _TIME, elapsed_seconds: int | float = 1, status: int = 200, media_type: str = "application/json; charset=utf-8") -> Stage12CLiveResponse:
        return Stage12CLiveResponse(
            status=status,
            media_type=media_type,
            body=body,
            captured_at=captured_at,
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
        with mock.patch(
            "quant_data.market.stage12c_incremental.sqlite3.connect",
            side_effect=AssertionError("prepare opened SQLite"),
        ), mock.patch(
            "quant_data.market.stage12c_incremental.StoreWriteLock",
            side_effect=AssertionError("prepare acquired a lock"),
        ):
            prepared = self.collector.prepare(self._request(), self._response(self.body))
        self.assertEqual(mutation_fingerprint(self.stores), before_prepare)
        first = self.collector.publish(prepared)
        self.assertEqual((first.outcome, first.written_versions), ("published", 2))
        self.assertEqual(self._counts(), (1, 2, 2))

        reordered = json.loads(self.body)
        reordered.reverse()
        replay_before = mutation_fingerprint(self.stores)
        replay = self.collector.prepare(
            self._request(),
            self._response(json.dumps(reordered, separators=(",", ":")).encode("utf-8"), captured_at="2026-08-15T02:00:00+00:00"),
        )
        receipt = self.collector.publish(replay)
        self.assertEqual((receipt.outcome, receipt.written_versions), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), replay_before)
        self.assertEqual(self._counts(), (1, 2, 2))

    def test_correction_appends_and_changed_rows_require_strictly_later_capture(self) -> None:
        self.collector.publish(self.collector.prepare(self._request(), self._response(self.body)))
        corrected = json.loads(self.body)
        corrected[1].update({"high": 209, "close": 207, "change": 6, "changePercent": 3, "vwap": 205})
        corrected_body = json.dumps(corrected, separators=(",", ":")).encode("utf-8")
        receipt = self.collector.publish(
            self.collector.prepare(
                self._request(),
                self._response(corrected_body, captured_at="2026-08-15T03:00:00Z"),
            )
        )
        self.assertEqual((receipt.outcome, receipt.written_versions), ("published", 1))
        self.assertEqual(self._counts(), (2, 3, 2))
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            row = connection.execute(
                """
                SELECT version.correction_sequence, version.close_value
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id = current.current_version_id
                WHERE current.trade_date = '2026-08-14'
                """
            ).fetchone()
        self.assertEqual((int(row[0]), str(row[1])), (2, "207"))

        before = mutation_fingerprint(self.stores)
        backdated = json.loads(self.body)
        backdated[1].update(dict(high=210, close=208, change=7, changePercent=4, vwap=206))
        with self.assertRaises(ConflictError):
            self.collector.publish(
                self.collector.prepare(
                    self._request(),
                    self._response(json.dumps(backdated, separators=(',', ':')).encode('utf-8'), captured_at='2026-08-15T03:00:00Z'),
                )
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_empty_partial_terminal_and_resource_failures_are_nonpublishable_and_no_write(self) -> None:
        before = mutation_fingerprint(self.stores)
        partial = json.loads(self.body)[:1]
        out_of_range = json.loads(self.body)
        out_of_range[1]["date"] = "2026-08-15"
        cases = (
            self._response(b"[]"),
            self._response(json.dumps(partial).encode("utf-8")),
            self._response(json.dumps(out_of_range).encode("utf-8")),
            self._response(self.body, status=404),
            self._response(self.body, media_type="text/plain"),
            self._response(b"not-json"),
            self._response(self.body, captured_at="2026-08-15T01:00:00"),
            self._response(self.body, elapsed_seconds=46),
        )
        for response in cases:
            with self.subTest(response=response.status, bytes=len(response.body)):
                with self.assertRaises((ValidationError, ResourceLimitError)):
                    self.collector.prepare(self._request(), response)
                self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_exact_window_roster_and_forged_scope_are_rejected_before_publication(self) -> None:
        before = mutation_fingerprint(self.stores)
        invalid_requests = (
            replace(self._request(), from_date="2026-08-12"),
            replace(self._request(), to_date="2026-08-15"),
            replace(self._request(), symbol="IWM"),
            replace(self._request(), symbol="UNKNOWN"),
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(ValidationError):
                    self.collector.prepare(request, self._response(self.body))
                self.assertEqual(mutation_fingerprint(self.stores), before)

        forged = replace(self.scope, collector=replace(self.scope.collector, max_rows=3))
        with self.assertRaises(ValidationError):
            Stage12CIncrementalCollector(
                project_root=self.root,
                market_store=self.stores.market,
                scope=forged,
                stage12a_scope=self.stage12a,
                stage12b_scope=self.stage12b,
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_public_constructor_is_fixture_only_and_live_factory_has_no_path_parameters(self) -> None:
        self.assertEqual(self.collector.market_store, self.stores.market)
        signature = inspect.signature(Stage12CIncrementalCollector._for_canonical_live)
        self.assertEqual(
            tuple(signature.parameters),
            ("scope", "stage12a_scope", "stage12b_scope"),
        )

        retained = Path(self.scope.source_target.retained_source_literal)
        rejected_targets = (
            (
                "canonical",
                PROJECT_ROOT,
                PROJECT_ROOT / self.scope.source_target.project_store_path,
            ),
            (
                "former_default",
                PROJECT_ROOT,
                PROJECT_ROOT / "data" / "market_data.sqlite",
            ),
            ("retained", retained.parent.parent, retained),
            (
                "system_temp_root",
                Path(tempfile.gettempdir()),
                Path(tempfile.gettempdir()) / "data" / "market.sqlite",
            ),
        )
        with mock.patch(
            "quant_data.market.stage12c_incremental.sqlite3.connect",
            side_effect=AssertionError("public construction opened SQLite"),
        ) as connect:
            for label, project_root, market_store in rejected_targets:
                with self.subTest(target=label):
                    with self.assertRaises(ValidationError):
                        Stage12CIncrementalCollector(
                            project_root=project_root,
                            market_store=market_store,
                            scope=self.scope,
                            stage12a_scope=self.stage12a,
                            stage12b_scope=self.stage12b,
                        )
        connect.assert_not_called()

    def test_project_local_target_confinement_rejects_default_fallback_alias_and_hardlink(self) -> None:
        before = mutation_fingerprint(self.stores)
        alternate = self.root / "data" / "alternate.sqlite"
        alternate.touch()
        with self.assertRaises(ValidationError):
            Stage12CIncrementalCollector(
                project_root=self.root,
                market_store=alternate,
                scope=self.scope,
                stage12a_scope=self.stage12a,
                stage12b_scope=self.stage12b,
            )

        alias_root = self.root.parent / "alias-project"
        (alias_root / "data").mkdir(parents=True)
        symlink_alias = alias_root / "data" / "market.sqlite"
        symlink_alias.symlink_to(self.stores.market)
        with self.assertRaises(ValidationError):
            Stage12CIncrementalCollector(
                project_root=alias_root,
                market_store=symlink_alias,
                scope=self.scope,
                stage12a_scope=self.stage12a,
                stage12b_scope=self.stage12b,
            )

        hard_root = self.root.parent / "hard-project"
        (hard_root / "data").mkdir(parents=True)
        hard_alias = hard_root / "data" / "market.sqlite"
        hard_alias.hardlink_to(self.stores.market)
        with self.assertRaises(ConflictError):
            Stage12CIncrementalCollector(
                project_root=hard_root,
                market_store=hard_alias,
                scope=self.scope,
                stage12a_scope=self.stage12a,
                stage12b_scope=self.stage12b,
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_prepared_publication_rejects_inode_replacement_before_and_after_lock(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        original_inode = self.stores.market.stat().st_ino
        replacement = self.root / "data" / "replacement.sqlite"
        shutil.copy2(self.stores.market, replacement)
        replacement.replace(self.stores.market)
        self.assertNotEqual(self.stores.market.stat().st_ino, original_inode)
        with self.assertRaises(ConflictError):
            self.collector.publish(prepared)
        self.assertEqual(self._counts(), (0, 0, 0))

        # Recreate an isolated collector/store for the under-lock TOCTOU check.
        self.temporary.cleanup()
        self.setUp()
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        market_store = self.stores.market
        parked_original = self.root / "data" / "parked-original.sqlite"
        lock_replacement = self.root / "data" / "lock-time-replacement.sqlite"
        shutil.copy2(market_store, lock_replacement)

        class SwapOnEnterLock:
            def __init__(inner_self, path: Path, **kwargs: object) -> None:
                inner_self._lock = StoreWriteLock(path, **kwargs)

            def __enter__(inner_self) -> StoreWriteLock:
                acquired = inner_self._lock.__enter__()
                market_store.replace(parked_original)
                lock_replacement.replace(market_store)
                return acquired

            def __exit__(inner_self, exc_type: object, exc: object, traceback: object) -> object:
                return inner_self._lock.__exit__(exc_type, exc, traceback)

        with mock.patch(
            "quant_data.market.stage12c_incremental.StoreWriteLock",
            SwapOnEnterLock,
        ), mock.patch(
            "quant_data.market.stage12c_incremental.sqlite3.connect",
            side_effect=AssertionError("SQLite opened before the under-lock identity check"),
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)
        self.assertEqual(self._counts_at(parked_original), (0, 0, 0))
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))

    def test_connect_replacement_cannot_publish_to_the_replacement_handle(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        market_store = self.stores.market
        parked_original = self.root / "data" / "connect-parked-original.sqlite"
        replacement = self.root / "data" / "connect-replacement.sqlite"
        shutil.copy2(market_store, replacement)
        real_connect = sqlite3.connect
        opened_connections: list[sqlite3.Connection] = []
        swapped = False

        def replace_before_real_open(
            database: object,
            *args: object,
            **kwargs: object,
        ) -> sqlite3.Connection:
            nonlocal swapped
            if Path(database) == market_store:
                self.assertFalse(swapped)
                market_store.replace(parked_original)
                replacement.replace(market_store)
                swapped = True
            connection = real_connect(database, *args, **kwargs)  # type: ignore[arg-type]
            opened_connections.append(connection)
            return connection

        with mock.patch(
            "quant_data.market.stage12c_incremental.sqlite3.connect",
            side_effect=replace_before_real_open,
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)

        self.assertTrue(swapped)
        self.assertEqual(len(opened_connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened_connections[0].execute("SELECT 1")
        self.assertEqual(self._counts_at(parked_original), (0, 0, 0))
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))

    def test_post_connect_pre_transaction_replacement_rolls_back_without_wrong_store_write(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        market_store = self.stores.market
        parked_original = self.root / "data" / "post-connect-parked-original.sqlite"
        replacement = self.root / "data" / "post-connect-replacement.sqlite"
        shutil.copy2(market_store, replacement)
        original_before_transaction = self.collector._before_transaction
        swapped = False

        def replace_then_recheck(state: object, descriptor: object) -> None:
            nonlocal swapped
            self.assertFalse(swapped)
            market_store.replace(parked_original)
            replacement.replace(market_store)
            swapped = True
            original_before_transaction(state, descriptor)  # type: ignore[arg-type]

        with mock.patch.object(
            self.collector,
            "_before_transaction",
            side_effect=replace_then_recheck,
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)

        self.assertTrue(swapped)
        self.assertEqual(self._counts_at(parked_original), (0, 0, 0))
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))

    def test_post_commit_replacement_cannot_handoff_wal_facts_to_replacement(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        market_store = self.stores.market
        parked_original = self.root / "data" / "post-commit-parked-original.sqlite"
        replacement = self.root / "data" / "post-commit-replacement.sqlite"
        shutil.copy2(market_store, replacement)
        original_after_commit = self.collector._after_commit
        swapped = False

        def replace_then_recheck(state: object, descriptor: object) -> None:
            nonlocal swapped
            self.assertFalse(swapped)
            market_store.replace(parked_original)
            replacement.replace(market_store)
            swapped = True
            original_after_commit(state, descriptor)  # type: ignore[arg-type]

        with mock.patch.object(
            self.collector,
            "_after_commit",
            side_effect=replace_then_recheck,
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)

        self.assertTrue(swapped)
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))
        self.assertEqual(self._counts_at(parked_original), (1, 2, 2))
        with self.assertRaises(ConflictError):
            self.collector.publish(prepared)
        self.assertEqual(self._counts_at(market_store), (0, 0, 0))
        self.assertEqual(self._counts_at(parked_original), (1, 2, 2))

    def test_checkpoint_failure_has_no_success_and_replay_is_semantic_unchanged(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        with mock.patch.object(
            self.collector,
            "_checkpoint_committed_publication",
            side_effect=ConflictError("fixture checkpoint busy"),
        ):
            with self.assertRaises(ConflictError):
                self.collector.publish(prepared)

        self.assertEqual(self._counts(), (1, 2, 2))
        replay = self.collector.publish(prepared)
        self.assertEqual((replay.outcome, replay.written_versions), ("unchanged", 0))
        self.assertEqual(self._counts(), (1, 2, 2))



    def test_commit_failure_rolls_back_the_entire_project_local_publication(self) -> None:
        prepared = self.collector.prepare(self._request(), self._response(self.body))
        before = mutation_fingerprint(self.stores)
        with mock.patch.object(self.collector, "_commit", side_effect=sqlite3.OperationalError("injected")):
            with self.assertRaises(sqlite3.OperationalError):
                self.collector.publish(prepared)
        self.assertEqual(mutation_fingerprint(self.stores), before)
        self.assertEqual(self._counts(), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
