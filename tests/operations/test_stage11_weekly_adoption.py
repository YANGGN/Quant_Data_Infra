from __future__ import annotations

import inspect
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.macro.stage11_eia import (
    parse_eia_weekly_response,
    prepare_eia_weekly_capture,
)
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.migrations import initialize_all
from quant_data.operations import stage11_weekly_adoption as adoption
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage11_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import canonical_path_uri


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 8, 15, 0, 56, 56, 838282, tzinfo=timezone.utc)


def _weekly_body() -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 2,
                "dateFormat": "YYYY-MM-DD",
                "frequency": "weekly",
                "data": [
                    {
                        "period": "2020-01-03",
                        "value": "2.5",
                        "series": "PET.WCESTUS1.W",
                        "units": "Dollars per Gallon",
                    },
                    {
                        "period": "2020-01-10",
                        "value": "2.6",
                        "series": "PET.WCESTUS1.W",
                        "units": "Dollars per Gallon",
                    },
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _stamps(path: Path) -> tuple[tuple[int, int, int, int, int, int, int], ...]:
    result: list[tuple[int, int, int, int, int, int, int]] = []
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    ):
        if not candidate.exists():
            result.append((-1, -1, -1, -1, -1, -1, -1))
            continue
        info = candidate.stat()
        result.append(
            (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
        )
    return tuple(result)


def _read_rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(
        "file:" + path.as_posix() + "?mode=ro&immutable=1",
        uri=True,
        isolation_level=None,
    )
    try:
        return list(connection.execute(sql))
    finally:
        connection.close()


class Stage11WeeklyAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.source_map = explicit_store_map(self.root / "retained")
        self.target_map = explicit_store_map(self.root / "canonical")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.source_map, self.registry, applied_at="2026-08-17T12:00:00Z")
        initialize_all(self.target_map, self.registry, applied_at="2026-08-17T12:00:00Z")
        stage11_registry = stage11_registry_profile(self.registry)
        source_importer = Stage11MacroImporter(
            self.source_map,
            stage11_registry,
            clock=lambda: _NOW,
        )
        self.body = _weekly_body()
        receipt = source_importer.publish_prepared(
            source_importer.prepare_eia_weekly_history(
                parse_eia_weekly_response(
                    body=self.body,
                    prepared=prepare_eia_weekly_capture(),
                )
            )
        )
        self.assertEqual(receipt.outcome, "succeeded")
        source_path = self.source_map.path("macro")
        self._seal_fixture_source()
        response_sha256 = _read_rows(
            source_path,
            "SELECT response_sha256 FROM stage11_eia_weekly_captures;",
        )[0][0]
        with adoption._source_connection(source_path) as source_connection:
            bundle_sha256 = adoption._bundle_sha256(
                adoption._read_source_bundle(source_connection)
            )
        self.expectation = adoption.Stage11WeeklySourceExpectation(
            current_count=2,
            version_count=2,
            first_period="2020-01-03",
            last_period="2020-01-10",
            response_sha256=str(response_sha256),
            bundle_sha256=bundle_sha256,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        expectation: adoption.Stage11WeeklySourceExpectation | None = None,
    ) -> adoption.Stage11WeeklyAdoptionRunner:
        return adoption.Stage11WeeklyAdoptionRunner(
            retained_source=self.source_map.path("macro"),
            target_store_map=self.target_map,
            expectation=expectation or self.expectation,
            lock_timeout_seconds=1.0,
        )

    def _seal_fixture_source(self) -> None:
        source = self.source_map.path("macro")
        connection = sqlite3.connect(source)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        for suffix in ("-wal", "-shm", "-journal"):
            source.with_name(source.name + suffix).unlink(missing_ok=True)

    def _assert_source_bundle_drift_rejected(
        self,
        *,
        drop_trigger: str,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> None:
        source = self.source_map.path("macro")
        connection = sqlite3.connect(source)
        try:
            connection.execute(f"DROP TRIGGER {drop_trigger}")
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()
        self._seal_fixture_source()
        before = mutation_fingerprint(self.target_map)

        with mock.patch.object(
            adoption,
            "StoreWriteLock",
            side_effect=AssertionError("target lock acquired after source preflight"),
        ):
            with self.assertRaisesRegex(
                ConflictError,
                "Stage 11 weekly retained bundle digest is invalid",
            ):
                self._runner().run()

        self.assertEqual(before, mutation_fingerprint(self.target_map))

    def test_adopts_complete_lineage_then_replays_without_writes(self) -> None:
        source = self.source_map.path("macro")
        source_stamps = _stamps(source)
        source_capture_before = _read_rows(
            source,
            "SELECT capture_id,response_sha256,hex(response_bytes),run_id FROM stage11_eia_weekly_captures;",
        )
        lock_paths: list[str] = []
        real_lock = adoption.StoreWriteLock

        class RecordingLock:
            def __init__(self, path: Path, *, timeout_seconds: float) -> None:
                lock_paths.append(canonical_path_uri(path))
                self._inner = real_lock(path, timeout_seconds=timeout_seconds)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, exc_type, exc, traceback) -> None:
                self._inner.__exit__(exc_type, exc, traceback)

        with mock.patch.object(adoption, "StoreWriteLock", RecordingLock):
            report = self._runner().run()

        self.assertEqual(
            (
                report.outcome,
                report.source_current_count,
                report.source_version_count,
                report.inserted_capture_count,
                report.inserted_version_count,
                report.inserted_current_count,
            ),
            ("adopted", 2, 2, 1, 2, 2),
        )
        self.assertEqual(
            lock_paths,
            [canonical_path_uri(self.target_map.path("macro"))],
        )
        self.assertEqual(source_stamps, _stamps(source))
        self.assertEqual(
            source_capture_before,
            _read_rows(
                source,
                "SELECT capture_id,response_sha256,hex(response_bytes),run_id FROM stage11_eia_weekly_captures;",
            ),
        )

        tables = (
            "ingestion_runs",
            "ingestion_run_outputs",
            "ingestion_artifacts",
            "ingestion_snapshots",
            "ingestion_snapshot_artifacts",
            "data_quality_results",
            "stage11_eia_weekly_captures",
            "stage11_eia_weekly_observation_versions",
            "stage11_eia_weekly_observations",
        )
        source_rows = {
            table: _read_rows(
                self.source_map.path("macro"),
                f"SELECT * FROM {table} WHERE "
                + (
                    "run_id IN (SELECT run_id FROM stage11_eia_weekly_captures)"
                    if table
                    in {
                        "ingestion_runs",
                        "ingestion_run_outputs",
                        "ingestion_artifacts",
                        "ingestion_snapshots",
                        "data_quality_results",
                    }
                    else "1=1"
                )
                + ";",
            )
            for table in tables
        }
        target_rows = {
            table: _read_rows(
                self.target_map.path("macro"),
                f"SELECT * FROM {table} WHERE "
                + (
                    "run_id IN (SELECT run_id FROM stage11_eia_weekly_captures)"
                    if table
                    in {
                        "ingestion_runs",
                        "ingestion_run_outputs",
                        "ingestion_artifacts",
                        "ingestion_snapshots",
                        "data_quality_results",
                    }
                    else "1=1"
                )
                + ";",
            )
            for table in tables
        }
        self.assertEqual(source_rows, target_rows)
        target_path = self.target_map.path("macro")
        self.assertEqual(
            _read_rows(target_path, "PRAGMA integrity_check;"),
            [("ok",)],
        )
        self.assertEqual(_read_rows(target_path, "PRAGMA foreign_key_check;"), [])

        before_replay = mutation_fingerprint(self.target_map)
        replay = self._runner().run()
        self.assertEqual(
            (replay.outcome, replay.inserted_capture_count, replay.inserted_version_count),
            ("unchanged", 0, 0),
        )
        self.assertEqual(before_replay, mutation_fingerprint(self.target_map))

    def test_rejects_partial_target_without_repairing_or_writing(self) -> None:
        self.assertEqual(self._runner().run().outcome, "adopted")
        target = self.target_map.path("macro")
        connection = sqlite3.connect(target)
        try:
            connection.execute(
                "DELETE FROM stage11_eia_weekly_observations WHERE period='2020-01-10'"
            )
            connection.commit()
        finally:
            connection.close()
        before = mutation_fingerprint(self.target_map)

        with self.assertRaises(ConflictError):
            self._runner().run()

        self.assertEqual(before, mutation_fingerprint(self.target_map))
        self.assertEqual(
            _read_rows(target, "SELECT count(*) FROM stage11_eia_weekly_observations;"),
            [(1,)],
        )

    def test_rejects_unsealed_source_before_target_write(self) -> None:
        before = mutation_fingerprint(self.target_map)
        mismatch = adoption.Stage11WeeklySourceExpectation(
            current_count=2,
            version_count=2,
            first_period="2020-01-03",
            last_period="2020-01-10",
            response_sha256="0" * 64,
            bundle_sha256=self.expectation.bundle_sha256,
        )

        with self.assertRaises(ConflictError):
            self._runner(expectation=mismatch).run()

        self.assertEqual(before, mutation_fingerprint(self.target_map))
        self.assertEqual(
            _read_rows(
                self.target_map.path("macro"),
                "SELECT count(*) FROM stage11_eia_weekly_observation_versions;",
            ),
            [(0,)],
        )

    def test_rejects_source_version_value_drift_before_target_lock(self) -> None:
        self._assert_source_bundle_drift_rejected(
            drop_trigger="stage11_eia_weekly_version_immutable_update",
            sql=(
                "UPDATE stage11_eia_weekly_observation_versions "
                "SET value_text=? WHERE period=?"
            ),
            parameters=("999", "2020-01-03"),
        )

    def test_rejects_source_version_unit_drift_before_target_lock(self) -> None:
        self._assert_source_bundle_drift_rejected(
            drop_trigger="stage11_eia_weekly_version_immutable_update",
            sql=(
                "UPDATE stage11_eia_weekly_observation_versions "
                "SET unit=? WHERE period=?"
            ),
            parameters=("tampered-unit", "2020-01-03"),
        )

    def test_rejects_source_version_content_hash_drift_before_target_lock(self) -> None:
        self._assert_source_bundle_drift_rejected(
            drop_trigger="stage11_eia_weekly_version_immutable_update",
            sql=(
                "UPDATE stage11_eia_weekly_observation_versions "
                "SET content_sha256=? WHERE period=?"
            ),
            parameters=("0" * 64, "2020-01-03"),
        )

    def test_rejects_source_quality_lineage_drift_before_target_lock(self) -> None:
        self._assert_source_bundle_drift_rejected(
            drop_trigger="data_quality_results_immutable_update",
            sql=(
                "UPDATE data_quality_results SET observed_json=? "
                "WHERE quality_result_id=("
                "SELECT quality_result_id FROM data_quality_results "
                "ORDER BY quality_result_id LIMIT 1)"
            ),
            parameters=("{}",),
        )

    def test_canonical_entry_point_is_fixed_and_zero_argument(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(adoption.adopt_retained_stage11_weekly_history).parameters),
            (),
        )
        self.assertEqual(
            adoption.RETAINED_STAGE11_WEEKLY_SOURCE,
            Path(
                "/home/volatility/quant-data-nonprod/"
                "stage10-fmp-market-history-v1/stores/macro.sqlite"
            ),
        )
        self.assertEqual(adoption.CANONICAL_MACRO_STORE, PROJECT_ROOT / "data" / "macro.sqlite")
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(adoption.main(["--source=/tmp/not-accepted.sqlite"]), 64)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
