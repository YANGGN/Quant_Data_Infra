from __future__ import annotations

import sqlite3
import shutil
import tempfile
import unittest
import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quant_data.errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    ValidationError,
)
from quant_data.fingerprint import logical_manifest, mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.migrations import initialize_all, migrate_store
from quant_data.registry import PUBLIC_TOOL_NAMES, load_registry, stage2_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap, StoreRole, read_connection
from quant_data.temporal import (
    DateOnlyPolicy,
    TemporalValue,
    availability_at_or_before,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class RegistryAndFixtureTests(unittest.TestCase):
    def test_registry_is_validated_two_tool_milestone_with_57_reserved_names(self) -> None:
        registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        self.assertEqual(registry.status, "validated")
        self.assertEqual(len(PUBLIC_TOOL_NAMES), 57)
        self.assertEqual(
            [tool["id"] for tool in registry.tools],
            ["macro.get_series", "timeseries.describe"],
        )
        self.assertEqual({store.id for store in registry.stores}, {"market", "macro", "company", "news"})
        self.assertEqual(
            registry.raw["compatibility_target"]["stage1_milestone_status"],
            "validated_non_active",
        )
        self.assertEqual(
            registry.tool("macro.get_series")["output_schema"],
            registry.tool("timeseries.describe")["input_schema"]["properties"]["series"],
        )
        for tool in registry.tools:
            self.assertTrue(tool["examples"])
            for example in tool["examples"]:
                validate_schema(example, tool["input_schema"])

    def test_duplicate_registry_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}', encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_empty_registry_override_is_rejected(self) -> None:
        with self.assertRaises(RegistryError):
            load_registry(
                Path("config/system_registry.json"),
                project_root=PROJECT_ROOT,
                environment={"QUANT_SYSTEM_REGISTRY_PATH": ""},
            )

    def test_fixture_bytes_match_reviewed_manifest(self) -> None:
        manifest = FixtureManifest.load(FIXTURE_MANIFEST, project_root=PROJECT_ROOT)
        self.assertEqual(manifest.get("market.base").byte_count, 518)
        self.assertEqual(manifest.get("macro.first_vintage").metadata["expected_missing"], 1)
        for fixture_id in (
            "market.base",
            "market.correction",
            "macro.first_vintage",
            "macro.revised_vintage",
        ):
            fixture = manifest.get(fixture_id)
            self.assertTrue(fixture.test_fixture)
            self.assertFalse(fixture.promotable)
            self.assertEqual(len(fixture.expected_semantic_identity), 64)
            self.assertEqual(fixture.expected_warnings, ())

    def test_registry_rejects_unknown_nested_fields_and_role_swapped_path_env(self) -> None:
        raw = loads_strict(REGISTRY_PATH.read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            raw["stores"][0]["unexpected"] = True
            path.write_text(dumps_strict(raw), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})
            del raw["stores"][0]["unexpected"]
            raw["stores"][0]["path_env"] = "QUANT_MACRO_DB_PATH"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})


class StoreAndMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_four_stores_initialize_and_rerun_deterministically(self) -> None:
        first_status = initialize_all(self.store_map, self.registry)
        first = logical_manifest(self.store_map, self.registry)
        second_status = initialize_all(self.store_map, self.registry)
        second = logical_manifest(self.store_map, self.registry)
        self.assertEqual(first_status, second_status)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(
            len(first_status["market"]),
            len(self.registry.migrations_for("market")),
        )
        self.assertEqual(
            len(first_status["macro"]),
            len(self.registry.migrations_for("macro")),
        )
        self.assertEqual(
            len(first_status["company"]),
            len(self.registry.migrations_for("company")),
        )
        self.assertEqual(
            len(first_status["news"]),
            len(self.registry.migrations_for("news")),
        )
        self.assertFalse((PROJECT_ROOT / "data").exists())

    def test_stage2_registry_rebuild_preserves_stage1_rows_and_foreign_keys(self) -> None:
        stage2_registry = stage2_registry_profile(self.registry)
        stage2_ids = {
            "market:0003_control_plane",
            "macro:0003_control_plane",
            "company:0002_control_plane",
            "news:0002_control_plane",
        }
        stage1_registry = replace(
            stage2_registry,
            registry_version="1.0.0",
            migrations=tuple(
                item for item in stage2_registry.migrations if item.id not in stage2_ids
            ),
            stores=tuple(
                replace(
                    store,
                    control_tables=(
                        "schema_migrations",
                        "dataset_registry",
                        "ingestion_runs",
                    ),
                    migration_order=tuple(
                        item for item in store.migration_order if item not in stage2_ids
                    ),
                )
                for store in stage2_registry.stores
            ),
        )
        migrate_store(
            self.store_map,
            stage1_registry,
            StoreRole.MARKET,
            applied_at="2026-08-09T11:00:00-04:00",
        )
        connection = sqlite3.connect(self.store_map.market)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO dataset_registry (
                dataset_id, store_role, layer, schema_version, relations_json,
                active, registered_at
            ) VALUES ('test.upgrade', 'market', 'canonical', '1.0.0', '[]', 0, ?)
            """,
            ("2026-08-09T11:00:00-04:00",),
        )
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, fetched_count, code_version
            ) VALUES ('test-upgrade-run', 'test.upgrade', ?, 'test.upgrade',
                      '{}', 'running', ?, 0, 'test')
            """,
            ("e" * 64, "2026-08-09T11:00:00-04:00"),
        )
        connection.commit()
        connection.close()

        migrate_store(
            self.store_map,
            stage2_registry,
            StoreRole.MARKET,
            applied_at="2026-08-09T12:00:00-04:00",
        )
        connection = sqlite3.connect(self.store_map.market)
        connection.execute("PRAGMA foreign_keys=ON")
        self.assertEqual(
            connection.execute(
                "SELECT layer FROM dataset_registry WHERE dataset_id='test.upgrade'"
            ).fetchone(),
            ("canonical",),
        )
        self.assertEqual(
            connection.execute(
                "SELECT dataset_id FROM ingestion_runs WHERE run_id='test-upgrade-run'"
            ).fetchone(),
            ("test.upgrade",),
        )
        self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
        connection.execute(
            """
            INSERT INTO dataset_registry (
                dataset_id, store_role, layer, schema_version, relations_json,
                active, registered_at
            ) VALUES ('test.derived', 'market', 'derived', '1.0.0', '[]', 0, ?)
            """,
            ("2026-08-09T12:00:00-04:00",),
        )
        connection.rollback()
        connection.close()

    def test_duplicate_physical_store_path_fails_closed(self) -> None:
        path = self.root / "same.sqlite"
        with self.assertRaises(ConflictError):
            StoreMap.four_explicit(
                market=path,
                macro=path,
                company=self.root / "company.sqlite",
                news=self.root / "news.sqlite",
            )

    def test_read_connection_is_query_only_and_role_checked(self) -> None:
        initialize_all(self.store_map, self.registry)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden(value TEXT)")
        swapped = StoreMap.four_explicit(
            market=self.store_map.macro,
            macro=self.store_map.market,
            company=self.store_map.company,
            news=self.store_map.news,
        )
        with self.assertRaises(Exception) as caught:
            with read_connection(swapped, StoreRole.MARKET):
                pass
        self.assertEqual(getattr(caught.exception, "code", None), "store_unavailable")

    def test_applied_ledger_is_immutable_before_later_work(self) -> None:
        initialize_all(self.store_map, self.registry)
        connection = sqlite3.connect(self.store_map.market)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=1",
                ("0" * 64,),
            )
        connection.rollback()
        connection.close()
        self.assertEqual(
            migrate_store(
                self.store_map,
                self.registry,
                StoreRole.MARKET,
                applied_at="2026-08-09T12:00:00-04:00",
            ),
            self.registry.store("market").migration_order,
        )

    def test_resource_tamper_after_registry_load_advances_no_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            project = Path(project_directory)
            shutil.copytree(PROJECT_ROOT / "config", project / "config")
            shutil.copytree(
                PROJECT_ROOT / "quant_data" / "migrations",
                project / "quant_data" / "migrations",
            )
            copied_registry = load_registry(
                project / "config" / "system_registry.json",
                project_root=project,
                environment={},
            )
            resource = project / "quant_data" / "migrations" / "market" / "0001_foundation.sql"
            resource.write_bytes(resource.read_bytes() + b"\n-- tampered after validation\n")
            stores = temporary_store_map(project / "stores")
            with self.assertRaises(MigrationError):
                migrate_store(
                    stores,
                    copied_registry,
                    StoreRole.MARKET,
                    applied_at="2026-08-09T12:00:00-04:00",
                )
            self.assertFalse(stores.market.exists())

    def test_applied_resource_tamper_after_registry_load_fails_current_store(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            project = Path(project_directory)
            shutil.copytree(PROJECT_ROOT / "config", project / "config")
            shutil.copytree(
                PROJECT_ROOT / "quant_data" / "migrations",
                project / "quant_data" / "migrations",
            )
            copied_registry = load_registry(
                project / "config" / "system_registry.json",
                project_root=project,
                environment={},
            )
            stores = temporary_store_map(project / "stores")
            initialize_all(stores, copied_registry)
            resource = project / "quant_data" / "migrations" / "market" / "0001_foundation.sql"
            resource.write_bytes(resource.read_bytes() + b"\n-- tampered after application\n")
            with self.assertRaises(MigrationError):
                migrate_store(
                    stores,
                    copied_registry,
                    StoreRole.MARKET,
                    applied_at="2026-08-09T12:00:00-04:00",
                )
            connection = sqlite3.connect(stores.market)
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            connection.close()
            self.assertEqual(
                count,
                len(copied_registry.migrations_for(StoreRole.MARKET.value)),
            )

    def test_inline_transaction_control_cannot_escape_atomic_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            project = Path(project_directory)
            shutil.copytree(PROJECT_ROOT / "config", project / "config")
            shutil.copytree(
                PROJECT_ROOT / "quant_data" / "migrations",
                project / "quant_data" / "migrations",
            )
            resource = project / "quant_data" / "migrations" / "market" / "0001_foundation.sql"
            resource_bytes = (
                b"CREATE TABLE escaped_commit(value INTEGER); COMMIT; "
                b"CREATE TABLE broken("
            )
            resource.write_bytes(resource_bytes)
            registry_path = project / "config" / "system_registry.json"
            raw = loads_strict(registry_path.read_bytes())
            raw["migrations"][0]["sha256"] = hashlib.sha256(resource_bytes).hexdigest()
            registry_path.write_text(dumps_strict(raw), encoding="utf-8")
            copied_registry = load_registry(
                registry_path,
                project_root=project,
                environment={},
            )
            stores = temporary_store_map(project / "stores")
            with self.assertRaises(MigrationError):
                migrate_store(
                    stores,
                    copied_registry,
                    StoreRole.MARKET,
                    applied_at="2026-08-09T12:00:00-04:00",
                )
            connection = sqlite3.connect(stores.market)
            escaped = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='escaped_commit'"
            ).fetchone()
            ledger_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            connection.close()
            self.assertIsNone(escaped)
            self.assertEqual(ledger_count, 0)

    def test_migration_table_rebuild_mode_rejects_foreign_key_violations(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            project = Path(project_directory)
            shutil.copytree(PROJECT_ROOT / "config", project / "config")
            shutil.copytree(
                PROJECT_ROOT / "quant_data" / "migrations",
                project / "quant_data" / "migrations",
            )
            resource = project / "quant_data" / "migrations" / "market" / "0001_foundation.sql"
            resource_bytes = b"""
CREATE TABLE parent(id TEXT PRIMARY KEY) STRICT;
CREATE TABLE child(
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL REFERENCES parent(id)
) STRICT;
INSERT INTO child(id, parent_id) VALUES ('child', 'missing');
"""
            resource.write_bytes(resource_bytes)
            registry_path = project / "config" / "system_registry.json"
            raw = loads_strict(registry_path.read_bytes())
            raw["migrations"][0]["sha256"] = hashlib.sha256(resource_bytes).hexdigest()
            registry_path.write_text(dumps_strict(raw), encoding="utf-8")
            copied_registry = load_registry(
                registry_path,
                project_root=project,
                environment={},
            )
            stores = temporary_store_map(project / "stores")
            with self.assertRaises(MigrationError):
                migrate_store(
                    stores,
                    copied_registry,
                    StoreRole.MARKET,
                    applied_at="2026-08-09T12:00:00-04:00",
                )
            connection = sqlite3.connect(stores.market)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='child'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                0,
            )
            connection.close()

    def test_mutation_fingerprint_includes_volatile_control_fields(self) -> None:
        initialize_all(self.store_map, self.registry)
        connection = sqlite3.connect(self.store_map.market)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, fetched_count, code_version
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                "run_fingerprint_probe",
                "fixture.market.daily_prices",
                "1" * 64,
                "test.fingerprint",
                "{}",
                "2026-08-09T12:00:00-04:00",
                0,
                "test",
            ),
        )
        connection.commit()
        connection.close()
        logical_before = logical_manifest(self.store_map, self.registry)
        mutation_before = mutation_fingerprint(self.store_map)
        connection = sqlite3.connect(self.store_map.market)
        connection.execute(
            "UPDATE ingestion_runs SET completed_at=? WHERE run_id=?",
            ("2026-08-09T12:00:01-04:00", "run_fingerprint_probe"),
        )
        connection.commit()
        connection.close()
        logical_after = logical_manifest(self.store_map, self.registry)
        mutation_after = mutation_fingerprint(self.store_map)
        self.assertEqual(logical_before["sha256"], logical_after["sha256"])
        self.assertNotEqual(mutation_before["sha256"], mutation_after["sha256"])


class TemporalAndJsonTests(unittest.TestCase):
    def test_date_only_cutoff_matrix(self) -> None:
        availability = TemporalValue.parse("2026-06-12")
        self.assertTrue(
            availability_at_or_before(
                availability,
                TemporalValue.parse("2026-06-12"),
                DateOnlyPolicy.COMPLETED_DATE,
            ).included
        )
        same_day_timestamp = TemporalValue.parse("2026-06-12T10:00:00-04:00")
        self.assertFalse(
            availability_at_or_before(
                availability,
                same_day_timestamp,
                DateOnlyPolicy.COMPLETED_DATE,
            ).included
        )
        inclusive = availability_at_or_before(
            availability,
            same_day_timestamp,
            DateOnlyPolicy.CALENDAR_DATE_INCLUSIVE,
        )
        self.assertTrue(inclusive.included)
        self.assertIn("date_only_same_day_intraday_safety_not_established", inclusive.warnings)

    def test_offset_datetimes_compare_as_instants(self) -> None:
        availability = TemporalValue.parse("2026-06-12T10:00:00-04:00")
        cutoff = TemporalValue.parse("2026-06-12T14:00:00Z")
        self.assertTrue(
            availability_at_or_before(
                availability, cutoff, DateOnlyPolicy.COMPLETED_DATE
            ).included
        )

    def test_naive_datetime_duplicate_keys_and_nonfinite_values_fail(self) -> None:
        with self.assertRaises(ValidationError):
            TemporalValue.parse("2026-06-12T10:00:00")
        with self.assertRaises(ValidationError):
            loads_strict('{"a":1,"a":2}')
        with self.assertRaises(ValidationError):
            loads_strict('{"value":NaN}')
        with self.assertRaises(ValidationError):
            dumps_strict({"value": Decimal("Infinity")})

    def test_decimal_rendering_is_finite_and_deterministic(self) -> None:
        self.assertEqual(dumps_strict({"b": Decimal("100.0"), "a": 1}), '{"a":1,"b":100.0}')

    def test_compact_extreme_exponents_and_implementation_temporals_fail(self) -> None:
        with self.assertRaises(ResourceLimitError):
            loads_strict('{"value":1e8500000}')
        with self.assertRaises(ResourceLimitError):
            dumps_strict({"value": Decimal("1e8500000")})
        with self.assertRaises(ValidationError) as date_error:
            dumps_strict({"value": date(2026, 8, 9)})
        self.assertEqual(date_error.exception.code, "invalid_output")
        with self.assertRaises(ValidationError):
            dumps_strict({"value": datetime(2026, 8, 9, tzinfo=timezone.utc)})

    def test_schema_validator_rejects_unknown_nested_fields_and_bounds(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["limit", "child"],
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "child": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string"}},
                },
            },
        }
        validate_schema({"limit": 10, "child": {"name": "ok"}}, schema)
        with self.assertRaises(ValidationError):
            validate_schema({"limit": 11, "child": {"unexpected": True}}, schema)


if __name__ == "__main__":
    unittest.main()
