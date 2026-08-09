"""Shared semantic no-write and short-transaction coordinator."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable

from .contracts import IngestionReceipt
from .errors import ConflictError, ValidationError
from .json_codec import dumps_strict
from .stores import StoreMap, StoreRole, StoreWriteLock, read_connection, writer_connection


@dataclass(frozen=True, slots=True)
class WriteResult:
    written_count: int
    artifact_id: str
    snapshot_id: str
    warnings: tuple[str, ...] = ()


WriteCallback = Callable[[sqlite3.Connection, str], WriteResult]


class IngestionCoordinator:
    """Own the total no-write replay gate and atomic successful receipt.

    Candidate bytes must already be verified, normalized, and batch-validated
    before this coordinator is called.  Callers must perform no network work in
    the callback because it runs under the physical store lock and transaction.
    """

    def __init__(self, store_map: StoreMap, *, code_version: str = "stage1.0.1") -> None:
        self.store_map = store_map
        self.code_version = code_version

    def _existing_run(
        self,
        role: StoreRole,
        dataset_id: str,
        semantic_identity: str,
    ) -> sqlite3.Row | None:
        with read_connection(self.store_map, role) as connection:
            return connection.execute(
                """
                SELECT run_id, artifact_id, snapshot_id, written_count, warnings_json
                FROM ingestion_runs
                WHERE dataset_id=? AND semantic_identity=? AND status='succeeded'
                """,
                (dataset_id, semantic_identity),
            ).fetchone()

    def execute(
        self,
        *,
        role: StoreRole | str,
        dataset_id: str,
        output_dataset_ids: Iterable[str],
        semantic_identity: str,
        run_id: str,
        command: str,
        scope: dict[str, object],
        started_at: str,
        completed_at: str,
        fetched_count: int,
        writer: WriteCallback,
    ) -> IngestionReceipt:
        normalized = StoreRole(role)
        if len(semantic_identity) != 64:
            raise ValidationError("Semantic identity must be a lowercase SHA-256 digest")
        outputs = tuple(dict.fromkeys(output_dataset_ids))
        if not outputs or dataset_id not in outputs:
            raise ValidationError("Canonical dataset must be one of the declared outputs")
        existing = self._existing_run(normalized, dataset_id, semantic_identity)
        if existing is not None:
            return IngestionReceipt(
                outcome="unchanged",
                store=normalized.value,
                dataset_id=dataset_id,
                semantic_identity=semantic_identity,
                run_id=None,
                artifact_id=None,
                snapshot_id=None,
                written_count=0,
            )

        path = self.store_map.path(normalized)
        with StoreWriteLock(path), writer_connection(
            self.store_map, normalized
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = connection.execute(
                    """
                    SELECT run_id FROM ingestion_runs
                    WHERE dataset_id=? AND semantic_identity=? AND status='succeeded'
                    """,
                    (dataset_id, semantic_identity),
                ).fetchone()
                if duplicate is not None:
                    connection.rollback()
                    return IngestionReceipt(
                        outcome="unchanged",
                        store=normalized.value,
                        dataset_id=dataset_id,
                        semantic_identity=semantic_identity,
                        run_id=None,
                        artifact_id=None,
                        snapshot_id=None,
                        written_count=0,
                    )
                known_outputs = {
                    row["dataset_id"]
                    for row in connection.execute(
                        "SELECT dataset_id FROM dataset_registry WHERE active=1"
                    )
                }
                if not set(outputs).issubset(known_outputs):
                    raise ConflictError("Ingestion output is not registered in the target store")
                connection.execute(
                    """
                    INSERT INTO ingestion_runs (
                        run_id, dataset_id, semantic_identity, command, scope_json,
                        status, started_at, fetched_count, code_version
                    ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                    """,
                    (
                        run_id,
                        dataset_id,
                        semantic_identity,
                        command,
                        dumps_strict(scope),
                        started_at,
                        fetched_count,
                        self.code_version,
                    ),
                )
                result = writer(connection, run_id)
                if result.written_count < 0:
                    raise ValidationError("Written count cannot be negative")
                warnings_json = dumps_strict(list(result.warnings))
                connection.execute(
                    """
                    UPDATE ingestion_runs
                    SET status='succeeded', completed_at=?, artifact_id=?,
                        snapshot_id=?, written_count=?, warnings_json=?
                    WHERE run_id=? AND status='running'
                    """,
                    (
                        completed_at,
                        result.artifact_id,
                        result.snapshot_id,
                        result.written_count,
                        warnings_json,
                        run_id,
                    ),
                )
                for output_dataset_id in outputs:
                    connection.execute(
                        """
                        UPDATE dataset_registry
                        SET last_successful_run_id=?, last_semantic_identity=?
                        WHERE dataset_id=?
                        """,
                        (run_id, semantic_identity, output_dataset_id),
                    )
                connection.commit()
                return IngestionReceipt(
                    outcome="succeeded",
                    store=normalized.value,
                    dataset_id=dataset_id,
                    semantic_identity=semantic_identity,
                    run_id=run_id,
                    artifact_id=result.artifact_id,
                    snapshot_id=result.snapshot_id,
                    written_count=result.written_count,
                    warnings=result.warnings,
                )
            except Exception:
                connection.rollback()
                raise
