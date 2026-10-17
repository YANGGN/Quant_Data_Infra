from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import hashlib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0012_stage11_bea_eia_live_history.sql"
RESOURCE_SHA256 = "4f29eed2d73fcb1aa6f3c519a3360c132658cf152341261a18d4332e140fd0a5"
NOW = "2026-08-12T12:00:00Z"


@dataclass(frozen=True)
class _Capture:
    capture_id: str
    artifact_id: str
    snapshot_id: str
    run_id: str


class Stage11MigrationTests(unittest.TestCase):
    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _connection() -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE dataset_registry(dataset_id TEXT PRIMARY KEY);
            CREATE TABLE ingestion_runs(run_id TEXT PRIMARY KEY);
            INSERT INTO dataset_registry VALUES
              ('macro.bea.nipa_history_evidence'),
              ('macro.bea.nipa_history'),
              ('macro.eia.electricity_retail_history_evidence'),
              ('macro.eia.electricity.retail_history'),
              ('macro.eia.petroleum_weekly_stock_history_evidence'),
              ('macro.eia.petroleum_weekly_stock_history');
            """
        )
        connection.executescript(RESOURCE.read_text(encoding="utf-8"))
        return connection

    @staticmethod
    def _add_run(connection: sqlite3.Connection, suffix: str) -> str:
        run_id = f"stage11-run-{suffix}"
        connection.execute("INSERT INTO ingestion_runs VALUES (?)", (run_id,))
        return run_id

    def _assert_aborts(self, connection: sqlite3.Connection, operation) -> None:
        """Keep each hostile direct-insert probe from polluting later probes."""

        connection.execute("SAVEPOINT stage11_hostile_probe")
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                operation()
        finally:
            connection.execute("ROLLBACK TO stage11_hostile_probe")
            connection.execute("RELEASE stage11_hostile_probe")

    def _insert_bea_capture(
        self,
        connection: sqlite3.Connection,
        *,
        suffix: str,
        run_id: str,
        table_name: str,
        series_code: str,
        response_seed: str | None = None,
    ) -> _Capture:
        capture = _Capture(
            capture_id=f"stage11-bea-capture-{suffix}",
            artifact_id=f"stage11-bea-artifact-{suffix}",
            snapshot_id=f"stage11-bea-snapshot-{suffix}",
            run_id=run_id,
        )
        response_seed = response_seed or suffix
        connection.execute(
            """
            INSERT INTO stage11_bea_nipa_captures (
                capture_id, dataset_id, provider, endpoint_path, table_name,
                series_code, request_scope_json, request_scope_sha256,
                response_sha256, response_bytes, semantic_identity,
                completeness, availability_basis, artifact_id, snapshot_id,
                captured_at, captured_precision, row_count,
                normalization_version, run_id
            ) VALUES (?, 'macro.bea.nipa_history_evidence', 'bea', '/api/data/',
                      ?, ?, '{}', ?, ?, ?, ?, 'complete', 'local_capture', ?, ?,
                      ?, 'datetime', 1, 'stage11.bea.nipa.v1', ?)
            """,
            (
                capture.capture_id,
                table_name,
                series_code,
                self._digest(f"bea-request:{suffix}"),
                self._digest(f"bea-response:{response_seed}"),
                b"{}",
                self._digest(f"bea-semantic:{suffix}"),
                capture.artifact_id,
                capture.snapshot_id,
                NOW,
                capture.run_id,
            ),
        )
        return capture

    def _insert_retail_capture(
        self,
        connection: sqlite3.Connection,
        *,
        suffix: str,
        run_id: str,
        page_offset: int,
        response_seed: str | None = None,
    ) -> _Capture:
        capture = _Capture(
            capture_id=f"stage11-retail-capture-{suffix}",
            artifact_id=f"stage11-retail-artifact-{suffix}",
            snapshot_id=f"stage11-retail-snapshot-{suffix}",
            run_id=run_id,
        )
        response_seed = response_seed or suffix
        connection.execute(
            """
            INSERT INTO stage11_eia_retail_captures (
                capture_id, dataset_id, provider, endpoint_path,
                request_scope_json, request_scope_sha256, response_sha256,
                response_bytes, semantic_identity, completeness,
                availability_basis, state_id, sector_id, page_offset,
                page_length, page_total, artifact_id, snapshot_id, captured_at,
                captured_precision, row_count, normalization_version, run_id
            ) VALUES (?, 'macro.eia.electricity_retail_history_evidence', 'eia',
                      '/v2/electricity/retail-sales/data', '{}', ?, ?, ?, ?,
                      'complete', 'local_capture', 'US', 'ALL', ?, 5000, 10000,
                      ?, ?, ?, 'datetime', 1, 'stage11.eia.retail.v1', ?)
            """,
            (
                capture.capture_id,
                self._digest(f"retail-request:{suffix}"),
                self._digest(f"retail-response:{response_seed}"),
                b"{}",
                self._digest(f"retail-semantic:{suffix}"),
                page_offset,
                capture.artifact_id,
                capture.snapshot_id,
                NOW,
                capture.run_id,
            ),
        )
        return capture

    def _insert_weekly_capture(
        self,
        connection: sqlite3.Connection,
        *,
        suffix: str,
        run_id: str,
        response_seed: str | None = None,
    ) -> _Capture:
        capture = _Capture(
            capture_id=f"stage11-weekly-capture-{suffix}",
            artifact_id=f"stage11-weekly-artifact-{suffix}",
            snapshot_id=f"stage11-weekly-snapshot-{suffix}",
            run_id=run_id,
        )
        response_seed = response_seed or suffix
        connection.execute(
            """
            INSERT INTO stage11_eia_weekly_captures (
                capture_id, dataset_id, provider, endpoint_path,
                provider_series_id, request_scope_json, request_scope_sha256,
                response_sha256, response_bytes, semantic_identity,
                completeness, availability_basis, artifact_id, snapshot_id,
                captured_at, captured_precision, row_count,
                normalization_version, run_id
            ) VALUES (?, 'macro.eia.petroleum_weekly_stock_history_evidence',
                      'eia', '/v2/seriesid/PET.WCESTUS1.W', 'PET.WCESTUS1.W',
                      '{}', ?, ?, ?, ?, 'complete', 'local_capture', ?, ?, ?,
                      'datetime', 1, 'stage11.eia.weekly.v1', ?)
            """,
            (
                capture.capture_id,
                self._digest(f"weekly-request:{suffix}"),
                self._digest(f"weekly-response:{response_seed}"),
                b"{}",
                self._digest(f"weekly-semantic:{suffix}"),
                capture.artifact_id,
                capture.snapshot_id,
                NOW,
                capture.run_id,
            ),
        )
        return capture

    def _insert_bea_version(
        self,
        connection: sqlite3.Connection,
        *,
        capture: _Capture,
        suffix: str,
        period: str = "2026Q1",
        correction_sequence: int = 1,
        supersedes_version_id: str | None = None,
        canonical_series_id: str = "macro.gdp.real_qoq_saar_pct",
        table_name: str = "T10101",
        series_code: str = "A191RL",
        artifact_id: str | None = None,
        snapshot_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        version_id = f"stage11-bea-version-{suffix}"
        connection.execute(
            """
            INSERT INTO stage11_bea_nipa_observation_versions (
                version_id, canonical_series_id, table_name, series_code,
                period, value_text, unit, unit_multiplier, available_at,
                available_precision, captured_at, captured_precision,
                correction_sequence, supersedes_version_id, capture_id,
                artifact_id, snapshot_id, run_id, source_row
            ) VALUES (?, ?, ?, ?, ?, '1', 'percent', 0, ?, 'datetime', ?,
                      'datetime', ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                version_id,
                canonical_series_id,
                table_name,
                series_code,
                period,
                NOW,
                NOW,
                correction_sequence,
                supersedes_version_id,
                capture.capture_id,
                artifact_id or capture.artifact_id,
                snapshot_id or capture.snapshot_id,
                run_id or capture.run_id,
            ),
        )
        return version_id

    def _insert_retail_version(
        self,
        connection: sqlite3.Connection,
        *,
        capture: _Capture,
        suffix: str,
        period: str = "2026-07",
        correction_sequence: int = 1,
        supersedes_version_id: str | None = None,
        canonical_series_id: str = "macro.eia.electricity.retail_sales",
        metric: str = "sales",
        artifact_id: str | None = None,
        snapshot_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        version_id = f"stage11-retail-version-{suffix}"
        connection.execute(
            """
            INSERT INTO stage11_eia_retail_observation_versions (
                version_id, canonical_series_id, metric, period, state_id,
                sector_id, value_text, unit, available_at, available_precision,
                captured_at, captured_precision, correction_sequence,
                supersedes_version_id, capture_id, artifact_id, snapshot_id,
                run_id, source_row
            ) VALUES (?, ?, ?, ?, 'US', 'ALL', '1', 'MWh', ?, 'datetime', ?,
                      'datetime', ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                version_id,
                canonical_series_id,
                metric,
                period,
                NOW,
                NOW,
                correction_sequence,
                supersedes_version_id,
                capture.capture_id,
                artifact_id or capture.artifact_id,
                snapshot_id or capture.snapshot_id,
                run_id or capture.run_id,
            ),
        )
        return version_id

    def _insert_weekly_version(
        self,
        connection: sqlite3.Connection,
        *,
        capture: _Capture,
        suffix: str,
        period: str = "2026-07-08",
        correction_sequence: int = 1,
        supersedes_version_id: str | None = None,
        artifact_id: str | None = None,
        snapshot_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        version_id = f"stage11-weekly-version-{suffix}"
        connection.execute(
            """
            INSERT INTO stage11_eia_weekly_observation_versions (
                version_id, canonical_series_id, provider_series_id, period,
                value_text, unit, content_sha256, available_at,
                available_precision, captured_at, captured_precision,
                correction_sequence, supersedes_version_id, capture_id,
                artifact_id, snapshot_id, run_id, source_row
            ) VALUES (?, 'macro.eia.weekly.petroleum_stock',
                      'PET.WCESTUS1.W', ?, '1', 'barrels', ?, ?, 'datetime',
                      ?, 'datetime', ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                version_id,
                period,
                self._digest(f"weekly-content:{suffix}"),
                NOW,
                NOW,
                correction_sequence,
                supersedes_version_id,
                capture.capture_id,
                artifact_id or capture.artifact_id,
                snapshot_id or capture.snapshot_id,
                run_id or capture.run_id,
            ),
        )
        return version_id

    def test_stage11_resource_is_strict_and_allocates_exact_relations(self) -> None:
        self.assertEqual(hashlib.sha256(RESOURCE.read_bytes()).hexdigest(), RESOURCE_SHA256)
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE dataset_registry(dataset_id TEXT PRIMARY KEY);
            CREATE TABLE ingestion_runs(run_id TEXT PRIMARY KEY);
            INSERT INTO dataset_registry VALUES
              ('macro.bea.nipa_history_evidence'),
              ('macro.bea.nipa_history'),
              ('macro.eia.electricity_retail_history_evidence'),
              ('macro.eia.electricity.retail_history'),
              ('macro.eia.petroleum_weekly_stock_history_evidence'),
              ('macro.eia.petroleum_weekly_stock_history');
            """
        )
        connection.executescript(RESOURCE.read_text(encoding="utf-8"))
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'stage11_%'"
            )
        }
        self.assertEqual(
            names,
            {
                "stage11_bea_nipa_captures",
                "stage11_bea_nipa_observation_versions",
                "stage11_bea_nipa_observations",
                "stage11_eia_retail_captures",
                "stage11_eia_retail_observation_versions",
                "stage11_eia_retail_observations",
                "stage11_eia_weekly_captures",
                "stage11_eia_weekly_observation_versions",
                "stage11_eia_weekly_observations",
            },
        )
        sql = RESOURCE.read_text(encoding="utf-8")
        for prohibited in ("api_key", "userid", "authorization", "http_headers", "source_url"):
            self.assertNotIn(prohibited, sql.casefold())

    def test_stage11_capture_multi_capture_uniqueness_and_immutability(self) -> None:
        connection = self._connection()
        try:
            with self.subTest(family="bea"):
                run_id = self._add_run(connection, "bea-batch")
                first = self._insert_bea_capture(
                    connection,
                    suffix="bea-first",
                    run_id=run_id,
                    table_name="T10101",
                    series_code="A191RL",
                )
                self._insert_bea_capture(
                    connection,
                    suffix="bea-second",
                    run_id=run_id,
                    table_name="T10105",
                    series_code="A191RC",
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_capture(
                        connection,
                        suffix="bea-duplicate-run-pair",
                        run_id=run_id,
                        table_name="T10101",
                        series_code="A191RL",
                    ),
                )
                replay_run = self._add_run(connection, "bea-replay")
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_capture(
                        connection,
                        suffix="bea-duplicate-response",
                        run_id=replay_run,
                        table_name="T10101",
                        series_code="A191RL",
                        response_seed="bea-first",
                    ),
                )
                next_run = self._add_run(connection, "bea-next")
                self._insert_bea_capture(
                    connection,
                    suffix="bea-next",
                    run_id=next_run,
                    table_name="T10101",
                    series_code="A191RL",
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_bea_nipa_captures SET row_count=2 WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_bea_nipa_captures WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )

            with self.subTest(family="eia-retail"):
                run_id = self._add_run(connection, "retail-batch")
                first = self._insert_retail_capture(
                    connection,
                    suffix="retail-first",
                    run_id=run_id,
                    page_offset=0,
                )
                self._insert_retail_capture(
                    connection,
                    suffix="retail-second-page",
                    run_id=run_id,
                    page_offset=5000,
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_capture(
                        connection,
                        suffix="retail-duplicate-run-page",
                        run_id=run_id,
                        page_offset=0,
                    ),
                )
                replay_run = self._add_run(connection, "retail-replay")
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_capture(
                        connection,
                        suffix="retail-duplicate-response",
                        run_id=replay_run,
                        page_offset=0,
                        response_seed="retail-first",
                    ),
                )
                next_run = self._add_run(connection, "retail-next")
                self._insert_retail_capture(
                    connection,
                    suffix="retail-next",
                    run_id=next_run,
                    page_offset=0,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_retail_captures SET row_count=2 WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_eia_retail_captures WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )

            with self.subTest(family="eia-weekly"):
                first_run = self._add_run(connection, "weekly-first")
                first = self._insert_weekly_capture(
                    connection,
                    suffix="weekly-first",
                    run_id=first_run,
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_capture(
                        connection,
                        suffix="weekly-duplicate-run",
                        run_id=first_run,
                    ),
                )
                replay_run = self._add_run(connection, "weekly-replay")
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_capture(
                        connection,
                        suffix="weekly-duplicate-response",
                        run_id=replay_run,
                        response_seed="weekly-first",
                    ),
                )
                next_run = self._add_run(connection, "weekly-next")
                self._insert_weekly_capture(
                    connection,
                    suffix="weekly-next",
                    run_id=next_run,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_weekly_captures SET row_count=2 WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_eia_weekly_captures WHERE capture_id=?",
                        (first.capture_id,),
                    ),
                )
        finally:
            connection.close()

    def test_stage11_versions_require_exact_correction_and_capture_lineage(self) -> None:
        connection = self._connection()
        try:
            with self.subTest(family="bea"):
                capture = self._insert_bea_capture(
                    connection,
                    suffix="bea-lineage",
                    run_id=self._add_run(connection, "bea-lineage"),
                    table_name="T10101",
                    series_code="A191RL",
                )
                version_one = self._insert_bea_version(
                    connection,
                    capture=capture,
                    suffix="bea-v1",
                )
                other_version = self._insert_bea_version(
                    connection,
                    capture=capture,
                    suffix="bea-other-v1",
                    period="2026Q2",
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_version(
                        connection,
                        capture=capture,
                        suffix="bea-initial-supersedes",
                        period="2026Q3",
                        supersedes_version_id=version_one,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_version(
                        connection,
                        capture=capture,
                        suffix="bea-seq2-no-prior",
                        period="2026Q4",
                        correction_sequence=2,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_version(
                        connection,
                        capture=capture,
                        suffix="bea-seq2-wrong-key",
                        correction_sequence=2,
                        supersedes_version_id=other_version,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_version(
                        connection,
                        capture=capture,
                        suffix="bea-capture-table-mismatch",
                        canonical_series_id="macro.gdp.nominal_billions",
                        table_name="T10105",
                        series_code="A191RC",
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_bea_version(
                        connection,
                        capture=capture,
                        suffix="bea-capture-artifact-mismatch",
                        artifact_id="unrelated-bea-artifact",
                    ),
                )
                version_two = self._insert_bea_version(
                    connection,
                    capture=capture,
                    suffix="bea-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_bea_nipa_observation_versions SET value_text='2' WHERE version_id=?",
                        (version_one,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_bea_nipa_observation_versions WHERE version_id=?",
                        (version_two,),
                    ),
                )

            with self.subTest(family="eia-retail"):
                capture = self._insert_retail_capture(
                    connection,
                    suffix="retail-lineage",
                    run_id=self._add_run(connection, "retail-lineage"),
                    page_offset=0,
                )
                version_one = self._insert_retail_version(
                    connection,
                    capture=capture,
                    suffix="retail-v1",
                )
                other_version = self._insert_retail_version(
                    connection,
                    capture=capture,
                    suffix="retail-other-v1",
                    period="2026-08",
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_version(
                        connection,
                        capture=capture,
                        suffix="retail-initial-supersedes",
                        period="2026-09",
                        supersedes_version_id=version_one,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_version(
                        connection,
                        capture=capture,
                        suffix="retail-seq2-no-prior",
                        period="2026-10",
                        correction_sequence=2,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_version(
                        connection,
                        capture=capture,
                        suffix="retail-seq2-wrong-key",
                        correction_sequence=2,
                        supersedes_version_id=other_version,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_retail_version(
                        connection,
                        capture=capture,
                        suffix="retail-capture-artifact-mismatch",
                        artifact_id="unrelated-retail-artifact",
                    ),
                )
                version_two = self._insert_retail_version(
                    connection,
                    capture=capture,
                    suffix="retail-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_retail_observation_versions SET value_text='2' WHERE version_id=?",
                        (version_one,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_eia_retail_observation_versions WHERE version_id=?",
                        (version_two,),
                    ),
                )

            with self.subTest(family="eia-weekly"):
                capture = self._insert_weekly_capture(
                    connection,
                    suffix="weekly-lineage",
                    run_id=self._add_run(connection, "weekly-lineage"),
                )
                version_one = self._insert_weekly_version(
                    connection,
                    capture=capture,
                    suffix="weekly-v1",
                )
                other_version = self._insert_weekly_version(
                    connection,
                    capture=capture,
                    suffix="weekly-other-v1",
                    period="2026-07-15",
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_version(
                        connection,
                        capture=capture,
                        suffix="weekly-initial-supersedes",
                        period="2026-07-22",
                        supersedes_version_id=version_one,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_version(
                        connection,
                        capture=capture,
                        suffix="weekly-seq2-no-prior",
                        period="2026-07-29",
                        correction_sequence=2,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_version(
                        connection,
                        capture=capture,
                        suffix="weekly-seq2-wrong-key",
                        correction_sequence=2,
                        supersedes_version_id=other_version,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: self._insert_weekly_version(
                        connection,
                        capture=capture,
                        suffix="weekly-capture-artifact-mismatch",
                        artifact_id="unrelated-weekly-artifact",
                    ),
                )
                version_two = self._insert_weekly_version(
                    connection,
                    capture=capture,
                    suffix="weekly-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_weekly_observation_versions SET value_text='2' WHERE version_id=?",
                        (version_one,),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "DELETE FROM stage11_eia_weekly_observation_versions WHERE version_id=?",
                        (version_two,),
                    ),
                )
        finally:
            connection.close()

    def test_stage11_current_pointers_require_latest_exact_versions(self) -> None:
        connection = self._connection()
        try:
            with self.subTest(family="bea"):
                capture = self._insert_bea_capture(
                    connection,
                    suffix="bea-current",
                    run_id=self._add_run(connection, "bea-current"),
                    table_name="T10101",
                    series_code="A191RL",
                )
                version_one = self._insert_bea_version(
                    connection, capture=capture, suffix="bea-current-v1"
                )
                version_two = self._insert_bea_version(
                    connection,
                    capture=capture,
                    suffix="bea-current-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_bea_nipa_observations VALUES (?, ?, ?)",
                        ("macro.gdp.real_qoq_saar_pct", "2026Q1", version_one),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_bea_nipa_observations VALUES (?, ?, ?)",
                        ("macro.gdp.real_qoq_saar_pct", "2026Q2", version_two),
                    ),
                )
                connection.execute(
                    "INSERT INTO stage11_bea_nipa_observations VALUES (?, ?, ?)",
                    ("macro.gdp.real_qoq_saar_pct", "2026Q1", version_two),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_bea_nipa_observations SET current_version_id=? "
                        "WHERE canonical_series_id=? AND period=?",
                        (version_one, "macro.gdp.real_qoq_saar_pct", "2026Q1"),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_bea_nipa_observations SET period='2026Q2' "
                        "WHERE canonical_series_id=? AND period=?",
                        ("macro.gdp.real_qoq_saar_pct", "2026Q1"),
                    ),
                )

            with self.subTest(family="eia-retail"):
                capture = self._insert_retail_capture(
                    connection,
                    suffix="retail-current",
                    run_id=self._add_run(connection, "retail-current"),
                    page_offset=0,
                )
                version_one = self._insert_retail_version(
                    connection, capture=capture, suffix="retail-current-v1"
                )
                version_two = self._insert_retail_version(
                    connection,
                    capture=capture,
                    suffix="retail-current-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_eia_retail_observations VALUES (?, ?, ?, ?, ?)",
                        (
                            "macro.eia.electricity.retail_sales",
                            "2026-07",
                            "US",
                            "ALL",
                            version_one,
                        ),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_eia_retail_observations VALUES (?, ?, ?, ?, ?)",
                        (
                            "macro.eia.electricity.retail_sales",
                            "2026-08",
                            "US",
                            "ALL",
                            version_two,
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO stage11_eia_retail_observations VALUES (?, ?, ?, ?, ?)",
                    (
                        "macro.eia.electricity.retail_sales",
                        "2026-07",
                        "US",
                        "ALL",
                        version_two,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_retail_observations SET current_version_id=? "
                        "WHERE canonical_series_id=? AND period=? AND state_id=? AND sector_id=?",
                        (
                            version_one,
                            "macro.eia.electricity.retail_sales",
                            "2026-07",
                            "US",
                            "ALL",
                        ),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_retail_observations SET period='2026-08' "
                        "WHERE canonical_series_id=? AND period=? AND state_id=? AND sector_id=?",
                        (
                            "macro.eia.electricity.retail_sales",
                            "2026-07",
                            "US",
                            "ALL",
                        ),
                    ),
                )

            with self.subTest(family="eia-weekly"):
                capture = self._insert_weekly_capture(
                    connection,
                    suffix="weekly-current",
                    run_id=self._add_run(connection, "weekly-current"),
                )
                version_one = self._insert_weekly_version(
                    connection, capture=capture, suffix="weekly-current-v1"
                )
                version_two = self._insert_weekly_version(
                    connection,
                    capture=capture,
                    suffix="weekly-current-v2",
                    correction_sequence=2,
                    supersedes_version_id=version_one,
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_eia_weekly_observations VALUES (?, ?, ?)",
                        (
                            "macro.eia.weekly.petroleum_stock",
                            "2026-07-08",
                            version_one,
                        ),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "INSERT INTO stage11_eia_weekly_observations VALUES (?, ?, ?)",
                        (
                            "macro.eia.weekly.petroleum_stock",
                            "2026-07-15",
                            version_two,
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO stage11_eia_weekly_observations VALUES (?, ?, ?)",
                    (
                        "macro.eia.weekly.petroleum_stock",
                        "2026-07-08",
                        version_two,
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_weekly_observations SET current_version_id=? "
                        "WHERE canonical_series_id=? AND period=?",
                        (version_one, "macro.eia.weekly.petroleum_stock", "2026-07-08"),
                    ),
                )
                self._assert_aborts(
                    connection,
                    lambda: connection.execute(
                        "UPDATE stage11_eia_weekly_observations SET period='2026-07-15' "
                        "WHERE canonical_series_id=? AND period=?",
                        ("macro.eia.weekly.petroleum_stock", "2026-07-08"),
                    ),
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
