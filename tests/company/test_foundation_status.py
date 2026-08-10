from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.company import company_foundation_status
from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
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


class CompanyFoundationStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_status_reports_the_exact_empty_stage2_company_contract(self) -> None:
        self.assertEqual(self.registry.registry_version, "2.1.0")
        self.assertEqual(
            [
                (migration.id, migration.sha256)
                for migration in sorted(
                    self.registry.migrations_for("company"), key=lambda item: item.ordinal
                )
            ],
            [
                (
                    "company:0001_foundation",
                    "081c609509c4337b74a706739c0506aa6beefb0a4de430801619781b9aad7915",
                ),
                (
                    "company:0002_control_plane",
                    "144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b",
                ),
            ],
        )
        self.assertEqual(
            company_foundation_status(self.store_map, self.registry),
            {
                "store_role": "company",
                "registry_version": self.registry.registry_version,
                "contract_version": "stage2",
                "migrations": [
                    {
                        "id": "company:0001_foundation",
                        "sha256": "081c609509c4337b74a706739c0506aa6beefb0a4de430801619781b9aad7915",
                    },
                    {
                        "id": "company:0002_control_plane",
                        "sha256": "144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b",
                    },
                ],
                "control_tables": [
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
                "empty_table_row_counts": {
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
                "integrity": "ok",
                "foreign_key_violations": 0,
                "query_only": True,
            },
        )

    def test_status_is_path_free_deterministic_and_does_not_mutate(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(company_foundation_status).parameters),
            ("store_map", "registry"),
        )
        before = mutation_fingerprint(self.store_map)
        first = company_foundation_status(self.store_map, self.registry)
        second = company_foundation_status(self.store_map, self.registry)
        after = mutation_fingerprint(self.store_map)

        self.assertEqual(first, second)
        self.assertNotIn(str(self.root), dumps_strict(first))
        self.assertEqual(before["sha256"], after["sha256"])

    def test_missing_company_store_fails_closed_without_initializing_it(self) -> None:
        missing_map = temporary_store_map(self.root / "missing")

        with self.assertRaises(StoreUnavailableError):
            company_foundation_status(missing_map, self.registry)

        self.assertFalse(missing_map.company.exists())

    def test_wrong_role_company_store_fails_closed(self) -> None:
        wrong_role_map = StoreMap.four_explicit(
            market=self.store_map.company,
            macro=self.store_map.macro,
            company=self.store_map.market,
            news=self.store_map.news,
        )

        with self.assertRaises(StoreUnavailableError) as caught:
            company_foundation_status(wrong_role_map, self.registry)

        self.assertEqual(caught.exception.code, "store_unavailable")

    def test_status_rejects_a_mismatched_migration_checksum(self) -> None:
        connection = sqlite3.connect(self.store_map.company)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE schema_migrations SET sha256=? WHERE ordinal=2",
                    ("0" * 64,),
                )
            connection.rollback()
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=2",
                ("0" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ValidationError):
            company_foundation_status(self.store_map, self.registry)

    def test_status_rejects_data_before_company_domain_restoration(self) -> None:
        connection = sqlite3.connect(self.store_map.company)
        try:
            connection.execute(
                """
                INSERT INTO dataset_registry (
                    dataset_id, store_role, layer, schema_version, relations_json,
                    active, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "unexpected.company.dataset",
                    "company",
                    "canonical",
                    "1.0.0",
                    "[]",
                    1,
                    "2026-08-09T12:00:00-04:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ValidationError):
            company_foundation_status(self.store_map, self.registry)

    def test_status_rejects_an_unexpected_domain_relation(self) -> None:
        connection = sqlite3.connect(self.store_map.company)
        try:
            connection.execute("CREATE TABLE unexpected_company_domain (value TEXT) STRICT")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ValidationError):
            company_foundation_status(self.store_map, self.registry)
