"""Shared semantic no-write and short-transaction coordinator."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .contracts import IngestionReceipt
from .errors import ConflictError, ValidationError
from .json_codec import dumps_strict
from .stores import StoreMap, StoreRole, StoreWriteLock, read_connection, writer_connection
from .temporal import TemporalPrecision, TemporalValue


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _RejectedPublication(ValidationError):
    """A write candidate failed validation and cannot be published."""


class _PartialPublication(ValidationError):
    """A partial candidate cannot be represented as a successful run."""


def _writer_transaction_authorizer(
    action: int,
    first: str | None,
    second: str | None,
    database: str | None,
    trigger: str | None,
) -> int:
    """Keep a callback inside the coordinator-owned transaction boundary."""

    del first, second, database, trigger
    if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


@dataclass(frozen=True, slots=True)
class ArtifactWrite:
    artifact_id: str
    dataset_id: str
    content_sha256: str
    media_type: str
    byte_count: int
    source_reference: str
    request_scope: dict[str, object]
    captured_at: str
    captured_precision: str
    normalization_version: str


@dataclass(frozen=True, slots=True)
class SnapshotWrite:
    snapshot_id: str
    dataset_id: str
    semantic_identity: str
    scope: dict[str, object]
    completeness: str
    row_count: int
    captured_at: str
    captured_precision: str
    validation_state: str
    artifact_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QualityWrite:
    quality_result_id: str
    dataset_id: str
    rule_id: str
    rule_version: str
    severity: str
    outcome: str
    subject_kind: str
    subject_id: str
    observed: dict[str, object]
    artifact_id: str | None = None
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class WriteResult:
    written_count: int
    artifacts: tuple[ArtifactWrite, ...]
    snapshot: SnapshotWrite
    quality_results: tuple[QualityWrite, ...]
    run_outcome: str = "succeeded"
    warnings: tuple[str, ...] = ()

    @property
    def artifact_id(self) -> str:
        return self.artifacts[0].artifact_id

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id


WriteCallback = Callable[[sqlite3.Connection, str], WriteResult]


def _run_writer_with_transaction_guard(
    connection: sqlite3.Connection,
    writer: WriteCallback,
    run_id: str,
) -> WriteResult:
    """Run trusted callback SQL without allowing transaction-boundary escape."""

    connection.set_authorizer(_writer_transaction_authorizer)
    try:
        return writer(connection, run_id)
    finally:
        # The coordinator owns rollback, failure recovery, and the final commit.
        connection.set_authorizer(None)


def _validate_control_write(
    result: WriteResult,
    *,
    semantic_identity: str,
    outputs: tuple[str, ...],
) -> None:
    if not result.artifacts:
        raise ValidationError("A successful ingestion requires immutable artifact evidence")
    artifact_ids = tuple(artifact.artifact_id for artifact in result.artifacts)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValidationError("Artifact identities must be unique within one run")
    for artifact in result.artifacts:
        source = PurePosixPath(artifact.source_reference)
        captured = TemporalValue.parse(
            artifact.captured_at,
            pointer="/artifacts/captured_at",
        )
        if (
            not isinstance(artifact.artifact_id, str)
            or not artifact.artifact_id
            or artifact.dataset_id not in outputs
            or not isinstance(artifact.content_sha256, str)
            or _SHA256.fullmatch(artifact.content_sha256) is None
            or isinstance(artifact.byte_count, bool)
            or not isinstance(artifact.byte_count, int)
            or artifact.byte_count < 1
            or not isinstance(artifact.media_type, str)
            or not artifact.media_type
            or not isinstance(artifact.source_reference, str)
            or not artifact.source_reference
            or artifact.captured_precision not in {"date", "datetime"}
            or captured.precision.value != artifact.captured_precision
            or not isinstance(artifact.normalization_version, str)
            or not artifact.normalization_version
            or source.is_absolute()
            or ".." in source.parts
            or "\\" in artifact.source_reference
        ):
            raise ValidationError("Artifact control-plane metadata is invalid")
    if result.run_outcome == "partial":
        raise _PartialPublication("Partial runs cannot be published as succeeded")
    if result.run_outcome == "rejected":
        raise _RejectedPublication("Rejected runs cannot be published as succeeded")
    if result.run_outcome != "succeeded":
        raise ValidationError("Unsupported ingestion run outcome")
    snapshot = result.snapshot
    if snapshot.validation_state == "rejected":
        raise _RejectedPublication("Rejected snapshots cannot be published as succeeded")
    captured = TemporalValue.parse(
        snapshot.captured_at,
        pointer="/snapshot/captured_at",
    )
    if (
        not isinstance(snapshot.snapshot_id, str)
        or not snapshot.snapshot_id
        or snapshot.dataset_id not in outputs
        or snapshot.semantic_identity != semantic_identity
        or _SHA256.fullmatch(snapshot.semantic_identity) is None
        or snapshot.completeness not in {"complete", "partial"}
        or isinstance(snapshot.row_count, bool)
        or not isinstance(snapshot.row_count, int)
        or snapshot.row_count < 0
        or snapshot.captured_precision not in {"date", "datetime"}
        or captured.precision.value != snapshot.captured_precision
        or snapshot.validation_state != "validated"
        or not snapshot.artifact_ids
        or len(snapshot.artifact_ids) != len(set(snapshot.artifact_ids))
        or not set(snapshot.artifact_ids).issubset(artifact_ids)
    ):
        raise ValidationError("Snapshot control-plane metadata is invalid")
    quality_ids: set[str] = set()
    for quality in result.quality_results:
        if (
            not quality.quality_result_id
            or quality.quality_result_id in quality_ids
            or quality.dataset_id not in outputs
            or not quality.rule_id
            or not quality.rule_version
            or quality.severity not in {"informational", "warning", "critical"}
            or quality.outcome not in {"passed", "failed", "skipped"}
            or quality.subject_kind not in {"run", "artifact", "snapshot", "version"}
            or not quality.subject_id
            or (quality.artifact_id is not None and quality.artifact_id not in artifact_ids)
            or (
                quality.snapshot_id is not None
                and quality.snapshot_id != snapshot.snapshot_id
            )
            or (
                quality.subject_kind == "artifact"
                and (
                    quality.artifact_id is None
                    or quality.subject_id != quality.artifact_id
                )
            )
            or (
                quality.subject_kind == "snapshot"
                and (
                    quality.snapshot_id is None
                    or quality.subject_id != quality.snapshot_id
                )
            )
        ):
            raise ValidationError("Quality-result control-plane metadata is invalid")
        quality_ids.add(quality.quality_result_id)


def _insert_control_write(
    connection: sqlite3.Connection,
    run_id: str,
    result: WriteResult,
    *,
    completed_at: str,
) -> None:
    for artifact in result.artifacts:
        connection.execute(
            """
            INSERT INTO ingestion_artifacts (
                artifact_id, run_id, dataset_id, content_sha256, media_type,
                byte_count, source_reference, request_scope_json, captured_at,
                captured_precision, normalization_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                run_id,
                artifact.dataset_id,
                artifact.content_sha256,
                artifact.media_type,
                artifact.byte_count,
                artifact.source_reference,
                dumps_strict(artifact.request_scope),
                artifact.captured_at,
                artifact.captured_precision,
                artifact.normalization_version,
            ),
        )
    snapshot = result.snapshot
    connection.execute(
        """
        INSERT INTO ingestion_snapshots (
            snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
            completeness, row_count, captured_at, captured_precision,
            validation_state, warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.snapshot_id,
            run_id,
            snapshot.dataset_id,
            snapshot.semantic_identity,
            dumps_strict(snapshot.scope),
            snapshot.completeness,
            snapshot.row_count,
            snapshot.captured_at,
            snapshot.captured_precision,
            snapshot.validation_state,
            dumps_strict(list(snapshot.warnings)),
        ),
    )
    for ordinal, artifact_id in enumerate(snapshot.artifact_ids, start=1):
        connection.execute(
            """
            INSERT INTO ingestion_snapshot_artifacts (
                snapshot_id, artifact_id, artifact_ordinal
            ) VALUES (?, ?, ?)
            """,
            (snapshot.snapshot_id, artifact_id, ordinal),
        )
    for quality in result.quality_results:
        connection.execute(
            """
            INSERT INTO data_quality_results (
                quality_result_id, run_id, dataset_id, rule_id, rule_version,
                severity, outcome, subject_kind, subject_id, artifact_id,
                snapshot_id, observed_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quality.quality_result_id,
                run_id,
                quality.dataset_id,
                quality.rule_id,
                quality.rule_version,
                quality.severity,
                quality.outcome,
                quality.subject_kind,
                quality.subject_id,
                quality.artifact_id,
                quality.snapshot_id,
                dumps_strict(quality.observed),
                completed_at,
            ),
        )


def _failure_classification(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, _PartialPublication):
        return "partial", "partial_publication"
    if isinstance(exc, ValidationError):
        return "rejected", "validation_rejected"
    if isinstance(exc, ConflictError):
        return "failed", "conflict"
    if isinstance(exc, sqlite3.Error):
        return "failed", "storage_error"
    return "failed", "writer_error"


def _record_failed_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    dataset_id: str,
    semantic_identity: str,
    command: str,
    scope: dict[str, object],
    started_at: str,
    completed_at: str,
    fetched_count: int,
    code_version: str,
    exc: Exception,
) -> None:
    status, error_code = _failure_classification(exc)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO ingestion_run_failures (
                failure_id, run_id, dataset_id, semantic_identity, command,
                scope_json, status, started_at, completed_at, fetched_count,
                error_code, code_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"failure:{run_id}",
                run_id,
                dataset_id,
                semantic_identity,
                command,
                dumps_strict(scope),
                status,
                started_at,
                completed_at,
                fetched_count,
                error_code,
                code_version,
            ),
        )
        connection.commit()
    except sqlite3.Error as audit_error:
        if connection.in_transaction:
            connection.rollback()
        raise ConflictError("Failed ingestion could not be durably recorded") from audit_error


class IngestionCoordinator:
    """Own the total no-write replay gate and atomic successful receipt.

    Candidate bytes must already be verified, normalized, and batch-validated
    before this coordinator is called.  Callers must perform no network work in
    the callback because it runs under the physical store lock and transaction.
    """

    def __init__(self, store_map: StoreMap, *, code_version: str = "stage2.0.0") -> None:
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
        if (
            not isinstance(semantic_identity, str)
            or _SHA256.fullmatch(semantic_identity) is None
        ):
            raise ValidationError("Semantic identity must be a lowercase SHA-256 digest")
        outputs = tuple(dict.fromkeys(output_dataset_ids))
        started = TemporalValue.parse(started_at, pointer="/started_at")
        completed = TemporalValue.parse(completed_at, pointer="/completed_at")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(dataset_id, str)
            or not dataset_id
            or not isinstance(command, str)
            or not command
            or not isinstance(scope, dict)
            or isinstance(fetched_count, bool)
            or not isinstance(fetched_count, int)
            or fetched_count < 0
            or started.precision is not TemporalPrecision.DATETIME
            or completed.precision is not TemporalPrecision.DATETIME
            or not isinstance(started.value, datetime)
            or not isinstance(completed.value, datetime)
            or completed.value < started.value
        ):
            raise ValidationError("Ingestion run metadata is invalid")
        if (
            not outputs
            or any(not isinstance(item, str) or not item for item in outputs)
            or dataset_id not in outputs
        ):
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
            run_started = False
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
                failed_identity = connection.execute(
                    "SELECT 1 FROM ingestion_run_failures WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if failed_identity is not None:
                    raise ConflictError("Ingestion run identity was already used")
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
                run_started = True
                for output_dataset_id in outputs:
                    connection.execute(
                        """
                        INSERT INTO ingestion_run_outputs (
                            run_id, dataset_id, semantic_identity
                        ) VALUES (?, ?, ?)
                        """,
                        (run_id, output_dataset_id, semantic_identity),
                    )
                result = _run_writer_with_transaction_guard(connection, writer, run_id)
                if result.written_count < 0:
                    raise ValidationError("Written count cannot be negative")
                _validate_control_write(
                    result,
                    semantic_identity=semantic_identity,
                    outputs=outputs,
                )
                _insert_control_write(
                    connection,
                    run_id,
                    result,
                    completed_at=completed_at,
                )
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
            except Exception as exc:
                connection.rollback()
                if run_started:
                    _record_failed_run(
                        connection,
                        run_id=run_id,
                        dataset_id=dataset_id,
                        semantic_identity=semantic_identity,
                        command=command,
                        scope=scope,
                        started_at=started_at,
                        completed_at=completed_at,
                        fetched_count=fetched_count,
                        code_version=self.code_version,
                        exc=exc,
                    )
                raise
