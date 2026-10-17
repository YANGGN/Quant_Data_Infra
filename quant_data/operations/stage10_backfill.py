"""Restricted, resumable Stage 10 FMP market-history backfill.

This is deliberately a single reviewed workflow rather than a generic data
loader.  It accepts only the approved project and nonproduction target roots,
uses ``FMP_API_KEY`` only from the supplied process environment, and keeps its
resume state private to that target.  Provider capture happens before every
database publication, one request at a time, so a database lock is never held
while a request is in flight.

The module does not promote an operational store, schedule itself, expose a
tool/dashboard/Atlas surface, or read ``.env`` files.  After all scoped
symbols commit, its default completion path writes only private backup,
restore, reconciliation, and candidate-receipt evidence.
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
from typing import Any, TextIO

from ..errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict, loads_strict
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..market.fmp_bulk_daily_prices import (
    FMP_STAGE10_MAX_PRICE_BYTES,
    FmpPriceHistoryUnavailable,
    FmpStage10PriceCapture,
    FmpStage10Transport,
    FmpStage10UniverseCapture,
    StdlibFmpStage10Transport,
    capture_fmp_stage10_price,
    capture_fmp_stage10_universe,
    prepare_fmp_stage10_price_capture,
    prepare_fmp_stage10_universe_capture,
)
from ..market.stage10_history_importer import (
    STAGE10_DAILY_HISTORY_COLLECTOR_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_RESOURCE,
    STAGE10_MIGRATION_SHA256,
    STAGE10_UNIVERSE_COLLECTOR_ID,
    STAGE10_UNIVERSES_DATASET_ID,
    Stage10HistoryImporter,
)
from ..market.stage10_scope import (
    STAGE10_TARGET_PROFILE_ID,
    Stage10MarketScope,
    UniverseSource,
    load_stage10_market_scope,
)
from ..migrations import initialize_all
from .backup import backup_all, restore_all
from .stage10_repopulation import (
    PrivateStage10CandidateState,
    Stage10CandidateReceipt,
    build_stage10_repopulation_evidence,
    normalize_stage10_failed_records,
    reconcile_stage10_market,
    stage10_failure_manifest_sha256,
)
from .health import all_store_health
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry, stage10_registry_profile
from ..stores import StoreMap, StoreRole, read_connection


APPROVED_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_TARGET_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
)
_SCOPE_RELATIVE_PATH = Path("config/stage10_market_scope.json")
_RESUME_FILENAME = "resume.json"
_RUN_LOCK_FILENAME = "run.lock"
_BACKUP_DIRECTORY = "backup"
_RESTORED_DIRECTORY = "restored"
_CANDIDATE_DIRECTORY = "candidate"
_COMPLETION_FILENAME = "completion.json"
_FAILURES_DIRECTORY = "failures"
_PRIVATE_DIRECTORY = "private"
_STORES_DIRECTORY = "stores"
_RESUME_CONTRACT = "quant_data.stage10_backfill_resume"
_RESUME_VERSION = "1.1.0"
_LEGACY_RESUME_VERSION = "1.0.0"
_COMPLETION_CONTRACT = "quant_data.stage10_backfill_completion"
_COMPLETION_VERSION = "1.1.0"
_FAILURE_CONTRACT = "quant_data.stage10_backfill_failure"
_FAILURE_VERSION = "1.0.0"
_RESULT_CONTRACT = "quant_data.stage10_backfill_result"
_ERROR_CONTRACT = "quant_data.stage10_backfill_error"
_CONTRACT_VERSION = "1.1.0"
_ERROR_CONTRACT_VERSION = "1.0.0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9.^-]{1,32}\Z")
_UNIVERSE_IDS = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
    "curated_etfs",
    "major_indexes",
)
_ASSET_TYPES = frozenset({"equity", "etf", "index"})
_ROOT_MODE = 0o700
_RESUME_MODE = 0o600
_MAX_RESUME_BYTES = 256 * 1024
_MAX_CANDIDATE_RECEIPT_BYTES = 512 * 1024
_MAX_FAILURE_RECEIPT_BYTES = 16 * 1024
_LEGACY_EUV_COMPLETED_COUNT = 200
_LEGACY_EUV_SYMBOL = "EUV"
_FAILURE_REASONS = frozenset(
    {"fmp_price_history_unavailable", "operator_authorized_prior_failure"}
)
_FAILURE_PROVENANCES = frozenset(
    {"fmp_http_200_application_json", "stage10_operator_authorized_seed"}
)


class _ArgumentFailure(Exception):
    """CLI-only rejection with no rendered argument detail."""


class _ConfigurationFailure(Exception):
    """Safe preflight failure for credentials or reviewed declarations."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class _TargetLayout:
    root: Path
    stores: Path
    private: Path
    resume: Path
    run_lock: Path
    backup: Path
    restored: Path
    candidate: Path
    completion: Path
    failures: Path


@dataclass(frozen=True, slots=True)
class Stage10FailureRecord:
    """One canonical, receipt-safe skipped-symbol declaration."""

    symbol: str
    reason: str
    provenance: str
    response_metadata: Mapping[str, object] | None = None
    response_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or self.symbol in {"^RUT", "IWM"}
            or "russell" in self.symbol.casefold()
            or self.reason not in _FAILURE_REASONS
            or self.provenance not in _FAILURE_PROVENANCES
        ):
            raise ValidationError("Stage 10 failure record is invalid")

        metadata = self.response_metadata
        response_sha256 = self.response_sha256
        if self.reason == "fmp_price_history_unavailable":
            if (
                self.provenance != "fmp_http_200_application_json"
                or not isinstance(metadata, Mapping)
                or response_sha256 is None
            ):
                raise ValidationError("Stage 10 failure record is invalid")
            if set(metadata) != {"body_byte_count", "content_type", "status"}:
                raise ValidationError("Stage 10 failure record is invalid")
            byte_count = metadata["body_byte_count"]
            if (
                isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or byte_count < 0
                or byte_count > FMP_STAGE10_MAX_PRICE_BYTES
                or metadata["content_type"] != "application/json"
                or metadata["status"] != 200
            ):
                raise ValidationError("Stage 10 failure record is invalid")
            _sha256(response_sha256, "failure response digest")
        elif (
            self.reason != "operator_authorized_prior_failure"
            or self.provenance != "stage10_operator_authorized_seed"
            or metadata is not None
            or response_sha256 is not None
        ):
            raise ValidationError("Stage 10 failure record is invalid")

    def to_primitive(self) -> dict[str, object]:
        result: dict[str, object] = {
            "provenance": self.provenance,
            "reason": self.reason,
            "symbol": self.symbol,
        }
        if self.response_metadata is not None:
            # Copy into a closed primitive so a mutable injected mapping cannot
            # alter the durable record after validation.
            result["response_metadata"] = {
                "body_byte_count": self.response_metadata["body_byte_count"],
                "content_type": self.response_metadata["content_type"],
                "status": self.response_metadata["status"],
            }
        if self.response_sha256 is not None:
            result["response_sha256"] = self.response_sha256
        return result

    @classmethod
    def from_primitive(cls, value: object) -> "Stage10FailureRecord":
        if not isinstance(value, Mapping):
            raise ConflictError("Stage 10 failure record is invalid")
        keys = set(value)
        required = {"symbol", "reason", "provenance"}
        optional = {"response_metadata", "response_sha256"}
        if not required.issubset(keys) or not keys.issubset(required | optional):
            raise ConflictError("Stage 10 failure record is invalid")
        try:
            return cls(
                symbol=value["symbol"],
                reason=value["reason"],
                provenance=value["provenance"],
                response_metadata=value.get("response_metadata"),
                response_sha256=value.get("response_sha256"),
            )
        except (TypeError, ValidationError) as exc:
            raise ConflictError("Stage 10 failure record is invalid") from exc

    @classmethod
    def from_unavailable(
        cls,
        value: FmpPriceHistoryUnavailable,
    ) -> "Stage10FailureRecord":
        if not isinstance(value, FmpPriceHistoryUnavailable):
            raise ValidationError("Stage 10 unavailable-history binding is invalid")
        return cls(
            symbol=value.symbol,
            reason="fmp_price_history_unavailable",
            provenance="fmp_http_200_application_json",
            response_metadata={
                "body_byte_count": value.body_byte_count,
                "content_type": value.content_type,
                "status": value.status,
            },
            response_sha256=value.body_sha256,
        )


def _failure_manifest_sha256(records: Sequence[Stage10FailureRecord]) -> str:
    return stage10_failure_manifest_sha256(
        tuple(record.to_primitive() for record in records)
    )


def _canonical_failure_records(
    records: object,
) -> tuple[Stage10FailureRecord, ...]:
    if not isinstance(records, (list, tuple)):
        raise ConflictError("Stage 10 failure records are invalid")
    try:
        primitives = tuple(
            item.to_primitive() if isinstance(item, Stage10FailureRecord) else item
            for item in records
        )
        normalized = normalize_stage10_failed_records(primitives)
        converted = tuple(Stage10FailureRecord.from_primitive(item) for item in normalized)
    except (TypeError, ValidationError, ConflictError) as exc:
        raise ConflictError("Stage 10 failure records are invalid")
    return converted


@dataclass(frozen=True, slots=True)
class Stage10ResumeState:
    """Private, credential-free progress checkpoint for the fixed target."""

    target_profile_id: str
    scope_manifest_sha256: str
    registry_source_sha256: str
    universes_published: bool
    completed_symbols: tuple[str, ...]
    failed_records: tuple[Stage10FailureRecord, ...] = ()
    failure_manifest_sha256: str = ""
    legacy: bool = False

    def to_primitive(self) -> dict[str, object]:
        if self.legacy:
            raise ValidationError("Stage 10 legacy resume state cannot be written")
        return {
            "completed_symbols": list(self.completed_symbols),
            "contract": _RESUME_CONTRACT,
            "failed_records": [item.to_primitive() for item in self.failed_records],
            "failure_manifest_sha256": self.failure_manifest_sha256,
            "registry_source_sha256": self.registry_source_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "universes_published": self.universes_published,
            "version": _RESUME_VERSION,
        }


@dataclass(frozen=True, slots=True)
class Stage10CompletionContext:
    """Narrow post-commit seam for a future private reconciliation receipt."""

    store_map: StoreMap
    registry: Registry
    scope: Stage10MarketScope
    private_root: Path
    completed_symbols: tuple[str, ...]
    roster_symbol_count: int
    resumed: bool
    failed_records: tuple[Stage10FailureRecord, ...] = ()
    failure_manifest_sha256: str = ""


@dataclass(frozen=True, slots=True)
class _CompletionReceipt:
    """Private, receipt-last marker used solely to reuse a finished run."""

    target_profile_id: str
    scope_manifest_sha256: str
    registry_source_sha256: str
    roster_symbol_count: int
    completed_symbol_count: int
    evidence_sha256: str
    candidate_receipt_sha256: str
    sha256: str
    failed_records: tuple[Stage10FailureRecord, ...] = ()
    failure_manifest_sha256: str = ""

    def material(self) -> dict[str, object]:
        return {
            "contract": _COMPLETION_CONTRACT,
            "version": _COMPLETION_VERSION,
            "target_profile_id": self.target_profile_id,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "registry_source_sha256": self.registry_source_sha256,
            "roster_symbol_count": self.roster_symbol_count,
            "completed_symbol_count": self.completed_symbol_count,
            "failed_records": [item.to_primitive() for item in self.failed_records],
            "failed_tickers": [item.symbol for item in self.failed_records],
            "failed_symbol_count": len(self.failed_records),
            "failure_manifest_sha256": self.failure_manifest_sha256,
            "evidence_sha256": self.evidence_sha256,
            "candidate_receipt_sha256": self.candidate_receipt_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class Stage10BackfillBindings:
    """All provider and store-touching dependencies for offline injection."""

    transport_factory: Callable[[str, StoreMap, Stage10MarketScope], object]
    capture_universe: Callable[[UniverseSource, str, object], object]
    capture_price: Callable[[str, str, object], object]
    importer_factory: Callable[[StoreMap, Registry, Stage10MarketScope], object]
    prepare_universes: Callable[[object, Mapping[str, object]], Sequence[object]]
    prepare_price: Callable[[object, object], object]
    publish: Callable[[object, object], object]
    roster_reader: Callable[[StoreMap, Stage10MarketScope], tuple[str, ...]]
    assert_no_partial_universe_publication: Callable[[StoreMap, Stage10MarketScope], None]


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python3 -m quant_data.operations.stage10_backfill",
        add_help=False,
        description="Run only the approved nonproduction Stage 10 FMP backfill",
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
    parent = root.parent
    try:
        parent_info = parent.lstat()
    except FileNotFoundError as exc:
        raise _ConfigurationFailure from exc
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise _ConfigurationFailure
    return root


def _read_api_key(environment: Mapping[str, str]) -> str:
    value = environment.get("FMP_API_KEY")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise _ConfigurationFailure
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise _ConfigurationFailure
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 {field_name} is invalid")
    return value


def _registry_preflight(registry: Registry) -> None:
    """Reject registry drift before target creation or provider activity."""

    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.6.0"
        or registry.registry_version != "2.8.0"
        or registry.status != "validated"
    ):
        raise _ConfigurationFailure
    migrations = [item for item in registry.migrations if item.id == STAGE10_MIGRATION_ID]
    collectors = {str(item.get("id")): item for item in registry.collectors}
    datasets = {item.id for item in registry.datasets}
    if len(migrations) != 1 or not {
        STAGE10_EVIDENCE_DATASET_ID,
        STAGE10_INSTRUMENTS_DATASET_ID,
        STAGE10_UNIVERSES_DATASET_ID,
        STAGE10_DAILY_PRICES_DATASET_ID,
    }.issubset(datasets):
        raise _ConfigurationFailure
    migration = migrations[0]
    if (
        migration.store != StoreRole.MARKET.value
        or migration.ordinal != 10
        or migration.resource != STAGE10_MIGRATION_RESOURCE
        or migration.sha256 != STAGE10_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
    ):
        raise _ConfigurationFailure
    expected_collectors = (
        (
            STAGE10_UNIVERSE_COLLECTOR_ID,
            "market.stage10_fmp_universes",
            (),
            (
                STAGE10_EVIDENCE_DATASET_ID,
                STAGE10_INSTRUMENTS_DATASET_ID,
                STAGE10_UNIVERSES_DATASET_ID,
            ),
        ),
        (
            STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            "market.stage10_fmp_daily_history",
            (STAGE10_INSTRUMENTS_DATASET_ID, STAGE10_UNIVERSES_DATASET_ID),
            (STAGE10_EVIDENCE_DATASET_ID, STAGE10_DAILY_PRICES_DATASET_ID),
        ),
    )
    for collector_id, handler, inputs, outputs in expected_collectors:
        collector = collectors.get(collector_id)
        if (
            collector is None
            or collector.get("handler") != handler
            or collector.get("network") is not True
            or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
            or tuple(collector.get("input_datasets", ())) != inputs
            or tuple(collector.get("output_datasets", ())) != outputs
            or collector.get("schedule_eligibility") != {"mode": "manual_only"}
        ):
            raise _ConfigurationFailure
    _sha256(registry.source_sha256, "registry source pin")


def _scope_preflight(scope: Stage10MarketScope) -> None:
    if not isinstance(scope, Stage10MarketScope):
        raise _ConfigurationFailure
    if (
        scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or scope.provider != "fmp"
        or scope.price_history.interval != "daily"
        or scope.price_history.history_policy
        != "earliest_available_per_provider_symbol"
        or scope.bounds.minimum_request_interval_milliseconds < 1000
        or tuple(item.id for item in scope.universe_sources)
        != _UNIVERSE_IDS[:3]
    ):
        raise _ConfigurationFailure
    _sha256(scope.manifest_sha256, "scope manifest pin")


def _store_map_for(stores_root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=stores_root / "market.sqlite",
        macro=stores_root / "macro.sqlite",
        company=stores_root / "company.sqlite",
        news=stores_root / "news.sqlite",
    )


def _validate_target_store_map(store_map: object, stores_root: Path) -> StoreMap:
    """Accept only the four fixed target-relative operational store paths."""

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 10 store binding is invalid")
    expected = {
        StoreRole.MARKET: stores_root / "market.sqlite",
        StoreRole.MACRO: stores_root / "macro.sqlite",
        StoreRole.COMPANY: stores_root / "company.sqlite",
        StoreRole.NEWS: stores_root / "news.sqlite",
    }
    for role, path in expected.items():
        try:
            expected_path = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValidationError("Stage 10 store binding is invalid") from exc
        if (
            store_map.path(role) != expected_path
            or _has_symlink_component(path)
        ):
            raise ValidationError("Stage 10 store binding is outside the approved target")

    return store_map


def _validate_materialized_store_files(
    store_map: StoreMap,
    stores_root: Path,
) -> None:
    """Reject missing, replaced, linked, or physically aliased target stores."""

    physical_identities: set[tuple[int, int]] = set()
    for role in StoreRole:
        expected = stores_root / f"{role.value}.sqlite"
        path = store_map.path(role)
        try:
            info = path.lstat()
            resolved = path.resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise ConflictError("Stage 10 target store layout is incomplete") from exc
        if (
            path != expected_resolved
            or resolved != expected_resolved
            or _has_symlink_component(expected)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1
        ):
            raise ConflictError("Stage 10 target store identity is unsafe")
        identity = (int(info.st_dev), int(info.st_ino))
        if identity in physical_identities:
            raise ConflictError("Stage 10 target stores are physically aliased")
        physical_identities.add(identity)
    if len(physical_identities) != len(tuple(StoreRole)):
        raise ConflictError("Stage 10 target stores are physically aliased")


def _validate_reused_store_evidence(
    layout: _TargetLayout,
    store_map: StoreMap,
    registry: Registry,
    scope: Stage10MarketScope,
) -> None:
    """Re-prove immutable stores before reporting a completed-run reuse."""

    candidate = _read_candidate_receipt(layout, scope=scope, registry=registry)
    evidence = candidate.evidence
    if not isinstance(evidence, Mapping):
        raise ConflictError("Stage 10 completed candidate evidence is invalid")
    state = _read_resume(layout, scope=scope, registry=registry)
    if state.legacy:
        raise ConflictError("Stage 10 completed resume state is invalid")
    before = mutation_fingerprint(store_map)
    reconciliation = reconcile_stage10_market(
        store_map,
        registry,
        scope,
        failed_records=tuple(record.to_primitive() for record in state.failed_records),
    )
    health = all_store_health(store_map, registry)
    manifest = logical_manifest(store_map, registry)
    after = mutation_fingerprint(store_map)
    health_sha256 = hashlib.sha256(
        dumps_strict(health.to_primitive()).encode("utf-8")
    ).hexdigest()
    expected_reconciliation = evidence.get("reconciliation")
    if (
        before.get("sha256") != after.get("sha256")
        or before.get("sha256") != evidence.get("source_mutation_after_sha256")
        or health_sha256 != evidence.get("source_health_sha256")
        or manifest.get("sha256") != evidence.get("source_logical_manifest_sha256")
        or not isinstance(expected_reconciliation, Mapping)
        or dumps_strict(reconciliation.to_primitive())
        != dumps_strict(expected_reconciliation)
    ):
        raise ConflictError("Stage 10 completed stores no longer match their candidate evidence")

def _require_private_directory(path: Path, *, label: str) -> None:

    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError("Stage 10 target layout is incomplete") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _ROOT_MODE
    ):
        raise ConflictError(f"Stage 10 {label} layout is invalid")


def _require_private_file(
    path: Path,
    *,
    label: str = "resume state",
    maximum_bytes: int = _MAX_RESUME_BYTES,
    require_empty: bool = False,
) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 10 private {label} is missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _RESUME_MODE
        or info.st_size > maximum_bytes
        or (require_empty and info.st_size != 0)
    ):
        raise ConflictError(f"Stage 10 private {label} is invalid")


def _require_optional_private_directory(path: Path, *, label: str) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    _require_private_directory(path, label=label)
    return True


def _require_optional_private_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    _require_private_file(path, label=label, maximum_bytes=maximum_bytes)
    return True


def _resume_state_from_primitive(
    value: object,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10ResumeState:
    legacy_fields = {
        "completed_symbols",
        "contract",
        "registry_source_sha256",
        "scope_manifest_sha256",
        "target_profile_id",
        "universes_published",
        "version",
    }
    current_fields = legacy_fields | {"failed_records", "failure_manifest_sha256"}
    if (
        not isinstance(value, Mapping)
        or (set(value) != legacy_fields and set(value) != current_fields)
    ):
        raise ConflictError("Stage 10 private resume state is invalid")
    symbols = value["completed_symbols"]
    if not isinstance(symbols, list) or len(symbols) > scope.bounds.max_instruments:
        raise ConflictError("Stage 10 private resume state is invalid")
    completed = tuple(symbols)
    is_legacy = set(value) == legacy_fields and value["version"] == _LEGACY_RESUME_VERSION
    if (
        value["contract"] != _RESUME_CONTRACT
        or value["version"] not in {_RESUME_VERSION, _LEGACY_RESUME_VERSION}
        or value["target_profile_id"] != scope.target_profile_id
        or value["scope_manifest_sha256"] != scope.manifest_sha256
        or value["registry_source_sha256"] != registry.source_sha256
        or not isinstance(value["universes_published"], bool)
        or any(not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None for symbol in completed)
        or completed != tuple(sorted(set(completed)))
        or (not value["universes_published"] and completed)
    ):
        raise ConflictError("Stage 10 private resume state does not match the reviewed target")
    if is_legacy:
        failed_records: tuple[Stage10FailureRecord, ...] = ()
        failure_manifest_sha256 = _failure_manifest_sha256(failed_records)
    else:
        if value["version"] != _RESUME_VERSION:
            raise ConflictError("Stage 10 private resume state is invalid")
        failed_records = _canonical_failure_records(value["failed_records"])
        failure_manifest_sha256 = _sha256(
            value["failure_manifest_sha256"], "failure manifest digest"
        )
        if failure_manifest_sha256 != _failure_manifest_sha256(failed_records):
            raise ConflictError("Stage 10 private resume failure manifest is invalid")
        if set(completed) & {record.symbol for record in failed_records}:
            raise ConflictError("Stage 10 private resume state is invalid")
    return Stage10ResumeState(
        target_profile_id=scope.target_profile_id,
        scope_manifest_sha256=scope.manifest_sha256,
        registry_source_sha256=registry.source_sha256,
        universes_published=value["universes_published"],
        completed_symbols=completed,
        failed_records=failed_records,
        failure_manifest_sha256=failure_manifest_sha256,
        legacy=is_legacy,
    )


def _initial_resume_state(scope: Stage10MarketScope, registry: Registry) -> Stage10ResumeState:
    return Stage10ResumeState(
        target_profile_id=scope.target_profile_id,
        scope_manifest_sha256=scope.manifest_sha256,
        registry_source_sha256=registry.source_sha256,
        universes_published=False,
        completed_symbols=(),
        failed_records=(),
        failure_manifest_sha256=_failure_manifest_sha256(()),
    )


def _write_all(handle: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(handle, content[offset:])
        if written <= 0:
            raise OSError("Stage 10 private state write failed")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_resume(layout: _TargetLayout, state: Stage10ResumeState) -> None:
    """Atomically replace one private progress file after a committed write."""

    temporary = layout.private / f".resume.{os.getpid()}.{uuid.uuid4().hex}.write"
    content = dumps_strict(state.to_primitive(), max_bytes=_MAX_RESUME_BYTES).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    replaced = False
    try:
        descriptor = os.open(temporary, flags, _RESUME_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, layout.resume)
        replaced = True
        os.chmod(layout.resume, _RESUME_MODE)
        _fsync_directory(layout.private)
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                temporary_info = temporary.lstat()
                if stat.S_ISREG(temporary_info.st_mode) and not stat.S_ISLNK(temporary_info.st_mode):
                    temporary.unlink()
            except (FileNotFoundError, OSError):
                pass
        raise


def _write_private_json(
    path: Path,
    value: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> None:
    """Atomically create one immutable private receipt without replacement."""

    content = dumps_strict(dict(value), max_bytes=maximum_bytes).encode("utf-8")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.write"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(temporary, flags, _RESUME_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path)
        linked = True
        os.chmod(path, _RESUME_MODE)
        _fsync_directory(path.parent)
        temporary.unlink()
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise ConflictError("Stage 10 private receipt already exists") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary.exists() or temporary.is_symlink():
            try:
                temporary.unlink()
            except OSError:
                pass
        if linked:
            _require_private_file(path, label="completion receipt", maximum_bytes=maximum_bytes)


def _failure_receipt_path(layout: _TargetLayout, symbol: str) -> Path:
    if _SYMBOL.fullmatch(symbol) is None:
        raise ValidationError("Stage 10 failure symbol is invalid")
    return layout.failures / f"{symbol}.json"


def _failure_receipt_material(
    record: Stage10FailureRecord,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> dict[str, object]:
    return {
        "contract": _FAILURE_CONTRACT,
        "record": record.to_primitive(),
        "registry_source_sha256": registry.source_sha256,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "version": _FAILURE_VERSION,
    }


def _require_failure_receipt(path: Path) -> None:
    _require_private_file(
        path,
        label="failure receipt",
        maximum_bytes=_MAX_FAILURE_RECEIPT_BYTES,
    )
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConflictError("Stage 10 private failure receipt is invalid") from exc
    if info.st_nlink != 1:
        raise ConflictError("Stage 10 private failure receipt is invalid")


def _write_failure_receipt(
    layout: _TargetLayout,
    record: Stage10FailureRecord,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Path:
    """Append one receipt first, using a direct 0600 O_EXCL file creation."""

    _require_private_directory(layout.failures, label="failure journal")
    material = _failure_receipt_material(record, scope=scope, registry=registry)
    payload = {
        **material,
        "sha256": hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest(),
    }
    content = dumps_strict(payload, max_bytes=_MAX_FAILURE_RECEIPT_BYTES).encode("utf-8")
    path = _failure_receipt_path(layout, record.symbol)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, _RESUME_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.fchmod(descriptor, _RESUME_MODE)
        os.close(descriptor)
        descriptor = None
        _fsync_directory(layout.failures)
    except FileExistsError as exc:
        raise ConflictError("Stage 10 private failure receipt already exists") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    _require_failure_receipt(path)
    return path


def _failure_receipt_from_primitive(
    value: object,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10FailureRecord:
    expected = {
        "contract",
        "record",
        "registry_source_sha256",
        "scope_manifest_sha256",
        "sha256",
        "target_profile_id",
        "version",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ConflictError("Stage 10 private failure receipt is invalid")
    try:
        record = Stage10FailureRecord.from_primitive(value["record"])
        material = _failure_receipt_material(record, scope=scope, registry=registry)
        digest = _sha256(value["sha256"], "failure receipt digest")
    except (TypeError, ValidationError, ConflictError) as exc:
        raise ConflictError("Stage 10 private failure receipt is invalid") from exc
    if (
        value["contract"] != _FAILURE_CONTRACT
        or value["version"] != _FAILURE_VERSION
        or value["target_profile_id"] != scope.target_profile_id
        or value["scope_manifest_sha256"] != scope.manifest_sha256
        or value["registry_source_sha256"] != registry.source_sha256
        or digest != hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()
    ):
        raise ConflictError("Stage 10 private failure receipt is invalid")
    return record


def _read_failure_journal(
    layout: _TargetLayout,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> tuple[Stage10FailureRecord, ...]:
    _require_private_directory(layout.failures, label="failure journal")
    receipts: list[Stage10FailureRecord] = []
    for entry in sorted(layout.failures.iterdir(), key=lambda item: item.name):
        if (
            not entry.name.endswith(".json")
            or _SYMBOL.fullmatch(entry.name[:-5]) is None
            or entry.name[:-5] in {"^RUT", "IWM"}
        ):
            raise ConflictError("Stage 10 private failure journal is invalid")
        _require_failure_receipt(entry)
        try:
            raw = loads_strict(
                entry.read_bytes(), max_bytes=_MAX_FAILURE_RECEIPT_BYTES
            )
            record = _failure_receipt_from_primitive(
                raw, scope=scope, registry=registry
            )
        except (OSError, ResourceLimitError, ValidationError, ConflictError) as exc:
            raise ConflictError("Stage 10 private failure journal is invalid") from exc
        if record.symbol != entry.name[:-5]:
            raise ConflictError("Stage 10 private failure journal is invalid")
        receipts.append(record)
    return _canonical_failure_records(tuple(receipts))


def _reconcile_failure_journal(
    layout: _TargetLayout,
    state: Stage10ResumeState,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10ResumeState:
    """Repair only journal-first crashes; resume-only declarations fail closed."""

    if state.legacy:
        # The strictly bounded one-time EUV migration below owns legacy state.
        # A pre-existing journal would make it impossible to prove that this is
        # the exact operator-authorized transition.
        if layout.failures.exists() or layout.failures.is_symlink():
            raise ConflictError("Stage 10 legacy failure layout is invalid")
        return state
    journal = _read_failure_journal(layout, scope=scope, registry=registry)
    state_by_symbol = {item.symbol: item for item in state.failed_records}
    journal_by_symbol = {item.symbol: item for item in journal}
    if not set(state_by_symbol).issubset(journal_by_symbol):
        raise ConflictError("Stage 10 private resume has unjournaled failures")
    if any(
        state_by_symbol[symbol].to_primitive()
        != journal_by_symbol[symbol].to_primitive()
        for symbol in state_by_symbol
    ):
        raise ConflictError("Stage 10 private failure journal conflicts with resume")
    if journal != state.failed_records:
        state = replace(
            state,
            failed_records=journal,
            failure_manifest_sha256=_failure_manifest_sha256(journal),
        )
        _write_resume(layout, state)
    return state


def _completion_from_primitive(
    value: object,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> _CompletionReceipt:
    expected = {
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
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ConflictError("Stage 10 private completion receipt is invalid")
    if (
        value["contract"] != _COMPLETION_CONTRACT
        or value["version"] != _COMPLETION_VERSION
        or value["target_profile_id"] != scope.target_profile_id
        or value["scope_manifest_sha256"] != scope.manifest_sha256
        or value["registry_source_sha256"] != registry.source_sha256
    ):
        raise ConflictError("Stage 10 private completion receipt does not match the reviewed target")
    try:
        receipt = _CompletionReceipt(
            target_profile_id=value["target_profile_id"],
            scope_manifest_sha256=_sha256(value["scope_manifest_sha256"], "scope manifest pin"),
            registry_source_sha256=_sha256(value["registry_source_sha256"], "registry source pin"),
            roster_symbol_count=value["roster_symbol_count"],
            completed_symbol_count=value["completed_symbol_count"],
            failed_records=_canonical_failure_records(value["failed_records"]),
            failure_manifest_sha256=_sha256(
                value["failure_manifest_sha256"], "failure manifest digest"
            ),
            evidence_sha256=_sha256(value["evidence_sha256"], "completion evidence pin"),
            candidate_receipt_sha256=_sha256(
                value["candidate_receipt_sha256"], "candidate receipt pin"
            ),
            sha256=_sha256(value["sha256"], "completion receipt pin"),
        )
    except (TypeError, ValidationError) as exc:
        raise ConflictError("Stage 10 private completion receipt is invalid") from exc
    if (
        isinstance(receipt.roster_symbol_count, bool)
        or not isinstance(receipt.roster_symbol_count, int)
        or isinstance(receipt.completed_symbol_count, bool)
        or not isinstance(receipt.completed_symbol_count, int)
        or value["failed_symbol_count"] != len(receipt.failed_records)
        or value["failed_tickers"] != [item.symbol for item in receipt.failed_records]
        or receipt.roster_symbol_count <= 0
        or receipt.roster_symbol_count > scope.bounds.max_instruments
        or receipt.completed_symbol_count < 0
        or receipt.completed_symbol_count + len(receipt.failed_records)
        != receipt.roster_symbol_count
        or receipt.failure_manifest_sha256
        != _failure_manifest_sha256(receipt.failed_records)
        or receipt.sha256 != hashlib.sha256(
            dumps_strict(receipt.material()).encode("utf-8")
        ).hexdigest()
    ):
        raise ConflictError("Stage 10 private completion receipt is invalid")
    return receipt


def _expected_candidate_attempt_id(scope: Stage10MarketScope) -> str:
    return "stage10-fmp-market-history-v1-" + scope.manifest_sha256[:16]


def _read_candidate_receipt(
    layout: _TargetLayout,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10CandidateReceipt:
    _require_private_directory(layout.candidate, label="candidate state")
    directory = layout.candidate / "promotion-candidates"
    _require_private_directory(directory, label="candidate receipt directory")
    attempt_id = _expected_candidate_attempt_id(scope)
    entries = {entry.name: entry for entry in directory.iterdir()}
    expected_name = attempt_id + ".json"
    if set(entries) != {expected_name}:
        raise ConflictError("Stage 10 private candidate receipt layout is invalid")
    receipt_path = entries[expected_name]
    _require_private_file(
        receipt_path,
        label="candidate receipt",
        maximum_bytes=_MAX_CANDIDATE_RECEIPT_BYTES,
    )
    try:
        raw = loads_strict(receipt_path.read_bytes(), max_bytes=_MAX_CANDIDATE_RECEIPT_BYTES)
        if not isinstance(raw, Mapping) or set(raw) != {
            "attempt_id",
            "candidate_state",
            "code_revision",
            "contract",
            "contract_version",
            "evidence",
            "evidence_sha256",
            "generated_at",
            "receipt_sha256",
        }:
            raise ValidationError("candidate receipt shape")
        receipt = Stage10CandidateReceipt(
            attempt_id=raw["attempt_id"],
            generated_at=raw["generated_at"],
            code_revision=raw["code_revision"],
            candidate_state=raw["candidate_state"],
            evidence=raw["evidence"],
            evidence_sha256=raw["evidence_sha256"],
            receipt_sha256=raw["receipt_sha256"],
        )
    except (OSError, ResourceLimitError, ValidationError, TypeError) as exc:
        raise ConflictError("Stage 10 private candidate receipt is invalid") from exc
    if (
        raw["contract"] != "quant_data.stage10_candidate_receipt"
        or raw["contract_version"] != "1.0.0"
        or receipt.attempt_id != attempt_id
        or receipt.code_revision != registry.source_sha256
        or not isinstance(receipt.evidence, Mapping)
        or receipt.evidence.get("sha256") != receipt.evidence_sha256
    ):
        raise ConflictError("Stage 10 private candidate receipt does not match the reviewed target")
    evidence = receipt.evidence
    reconciliation = evidence.get("reconciliation")
    if (
        not isinstance(reconciliation, Mapping)
        or reconciliation.get("scope_manifest_sha256") != scope.manifest_sha256
        or reconciliation.get("target_profile_id") != scope.target_profile_id
    ):
        raise ConflictError("Stage 10 private candidate receipt does not match the reviewed target")
    return receipt


def _read_completed_receipt(
    layout: _TargetLayout,
    state: Stage10ResumeState,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> _CompletionReceipt | None:
    if not _require_optional_private_file(
        layout.completion,
        label="completion receipt",
        maximum_bytes=_MAX_RESUME_BYTES,
    ):
        return None
    try:
        raw = loads_strict(layout.completion.read_bytes(), max_bytes=_MAX_RESUME_BYTES)
    except (OSError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError("Stage 10 private completion receipt is unreadable") from exc
    receipt = _completion_from_primitive(raw, scope=scope, registry=registry)
    candidate = _read_candidate_receipt(layout, scope=scope, registry=registry)
    # ``Stage10CandidateReceipt`` recursively freezes nested JSON arrays and
    # mappings.  Compare the receipt's public JSON primitive rather than its
    # tuple/mappingproxy implementation representation; otherwise every valid
    # nonzero failure manifest compares unequal to the completion's JSON lists.
    candidate_primitive = candidate.to_primitive()
    candidate_evidence = candidate_primitive.get("evidence")
    reconciliation = (
        candidate_evidence.get("reconciliation")
        if isinstance(candidate_evidence, Mapping)
        else None
    )
    expected_records = [item.to_primitive() for item in receipt.failed_records]
    if (
        state.legacy
        or not state.universes_published
        or len(state.completed_symbols) != receipt.completed_symbol_count
        or state.failed_records != receipt.failed_records
        or state.failure_manifest_sha256 != receipt.failure_manifest_sha256
        or receipt.evidence_sha256 != candidate.evidence_sha256
        or receipt.candidate_receipt_sha256 != candidate.receipt_sha256
        or not isinstance(reconciliation, Mapping)
        or reconciliation.get("failed_records") != expected_records
        or reconciliation.get("failed_tickers")
        != [item.symbol for item in receipt.failed_records]
        or reconciliation.get("failed_symbol_count") != len(receipt.failed_records)
        or reconciliation.get("successful_symbol_count")
        != receipt.completed_symbol_count
        or reconciliation.get("failure_manifest_sha256")
        != receipt.failure_manifest_sha256
    ):
        raise ConflictError("Stage 10 private completion receipt is inconsistent")
    return receipt


def _write_completion_receipt(
    layout: _TargetLayout,
    context: Stage10CompletionContext,
    candidate: Stage10CandidateReceipt,
) -> _CompletionReceipt:
    if (
        context.failure_manifest_sha256
        != _failure_manifest_sha256(context.failed_records)
    ):
        raise ValidationError("Stage 10 completion failure manifest is invalid")
    material = {
        "target_profile_id": context.scope.target_profile_id,
        "scope_manifest_sha256": context.scope.manifest_sha256,
        "registry_source_sha256": context.registry.source_sha256,
        "roster_symbol_count": context.roster_symbol_count,
        "completed_symbol_count": len(context.completed_symbols),
        "failed_records": [item.to_primitive() for item in context.failed_records],
        "failed_tickers": [item.symbol for item in context.failed_records],
        "failed_symbol_count": len(context.failed_records),
        "failure_manifest_sha256": context.failure_manifest_sha256,
        "evidence_sha256": candidate.evidence_sha256,
        "candidate_receipt_sha256": candidate.receipt_sha256,
    }
    receipt = _CompletionReceipt(
        target_profile_id=context.scope.target_profile_id,
        scope_manifest_sha256=context.scope.manifest_sha256,
        registry_source_sha256=context.registry.source_sha256,
        roster_symbol_count=context.roster_symbol_count,
        completed_symbol_count=len(context.completed_symbols),
        failed_records=context.failed_records,
        failure_manifest_sha256=context.failure_manifest_sha256,
        evidence_sha256=candidate.evidence_sha256,
        candidate_receipt_sha256=candidate.receipt_sha256,
        sha256=hashlib.sha256(
            dumps_strict(
                {
                    "contract": _COMPLETION_CONTRACT,
                    "version": _COMPLETION_VERSION,
                    **material,
                }
            ).encode("utf-8")
        ).hexdigest(),
    )
    _write_private_json(layout.completion, receipt.to_primitive(), maximum_bytes=_MAX_RESUME_BYTES)
    return receipt


def _layout_paths(root: Path) -> _TargetLayout:
    stores = root / _STORES_DIRECTORY
    private = root / _PRIVATE_DIRECTORY
    return _TargetLayout(
        root=root,
        stores=stores,
        private=private,
        resume=private / _RESUME_FILENAME,
        run_lock=private / _RUN_LOCK_FILENAME,
        backup=private / _BACKUP_DIRECTORY,
        restored=private / _RESTORED_DIRECTORY,
        candidate=private / _CANDIDATE_DIRECTORY,
        completion=private / _COMPLETION_FILENAME,
        failures=private / _FAILURES_DIRECTORY,
    )


def _existing_layout(root: Path) -> _TargetLayout:
    _require_private_directory(root, label="target root")
    entries = {entry.name: entry for entry in root.iterdir()}
    if set(entries) != {_STORES_DIRECTORY, _PRIVATE_DIRECTORY}:
        raise ConflictError("Stage 10 target contains unreviewed entries")
    layout = _layout_paths(root)
    _require_private_directory(layout.stores, label="stores")
    _require_private_directory(layout.private, label="private")
    private_entries = {entry.name for entry in layout.private.iterdir()}
    allowed = {
        _RESUME_FILENAME,
        _RUN_LOCK_FILENAME,
        _BACKUP_DIRECTORY,
        _RESTORED_DIRECTORY,
        _CANDIDATE_DIRECTORY,
        _COMPLETION_FILENAME,
        _FAILURES_DIRECTORY,
    }
    if not {_RESUME_FILENAME, _RUN_LOCK_FILENAME}.issubset(private_entries) or not private_entries.issubset(allowed):
        raise ConflictError("Stage 10 private target contains unreviewed entries")
    _require_private_file(layout.resume)
    _require_private_file(layout.run_lock, label="run lock", maximum_bytes=0, require_empty=True)
    backup_exists = _require_optional_private_directory(layout.backup, label="backup")
    restored_exists = _require_optional_private_directory(layout.restored, label="restored")
    candidate_exists = _require_optional_private_directory(layout.candidate, label="candidate state")
    failures_exists = _require_optional_private_directory(
        layout.failures, label="failure journal"
    )
    completion_exists = _require_optional_private_file(
        layout.completion,
        label="completion receipt",
        maximum_bytes=_MAX_RESUME_BYTES,
    )
    if completion_exists and not (backup_exists and restored_exists and candidate_exists):
        raise ConflictError("Stage 10 completion receipt layout is incomplete")
    if completion_exists and not failures_exists:
        raise ConflictError("Stage 10 completion receipt layout is incomplete")
    return layout


def _read_resume(
    layout: _TargetLayout,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10ResumeState:
    _require_private_file(layout.resume)
    try:
        raw = loads_strict(layout.resume.read_bytes(), max_bytes=_MAX_RESUME_BYTES)
    except (OSError, ValidationError, ResourceLimitError) as exc:
        raise ConflictError("Stage 10 private resume state is unreadable") from exc
    return _resume_state_from_primitive(raw, scope=scope, registry=registry)


def _create_failure_directory(layout: _TargetLayout) -> None:
    """Create the exact private append-only journal directory once."""

    try:
        layout.failures.mkdir(mode=_ROOT_MODE)
    except FileExistsError as exc:
        raise ConflictError("Stage 10 private failure journal already exists") from exc
    _require_private_directory(layout.failures, label="failure journal")
    _fsync_directory(layout.private)


def _legacy_euv_has_no_published_history(store_map: StoreMap) -> bool:
    """Read-only proof that the prior EUV failure never published evidence/facts."""

    with read_connection(store_map, StoreRole.MARKET) as connection:
        captures = connection.execute(
            """
            SELECT count(*)
            FROM stage10_daily_price_captures
            WHERE provider='fmp' AND provider_symbol=?
            """,
            (_LEGACY_EUV_SYMBOL,),
        ).fetchone()
        versions = connection.execute(
            """
            SELECT count(*)
            FROM stage10_daily_price_versions AS version
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=version.instrument_id
            WHERE instrument.provider='fmp' AND instrument.provider_symbol=?
            """,
            (_LEGACY_EUV_SYMBOL,),
        ).fetchone()
        current = connection.execute(
            """
            SELECT count(*)
            FROM stage10_daily_prices AS current
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=current.instrument_id
            WHERE instrument.provider='fmp' AND instrument.provider_symbol=?
            """,
            (_LEGACY_EUV_SYMBOL,),
        ).fetchone()
    return all(
        row is not None and int(row[0]) == 0 for row in (captures, versions, current)
    )


def _migrate_exact_legacy_euv_failure(
    layout: _TargetLayout,
    state: Stage10ResumeState,
    *,
    roster: tuple[str, ...],
    store_map: StoreMap,
    scope: Stage10MarketScope,
    registry: Registry,
) -> Stage10ResumeState:
    """Perform only the reviewed one-time EUV journal/resume reconstruction.

    This is intentionally not a generic legacy-reader.  It is valid only for
    the one interrupted live target whose old resume pin names exactly 200
    committed symbols and for which EUV is the next untouched reviewed roster
    member.  The receipt is appended before the upgraded resume.  If the
    process dies in between, the next invocation repairs only that verified
    journal-first state.
    """

    if not state.legacy:
        return state
    completed = set(state.completed_symbols)
    pending = tuple(symbol for symbol in roster if symbol not in completed)
    if (
        not state.universes_published
        or len(state.completed_symbols) != _LEGACY_EUV_COMPLETED_COUNT
        or len(completed) != _LEGACY_EUV_COMPLETED_COUNT
        or _LEGACY_EUV_SYMBOL in completed
        or not pending
        or pending[0] != _LEGACY_EUV_SYMBOL
        or not _legacy_euv_has_no_published_history(store_map)
    ):
        raise ConflictError("Stage 10 legacy resume is not the authorized EUV transition")

    seed = Stage10FailureRecord(
        symbol=_LEGACY_EUV_SYMBOL,
        reason="operator_authorized_prior_failure",
        provenance="stage10_operator_authorized_seed",
    )
    if layout.failures.exists() or layout.failures.is_symlink():
        journal = _read_failure_journal(layout, scope=scope, registry=registry)
        if journal != (seed,):
            raise ConflictError("Stage 10 legacy failure journal is not the authorized EUV transition")
    else:
        _create_failure_directory(layout)
        _write_failure_receipt(layout, seed, scope=scope, registry=registry)
        journal = (seed,)
    upgraded = replace(
        state,
        failed_records=journal,
        failure_manifest_sha256=_failure_manifest_sha256(journal),
        legacy=False,
    )
    _write_resume(layout, upgraded)
    return upgraded


def _new_layout(
    root: Path,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> _TargetLayout:
    root.mkdir(mode=_ROOT_MODE)
    layout = _layout_paths(root)
    layout.stores.mkdir(mode=_ROOT_MODE)
    layout.private.mkdir(mode=_ROOT_MODE)
    layout.failures.mkdir(mode=_ROOT_MODE)
    _require_private_directory(root, label="target root")
    _require_private_directory(layout.stores, label="stores")
    _require_private_directory(layout.private, label="private")
    _require_private_directory(layout.failures, label="failure journal")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags, _RESUME_MODE)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(layout.private)
    state = _initial_resume_state(scope, registry)
    try:
        _write_resume(layout, state)
    except BaseException:
        # This layout has not published a capture or opened a store.  Remove
        # only the exact files/directories created above, and only while they
        # remain empty; a concurrent or unexpected entry is never removed.
        try:
            layout.run_lock.unlink()
            layout.failures.rmdir()
            layout.private.rmdir()
            layout.stores.rmdir()
            layout.root.rmdir()
        except OSError:
            pass
        raise
    return layout


def _open_target(
    root: Path,
    *,
    scope: Stage10MarketScope,
    registry: Registry,
) -> tuple[_TargetLayout, bool]:
    try:
        root.lstat()
    except FileNotFoundError:
        try:
            layout = _new_layout(root, scope=scope, registry=registry)
        except FileExistsError:
            layout = _existing_layout(root)
            return layout, True
        return layout, False
    return _existing_layout(root), True


@dataclass(slots=True)
class _TargetRunLock:
    """One private, nonblocking advisory lock held across the full manual run."""

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

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def _acquire_run_lock(layout: _TargetLayout) -> _TargetRunLock:
    """Acquire the exact private lock once, with no wait or stale-file removal."""

    _require_private_file(layout.run_lock, label="run lock", maximum_bytes=0, require_empty=True)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags)
    try:
        before = layout.run_lock.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_ino != opened.st_ino
            or before.st_dev != opened.st_dev
            or stat.S_IMODE(opened.st_mode) != _RESUME_MODE
            or opened.st_size != 0
        ):
            raise ConflictError("Stage 10 private run lock is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Stage 10 target is already running") from exc
        current = layout.run_lock.lstat()
        if current.st_ino != opened.st_ino or current.st_dev != opened.st_dev:
            raise ConflictError("Stage 10 private run lock changed during acquisition")
        return _TargetRunLock(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _outcome(value: object) -> str:
    result = value.get("outcome") if isinstance(value, Mapping) else getattr(value, "outcome", None)
    if not isinstance(result, str):
        raise ValidationError("Stage 10 publication receipt is invalid")
    return result


def _default_assert_no_partial_universe_publication(
    store_map: StoreMap,
    scope: Stage10MarketScope,
) -> None:
    with read_connection(store_map, StoreRole.MARKET) as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM stage10_scope_snapshots
            WHERE scope_manifest_sha256=? AND target_profile_id=? AND provider='fmp'
            """,
            (scope.manifest_sha256, scope.target_profile_id),
        ).fetchone()
    if row is None or int(row[0]) != 0:
        raise ConflictError("Stage 10 target has partial universe publication state")


def _default_roster_reader(
    store_map: StoreMap,
    scope: Stage10MarketScope,
) -> tuple[str, ...]:
    """Read the deduplicated scoped roster without mutating any store."""

    with read_connection(store_map, StoreRole.MARKET) as connection:
        scope_rows = tuple(
            connection.execute(
                """
                SELECT scope_snapshot_id
                FROM stage10_scope_snapshots
                WHERE scope_manifest_sha256=? AND target_profile_id=? AND provider='fmp'
                ORDER BY scope_snapshot_id
                """,
                (scope.manifest_sha256, scope.target_profile_id),
            )
        )
        if len(scope_rows) != 1:
            raise ValidationError("Stage 10 scoped roster has an invalid scope snapshot")
        scope_snapshot_id = str(scope_rows[0]["scope_snapshot_id"])
        snapshots = tuple(
            connection.execute(
                """
                SELECT universe_id, count(*) AS snapshot_count
                FROM stage10_universe_snapshots
                WHERE scope_snapshot_id=? AND completeness='complete'
                GROUP BY universe_id
                ORDER BY universe_id
                """,
                (scope_snapshot_id,),
            )
        )
        if tuple((str(row["universe_id"]), int(row["snapshot_count"])) for row in snapshots) != tuple(
            (universe_id, 1) for universe_id in sorted(_UNIVERSE_IDS)
        ):
            raise ValidationError("Stage 10 scoped roster has invalid universe snapshots")
        rows = tuple(
            connection.execute(
                """
                SELECT DISTINCT instrument.provider_symbol, instrument.asset_type
                FROM stage10_universe_snapshot_members AS member
                JOIN stage10_universe_snapshots AS snapshot
                  ON snapshot.universe_snapshot_id=member.universe_snapshot_id
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=member.instrument_id
                WHERE snapshot.scope_snapshot_id=?
                  AND snapshot.universe_id IN (?, ?, ?, ?, ?)
                  AND snapshot.completeness='complete'
                ORDER BY instrument.provider_symbol
                """,
                (scope_snapshot_id, *_UNIVERSE_IDS),
            )
        )
    roster = tuple(str(row["provider_symbol"]) for row in rows)
    if (
        not roster
        or len(roster) != len(set(roster))
        or len(roster) > scope.bounds.max_instruments
        or any(
            _SYMBOL.fullmatch(symbol) is None
            or symbol in {"^RUT", "IWM"}
            or "russell" in symbol.casefold()
            or str(row["asset_type"]) not in _ASSET_TYPES
            for symbol, row in zip(roster, rows, strict=True)
        )
    ):
        raise ValidationError("Stage 10 scoped roster is invalid")
    return roster


def _default_bindings() -> Stage10BackfillBindings:
    def capture_universe(
        source: UniverseSource,
        api_key: str,
        transport: object,
    ) -> FmpStage10UniverseCapture:
        return capture_fmp_stage10_universe(
            prepare_fmp_stage10_universe_capture(source.endpoint_path),
            api_key=api_key,
            transport=transport,  # type: ignore[arg-type]
        )

    def capture_price(
        symbol: str,
        api_key: str,
        transport: object,
    ) -> FmpStage10PriceCapture:
        return capture_fmp_stage10_price(
            prepare_fmp_stage10_price_capture(symbol),
            api_key=api_key,
            transport=transport,  # type: ignore[arg-type]
        )

    def importer_factory(
        store_map: StoreMap,
        registry: Registry,
        scope: Stage10MarketScope,
    ) -> Stage10HistoryImporter:
        return Stage10HistoryImporter(store_map, registry, scope)

    def prepare_universes(
        importer: object,
        captures: Mapping[str, object],
    ) -> Sequence[object]:
        if not isinstance(importer, Stage10HistoryImporter):
            raise ValidationError("Stage 10 importer binding is invalid")
        typed = {key: value for key, value in captures.items()}
        return importer.prepare_universe_publications(typed)  # type: ignore[arg-type]

    def prepare_price(importer: object, capture: object) -> object:
        if not isinstance(importer, Stage10HistoryImporter) or not isinstance(
            capture, FmpStage10PriceCapture
        ):
            raise ValidationError("Stage 10 importer binding is invalid")
        return importer.prepare_price_capture(capture)

    def publish(importer: object, prepared: object) -> object:
        if not isinstance(importer, Stage10HistoryImporter):
            raise ValidationError("Stage 10 importer binding is invalid")
        return importer.publish_prepared(prepared)  # type: ignore[arg-type]

    return Stage10BackfillBindings(
        transport_factory=lambda _api_key, store_map, scope: StdlibFmpStage10Transport(store_map, scope),
        capture_universe=capture_universe,
        capture_price=capture_price,
        importer_factory=importer_factory,
        prepare_universes=prepare_universes,
        prepare_price=prepare_price,
        publish=publish,
        roster_reader=_default_roster_reader,
        assert_no_partial_universe_publication=_default_assert_no_partial_universe_publication,
    )


def _resolve_bindings(value: Stage10BackfillBindings | None) -> Stage10BackfillBindings:
    bindings = _default_bindings() if value is None else value
    if not isinstance(bindings, Stage10BackfillBindings) or any(
        not callable(getattr(bindings, field_name))
        for field_name in (
            "transport_factory",
            "capture_universe",
            "capture_price",
            "importer_factory",
            "prepare_universes",
            "prepare_price",
            "publish",
            "roster_reader",
            "assert_no_partial_universe_publication",
        )
    ):
        raise _ConfigurationFailure
    return bindings


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_default_completion(
    context: Stage10CompletionContext,
    layout: _TargetLayout,
    *,
    now: Callable[[], object],
    backup: Callable[..., object] = backup_all,
    restore: Callable[..., object] = restore_all,
    evidence_builder: Callable[..., object] = build_stage10_repopulation_evidence,
    candidate_state_factory: Callable[[Path], object] = PrivateStage10CandidateState,
) -> _CompletionReceipt:
    """Back up, restore, reconcile, and publish private candidate evidence.

    The backup and restore roots are exact private children.  A partial
    completion is deliberately left immutable and fails closed; the runner
    never deletes a receipt or backup material to make a subsequent attempt
    look clean.
    """

    if not isinstance(context, Stage10CompletionContext):
        raise ValidationError("Stage 10 completion context is invalid")
    if any(path.exists() for path in (layout.backup, layout.restored, layout.candidate)):
        raise ConflictError("Stage 10 private completion material already exists without a receipt")
    backup_cohort = backup(context.store_map, context.registry, target_root=layout.backup)
    os.chmod(layout.backup, _ROOT_MODE)
    _require_private_directory(layout.backup, label="backup")
    restored_cohort = restore(backup_cohort, context.registry, target_root=layout.restored)
    os.chmod(layout.restored, _ROOT_MODE)
    _require_private_directory(layout.restored, label="restored")
    restored_map = getattr(restored_cohort, "store_map", None)
    if not isinstance(restored_map, StoreMap):
        raise ValidationError("Stage 10 restore receipt is invalid")
    evidence = evidence_builder(
        context.store_map,
        restored_map,
        context.registry,
        context.scope,
        replay_unchanged=True,
        failed_records=tuple(
            record.to_primitive() for record in context.failed_records
        ),
    )
    state = candidate_state_factory(layout.candidate)
    if not isinstance(state, PrivateStage10CandidateState):
        raise ValidationError("Stage 10 candidate state binding is invalid")
    candidate = state.publish(
        evidence,
        code_revision=context.registry.source_sha256,
        now=now,
        attempt_id=_expected_candidate_attempt_id(context.scope),
    )
    if not isinstance(candidate, Stage10CandidateReceipt):
        raise ValidationError("Stage 10 candidate receipt is invalid")
    return _write_completion_receipt(layout, context, candidate)


def _monotonic_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValidationError("Stage 10 monotonic clock is invalid")
    return float(value)


class _RequestPacer:
    """Enforce the reviewed delay before every provider request after the first."""

    def __init__(
        self,
        *,
        interval_seconds: float,
        monotonic: Callable[[], object],
        sleeper: Callable[[float], None],
        force_initial_delay: bool,
    ) -> None:
        if interval_seconds < 1.0 or not callable(monotonic) or not callable(sleeper):
            raise _ConfigurationFailure
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._force_initial_delay = force_initial_delay
        self._last_request_at: float | None = None

    def before_request(self) -> None:
        if self._last_request_at is None:
            if self._force_initial_delay:
                self._sleeper(self._interval_seconds)
            self._last_request_at = _monotonic_seconds(self._monotonic())
            return
        for _ in range(4):
            now = _monotonic_seconds(self._monotonic())
            remaining = self._interval_seconds - (now - self._last_request_at)
            if remaining <= 0:
                self._last_request_at = now
                return
            self._sleeper(remaining)
        now = _monotonic_seconds(self._monotonic())
        if now - self._last_request_at < self._interval_seconds:
            raise ValidationError("Stage 10 request pacing did not advance")
        self._last_request_at = now


def _validate_roster(
    roster: object,
    *,
    scope: Stage10MarketScope,
    state: Stage10ResumeState,
) -> tuple[str, ...]:
    if not isinstance(roster, tuple) or not roster or len(roster) > scope.bounds.max_instruments:
        raise ValidationError("Stage 10 scoped roster is invalid")
    if (
        roster != tuple(sorted(set(roster)))
        or any(
            not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or symbol in {"^RUT", "IWM"}
            or "russell" in symbol.casefold()
            for symbol in roster
        )
        or not set(state.completed_symbols).issubset(roster)
        or not {record.symbol for record in state.failed_records}.issubset(roster)
        or set(state.completed_symbols) & {record.symbol for record in state.failed_records}
        or len(state.completed_symbols) + len(state.failed_records) > len(roster)
    ):
        raise ValidationError("Stage 10 scoped roster is invalid")
    return roster


def _success_payload(
    *,
    scope: Stage10MarketScope,
    registry: Registry,
    roster_count: int,
    completed_count: int,
    failed_records: tuple[Stage10FailureRecord, ...],
    failure_manifest_sha256: str,
    resumed: bool,
    universe_requests: int,
    price_requests: int,
    completion: str,
) -> dict[str, object]:
    return {
        "candidate_state": "candidate_only_no_operational_promotion",
        "completed_symbol_count": completed_count,
        "completion": completion,
        "contract": _RESULT_CONTRACT,
        "contract_version": _CONTRACT_VERSION,
        "failed_records": [record.to_primitive() for record in failed_records],
        "failed_symbol_count": len(failed_records),
        "failed_tickers": [record.symbol for record in failed_records],
        "failure_manifest_sha256": failure_manifest_sha256,
        "price_request_count": price_requests,
        "provider": "fmp",
        "registry_source_sha256": _sha256(registry.source_sha256, "registry source pin"),
        "resumed": resumed,
        "roster_symbol_count": roster_count,
        "scope_manifest_sha256": _sha256(scope.manifest_sha256, "scope manifest pin"),
        "target_profile_id": scope.target_profile_id,
        "universe_request_count": universe_requests,
    }


def _write_success(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(dumps_strict(dict(payload)))
    stream.write("\n")
    stream.flush()


def _write_error(stream: TextIO, code: str, exit_code: int) -> None:
    stream.write(
        dumps_strict(
            {
                "contract": _ERROR_CONTRACT,
                "contract_version": _ERROR_CONTRACT_VERSION,
                "error": code,
                "exit_code": exit_code,
            }
        )
    )
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
    scope_loader: Callable[[str | Path], Stage10MarketScope] = load_stage10_market_scope,
    scope_validator: Callable[[Stage10MarketScope], None] = _scope_preflight,
    store_map_factory: Callable[[Path], StoreMap] = _store_map_for,
    store_file_validator: Callable[[StoreMap, Path], None] = _validate_materialized_store_files,
    completed_store_validator: Callable[
        [_TargetLayout, StoreMap, Registry, Stage10MarketScope], None
    ] = _validate_reused_store_evidence,
    initializer: Callable[[StoreMap, Registry], object] = initialize_all,
    bindings: Stage10BackfillBindings | None = None,
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    completion_callback: Callable[[Stage10CompletionContext], None] | None = None,
    now: Callable[[], object] = _default_now,
    approved_project_root: str | Path = APPROVED_PROJECT_ROOT,
    approved_target_root: str | Path = APPROVED_TARGET_ROOT,
) -> int:
    """Run the one fixed, manual, candidate-only Stage 10 backfill.

    The callable seams keep offline tests fully deterministic.  The production
    default has no input for symbols, date ranges, target layout, retries, or
    promotion; all of those are fixed by the reviewed scope and this module.
    """

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        project_root = _preflight_project_root(arguments.project_root, approved_project_root)
        target_root = _preflight_target_root(arguments.target_root, approved_target_root)
        api_key = _read_api_key(environment if environment is not None else os.environ)
        if (
            not callable(store_file_validator)
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
            getattr(registry, "schema_version", None) == "1.7.0"
            and getattr(registry, "registry_version", None)
            in {"2.9.0", "2.10.0"}
        ):
            registry = stage10_registry_profile(registry)
        registry_validator(registry)
        scope = scope_loader(project_root / _SCOPE_RELATIVE_PATH)
        scope_validator(scope)
        resolved_bindings = _resolve_bindings(bindings)

        layout, resumed = _open_target(target_root, scope=scope, registry=registry)
        with _acquire_run_lock(layout):
            state = _read_resume(layout, scope=scope, registry=registry)
            if not state.legacy:
                state = _reconcile_failure_journal(
                    layout,
                    state,
                    scope=scope,
                    registry=registry,
                )
            completed_receipt = _read_completed_receipt(
                layout,
                state,
                scope=scope,
                registry=registry,
            )
            if completed_receipt is not None:
                store_map = _validate_target_store_map(
                    store_map_factory(layout.stores), layout.stores
                )
                store_file_validator(store_map, layout.stores)
                completed_store_validator(
                    layout,
                    store_map,
                    registry,
                    scope,
                )
                _write_success(
                    output,
                    _success_payload(
                        scope=scope,
                        registry=registry,
                        roster_count=completed_receipt.roster_symbol_count,
                        completed_count=completed_receipt.completed_symbol_count,
                        failed_records=completed_receipt.failed_records,
                        failure_manifest_sha256=completed_receipt.failure_manifest_sha256,
                        resumed=True,
                        universe_requests=0,
                        price_requests=0,
                        completion="reused",
                    ),
                )
                return 0
            store_map = _validate_target_store_map(store_map_factory(layout.stores), layout.stores)
            initializer(store_map, registry)
            store_file_validator(store_map, layout.stores)
            importer = resolved_bindings.importer_factory(store_map, registry, scope)
            transport = resolved_bindings.transport_factory(api_key, store_map, scope)
            if transport is None:
                raise ValidationError("Stage 10 transport binding is invalid")
            pacer = _RequestPacer(
                interval_seconds=scope.bounds.minimum_request_interval_milliseconds / 1000.0,
                monotonic=monotonic,
                sleeper=sleeper,
                force_initial_delay=resumed,
            )
            universe_request_count = 0
            price_request_count = 0

            if state.legacy and not state.universes_published:
                raise ConflictError("Stage 10 legacy resume is not the authorized EUV transition")
            if not state.universes_published:
                captures: dict[str, object] = {}
                for source in scope.universe_sources:
                    pacer.before_request()
                    captures[source.id] = resolved_bindings.capture_universe(source, api_key, transport)
                    universe_request_count += 1
                candidates = tuple(resolved_bindings.prepare_universes(importer, captures))
                if len(candidates) != len(_UNIVERSE_IDS):
                    raise ValidationError("Stage 10 universe preparation did not return five candidates")
                for candidate in candidates:
                    if _outcome(resolved_bindings.publish(importer, candidate)) not in {
                        "succeeded",
                        "unchanged",
                    }:
                        raise ValidationError("Stage 10 universe publication did not succeed")
                state = replace(state, universes_published=True)
                _write_resume(layout, state)

            roster = _validate_roster(
                resolved_bindings.roster_reader(store_map, scope),
                scope=scope,
                state=state,
            )
            if state.legacy:
                state = _migrate_exact_legacy_euv_failure(
                    layout,
                    state,
                    roster=roster,
                    store_map=store_map,
                    scope=scope,
                    registry=registry,
                )
                state = _reconcile_failure_journal(
                    layout,
                    state,
                    scope=scope,
                    registry=registry,
                )
                roster = _validate_roster(roster, scope=scope, state=state)
            completed = set(state.completed_symbols)
            failed = {record.symbol for record in state.failed_records}
            for symbol in roster:
                if symbol in completed or symbol in failed:
                    continue
                pacer.before_request()
                price_request_count += 1
                try:
                    capture = resolved_bindings.capture_price(symbol, api_key, transport)
                except FmpPriceHistoryUnavailable as exc:
                    record = Stage10FailureRecord.from_unavailable(exc)
                    if record.symbol != symbol or record.symbol in completed or record.symbol in failed:
                        raise ValidationError("Stage 10 unavailable-history failure is invalid")
                    # Receipt first, then the replaceable resume: a crash here
                    # is repaired solely from a valid append-only journal.
                    _write_failure_receipt(
                        layout,
                        record,
                        scope=scope,
                        registry=registry,
                    )
                    failed.add(record.symbol)
                    records = tuple(
                        sorted(
                            (*state.failed_records, record),
                            key=lambda item: item.symbol,
                        )
                    )
                    state = replace(
                        state,
                        failed_records=records,
                        failure_manifest_sha256=_failure_manifest_sha256(records),
                    )
                    _write_resume(layout, state)
                    continue
                candidate = resolved_bindings.prepare_price(importer, capture)
                outcome = _outcome(resolved_bindings.publish(importer, candidate))
                if outcome not in {"succeeded", "unchanged"}:
                    raise ValidationError("Stage 10 price publication did not commit")
                completed.add(symbol)
                state = replace(state, completed_symbols=tuple(sorted(completed)))
                _write_resume(layout, state)

            if len(state.completed_symbols) + len(state.failed_records) != len(roster):
                raise ValidationError("Stage 10 completion state is incomplete")

            context = Stage10CompletionContext(
                store_map=store_map,
                registry=registry,
                scope=scope,
                private_root=layout.private,
                completed_symbols=state.completed_symbols,
                failed_records=state.failed_records,
                failure_manifest_sha256=state.failure_manifest_sha256,
                roster_symbol_count=len(roster),
                resumed=resumed,
            )
            if completion_callback is None:
                _run_default_completion(context, layout, now=now)
                completion = "completed"
            else:
                callback_result = completion_callback(context)
                if callback_result is not None:
                    raise ValidationError("Stage 10 completion callback must not return public data")
                completion = "injected"
            _write_success(
                output,
                _success_payload(
                    scope=scope,
                    registry=registry,
                    roster_count=len(roster),
                    completed_count=len(state.completed_symbols),
                    failed_records=state.failed_records,
                    failure_manifest_sha256=state.failure_manifest_sha256,
                    resumed=resumed,
                    universe_requests=universe_request_count,
                    price_requests=price_request_count,
                    completion=completion,
                ),
            )
            return 0
    except _ArgumentFailure:
        exit_code, code = 64, "invalid_arguments"
    except _ConfigurationFailure:
        exit_code, code = 78, "invalid_configuration"
    except (StoreUnavailableError,):
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
