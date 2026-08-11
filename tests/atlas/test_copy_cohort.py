from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

import quant_data.atlas.projections as atlas_projections
from quant_data.atlas.projections import _public_row
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fixtures import FixtureManifest
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
from quant_data.operations.backup import (
    _required_source_receipt_checks,
    capture_readonly_copies,
)
from quant_data.registry import load_registry
from quant_data.stores import STORE_ROLES, StoreMap, StoreRole, StoreWriteLock, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
REQUIRED_DATASETS_BY_ROLE = {
    "market": "fixture.market.daily_prices",
    "macro": "fixture.macro.gdp_vintages",
    "company": "fixture.company.issuers",
    "news": "fixture.news.items",
}


class _RecordingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.progress_handler_calls: list[tuple[object, int]] = []

    def set_progress_handler(self, handler, n: int) -> None:  # type: ignore[no-untyped-def]
        self.progress_handler_calls.append((handler, n))
        super().set_progress_handler(handler, n)


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class ReadOnlyCopyCohortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.store_map = _stores(self.root / "source")
        initialize_all(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_complete_required_receipt_chains(
        self,
        *,
        include_market_correction: bool = False,
    ) -> None:
        fixtures = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        imports = [(DailyPriceImporter(self.store_map, fixtures), "market.base")]
        if include_market_correction:
            imports.append(
                (DailyPriceImporter(self.store_map, fixtures), "market.correction")
            )
        imports.extend(
            (
                (MacroStage3FixtureImporter(self.store_map, fixtures), "gdp_advance"),
                (CompanyStage4FixtureImporter(self.store_map, fixtures), "sec_initial"),
                (NewsStage4FixtureImporter(self.store_map, fixtures), "news_initial"),
            )
        )
        for importer, fixture_id in imports:
            receipt = importer.import_fixture(fixture_id)
            self.assertEqual(receipt.outcome, "succeeded", fixture_id)

    def _market_checkpoint(self, connection: sqlite3.Connection) -> sqlite3.Row:
        dataset_id = REQUIRED_DATASETS_BY_ROLE[StoreRole.MARKET.value]
        checkpoint = connection.execute(
            """
            SELECT registry.last_successful_run_id, registry.last_semantic_identity,
                   run.snapshot_id, run.artifact_id, snapshot.completeness
            FROM dataset_registry AS registry
            JOIN ingestion_runs AS run
              ON run.run_id=registry.last_successful_run_id
            JOIN ingestion_snapshots AS snapshot
              ON snapshot.snapshot_id=run.snapshot_id
            WHERE registry.dataset_id=?
            """,
            (dataset_id,),
        ).fetchone()
        if checkpoint is None:
            raise AssertionError("fixture did not create a market checkpoint")
        return checkpoint

    def _mutate_market(self, callback) -> None:  # type: ignore[no-untyped-def]
        role = StoreRole.MARKET
        with StoreWriteLock(self.store_map.path(role), timeout_seconds=0.0):
            connection = sqlite3.connect(self.store_map.path(role))
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=OFF")
                callback(connection)
                connection.commit()
            finally:
                connection.close()

    def test_required_source_audit_accepts_real_complete_baseline_plus_validated_delta(self) -> None:
        self._seed_complete_required_receipt_chains(include_market_correction=True)
        checks = _required_source_receipt_checks(
            self.store_map,
            REQUIRED_DATASETS_BY_ROLE,
        )
        by_role = {item.role: item for item in checks}
        market = by_role[StoreRole.MARKET.value]
        self.assertTrue(market.complete)
        self.assertEqual(
            market.completion_basis,
            "complete_baseline_plus_validated_delta",
        )
        self.assertEqual(len(market.checkpoint_sha256), 64)
        self.assertTrue(
            all(
                item.completion_basis == "selected_complete"
                for role, item in by_role.items()
                if role != StoreRole.MARKET.value
            )
        )

        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            checkpoint = self._market_checkpoint(connection)
        self.assertEqual(checkpoint["completeness"], "partial")
        rendered = dumps_strict(market.to_primitive())
        self.assertNotIn(str(checkpoint["last_successful_run_id"]), rendered)
        self.assertNotIn(str(checkpoint["snapshot_id"]), rendered)

    def test_required_source_audit_rejects_partial_checkpoint_without_complete_baseline(self) -> None:
        self._seed_complete_required_receipt_chains()

        def corrupt(connection: sqlite3.Connection) -> None:
            checkpoint = self._market_checkpoint(connection)
            connection.execute("DROP TRIGGER ingestion_snapshots_immutable_update")
            connection.execute(
                "UPDATE ingestion_snapshots SET completeness='partial' WHERE snapshot_id=?",
                (checkpoint["snapshot_id"],),
            )

        self._mutate_market(corrupt)
        with self.assertRaisesRegex(ValidationError, "complete baseline"):
            _required_source_receipt_checks(
                self.store_map,
                REQUIRED_DATASETS_BY_ROLE,
            )

    def test_required_source_audit_rejects_invalid_complete_baseline(self) -> None:
        self._seed_complete_required_receipt_chains(include_market_correction=True)
        dataset_id = REQUIRED_DATASETS_BY_ROLE[StoreRole.MARKET.value]

        def corrupt(connection: sqlite3.Connection) -> None:
            baseline = connection.execute(
                """
                SELECT run.snapshot_id, run.artifact_id
                FROM ingestion_runs AS run
                JOIN ingestion_snapshots AS snapshot
                  ON snapshot.snapshot_id=run.snapshot_id
                WHERE run.dataset_id=? AND snapshot.completeness='complete'
                ORDER BY run.completed_at
                LIMIT 1
                """,
                (dataset_id,),
            ).fetchone()
            if baseline is None:
                raise AssertionError("fixture did not create a complete baseline")
            connection.execute(
                "DROP TRIGGER ingestion_snapshot_artifacts_immutable_delete"
            )
            connection.execute(
                """
                DELETE FROM ingestion_snapshot_artifacts
                WHERE snapshot_id=? AND artifact_id=?
                """,
                (baseline["snapshot_id"], baseline["artifact_id"]),
            )

        self._mutate_market(corrupt)
        with self.assertRaisesRegex(ValidationError, "checkpoint is unreconciled"):
            _required_source_receipt_checks(
                self.store_map,
                REQUIRED_DATASETS_BY_ROLE,
            )

    def test_required_source_audit_rejects_later_terminal_failure(self) -> None:
        self._seed_complete_required_receipt_chains(include_market_correction=True)
        dataset_id = REQUIRED_DATASETS_BY_ROLE[StoreRole.MARKET.value]

        def corrupt(connection: sqlite3.Connection) -> None:
            checkpoint = self._market_checkpoint(connection)
            completed = datetime.fromisoformat(
                str(
                    connection.execute(
                        "SELECT completed_at FROM ingestion_runs WHERE run_id=?",
                        (checkpoint["last_successful_run_id"],),
                    ).fetchone()[0]
                ).replace("Z", "+00:00")
            )
            failure_at = completed + timedelta(seconds=1)
            connection.execute(
                """
                INSERT INTO ingestion_run_failures (
                    failure_id, run_id, dataset_id, semantic_identity, command,
                    scope_json, status, started_at, completed_at, fetched_count,
                    error_code, code_version
                ) VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, 0, ?, ?)
                """,
                (
                    "copy-cohort-later-failure",
                    "copy-cohort-later-failed-run",
                    dataset_id,
                    "f" * 64,
                    "copy-cohort-test",
                    "{}",
                    failure_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    (failure_at + timedelta(seconds=1))
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "injected_failure",
                    "copy-cohort-test",
                ),
            )

        self._mutate_market(corrupt)
        with self.assertRaisesRegex(
            ValidationError,
            "failed or partial collector receipt",
        ):
            _required_source_receipt_checks(
                self.store_map,
                REQUIRED_DATASETS_BY_ROLE,
            )

    def test_online_backup_progress_deadline_is_sanitized_and_releases_locks(self) -> None:
        before = mutation_fingerprint(self.store_map)
        phases: list[str] = []
        monotonic_values = iter((0.0, 31.0))

        def progress(phase: str) -> None:
            phases.append(phase)
            if phase.endswith("_backup_progress") and next(monotonic_values) > 30.0:
                raise ResourceLimitError("deadline exceeded")

        with self.assertRaisesRegex(
            ValidationError,
            "source copy deadline exceeded during SQLite online backup",
        ):
            capture_readonly_copies(
                self.store_map,
                self.registry,
                self.root / "deadline-copies",
                datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
                progress_check=progress,
            )
        self.assertTrue(
            any(phase.endswith("_backup_progress") for phase in phases),
            phases,
        )
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        for role in STORE_ROLES:
            with StoreWriteLock(self.store_map.path(role), timeout_seconds=0.0):
                pass

    def test_complete_online_copy_is_query_only_neutral_and_releases_locks(self) -> None:
        before = mutation_fingerprint(self.store_map)
        calls = 0

        def clock() -> datetime:
            nonlocal calls
            value = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc) + timedelta(
                seconds=calls
            )
            calls += 1
            return value

        cohort = capture_readonly_copies(
            self.store_map,
            self.registry,
            self.root / "copies",
            clock,
        )
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        self.assertFalse(cohort.cross_store_atomic)
        self.assertEqual(
            tuple(receipt.role for receipt in cohort.receipts),
            tuple(role.value for role in STORE_ROLES),
        )
        self.assertEqual(
            {item.role for item in cohort.lock_order},
            {role.value for role in STORE_ROLES},
        )
        self.assertEqual(len(cohort.lock_order), len(STORE_ROLES))
        self.assertEqual(len({item.completed_at for item in cohort.receipts}), 4)
        self.assertEqual(
            cohort.source_logical_manifest_sha256,
            cohort.copy_logical_manifest_sha256,
        )
        self.assertEqual(
            cohort.source_mutation_before_sha256,
            cohort.source_mutation_after_sha256,
        )
        self.assertNotIn(str(self.root), dumps_strict(cohort.to_primitive()))

        for role in STORE_ROLES:
            self.assertTrue(cohort.copy_store_map.path(role).is_file())
            with read_connection(cohort.copy_store_map, role) as connection:
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(Exception):
                    connection.execute("CREATE TABLE forbidden_copy_write(value TEXT)")
            with StoreWriteLock(self.store_map.path(role), timeout_seconds=0.0):
                pass

    def test_projection_query_progress_deadline_is_sanitized_and_cleared(self) -> None:
        connection = sqlite3.connect(":memory:", factory=_RecordingConnection)
        try:
            self.assertIsInstance(connection, _RecordingConnection)
            connection.execute("CREATE TABLE values_table(value INTEGER NOT NULL)")
            connection.executemany(
                "INSERT INTO values_table(value) VALUES (?)",
                [(value,) for value in range(64)],
            )
            monotonic_values = iter((0.0, 31.0))
            with self.assertRaisesRegex(
                ValidationError,
                "projection deadline exceeded during SQLite query",
            ):
                atlas_projections._query_rows(
                    connection,
                    """
                    SELECT left_values.value
                    FROM values_table AS left_values
                    CROSS JOIN values_table AS right_values
                    LIMIT ?
                    """,
                    max_rows=5_000,
                    message="test row bound",
                    deadline=30.0,
                    monotonic=lambda: next(monotonic_values),
                )
            self.assertGreaterEqual(len(connection.progress_handler_calls), 2)
            handler, steps = connection.progress_handler_calls[0]
            self.assertTrue(callable(handler))
            self.assertEqual(steps, 1_000)
            self.assertEqual(connection.progress_handler_calls[-1], (None, 0))
        finally:
            connection.close()

    def test_projection_public_row_loop_deadline_is_sanitized(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "projection deadline exceeded during SQLite query",
        ):
            atlas_projections._public_rows(
                ({"value": "fixture"},),
                (("value", "string", False),),
                deadline=30.0,
                monotonic=lambda: 31.0,
            )

    def test_public_row_uses_sqlite_row_keys_not_row_values(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT
                    'instrument-1' AS instrument_id,
                    '2026-08-10' AS trade_date,
                    '2026-08-10' AS available_at,
                    'date' AS available_precision,
                    '2026-08-10T12:00:00Z' AS captured_at,
                    'datetime' AS captured_precision,
                    7 AS volume,
                    '101.25' AS close
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(
            _public_row(
                row,
                (
                    ("instrument_id", "string", False),
                    ("trade_date", "date", False),
                    ("available_at", "temporal_string", False),
                    ("available_precision", "temporal_precision", False),
                    ("captured_at", "datetime", False),
                    ("captured_precision", "temporal_precision", False),
                    ("volume", "integer", False),
                    ("close", "decimal_string", False),
                ),
            ),
            {
                "instrument_id": "instrument-1",
                "trade_date": "2026-08-10",
                "available_at": "2026-08-10",
                "available_precision": "date",
                "captured_at": "2026-08-10T12:00:00Z",
                "captured_precision": "datetime",
                "volume": 7,
                "close": "101.25",
            },
        )


if __name__ == "__main__":
    unittest.main()
