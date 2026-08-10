from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
DATASET_ID = "fixture.market.daily_prices"


class Stage2PhysicalLineageInvariantTests(unittest.TestCase):
    def _initialized_store(self, directory: str):
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        store_map = explicit_store_map(Path(directory) / "stores")
        initialize_all(store_map, registry)
        return store_map

    @staticmethod
    def _insert_candidate(
        connection: sqlite3.Connection,
        *,
        suffix: str,
        completeness: str,
        include_membership: bool,
    ) -> tuple[str, str, str, str]:
        run_id = f"lineage-run-{suffix}"
        artifact_id = f"lineage-artifact-{suffix}"
        snapshot_id = f"lineage-snapshot-{suffix}"
        semantic_identity = suffix * 64
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, fetched_count, written_count,
                warnings_json, code_version
            ) VALUES (?, ?, ?, 'test.stage2_lineage', '{}', 'running',
                      '2026-08-09T12:00:00-04:00', 1, 0, '[]', '0.2.0')
            """,
            (run_id, DATASET_ID, semantic_identity),
        )
        connection.execute(
            """
            INSERT INTO ingestion_run_outputs (run_id, dataset_id, semantic_identity)
            VALUES (?, ?, ?)
            """,
            (run_id, DATASET_ID, semantic_identity),
        )
        connection.execute(
            """
            INSERT INTO ingestion_artifacts (
                artifact_id, run_id, dataset_id, content_sha256, media_type,
                byte_count, source_reference, request_scope_json, captured_at,
                captured_precision, normalization_version
            ) VALUES (?, ?, ?, ?, 'text/csv', 1, 'fixtures/lineage.csv', '{}',
                      '2026-08-09T12:00:00-04:00', 'datetime', '1.0.0')
            """,
            (artifact_id, run_id, DATASET_ID, semantic_identity),
        )
        connection.execute(
            """
            INSERT INTO ingestion_snapshots (
                snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
                completeness, row_count, captured_at, captured_precision,
                validation_state, warnings_json
            ) VALUES (?, ?, ?, ?, '{}', ?, 1,
                      '2026-08-09T12:00:00-04:00', 'datetime', 'validated', '[]')
            """,
            (snapshot_id, run_id, DATASET_ID, semantic_identity, completeness),
        )
        if include_membership:
            connection.execute(
                """
                INSERT INTO ingestion_snapshot_artifacts (
                    snapshot_id, artifact_id, artifact_ordinal
                ) VALUES (?, ?, 1)
                """,
                (snapshot_id, artifact_id),
            )
        return run_id, artifact_id, snapshot_id, semantic_identity

    @staticmethod
    def _mark_success(
        connection: sqlite3.Connection,
        candidate: tuple[str, str, str, str],
    ) -> None:
        run_id, artifact_id, snapshot_id, _ = candidate
        connection.execute(
            """
            UPDATE ingestion_runs
            SET status='succeeded', completed_at='2026-08-09T12:00:01-04:00',
                artifact_id=?, snapshot_id=?
            WHERE run_id=?
            """,
            (artifact_id, snapshot_id, run_id),
        )

    def test_success_requires_snapshot_artifact_membership(self) -> None:
        rejected_shapes = (
            ("a", "complete", False),
            ("b", "partial", False),
        )
        for suffix, completeness, include_membership in rejected_shapes:
            with self.subTest(
                completeness=completeness,
                include_membership=include_membership,
            ), tempfile.TemporaryDirectory() as directory:
                store_map = self._initialized_store(directory)
                with writer_connection(store_map, StoreRole.MARKET) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    candidate = self._insert_candidate(
                        connection,
                        suffix=suffix,
                        completeness=completeness,
                        include_membership=include_membership,
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        self._mark_success(connection, candidate)
                    self.assertEqual(
                        connection.execute(
                            "SELECT status FROM ingestion_runs WHERE run_id=?",
                            (candidate[0],),
                        ).fetchone()[0],
                        "running",
                    )
                    connection.rollback()

    def test_valid_complete_or_bounded_partial_membership_can_checkpoint(self) -> None:
        for suffix, completeness in (("d", "complete"), ("e", "partial")):
            with self.subTest(completeness=completeness), tempfile.TemporaryDirectory() as directory:
                store_map = self._initialized_store(directory)
                with writer_connection(store_map, StoreRole.MARKET) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    candidate = self._insert_candidate(
                        connection,
                        suffix=suffix,
                        completeness=completeness,
                        include_membership=True,
                    )
                    self._mark_success(connection, candidate)
                    connection.execute(
                        """
                        UPDATE dataset_registry
                        SET last_successful_run_id=?, last_semantic_identity=?
                        WHERE dataset_id=?
                        """,
                        (candidate[0], candidate[3], DATASET_ID),
                    )
                    connection.commit()
                    self.assertEqual(
                        tuple(connection.execute(
                            """
                            SELECT run.status, dataset.last_successful_run_id
                            FROM ingestion_runs AS run
                            JOIN dataset_registry AS dataset
                              ON dataset.dataset_id=run.dataset_id
                            WHERE run.run_id=?
                            """,
                            (candidate[0],),
                        ).fetchone()),
                        ("succeeded", candidate[0]),
                    )
                    self.assertEqual(
                        list(connection.execute("PRAGMA foreign_key_check")), []
                    )


if __name__ == "__main__":
    unittest.main()
