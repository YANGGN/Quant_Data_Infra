from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.news.fmp_stock_latest import CapturedFmpStockLatestResponse
from quant_data.operations import fmp_stock_latest_news_backfill as operation
from quant_data.registry import load_registry, stage4_registry_profile
from quant_data.migrations import migrate_store
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
FIXTURE_PATH = PROJECT_ROOT / "tests/fixtures/fmp_stock_latest/stock_latest_page0.json"
FIXED_TIME = datetime(2026, 8, 14, 15, 30, tzinfo=timezone.utc)


class _Transport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedFmpStockLatestResponse:
        self.calls += 1
        if kwargs != {
            "path": "/stable/news/stock-latest",
            "query": {"page": "0", "limit": "1000"},
            "headers": {"apikey": "offline-key", "Accept": "application/json"},
            "timeout_seconds": 60,
            "max_bytes": 64 * 1024 * 1024,
        }:
            raise AssertionError("operation widened the approved FMP request")
        return CapturedFmpStockLatestResponse(
            200,
            "application/json; charset=UTF-8",
            self.body,
        )


class FmpStockLatestOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.target = self.root / "data" / "news.sqlite"
        self.target.parent.mkdir()
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stores(self) -> StoreMap:
        return StoreMap.four_explicit(
            market=self.root / "data" / "market.sqlite",
            macro=self.root / "data" / "macro_data.sqlite",
            company=self.root / "data" / "company_data.sqlite",
            news=self.target,
        )

    def _exact_target_patches(self) -> object:
        return mock.patch.multiple(
            operation,
            APPROVED_PROJECT_ROOT=self.root,
            APPROVED_NEWS_TARGET=self.target,
        )

    def _target_snapshot(self) -> tuple[bytes, int, int, int]:
        details = self.target.stat()
        return (
            self.target.read_bytes(),
            details.st_mode,
            details.st_size,
            details.st_mtime_ns,
        )

    def _initialize_pre_0005_target(self) -> StoreMap:
        stores = self._stores()
        applied = migrate_store(
            stores,
            stage4_registry_profile(self.registry),
            StoreRole.NEWS,
            applied_at="2026-08-14T15:30:00Z",
        )
        self.assertEqual(applied[-1], "news:0004_search_index")
        return stores

    def test_missing_credential_creates_no_target_or_intent(self) -> None:
        with mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=self.root,
                    target=self.target,
                    environment={},
                    transport=_Transport(FIXTURE_PATH.read_bytes()),
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_invalid_credential_is_preflight_and_creates_no_target_or_intent(self) -> None:
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=self.root,
                    target=self.target,
                    environment={"FMP_API_KEY": "invalid key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertEqual(transport.calls, 0)
        self.assertFalse(self.target.exists())

    def test_exact_target_migrates_and_registers_only_news_before_one_request(self) -> None:
        self._initialize_pre_0005_target()
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with mock.patch.object(operation, "APPROVED_PROJECT_ROOT", self.root), mock.patch.object(
            operation, "APPROVED_NEWS_TARGET", self.target
        ), mock.patch.object(operation, "load_registry", return_value=self.registry) as load:
            result = operation.populate_fmp_stock_latest_news(
                project_root=str(self.root),
                target=str(self.target),
                environment={"FMP_API_KEY": "offline-key"},
                transport=transport,
                clock=lambda: FIXED_TIME,
            )
        self.assertEqual((result.outcome, result.article_count), ("succeeded", 3))
        self.assertEqual(transport.calls, 1)
        load.assert_called_once()
        self.assertTrue(self.target.is_file())
        self.assertFalse((self.root / "data" / "market.sqlite").exists())
        self.assertFalse((self.root / "data" / "macro_data.sqlite").exists())
        self.assertFalse((self.root / "data" / "company_data.sqlite").exists())
        stores = self._stores()
        with read_connection(stores, StoreRole.NEWS) as connection:
            applied = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT migration_id FROM schema_migrations ORDER BY ordinal"
                )
            )
            self.assertEqual(applied[-1], "news:0005_fmp_stock_latest")
            self.assertEqual(
                connection.execute("SELECT count(*) FROM fmp_stock_latest_attempts").fetchone()[0],
                1,
            )

    def test_missing_existing_target_is_rejected_before_migration_or_transport(self) -> None:
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(
            operation, "load_registry", return_value=self.registry
        ), mock.patch.object(operation, "migrate_and_register_store") as migrate:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        migrate.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(transport.calls, 0)

    def test_existing_unanchored_sqlite_target_is_unchanged_and_never_migrated(self) -> None:
        with sqlite3.connect(self.target) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        before = self._target_snapshot()
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(
            operation, "load_registry", return_value=self.registry
        ), mock.patch.object(operation, "migrate_and_register_store") as migrate:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        migrate.assert_not_called()
        self.assertEqual(self._target_snapshot(), before)
        self.assertEqual(transport.calls, 0)

    def test_existing_other_role_target_is_unchanged_and_never_migrated(self) -> None:
        with sqlite3.connect(self.target) as connection:
            connection.execute(
                """
                CREATE TABLE store_metadata (
                    singleton INTEGER PRIMARY KEY,
                    store_role TEXT NOT NULL,
                    contract_version TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO store_metadata (singleton, store_role, contract_version) "
                "VALUES (1, 'market', 'stage1')"
            )
        before = self._target_snapshot()
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(
            operation, "load_registry", return_value=self.registry
        ), mock.patch.object(operation, "migrate_and_register_store") as migrate:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        migrate.assert_not_called()
        self.assertEqual(self._target_snapshot(), before)
        self.assertEqual(transport.calls, 0)

    def test_existing_news_target_with_wrong_prefix_is_unchanged_and_never_migrated(self) -> None:
        with sqlite3.connect(self.target) as connection:
            connection.execute(
                """
                CREATE TABLE store_metadata (
                    singleton INTEGER PRIMARY KEY,
                    store_role TEXT NOT NULL,
                    contract_version TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO store_metadata (singleton, store_role, contract_version) "
                "VALUES (1, 'news', 'stage1')"
            )
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    migration_id TEXT,
                    store_role TEXT,
                    ordinal INTEGER,
                    resource TEXT,
                    sha256 TEXT,
                    reconstruction_state TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations (
                    migration_id, store_role, ordinal, resource, sha256,
                    reconstruction_state
                ) VALUES (
                    'news:unexpected', 'news', 1,
                    'quant_data/migrations/news/0001_foundation.sql',
                    '0000000000000000000000000000000000000000000000000000000000000000',
                    'fixture_validated'
                )
                """
            )
        before = self._target_snapshot()
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(
            operation, "load_registry", return_value=self.registry
        ), mock.patch.object(operation, "migrate_and_register_store") as migrate:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        migrate.assert_not_called()
        self.assertEqual(self._target_snapshot(), before)
        self.assertEqual(transport.calls, 0)

    def test_existing_non_sqlite_target_is_unchanged_and_never_migrated(self) -> None:
        self.target.write_bytes(b"not a sqlite database")
        before = self._target_snapshot()
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(
            operation, "load_registry", return_value=self.registry
        ), mock.patch.object(operation, "migrate_and_register_store") as migrate:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        migrate.assert_not_called()
        self.assertEqual(self._target_snapshot(), before)
        self.assertEqual(transport.calls, 0)

    def test_wrong_target_is_rejected_before_registry_or_transport(self) -> None:
        wrong_target = self.root / "data" / "other.sqlite"
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with mock.patch.object(operation, "APPROVED_PROJECT_ROOT", self.root), mock.patch.object(
            operation, "APPROVED_NEWS_TARGET", self.target
        ), mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ArgumentFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(wrong_target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertEqual(transport.calls, 0)
        self.assertFalse(wrong_target.exists())

    def test_symlinked_target_ancestor_is_rejected_before_registry_or_transport(self) -> None:
        real_data = self.root / "real_data"
        real_data.mkdir()
        self.target.parent.rmdir()
        self.target.parent.symlink_to(real_data, target_is_directory=True)
        transport = _Transport(FIXTURE_PATH.read_bytes())
        with self._exact_target_patches(), mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ArgumentFailure):
                operation.populate_fmp_stock_latest_news(
                    project_root=str(self.root),
                    target=str(self.target),
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertEqual(transport.calls, 0)

    def test_cli_sanitizes_bad_arguments(self) -> None:
        self.assertEqual(
            operation.main(["--project-root", "/wrong", "--target", "/wrong/news.sqlite"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
