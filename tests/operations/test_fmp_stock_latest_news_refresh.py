from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.migrations import migrate_and_register_store
from quant_data.news.current_multi_source import (
    CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID,
    CURRENT_MULTI_SOURCE_COLLECTOR_ID,
    CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID,
    CURRENT_MULTI_SOURCE_MIGRATION_ID,
    LEGACY_FMP_NEWS_DATASET_ID,
    LEGACY_FMP_NEWS_MIGRATION_ID,
)
from quant_data.news.fmp_stock_latest_current import (
    CapturedFmpStockLatestCurrentResponse,
    FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
    FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
)
from quant_data.operations import fmp_stock_latest_news_refresh as operation
from quant_data.registry import DatasetDeclaration, MigrationDeclaration, load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
MIGRATION_PATH = PROJECT_ROOT / "quant_data/migrations/news/0006_fmp_stock_latest_current.sql"
FIXED_TIME = datetime(2026, 8, 29, 15, 32, tzinfo=timezone.utc)


def _dataset(dataset_id: str, layer: str, relations: tuple[str, ...]) -> DatasetDeclaration:
    return DatasetDeclaration(
        id=dataset_id,
        version="1.0.0",
        store=StoreRole.NEWS.value,
        layer=layer,
        relations=relations,
        physical={"relations": relations},
        identity={"provider": "fmp", "dataset": dataset_id},
        temporal={"availability_basis": "local_capture"},
        revision_policy="append_only",
        freshness={"cadence": "hourly"},
        quality_contract={"mode": "validated_rows"},
        collector_ids=(FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,),
        tool_ids=(),
        dashboard_ids=(),
        export_ids=(),
        active=True,
    )


def _successor_registry():
    base = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
    if any(item.id == FMP_STOCK_LATEST_CURRENT_MIGRATION_ID for item in base.migrations):
        return base
    migration = MigrationDeclaration(
        id=FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
        store=StoreRole.NEWS.value,
        ordinal=6,
        resource="quant_data/migrations/news/0006_fmp_stock_latest_current.sql",
        sha256=hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest(),
        semantic_scope="repeatable current FMP stock-latest news page",
        dependencies=("news:0005_fmp_stock_latest",),
        reconstruction_state="fixture_validated",
    )
    evidence = _dataset(
        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
        "evidence",
        (
            "fmp_stock_latest_current_attempts",
            "fmp_stock_latest_current_outcomes",
            "fmp_stock_latest_current_captures",
        ),
    )
    articles = _dataset(
        FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
        "canonical",
        (
            "fmp_stock_latest_current_articles",
            "fmp_stock_latest_current_article_versions",
            "fmp_stock_latest_current_capture_articles",
        ),
    )
    stores = tuple(
        replace(item, migration_order=(*item.migration_order, migration.id))
        if item.id == StoreRole.NEWS.value
        else item
        for item in base.stores
    )
    return replace(
        base,
        registry_version="test-current-news",
        stores=stores,
        migrations=(*base.migrations, migration),
        datasets=(*base.datasets, evidence, articles),
        collectors=(
            *base.collectors,
            {
                "id": FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
                "handler": "news.fmp_stock_latest_current",
                "network": True,
                "configuration_env": ["FMP_API_KEY"],
                "output_datasets": [
                    FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
                    FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
                ],
            },
        ),
    )


def _prefix_registry(registry):
    removed_migrations = {
        FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
        CURRENT_MULTI_SOURCE_MIGRATION_ID,
        LEGACY_FMP_NEWS_MIGRATION_ID,
    }
    removed_datasets = {
        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
        FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
        CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID,
        CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID,
        LEGACY_FMP_NEWS_DATASET_ID,
    }
    removed_collectors = {
        FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
        CURRENT_MULTI_SOURCE_COLLECTOR_ID,
    }
    migrations = tuple(
        item for item in registry.migrations if item.id not in removed_migrations
    )
    datasets = tuple(
        item for item in registry.datasets if item.id not in removed_datasets
    )
    collectors = tuple(
        item
        for item in registry.collectors
        if item.get("id") not in removed_collectors
    )
    stores = tuple(
        replace(
            item,
            migration_order=tuple(
                migration_id
                for migration_id in item.migration_order
                if migration_id not in removed_migrations
            ),
        )
        if item.id == StoreRole.NEWS.value
        else item
        for item in registry.stores
    )
    return replace(
        registry,
        stores=stores,
        migrations=migrations,
        datasets=datasets,
        collectors=collectors,
    )


def _body() -> bytes:
    return (
        b'[{"symbol":"AAPL","title":"A title","text":"body",'
        b'"site":"example.test","url":"https://example.test/aapl",'
        b'"publishedDate":"2026-08-29T15:00:00Z"}]'
    )


class _Transport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedFmpStockLatestCurrentResponse:
        self.calls += 1
        if kwargs != {
            "path": "/stable/news/stock-latest",
            "query": {"page": "0", "limit": "1000"},
            "headers": {"apikey": "offline-key", "Accept": "application/json"},
            "timeout_seconds": 60,
            "max_bytes": 64 * 1024 * 1024,
        }:
            raise AssertionError("operation widened the current FMP request")
        return CapturedFmpStockLatestCurrentResponse(
            200,
            "application/json; charset=UTF-8",
            _body(),
        )


class FmpStockLatestCurrentRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.target = self.root / "data" / "news.sqlite"
        self.target.parent.mkdir()
        self.registry = _successor_registry()
        self.prefix = _prefix_registry(self.registry)
        self.stores = StoreMap.four_explicit(
            market=self.root / "data" / "market.sqlite",
            macro=self.root / "data" / "macro.sqlite",
            company=self.root / "data" / "company.sqlite",
            news=self.target,
        )
        migrate_and_register_store(
            self.stores,
            self.prefix,
            StoreRole.NEWS,
            applied_at="2026-08-29T15:00:00Z",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _patches(self):
        return mock.patch.multiple(
            operation,
            APPROVED_PROJECT_ROOT=self.root,
            APPROVED_NEWS_TARGET=self.target,
        )

    def test_exact_target_applies_approved_news_successors_without_other_stores(self) -> None:
        transport = _Transport()
        with self._patches(), mock.patch.object(
            operation,
            "load_registry",
            return_value=self.registry,
        ) as load:
            result = operation.refresh_fmp_stock_latest_news(
                environment={"FMP_API_KEY": "offline-key"},
                transport=transport,
                clock=lambda: FIXED_TIME,
            )
        self.assertEqual(
            (
                result.outcome,
                result.poll_slot,
                result.provider_row_count,
                result.accepted_row_count,
                result.rejected_row_count,
            ),
            ("succeeded", "2026-08-29T15:00:00Z", 1, 1, 0),
        )
        self.assertEqual(transport.calls, 1)
        load.assert_called_once()
        self.assertFalse((self.root / "data" / "market.sqlite").exists())
        self.assertFalse((self.root / "data" / "macro.sqlite").exists())
        self.assertFalse((self.root / "data" / "company.sqlite").exists())
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            migrations = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT migration_id FROM schema_migrations ORDER BY ordinal"
                )
            )
            self.assertEqual(
                migrations[-3:],
                (
                    FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
                    CURRENT_MULTI_SOURCE_MIGRATION_ID,
                    LEGACY_FMP_NEWS_MIGRATION_ID,
                ),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM fmp_stock_latest_current_attempts"
                ).fetchone()[0],
                1,
            )

    def test_missing_credential_makes_no_request_or_migration(self) -> None:
        transport = _Transport()
        with self._patches(), mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.refresh_fmp_stock_latest_news(
                    environment={},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertEqual(transport.calls, 0)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM schema_migrations WHERE ordinal=6"
                ).fetchone()[0],
                0,
            )

    def test_invalid_current_ledger_rejects_before_target_mutation_or_transport(self) -> None:
        transport = _Transport()
        with self._patches(), mock.patch.object(
            operation,
            "load_registry",
            return_value=self.prefix,
        ):
            with self.assertRaises(operation._ConfigurationFailure):
                operation.refresh_fmp_stock_latest_news(
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        self.assertEqual(transport.calls, 0)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM schema_migrations WHERE ordinal=6"
                ).fetchone()[0],
                0,
            )

    def test_wrong_fixed_target_is_rejected_before_registry_or_transport(self) -> None:
        transport = _Transport()
        wrong = self.root / "data" / "other.sqlite"
        with mock.patch.object(operation, "APPROVED_PROJECT_ROOT", self.root), mock.patch.object(
            operation,
            "APPROVED_NEWS_TARGET",
            wrong,
        ), mock.patch.object(operation, "load_registry") as load:
            with self.assertRaises(operation._ConfigurationFailure):
                operation.refresh_fmp_stock_latest_news(
                    environment={"FMP_API_KEY": "offline-key"},
                    transport=transport,
                    clock=lambda: FIXED_TIME,
                )
        load.assert_not_called()
        self.assertEqual(transport.calls, 0)
        self.assertFalse(wrong.exists())

    def test_cli_rejects_caller_arguments(self) -> None:
        self.assertEqual(operation.main(["--target", "/wrong/news.sqlite"]), 2)


if __name__ == "__main__":
    unittest.main()
