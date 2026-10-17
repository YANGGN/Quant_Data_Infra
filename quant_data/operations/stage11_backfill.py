"""Restricted manual Stage 11 BEA/EIA macro-history backfill.

The runner has one reviewed target and one fixed set of requests.  It never
loads a dotenv file, discovers a provider, or exposes a promotion path.  Its
only retries are the reviewed bounded transient attempts.  Credentials are supplied by the caller's process environment
only, are passed directly to the capture primitives, and are never retained in
the private progress material or rendered result.

Stage 10 owns the surrounding target layout.  Consequently Stage 11 keeps all
of its own private state beneath ``private/candidate/stage11``; this preserves
the Stage 10 completed-receipt reader and its zero-network reuse semantics.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import math
import os
import re
import stat
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from ..errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict, loads_strict
from ..macro.stage11_bea import (
    BeaNipaHistoryCohort,
    StdlibBeaTransport,
    assemble_bea_nipa_history,
    capture_bea_nipa,
    prepare_bea_nipa_captures,
)
from ..macro.stage11_eia import (
    EIA_RETAIL_PAGE_LENGTH,
    EiaRetailCapture,
    EiaWeeklyCapture,
    StdlibEiaTransport,
    assemble_eia_retail_capture,
    capture_eia_retail_page,
    capture_eia_weekly,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from ..macro.stage11_retry import (
    STAGE11_MAX_REQUEST_ATTEMPTS,
    STAGE11_RETRY_BACKOFF_SECONDS,
    Stage11TransientRequestError,
)
from ..macro.stage11_publication import (
    BEA_NIPA_HISTORY_DATASET_ID,
    EIA_ELECTRICITY_RETAIL_HISTORY_DATASET_ID,
    EIA_PETROLEUM_WEEKLY_STOCK_HISTORY_DATASET_ID,
    STAGE11_MIGRATION_ID,
    STAGE11_MIGRATION_RESOURCE,
    STAGE11_MIGRATION_SHA256,
    Stage11MacroImporter,
)
from ..macro.stage11_scope import (
    REVIEWED_STAGE11_MACRO_SCOPE_SHA256,
    STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT,
    STAGE11_REQUIRED_MARKET_PROFILE_ID,
    STAGE11_TARGET_PROFILE_ID,
    Stage11MacroScope,
    load_stage11_macro_scope,
)
from ..migrations import initialize_all
from ..registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage11_registry_profile,
)
from ..stores import StoreMap, StoreRole
from .stage10_completion_markers import (
    Stage10CandidateReceipt,
    normalize_stage10_failed_records,
    stage10_failure_manifest_sha256,
)
from .stage11_completion import (
    PrivateStage11CompletionCandidateState,
    Stage11CandidateReceipt,
    Stage11CompletionEvidence,
    build_stage11_completion_evidence,
)


APPROVED_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_TARGET_ROOT = Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1")
_SCOPE_RELATIVE_PATH = Path("config/stage11_macro_scope.json")
_STAGE10_SCOPE_RELATIVE_PATH = Path("config/stage10_market_scope.json")
_PRIVATE_DIRECTORY = "private"
_STORES_DIRECTORY = "stores"
_CANDIDATE_DIRECTORY = "candidate"
_STAGE11_DIRECTORY = "stage11"
_STAGE10_RECEIPTS_DIRECTORY = "promotion-candidates"
_STAGE10_RESUME_FILENAME = "resume.json"
_STAGE10_COMPLETION_FILENAME = "completion.json"
_STAGE10_FAILURES_DIRECTORY = "failures"
_STAGE10_RESUME_CONTRACT = "quant_data.stage10_backfill_resume"
_STAGE10_COMPLETION_CONTRACT = "quant_data.stage10_backfill_completion"
_STAGE10_FAILURE_CONTRACT = "quant_data.stage10_backfill_failure"
_STAGE10_RESUME_VERSION = "1.1.0"
_STAGE10_COMPLETION_VERSION = "1.1.0"
_STAGE10_FAILURE_VERSION = "1.0.0"
_STAGE10_RECONCILIATION_CONTRACT = "quant_data.stage10_market_reconciliation"
_STAGE10_RECONCILIATION_VERSION = "1.1.0"
_STAGE10_EVIDENCE_CONTRACT = "quant_data.stage10_repopulation_evidence"
_STAGE10_EVIDENCE_VERSION = "1.1.0"
_RESUME_FILENAME = "resume.json"
_RUN_LOCK_FILENAME = "run.lock"
_BACKUP_DIRECTORY = "backup"
_RESTORED_DIRECTORY = "restored"
_CANDIDATE_STATE_DIRECTORY = "candidate"
_COMPLETION_FILENAME = "completion.json"
_RESUME_CONTRACT = "quant_data.stage11_backfill_resume"
_RESUME_VERSION = "1.0.0"
_COMPLETION_CONTRACT = "quant_data.stage11_backfill_completion"
_COMPLETION_VERSION = "1.0.0"
_RESULT_CONTRACT = "quant_data.stage11_backfill_result"
_ERROR_CONTRACT = "quant_data.stage11_backfill_error"
_CONTRACT_VERSION = "1.0.0"
_ROOT_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_MAX_RESUME_BYTES = 256 * 1024
_MAX_RECEIPT_BYTES = 512 * 1024
_MAX_STAGE10_FAILURE_RECEIPT_BYTES = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")


class _ArgumentFailure(Exception):
    """A CLI input failed without disclosing the supplied value."""


class _ConfigurationFailure(Exception):
    """A reviewed declaration or process-only credential is unavailable."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class _TargetLayout:
    root: Path
    stores: Path
    private: Path
    stage11: Path
    resume: Path
    run_lock: Path
    backup: Path
    restored: Path
    candidate: Path
    completion: Path


@dataclass(frozen=True, slots=True)
class Stage11ResumeState:
    """Credential-free private phase checkpoint for the one Stage 11 cohort."""

    target_profile_id: str
    scope_manifest_sha256: str
    registry_source_sha256: str
    bea_published: bool
    retail_published: bool
    weekly_published: bool

    def to_primitive(self) -> dict[str, object]:
        return {
            "bea_published": self.bea_published,
            "contract": _RESUME_CONTRACT,
            "registry_source_sha256": self.registry_source_sha256,
            "retail_published": self.retail_published,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "version": _RESUME_VERSION,
            "weekly_published": self.weekly_published,
        }


@dataclass(frozen=True, slots=True)
class Stage11CompletionContext:
    """Narrow private completion seam after all three publications commit."""

    store_map: StoreMap
    registry: Registry
    scope: Stage11MacroScope
    private_root: Path
    resumed: bool


@dataclass(frozen=True, slots=True)
class _CompletionReceipt:
    target_profile_id: str
    scope_manifest_sha256: str
    registry_source_sha256: str
    evidence_sha256: str
    candidate_receipt_sha256: str
    sha256: str

    def material(self) -> dict[str, object]:
        return {
            "candidate_receipt_sha256": self.candidate_receipt_sha256,
            "contract": _COMPLETION_CONTRACT,
            "evidence_sha256": self.evidence_sha256,
            "registry_source_sha256": self.registry_source_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "version": _COMPLETION_VERSION,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class Stage11BackfillBindings:
    """Transport/capture/publication seams used by the offline operation tests."""

    bea_transport_factory: Callable[[str, StoreMap, Stage11MacroScope], object]
    eia_transport_factory: Callable[[str, StoreMap, Stage11MacroScope], object]
    capture_bea: Callable[[object, str, object], object]
    assemble_bea: Callable[[Sequence[object]], object]
    capture_retail_page: Callable[[object, str, object], object]
    assemble_retail: Callable[[Sequence[object]], object]
    capture_weekly: Callable[[object, str, object], object]
    importer_factory: Callable[[StoreMap, Registry], object]
    prepare_bea: Callable[[object, object], object]
    prepare_retail: Callable[[object, object], object]
    prepare_weekly: Callable[[object, object], object]
    publish: Callable[[object, object], object]


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python3 -m quant_data.operations.stage11_backfill",
        add_help=False,
        description="Run only the reviewed nonproduction Stage 11 BEA/EIA backfill",
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--target-root", required=True)
    return parser


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode):
            return True
    return False


def _require_exact_path(value: object, *, expected: str | Path) -> Path:
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise _ConfigurationFailure
    if not isinstance(value, str) or value != str(expected_path):
        raise _ArgumentFailure
    supplied = Path(value)
    try:
        if not supplied.is_absolute() or _has_symlink_component(supplied):
            raise ValueError
        resolved = supplied.resolve(strict=False)
        expected_resolved = expected_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ArgumentFailure from exc
    if resolved != expected_resolved:
        raise _ArgumentFailure
    return expected_resolved


def _preflight_project_root(value: object, expected: str | Path) -> Path:
    root = _require_exact_path(value, expected=expected)
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise _ArgumentFailure from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise _ArgumentFailure
    return root


def _preflight_target_root(value: object, expected: str | Path) -> Path:
    root = _require_exact_path(value, expected=expected)
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise _ConfigurationFailure from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise _ConfigurationFailure
    return root


def _read_api_key(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _ConfigurationFailure
    return value


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 11 {field_name} is invalid")
    return value


def _registry_preflight(registry: Registry) -> None:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.7.0"
        or registry.registry_version != "2.9.0"
        or registry.status != "validated"
    ):
        raise _ConfigurationFailure
    migration = next((item for item in registry.migrations if item.id == STAGE11_MIGRATION_ID), None)
    if (
        migration is None
        or migration.store != StoreRole.MACRO.value
        or migration.ordinal != 12
        or migration.resource != STAGE11_MIGRATION_RESOURCE
        or migration.sha256 != STAGE11_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
    ):
        raise _ConfigurationFailure
    _digest(registry.source_sha256, "registry source pin")


def _scope_preflight(scope: Stage11MacroScope) -> None:
    if not isinstance(scope, Stage11MacroScope) or (
        scope.target_profile_id != STAGE11_TARGET_PROFILE_ID
        or scope.manifest_sha256 != REVIEWED_STAGE11_MACRO_SCOPE_SHA256
        or scope.dependency.required_market_candidate_contract
        != STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT
        or scope.dependency.required_market_profile_id != STAGE11_REQUIRED_MARKET_PROFILE_ID
        or scope.bounds.bea_max_requests != 2
        or scope.bounds.eia_max_pages != 8
        or scope.bounds.minimum_request_interval_milliseconds < 1000
    ):
        raise _ConfigurationFailure


def _store_map_for(stores_root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=stores_root / "market.sqlite",
        macro=stores_root / "macro.sqlite",
        company=stores_root / "company.sqlite",
        news=stores_root / "news.sqlite",
    )


def _validate_target_store_map(store_map: object, stores_root: Path) -> StoreMap:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 11 store binding is invalid")
    for role, filename in (
        (StoreRole.MARKET, "market.sqlite"),
        (StoreRole.MACRO, "macro.sqlite"),
        (StoreRole.COMPANY, "company.sqlite"),
        (StoreRole.NEWS, "news.sqlite"),
    ):
        expected = (stores_root / filename).resolve(strict=False)
        if store_map.path(role) != expected or _has_symlink_component(stores_root / filename):
            raise ValidationError("Stage 11 store binding is outside the approved target")
    return store_map


def _validate_materialized_store_files(store_map: StoreMap, stores_root: Path) -> None:
    """Reject missing, linked, replaced, or physically aliased target stores."""

    physical_identities: set[tuple[int, int]] = set()
    for role in StoreRole:
        expected = stores_root / f"{role.value}.sqlite"
        path = store_map.path(role)
        try:
            info = path.lstat()
            resolved = path.resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise ConflictError("Stage 11 target store layout is incomplete") from exc
        if (
            path != expected_resolved
            or resolved != expected_resolved
            or _has_symlink_component(expected)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1
        ):
            raise ConflictError("Stage 11 target store identity is unsafe")
        identity = (int(info.st_dev), int(info.st_ino))
        if identity in physical_identities:
            raise ConflictError("Stage 11 target stores are physically aliased")
        physical_identities.add(identity)
    if len(physical_identities) != len(tuple(StoreRole)):
        raise ConflictError("Stage 11 target stores are physically aliased")


def _require_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 11 {label} is missing") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != _ROOT_MODE:
        raise ConflictError(f"Stage 11 {label} is invalid")


def _require_file(path: Path, *, label: str, maximum: int = _MAX_RESUME_BYTES, empty: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 11 {label} is missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE_MODE
        or info.st_size > maximum
        or (empty and info.st_size != 0)
    ):
        raise ConflictError(f"Stage 11 {label} is invalid")


def _layout_paths(root: Path) -> _TargetLayout:
    stores = root / _STORES_DIRECTORY
    private = root / _PRIVATE_DIRECTORY
    stage11 = private / _CANDIDATE_DIRECTORY / _STAGE11_DIRECTORY
    return _TargetLayout(
        root=root,
        stores=stores,
        private=private,
        stage11=stage11,
        resume=stage11 / _RESUME_FILENAME,
        run_lock=stage11 / _RUN_LOCK_FILENAME,
        backup=stage11 / _BACKUP_DIRECTORY,
        restored=stage11 / _RESTORED_DIRECTORY,
        candidate=stage11 / _CANDIDATE_STATE_DIRECTORY,
        completion=stage11 / _COMPLETION_FILENAME,
    )


def _existing_target_layout(root: Path) -> _TargetLayout:
    """Validate only Stage 10-owned parent components without changing them."""

    _require_directory(root, label="target root")
    entries = {item.name for item in root.iterdir()}
    if entries != {_STORES_DIRECTORY, _PRIVATE_DIRECTORY}:
        raise ConflictError("Stage 11 target layout is not the reviewed Stage 10 target")
    layout = _layout_paths(root)
    _require_directory(layout.stores, label="stores directory")
    _require_directory(layout.private, label="private directory")
    candidate_parent = layout.private / _CANDIDATE_DIRECTORY
    _require_directory(candidate_parent, label="Stage 10 candidate directory")
    return layout


@dataclass(frozen=True, slots=True)
class _Stage10FailureManifest:
    """One canonical Stage 10 omission declaration bound across all markers."""

    records: tuple[Mapping[str, object], ...]
    tickers: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class _Stage10CompletionBinding:
    """Receipt-last Stage 10 coverage state needed for the current-store proof."""

    completed_symbols: tuple[str, ...]
    roster_symbol_count: int
    failures: _Stage10FailureManifest


def _stage10_failure_manifest(
    value: object,
    *,
    label: str,
) -> _Stage10FailureManifest:
    """Validate and retain the exact canonical failure-record JSON list.

    The Stage 10 normalizer deliberately accepts only a tuple of closed record
    mappings, then sorts it by symbol.  Requiring the input JSON digest to
    equal that normalized digest means a Stage 11 dependency cannot quietly
    accept a reordered, widened, or otherwise noncanonical manifest.
    """

    if not isinstance(value, list):
        raise ConflictError(f"Stage 11 {label} is invalid")
    try:
        # Retain the explicit normalizer call as the authoritative closed
        # record validator; the separate raw digest comparison below rejects
        # its otherwise intentional canonical reordering behavior.
        normalize_stage10_failed_records(tuple(value))
        normalized_digest = stage10_failure_manifest_sha256(tuple(value))
        rendered = dumps_strict(value)
        raw_digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    except (TypeError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError(f"Stage 11 {label} is invalid") from exc
    if raw_digest != normalized_digest or any(
        not isinstance(record, Mapping) for record in value
    ):
        raise ConflictError(f"Stage 11 {label} is invalid")
    records = tuple(value)
    tickers = tuple(str(record["symbol"]) for record in records)
    return _Stage10FailureManifest(
        records=records,
        tickers=tickers,
        sha256=normalized_digest,
    )


def _same_stage10_failure_manifest(
    left: _Stage10FailureManifest,
    right: _Stage10FailureManifest,
) -> bool:
    return (
        left.sha256 == right.sha256
        and left.tickers == right.tickers
        and dumps_strict(list(left.records)) == dumps_strict(list(right.records))
    )


def _candidate_failure_manifest(candidate: Stage10CandidateReceipt) -> _Stage10FailureManifest:
    """Extract the exact v1.1 Stage 10 omission proof from a candidate."""

    primitive = candidate.to_primitive()
    evidence = primitive.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ConflictError("Stage 11 required Stage 10 candidate evidence is invalid")
    reconciliation = evidence.get("reconciliation")
    if not isinstance(reconciliation, Mapping):
        raise ConflictError("Stage 11 required Stage 10 candidate evidence is invalid")
    try:
        manifest = _stage10_failure_manifest(
            reconciliation.get("failed_records"),
            label="Stage 10 candidate failure manifest",
        )
        declared_digest = _digest(
            reconciliation.get("failure_manifest_sha256"),
            "Stage 10 candidate failure manifest digest",
        )
        source_digest = _digest(
            evidence.get("source_failure_manifest_sha256"),
            "Stage 10 source failure manifest digest",
        )
        restored_digest = _digest(
            evidence.get("restored_failure_manifest_sha256"),
            "Stage 10 restored failure manifest digest",
        )
        reconciliation_digest = _digest(
            reconciliation.get("sha256"), "Stage 10 reconciliation digest"
        )
        source_reconciliation_digest = _digest(
            evidence.get("source_reconciliation_sha256"),
            "Stage 10 source reconciliation digest",
        )
        restored_reconciliation_digest = _digest(
            evidence.get("restored_reconciliation_sha256"),
            "Stage 10 restored reconciliation digest",
        )
    except ValidationError as exc:
        raise ConflictError("Stage 11 required Stage 10 candidate evidence is invalid") from exc
    counts = (
        reconciliation.get("roster_count"),
        reconciliation.get("successful_symbol_count"),
        reconciliation.get("failed_symbol_count"),
    )
    coverage = reconciliation.get("price_coverage")
    if (
        evidence.get("contract") != _STAGE10_EVIDENCE_CONTRACT
        or evidence.get("contract_version") != _STAGE10_EVIDENCE_VERSION
        or reconciliation.get("contract") != _STAGE10_RECONCILIATION_CONTRACT
        or reconciliation.get("contract_version") != _STAGE10_RECONCILIATION_VERSION
        or reconciliation.get("failed_tickers") != list(manifest.tickers)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in counts)
        or counts[0] <= 0
        or counts[1] < 0
        or counts[2] < 0
        or counts[0] != counts[1] + counts[2]
        or counts[2] != len(manifest.records)
        or not isinstance(coverage, list)
        or len(coverage) != counts[1]
        or declared_digest != manifest.sha256
        or source_digest != manifest.sha256
        or restored_digest != manifest.sha256
        or reconciliation_digest != source_reconciliation_digest
        or reconciliation_digest != restored_reconciliation_digest
        or evidence.get("complete_symbol_coverage") is not True
    ):
        raise ConflictError("Stage 11 required Stage 10 candidate evidence is invalid")
    return manifest


def _read_stage10_failure_journal(
    layout: _TargetLayout,
    *,
    scope: Stage11MacroScope,
    scope_manifest_sha256: str,
    registry_source_sha256: str,
    expected: _Stage10FailureManifest,
) -> _Stage10FailureManifest:
    """Prove the journal-first durable failure receipts match its markers."""

    directory = layout.private / _STAGE10_FAILURES_DIRECTORY
    _require_directory(directory, label="Stage 10 failure journal")
    expected_names = {f"{symbol}.json" for symbol in expected.tickers}
    entries = {entry.name: entry for entry in directory.iterdir()}
    if set(entries) != expected_names:
        raise ConflictError("Stage 11 required Stage 10 failure journal is inconsistent")
    journal_records: list[Mapping[str, object]] = []
    for symbol, record in zip(expected.tickers, expected.records, strict=True):
        path = entries[f"{symbol}.json"]
        _require_file(
            path,
            label="Stage 10 failure receipt",
            maximum=_MAX_STAGE10_FAILURE_RECEIPT_BYTES,
        )
        try:
            info = path.lstat()
            raw = loads_strict(
                path.read_bytes(), max_bytes=_MAX_STAGE10_FAILURE_RECEIPT_BYTES
            )
        except (OSError, ResourceLimitError, ValidationError) as exc:
            raise ConflictError("Stage 11 required Stage 10 failure journal is invalid") from exc
        fields = {
            "contract",
            "record",
            "registry_source_sha256",
            "scope_manifest_sha256",
            "sha256",
            "target_profile_id",
            "version",
        }
        if (
            info.st_nlink != 1
            or not isinstance(raw, Mapping)
            or set(raw) != fields
            or raw["contract"] != _STAGE10_FAILURE_CONTRACT
            or raw["version"] != _STAGE10_FAILURE_VERSION
            or raw["target_profile_id"]
            != scope.dependency.required_market_profile_id
            or raw["scope_manifest_sha256"] != scope_manifest_sha256
            or raw["registry_source_sha256"] != registry_source_sha256
        ):
            raise ConflictError("Stage 11 required Stage 10 failure journal is invalid")
        receipt_manifest = _stage10_failure_manifest(
            [raw["record"]], label="Stage 10 failure receipt record"
        )
        material = {
            "contract": raw["contract"],
            "record": raw["record"],
            "registry_source_sha256": raw["registry_source_sha256"],
            "scope_manifest_sha256": raw["scope_manifest_sha256"],
            "target_profile_id": raw["target_profile_id"],
            "version": raw["version"],
        }
        try:
            receipt_digest = _digest(raw["sha256"], "Stage 10 failure receipt digest")
        except ValidationError as exc:
            raise ConflictError("Stage 11 required Stage 10 failure journal is invalid") from exc
        if (
            receipt_manifest.tickers != (symbol,)
            or dumps_strict(list(receipt_manifest.records))
            != dumps_strict([record])
            or receipt_digest
            != hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()
        ):
            raise ConflictError("Stage 11 required Stage 10 failure journal is inconsistent")
        journal_records.append(receipt_manifest.records[0])
    journal = _stage10_failure_manifest(
        journal_records, label="Stage 10 failure journal"
    )
    if not _same_stage10_failure_manifest(journal, expected):
        raise ConflictError("Stage 11 required Stage 10 failure journal is inconsistent")
    return journal


def _dependency_receipt(layout: _TargetLayout, scope: Stage11MacroScope) -> Stage10CandidateReceipt:
    """Read the completed Stage 10 candidate before credentials or writes.

    This intentionally validates the receipt's immutable self-digests through
    the published Stage 10 receipt type and binds its reconciliation profile to
    the Stage 11 scope.  It does not use a current registry source pin because
    the Stage 10 receipt necessarily predates the additive Stage 11 registry.
    """

    directory = layout.private / _CANDIDATE_DIRECTORY / _STAGE10_RECEIPTS_DIRECTORY
    _require_directory(directory, label="Stage 10 candidate receipt directory")
    entries = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    if len(entries) != 1 or entries[0].suffix != ".json":
        raise ConflictError("Stage 11 requires one completed Stage 10 candidate receipt")
    path = entries[0]
    _require_file(path, label="Stage 10 candidate receipt", maximum=_MAX_RECEIPT_BYTES)
    try:
        raw = loads_strict(path.read_bytes(), max_bytes=_MAX_RECEIPT_BYTES)
        if not isinstance(raw, Mapping) or set(raw) != {
            "attempt_id", "candidate_state", "code_revision", "contract",
            "contract_version", "evidence", "evidence_sha256", "generated_at", "receipt_sha256",
        }:
            raise ValidationError("receipt shape")
        receipt = Stage10CandidateReceipt(
            attempt_id=raw["attempt_id"], generated_at=raw["generated_at"],
            code_revision=raw["code_revision"], candidate_state=raw["candidate_state"],
            evidence=raw["evidence"], evidence_sha256=raw["evidence_sha256"],
            receipt_sha256=raw["receipt_sha256"],
        )
    except (OSError, ResourceLimitError, TypeError, ValidationError) as exc:
        raise ConflictError("Stage 11 required Stage 10 receipt is invalid") from exc
    primitive = receipt.to_primitive()
    evidence = primitive.get("evidence")
    reconciliation = evidence.get("reconciliation") if isinstance(evidence, Mapping) else None
    if (
        raw["contract"] != scope.dependency.required_market_candidate_contract
        or raw["contract_version"] != "1.0.0"
        or receipt.candidate_state != "private_candidate_only_no_operational_promotion"
        or not isinstance(reconciliation, Mapping)
        or reconciliation.get("target_profile_id") != scope.dependency.required_market_profile_id
        or evidence.get("backup_restore_outcome") != "validated_restored_cohort_equal"
        or evidence.get("replay_unchanged") is not True
    ):
        raise ConflictError("Stage 11 required Stage 10 receipt does not satisfy the reviewed dependency")
    _candidate_failure_manifest(receipt)
    return receipt


def _require_stage10_completion(
    layout: _TargetLayout,
    scope: Stage11MacroScope,
    candidate: Stage10CandidateReceipt,
) -> _Stage10CompletionBinding:
    """Require Stage 10's receipt-last completion state, not a candidate orphan.

    The Stage 10 receipt predates the additive Stage 11 registry.  This check
    therefore binds the two private Stage 10 markers to the candidate receipt's
    immutable code/evidence identities instead of using the current registry.
    """

    completion_path = layout.private / _STAGE10_COMPLETION_FILENAME
    resume_path = layout.private / _STAGE10_RESUME_FILENAME
    _require_file(completion_path, label="Stage 10 completion receipt")
    _require_file(resume_path, label="Stage 10 resume state")
    try:
        completion_raw = loads_strict(
            completion_path.read_bytes(), max_bytes=_MAX_RESUME_BYTES
        )
        resume_raw = loads_strict(resume_path.read_bytes(), max_bytes=_MAX_RESUME_BYTES)
    except (OSError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError("Stage 11 required Stage 10 completion state is unreadable") from exc

    completion_fields = {
        "candidate_receipt_sha256",
        "completed_symbol_count",
        "contract",
        "evidence_sha256",
        "failed_records",
        "failed_symbol_count",
        "failed_tickers",
        "failure_manifest_sha256",
        "registry_source_sha256",
        "roster_symbol_count",
        "scope_manifest_sha256",
        "sha256",
        "target_profile_id",
        "version",
    }
    if not isinstance(completion_raw, Mapping) or set(completion_raw) != completion_fields:
        raise ConflictError("Stage 11 required Stage 10 completion receipt is invalid")
    material = {
        "contract": completion_raw["contract"],
        "version": completion_raw["version"],
        "target_profile_id": completion_raw["target_profile_id"],
        "scope_manifest_sha256": completion_raw["scope_manifest_sha256"],
        "registry_source_sha256": completion_raw["registry_source_sha256"],
        "roster_symbol_count": completion_raw["roster_symbol_count"],
        "completed_symbol_count": completion_raw["completed_symbol_count"],
        "failed_records": completion_raw["failed_records"],
        "failed_tickers": completion_raw["failed_tickers"],
        "failed_symbol_count": completion_raw["failed_symbol_count"],
        "failure_manifest_sha256": completion_raw["failure_manifest_sha256"],
        "evidence_sha256": completion_raw["evidence_sha256"],
        "candidate_receipt_sha256": completion_raw["candidate_receipt_sha256"],
    }
    try:
        completion_digest = _digest(completion_raw["sha256"], "Stage 10 completion digest")
        candidate_digest = _digest(
            completion_raw["candidate_receipt_sha256"], "Stage 10 candidate digest"
        )
        evidence_digest = _digest(completion_raw["evidence_sha256"], "Stage 10 evidence digest")
        scope_digest = _digest(completion_raw["scope_manifest_sha256"], "Stage 10 scope digest")
        revision = _digest(completion_raw["registry_source_sha256"], "Stage 10 registry digest")
        completion_failure_digest = _digest(
            completion_raw["failure_manifest_sha256"],
            "Stage 10 completion failure manifest digest",
        )
    except ValidationError as exc:
        raise ConflictError("Stage 11 required Stage 10 completion receipt is invalid") from exc
    completion_manifest = _stage10_failure_manifest(
        completion_raw["failed_records"],
        label="Stage 10 completion failure manifest",
    )
    counts = (completion_raw["roster_symbol_count"], completion_raw["completed_symbol_count"])
    if (
        completion_raw["contract"] != _STAGE10_COMPLETION_CONTRACT
        or completion_raw["version"] != _STAGE10_COMPLETION_VERSION
        or completion_raw["target_profile_id"] != scope.dependency.required_market_profile_id
        or isinstance(counts[0], bool)
        or isinstance(counts[1], bool)
        or not isinstance(completion_raw["failed_symbol_count"], int)
        or isinstance(completion_raw["failed_symbol_count"], bool)
        or not all(isinstance(item, int) for item in counts)
        or counts[0] <= 0
        or counts[1] < 0
        or completion_raw["failed_symbol_count"] != len(completion_manifest.records)
        or completion_raw["failed_tickers"] != list(completion_manifest.tickers)
        or completion_failure_digest != completion_manifest.sha256
        or counts[0] != counts[1] + len(completion_manifest.records)
        or completion_digest
        != hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()
        or candidate_digest != candidate.receipt_sha256
        or evidence_digest != candidate.evidence_sha256
        or revision != candidate.code_revision
    ):
        raise ConflictError("Stage 11 required Stage 10 completion receipt is inconsistent")

    candidate_primitive = candidate.to_primitive()
    candidate_evidence = candidate_primitive.get("evidence")
    reconciliation = (
        candidate_evidence.get("reconciliation")
        if isinstance(candidate_evidence, Mapping)
        else None
    )
    candidate_manifest = _candidate_failure_manifest(candidate)
    if (
        not isinstance(reconciliation, Mapping)
        or reconciliation.get("scope_manifest_sha256") != scope_digest
        or reconciliation.get("target_profile_id") != completion_raw["target_profile_id"]
        or reconciliation.get("roster_count") != counts[0]
        or reconciliation.get("successful_symbol_count") != counts[1]
        or reconciliation.get("failed_symbol_count") != len(completion_manifest.records)
        or reconciliation.get("failure_manifest_sha256") != completion_manifest.sha256
        or not _same_stage10_failure_manifest(candidate_manifest, completion_manifest)
    ):
        raise ConflictError("Stage 11 required Stage 10 completion receipt is inconsistent")
    resume_fields = {
        "completed_symbols",
        "contract",
        "failed_records",
        "failure_manifest_sha256",
        "registry_source_sha256",
        "scope_manifest_sha256",
        "target_profile_id",
        "universes_published",
        "version",
    }
    if not isinstance(resume_raw, Mapping) or set(resume_raw) != resume_fields:
        raise ConflictError("Stage 11 required Stage 10 resume state is invalid")
    symbols = resume_raw["completed_symbols"]
    resume_manifest = _stage10_failure_manifest(
        resume_raw["failed_records"], label="Stage 10 resume failure manifest"
    )
    try:
        resume_failure_digest = _digest(
            resume_raw["failure_manifest_sha256"],
            "Stage 10 resume failure manifest digest",
        )
    except ValidationError as exc:
        raise ConflictError("Stage 11 required Stage 10 resume state is invalid") from exc
    if (
        resume_raw["contract"] != _STAGE10_RESUME_CONTRACT
        or resume_raw["version"] != _STAGE10_RESUME_VERSION
        or resume_raw["target_profile_id"] != completion_raw["target_profile_id"]
        or resume_raw["scope_manifest_sha256"] != scope_digest
        or resume_raw["registry_source_sha256"] != revision
        or resume_raw["universes_published"] is not True
        or not isinstance(symbols, list)
        or len(symbols) != counts[1]
        or tuple(symbols) != tuple(sorted(set(symbols)))
        or any(
            not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or symbol in {"^RUT", "IWM"}
            or "russell" in symbol.casefold()
            for symbol in symbols
        )
        or set(symbols).intersection(resume_manifest.tickers)
        or resume_failure_digest != resume_manifest.sha256
        or not _same_stage10_failure_manifest(resume_manifest, completion_manifest)
    ):
        raise ConflictError("Stage 11 required Stage 10 resume state is inconsistent")
    journal_manifest = _read_stage10_failure_journal(
        layout,
        scope=scope,
        scope_manifest_sha256=scope_digest,
        registry_source_sha256=revision,
        expected=completion_manifest,
    )
    if not _same_stage10_failure_manifest(journal_manifest, completion_manifest):
        raise ConflictError("Stage 11 required Stage 10 failure journal is inconsistent")
    return _Stage10CompletionBinding(
        completed_symbols=tuple(symbols),
        roster_symbol_count=counts[0],
        failures=completion_manifest,
    )


def _require_completed_stage10_dependency(
    layout: _TargetLayout, scope: Stage11MacroScope
) -> Stage10CandidateReceipt:
    candidate = _dependency_receipt(layout, scope)
    _require_stage10_completion(layout, scope, candidate)
    return candidate


def _validate_current_stage10_dependency(
    layout: _TargetLayout,
    store_map: StoreMap,
    registry: Registry,
    project_root: Path,
    scope: Stage11MacroScope,
    candidate: Stage10CandidateReceipt,
) -> None:
    """Re-prove the completed Stage 10 cohort before any Stage 11 activity.

    Stage 10's private completion markers establish that one candidate was
    completed, but markers alone cannot prove that its four stores still match
    the immutable evidence.  This check deliberately projects the current
    canonical registry back to the frozen Stage 10 profile, reloads the one
    reviewed Stage 10 scope from the supplied project root, and uses only
    query-only inspection functions.  It runs before credentials are read and
    before ``initialize_all`` can apply Stage 11's additive macro migration.
    """

    # Trust the sealed Stage 10 completion markers and freshly bind the exact
    # four physical files; normal Stage 11 startup does not rescan market data.
    current_candidate = _dependency_receipt(layout, scope)
    _require_stage10_completion(layout, scope, current_candidate)
    if dumps_strict(current_candidate.to_primitive()) != dumps_strict(
        candidate.to_primitive()
    ):
        raise ConflictError("Stage 11 required Stage 10 dependency changed during validation")
    try:
        exact_store_map = _validate_target_store_map(
            _store_map_for(layout.stores), layout.stores
        )
        _validate_materialized_store_files(exact_store_map, layout.stores)
    except ConflictError:
        raise
    except (OSError, ValidationError, ValueError) as exc:
        raise ConflictError("Stage 11 required Stage 10 stores are unavailable") from exc
    if store_map != exact_store_map:
        raise ConflictError("Stage 11 Stage 10 store binding changed during validation")
    return




def _initial_state(scope: Stage11MacroScope, registry: Registry) -> Stage11ResumeState:
    return Stage11ResumeState(scope.target_profile_id, scope.manifest_sha256, registry.source_sha256, False, False, False)


def _state_from_primitive(raw: object, *, scope: Stage11MacroScope, registry: Registry) -> Stage11ResumeState:
    if not isinstance(raw, Mapping) or set(raw) != {
        "bea_published", "contract", "registry_source_sha256", "retail_published",
        "scope_manifest_sha256", "target_profile_id", "version", "weekly_published",
    }:
        raise ConflictError("Stage 11 private resume state is invalid")
    if (
        raw["contract"] != _RESUME_CONTRACT or raw["version"] != _RESUME_VERSION
        or raw["target_profile_id"] != scope.target_profile_id
        or raw["scope_manifest_sha256"] != scope.manifest_sha256
        or raw["registry_source_sha256"] != registry.source_sha256
        or any(not isinstance(raw[name], bool) for name in ("bea_published", "retail_published", "weekly_published"))
    ):
        raise ConflictError("Stage 11 private resume state does not match the reviewed target")
    return Stage11ResumeState(
        target_profile_id=raw["target_profile_id"],
        scope_manifest_sha256=raw["scope_manifest_sha256"],
        registry_source_sha256=raw["registry_source_sha256"],
        bea_published=raw["bea_published"], retail_published=raw["retail_published"], weekly_published=raw["weekly_published"],
    )


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("Stage 11 private state write failed")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.write"
    descriptor: int | None = None
    replaced = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, _PRIVATE_FILE_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        replaced = True
        os.chmod(path, _PRIVATE_FILE_MODE)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not replaced and (temporary.exists() or temporary.is_symlink()):
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_create(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.write"
    descriptor: int | None = None
    linked = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, _PRIVATE_FILE_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path)
        linked = True
        os.chmod(path, _PRIVATE_FILE_MODE)
        _fsync_directory(path.parent)
        temporary.unlink()
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise ConflictError("Stage 11 private receipt already exists") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if (temporary.exists() or temporary.is_symlink()) and not linked:
            try:
                temporary.unlink()
            except OSError:
                pass


def _create_stage11_layout(layout: _TargetLayout, *, scope: Stage11MacroScope, registry: Registry) -> None:
    """Create only the nested Stage 11 directory after dependency/key checks."""

    try:
        layout.stage11.mkdir(mode=_ROOT_MODE)
    except FileExistsError:
        _require_directory(layout.stage11, label="private Stage 11 state")
        return
    try:
        for child in (layout.backup, layout.restored, layout.candidate):
            if child.exists():
                raise ConflictError("Stage 11 new private state is unexpectedly populated")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(layout.run_lock, flags, _PRIVATE_FILE_MODE)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _atomic_replace(layout.resume, dumps_strict(_initial_state(scope, registry).to_primitive()).encode("utf-8"))
        _fsync_directory(layout.stage11)
    except BaseException:
        # The only safely recoverable case has no committed publication.  Keep
        # unknown material and remove only our exact empty/new files.
        try:
            if layout.resume.exists():
                layout.resume.unlink()
            if layout.run_lock.exists():
                layout.run_lock.unlink()
            layout.stage11.rmdir()
        except OSError:
            pass
        raise


def _validate_stage11_layout(layout: _TargetLayout) -> None:
    _require_directory(layout.stage11, label="private Stage 11 state")
    entries = {item.name for item in layout.stage11.iterdir()}
    allowed = {_RESUME_FILENAME, _RUN_LOCK_FILENAME, _BACKUP_DIRECTORY, _RESTORED_DIRECTORY, _CANDIDATE_STATE_DIRECTORY, _COMPLETION_FILENAME}
    if not {_RESUME_FILENAME, _RUN_LOCK_FILENAME}.issubset(entries) or not entries.issubset(allowed):
        raise ConflictError("Stage 11 private state contains unreviewed entries")
    _require_file(layout.resume, label="private resume state")
    _require_file(layout.run_lock, label="private run lock", maximum=0, empty=True)
    for path, label in ((layout.backup, "backup"), (layout.restored, "restored"), (layout.candidate, "candidate")):
        if path.exists():
            _require_directory(path, label=label)
    if layout.completion.exists():
        _require_file(layout.completion, label="completion receipt")


def _read_state(layout: _TargetLayout, *, scope: Stage11MacroScope, registry: Registry) -> Stage11ResumeState:
    _require_file(layout.resume, label="private resume state")
    try:
        raw = loads_strict(layout.resume.read_bytes(), max_bytes=_MAX_RESUME_BYTES)
    except (OSError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError("Stage 11 private resume state is unreadable") from exc
    return _state_from_primitive(raw, scope=scope, registry=registry)


def _write_state(layout: _TargetLayout, state: Stage11ResumeState) -> None:
    _atomic_replace(layout.resume, dumps_strict(state.to_primitive(), max_bytes=_MAX_RESUME_BYTES).encode("utf-8"))


def _expected_attempt_id(scope: Stage11MacroScope) -> str:
    return "stage11-bea-eia-macro-v1-" + scope.manifest_sha256[:16]


def _completion_from_primitive(raw: object, *, scope: Stage11MacroScope, registry: Registry) -> _CompletionReceipt:
    expected = {"candidate_receipt_sha256", "contract", "evidence_sha256", "registry_source_sha256", "scope_manifest_sha256", "sha256", "target_profile_id", "version"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ConflictError("Stage 11 private completion receipt is invalid")
    if (
        raw["contract"] != _COMPLETION_CONTRACT or raw["version"] != _COMPLETION_VERSION
        or raw["target_profile_id"] != scope.target_profile_id
        or raw["scope_manifest_sha256"] != scope.manifest_sha256
        or raw["registry_source_sha256"] != registry.source_sha256
    ):
        raise ConflictError("Stage 11 private completion receipt does not match the reviewed target")
    try:
        receipt = _CompletionReceipt(
            target_profile_id=raw["target_profile_id"], scope_manifest_sha256=_digest(raw["scope_manifest_sha256"], "scope pin"),
            registry_source_sha256=_digest(raw["registry_source_sha256"], "registry pin"), evidence_sha256=_digest(raw["evidence_sha256"], "evidence pin"),
            candidate_receipt_sha256=_digest(raw["candidate_receipt_sha256"], "candidate pin"), sha256=_digest(raw["sha256"], "completion pin"),
        )
    except (TypeError, ValidationError) as exc:
        raise ConflictError("Stage 11 private completion receipt is invalid") from exc
    if receipt.sha256 != hashlib.sha256(dumps_strict(receipt.material()).encode("utf-8")).hexdigest():
        raise ConflictError("Stage 11 private completion receipt is invalid")
    return receipt


def _read_stage11_candidate(layout: _TargetLayout, *, scope: Stage11MacroScope, registry: Registry) -> Stage11CandidateReceipt:
    _require_directory(layout.candidate, label="candidate state")
    directory = layout.candidate / _STAGE10_RECEIPTS_DIRECTORY
    _require_directory(directory, label="candidate receipt directory")
    expected_name = _expected_attempt_id(scope) + ".json"
    entries = {item.name: item for item in directory.iterdir()}
    if set(entries) != {expected_name}:
        raise ConflictError("Stage 11 private candidate receipt layout is invalid")
    path = entries[expected_name]
    _require_file(path, label="candidate receipt", maximum=_MAX_RECEIPT_BYTES)
    try:
        raw = loads_strict(path.read_bytes(), max_bytes=_MAX_RECEIPT_BYTES)
        if not isinstance(raw, Mapping) or set(raw) != {"attempt_id", "candidate_state", "code_revision", "contract", "contract_version", "evidence", "evidence_sha256", "generated_at", "receipt_sha256"}:
            raise ValidationError("candidate receipt shape")
        receipt = Stage11CandidateReceipt(
            attempt_id=raw["attempt_id"], generated_at=raw["generated_at"], code_revision=raw["code_revision"], candidate_state=raw["candidate_state"],
            evidence=raw["evidence"], evidence_sha256=raw["evidence_sha256"], receipt_sha256=raw["receipt_sha256"],
        )
    except (OSError, ResourceLimitError, TypeError, ValidationError) as exc:
        raise ConflictError("Stage 11 private candidate receipt is invalid") from exc
    try:
        evidence = Stage11CompletionEvidence.from_primitive(receipt.evidence)
    except (TypeError, ValidationError) as exc:
        raise ConflictError("Stage 11 private candidate evidence is invalid") from exc
    if (
        raw["contract"] != "quant_data.stage11_candidate_receipt" or raw["contract_version"] != "1.0.0"
        or receipt.attempt_id != _expected_attempt_id(scope) or receipt.code_revision != registry.source_sha256
        or evidence.sha256 != receipt.evidence_sha256
        or evidence.scope_manifest_sha256 != scope.manifest_sha256
        or evidence.target_profile_id != scope.target_profile_id
        or evidence.registry_source_sha256 != registry.source_sha256
    ):
        raise ConflictError("Stage 11 private candidate receipt does not match the reviewed target")
    return receipt


def _read_completed_receipt(layout: _TargetLayout, *, scope: Stage11MacroScope, registry: Registry) -> _CompletionReceipt | None:
    if not layout.stage11.exists():
        return None
    _validate_stage11_layout(layout)
    if not layout.completion.exists():
        return None
    try:
        raw = loads_strict(layout.completion.read_bytes(), max_bytes=_MAX_RESUME_BYTES)
    except (OSError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError("Stage 11 private completion receipt is unreadable") from exc
    receipt = _completion_from_primitive(raw, scope=scope, registry=registry)
    state = _read_state(layout, scope=scope, registry=registry)
    candidate = _read_stage11_candidate(layout, scope=scope, registry=registry)
    if not (state.bea_published and state.retail_published and state.weekly_published) or (
        receipt.evidence_sha256 != candidate.evidence_sha256 or receipt.candidate_receipt_sha256 != candidate.receipt_sha256
    ):
        raise ConflictError("Stage 11 private completion receipt is inconsistent")
    return receipt


def _validate_reused_store_evidence(
    layout: _TargetLayout,
    store_map: StoreMap,
    registry: Registry,
    scope: Stage11MacroScope,
) -> None:
    """Re-prove the completed candidate before a zero-network reuse result.

    A private completion marker is only a cache hint.  It cannot by itself
    authorize a success result after the underlying stores have been replaced,
    changed, or become inconsistent with the immutable candidate receipt.
    The reconciliation helper uses only query-only connections; fingerprints on
    both sides make that no-mutation property executable at this boundary.
    """

    lean_candidate = _read_stage11_candidate(layout, scope=scope, registry=registry)
    try:
        expected = Stage11CompletionEvidence.from_primitive(lean_candidate.evidence)
        current = build_stage11_completion_evidence(store_map, registry, scope)
    except (TypeError, ValidationError, ValueError) as exc:
        raise ConflictError("Stage 11 completed stores failed targeted integrity checks") from exc
    if (
        dumps_strict(current.to_primitive())
        != dumps_strict(expected.to_primitive())
    ):
        raise ConflictError("Stage 11 completed stores no longer match candidate evidence")
    return




@dataclass(slots=True)
class _TargetRunLock:
    descriptor: int

    def close(self) -> None:
        descriptor, self.descriptor = self.descriptor, -1
        if descriptor < 0:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "_TargetRunLock":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()


def _acquire_run_lock(layout: _TargetLayout) -> _TargetRunLock:
    _require_file(layout.run_lock, label="private run lock", maximum=0, empty=True)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags)
    try:
        before = layout.run_lock.lstat()
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) != _PRIVATE_FILE_MODE or opened.st_size != 0 or before.st_ino != opened.st_ino or before.st_dev != opened.st_dev:
            raise ConflictError("Stage 11 private run lock is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Stage 11 target is already running") from exc
        if layout.run_lock.lstat().st_ino != opened.st_ino:
            raise ConflictError("Stage 11 private run lock changed during acquisition")
        return _TargetRunLock(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _outcome(value: object) -> str:
    outcome = value.get("outcome") if isinstance(value, Mapping) else getattr(value, "outcome", None)
    if not isinstance(outcome, str):
        raise ValidationError("Stage 11 publication receipt is invalid")
    return outcome


def _default_bindings() -> Stage11BackfillBindings:
    def capture_bea(prepared: object, key: str, transport: object) -> object:
        return capture_bea_nipa(prepared, api_key=key, transport=transport)  # type: ignore[arg-type]

    def capture_retail(prepared: object, key: str, transport: object) -> object:
        return capture_eia_retail_page(prepared, api_key=key, transport=transport)  # type: ignore[arg-type]

    def capture_weekly(prepared: object, key: str, transport: object) -> object:
        return capture_eia_weekly(prepared, api_key=key, transport=transport)  # type: ignore[arg-type]

    def importer_factory(store_map: StoreMap, registry: Registry) -> Stage11MacroImporter:
        return Stage11MacroImporter(store_map, registry)

    def prepare_bea(importer: object, capture: object) -> object:
        if not isinstance(importer, Stage11MacroImporter) or not isinstance(capture, BeaNipaHistoryCohort):
            raise ValidationError("Stage 11 importer binding is invalid")
        return importer.prepare_bea_nipa_history(capture)

    def prepare_retail(importer: object, capture: object) -> object:
        if not isinstance(importer, Stage11MacroImporter) or not isinstance(capture, EiaRetailCapture):
            raise ValidationError("Stage 11 importer binding is invalid")
        return importer.prepare_eia_retail_history(capture)

    def prepare_weekly(importer: object, capture: object) -> object:
        if not isinstance(importer, Stage11MacroImporter) or not isinstance(capture, EiaWeeklyCapture):
            raise ValidationError("Stage 11 importer binding is invalid")
        return importer.prepare_eia_weekly_history(capture)

    def publish(importer: object, candidate: object) -> object:
        if not isinstance(importer, Stage11MacroImporter):
            raise ValidationError("Stage 11 importer binding is invalid")
        return importer.publish_prepared(candidate)  # type: ignore[arg-type]

    return Stage11BackfillBindings(
        bea_transport_factory=lambda _key, _map, _scope: StdlibBeaTransport(),
        eia_transport_factory=lambda _key, _map, _scope: StdlibEiaTransport(),
        capture_bea=capture_bea, assemble_bea=assemble_bea_nipa_history,
        capture_retail_page=capture_retail, assemble_retail=assemble_eia_retail_capture,
        capture_weekly=capture_weekly, importer_factory=importer_factory,
        prepare_bea=prepare_bea, prepare_retail=prepare_retail, prepare_weekly=prepare_weekly,
        publish=publish,
    )


def _resolve_bindings(value: Stage11BackfillBindings | None) -> Stage11BackfillBindings:
    bindings = _default_bindings() if value is None else value
    names = (
        "bea_transport_factory", "eia_transport_factory", "capture_bea", "assemble_bea",
        "capture_retail_page", "assemble_retail", "capture_weekly", "importer_factory",
        "prepare_bea", "prepare_retail", "prepare_weekly", "publish",
    )
    if not isinstance(bindings, Stage11BackfillBindings) or any(not callable(getattr(bindings, name)) for name in names):
        raise _ConfigurationFailure
    return bindings


def _monotonic_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValidationError("Stage 11 monotonic clock is invalid")
    return float(value)


class _RequestPacer:
    def __init__(self, *, interval_seconds: float, monotonic: Callable[[], object], sleeper: Callable[[float], None], force_initial_delay: bool) -> None:
        if interval_seconds < 1.0 or not callable(monotonic) or not callable(sleeper):
            raise _ConfigurationFailure
        self._interval = interval_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._force_initial_delay = force_initial_delay
        self._last: float | None = None

    def before_request(self) -> None:
        if self._last is None:
            if self._force_initial_delay:
                self._sleeper(self._interval)
            self._last = _monotonic_seconds(self._monotonic())
            return
        for _ in range(4):
            now = _monotonic_seconds(self._monotonic())
            remaining = self._interval - (now - self._last)
            if remaining <= 0:
                self._last = now
                return
            self._sleeper(remaining)
        now = _monotonic_seconds(self._monotonic())
        if now - self._last < self._interval:
            raise ValidationError("Stage 11 request pacing did not advance")
        self._last = now


def _capture_with_retry(
    capture: Callable[[object, str, object], object],
    prepared: object,
    api_key: str,
    transport: object,
    *,
    pacer: _RequestPacer,
    sleeper: Callable[[float], None],
) -> tuple[object, int]:
    """Capture one exact logical request with bounded transient retries."""

    attempts = 0
    while attempts < STAGE11_MAX_REQUEST_ATTEMPTS:
        pacer.before_request()
        attempts += 1
        try:
            return capture(prepared, api_key, transport), attempts
        except Stage11TransientRequestError:
            if attempts >= STAGE11_MAX_REQUEST_ATTEMPTS:
                raise
            sleeper(STAGE11_RETRY_BACKOFF_SECONDS[attempts - 1])
    raise ValidationError("Stage 11 retry attempt accounting is invalid")


def _write_completion(layout: _TargetLayout, context: Stage11CompletionContext, candidate: Stage11CandidateReceipt) -> _CompletionReceipt:
    material = {
        "target_profile_id": context.scope.target_profile_id,
        "scope_manifest_sha256": context.scope.manifest_sha256,
        "registry_source_sha256": context.registry.source_sha256,
        "evidence_sha256": candidate.evidence_sha256,
        "candidate_receipt_sha256": candidate.receipt_sha256,
    }
    receipt = _CompletionReceipt(
        **material,
        sha256=hashlib.sha256(dumps_strict({"contract": _COMPLETION_CONTRACT, "version": _COMPLETION_VERSION, **material}).encode("utf-8")).hexdigest(),
    )
    _atomic_create(layout.completion, dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RESUME_BYTES).encode("utf-8"))
    return receipt


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_default_completion(
    context: Stage11CompletionContext,
    layout: _TargetLayout,
    *,
    now: Callable[[], object],
    evidence_builder: Callable[..., object] = build_stage11_completion_evidence,
    candidate_state_factory: Callable[[Path], object] = PrivateStage11CompletionCandidateState,
) -> _CompletionReceipt:
    if not isinstance(context, Stage11CompletionContext):
        raise ValidationError("Stage 11 completion context is invalid")
    if any(path.exists() for path in (layout.backup, layout.restored, layout.candidate)):
        raise ConflictError("Stage 11 private completion material already exists without a receipt")
    evidence = evidence_builder(context.store_map, context.registry, context.scope)
    if not isinstance(evidence, Stage11CompletionEvidence):
        raise ValidationError("Stage 11 completion evidence is invalid")
    state = candidate_state_factory(layout.candidate)
    if not isinstance(state, PrivateStage11CompletionCandidateState):
        raise ValidationError("Stage 11 candidate state binding is invalid")
    candidate = state.publish(evidence, context.registry.source_sha256, now, _expected_attempt_id(context.scope))
    if not isinstance(candidate, Stage11CandidateReceipt):
        raise ValidationError("Stage 11 candidate receipt is invalid")
    return _write_completion(layout, context, candidate)



def _success_payload(*, scope: Stage11MacroScope, registry: Registry, resumed: bool, bea_requests: int, retail_requests: int, weekly_requests: int, completion: str) -> dict[str, object]:
    return {
        "bea_request_count": bea_requests,
        "candidate_state": "private_candidate_only_no_operational_promotion",
        "completion": completion,
        "contract": _RESULT_CONTRACT,
        "contract_version": _CONTRACT_VERSION,
        "eia_retail_request_count": retail_requests,
        "eia_weekly_request_count": weekly_requests,
        "provider": "bea_eia",
        "registry_source_sha256": _digest(registry.source_sha256, "registry source pin"),
        "resumed": resumed,
        "scope_manifest_sha256": _digest(scope.manifest_sha256, "scope manifest pin"),
        "target_profile_id": scope.target_profile_id,
    }


def _write_success(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(dumps_strict(dict(payload)))
    stream.write("\n")
    stream.flush()


def _write_error(stream: TextIO, code: str, exit_code: int) -> None:
    stream.write(dumps_strict({"contract": _ERROR_CONTRACT, "contract_version": _CONTRACT_VERSION, "error": code, "exit_code": exit_code}))
    stream.write("\n")
    stream.flush()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    registry_loader: Callable[..., Registry] = load_registry,
    registry_validator: Callable[[Registry], None] = _registry_preflight,
    scope_loader: Callable[[str | Path], Stage11MacroScope] = load_stage11_macro_scope,
    scope_validator: Callable[[Stage11MacroScope], None] = _scope_preflight,
    store_map_factory: Callable[[Path], StoreMap] = _store_map_for,
    store_file_validator: Callable[[StoreMap, Path], None] = _validate_materialized_store_files,
    stage10_dependency_validator: Callable[
        [_TargetLayout, StoreMap, Registry, Path, Stage11MacroScope, Stage10CandidateReceipt],
        None,
    ] = _validate_current_stage10_dependency,
    completed_store_validator: Callable[
        [_TargetLayout, StoreMap, Registry, Stage11MacroScope], None
    ] = _validate_reused_store_evidence,
    initializer: Callable[[StoreMap, Registry], object] = initialize_all,
    bindings: Stage11BackfillBindings | None = None,
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    completion_callback: Callable[[Stage11CompletionContext], None] | None = None,
    now: Callable[[], object] = _default_now,
    approved_project_root: str | Path = APPROVED_PROJECT_ROOT,
    approved_target_root: str | Path = APPROVED_TARGET_ROOT,
) -> int:
    """Run the exact manual Stage 11 candidate-only population once.

    Dependency and completed-receipt reads happen before both credential access
    and any Stage 11 directory creation.  Capture calls are sequential; a
    complete provider cohort is parsed before its one semantic publication.
    """

    output, errors = stdout or sys.stdout, stderr or sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        project_root = _preflight_project_root(arguments.project_root, approved_project_root)
        target_root = _preflight_target_root(arguments.target_root, approved_target_root)
        if (
            not callable(store_file_validator)
            or not callable(stage10_dependency_validator)
            or not callable(completed_store_validator)
            or (completion_callback is not None and not callable(completion_callback))
        ):
            raise _ConfigurationFailure
        registry = registry_loader(
            project_root / CANONICAL_REGISTRY_PATH,
            project_root=project_root,
            environment={},
        )
        if (
            isinstance(registry, Registry)
            and registry.schema_version == "1.7.0"
            and registry.registry_version == "2.10.0"
        ):
            registry = stage11_registry_profile(registry)
        registry_validator(registry)
        scope = scope_loader(project_root / _SCOPE_RELATIVE_PATH)
        scope_validator(scope)
        layout = _existing_target_layout(target_root)
        stage10_candidate = _require_completed_stage10_dependency(layout, scope)
        # Stage 10 has already materialized this fixed four-store cohort.  A
        # missing, linked, aliased, or replaced store is a local conflict, not
        # a reason to read credentials or contact a provider.
        store_map = _validate_target_store_map(
            store_map_factory(layout.stores), layout.stores
        )
        store_file_validator(store_map, layout.stores)
        # A signed Stage 10 marker is not sufficient by itself.  Reconcile the
        # exact current four-store cohort against the frozen Stage 10 profile
        # before reading BEA/EIA credentials or applying the Stage 11 macro
        # migration.  This binding is query-only and fail-closed.
        stage10_dependency_validator(
            layout,
            store_map,
            registry,
            project_root,
            scope,
            stage10_candidate,
        )
        completed = _read_completed_receipt(layout, scope=scope, registry=registry)
        if completed is not None:
            # Match the ordinary-run locking discipline before treating a
            # completion marker as reusable.  Re-read both marker and stores
            # under the target lock rather than trusting the pre-lock view.
            with _acquire_run_lock(layout):
                completed = _read_completed_receipt(
                    layout, scope=scope, registry=registry
                )
                if completed is None:
                    raise ConflictError(
                        "Stage 11 private completion receipt changed during reuse"
                    )
                store_map = _validate_target_store_map(
                    store_map_factory(layout.stores), layout.stores
                )
                store_file_validator(store_map, layout.stores)
                completed_store_validator(layout, store_map, registry, scope)
            _write_success(output, _success_payload(scope=scope, registry=registry, resumed=True, bea_requests=0, retail_requests=0, weekly_requests=0, completion="reused"))
            return 0

        # Only after the completed Stage 10 receipt and possible Stage 11 reuse
        # gate do we touch caller-owned process credentials or create state.
        environment_map = environment if environment is not None else os.environ
        bea_key = _read_api_key(environment_map, scope.bea.configuration_env)
        eia_key = _read_api_key(environment_map, scope.eia.configuration_env)
        if not layout.stage11.exists():
            _create_stage11_layout(layout, scope=scope, registry=registry)
            resumed = False
        else:
            _validate_stage11_layout(layout)
            resumed = True
        with _acquire_run_lock(layout):
            state = _read_state(layout, scope=scope, registry=registry)
            completed = _read_completed_receipt(layout, scope=scope, registry=registry)
            if completed is not None:
                # Rebuild and re-check while holding the target lock so an
                # in-flight replacement cannot slip between the early gate and
                # a successful completed-run response.
                store_map = _validate_target_store_map(
                    store_map_factory(layout.stores), layout.stores
                )
                store_file_validator(store_map, layout.stores)
                completed_store_validator(layout, store_map, registry, scope)
                _write_success(output, _success_payload(scope=scope, registry=registry, resumed=True, bea_requests=0, retail_requests=0, weekly_requests=0, completion="reused"))
                return 0
            store_map = _validate_target_store_map(store_map_factory(layout.stores), layout.stores)
            initializer(store_map, registry)
            # ``initialize_all`` is the final permitted store-touching step
            # before transport creation.  Re-assert fixed physical identity
            # immediately afterwards and before any provider call.
            store_file_validator(store_map, layout.stores)
            resolved = _resolve_bindings(bindings)
            importer = resolved.importer_factory(store_map, registry)
            if importer is None:
                raise ValidationError("Stage 11 importer binding is invalid")
            bea_transport = resolved.bea_transport_factory(bea_key, store_map, scope)
            eia_transport = resolved.eia_transport_factory(eia_key, store_map, scope)
            if bea_transport is None or eia_transport is None:
                raise ValidationError("Stage 11 transport binding is invalid")
            pacer = _RequestPacer(interval_seconds=scope.bounds.minimum_request_interval_milliseconds / 1000.0, monotonic=monotonic, sleeper=sleeper, force_initial_delay=resumed)
            bea_requests = retail_requests = weekly_requests = 0

            if not state.bea_published:
                captures: list[object] = []
                for prepared in prepare_bea_nipa_captures():
                    capture, attempts = _capture_with_retry(
                        resolved.capture_bea,
                        prepared,
                        bea_key,
                        bea_transport,
                        pacer=pacer,
                        sleeper=sleeper,
                    )
                    captures.append(capture)
                    bea_requests += attempts
                cohort = resolved.assemble_bea(tuple(captures))
                if _outcome(resolved.publish(importer, resolved.prepare_bea(importer, cohort))) not in {"succeeded", "unchanged"}:
                    raise ValidationError("Stage 11 BEA publication did not commit")
                state = replace(state, bea_published=True)
                _write_state(layout, state)

            if not state.retail_published:
                first_prepared = prepare_eia_retail_page(0)
                first, attempts = _capture_with_retry(
                    resolved.capture_retail_page,
                    first_prepared,
                    eia_key,
                    eia_transport,
                    pacer=pacer,
                    sleeper=sleeper,
                )
                retail_requests += attempts
                total = getattr(first, "total", None)
                if isinstance(total, bool) or not isinstance(total, int) or total < 1:
                    raise ValidationError("Stage 11 EIA retail first page is invalid")
                page_count = (total + EIA_RETAIL_PAGE_LENGTH - 1) // EIA_RETAIL_PAGE_LENGTH
                if page_count > scope.bounds.eia_max_pages:
                    raise ValidationError("Stage 11 EIA retail pagination exceeds the reviewed bound")
                pages: list[object] = [first]
                for number in range(1, page_count):
                    page, attempts = _capture_with_retry(
                        resolved.capture_retail_page,
                        prepare_eia_retail_page(number * EIA_RETAIL_PAGE_LENGTH),
                        eia_key,
                        eia_transport,
                        pacer=pacer,
                        sleeper=sleeper,
                    )
                    pages.append(page)
                    retail_requests += attempts
                cohort = resolved.assemble_retail(tuple(pages))
                if _outcome(resolved.publish(importer, resolved.prepare_retail(importer, cohort))) not in {"succeeded", "unchanged"}:
                    raise ValidationError("Stage 11 EIA retail publication did not commit")
                state = replace(state, retail_published=True)
                _write_state(layout, state)

            if not state.weekly_published:
                capture, attempts = _capture_with_retry(
                    resolved.capture_weekly,
                    prepare_eia_weekly_capture(),
                    eia_key,
                    eia_transport,
                    pacer=pacer,
                    sleeper=sleeper,
                )
                weekly_requests += attempts
                if _outcome(resolved.publish(importer, resolved.prepare_weekly(importer, capture))) not in {"succeeded", "unchanged"}:
                    raise ValidationError("Stage 11 EIA weekly publication did not commit")
                state = replace(state, weekly_published=True)
                _write_state(layout, state)

            context = Stage11CompletionContext(store_map=store_map, registry=registry, scope=scope, private_root=layout.stage11, resumed=resumed)
            if completion_callback is None:
                _run_default_completion(context, layout, now=now)
                completion = "completed"
            else:
                if completion_callback(context) is not None:
                    raise ValidationError("Stage 11 completion callback must not return public data")
                completion = "injected"
            _write_success(output, _success_payload(scope=scope, registry=registry, resumed=resumed, bea_requests=bea_requests, retail_requests=retail_requests, weekly_requests=weekly_requests, completion=completion))
            return 0
    except _ArgumentFailure:
        exit_code, code = 64, "invalid_arguments"
    except _ConfigurationFailure:
        exit_code, code = 78, "invalid_configuration"
    except StoreUnavailableError:
        exit_code, code = 69, "unavailable"
    except OSError:
        exit_code, code = 74, "local_io"
    except ConflictError:
        exit_code, code = 75, "conflict"
    except RegistryError:
        exit_code, code = 78, "invalid_configuration"
    except (ValidationError, MigrationError, ResourceLimitError):
        exit_code, code = 70, "contract_failure"
    except Exception:
        exit_code, code = 70, "internal_failure"
    _write_error(errors, code, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "APPROVED_PROJECT_ROOT",
    "APPROVED_TARGET_ROOT",
    "Stage11BackfillBindings",
    "Stage11CompletionContext",
    "Stage11ResumeState",
    "main",
)
