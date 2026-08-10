"""Atomic callback-boundary tests for the shared ingestion coordinator."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    SnapshotWrite,
    WriteResult,
)
from quant_data.macro import MacroFixtureImporter
from quant_data.market import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
MARKET_DATASET = "fixture.market.daily_prices"
MARKET_OUTPUTS = (
    "fixture.market.daily_price_evidence",
    "fixture.market.instruments",
    MARKET_DATASET,
)
_TIMESTAMP = "2026-08-09T12:00:00-04:00"
_COMPLETED_TIMESTAMP = "2026-08-09T12:00:01-04:00"


class IngestionAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.store_map, self.registry)
        self.coordinator = IngestionCoordinator(self.store_map)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _semantic_identity(label: str) -> str:
        return hashlib.sha256(label.encode("utf-8")).hexdigest()

    def _execute(self, *, run_id: str, semantic_identity: str, writer) -> object:
        return self.coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=MARKET_DATASET,
            output_dataset_ids=MARKET_OUTPUTS,
            semantic_identity=semantic_identity,
            run_id=run_id,
            command="tests.ingestion_atomicity",
            scope={"case": run_id},
            started_at=_TIMESTAMP,
            completed_at=_COMPLETED_TIMESTAMP,
            fetched_count=1,
            writer=writer,
        )

    @staticmethod
    def _write_market_domain_probe(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        semantic_identity: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO market_price_ingestion_requests (
                request_id, dataset_id, provider, scope_json, scope_digest,
                semantic_identity, completeness, artifact_id, artifact_sha256,
                snapshot_id, captured_at, captured_precision, source_resource,
                row_count, normalization_version, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"callback-request-{run_id}",
                "fixture.market.daily_price_evidence",
                "atomicity-test",
                "{}",
                "a" * 64,
                semantic_identity,
                "complete",
                f"callback-artifact-{run_id}",
                "b" * 64,
                f"callback-snapshot-{run_id}",
                _TIMESTAMP,
                "datetime",
                "tests/test_ingestion_atomicity.py",
                1,
                "test-v1",
                run_id,
            ),
        )

    @staticmethod
    def _control_result(
        semantic_identity: str,
        *,
        completeness: str = "complete",
        artifact_ids: tuple[str, ...] | None = None,
        run_outcome: str = "succeeded",
    ) -> WriteResult:
        artifact_id = f"artifact-{semantic_identity[:12]}"
        snapshot_id = f"snapshot-{semantic_identity[:12]}"
        return WriteResult(
            written_count=0,
            artifacts=(
                ArtifactWrite(
                    artifact_id=artifact_id,
                    dataset_id="fixture.market.daily_price_evidence",
                    content_sha256="c" * 64,
                    media_type="text/csv",
                    byte_count=1,
                    source_reference="tests/atomicity.csv",
                    request_scope={},
                    captured_at=_TIMESTAMP,
                    captured_precision="datetime",
                    normalization_version="test-v1",
                ),
            ),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=MARKET_DATASET,
                semantic_identity=semantic_identity,
                scope={},
                completeness=completeness,
                row_count=0,
                captured_at=_TIMESTAMP,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,) if artifact_ids is None else artifact_ids,
            ),
            quality_results=(),
            run_outcome=run_outcome,
        )

    def _assert_no_successful_or_domain_state(self) -> None:
        relations = (
            "ingestion_runs",
            "ingestion_run_outputs",
            "ingestion_artifacts",
            "ingestion_snapshots",
            "ingestion_snapshot_artifacts",
            "data_quality_results",
            "market_price_ingestion_requests",
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            for relation in relations:
                self.assertEqual(
                    connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0],
                    0,
                    relation,
                )
            checkpoints = list(
                connection.execute(
                    """
                    SELECT last_successful_run_id, last_semantic_identity
                    FROM dataset_registry
                    WHERE dataset_id IN (?, ?, ?)
                    ORDER BY dataset_id
                    """,
                    MARKET_OUTPUTS,
                )
            )
        self.assertEqual(
            [tuple(row) for row in checkpoints],
            [(None, None), (None, None), (None, None)],
        )

    def _assert_one_path_free_failure(
        self,
        *,
        run_id: str,
        expected_status: str,
        expected_error_code: str,
    ) -> None:
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT failure_id, status, error_code, scope_json
                    FROM ingestion_run_failures
                    WHERE run_id=?
                    """,
                    (run_id,),
                )
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["failure_id"], f"failure:{run_id}")
        self.assertEqual(rows[0]["status"], expected_status)
        self.assertEqual(rows[0]["error_code"], expected_error_code)
        self.assertNotIn(str(self.root), rows[0]["scope_json"])

    def test_callback_transaction_escapes_rollback_and_record_one_failure(self) -> None:
        attempts = (
            ("connection-commit", lambda connection: connection.commit()),
            ("connection-rollback", lambda connection: connection.rollback()),
            ("sql-begin", lambda connection: connection.execute("BEGIN")),
            ("sql-commit", lambda connection: connection.execute("COMMIT")),
            ("sql-end", lambda connection: connection.execute("END")),
            ("sql-rollback", lambda connection: connection.execute("ROLLBACK")),
            (
                "sql-savepoint",
                lambda connection: connection.execute("SAVEPOINT callback_escape"),
            ),
            (
                "sql-release",
                lambda connection: connection.execute("RELEASE callback_escape"),
            ),
            (
                "script-transaction",
                lambda connection: connection.executescript("BEGIN; COMMIT;"),
            ),
            (
                "script-savepoint",
                lambda connection: connection.executescript(
                    "SAVEPOINT callback_escape; RELEASE callback_escape;"
                ),
            ),
        )
        first_run_id = ""
        first_semantic_identity = ""
        for label, attempt in attempts:
            run_id = f"atomicity-{label}"
            semantic_identity = self._semantic_identity(run_id)
            if not first_run_id:
                first_run_id = run_id
                first_semantic_identity = semantic_identity

            def writer(
                connection: sqlite3.Connection,
                active_run_id: str,
                *,
                _attempt=attempt,
                _semantic_identity=semantic_identity,
            ) -> WriteResult:
                self._write_market_domain_probe(
                    connection,
                    run_id=active_run_id,
                    semantic_identity=_semantic_identity,
                )
                _attempt(connection)
                raise AssertionError("callback transaction escape was unexpectedly allowed")

            with self.subTest(label=label):
                with self.assertRaises(sqlite3.DatabaseError):
                    self._execute(
                        run_id=run_id,
                        semantic_identity=semantic_identity,
                        writer=writer,
                    )
                self._assert_no_successful_or_domain_state()
                self._assert_one_path_free_failure(
                    run_id=run_id,
                    expected_status="failed",
                    expected_error_code="storage_error",
                )

        connection = sqlite3.connect(self.store_map.market)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE ingestion_run_failures SET error_code='tampered' WHERE run_id=?",
                    (first_run_id,),
                )
            connection.rollback()
        finally:
            connection.close()

        with self.assertRaises(ConflictError):
            self._execute(
                run_id=first_run_id,
                semantic_identity=first_semantic_identity,
                writer=lambda connection, active_run_id: self._control_result(
                    first_semantic_identity
                ),
            )
        self._assert_one_path_free_failure(
            run_id=first_run_id,
            expected_status="failed",
            expected_error_code="storage_error",
        )

    def test_partial_run_or_artifactless_snapshot_cannot_be_published(self) -> None:
        partial_run_id = "atomicity-partial-run"
        partial_identity = self._semantic_identity(partial_run_id)
        with self.assertRaises(ValidationError):
            self._execute(
                run_id=partial_run_id,
                semantic_identity=partial_identity,
                writer=lambda connection, active_run_id: self._control_result(
                    partial_identity,
                    completeness="partial",
                    run_outcome="partial",
                ),
            )
        self._assert_no_successful_or_domain_state()
        self._assert_one_path_free_failure(
            run_id=partial_run_id,
            expected_status="partial",
            expected_error_code="partial_publication",
        )

        artifactless_run_id = "atomicity-artifactless-snapshot"
        artifactless_identity = self._semantic_identity(artifactless_run_id)
        with self.assertRaises(ValidationError):
            self._execute(
                run_id=artifactless_run_id,
                semantic_identity=artifactless_identity,
                writer=lambda connection, active_run_id: self._control_result(
                    artifactless_identity,
                    artifact_ids=(),
                ),
            )
        self._assert_no_successful_or_domain_state()
        self._assert_one_path_free_failure(
            run_id=artifactless_run_id,
            expected_status="rejected",
            expected_error_code="validation_rejected",
        )

    def test_current_fixture_writers_and_exact_replay_still_succeed(self) -> None:
        manifest = FixtureManifest.load(FIXTURE_MANIFEST, project_root=PROJECT_ROOT)
        market = DailyPriceImporter(self.store_map, manifest)
        macro = MacroFixtureImporter(self.store_map, manifest)

        self.assertEqual(market.import_fixture("market.base").outcome, "succeeded")
        before_market_replay = mutation_fingerprint(self.store_map)
        market_replay = market.import_fixture("market.base")
        self.assertEqual(market_replay.outcome, "unchanged")
        self.assertEqual(market_replay.written_count, 0)
        self.assertEqual(before_market_replay["sha256"], mutation_fingerprint(self.store_map)["sha256"])

        self.assertEqual(market.import_fixture("market.correction").outcome, "succeeded")
        before_correction_replay = mutation_fingerprint(self.store_map)
        correction_replay = market.import_fixture("market.correction")
        self.assertEqual(correction_replay.outcome, "unchanged")
        self.assertEqual(correction_replay.written_count, 0)
        self.assertEqual(
            before_correction_replay["sha256"],
            mutation_fingerprint(self.store_map)["sha256"],
        )

        self.assertEqual(macro.import_fixture("macro.first_vintage").outcome, "succeeded")
        before_macro_replay = mutation_fingerprint(self.store_map)
        macro_replay = macro.import_fixture("macro.first_vintage")
        self.assertEqual(macro_replay.outcome, "unchanged")
        self.assertEqual(macro_replay.written_count, 0)
        self.assertEqual(before_macro_replay["sha256"], mutation_fingerprint(self.store_map)["sha256"])


if __name__ == "__main__":
    unittest.main()
