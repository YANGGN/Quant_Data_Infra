from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError, StoreUnavailableError, ValidationError
from quant_data.fingerprint import logical_manifest, mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import dumps_strict
from quant_data.market import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.operations import (
    all_store_health,
    backup_all,
    inspect_store,
    restore_all,
)
from quant_data.registry import load_registry
from quant_data.stores import STORE_ROLES, StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_STORE_FILENAMES = {"market.sqlite", "macro.sqlite", "company.sqlite", "news.sqlite"}


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class OperationsBackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        self.store_map = _store_map(self.root / "source")
        initialize_all(self.store_map, self.registry)
        self.fixture_manifest = FixtureManifest.load(
            PROJECT_ROOT / "tests" / "fixtures" / "manifest.json",
            project_root=PROJECT_ROOT,
        )
        self._wal_connection: sqlite3.Connection | None = None

    def tearDown(self) -> None:
        if self._wal_connection is not None:
            self._wal_connection.close()
        self.temporary.cleanup()

    def _commit_market_row_to_open_wal(self) -> None:
        connection = sqlite3.connect(self.store_map.market, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        self._wal_connection = connection
        receipt = DailyPriceImporter(
            self.store_map,
            self.fixture_manifest,
        ).import_fixture("market.base")
        self.assertEqual(receipt.outcome, "succeeded")

    def test_wal_safe_backup_and_restore_preserve_four_store_state(self) -> None:
        self._commit_market_row_to_open_wal()
        wal_path = Path(f"{self.store_map.market}-wal")
        self.assertTrue(wal_path.is_file())
        self.assertGreater(wal_path.stat().st_size, 0)
        source_mutation_before = mutation_fingerprint(self.store_map)
        source_logical_before = logical_manifest(self.store_map, self.registry)

        backup = backup_all(
            self.store_map,
            self.registry,
            target_root=self.root / "backup",
        )

        self.assertEqual(source_mutation_before["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        self.assertEqual(source_logical_before["sha256"], logical_manifest(backup.store_map, self.registry)["sha256"])
        self.assertEqual({path.name for path in (self.root / "backup").glob("*.sqlite")}, _STORE_FILENAMES)
        self.assertEqual(tuple(receipt.role for receipt in backup.receipts), tuple(role.value for role in STORE_ROLES))
        self.assertEqual(backup.source_mutation_before_sha256, backup.source_mutation_after_sha256)
        backup_receipt = dumps_strict(backup.to_primitive())
        self.assertNotIn(str(self.root), backup_receipt)
        with read_connection(backup.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ingestion_runs WHERE dataset_id=?",
                    ("fixture.market.daily_prices",),
                ).fetchone()[0],
                1,
            )

        restored = restore_all(
            backup,
            self.registry,
            target_root=self.root / "restored",
        )

        self.assertEqual(source_logical_before["sha256"], logical_manifest(restored.store_map, self.registry)["sha256"])
        self.assertEqual({path.name for path in (self.root / "restored").glob("*.sqlite")}, _STORE_FILENAMES)
        self.assertEqual(restored.backup_mutation_before_sha256, restored.backup_mutation_after_sha256)
        for role in STORE_ROLES:
            with read_connection(restored.store_map, role) as connection:
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE forbidden_after_restore(value TEXT)")

    def test_backup_and_restore_refuse_nonempty_targets_without_overwrite(self) -> None:
        backup_target = self.root / "backup-refusal"
        backup_target.mkdir()
        existing_backup = backup_target / "market.sqlite"
        existing_backup.write_text("preserve", encoding="utf-8")
        with self.assertRaises(ConflictError):
            backup_all(self.store_map, self.registry, target_root=backup_target)
        self.assertEqual(existing_backup.read_text(encoding="utf-8"), "preserve")

        backup = backup_all(
            self.store_map,
            self.registry,
            target_root=self.root / "backup",
        )
        restore_target = self.root / "restore-refusal"
        restore_target.mkdir()
        existing_restore = restore_target / "news.sqlite"
        existing_restore.write_text("preserve", encoding="utf-8")
        with self.assertRaises(ConflictError):
            restore_all(backup, self.registry, target_root=restore_target)
        self.assertEqual(existing_restore.read_text(encoding="utf-8"), "preserve")

    def test_health_is_path_free_read_only_and_missing_store_fails_closed(self) -> None:
        before = mutation_fingerprint(self.store_map)
        report = all_store_health(self.store_map, self.registry)
        after = mutation_fingerprint(self.store_map)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(tuple(item.role for item in report.stores), tuple(role.value for role in STORE_ROLES))
        self.assertNotIn(str(self.root), dumps_strict(report.to_primitive()))
        for inspection in report.stores:
            self.assertTrue(inspection.healthy)
            self.assertEqual(inspection.integrity, "ok")
            self.assertEqual(inspection.foreign_key_violations, 0)

        missing = StoreMap.four_explicit(
            market=self.store_map.market,
            macro=self.store_map.macro,
            company=self.store_map.company,
            news=self.root / "missing" / "news.sqlite",
        )
        with self.assertRaises(StoreUnavailableError):
            inspect_store(missing, self.registry, StoreRole.NEWS)

    def test_wrong_role_and_tampered_ledgers_fail_before_copy_or_restore(self) -> None:
        swapped = StoreMap.four_explicit(
            market=self.store_map.macro,
            macro=self.store_map.market,
            company=self.store_map.company,
            news=self.store_map.news,
        )
        with self.assertRaises(StoreUnavailableError):
            inspect_store(swapped, self.registry, StoreRole.MARKET)

        corrupt_path = self.root / "corrupt.sqlite"
        corrupt_path.write_bytes(b"not a sqlite database")
        corrupt = StoreMap.four_explicit(
            market=corrupt_path,
            macro=self.store_map.macro,
            company=self.store_map.company,
            news=self.store_map.news,
        )
        with self.assertRaises(StoreUnavailableError):
            inspect_store(corrupt, self.registry, StoreRole.MARKET)

        connection = sqlite3.connect(self.store_map.market)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=1",
                ("0" * 64,),
            )
        connection.rollback()
        connection.execute("DROP TRIGGER schema_migrations_immutable_update")
        connection.execute(
            "UPDATE schema_migrations SET sha256=? WHERE ordinal=1",
            ("0" * 64,),
        )
        connection.commit()
        connection.close()
        failed_target = self.root / "not-created"
        with self.assertRaises(ValidationError):
            backup_all(self.store_map, self.registry, target_root=failed_target)
        self.assertFalse(failed_target.exists())

    def test_health_fails_closed_when_control_trigger_sql_is_substituted(self) -> None:
        connection = sqlite3.connect(self.store_map.market)
        connection.execute("DROP TRIGGER ingestion_runs_success_lineage")
        connection.execute(
            """
            CREATE TRIGGER ingestion_runs_success_lineage
            BEFORE UPDATE ON ingestion_runs
            BEGIN
                SELECT 1;
            END
            """
        )
        connection.commit()
        connection.close()

        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            all_store_health(self.store_map, self.registry)
        self.assertEqual(
            before["sha256"],
            mutation_fingerprint(self.store_map)["sha256"],
        )

    def test_tampered_backup_ledger_refuses_restore_before_target_creation(self) -> None:
        backup = backup_all(
            self.store_map,
            self.registry,
            target_root=self.root / "backup",
        )
        connection = sqlite3.connect(backup.store_map.macro)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=1",
                ("f" * 64,),
            )
        connection.rollback()
        connection.execute("DROP TRIGGER schema_migrations_immutable_update")
        connection.execute(
            "UPDATE schema_migrations SET sha256=? WHERE ordinal=1",
            ("f" * 64,),
        )
        connection.commit()
        connection.close()
        failed_target = self.root / "restore-not-created"
        with self.assertRaises(ValidationError):
            restore_all(backup, self.registry, target_root=failed_target)
        self.assertFalse(failed_target.exists())


if __name__ == "__main__":
    unittest.main()
