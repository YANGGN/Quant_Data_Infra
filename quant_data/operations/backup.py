"""Offline, SQLite-online-backup cohorts for the four operational stores."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ..errors import ConflictError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..registry import Registry
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
) -> None:
    """Copy one committed SQLite snapshot without copying WAL sidecar files."""

    target_path = target_map.path(role)
    if target_path.exists():
        raise ConflictError("Backup and restore targets must not overwrite a store")
    declaration = registry.store(role.value)
    destination: sqlite3.Connection | None = None
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
            source.backup(destination)
    except sqlite3.Error as exc:
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
