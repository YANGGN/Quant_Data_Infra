from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import StoreUnavailableError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
from quant_data.news import news_foundation_status
from quant_data.registry import load_registry
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class NewsFoundationStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_status_proves_the_empty_stage2_foundation_without_mutation(self) -> None:
        initialize_all(self.store_map, self.registry)
        before = mutation_fingerprint(self.store_map)

        status = news_foundation_status(self.store_map, self.registry)
        expected_migrations = [
            {
                "id": "news:0001_foundation",
                "sha256": "521f54561714ba9b3c67dec4b6c9eaf703ec521a8b1250193ffb40c9e9eb1097",
            },
            {
                "id": "news:0002_control_plane",
                "sha256": "144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b",
            },
        ]
        self.assertEqual(status, news_foundation_status(self.store_map, self.registry))
        after = mutation_fingerprint(self.store_map)
        self.assertEqual(before, after)
        self.assertEqual(status["registry_version"], "2.0.0")
        self.assertEqual(status["store_role"], "news")
        self.assertEqual(status["store_contract_version"], "stage2")
        self.assertEqual(
            [item["id"] for item in status["migrations"]],
            ["news:0001_foundation", "news:0002_control_plane"],
        )
        self.assertEqual(status["migrations"], expected_migrations)
        self.assertEqual(
            [
                {"id": declaration.id, "sha256": declaration.sha256}
                for declaration in sorted(
                    self.registry.migrations_for("news"), key=lambda item: item.ordinal
                )
            ],
            expected_migrations,
        )
        self.assertEqual(
            status["control_tables"],
            [
                "store_metadata",
                "schema_migrations",
                "dataset_registry",
                "dataset_identity_contracts",
                "ingestion_runs",
                "ingestion_run_outputs",
                "ingestion_run_failures",
                "ingestion_artifacts",
                "ingestion_snapshots",
                "ingestion_snapshot_artifacts",
                "data_quality_results",
            ],
        )
        self.assertEqual(
            status["row_counts"],
            {
                "store_metadata": 1,
                "schema_migrations": 2,
                "dataset_registry": 0,
                "dataset_identity_contracts": 0,
                "ingestion_runs": 0,
                "ingestion_run_outputs": 0,
                "ingestion_run_failures": 0,
                "ingestion_artifacts": 0,
                "ingestion_snapshots": 0,
                "ingestion_snapshot_artifacts": 0,
                "data_quality_results": 0,
            },
        )
        self.assertEqual(status["registered_domain_datasets"], 0)
        self.assertEqual(status["integrity_check"], "ok")
        self.assertEqual(status["foreign_key_violations"], 0)
        self.assertTrue(status["query_only"])
        self.assertNotIn(str(self.store_map.news), dumps_strict(status))

    def test_missing_news_store_fails_closed_without_creating_it(self) -> None:
        with self.assertRaises(StoreUnavailableError):
            news_foundation_status(self.store_map, self.registry)

        self.assertFalse(self.store_map.news.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_role_store_fails_closed(self) -> None:
        initialize_all(self.store_map, self.registry)
        wrong_role_map = StoreMap.four_explicit(
            market=self.store_map.news,
            macro=self.store_map.macro,
            company=self.store_map.company,
            news=self.store_map.market,
        )

        with self.assertRaises(StoreUnavailableError):
            news_foundation_status(wrong_role_map, self.registry)

    def test_ledger_checksum_mismatch_fails_closed(self) -> None:
        initialize_all(self.store_map, self.registry)
        connection = sqlite3.connect(self.store_map.news)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE schema_migrations SET sha256=? WHERE migration_id=?",
                    ("0" * 64, "news:0002_control_plane"),
                )
            connection.rollback()
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE migration_id=?",
                ("0" * 64, "news:0002_control_plane"),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(StoreUnavailableError):
            news_foundation_status(self.store_map, self.registry)

    def test_unexpected_domain_relation_fails_closed(self) -> None:
        initialize_all(self.store_map, self.registry)
        connection = sqlite3.connect(self.store_map.news)
        try:
            connection.execute("CREATE TABLE news_item_forbidden(item_id TEXT PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(StoreUnavailableError):
            news_foundation_status(self.store_map, self.registry)

    def test_unexpected_control_row_fails_closed(self) -> None:
        initialize_all(self.store_map, self.registry)
        connection = sqlite3.connect(self.store_map.news)
        try:
            connection.execute(
                """
                INSERT INTO dataset_registry (
                    dataset_id, store_role, layer, schema_version, relations_json,
                    active, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "news.forbidden",
                    "news",
                    "evidence",
                    "1.0.0",
                    "[]",
                    0,
                    "2026-08-09T12:00:00-04:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(StoreUnavailableError):
            news_foundation_status(self.store_map, self.registry)


if __name__ == "__main__":
    unittest.main()
