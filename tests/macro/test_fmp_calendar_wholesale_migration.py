"""Upgrade and integrity checks for wholesale FMP calendar evidence DDL."""

from __future__ import annotations

import hashlib
import sqlite3
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES = tuple(
    PROJECT_ROOT / f"quant_data/migrations/macro/{name}"
    for name in (
        "0013_live_gdp_cpi_vintages.sql",
        "0014_live_employment_vintages.sql",
        "0015_live_macro_history_extension.sql",
    )
)
RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0016_fmp_calendar_wholesale_evidence.sql"

WHOLESALE_DATASET = "macro.fmp.economic_calendar_evidence"
FIXTURE_DATASET = "fixture.macro.economic_calendar"
WHOLESALE_COMMAND = "fmp.macro.us_economic_calendar_wholesale"
_CAPTURED_AT = "2026-08-18T18:00:00Z"
_NORMALIZATION_VERSION = "fmp_us_calendar_wholesale_evidence_v1"
_SOURCE_REFERENCE = "fmp/economic-calendar/us-wholesale.json"


def _digest(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _control_plane(connection: sqlite3.Connection) -> None:
    """Create the real 0001/0003 shape used by this trigger's lineage inputs."""

    connection.executescript(
        """
        CREATE TABLE dataset_registry (
            dataset_id TEXT PRIMARY KEY,
            store_role TEXT NOT NULL CHECK (store_role = 'macro'),
            layer TEXT NOT NULL CHECK (layer IN ('evidence', 'canonical', 'research')),
            schema_version TEXT NOT NULL,
            relations_json TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            registered_at TEXT NOT NULL,
            last_successful_run_id TEXT,
            last_semantic_identity TEXT
        ) STRICT;
        INSERT INTO dataset_registry (
            dataset_id, store_role, layer, schema_version, relations_json, active,
            registered_at, last_successful_run_id, last_semantic_identity
        ) VALUES
            (
                'fixture.macro.economic_calendar', 'macro', 'evidence', 'test',
                '[]', 1, '2026-08-18T18:00:00Z', NULL, NULL
            ),
            (
                'macro.fmp.economic_calendar_evidence', 'macro', 'evidence',
                'test', '[]', 1, '2026-08-18T18:00:00Z', NULL, NULL
            );

        CREATE TABLE ingestion_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
            semantic_identity TEXT NOT NULL,
            command TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded')),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_id TEXT,
            snapshot_id TEXT,
            fetched_count INTEGER NOT NULL DEFAULT 0 CHECK (fetched_count >= 0),
            written_count INTEGER NOT NULL DEFAULT 0 CHECK (written_count >= 0),
            warnings_json TEXT NOT NULL DEFAULT '[]',
            code_version TEXT NOT NULL,
            UNIQUE (dataset_id, semantic_identity)
        ) STRICT;

        CREATE TABLE ingestion_artifacts (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
            dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
            content_sha256 TEXT NOT NULL,
            media_type TEXT NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count > 0),
            source_reference TEXT NOT NULL,
            request_scope_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            captured_precision TEXT NOT NULL CHECK (
                captured_precision IN ('date', 'datetime')
            ),
            normalization_version TEXT NOT NULL
        ) STRICT;

        CREATE TABLE ingestion_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
            dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
            semantic_identity TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            captured_at TEXT NOT NULL,
            captured_precision TEXT NOT NULL CHECK (
                captured_precision IN ('date', 'datetime')
            ),
            validation_state TEXT NOT NULL CHECK (
                validation_state IN ('validated', 'rejected')
            ),
            warnings_json TEXT NOT NULL,
            UNIQUE (dataset_id, semantic_identity)
        ) STRICT;

        CREATE TABLE ingestion_snapshot_artifacts (
            snapshot_id TEXT NOT NULL REFERENCES ingestion_snapshots(snapshot_id),
            artifact_id TEXT NOT NULL REFERENCES ingestion_artifacts(artifact_id),
            artifact_ordinal INTEGER NOT NULL CHECK (artifact_ordinal > 0),
            PRIMARY KEY (snapshot_id, artifact_id),
            UNIQUE (snapshot_id, artifact_ordinal)
        ) STRICT;

        CREATE TABLE ingestion_run_outputs (
            run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
            dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
            semantic_identity TEXT NOT NULL,
            PRIMARY KEY (run_id, dataset_id)
        ) STRICT;
        """
    )


def _insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    dataset_id: str,
    semantic_identity: str,
    command: str,
    scope_json: str,
    status: str = "running",
) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_runs (
            run_id, dataset_id, semantic_identity, command, scope_json, status,
            started_at, completed_at, artifact_id, snapshot_id, fetched_count,
            written_count, warnings_json, code_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, '[]', 'test')
        """,
        (
            run_id,
            dataset_id,
            semantic_identity,
            command,
            scope_json,
            status,
            _CAPTURED_AT,
        ),
    )


def _insert_output(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    dataset_id: str,
    semantic_identity: str,
) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_run_outputs (run_id, dataset_id, semantic_identity)
        VALUES (?, ?, ?)
        """,
        (run_id, dataset_id, semantic_identity),
    )


def _insert_capture(
    connection: sqlite3.Connection,
    *,
    capture_id: str,
    run_id: str,
    artifact_id: str,
    snapshot_id: str,
    response: bytes,
    semantic_identity: str,
    start_date: str,
    end_date: str,
    row_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO fmp_economic_calendar_captures (
            capture_id, provider, source_resource, request_country,
            request_start_date, request_end_date, response_sha256,
            response_bytes, semantic_identity, captured_at, captured_precision,
            normalization_version, row_count, run_id, artifact_id, snapshot_id
        ) VALUES (
            ?, 'fmp', 'fmp_stable_economic_calendar', 'US', ?, ?, ?, ?, ?, ?,
            'datetime', ?, ?, ?, ?, ?
        )
        """,
        (
            capture_id,
            start_date,
            end_date,
            _digest(response),
            response,
            semantic_identity,
            _CAPTURED_AT,
            _NORMALIZATION_VERSION,
            row_count,
            run_id,
            artifact_id,
            snapshot_id,
        ),
    )


def _insert_row(
    connection: sqlite3.Connection,
    *,
    capture_id: str,
    source_row: int,
    raw_row_json: str,
    event_name: str = "Raw",
) -> None:
    connection.execute(
        """
        INSERT INTO fmp_economic_calendar_rows (
            capture_id, source_row, row_sha256, raw_row_json, event_at, country,
            event_name
        ) VALUES (?, ?, ?, ?, '2024-04-25 12:30:00', 'US', ?)
        """,
        (capture_id, source_row, _digest(raw_row_json), raw_row_json, event_name),
    )


def _insert_artifact(
    connection: sqlite3.Connection,
    *,
    artifact_id: str,
    run_id: str,
    response: bytes,
    scope_json: str,
    content_sha256: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_artifacts (
            artifact_id, run_id, dataset_id, content_sha256, media_type,
            byte_count, source_reference, request_scope_json, captured_at,
            captured_precision, normalization_version
        ) VALUES (?, ?, ?, ?, 'application/json', ?, ?, ?, ?, 'datetime', ?)
        """,
        (
            artifact_id,
            run_id,
            WHOLESALE_DATASET,
            _digest(response) if content_sha256 is None else content_sha256,
            len(response),
            _SOURCE_REFERENCE,
            scope_json,
            _CAPTURED_AT,
            _NORMALIZATION_VERSION,
        ),
    )


def _insert_snapshot(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    run_id: str,
    semantic_identity: str,
    scope_json: str,
    row_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_snapshots (
            snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
            completeness, row_count, captured_at, captured_precision,
            validation_state, warnings_json
        ) VALUES (?, ?, ?, ?, ?, 'complete', ?, ?, 'datetime', 'validated', '[]')
        """,
        (
            snapshot_id,
            run_id,
            WHOLESALE_DATASET,
            semantic_identity,
            scope_json,
            row_count,
            _CAPTURED_AT,
        ),
    )


def _legacy_capture(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO macro_live_vintage_captures (
            capture_id, provider, source_resource, media_type, response_sha256,
            response_bytes, semantic_identity, captured_at, captured_precision,
            source_published_at, source_published_precision, availability_basis,
            normalization_version, observation_count
        ) VALUES (
            'legacy-bls', 'bls', 'fixture/bls', 'application/json', ?, X'7B7D',
            ?, '2026-08-18T18:00:00Z', 'datetime', NULL, NULL,
            'local_capture', 'macro.live_vintage.v1', 1
        )
        """,
        (_digest("legacy-response"), _digest("legacy-semantic")),
    )


class FmpWholesaleCalendarMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys=ON")
        for resource in RESOURCES:
            self.connection.executescript(resource.read_text(encoding="utf-8"))
        _legacy_capture(self.connection)
        _control_plane(self.connection)
        self.connection.executescript(RESOURCE.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.connection.close()

    def _begin(self) -> None:
        self.connection.commit()
        self.connection.execute("BEGIN")

    def test_upgrade_from_0013_through_0015_preserves_legacy_rows(self) -> None:
        self.assertEqual(
            self.connection.execute(
                "SELECT provider FROM macro_live_vintage_captures WHERE capture_id='legacy-bls'"
            ).fetchone()[0],
            "bls",
        )
        self.assertEqual(
            {
                row[0]
                for row in self.connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE 'fmp_economic_calendar_%'
                    """
                )
            },
            {"fmp_economic_calendar_captures", "fmp_economic_calendar_rows"},
        )
        self.assertEqual(list(self.connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(
            self.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
        )

    def test_capture_snapshot_lineage_complete_rows_and_immutability(self) -> None:
        response = b'[{"date":"2024-04-25 12:30:00","country":"US","event":"Raw"}]'
        semantic = _digest("wholesale-semantic")
        row_json = '{"country":"US","date":"2024-04-25 12:30:00","event":"Raw"}'
        scope = '{"fixture":"valid"}'
        self._begin()
        _insert_run(
            self.connection,
            run_id="run-1",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
            command=WHOLESALE_COMMAND,
            scope_json=scope,
        )
        _insert_output(
            self.connection,
            run_id="run-1",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
        )
        _insert_capture(
            self.connection,
            capture_id="capture-1",
            run_id="run-1",
            artifact_id="artifact-1",
            snapshot_id="snapshot-1",
            response=response,
            semantic_identity=semantic,
            start_date="2024-04-01",
            end_date="2024-06-30",
            row_count=1,
        )
        _insert_row(
            self.connection,
            capture_id="capture-1",
            source_row=1,
            raw_row_json=row_json,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_row(
                self.connection,
                capture_id="capture-1",
                source_row=2,
                raw_row_json='{"event":"Extra"}',
                event_name="Extra",
            )
        _insert_artifact(
            self.connection,
            artifact_id="artifact-1",
            run_id="run-1",
            response=response,
            scope_json=scope,
        )
        _insert_snapshot(
            self.connection,
            snapshot_id="snapshot-1",
            run_id="run-1",
            semantic_identity=semantic,
            scope_json=scope,
            row_count=1,
        )
        self.connection.execute(
            """
            UPDATE ingestion_runs
            SET status='succeeded', completed_at=?
            WHERE run_id='run-1' AND status='running'
            """,
            (_CAPTURED_AT,),
        )
        self.connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE fmp_economic_calendar_captures SET row_count=0"
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM fmp_economic_calendar_rows")
        self.connection.rollback()
        self.assertEqual(list(self.connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(
            self.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
        )

    def test_snapshot_refuses_incomplete_capture_membership(self) -> None:
        semantic = _digest("incomplete-semantic")
        response = b"[]"
        scope = '{"fixture":"incomplete"}'
        self._begin()
        _insert_run(
            self.connection,
            run_id="run-2",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
            command=WHOLESALE_COMMAND,
            scope_json=scope,
        )
        _insert_output(
            self.connection,
            run_id="run-2",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
        )
        _insert_capture(
            self.connection,
            capture_id="capture-2",
            run_id="run-2",
            artifact_id="artifact-2",
            snapshot_id="snapshot-2",
            response=response,
            semantic_identity=semantic,
            start_date="2024-07-01",
            end_date="2024-09-30",
            row_count=1,
        )
        _insert_artifact(
            self.connection,
            artifact_id="artifact-2",
            run_id="run-2",
            response=response,
            scope_json=scope,
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "lineage is incomplete"):
            _insert_snapshot(
                self.connection,
                snapshot_id="snapshot-2",
                run_id="run-2",
                semantic_identity=semantic,
                scope_json=scope,
                row_count=1,
            )
        self.connection.rollback()

    def test_snapshot_refuses_mismatched_artifact_hash_with_complete_raw_capture(self) -> None:
        response = b'[{"date":"2024-04-25 12:30:00","country":"US","event":"Raw"}]'
        semantic = _digest("mismatched-artifact-semantic")
        row_json = '{"country":"US","date":"2024-04-25 12:30:00","event":"Raw"}'
        scope = '{"fixture":"mismatched-artifact"}'
        self._begin()
        _insert_run(
            self.connection,
            run_id="run-3",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
            command=WHOLESALE_COMMAND,
            scope_json=scope,
        )
        _insert_output(
            self.connection,
            run_id="run-3",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
        )
        _insert_capture(
            self.connection,
            capture_id="capture-3",
            run_id="run-3",
            artifact_id="artifact-3",
            snapshot_id="snapshot-3",
            response=response,
            semantic_identity=semantic,
            start_date="2024-10-01",
            end_date="2024-12-31",
            row_count=1,
        )
        _insert_row(
            self.connection,
            capture_id="capture-3",
            source_row=1,
            raw_row_json=row_json,
        )
        _insert_artifact(
            self.connection,
            artifact_id="artifact-3",
            run_id="run-3",
            response=response,
            scope_json=scope,
            content_sha256=_digest("different raw response"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "lineage is incomplete"):
            _insert_snapshot(
                self.connection,
                snapshot_id="snapshot-3",
                run_id="run-3",
                semantic_identity=semantic,
                scope_json=scope,
                row_count=1,
            )
        self.connection.rollback()

    def test_snapshot_refuses_forged_fixture_run_with_wholesale_output(self) -> None:
        response = b'[{"date":"2024-04-25 12:30:00","country":"US","event":"Raw"}]'
        semantic = _digest("forged-run-semantic")
        row_json = '{"country":"US","date":"2024-04-25 12:30:00","event":"Raw"}'
        snapshot_scope = '{"fixture":"forged-snapshot"}'
        self._begin()
        _insert_run(
            self.connection,
            run_id="forged-run",
            dataset_id=FIXTURE_DATASET,
            semantic_identity=semantic,
            command="forged.command",
            scope_json='{"fixture":"wrong-scope"}',
        )
        # The forged run advertises wholesale output and otherwise every raw
        # capture/artifact/snapshot field agrees. Its primary contract does not.
        _insert_output(
            self.connection,
            run_id="forged-run",
            dataset_id=WHOLESALE_DATASET,
            semantic_identity=semantic,
        )
        _insert_capture(
            self.connection,
            capture_id="forged-capture",
            run_id="forged-run",
            artifact_id="forged-artifact",
            snapshot_id="forged-snapshot",
            response=response,
            semantic_identity=semantic,
            start_date="2024-10-01",
            end_date="2024-12-31",
            row_count=1,
        )
        _insert_row(
            self.connection,
            capture_id="forged-capture",
            source_row=1,
            raw_row_json=row_json,
        )
        _insert_artifact(
            self.connection,
            artifact_id="forged-artifact",
            run_id="forged-run",
            response=response,
            scope_json=snapshot_scope,
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "lineage is incomplete"):
            _insert_snapshot(
                self.connection,
                snapshot_id="forged-snapshot",
                run_id="forged-run",
                semantic_identity=semantic,
                scope_json=snapshot_scope,
                row_count=1,
            )
        self.connection.rollback()


if __name__ == "__main__":
    unittest.main()
