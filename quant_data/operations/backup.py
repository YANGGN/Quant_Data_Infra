"""Offline, SQLite-online-backup cohorts for the four operational stores."""

from __future__ import annotations

from datetime import datetime
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TypeAlias

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..registry import Registry
from ..temporal import TemporalPrecision, TemporalValue
from ..stores import (
    STORE_ROLES,
    StoreMap,
    StoreRole,
    acquire_write_locks,
    read_connection,
)
from .health import HealthReport, StoreInspection, inspect_all_stores


DEFAULT_TIMEOUT_SECONDS = 5.0
_STORE_FILENAMES = {
    StoreRole.MARKET: "market.sqlite",
    StoreRole.MACRO: "macro.sqlite",
    StoreRole.COMPANY: "company.sqlite",
    StoreRole.NEWS: "news.sqlite",
}
TargetRoot: TypeAlias = str | Path


class _BackupProgressDeadlineExpired(Exception):
    """Internal marker used to sanitize a Stage 8 backup deadline expiry."""


@dataclass(frozen=True, slots=True)
class StoreCopyReceipt:
    """Path-free evidence for one source-to-copy SQLite online backup."""

    role: str
    source_logical_sha256: str
    target_logical_sha256: str
    source_inspection: StoreInspection
    target_inspection: StoreInspection

    def to_primitive(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_logical_sha256": self.source_logical_sha256,
            "target_logical_sha256": self.target_logical_sha256,
            "source_inspection": self.source_inspection.to_primitive(),
            "target_inspection": self.target_inspection.to_primitive(),
        }


@dataclass(frozen=True, slots=True)
class BackupCohort:
    """A validated four-store backup and its path-free verification receipts."""

    store_map: StoreMap
    registry_revision: str
    source_health: HealthReport
    backup_health: HealthReport
    receipts: tuple[StoreCopyReceipt, ...]
    source_logical_manifest_sha256: str
    backup_logical_manifest_sha256: str
    source_mutation_before_sha256: str
    source_mutation_after_sha256: str

    @property
    def backup_store_map(self) -> StoreMap:
        """An explicit name for callers that retain source and backup maps."""

        return self.store_map

    def to_primitive(self) -> dict[str, object]:
        """Return cohort evidence without physical source or target paths."""

        return {
            "registry_revision": self.registry_revision,
            "source_health": self.source_health.to_primitive(),
            "backup_health": self.backup_health.to_primitive(),
            "receipts": [receipt.to_primitive() for receipt in self.receipts],
            "source_logical_manifest_sha256": self.source_logical_manifest_sha256,
            "backup_logical_manifest_sha256": self.backup_logical_manifest_sha256,
            "source_mutation_before_sha256": self.source_mutation_before_sha256,
            "source_mutation_after_sha256": self.source_mutation_after_sha256,
        }


@dataclass(frozen=True, slots=True)
class RestoreCohort:
    """A validated restore of an immutable backup cohort into a new root."""

    store_map: StoreMap
    registry_revision: str
    backup_health: HealthReport
    restored_health: HealthReport
    receipts: tuple[StoreCopyReceipt, ...]
    backup_logical_manifest_sha256: str
    restored_logical_manifest_sha256: str
    backup_mutation_before_sha256: str
    backup_mutation_after_sha256: str

    @property
    def restored_store_map(self) -> StoreMap:
        return self.store_map

    def to_primitive(self) -> dict[str, object]:
        return {
            "registry_revision": self.registry_revision,
            "backup_health": self.backup_health.to_primitive(),
            "restored_health": self.restored_health.to_primitive(),
            "receipts": [receipt.to_primitive() for receipt in self.receipts],
            "backup_logical_manifest_sha256": self.backup_logical_manifest_sha256,
            "restored_logical_manifest_sha256": self.restored_logical_manifest_sha256,
            "backup_mutation_before_sha256": self.backup_mutation_before_sha256,
            "backup_mutation_after_sha256": self.backup_mutation_after_sha256,
        }


def _validated_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds < 0
    ):
        raise ValidationError("Operation timeout must be a finite nonnegative number")
    return float(timeout_seconds)


def _prepare_empty_target_root(target_root: TargetRoot) -> Path:
    if target_root is None or (isinstance(target_root, str) and not target_root.strip()):
        raise ValidationError("An explicit target root is required")
    try:
        root = Path(target_root).expanduser().resolve(strict=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("An explicit target root is required") from exc
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ValidationError("Target root is too broad")
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Target root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Backup and restore targets must be empty")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _store_map_for_root(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / _STORE_FILENAMES[StoreRole.MARKET],
        macro=root / _STORE_FILENAMES[StoreRole.MACRO],
        company=root / _STORE_FILENAMES[StoreRole.COMPANY],
        news=root / _STORE_FILENAMES[StoreRole.NEWS],
    )


def _assert_empty_targets(store_map: StoreMap) -> None:
    if any(path.exists() for _, path in store_map.items()):
        raise ConflictError("Backup and restore targets must not overwrite a store")


def _online_copy(
    source_map: StoreMap,
    target_map: StoreMap,
    registry: Registry,
    role: StoreRole,
    *,
    timeout_seconds: float,
    progress_check: Callable[[str], None] | None = None,
) -> None:
    """Copy one committed SQLite snapshot without copying WAL sidecar files."""

    target_path = target_map.path(role)
    if target_path.exists():
        raise ConflictError("Backup and restore targets must not overwrite a store")
    declaration = registry.store(role.value)
    destination: sqlite3.Connection | None = None
    deadline_expired = False

    def backup_progress(_status: int, _remaining: int, _total: int) -> None:
        nonlocal deadline_expired
        try:
            _copy_progress(
                progress_check,
                "copy_" + role.value + "_backup_progress",
            )
        except ResourceLimitError:
            deadline_expired = True
            raise _BackupProgressDeadlineExpired

    try:
        with read_connection(
            source_map,
            role,
            expected_anchor=declaration.anchor_relation,
        ) as source:
            destination = sqlite3.connect(
                target_path,
                timeout=timeout_seconds,
                isolation_level=None,
            )
            destination.execute("PRAGMA foreign_keys=ON")
            if progress_check is None:
                # Preserve the established backup_all/restore_all call exactly.
                source.backup(destination)
            else:
                source.backup(
                    destination,
                    pages=1,
                    progress=backup_progress,
                )
    except _BackupProgressDeadlineExpired:
        raise ValidationError(
            "Atlas source copy deadline exceeded during SQLite online backup"
        ) from None
    except sqlite3.Error as exc:
        if deadline_expired:
            raise ValidationError(
                "Atlas source copy deadline exceeded during SQLite online backup"
            ) from None
        raise ValidationError("SQLite online backup failed") from exc
    finally:
        if destination is not None:
            destination.close()


def _store_logical_sha256(manifest: dict[str, object], role: StoreRole) -> str:
    stores = manifest.get("stores")
    if not isinstance(stores, dict):
        raise ValidationError("Logical manifest is malformed")
    store = stores.get(role.value)
    if not isinstance(store, dict) or not isinstance(store.get("sha256"), str):
        raise ValidationError("Logical manifest is malformed")
    return store["sha256"]


def _receipts(
    source_health: HealthReport,
    target_health: HealthReport,
    source_manifest: dict[str, object],
    target_manifest: dict[str, object],
) -> tuple[StoreCopyReceipt, ...]:
    return tuple(
        StoreCopyReceipt(
            role=role.value,
            source_logical_sha256=_store_logical_sha256(source_manifest, role),
            target_logical_sha256=_store_logical_sha256(target_manifest, role),
            source_inspection=source_health.inspection_for(role),
            target_inspection=target_health.inspection_for(role),
        )
        for role in STORE_ROLES
    )


def _require_equal_copy_evidence(
    source_health: HealthReport,
    target_health: HealthReport,
    source_manifest: dict[str, object],
    target_manifest: dict[str, object],
) -> None:
    if source_health != target_health:
        raise ValidationError("Store health changed during SQLite copy")
    source_sha256 = source_manifest.get("sha256")
    target_sha256 = target_manifest.get("sha256")
    if not isinstance(source_sha256, str) or source_sha256 != target_sha256:
        raise ValidationError("SQLite copy logical manifest does not match source")


def backup_all(
    store_map: StoreMap,
    registry: Registry,
    *,
    target_root: TargetRoot,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> BackupCohort:
    """Create and verify a four-store online SQLite backup cohort.

    The caller must choose a new or empty root.  The only source coordination
    is one deterministic acquisition of all four physical-store locks.
    """

    timeout = _validated_timeout(timeout_seconds)
    store_map.validate_distinct()
    # Fail closed before creating an output directory when the source is not a
    # healthy four-store cohort.
    inspect_all_stores(store_map, registry)
    target_map = _store_map_for_root(_prepare_empty_target_root(target_root))
    _assert_empty_targets(target_map)

    with acquire_write_locks(store_map, STORE_ROLES, timeout_seconds=timeout):
        source_health = inspect_all_stores(store_map, registry)
        source_mutation_before = mutation_fingerprint(store_map)
        source_manifest = logical_manifest(store_map, registry)
        for role in STORE_ROLES:
            _online_copy(
                store_map,
                target_map,
                registry,
                role,
                timeout_seconds=timeout,
            )
        backup_health = inspect_all_stores(target_map, registry)
        backup_manifest = logical_manifest(target_map, registry)
        source_mutation_after = mutation_fingerprint(store_map)

    if source_mutation_before["sha256"] != source_mutation_after["sha256"]:
        raise ValidationError("SQLite backup mutated a source store")
    _require_equal_copy_evidence(
        source_health,
        backup_health,
        source_manifest,
        backup_manifest,
    )
    return BackupCohort(
        store_map=target_map,
        registry_revision=registry.revision,
        source_health=source_health,
        backup_health=backup_health,
        receipts=_receipts(
            source_health,
            backup_health,
            source_manifest,
            backup_manifest,
        ),
        source_logical_manifest_sha256=source_manifest["sha256"],
        backup_logical_manifest_sha256=backup_manifest["sha256"],
        source_mutation_before_sha256=source_mutation_before["sha256"],
        source_mutation_after_sha256=source_mutation_after["sha256"],
    )


def _validate_backup_cohort(backup_cohort: BackupCohort, registry: Registry) -> None:
    if not isinstance(backup_cohort, BackupCohort):
        raise ValidationError("Restore requires a validated backup cohort")
    if backup_cohort.registry_revision != registry.revision:
        raise ValidationError("Backup cohort registry revision does not match")
    expected_roles = tuple(role.value for role in STORE_ROLES)
    if tuple(receipt.role for receipt in backup_cohort.receipts) != expected_roles:
        raise ValidationError("Backup cohort receipts are incomplete")
    if (
        backup_cohort.source_logical_manifest_sha256
        != backup_cohort.backup_logical_manifest_sha256
        or backup_cohort.source_mutation_before_sha256
        != backup_cohort.source_mutation_after_sha256
    ):
        raise ValidationError("Backup cohort verification evidence is inconsistent")
    backup_cohort.store_map.validate_distinct()


def restore_all(
    backup_cohort: BackupCohort,
    registry: Registry,
    *,
    target_root: TargetRoot,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RestoreCohort:
    """Restore a validated backup cohort into another new or empty root."""

    timeout = _validated_timeout(timeout_seconds)
    _validate_backup_cohort(backup_cohort, registry)
    backup_map = backup_cohort.store_map
    # Refuse a stale, role-swapped, tampered, or corrupt backup before creating
    # an output root.
    preflight_health = inspect_all_stores(backup_map, registry)
    preflight_manifest = logical_manifest(backup_map, registry)
    if (
        preflight_health != backup_cohort.backup_health
        or preflight_manifest.get("sha256")
        != backup_cohort.backup_logical_manifest_sha256
    ):
        raise ValidationError("Backup cohort no longer matches its receipts")

    restored_map = _store_map_for_root(_prepare_empty_target_root(target_root))
    _assert_empty_targets(restored_map)
    with acquire_write_locks(backup_map, STORE_ROLES, timeout_seconds=timeout):
        backup_health = inspect_all_stores(backup_map, registry)
        backup_mutation_before = mutation_fingerprint(backup_map)
        backup_manifest = logical_manifest(backup_map, registry)
        if (
            backup_health != backup_cohort.backup_health
            or backup_manifest.get("sha256")
            != backup_cohort.backup_logical_manifest_sha256
        ):
            raise ValidationError("Backup cohort changed before restore")
        for role in STORE_ROLES:
            _online_copy(
                backup_map,
                restored_map,
                registry,
                role,
                timeout_seconds=timeout,
            )
        restored_health = inspect_all_stores(restored_map, registry)
        restored_manifest = logical_manifest(restored_map, registry)
        backup_mutation_after = mutation_fingerprint(backup_map)

    if backup_mutation_before["sha256"] != backup_mutation_after["sha256"]:
        raise ValidationError("SQLite restore mutated a backup store")
    _require_equal_copy_evidence(
        backup_health,
        restored_health,
        backup_manifest,
        restored_manifest,
    )
    return RestoreCohort(
        store_map=restored_map,
        registry_revision=registry.revision,
        backup_health=backup_health,
        restored_health=restored_health,
        receipts=_receipts(
            backup_health,
            restored_health,
            backup_manifest,
            restored_manifest,
        ),
        backup_logical_manifest_sha256=backup_manifest["sha256"],
        restored_logical_manifest_sha256=restored_manifest["sha256"],
        backup_mutation_before_sha256=backup_mutation_before["sha256"],
        backup_mutation_after_sha256=backup_mutation_after["sha256"],
    )

@dataclass(frozen=True, slots=True)
class ReadOnlyCopyLockReceipt:
    """One sanitized physical-lock acquisition used for a source-copy cohort."""

    role: str
    lock_key: str
    wait_seconds: float

    def to_primitive(self) -> dict[str, object]:
        return {
            "role": self.role,
            "lock_key": self.lock_key,
            "wait_seconds": self.wait_seconds,
        }


@dataclass(frozen=True, slots=True)
class ReadOnlyCopyReceipt:
    """Path-free verification evidence for one read-only export source copy."""

    role: str
    source_logical_sha256: str
    target_logical_sha256: str
    source_inspection: StoreInspection
    target_inspection: StoreInspection
    completed_at: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_logical_sha256": self.source_logical_sha256,
            "target_logical_sha256": self.target_logical_sha256,
            "source_inspection": self.source_inspection.to_primitive(),
            "target_inspection": self.target_inspection.to_primitive(),
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class ReadOnlySourceReceiptCheck:
    """Sanitized required-source completion evidence for an Atlas copy role."""

    role: str
    dataset_id: str
    checkpoint_sha256: str
    completed_at: str
    complete: bool
    running_runs: int
    unreconciled_failures: int
    completion_basis: str = "selected_complete"

    def to_primitive(self) -> dict[str, object]:
        return {
            "role": self.role,
            "dataset_id": self.dataset_id,
            "checkpoint_sha256": self.checkpoint_sha256,
            "completed_at": self.completed_at,
            "complete": self.complete,
            "running_runs": self.running_runs,
            "unreconciled_failures": self.unreconciled_failures,
            "completion_basis": self.completion_basis,
        }


@dataclass(frozen=True, slots=True)
class _SourceReceiptRecord:
    """Internal validated receipt-chain link; raw identifiers never leave this module."""

    run_id: str
    semantic_identity: str
    artifact_id: str
    snapshot_id: str
    completed_at: datetime
    completion_text: str
    completeness: str


@dataclass(frozen=True, slots=True)
class ReadOnlyCopyCohort:
    """Validated consistent source copies for a later read-only export phase.

    store_map is retained only as the internal handoff for the exporter. Its
    public primitive is path-free and contains no writable source handles.
    """

    store_map: StoreMap
    registry_revision: str
    source_health: HealthReport
    copy_health: HealthReport
    receipts: tuple[ReadOnlyCopyReceipt, ...]
    lock_order: tuple[ReadOnlyCopyLockReceipt, ...]
    coordination_started_at: str
    coordination_completed_at: str
    cross_store_atomic: bool
    source_logical_manifest_sha256: str
    copy_logical_manifest_sha256: str
    source_mutation_before_sha256: str
    source_mutation_after_sha256: str
    source_receipt_checks: tuple[ReadOnlySourceReceiptCheck, ...] = ()

    @property
    def copy_store_map(self) -> StoreMap:
        """Explicit internal name for the immutable SQLite copy cohort."""

        return self.store_map

    def to_primitive(self) -> dict[str, object]:
        return {
            "registry_revision": self.registry_revision,
            "source_health": self.source_health.to_primitive(),
            "copy_health": self.copy_health.to_primitive(),
            "receipts": [receipt.to_primitive() for receipt in self.receipts],
            "lock_order": [item.to_primitive() for item in self.lock_order],
            "coordination_started_at": self.coordination_started_at,
            "coordination_completed_at": self.coordination_completed_at,
            "cross_store_atomic": self.cross_store_atomic,
            "source_logical_manifest_sha256": self.source_logical_manifest_sha256,
            "copy_logical_manifest_sha256": self.copy_logical_manifest_sha256,
            "source_mutation_before_sha256": self.source_mutation_before_sha256,
            "source_mutation_after_sha256": self.source_mutation_after_sha256,
            "source_receipt_checks": [
                item.to_primitive() for item in self.source_receipt_checks
            ],
        }


def _read_copy_now(now: object) -> str:
    """Render an injected aware instant without consulting the ambient clock."""

    from datetime import datetime, timezone

    candidate = now() if callable(now) else now
    if (
        not isinstance(candidate, datetime)
        or candidate.tzinfo is None
        or candidate.utcoffset() is None
    ):
        raise ValidationError("Read-only copy time must be an aware datetime")
    return candidate.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _required_source_datasets(
    required_dataset_ids_by_role: Mapping[StoreRole | str, str] | None,
) -> tuple[tuple[StoreRole, str], ...]:
    """Normalize an explicit four-store Atlas source requirement."""

    if required_dataset_ids_by_role is None:
        return ()
    if not isinstance(required_dataset_ids_by_role, Mapping):
        raise ValidationError("Atlas source requirement is invalid")
    normalized: dict[StoreRole, str] = {}
    for raw_role, dataset_id in required_dataset_ids_by_role.items():
        try:
            role = StoreRole(raw_role)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Atlas source requirement has an unknown store role") from exc
        if (
            role in normalized
            or not isinstance(dataset_id, str)
            or not dataset_id
        ):
            raise ValidationError("Atlas source requirement is invalid")
        normalized[role] = dataset_id
    if set(normalized) != set(STORE_ROLES):
        raise ValidationError("Atlas source requirement must cover all four stores")
    return tuple((role, normalized[role]) for role in STORE_ROLES)


def _aware_completion(value: object, *, pointer: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("Atlas source receipt completion time is invalid")
    parsed = TemporalValue.parse(value, pointer=pointer)
    if parsed.precision is not TemporalPrecision.DATETIME or not isinstance(parsed.value, datetime):
        raise ValidationError("Atlas source receipt completion must be an aware datetime")
    return parsed.value


def _validate_aware_interval(
    started_at: object,
    completed_at: object,
    *,
    prefix: str,
) -> datetime:
    """Validate a terminal receipt interval without exposing receipt details."""

    started = _aware_completion(started_at, pointer=prefix + "/started_at")
    completed = _aware_completion(completed_at, pointer=prefix + "/completed_at")
    if completed < started:
        raise ValidationError("Atlas source receipt completion precedes its start")
    return completed


def _source_receipt_rows(
    connection: sqlite3.Connection,
    dataset_id: str,
) -> tuple[sqlite3.Row, ...]:
    """Return fixed-schema receipt rows for one registered source dataset."""

    return tuple(
        connection.execute(
            """
            SELECT run.run_id, run.dataset_id AS run_dataset_id, run.status,
                   run.started_at, run.completed_at,
                   run.semantic_identity AS run_semantic_identity,
                   run.artifact_id, run.snapshot_id,
                   output.dataset_id AS output_dataset_id,
                   output.semantic_identity AS output_semantic_identity,
                   artifact.artifact_id AS verified_artifact_id,
                   artifact.run_id AS artifact_run_id,
                   snapshot.snapshot_id AS verified_snapshot_id,
                   snapshot.run_id AS snapshot_run_id,
                   snapshot.semantic_identity AS snapshot_semantic_identity,
                   snapshot.completeness, snapshot.validation_state,
                   membership.snapshot_id AS membership_snapshot_id,
                   membership.artifact_id AS membership_artifact_id
            FROM ingestion_runs AS run
            LEFT JOIN ingestion_run_outputs AS output
              ON output.run_id=run.run_id
             AND output.dataset_id=?
            LEFT JOIN ingestion_artifacts AS artifact
              ON artifact.artifact_id=run.artifact_id
            LEFT JOIN ingestion_snapshots AS snapshot
              ON snapshot.snapshot_id=run.snapshot_id
            LEFT JOIN ingestion_snapshot_artifacts AS membership
              ON membership.snapshot_id=run.snapshot_id
             AND membership.artifact_id=run.artifact_id
            WHERE output.dataset_id=?
            """,
            (dataset_id, dataset_id),
        )
    )


def _validated_source_receipt(
    row: sqlite3.Row,
    dataset_id: str,
    *,
    prefix: str,
) -> _SourceReceiptRecord:
    """Validate one successful source receipt without exposing its raw IDs."""

    if (
        not isinstance(row["run_id"], str)
        or not row["run_id"]
        or row["status"] != "succeeded"
        or not isinstance(row["run_semantic_identity"], str)
        or not row["run_semantic_identity"]
        or not isinstance(row["artifact_id"], str)
        or not row["artifact_id"]
        or not isinstance(row["snapshot_id"], str)
        or not row["snapshot_id"]
        or row["output_dataset_id"] != dataset_id
        or row["output_semantic_identity"] != row["run_semantic_identity"]
        or row["verified_artifact_id"] != row["artifact_id"]
        or row["artifact_run_id"] != row["run_id"]
        or row["verified_snapshot_id"] != row["snapshot_id"]
        or row["snapshot_run_id"] != row["run_id"]
        or row["snapshot_semantic_identity"] != row["run_semantic_identity"]
        or row["membership_snapshot_id"] != row["snapshot_id"]
        or row["membership_artifact_id"] != row["artifact_id"]
        or row["completeness"] not in {"complete", "partial"}
        or row["validation_state"] != "validated"
        or not isinstance(row["completed_at"], str)
    ):
        raise ValidationError("Atlas source collector checkpoint is unreconciled")
    completed_at = _validate_aware_interval(
        row["started_at"],
        row["completed_at"],
        prefix=prefix,
    )
    return _SourceReceiptRecord(
        run_id=row["run_id"],
        semantic_identity=row["run_semantic_identity"],
        artifact_id=row["artifact_id"],
        snapshot_id=row["snapshot_id"],
        completed_at=completed_at,
        completion_text=row["completed_at"],
        completeness=row["completeness"],
    )


def _receipt_chain_sha256(
    completion_basis: str,
    records: tuple[_SourceReceiptRecord, ...],
) -> str:
    """Bind source receipt-chain identities in a digest without publishing them."""

    digest = hashlib.sha256()
    for value in (completion_basis, *(item for record in records for item in (
        record.run_id,
        record.semantic_identity,
        record.artifact_id,
        record.snapshot_id,
        record.completion_text,
        record.completeness,
    ))):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _required_source_receipt_checks(
    store_map: StoreMap,
    required_dataset_ids_by_role: Mapping[StoreRole | str, str] | None,
) -> tuple[ReadOnlySourceReceiptCheck, ...]:
    """Audit a complete checkpoint or a complete baseline plus validated deltas."""

    checks: list[ReadOnlySourceReceiptCheck] = []
    for role, dataset_id in _required_source_datasets(required_dataset_ids_by_role):
        with read_connection(store_map, role) as connection:
            dataset = connection.execute(
                """
                SELECT active, last_successful_run_id, last_semantic_identity
                FROM dataset_registry
                WHERE dataset_id=?
                """,
                (dataset_id,),
            ).fetchone()
            if (
                dataset is None
                or dataset["active"] != 1
                or not isinstance(dataset["last_successful_run_id"], str)
                or not dataset["last_successful_run_id"]
                or not isinstance(dataset["last_semantic_identity"], str)
                or not dataset["last_semantic_identity"]
            ):
                raise ValidationError(
                    "Atlas source is missing a required completed collector receipt"
                )

            rows = _source_receipt_rows(connection, dataset_id)
            selected_rows = tuple(
                row
                for row in rows
                if row["run_id"] == dataset["last_successful_run_id"]
                and row["run_semantic_identity"] == dataset["last_semantic_identity"]
            )
            if len(selected_rows) != 1:
                raise ValidationError("Atlas source collector checkpoint is unreconciled")
            selected = _validated_source_receipt(
                selected_rows[0],
                dataset_id,
                prefix="/selected",
            )

            if selected.completeness == "complete":
                completion_basis = "selected_complete"
                chain = (selected,)
            else:
                complete_candidates: list[_SourceReceiptRecord] = []
                for row in rows:
                    if (
                        row["run_id"] == selected.run_id
                        or row["status"] != "succeeded"
                        or row["completeness"] != "complete"
                    ):
                        continue
                    candidate = _validated_source_receipt(
                        row,
                        dataset_id,
                        prefix="/baseline",
                    )
                    if candidate.completed_at <= selected.completed_at:
                        complete_candidates.append(candidate)
                if not complete_candidates:
                    raise ValidationError(
                        "Atlas partial source checkpoint has no complete baseline"
                    )
                baseline = max(
                    complete_candidates,
                    key=lambda record: (
                        record.completed_at,
                        record.run_id,
                        record.snapshot_id,
                    ),
                )
                chain_records: list[_SourceReceiptRecord] = []
                for row in rows:
                    if row["status"] != "succeeded":
                        continue
                    completed_at = _validate_aware_interval(
                        row["started_at"],
                        row["completed_at"],
                        prefix="/delta",
                    )
                    if baseline.completed_at <= completed_at <= selected.completed_at:
                        chain_records.append(
                            _validated_source_receipt(
                                row,
                                dataset_id,
                                prefix="/delta",
                            )
                        )
                chain = tuple(
                    sorted(
                        chain_records,
                        key=lambda record: (
                            record.completed_at,
                            record.run_id,
                            record.snapshot_id,
                        ),
                    )
                )
                if (
                    baseline not in chain
                    or selected not in chain
                    or not chain
                ):
                    raise ValidationError("Atlas source receipt chain is unreconciled")
                completion_basis = "complete_baseline_plus_validated_delta"

            for row in rows:
                if row["status"] != "succeeded":
                    continue
                completed_at = _validate_aware_interval(
                    row["started_at"],
                    row["completed_at"],
                    prefix="/later",
                )
                if completed_at >= selected.completed_at:
                    _validated_source_receipt(
                        row,
                        dataset_id,
                        prefix="/later",
                    )

            running_runs = int(
                connection.execute(
                    """
                    SELECT count(*)
                    FROM ingestion_runs AS run
                    JOIN ingestion_run_outputs AS output
                      ON output.run_id=run.run_id
                    WHERE output.dataset_id=? AND run.status != 'succeeded'
                    """,
                    (dataset_id,),
                ).fetchone()[0]
            )
            failure_times = [
                _validate_aware_interval(
                    row["started_at"],
                    row["completed_at"],
                    prefix="/failure",
                )
                for row in connection.execute(
                    """
                    SELECT started_at, completed_at
                    FROM ingestion_run_failures
                    WHERE dataset_id=?
                    """,
                    (dataset_id,),
                )
            ]

        unreconciled_failures = sum(
            1 for failure_time in failure_times if failure_time >= selected.completed_at
        )
        if running_runs:
            raise ValidationError("Atlas source has an unreconciled running collector")
        if unreconciled_failures:
            raise ValidationError("Atlas source has a failed or partial collector receipt")
        checks.append(
            ReadOnlySourceReceiptCheck(
                role=role.value,
                dataset_id=dataset_id,
                checkpoint_sha256=_receipt_chain_sha256(completion_basis, chain),
                completed_at=selected.completion_text,
                complete=True,
                running_runs=running_runs,
                unreconciled_failures=unreconciled_failures,
                completion_basis=completion_basis,
            )
        )
    return tuple(checks)


def _copy_progress(
    callback: Callable[[str], None] | None,
    phase: str,
) -> None:
    if callback is not None:
        callback(phase)



def capture_readonly_copies(
    store_map: StoreMap,
    registry: Registry,
    target_root: TargetRoot,
    now: object,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    *,
    required_dataset_ids_by_role: Mapping[StoreRole | str, str] | None = None,
    progress_check: Callable[[str], None] | None = None,
) -> ReadOnlyCopyCohort:
    """Capture a complete validated SQLite-copy cohort for a read-only export.

    This additive primitive differs from backup_all only in its export-oriented
    receipt detail. It coordinates all four physical identities once, takes
    online SQLite backups, records each completed copy instant, proves source
    neutrality, and releases every lock before the caller receives the copy
    map for projection queries.
    """

    from ..stores import acquire_write_session

    timeout = _validated_timeout(timeout_seconds)
    store_map.validate_distinct()
    _copy_progress(progress_check, "copy_preflight")
    # Fail before creating a target whenever the operational cohort is not a
    # reconciled four-store source.
    inspect_all_stores(store_map, registry)
    root = _prepare_empty_target_root(target_root)
    target_map = _store_map_for_root(root)
    _assert_empty_targets(target_map)

    source_health: HealthReport
    source_manifest: dict[str, object]
    source_mutation_before: dict[str, object]
    source_mutation_after: dict[str, object]
    completion_by_role: dict[StoreRole, str] = {}
    lock_order: tuple[ReadOnlyCopyLockReceipt, ...]
    coordination_started_at: str
    coordination_completed_at: str
    source_receipt_checks: tuple[ReadOnlySourceReceiptCheck, ...]
    with acquire_write_session(
        store_map,
        STORE_ROLES,
        timeout_seconds=timeout,
    ) as held_locks:
        coordination_started_at = _read_copy_now(now)
        _copy_progress(progress_check, "copy_locked")
        source_health = inspect_all_stores(store_map, registry)
        source_receipt_checks = _required_source_receipt_checks(
            store_map,
            required_dataset_ids_by_role,
        )
        _copy_progress(progress_check, "copy_source_receipts")
        source_mutation_before = mutation_fingerprint(store_map)
        source_manifest = logical_manifest(store_map, registry)
        for role in STORE_ROLES:
            _copy_progress(progress_check, "copy_" + role.value + "_before")
            _online_copy(
                store_map,
                target_map,
                registry,
                role,
                timeout_seconds=timeout,
                progress_check=progress_check,
            )
            completion_by_role[role] = _read_copy_now(now)
            _copy_progress(progress_check, "copy_" + role.value + "_after")
        coordination_completed_at = _read_copy_now(now)
        source_mutation_after = mutation_fingerprint(store_map)
        lock_order = tuple(
            ReadOnlyCopyLockReceipt(
                role=identity.role.value,
                lock_key=identity.lock_key,
                wait_seconds=wait_seconds,
            )
            for identity, wait_seconds in zip(
                held_locks.identities,
                held_locks.acquisition_wait_seconds,
                strict=True,
            )
        )

    _copy_progress(progress_check, "copy_released")

    if source_mutation_before["sha256"] != source_mutation_after["sha256"]:
        raise ValidationError("Read-only SQLite copy mutated a source store")
    copy_health = inspect_all_stores(target_map, registry)
    copy_manifest = logical_manifest(target_map, registry)
    _require_equal_copy_evidence(
        source_health,
        copy_health,
        source_manifest,
        copy_manifest,
    )
    return ReadOnlyCopyCohort(
        store_map=target_map,
        registry_revision=registry.revision,
        source_health=source_health,
        copy_health=copy_health,
        receipts=tuple(
            ReadOnlyCopyReceipt(
                role=role.value,
                source_logical_sha256=_store_logical_sha256(source_manifest, role),
                target_logical_sha256=_store_logical_sha256(copy_manifest, role),
                source_inspection=source_health.inspection_for(role),
                target_inspection=copy_health.inspection_for(role),
                completed_at=completion_by_role[role],
            )
            for role in STORE_ROLES
        ),
        lock_order=lock_order,
        coordination_started_at=coordination_started_at,
        coordination_completed_at=coordination_completed_at,
        cross_store_atomic=False,
        source_logical_manifest_sha256=source_manifest["sha256"],
        copy_logical_manifest_sha256=copy_manifest["sha256"],
        source_mutation_before_sha256=source_mutation_before["sha256"],
        source_mutation_after_sha256=source_mutation_after["sha256"],
        source_receipt_checks=source_receipt_checks,
    )
