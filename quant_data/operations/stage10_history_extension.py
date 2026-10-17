"""Manual, resumable Stage 10 dated-history extension.

The public entry point is bound to the one reviewed non-production target and
the fixed 629-symbol, eight-window schedule. It requires a separate AAPL
1990--1994 entitlement sentinel phase before continuation, journals intent
before each provider call, never retries automatically, and retains immutable
raw responses before short atomic publication. Finalization creates only a
private backup/restore-validated candidate receipt.

The private namespace is a child of Stage 10's already-approved candidate
directory instead of a new top-level ``private`` entry:

``private/candidate/stage10-history-extension-v1``.

That placement preserves the completed base runner's existing top-level layout
contract; it does not promote or expose an operational store.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Final, TextIO

from ..contracts import IngestionReceipt
from ..errors import (
    CapabilityUnavailableError,
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict, loads_strict
from ..market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_PRICE_PATH,
    FMP_STAGE10_TIMEOUT_SECONDS,
    FmpStage10Transport,
    StdlibFmpStage10Transport,
)
from ..market.stage10_history_windows import (
    FmpStage10WindowCapture,
    PreparedFmpStage10WindowCapture,
    STAGE10_HISTORY_WINDOWS,
    Stage10HistoryWindow,
    parse_fmp_stage10_window_response,
    prepare_fmp_stage10_window_capture,
)
from ..market.stage10_scope import Stage10MarketScope
from ..market.stage10_window_importer import Stage10HistoryWindowImporter
from ..registry import Registry, stage11_registry_profile
from ..stores import StoreMap, StoreRole, read_connection
from .stage10_history_extension_transition_402 import (
    CLOSED_REQUEST_COUNT as TRANSITION_402_CLOSED_REQUEST_COUNT,
    CONTENT_TYPE as TRANSITION_402_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_402_HTTP_STATUS,
    OLD_EXECUTION_REVISION as TRANSITION_402_OLD_EXECUTION_REVISION,
    PENDING_FROM as TRANSITION_402_PENDING_FROM,
    PENDING_INTENT_SHA256 as TRANSITION_402_PENDING_INTENT_SHA256,
    PENDING_ORDINAL as TRANSITION_402_PENDING_ORDINAL,
    PENDING_SYMBOL as TRANSITION_402_PENDING_SYMBOL,
    PENDING_TO as TRANSITION_402_PENDING_TO,
    PENDING_WINDOW_ID as TRANSITION_402_PENDING_WINDOW_ID,
    PLAN_SHA256 as TRANSITION_402_PLAN_SHA256,
    RESPONSE_BYTE_COUNT as TRANSITION_402_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_402_RESPONSE_SHA256,
    TRANSITION_AUTHORIZATION as TRANSITION_402_AUTHORIZATION,
    TRANSITION_CONTRACT as TRANSITION_402_CONTRACT,
    TRANSITION_FILENAME as TRANSITION_402_FILENAME,
    TRANSITION_TERMINAL_REASON as TRANSITION_402_TERMINAL_REASON,
    TRANSITION_VERSION as TRANSITION_402_VERSION,
)
from .stage10_history_extension_transition_empty import (
    CLOSED_REQUEST_COUNT as TRANSITION_EMPTY_CLOSED_REQUEST_COUNT,
    CONTENT_TYPE as TRANSITION_EMPTY_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_EMPTY_HTTP_STATUS,
    OLD_EXECUTION_REVISION as TRANSITION_EMPTY_OLD_EXECUTION_REVISION,
    PENDING_FROM as TRANSITION_EMPTY_PENDING_FROM,
    PENDING_INTENT_SHA256 as TRANSITION_EMPTY_PENDING_INTENT_SHA256,
    PENDING_ORDINAL as TRANSITION_EMPTY_PENDING_ORDINAL,
    PENDING_SYMBOL as TRANSITION_EMPTY_PENDING_SYMBOL,
    PENDING_TO as TRANSITION_EMPTY_PENDING_TO,
    PENDING_WINDOW_ID as TRANSITION_EMPTY_PENDING_WINDOW_ID,
    PLAN_SHA256 as TRANSITION_EMPTY_PLAN_SHA256,
    PRIOR_TRANSITION_SHA256 as TRANSITION_EMPTY_PRIOR_TRANSITION_SHA256,
    RESPONSE_BYTE_COUNT as TRANSITION_EMPTY_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_EMPTY_RESPONSE_SHA256,
    TRANSITION_AUTHORIZATION as TRANSITION_EMPTY_AUTHORIZATION,
    TRANSITION_CONTRACT as TRANSITION_EMPTY_CONTRACT,
    TRANSITION_FILENAME as TRANSITION_EMPTY_FILENAME,
    TRANSITION_TERMINAL_REASON as TRANSITION_EMPTY_TERMINAL_REASON,
    TRANSITION_VERSION as TRANSITION_EMPTY_VERSION,
)

from .stage10_history_extension_transition_empty_chain import (
    CLOSED_REQUEST_COUNT as TRANSITION_CHAIN_CLOSED_REQUEST_COUNT,
    KNOWN_LISTED_EMPTY_TRANSITION_SHA256 as TRANSITION_CHAIN_EMPTY_SHA256,
    LIVE_EXECUTION_REVISION as TRANSITION_CHAIN_LIVE_EXECUTION_REVISION,
    LIVE_MANIFEST_SHA256 as TRANSITION_CHAIN_LIVE_MANIFEST_SHA256,
    OLD_EXECUTION_REVISION as TRANSITION_CHAIN_OLD_EXECUTION_REVISION,
    PENDING_CONTENT_TYPE as TRANSITION_CHAIN_PENDING_CONTENT_TYPE,
    PENDING_FROM as TRANSITION_CHAIN_PENDING_FROM,
    PENDING_HTTP_STATUS as TRANSITION_CHAIN_PENDING_HTTP_STATUS,
    PENDING_INTENT_SHA256 as TRANSITION_CHAIN_PENDING_INTENT_SHA256,
    PENDING_ORDINAL as TRANSITION_CHAIN_PENDING_ORDINAL,
    PENDING_RESPONSE_BYTE_COUNT as TRANSITION_CHAIN_PENDING_RESPONSE_BYTE_COUNT,
    PENDING_RESPONSE_SHA256 as TRANSITION_CHAIN_PENDING_RESPONSE_SHA256,
    PENDING_SYMBOL as TRANSITION_CHAIN_PENDING_SYMBOL,
    PENDING_TO as TRANSITION_CHAIN_PENDING_TO,
    PENDING_WINDOW_ID as TRANSITION_CHAIN_PENDING_WINDOW_ID,
    PLAN_SHA256 as TRANSITION_CHAIN_PLAN_SHA256,
    PREFIX_LEDGER_SHA256 as TRANSITION_CHAIN_PREFIX_LEDGER_SHA256,
    PREFIX_RAW_SIDECAR_MANIFEST_SHA256 as TRANSITION_CHAIN_PREFIX_RAW_SHA256,
    PRIOR_402_TRANSITION_SHA256 as TRANSITION_CHAIN_402_SHA256,
    TRANSITION_AUTHORIZATION as TRANSITION_CHAIN_AUTHORIZATION,
    TRANSITION_CONTRACT as TRANSITION_CHAIN_CONTRACT,
    TRANSITION_FILENAME as TRANSITION_CHAIN_FILENAME,
    TRANSITION_VERSION as TRANSITION_CHAIN_VERSION,
    build_stage10_history_extension_authorization_chain,
    build_stage10_history_extension_authorization_proof,
    stage10_history_extension_execution_revision,
)



HISTORY_EXTENSION_CONTRACT: Final = "quant_data.stage10_history_extension"
HISTORY_EXTENSION_VERSION: Final = "1.0.0"
HISTORY_EXTENSION_NAMESPACE: Final = Path(
    "private/candidate/stage10-history-extension-v1"
)
HISTORY_EXTENSION_TARGET_PROFILE_ID: Final = "stage10_fmp_market_history_v1"
HISTORY_EXTENSION_EXPECTED_ROSTER_COUNT: Final = 629
HISTORY_EXTENSION_EXPECTED_BASE_FAILURE_COUNT: Final = 8
HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT: Final = 5_032
HISTORY_EXTENSION_MINIMUM_INTERVAL_SECONDS: Final = 1.0
_REQUEST_SCHEDULE: Final = "window_major_aapl_then_symbol_ascending"

_PLAN_CONTRACT: Final = "quant_data.stage10_history_extension_plan"
_BASE_CONTRACT: Final = "quant_data.stage10_history_extension_verified_base"
_MANIFEST_CONTRACT: Final = "quant_data.stage10_history_extension_manifest"
_RESUME_CONTRACT: Final = "quant_data.stage10_history_extension_resume"
_INTENT_CONTRACT: Final = "quant_data.stage10_history_extension_intent"
_RESULT_CONTRACT: Final = "quant_data.stage10_history_extension_result"
_ERROR_CONTRACT: Final = "quant_data.stage10_history_extension_error"
_ROOT_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_MAX_STATE_BYTES: Final = 768 * 1024
_MAX_RECEIPT_BYTES: Final = 16 * 1024
_MAX_RESULT_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9.^-]{1,32}\Z")
_INTENT_ID = re.compile(r"stage10_history_extension_intent_[0-9a-f]{32}\Z")
_SAFE_FILENAME = re.compile(r"stage10_history_extension_intent_[0-9a-f]{32}\.json\Z")

def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 history-extension {label} is invalid")
    return value


def _utc_text(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"Stage 10 history-extension {label} is invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    if canonical != value:
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")
    return value


# Compatibility aliases only: the canonical Stage 10 capture module owns the
# approved IDs and inclusive dates.  This runner must never reproduce them.
HistoryExtensionWindow = Stage10HistoryWindow
HISTORY_EXTENSION_WINDOWS: Final[tuple[Stage10HistoryWindow, ...]] = STAGE10_HISTORY_WINDOWS


def _window_primitive(window: Stage10HistoryWindow) -> dict[str, str]:
    return {
        "from": window.start_date,
        "id": window.window_id,
        "to": window.end_date,
    }


@dataclass(frozen=True, slots=True)
class VerifiedStage10Base:
    """Opaque-to-this-module proof supplied by future base verification.

    The callable seam is deliberately responsible for validating the actual
    base completion receipt and reconciliation.  This value then pins that
    proof into the extension plan without the skeleton re-opening or mutating
    the base private receipt.
    """

    target_profile_id: str
    scope_manifest_sha256: str
    registry_source_sha256: str
    completion_sha256: str
    reconciliation_sha256: str
    failure_manifest_sha256: str
    roster_symbols: tuple[str, ...]
    inherited_failed_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.target_profile_id != HISTORY_EXTENSION_TARGET_PROFILE_ID:
            raise ValidationError("Stage 10 history-extension base profile is invalid")
        for label, value in (
            ("base scope digest", self.scope_manifest_sha256),
            ("base registry digest", self.registry_source_sha256),
            ("base completion digest", self.completion_sha256),
            ("base reconciliation digest", self.reconciliation_sha256),
            ("base failure-manifest digest", self.failure_manifest_sha256),
        ):
            _require_sha256(value, label)
        roster = self.roster_symbols
        if (
            not isinstance(roster, tuple)
            or len(roster) != HISTORY_EXTENSION_EXPECTED_ROSTER_COUNT
            or any(not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None for symbol in roster)
            or roster != tuple(sorted(set(roster)))
            or "AAPL" not in roster
        ):
            raise ValidationError("Stage 10 history-extension base roster is invalid")
        failures = self.inherited_failed_symbols
        if (
            not isinstance(failures, tuple)
            or len(failures) != HISTORY_EXTENSION_EXPECTED_BASE_FAILURE_COUNT
            or any(not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None for symbol in failures)
            or failures != tuple(sorted(set(failures)))
            or not set(failures).issubset(roster)
        ):
            raise ValidationError("Stage 10 history-extension inherited failures are invalid")

    def to_primitive(self) -> dict[str, object]:
        return {
            "completion_sha256": self.completion_sha256,
            "failure_manifest_sha256": self.failure_manifest_sha256,
            "inherited_failed_symbols": list(self.inherited_failed_symbols),
            "reconciliation_sha256": self.reconciliation_sha256,
            "registry_source_sha256": self.registry_source_sha256,
            "roster_symbols": list(self.roster_symbols),
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
        }


@dataclass(frozen=True, slots=True)
class HistoryExtensionIntent:
    """One durable, issued-before-capture request declaration."""

    ordinal: int
    symbol: str
    window: Stage10HistoryWindow
    plan_sha256: str
    base_completion_sha256: str
    base_reconciliation_sha256: str

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValidationError("Stage 10 history-extension intent ordinal is invalid")
        if not isinstance(self.symbol, str) or _SYMBOL.fullmatch(self.symbol) is None:
            raise ValidationError("Stage 10 history-extension intent symbol is invalid")
        if not isinstance(self.window, Stage10HistoryWindow) or self.window not in STAGE10_HISTORY_WINDOWS:
            raise ValidationError("Stage 10 history-extension intent window is invalid")
        _require_sha256(self.plan_sha256, "intent plan digest")
        _require_sha256(self.base_completion_sha256, "intent base completion digest")
        _require_sha256(
            self.base_reconciliation_sha256, "intent base reconciliation digest"
        )

    @property
    def id(self) -> str:
        digest = _sha256_json(
            {
                "base_completion_sha256": self.base_completion_sha256,
                "base_reconciliation_sha256": self.base_reconciliation_sha256,
                "from": self.window.start_date,
                "ordinal": self.ordinal,
                "plan_sha256": self.plan_sha256,
                "symbol": self.symbol,
                "to": self.window.end_date,
                "window_id": self.window.window_id,
            }
        )
        return f"stage10_history_extension_intent_{digest[:32]}"

    @property
    def is_entitlement_sentinel(self) -> bool:
        return (
            self.ordinal == 1
            and self.symbol == "AAPL"
            and self.window == STAGE10_HISTORY_WINDOWS[0]
        )

    def material(self, *, issued_at: str) -> dict[str, object]:
        _parse_utc_text(issued_at, "intent issued_at")
        return {
            "base_completion_sha256": self.base_completion_sha256,
            "base_reconciliation_sha256": self.base_reconciliation_sha256,
            "from": self.window.start_date,
            "intent_id": self.id,
            "issued_at": issued_at,
            "ordinal": self.ordinal,
            "plan_sha256": self.plan_sha256,
            "symbol": self.symbol,
            "to": self.window.end_date,
            "window_id": self.window.window_id,
        }

    def issued_receipt(self, *, issued_at: str) -> dict[str, object]:
        material = self.material(issued_at=issued_at)
        return {
            "contract": _INTENT_CONTRACT,
            "intent_sha256": _sha256_json(material),
            **material,
            "version": HISTORY_EXTENSION_VERSION,
        }


def _scheduled_symbols(base: VerifiedStage10Base) -> tuple[str, ...]:
    """Place the entitlement sentinel first without dropping a base symbol."""

    return ("AAPL",) + tuple(
        symbol for symbol in base.roster_symbols if symbol != "AAPL"
    )


@dataclass(frozen=True, slots=True)
class HistoryExtensionPlan:
    """Canonical fixed window schedule bound to one verified base cohort."""

    base: VerifiedStage10Base
    intents: tuple[HistoryExtensionIntent, ...]
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.base, VerifiedStage10Base):
            raise ValidationError("Stage 10 history-extension plan base is invalid")
        _require_sha256(self.sha256, "plan digest")
        if (
            not isinstance(self.intents, tuple)
            or len(self.intents) != HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
            or any(not isinstance(intent, HistoryExtensionIntent) for intent in self.intents)
            or tuple(intent.ordinal for intent in self.intents)
            != tuple(range(1, HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT + 1))
            or not self.intents[0].is_entitlement_sentinel
            or any(intent.plan_sha256 != self.sha256 for intent in self.intents)
        ):
            raise ValidationError("Stage 10 history-extension plan is invalid")
        schedule_symbols = _scheduled_symbols(self.base)
        expected_requests = (
            (window, symbol)
            for window in HISTORY_EXTENSION_WINDOWS
            for symbol in schedule_symbols
        )
        if any(
            intent.window != window
            or intent.symbol != symbol
            or intent.base_completion_sha256 != self.base.completion_sha256
            or intent.base_reconciliation_sha256 != self.base.reconciliation_sha256
            for intent, (window, symbol) in zip(self.intents, expected_requests, strict=True)
        ):
            raise ValidationError("Stage 10 history-extension plan schedule is invalid")

    def material(self) -> dict[str, object]:
        return {
            "base": self.base.to_primitive(),
            "request_schedule": _REQUEST_SCHEDULE,
            "request_count": len(self.intents),
            "windows": [_window_primitive(window) for window in HISTORY_EXTENSION_WINDOWS],
        }

    def to_primitive(self) -> dict[str, object]:
        return {
            "contract": _PLAN_CONTRACT,
            **self.material(),
            "sha256": self.sha256,
            "version": HISTORY_EXTENSION_VERSION,
        }


def build_history_extension_plan(base: VerifiedStage10Base) -> HistoryExtensionPlan:
    """Build the fixed 8 x 629 plan without excluding base failed symbols."""

    if not isinstance(base, VerifiedStage10Base):
        raise ValidationError("Stage 10 history-extension base binding is invalid")
    material = {
        "base": base.to_primitive(),
        "request_schedule": _REQUEST_SCHEDULE,
        "request_count": HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT,
        "windows": [_window_primitive(window) for window in HISTORY_EXTENSION_WINDOWS],
    }
    digest = _sha256_json(material)
    schedule_symbols = _scheduled_symbols(base)
    intents = tuple(
        HistoryExtensionIntent(
            ordinal=ordinal,
            symbol=symbol,
            window=window,
            plan_sha256=digest,
            base_completion_sha256=base.completion_sha256,
            base_reconciliation_sha256=base.reconciliation_sha256,
        )
        for ordinal, (window, symbol) in enumerate(
            (
                (window, symbol)
                for window in HISTORY_EXTENSION_WINDOWS
                for symbol in schedule_symbols
            ),
            start=1,
        )
    )
    return HistoryExtensionPlan(base=base, intents=intents, sha256=digest)


@dataclass(frozen=True, slots=True)
class HistoryExtensionResultDraft:
    """Sanitized result supplied by an injected offline capture fake.

    This is intentionally metadata only.  Raw provider response bytes and
    parsed OHLCV values have no persistence path in the skeleton.
    """

    response_sha256: str
    response_byte_count: int
    row_count: int

    def __post_init__(self) -> None:
        _require_sha256(self.response_sha256, "result response digest")
        if (
            isinstance(self.response_byte_count, bool)
            or not isinstance(self.response_byte_count, int)
            or self.response_byte_count < 0
            or self.response_byte_count > _MAX_RESULT_RESPONSE_BYTES
            or isinstance(self.row_count, bool)
            or not isinstance(self.row_count, int)
            or self.row_count < 0
        ):
            raise ValidationError("Stage 10 history-extension result draft is invalid")

    @property
    def outcome(self) -> str:
        return "empty" if self.row_count == 0 else "rows_available"


@dataclass(frozen=True, slots=True)
class HistoryExtensionResume:
    """Compact resume cache; immutable intent/result files remain authoritative."""

    plan_sha256: str
    recorded_intent_ids: tuple[str, ...]
    sentinel_status: str

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "resume plan digest")
        if (
            not isinstance(self.recorded_intent_ids, tuple)
            or len(self.recorded_intent_ids) > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
            or any(
                not isinstance(value, str) or _INTENT_ID.fullmatch(value) is None
                for value in self.recorded_intent_ids
            )
            or self.recorded_intent_ids != tuple(sorted(set(self.recorded_intent_ids)))
            or not isinstance(self.sentinel_status, str)
            or self.sentinel_status not in {"pending", "rows_available", "empty"}
        ):
            raise ValidationError("Stage 10 history-extension resume is invalid")

    def to_primitive(self) -> dict[str, object]:
        return {
            "contract": _RESUME_CONTRACT,
            "plan_sha256": self.plan_sha256,
            "recorded_intent_ids": list(self.recorded_intent_ids),
            "sentinel_status": self.sentinel_status,
            "version": HISTORY_EXTENSION_VERSION,
        }


@dataclass(frozen=True, slots=True)
class HistoryExtensionRunReport:
    """Non-success report for an offline journal rehearsal only."""

    plan_sha256: str
    requests_issued: int
    recorded_request_count: int
    sentinel_status: str
    stop_reason: str

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "report plan digest")
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (self.requests_issued, self.recorded_request_count)
            )
            or self.recorded_request_count > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
            or not isinstance(self.sentinel_status, str)
            or self.sentinel_status not in {"pending", "rows_available", "empty"}
            or not isinstance(self.stop_reason, str)
            or self.stop_reason
            not in {
                "request_limit_reached",
                "sentinel_empty",
                "no_pending_requests",
            }
        ):
            raise ValidationError("Stage 10 history-extension report is invalid")

    @property
    def completion_claimed(self) -> bool:
        """The skeleton must never claim publishable/cohort completion."""

        return False

    def to_primitive(self) -> dict[str, object]:
        return {
            "completion_claimed": False,
            "mode": "offline_journal_rehearsal_not_publishable",
            "plan_sha256": self.plan_sha256,
            "recorded_request_count": self.recorded_request_count,
            "requests_issued": self.requests_issued,
            "sentinel_status": self.sentinel_status,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True, slots=True)
class _Layout:
    root: Path
    manifest: Path
    resume: Path
    run_lock: Path
    intents: Path
    results: Path


def _extension_root(target_root: Path) -> Path:
    if not isinstance(target_root, Path) or not target_root.is_absolute():
        raise ValidationError("Stage 10 history-extension target root is invalid")
    return target_root / HISTORY_EXTENSION_NAMESPACE


def _require_private_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is missing") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _ROOT_MODE
    ):
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")


def _require_private_file(path: Path, label: str, maximum_bytes: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _FILE_MODE
        or info.st_nlink != 1
        or info.st_size > maximum_bytes
    ):
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("Stage 10 history-extension private write failed")
        offset += written


def _write_json_exclusive(path: Path, value: Mapping[str, object], *, maximum_bytes: int) -> None:
    content = dumps_strict(dict(value), max_bytes=maximum_bytes).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, _FILE_MODE)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _write_json_replace(path: Path, value: Mapping[str, object], *, maximum_bytes: int) -> None:
    content = dumps_strict(dict(value), max_bytes=maximum_bytes).encode("utf-8")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.write"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    replaced = False
    try:
        descriptor = os.open(temporary, flags, _FILE_MODE)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not replaced:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _read_json(path: Path, label: str, maximum_bytes: int) -> Mapping[str, object]:
    _require_private_file(path, label, maximum_bytes)
    try:
        value = loads_strict(path.read_bytes(), max_bytes=maximum_bytes)
    except (OSError, ResourceLimitError, ValidationError) as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")
    return value


def _new_layout(target_root: Path) -> _Layout:
    """Create a test-only child namespace below an existing private candidate dir."""

    if target_root.is_symlink():
        raise ConflictError("Stage 10 history-extension target root is invalid")
    _require_private_directory(target_root, "target root")
    private = target_root / "private"
    candidate = private / "candidate"
    _require_private_directory(private, "private root")
    _require_private_directory(candidate, "candidate root")
    root = _extension_root(target_root)
    if root.exists() or root.is_symlink():
        raise ConflictError("Stage 10 history-extension namespace already exists")
    root.mkdir(mode=_ROOT_MODE)
    intents = root / "intents"
    results = root / "results"
    intents.mkdir(mode=_ROOT_MODE)
    results.mkdir(mode=_ROOT_MODE)
    run_lock = root / "run.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(run_lock, flags, _FILE_MODE)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)
    _fsync_directory(candidate)
    return _Layout(
        root=root,
        manifest=root / "manifest.json",
        resume=root / "resume.json",
        run_lock=run_lock,
        intents=intents,
        results=results,
    )


def _open_layout(target_root: Path) -> _Layout:
    root = _extension_root(target_root)
    layout = _Layout(
        root=root,
        manifest=root / "manifest.json",
        resume=root / "resume.json",
        run_lock=root / "run.lock",
        intents=root / "intents",
        results=root / "results",
    )
    _require_private_directory(target_root, "target root")
    _require_private_directory(target_root / "private", "private root")
    _require_private_directory(target_root / "private" / "candidate", "candidate root")
    _require_private_directory(layout.root, "extension namespace")
    _require_private_directory(layout.intents, "intent journal")
    _require_private_directory(layout.results, "result journal")
    _require_private_file(layout.run_lock, "run lock", 0)
    _require_private_file(layout.manifest, "manifest", _MAX_STATE_BYTES)
    _require_private_file(layout.resume, "resume", _MAX_STATE_BYTES)
    return layout


@contextmanager
def _acquire_run_lock(layout: _Layout) -> Iterator[None]:
    _require_private_file(layout.run_lock, "run lock", 0)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags)
    try:
        before = layout.run_lock.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or stat.S_IMODE(opened.st_mode) != _FILE_MODE
            or opened.st_size != 0
        ):
            raise ConflictError("Stage 10 history-extension run lock is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Stage 10 history-extension is already running") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _manifest_value(plan: HistoryExtensionPlan) -> dict[str, object]:
    material = {
        "base": plan.base.to_primitive(),
        "plan_sha256": plan.sha256,
        "request_count": len(plan.intents),
        "windows": [_window_primitive(window) for window in HISTORY_EXTENSION_WINDOWS],
    }
    return {
        "contract": _MANIFEST_CONTRACT,
        "manifest_sha256": _sha256_json(material),
        **material,
        "version": HISTORY_EXTENSION_VERSION,
    }


def _validate_manifest(value: Mapping[str, object], plan: HistoryExtensionPlan) -> None:
    expected = _manifest_value(plan)
    if dict(value) != expected:
        raise ConflictError("Stage 10 history-extension manifest does not match base proof")


def _resume_from_value(value: Mapping[str, object], plan: HistoryExtensionPlan) -> HistoryExtensionResume:
    required = {
        "contract",
        "plan_sha256",
        "recorded_intent_ids",
        "sentinel_status",
        "version",
    }
    if set(value) != required or value.get("contract") != _RESUME_CONTRACT or value.get("version") != HISTORY_EXTENSION_VERSION:
        raise ConflictError("Stage 10 history-extension resume is invalid")
    ids = value.get("recorded_intent_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ConflictError("Stage 10 history-extension resume is invalid")
    try:
        result = HistoryExtensionResume(
            plan_sha256=value["plan_sha256"],
            recorded_intent_ids=tuple(ids),
            sentinel_status=value["sentinel_status"],
        )
    except ValidationError as exc:
        raise ConflictError("Stage 10 history-extension resume is invalid") from exc
    if result.plan_sha256 != plan.sha256:
        raise ConflictError("Stage 10 history-extension resume plan binding is invalid")
    return result


def _intent_filename(intent: HistoryExtensionIntent) -> str:
    return f"{intent.id}.json"


def _result_filename(intent: HistoryExtensionIntent) -> str:
    return f"{intent.id}.json"


def _validate_intent_receipt(
    value: Mapping[str, object],
    intent: HistoryExtensionIntent,
) -> tuple[str, str]:
    material_fields = {
        "base_completion_sha256",
        "base_reconciliation_sha256",
        "from",
        "intent_id",
        "issued_at",
        "ordinal",
        "plan_sha256",
        "symbol",
        "to",
        "window_id",
    }
    required = material_fields | {"contract", "intent_sha256", "version"}
    if (
        set(value) != required
        or value.get("contract") != _INTENT_CONTRACT
        or value.get("version") != HISTORY_EXTENSION_VERSION
    ):
        raise ConflictError("Stage 10 history-extension intent is invalid")
    material = intent.material(issued_at=_parse_utc_text(value.get("issued_at"), "intent issued_at"))
    if any(value.get(key) != expected for key, expected in material.items()):
        raise ConflictError("Stage 10 history-extension intent binding is invalid")
    intent_sha256 = _require_sha256(value.get("intent_sha256"), "intent digest")
    if intent_sha256 != _sha256_json(material):
        raise ConflictError("Stage 10 history-extension intent digest is invalid")
    return str(value["issued_at"]), intent_sha256


def _result_material(
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
    draft: HistoryExtensionResultDraft,
    captured_at: str,
) -> dict[str, object]:
    _parse_utc_text(captured_at, "result captured_at")
    return {
        "captured_at": captured_at,
        "from": intent.window.start_date,
        "intent_id": intent.id,
        "intent_sha256": intent_sha256,
        "outcome": draft.outcome,
        "response_byte_count": draft.response_byte_count,
        "response_sha256": draft.response_sha256,
        "row_count": draft.row_count,
        "symbol": intent.symbol,
        "to": intent.window.end_date,
        "window_id": intent.window.window_id,
    }


def _validate_result_receipt(
    value: Mapping[str, object],
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
) -> str:
    material_fields = {
        "captured_at",
        "from",
        "intent_id",
        "intent_sha256",
        "outcome",
        "response_byte_count",
        "response_sha256",
        "row_count",
        "symbol",
        "to",
        "window_id",
    }
    required = material_fields | {"contract", "result_sha256", "version"}
    if (
        set(value) != required
        or value.get("contract") != _RESULT_CONTRACT
        or value.get("version") != HISTORY_EXTENSION_VERSION
    ):
        raise ConflictError("Stage 10 history-extension result is invalid")
    try:
        draft = HistoryExtensionResultDraft(
            response_sha256=value["response_sha256"],
            response_byte_count=value["response_byte_count"],
            row_count=value["row_count"],
        )
    except (KeyError, ValidationError) as exc:
        raise ConflictError("Stage 10 history-extension result is invalid") from exc
    captured_at = _parse_utc_text(value.get("captured_at"), "result captured_at")
    material = _result_material(
        intent,
        intent_sha256=intent_sha256,
        draft=draft,
        captured_at=captured_at,
    )
    if any(value.get(key) != expected for key, expected in material.items()):
        raise ConflictError("Stage 10 history-extension result binding is invalid")
    result_sha256 = _require_sha256(value.get("result_sha256"), "result digest")
    if result_sha256 != _sha256_json(material):
        raise ConflictError("Stage 10 history-extension result digest is invalid")
    return draft.outcome


def _journal_entries(directory: Path, label: str) -> tuple[Path, ...]:
    _require_private_directory(directory, label)
    entries = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    if len(entries) > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT:
        raise ConflictError(f"Stage 10 history-extension {label} exceeds the reviewed bound")
    for entry in entries:
        try:
            info = entry.lstat()
        except OSError as exc:
            raise ConflictError(f"Stage 10 history-extension {label} is unreadable") from exc
        if (
            _SAFE_FILENAME.fullmatch(entry.name) is None
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != _FILE_MODE
            or info.st_size > _MAX_RECEIPT_BYTES
        ):
            raise ConflictError(f"Stage 10 history-extension {label} contains an invalid entry")
    return entries


def _reconcile_journal(layout: _Layout, plan: HistoryExtensionPlan) -> HistoryExtensionResume:
    """Validate durable receipts and repair only a post-result resume lag.

    A crash after intent fsync but before a result is intentionally a terminal
    conflict: retrying would be an unreviewed duplicate provider request.  A
    crash after durable result fsync but before replacing resume can be repaired
    from the immutable result receipt without making a request.
    """

    intent_by_id = {intent.id: intent for intent in plan.intents}
    issued: dict[str, str] = {}
    for path in _journal_entries(layout.intents, "intent journal"):
        identifier = path.stem
        intent = intent_by_id.get(identifier)
        if intent is None:
            raise ConflictError("Stage 10 history-extension intent is outside the plan")
        _, digest = _validate_intent_receipt(
            _read_json(path, "intent receipt", _MAX_RECEIPT_BYTES), intent
        )
        issued[identifier] = digest

    recorded: dict[str, str] = {}
    for path in _journal_entries(layout.results, "result journal"):
        identifier = path.stem
        intent = intent_by_id.get(identifier)
        digest = issued.get(identifier)
        if intent is None or digest is None:
            raise ConflictError("Stage 10 history-extension result lacks an issued intent")
        recorded[identifier] = _validate_result_receipt(
            _read_json(path, "result receipt", _MAX_RECEIPT_BYTES),
            intent,
            intent_sha256=digest,
        )

    unresolved = sorted(set(issued) - set(recorded))
    if unresolved:
        raise ConflictError(
            "Stage 10 history-extension has an unresolved issued request; automatic retry is forbidden"
        )

    loaded = _resume_from_value(_read_json(layout.resume, "resume", _MAX_STATE_BYTES), plan)
    sentinel = plan.intents[0]
    sentinel_outcome = recorded.get(sentinel.id)
    expected_status = "pending" if sentinel_outcome is None else sentinel_outcome
    if expected_status not in {"pending", "rows_available", "empty"}:
        raise ConflictError("Stage 10 history-extension sentinel result is invalid")
    expected_ids = tuple(sorted(recorded))
    repaired = HistoryExtensionResume(
        plan_sha256=plan.sha256,
        recorded_intent_ids=expected_ids,
        sentinel_status=expected_status,
    )
    if loaded != repaired:
        _write_json_replace(layout.resume, repaired.to_primitive(), maximum_bytes=_MAX_STATE_BYTES)
    return repaired


def _initialize_or_open(target_root: Path, plan: HistoryExtensionPlan) -> _Layout:
    root = _extension_root(target_root)
    if root.exists() or root.is_symlink():
        layout = _open_layout(target_root)
        _validate_manifest(_read_json(layout.manifest, "manifest", _MAX_STATE_BYTES), plan)
        return layout
    layout = _new_layout(target_root)
    _write_json_exclusive(layout.manifest, _manifest_value(plan), maximum_bytes=_MAX_STATE_BYTES)
    _write_json_exclusive(
        layout.resume,
        HistoryExtensionResume(
            plan_sha256=plan.sha256,
            recorded_intent_ids=(),
            sentinel_status="pending",
        ).to_primitive(),
        maximum_bytes=_MAX_STATE_BYTES,
    )
    return layout


class _RequestPacer:
    """Conservatively enforce at least one second before every injected request."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], object],
        sleeper: Callable[[float], None],
    ) -> None:
        if not callable(monotonic) or not callable(sleeper):
            raise ValidationError("Stage 10 history-extension pacing bindings are invalid")
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_at: float | None = None

    @staticmethod
    def _seconds(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValidationError("Stage 10 history-extension monotonic clock is invalid")
        return float(value)

    def before_request(self) -> None:
        now = self._seconds(self._monotonic())
        if self._last_request_at is None:
            self._sleeper(HISTORY_EXTENSION_MINIMUM_INTERVAL_SECONDS)
            self._last_request_at = self._seconds(self._monotonic())
            return
        remaining = HISTORY_EXTENSION_MINIMUM_INTERVAL_SECONDS - (now - self._last_request_at)
        if remaining > 0:
            self._sleeper(remaining)
        observed = self._seconds(self._monotonic())
        if observed - self._last_request_at < HISTORY_EXTENSION_MINIMUM_INTERVAL_SECONDS:
            raise ValidationError("Stage 10 history-extension pacing did not advance")
        self._last_request_at = observed


def _write_intent(layout: _Layout, intent: HistoryExtensionIntent, *, now: Callable[[], object]) -> str:
    receipt = intent.issued_receipt(issued_at=_utc_text(now(), "intent issued_at"))
    _write_json_exclusive(
        layout.intents / _intent_filename(intent), receipt, maximum_bytes=_MAX_RECEIPT_BYTES
    )
    return str(receipt["intent_sha256"])


def _write_result(
    layout: _Layout,
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
    draft: HistoryExtensionResultDraft,
    now: Callable[[], object],
) -> str:
    material = _result_material(
        intent,
        intent_sha256=intent_sha256,
        draft=draft,
        captured_at=_utc_text(now(), "result captured_at"),
    )
    receipt = {
        "contract": _RESULT_CONTRACT,
        "result_sha256": _sha256_json(material),
        **material,
        "version": HISTORY_EXTENSION_VERSION,
    }
    _write_json_exclusive(
        layout.results / _result_filename(intent), receipt, maximum_bytes=_MAX_RECEIPT_BYTES
    )
    return draft.outcome


def run_offline_history_extension_rehearsal(
    target_root: Path,
    *,
    base_verifier: Callable[[Path], VerifiedStage10Base],
    capture: Callable[[HistoryExtensionIntent], HistoryExtensionResultDraft],
    max_requests: int,
    now: Callable[[], object],
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    offline_test_mode: bool = False,
) -> HistoryExtensionRunReport:
    """Exercise only private journal sequencing with an injected offline fake.

    ``offline_test_mode`` is intentionally mandatory.  It prevents a caller
    from accidentally treating this incomplete skeleton as a live runner.  No
    database or base Stage 10 receipt is opened or changed here.
    """

    if offline_test_mode is not True:
        raise CapabilityUnavailableError(
            "Stage 10 history-extension publication integration is unavailable",
            capability_id="stage10.history_extension.live_runner",
        )
    if not callable(base_verifier) or not callable(capture) or not callable(now):
        raise ValidationError("Stage 10 history-extension rehearsal bindings are invalid")
    if (
        isinstance(max_requests, bool)
        or not isinstance(max_requests, int)
        or max_requests < 1
        or max_requests > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
    ):
        raise ValidationError("Stage 10 history-extension request limit is invalid")
    base = base_verifier(target_root)
    if not isinstance(base, VerifiedStage10Base):
        raise ValidationError("Stage 10 history-extension base verifier is invalid")
    plan = build_history_extension_plan(base)
    layout = _initialize_or_open(target_root, plan)
    with _acquire_run_lock(layout):
        resume = _reconcile_journal(layout, plan)
        if resume.sentinel_status == "empty":
            return HistoryExtensionRunReport(
                plan_sha256=plan.sha256,
                requests_issued=0,
                recorded_request_count=len(resume.recorded_intent_ids),
                sentinel_status="empty",
                stop_reason="sentinel_empty",
            )
        recorded = set(resume.recorded_intent_ids)
        pacer = _RequestPacer(monotonic=monotonic, sleeper=sleeper)
        issued_count = 0
        for intent in plan.intents:
            if intent.id in recorded:
                continue
            # An intent is fsynced before pacing/capture.  Any crash or fake
            # failure afterwards is intentionally unretryable without an
            # operator-reviewed disposition.
            intent_sha256 = _write_intent(layout, intent, now=now)
            pacer.before_request()
            draft = capture(intent)
            if not isinstance(draft, HistoryExtensionResultDraft):
                raise ValidationError("Stage 10 history-extension capture result is invalid")
            outcome = _write_result(
                layout,
                intent,
                intent_sha256=intent_sha256,
                draft=draft,
                now=now,
            )
            issued_count += 1
            resume = _reconcile_journal(layout, plan)
            recorded = set(resume.recorded_intent_ids)
            if intent.is_entitlement_sentinel and outcome == "empty":
                return HistoryExtensionRunReport(
                    plan_sha256=plan.sha256,
                    requests_issued=issued_count,
                    recorded_request_count=len(recorded),
                    sentinel_status="empty",
                    stop_reason="sentinel_empty",
                )
            if issued_count >= max_requests:
                return HistoryExtensionRunReport(
                    plan_sha256=plan.sha256,
                    requests_issued=issued_count,
                    recorded_request_count=len(recorded),
                    sentinel_status=resume.sentinel_status,
                    stop_reason="request_limit_reached",
                )
        return HistoryExtensionRunReport(
            plan_sha256=plan.sha256,
            requests_issued=issued_count,
            recorded_request_count=len(recorded),
            sentinel_status=resume.sentinel_status,
            stop_reason="no_pending_requests",
        )


# Live runner implementation.  The original rehearsal API above remains an
# offline-only seam; this namespace owns a separate journal format so a prior
# rehearsal can never be mistaken for live evidence.
_LIVE_VERSION: Final = "2.0.0"
_LIVE_DIRECTORY: Final = "live"
_LIVE_MANIFEST: Final = "manifest.json"
_LIVE_RESUME: Final = "resume.json"
_LIVE_LOCK: Final = "run.lock"
_LIVE_INTENTS: Final = "intents"
_LIVE_SPOOLS: Final = "spools"
_LIVE_RAW: Final = "raw"
_LIVE_RESULTS: Final = "results"
_LIVE_COMPLETION: Final = "completion.json"
_LIVE_CONTRACT: Final = "quant_data.stage10_history_extension_live"
_LIVE_MANIFEST_CONTRACT: Final = "quant_data.stage10_history_extension_live_manifest"
_LIVE_RESUME_CONTRACT: Final = "quant_data.stage10_history_extension_live_resume"
_LIVE_SPOOL_CONTRACT: Final = "quant_data.stage10_history_extension_live_spool"
_LIVE_RESULT_CONTRACT: Final = "quant_data.stage10_history_extension_live_result"
_LIVE_COMPLETION_CONTRACT: Final = "quant_data.stage10_history_extension_live_completion"
_LIVE_ERROR_CONTRACT: Final = "quant_data.stage10_history_extension_live_error"
_LIVE_SAFE_RAW = re.compile(r"stage10_history_extension_intent_[0-9a-f]{32}\.raw\Z")
_LIVE_TERMINAL_STATUS_REASON: Final[dict[int, str]] = {
    404: "provider_not_found",
    410: "provider_unsupported",
    422: "provider_application_error",
}


@dataclass(frozen=True, slots=True)
class Stage10HistoryExtensionBaseProof:
    """Read-only binding to the completed base Stage 10 cohort."""

    verified_base: VerifiedStage10Base
    store_map: object
    registry: object
    scope: object
    base_reconciliation: object
    cohort_registry: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verified_base, VerifiedStage10Base):
            raise ValidationError("Stage 10 history-extension base proof is invalid")
        if (
            self.store_map is None
            or self.registry is None
            or self.scope is None
            or self.base_reconciliation is None
            or getattr(self.base_reconciliation, "sha256", None)
            != self.verified_base.reconciliation_sha256
        ):
            raise ValidationError("Stage 10 history-extension base proof is invalid")


@dataclass(frozen=True, slots=True)
class Stage10HistoryExtensionBindings:
    """Explicit dependencies for an offline rehearsal of the live runner."""

    base_verifier: Callable[[Path, Path], Stage10HistoryExtensionBaseProof]
    transport_factory: Callable[[str, object, object], object]
    importer_factory: Callable[[object, object, object], object]

    def __post_init__(self) -> None:
        if any(
            not callable(value)
            for value in (
                self.base_verifier,
                self.transport_factory,
                self.importer_factory,
            )
        ):
            raise ValidationError("Stage 10 history-extension live bindings are invalid")


@dataclass(frozen=True, slots=True)
class LiveHistoryExtensionRunReport:
    """Path-free progress report for the manual extension runner."""

    plan_sha256: str
    requests_issued: int
    closed_request_count: int
    terminal_failure_count: int
    sentinel_status: str
    completion: str
    failed_windows: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "live report plan digest")
        for value in (
            self.requests_issued,
            self.closed_request_count,
            self.terminal_failure_count,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError("Stage 10 history-extension live report is invalid")
        if (
            self.closed_request_count > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
            or self.terminal_failure_count > self.closed_request_count
            or self.sentinel_status not in {"pending", "complete", "complete_empty", "terminal_failure"}
            or self.completion not in {"partial", "sentinel_failed", "candidate_only", "finalized"}
            or not isinstance(self.failed_windows, tuple)
            or any(not isinstance(item, Mapping) for item in self.failed_windows)
        ):
            raise ValidationError("Stage 10 history-extension live report is invalid")

    def to_primitive(self) -> dict[str, object]:
        return {
            "candidate_state": "candidate_only_no_operational_promotion",
            "closed_request_count": self.closed_request_count,
            "completion": self.completion,
            "contract": _LIVE_CONTRACT,
            "failed_windows": [dict(item) for item in self.failed_windows],
            "plan_sha256": self.plan_sha256,
            "requests_issued": self.requests_issued,
            "sentinel_status": self.sentinel_status,
            "terminal_failure_count": self.terminal_failure_count,
            "version": _LIVE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class _LiveLayout:
    root: Path
    manifest: Path
    resume: Path
    run_lock: Path
    intents: Path
    spools: Path
    raw: Path
    results: Path
    backup: Path
    restored: Path
    candidate: Path
    completion: Path


def _live_root(target_root: Path) -> Path:
    return _extension_root(target_root) / _LIVE_DIRECTORY


def _live_layout(target_root: Path) -> _LiveLayout:
    root = _live_root(target_root)
    return _LiveLayout(
        root=root,
        manifest=root / _LIVE_MANIFEST,
        resume=root / _LIVE_RESUME,
        run_lock=root / _LIVE_LOCK,
        intents=root / _LIVE_INTENTS,
        spools=root / _LIVE_SPOOLS,
        raw=root / _LIVE_RAW,
        results=root / _LIVE_RESULTS,
        backup=root / "backup",
        restored=root / "restored",
        candidate=root / "candidate",
        completion=root / _LIVE_COMPLETION,
    )


def _create_live_layout(
    target_root: Path,
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> _LiveLayout:
    _require_private_directory(target_root, "target root")
    _require_private_directory(target_root / "private", "private root")
    _require_private_directory(target_root / "private" / "candidate", "candidate root")
    extension_root = _extension_root(target_root)
    if extension_root.exists() or extension_root.is_symlink():
        raise ConflictError("Stage 10 history-extension namespace already exists")
    extension_root.mkdir(mode=_ROOT_MODE)
    root = extension_root / _LIVE_DIRECTORY
    root.mkdir(mode=_ROOT_MODE)
    layout = _live_layout(target_root)
    for directory in (layout.intents, layout.spools, layout.raw, layout.results):
        directory.mkdir(mode=_ROOT_MODE)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags, _FILE_MODE)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _write_json_exclusive(
        layout.manifest,
        _live_manifest(plan, execution_revision),
        maximum_bytes=_MAX_STATE_BYTES,
    )
    _write_json_exclusive(
        layout.resume,
        _live_resume_value(plan, ()),
        maximum_bytes=_MAX_STATE_BYTES,
    )
    _fsync_directory(layout.root)
    _fsync_directory(extension_root)
    return layout


def _open_live_layout(
    target_root: Path,
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> _LiveLayout:
    layout = _live_layout(target_root)
    _require_private_directory(target_root, "target root")
    _require_private_directory(target_root / "private", "private root")
    _require_private_directory(target_root / "private" / "candidate", "candidate root")
    _require_private_directory(_extension_root(target_root), "extension namespace")
    _require_private_directory(layout.root, "live extension namespace")
    for path, label in (
        (layout.intents, "live intent journal"),
        (layout.spools, "live spool journal"),
        (layout.raw, "live raw journal"),
        (layout.results, "live result journal"),
    ):
        _require_private_directory(path, label)
    _require_private_file(layout.manifest, "live manifest", _MAX_STATE_BYTES)
    _require_private_file(layout.resume, "live resume", _MAX_STATE_BYTES)
    _require_private_file(layout.run_lock, "live run lock", 0)
    manifest_value = _read_json(layout.manifest, "live manifest", _MAX_STATE_BYTES)
    try:
        _validate_live_manifest(manifest_value, plan, execution_revision)
    except ConflictError:
        successor = _read_execution_transition_empty_chain(
            layout,
            plan,
            execution_revision,
        )
        if successor is None:
            transition = _read_execution_transition_empty(
                layout,
                plan,
                execution_revision,
                manifest_value=manifest_value,
            )
            if transition is None and _read_execution_transition_402(
                layout,
                plan,
                execution_revision,
                manifest_value=manifest_value,
            ) is None:
                raise
    return layout


def _initialize_or_open_live_layout(
    target_root: Path,
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> _LiveLayout:
    root = _live_root(target_root)
    if root.exists() or root.is_symlink():
        return _open_live_layout(target_root, plan, execution_revision)
    if _extension_root(target_root).exists() or _extension_root(target_root).is_symlink():
        raise ConflictError("Stage 10 history-extension namespace is not a live journal")
    return _create_live_layout(target_root, plan, execution_revision)


def _live_manifest(
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> dict[str, object]:
    _require_sha256(execution_revision, "execution source digest")
    material = {
        "base": plan.base.to_primitive(),
        "execution_revision": execution_revision,
        "plan_sha256": plan.sha256,
        "request_count": len(plan.intents),
        "request_schedule": _REQUEST_SCHEDULE,
        "windows": [_window_primitive(window) for window in HISTORY_EXTENSION_WINDOWS],
    }
    return {
        "contract": _LIVE_MANIFEST_CONTRACT,
        "manifest_sha256": _sha256_json(material),
        **material,
        "version": _LIVE_VERSION,
    }


def _validate_live_manifest(
    value: Mapping[str, object],
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> None:
    if dict(value) != _live_manifest(plan, execution_revision):
        raise ConflictError("Stage 10 history-extension live manifest does not match base proof")


def _transition_402_path(layout: _LiveLayout) -> Path:
    return layout.root / TRANSITION_402_FILENAME


def _transition_402_intent(plan: HistoryExtensionPlan) -> HistoryExtensionIntent:
    if (
        plan.sha256 != TRANSITION_402_PLAN_SHA256
        or len(plan.intents) <= TRANSITION_402_PENDING_ORDINAL - 1
    ):
        raise ConflictError("Stage 10 history-extension transition plan is invalid")
    intent = plan.intents[TRANSITION_402_PENDING_ORDINAL - 1]
    if (
        intent.ordinal != TRANSITION_402_PENDING_ORDINAL
        or intent.symbol != TRANSITION_402_PENDING_SYMBOL
        or intent.window.window_id != TRANSITION_402_PENDING_WINDOW_ID
        or intent.window.start_date != TRANSITION_402_PENDING_FROM
        or intent.window.end_date != TRANSITION_402_PENDING_TO
    ):
        raise ConflictError("Stage 10 history-extension transition intent is invalid")
    return intent


def _authorized_402_response_matches(
    spool: _LiveSpool,
    body: bytes,
) -> bool:
    """Match only the exact entitlement response authorized by the transition."""

    return (
        spool.status == TRANSITION_402_HTTP_STATUS
        and spool.content_type == TRANSITION_402_CONTENT_TYPE
        and spool.response_sha256 == TRANSITION_402_RESPONSE_SHA256
        and spool.response_byte_count == TRANSITION_402_RESPONSE_BYTE_COUNT
        and len(body) == TRANSITION_402_RESPONSE_BYTE_COUNT
        and hashlib.sha256(body).hexdigest() == TRANSITION_402_RESPONSE_SHA256
    )


def _transition_402_material(
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    _require_sha256(new_execution_revision, "transition execution source digest")
    intent = _transition_402_intent(plan)
    return {
        "authorization": TRANSITION_402_AUTHORIZATION,
        "closed_request_count": TRANSITION_402_CLOSED_REQUEST_COUNT,
        "new_execution_revision": new_execution_revision,
        "old_execution_revision": TRANSITION_402_OLD_EXECUTION_REVISION,
        "pending": {
            "from": TRANSITION_402_PENDING_FROM,
            "intent_id": intent.id,
            "intent_sha256": TRANSITION_402_PENDING_INTENT_SHA256,
            "ordinal": TRANSITION_402_PENDING_ORDINAL,
            "symbol": TRANSITION_402_PENDING_SYMBOL,
            "to": TRANSITION_402_PENDING_TO,
            "window_id": TRANSITION_402_PENDING_WINDOW_ID,
        },
        "plan_sha256": TRANSITION_402_PLAN_SHA256,
        "response": {
            "content_type": TRANSITION_402_CONTENT_TYPE,
            "http_status": TRANSITION_402_HTTP_STATUS,
            "response_byte_count": TRANSITION_402_RESPONSE_BYTE_COUNT,
            "response_sha256": TRANSITION_402_RESPONSE_SHA256,
        },
        "terminal_reason": TRANSITION_402_TERMINAL_REASON,
    }


def _transition_402_receipt(
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract": TRANSITION_402_CONTRACT,
        **_transition_402_material(plan, new_execution_revision),
        "version": TRANSITION_402_VERSION,
    }
    receipt["transition_sha256"] = _sha256_json(receipt)
    return receipt


def _validate_transition_402_source_evidence(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
) -> tuple[HistoryExtensionIntent, _LiveSpool, bytes]:
    manifest_value = _read_json(layout.manifest, "live manifest", _MAX_STATE_BYTES)
    _validate_live_manifest(
        manifest_value,
        plan,
        TRANSITION_402_OLD_EXECUTION_REVISION,
    )
    intent = _transition_402_intent(plan)
    intent_sha256 = _read_live_intent(layout, intent)
    if intent_sha256 != TRANSITION_402_PENDING_INTENT_SHA256:
        raise ConflictError("Stage 10 history-extension transition intent digest differs")
    recovered = _read_live_spool(
        layout,
        intent,
        intent_sha256=TRANSITION_402_PENDING_INTENT_SHA256,
    )
    if recovered is None:
        raise ConflictError("Stage 10 history-extension transition spool is missing")
    spool, body = recovered
    if not _authorized_402_response_matches(spool, body):
        raise ConflictError("Stage 10 history-extension transition spool differs")
    result = _read_live_result(
        layout,
        intent,
        intent_sha256=TRANSITION_402_PENDING_INTENT_SHA256,
    )
    if result is not None and (
        result.get("disposition") != "terminal_failure"
        or result.get("terminal_reason") != TRANSITION_402_TERMINAL_REASON
        or result.get("http_status") != TRANSITION_402_HTTP_STATUS
        or result.get("content_type") != TRANSITION_402_CONTENT_TYPE
        or result.get("response_sha256") != TRANSITION_402_RESPONSE_SHA256
        or result.get("response_byte_count") != TRANSITION_402_RESPONSE_BYTE_COUNT
        or result.get("row_count") != 0
    ):
        raise ConflictError("Stage 10 history-extension transition result differs")
    return intent, spool, body


def _read_execution_transition_402(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
    *,
    manifest_value: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    path = _transition_402_path(layout)
    if not path.exists() and not path.is_symlink():
        return None
    _require_immutable_live_file(path, "execution transition receipt", _MAX_RECEIPT_BYTES)
    value = _read_json(path, "execution transition receipt", _MAX_RECEIPT_BYTES)
    expected = _transition_402_receipt(plan, new_execution_revision)
    if dict(value) != expected:
        raise ConflictError("Stage 10 history-extension execution transition is invalid")
    if manifest_value is not None:
        _validate_live_manifest(
            manifest_value,
            plan,
            TRANSITION_402_OLD_EXECUTION_REVISION,
        )
    _validate_transition_402_source_evidence(layout, plan)
    return value


def _transition_empty_path(layout: _LiveLayout) -> Path:
    return layout.root / TRANSITION_EMPTY_FILENAME


def _transition_empty_intent(plan: HistoryExtensionPlan) -> HistoryExtensionIntent:
    if (
        plan.sha256 != TRANSITION_EMPTY_PLAN_SHA256
        or len(plan.intents) <= TRANSITION_EMPTY_PENDING_ORDINAL - 1
    ):
        raise ConflictError(
            "Stage 10 history-extension empty transition plan is invalid"
        )
    intent = plan.intents[TRANSITION_EMPTY_PENDING_ORDINAL - 1]
    if (
        intent.ordinal != TRANSITION_EMPTY_PENDING_ORDINAL
        or intent.symbol != TRANSITION_EMPTY_PENDING_SYMBOL
        or intent.window.window_id != TRANSITION_EMPTY_PENDING_WINDOW_ID
        or intent.window.start_date != TRANSITION_EMPTY_PENDING_FROM
        or intent.window.end_date != TRANSITION_EMPTY_PENDING_TO
    ):
        raise ConflictError(
            "Stage 10 history-extension empty transition intent is invalid"
        )
    return intent


def _authorized_known_listed_empty_response_matches(
    spool: _LiveSpool,
    body: bytes,
) -> bool:
    """Match only the exact retained HTTP 200 JSON empty-array response."""

    return (
        spool.status == TRANSITION_EMPTY_HTTP_STATUS
        and spool.content_type == TRANSITION_EMPTY_CONTENT_TYPE
        and spool.response_sha256 == TRANSITION_EMPTY_RESPONSE_SHA256
        and spool.response_byte_count == TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
        and len(body) == TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
        and hashlib.sha256(body).hexdigest() == TRANSITION_EMPTY_RESPONSE_SHA256
    )


def _transition_empty_prefix_binding(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
) -> tuple[str, str]:
    """Hash the exact authenticated prefix ledger and raw-sidecar manifest."""

    records = _reconcile_live_journal(
        layout,
        plan,
        repair_resume=False,
    )
    prefix = plan.intents[:TRANSITION_EMPTY_CLOSED_REQUEST_COUNT]
    expected_ids = {intent.id for intent in prefix}
    if not expected_ids.issubset(records):
        raise ConflictError(
            "Stage 10 history-extension empty transition prefix is incomplete"
        )
    ledger: list[Mapping[str, object]] = []
    sidecars: list[dict[str, object]] = []
    for intent in prefix:
        record = records[intent.id]
        intent_sha256 = _read_live_intent(layout, intent)
        if intent_sha256 is None:
            raise ConflictError(
                "Stage 10 history-extension empty transition intent is missing"
            )
        recovered = _read_live_spool(
            layout,
            intent,
            intent_sha256=intent_sha256,
        )
        if recovered is None:
            raise ConflictError(
                "Stage 10 history-extension empty transition sidecar is missing"
            )
        spool, body = recovered
        _validate_result_spool_binding(record, spool, body)
        ledger.append(_ledger_record(record))
        sidecars.append(
            {
                "intent_sha256": intent_sha256,
                "response_sha256": spool.response_sha256,
                "response_byte_count": spool.response_byte_count,
            }
        )
    return _sha256_json(ledger), _sha256_json(sidecars)


def _transition_empty_material(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    _require_sha256(new_execution_revision, "empty transition execution source digest")
    intent = _transition_empty_intent(plan)
    prefix_ledger_sha256, prefix_raw_sidecar_manifest_sha256 = (
        _transition_empty_prefix_binding(layout, plan)
    )
    return {
        "authorization": TRANSITION_EMPTY_AUTHORIZATION,
        "closed_request_count": TRANSITION_EMPTY_CLOSED_REQUEST_COUNT,
        "new_execution_revision": new_execution_revision,
        "old_execution_revision": TRANSITION_EMPTY_OLD_EXECUTION_REVISION,
        "prior_transition_sha256": TRANSITION_EMPTY_PRIOR_TRANSITION_SHA256,
        "pending": {
            "from": TRANSITION_EMPTY_PENDING_FROM,
            "intent_id": intent.id,
            "intent_sha256": TRANSITION_EMPTY_PENDING_INTENT_SHA256,
            "ordinal": TRANSITION_EMPTY_PENDING_ORDINAL,
            "symbol": TRANSITION_EMPTY_PENDING_SYMBOL,
            "to": TRANSITION_EMPTY_PENDING_TO,
            "window_id": TRANSITION_EMPTY_PENDING_WINDOW_ID,
        },
        "plan_sha256": TRANSITION_EMPTY_PLAN_SHA256,
        "prefix_ledger_sha256": prefix_ledger_sha256,
        "prefix_raw_sidecar_manifest_sha256": prefix_raw_sidecar_manifest_sha256,
        "response": {
            "content_type": TRANSITION_EMPTY_CONTENT_TYPE,
            "http_status": TRANSITION_EMPTY_HTTP_STATUS,
            "response_byte_count": TRANSITION_EMPTY_RESPONSE_BYTE_COUNT,
            "response_sha256": TRANSITION_EMPTY_RESPONSE_SHA256,
        },
        "terminal_reason": TRANSITION_EMPTY_TERMINAL_REASON,
    }


def _transition_empty_receipt(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract": TRANSITION_EMPTY_CONTRACT,
        **_transition_empty_material(layout, plan, new_execution_revision),
        "version": TRANSITION_EMPTY_VERSION,
    }
    receipt["transition_sha256"] = _sha256_json(receipt)
    return receipt


def _validate_transition_empty_source_evidence(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
) -> tuple[HistoryExtensionIntent, _LiveSpool, bytes]:
    manifest_value = _read_json(layout.manifest, "live manifest", _MAX_STATE_BYTES)
    _validate_live_manifest(
        manifest_value,
        plan,
        TRANSITION_402_OLD_EXECUTION_REVISION,
    )
    prior = _read_execution_transition_402(
        layout,
        plan,
        TRANSITION_EMPTY_OLD_EXECUTION_REVISION,
        manifest_value=manifest_value,
    )
    if (
        prior is None
        or prior.get("transition_sha256")
        != TRANSITION_EMPTY_PRIOR_TRANSITION_SHA256
    ):
        raise ConflictError(
            "Stage 10 history-extension prior transition binding differs"
        )
    intent = _transition_empty_intent(plan)
    intent_sha256 = _read_live_intent(layout, intent)
    if intent_sha256 != TRANSITION_EMPTY_PENDING_INTENT_SHA256:
        raise ConflictError(
            "Stage 10 history-extension empty transition intent digest differs"
        )
    recovered = _read_live_spool(
        layout,
        intent,
        intent_sha256=TRANSITION_EMPTY_PENDING_INTENT_SHA256,
    )
    if recovered is None:
        raise ConflictError(
            "Stage 10 history-extension empty transition spool is missing"
        )
    spool, body = recovered
    if not _authorized_known_listed_empty_response_matches(spool, body):
        raise ConflictError(
            "Stage 10 history-extension empty transition spool differs"
        )
    result = _read_live_result(
        layout,
        intent,
        intent_sha256=TRANSITION_EMPTY_PENDING_INTENT_SHA256,
    )
    if result is not None and (
        result.get("disposition") != "terminal_failure"
        or result.get("terminal_reason") != TRANSITION_EMPTY_TERMINAL_REASON
        or result.get("http_status") != TRANSITION_EMPTY_HTTP_STATUS
        or result.get("content_type") != TRANSITION_EMPTY_CONTENT_TYPE
        or result.get("response_sha256") != TRANSITION_EMPTY_RESPONSE_SHA256
        or result.get("response_byte_count") != TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
        or result.get("row_count") != 0
    ):
        raise ConflictError(
            "Stage 10 history-extension empty transition result differs"
        )
    return intent, spool, body


def _read_execution_transition_empty(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
    *,
    manifest_value: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    path = _transition_empty_path(layout)
    if not path.exists() and not path.is_symlink():
        return None
    _require_immutable_live_file(
        path, "empty execution transition receipt", _MAX_RECEIPT_BYTES
    )
    value = _read_json(
        path, "empty execution transition receipt", _MAX_RECEIPT_BYTES
    )
    expected = _transition_empty_receipt(layout, plan, new_execution_revision)
    if dict(value) != expected:
        raise ConflictError(
            "Stage 10 history-extension empty execution transition is invalid"
        )
    if manifest_value is not None:
        _validate_live_manifest(
            manifest_value,
            plan,
            TRANSITION_402_OLD_EXECUTION_REVISION,
        )
    _validate_transition_empty_source_evidence(layout, plan)
    return value


def _transition_empty_chain_path(layout: _LiveLayout) -> Path:
    return layout.root / TRANSITION_CHAIN_FILENAME


def _transition_empty_chain_source(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
) -> Mapping[str, object]:
    manifest = _read_json(layout.manifest, "live manifest", _MAX_STATE_BYTES)
    if (
        manifest.get("execution_revision") != TRANSITION_CHAIN_LIVE_EXECUTION_REVISION
        or manifest.get("manifest_sha256") != TRANSITION_CHAIN_LIVE_MANIFEST_SHA256
    ):
        raise ConflictError(
            "Stage 10 history-extension successor transition manifest differs"
        )
    _validate_live_manifest(
        manifest,
        plan,
        TRANSITION_CHAIN_LIVE_EXECUTION_REVISION,
    )
    prior = _read_execution_transition_402(
        layout,
        plan,
        TRANSITION_EMPTY_OLD_EXECUTION_REVISION,
        manifest_value=manifest,
    )
    empty = _read_execution_transition_empty(
        layout,
        plan,
        TRANSITION_CHAIN_OLD_EXECUTION_REVISION,
        manifest_value=manifest,
    )
    if (
        prior is None
        or prior.get("transition_sha256") != TRANSITION_CHAIN_402_SHA256
        or empty is None
        or empty.get("transition_sha256") != TRANSITION_CHAIN_EMPTY_SHA256
    ):
        raise ConflictError(
            "Stage 10 history-extension successor authorization chain differs"
        )
    prefix_ledger_sha256, prefix_raw_sha256 = _transition_empty_prefix_binding(
        layout,
        plan,
    )
    if (
        prefix_ledger_sha256 != TRANSITION_CHAIN_PREFIX_LEDGER_SHA256
        or prefix_raw_sha256 != TRANSITION_CHAIN_PREFIX_RAW_SHA256
    ):
        raise ConflictError(
            "Stage 10 history-extension successor transition prefix differs"
        )
    intent, spool, body = _validate_transition_empty_source_evidence(layout, plan)
    if (
        plan.sha256 != TRANSITION_CHAIN_PLAN_SHA256
        or intent.ordinal != TRANSITION_CHAIN_PENDING_ORDINAL
        or intent.symbol != TRANSITION_CHAIN_PENDING_SYMBOL
        or intent.window.window_id != TRANSITION_CHAIN_PENDING_WINDOW_ID
        or intent.window.start_date != TRANSITION_CHAIN_PENDING_FROM
        or intent.window.end_date != TRANSITION_CHAIN_PENDING_TO
        or _read_live_intent(layout, intent) != TRANSITION_CHAIN_PENDING_INTENT_SHA256
        or spool.status != TRANSITION_CHAIN_PENDING_HTTP_STATUS
        or spool.content_type != TRANSITION_CHAIN_PENDING_CONTENT_TYPE
        or spool.response_sha256 != TRANSITION_CHAIN_PENDING_RESPONSE_SHA256
        or spool.response_byte_count != TRANSITION_CHAIN_PENDING_RESPONSE_BYTE_COUNT
        or len(body) != TRANSITION_CHAIN_PENDING_RESPONSE_BYTE_COUNT
        or hashlib.sha256(body).hexdigest() != TRANSITION_CHAIN_PENDING_RESPONSE_SHA256
    ):
        raise ConflictError(
            "Stage 10 history-extension successor transition evidence differs"
        )
    try:
        return build_stage10_history_extension_authorization_chain(prior, empty)
    except ValueError as exc:
        raise ConflictError(
            "Stage 10 history-extension successor authorization chain is invalid"
        ) from exc


def _transition_empty_chain_material(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    _require_sha256(new_execution_revision, "successor execution source digest")
    authorization_chain = _transition_empty_chain_source(layout, plan)
    intent = _transition_empty_intent(plan)
    return {
        "authorization": TRANSITION_CHAIN_AUTHORIZATION,
        "authorization_chain": dict(authorization_chain),
        "closed_request_count": TRANSITION_CHAIN_CLOSED_REQUEST_COUNT,
        "live_manifest_sha256": TRANSITION_CHAIN_LIVE_MANIFEST_SHA256,
        "new_execution_revision": new_execution_revision,
        "old_execution_revision": TRANSITION_CHAIN_OLD_EXECUTION_REVISION,
        "pending": {
            "from": TRANSITION_CHAIN_PENDING_FROM,
            "intent_id": intent.id,
            "intent_sha256": TRANSITION_CHAIN_PENDING_INTENT_SHA256,
            "ordinal": TRANSITION_CHAIN_PENDING_ORDINAL,
            "symbol": TRANSITION_CHAIN_PENDING_SYMBOL,
            "to": TRANSITION_CHAIN_PENDING_TO,
            "window_id": TRANSITION_CHAIN_PENDING_WINDOW_ID,
        },
        "plan_sha256": TRANSITION_CHAIN_PLAN_SHA256,
        "prefix_ledger_sha256": TRANSITION_CHAIN_PREFIX_LEDGER_SHA256,
        "prefix_raw_sidecar_manifest_sha256": TRANSITION_CHAIN_PREFIX_RAW_SHA256,
        "response": {
            "content_type": TRANSITION_CHAIN_PENDING_CONTENT_TYPE,
            "http_status": TRANSITION_CHAIN_PENDING_HTTP_STATUS,
            "response_byte_count": TRANSITION_CHAIN_PENDING_RESPONSE_BYTE_COUNT,
            "response_sha256": TRANSITION_CHAIN_PENDING_RESPONSE_SHA256,
        },
        "terminal_reason": TRANSITION_EMPTY_TERMINAL_REASON,
    }


def _transition_empty_chain_receipt(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract": TRANSITION_CHAIN_CONTRACT,
        **_transition_empty_chain_material(layout, plan, new_execution_revision),
        "version": TRANSITION_CHAIN_VERSION,
    }
    receipt["transition_sha256"] = _sha256_json(receipt)
    return receipt


def _read_execution_transition_empty_chain(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    new_execution_revision: str,
) -> Mapping[str, object] | None:
    path = _transition_empty_chain_path(layout)
    if not path.exists() and not path.is_symlink():
        return None
    _require_immutable_live_file(
        path,
        "successor execution transition receipt",
        _MAX_RECEIPT_BYTES,
    )
    value = _read_json(
        path,
        "successor execution transition receipt",
        _MAX_RECEIPT_BYTES,
    )
    expected = _transition_empty_chain_receipt(
        layout,
        plan,
        new_execution_revision,
    )
    if dict(value) != expected:
        raise ConflictError(
            "Stage 10 history-extension successor execution transition is invalid"
        )
    return value


def _verified_live_authorization_proof(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    execution_revision: str,
) -> Mapping[str, object]:
    transition = _read_execution_transition_empty_chain(
        layout,
        plan,
        execution_revision,
    )
    if transition is None:
        raise ConflictError(
            "Stage 10 history-extension successor authorization is missing"
        )
    try:
        proof = build_stage10_history_extension_authorization_proof(
            transition,
        )
    except ValueError as exc:
        raise ConflictError(
            "Stage 10 history-extension successor authorization is invalid"
        ) from exc
    return proof


def _live_resume_value(
    plan: HistoryExtensionPlan,
    result_ids: tuple[str, ...],
) -> dict[str, object]:
    if result_ids != tuple(sorted(set(result_ids))):
        raise ValidationError("Stage 10 history-extension live resume is invalid")
    return {
        "contract": _LIVE_RESUME_CONTRACT,
        "plan_sha256": plan.sha256,
        "recorded_result_ids": list(result_ids),
        "version": _LIVE_VERSION,
    }


def _write_live_resume(layout: _LiveLayout, plan: HistoryExtensionPlan, result_ids: tuple[str, ...]) -> None:
    _write_json_replace(
        layout.resume,
        _live_resume_value(plan, result_ids),
        maximum_bytes=_MAX_STATE_BYTES,
    )


def _require_live_filename(path: Path, *, suffix: str, label: str, maximum_bytes: int) -> None:
    expected = path.stem
    if _INTENT_ID.fullmatch(expected) is None or path.suffix != suffix:
        raise ConflictError(f"Stage 10 history-extension {label} contains an invalid entry")
    _require_private_file(path, label, maximum_bytes)


@contextmanager
def _acquire_live_run_lock(layout: _LiveLayout) -> Iterator[None]:
    _require_private_file(layout.run_lock, "live run lock", 0)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(layout.run_lock, flags)
    try:
        before = layout.run_lock.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or stat.S_IMODE(opened.st_mode) != _FILE_MODE
            or opened.st_size != 0
        ):
            raise ConflictError("Stage 10 history-extension live run lock is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Stage 10 history-extension is already running") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

@dataclass(frozen=True, slots=True)
class _LiveSpool:
    intent_id: str
    intent_sha256: str
    status: int
    content_type: str
    response_sha256: str
    response_byte_count: int

    def __post_init__(self) -> None:
        if (
            _INTENT_ID.fullmatch(self.intent_id) is None
            or not isinstance(self.intent_sha256, str)
            or _SHA256.fullmatch(self.intent_sha256) is None
            or isinstance(self.status, bool)
            or not isinstance(self.status, int)
            or not 100 <= self.status <= 599
            or self.content_type not in {"application/json", "non_json"}
            or _SHA256.fullmatch(self.response_sha256) is None
            or isinstance(self.response_byte_count, bool)
            or not isinstance(self.response_byte_count, int)
            or self.response_byte_count < 1
            or self.response_byte_count > _MAX_RESULT_RESPONSE_BYTES
        ):
            raise ValidationError("Stage 10 history-extension live spool is invalid")

    def material(self) -> dict[str, object]:
        return {
            "content_type": self.content_type,
            "http_status": self.status,
            "intent_id": self.intent_id,
            "intent_sha256": self.intent_sha256,
            "response_byte_count": self.response_byte_count,
            "response_sha256": self.response_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {
            "contract": _LIVE_SPOOL_CONTRACT,
            **self.material(),
            "version": _LIVE_VERSION,
        }


def _safe_content_type(value: object) -> str:
    """Keep only a safe media-type classification, never raw provider text."""
    if not isinstance(value, str) or len(value) > 256:
        return "non_json"
    return "application/json" if value.split(";", 1)[0].strip().lower() == "application/json" else "non_json"


def _raw_path(layout: _LiveLayout, intent: HistoryExtensionIntent) -> Path:
    return layout.raw / f"{intent.id}.raw"


def _spool_path(layout: _LiveLayout, intent: HistoryExtensionIntent) -> Path:
    return layout.spools / f"{intent.id}.json"


def _live_intent_path(layout: _LiveLayout, intent: HistoryExtensionIntent) -> Path:
    return layout.intents / f"{intent.id}.json"


def _live_result_path(layout: _LiveLayout, intent: HistoryExtensionIntent) -> Path:
    return layout.results / f"{intent.id}.json"


def _write_bytes_exclusive(path: Path, value: bytes, *, maximum_bytes: int) -> None:
    if not isinstance(value, bytes) or not value or len(value) > maximum_bytes:
        raise ValidationError("Stage 10 history-extension raw response is invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, _FILE_MODE)
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _require_raw_file(path: Path, label: str, maximum_bytes: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _FILE_MODE
        or info.st_nlink != 1
        or info.st_size < 1
        or info.st_size > maximum_bytes
    ):
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")


def _read_bytes(path: Path, label: str, maximum_bytes: int) -> bytes:
    _require_raw_file(path, label, maximum_bytes)
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ConflictError(f"Stage 10 history-extension {label} is unreadable") from exc
    if not value:
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")
    return value


def _write_live_intent(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
    *,
    now: Callable[[], object],
) -> str:
    path = _live_intent_path(layout, intent)
    if path.exists() or path.is_symlink():
        _, digest = _validate_intent_receipt(
            _read_json(path, "live intent receipt", _MAX_RECEIPT_BYTES), intent
        )
        return digest
    receipt = intent.issued_receipt(issued_at=_utc_text(now(), "intent issued_at"))
    _write_json_exclusive(path, receipt, maximum_bytes=_MAX_RECEIPT_BYTES)
    return str(receipt["intent_sha256"])


def _read_live_intent(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
) -> str | None:
    path = _live_intent_path(layout, intent)
    if not path.exists() and not path.is_symlink():
        return None
    _, digest = _validate_intent_receipt(
        _read_json(path, "live intent receipt", _MAX_RECEIPT_BYTES), intent
    )
    return digest


def _spool_response(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
    api_key: str,
    response: object,
) -> _LiveSpool:
    if not isinstance(response, object) or not hasattr(response, "body"):
        raise ValidationError("Stage 10 history-extension transport response is invalid")
    status = getattr(response, "status", None)
    content_type = _safe_content_type(getattr(response, "content_type", None))
    body = getattr(response, "body", None)
    if (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not isinstance(body, bytes)
        or not body
        or len(body) > _MAX_RESULT_RESPONSE_BYTES
    ):
        raise ValidationError("Stage 10 history-extension transport response is invalid")
    if api_key.encode("utf-8") in body or any(
        marker in body.lower()
        for marker in (b"api_key", b"apikey", b"authorization", b"bearer")
    ):
        raise ConflictError(
            "Stage 10 history-extension response may contain credential material"
        )
    spool = _LiveSpool(
        intent_id=intent.id,
        intent_sha256=intent_sha256,
        status=status,
        content_type=content_type,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_byte_count=len(body),
    )
    raw_path = _raw_path(layout, intent)
    spool_path = _spool_path(layout, intent)
    if raw_path.exists() or raw_path.is_symlink() or spool_path.exists() or spool_path.is_symlink():
        raise ConflictError("Stage 10 history-extension response spool already exists")
    # The body is fsynced before its metadata.  A crash between the files is a
    # durable no-retry conflict, never an excuse to issue the request again.
    _write_bytes_exclusive(raw_path, body, maximum_bytes=_MAX_RESULT_RESPONSE_BYTES)
    _write_json_exclusive(
        spool_path,
        spool.to_primitive(),
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    return spool


def _read_live_spool(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
) -> tuple[_LiveSpool, bytes] | None:
    raw_path = _raw_path(layout, intent)
    spool_path = _spool_path(layout, intent)
    raw_exists = raw_path.exists() or raw_path.is_symlink()
    metadata_exists = spool_path.exists() or spool_path.is_symlink()
    if not raw_exists and not metadata_exists:
        return None
    if raw_exists != metadata_exists:
        raise ConflictError(
            "Stage 10 history-extension has an incomplete response spool; automatic retry is forbidden"
        )
    value = _read_json(spool_path, "live spool metadata", _MAX_RECEIPT_BYTES)
    required = {
        "contract",
        "content_type",
        "http_status",
        "intent_id",
        "intent_sha256",
        "response_byte_count",
        "response_sha256",
        "version",
    }
    if (
        set(value) != required
        or value.get("contract") != _LIVE_SPOOL_CONTRACT
        or value.get("version") != _LIVE_VERSION
    ):
        raise ConflictError("Stage 10 history-extension live spool metadata is invalid")
    try:
        spool = _LiveSpool(
            intent_id=value["intent_id"],
            intent_sha256=value["intent_sha256"],
            status=value["http_status"],
            content_type=value["content_type"],
            response_sha256=value["response_sha256"],
            response_byte_count=value["response_byte_count"],
        )
    except (KeyError, ValidationError) as exc:
        raise ConflictError("Stage 10 history-extension live spool metadata is invalid") from exc
    if spool.intent_id != intent.id or spool.intent_sha256 != intent_sha256:
        raise ConflictError("Stage 10 history-extension live spool binding is invalid")
    body = _read_bytes(raw_path, "live raw response", _MAX_RESULT_RESPONSE_BYTES)
    if len(body) != spool.response_byte_count or hashlib.sha256(body).hexdigest() != spool.response_sha256:
        raise ConflictError("Stage 10 history-extension live raw response digest is invalid")
    return spool, body


def _live_result_material(
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
    disposition: str,
    response_sha256: str,
    response_byte_count: int,
    row_count: int,
    publication_receipt_sha256: str | None = None,
    semantic_identity: str | None = None,
    terminal_reason: str | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
) -> dict[str, object]:
    common: dict[str, object] = {
        "base_completion_sha256": intent.base_completion_sha256,
        "base_reconciliation_sha256": intent.base_reconciliation_sha256,
        "disposition": disposition,
        "from": intent.window.start_date,
        "intent_id": intent.id,
        "intent_sha256": intent_sha256,
        "ordinal": intent.ordinal,
        "query": intent.window.query(intent.symbol),
        "response_byte_count": response_byte_count,
        "response_sha256": response_sha256,
        "row_count": row_count,
        "symbol": intent.symbol,
        "to": intent.window.end_date,
        "window_id": intent.window.window_id,
    }
    if disposition == "complete":
        if publication_receipt_sha256 is None or semantic_identity is None:
            raise ValidationError("Stage 10 history-extension publication result is invalid")
        common["publication_receipt_sha256"] = publication_receipt_sha256
        common["semantic_identity"] = semantic_identity
    elif disposition == "complete_empty":
        if row_count != 0:
            raise ValidationError("Stage 10 history-extension empty result is invalid")
    elif disposition == "terminal_failure":
        standard_terminal = (
            terminal_reason in set(_LIVE_TERMINAL_STATUS_REASON.values())
            and http_status in _LIVE_TERMINAL_STATUS_REASON
            and _LIVE_TERMINAL_STATUS_REASON[http_status] == terminal_reason
            and content_type == "application/json"
            and row_count == 0
        )
        operator_terminal = (
            terminal_reason == TRANSITION_402_TERMINAL_REASON
            and http_status == TRANSITION_402_HTTP_STATUS
            and content_type == TRANSITION_402_CONTENT_TYPE
            and response_sha256 == TRANSITION_402_RESPONSE_SHA256
            and response_byte_count == TRANSITION_402_RESPONSE_BYTE_COUNT
            and row_count == 0
        )
        operator_known_listed_empty = (
            terminal_reason == TRANSITION_EMPTY_TERMINAL_REASON
            and http_status == TRANSITION_EMPTY_HTTP_STATUS
            and content_type == TRANSITION_EMPTY_CONTENT_TYPE
            and response_sha256 == TRANSITION_EMPTY_RESPONSE_SHA256
            and response_byte_count == TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
            and row_count == 0
        )
        if (
            not standard_terminal
            and not operator_terminal
            and not operator_known_listed_empty
        ):
            raise ValidationError("Stage 10 history-extension terminal result is invalid")
        common["terminal_reason"] = terminal_reason
        common["http_status"] = http_status
        common["content_type"] = content_type
    else:
        raise ValidationError("Stage 10 history-extension result disposition is invalid")
    return common


def _write_live_result(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
    material: Mapping[str, object],
) -> Mapping[str, object]:
    result_sha256 = _sha256_json(dict(material))
    receipt = {
        "contract": _LIVE_RESULT_CONTRACT,
        "result_sha256": result_sha256,
        **dict(material),
        "version": _LIVE_VERSION,
    }
    _write_json_exclusive(
        _live_result_path(layout, intent),
        receipt,
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    return receipt


def _read_live_result(
    layout: _LiveLayout,
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
) -> Mapping[str, object] | None:
    path = _live_result_path(layout, intent)
    if not path.exists() and not path.is_symlink():
        return None
    value = _read_json(path, "live result receipt", _MAX_RECEIPT_BYTES)
    required_common = {
        "base_completion_sha256",
        "base_reconciliation_sha256",
        "disposition",
        "from",
        "intent_id",
        "intent_sha256",
        "ordinal",
        "query",
        "response_byte_count",
        "response_sha256",
        "row_count",
        "symbol",
        "to",
        "window_id",
    }
    disposition = value.get("disposition")
    if disposition == "complete":
        material_fields = required_common | {"publication_receipt_sha256", "semantic_identity"}
    elif disposition == "complete_empty":
        material_fields = required_common
    elif disposition == "terminal_failure":
        material_fields = required_common | {"terminal_reason", "http_status", "content_type"}
    else:
        raise ConflictError("Stage 10 history-extension live result disposition is invalid")
    if (
        set(value) != material_fields | {"contract", "result_sha256", "version"}
        or value.get("contract") != _LIVE_RESULT_CONTRACT
        or value.get("version") != _LIVE_VERSION
    ):
        raise ConflictError("Stage 10 history-extension live result is invalid")
    try:
        material = _live_result_material(
            intent,
            intent_sha256=intent_sha256,
            disposition=disposition,
            response_sha256=value["response_sha256"],
            response_byte_count=value["response_byte_count"],
            row_count=value["row_count"],
            publication_receipt_sha256=value.get("publication_receipt_sha256"),
            semantic_identity=value.get("semantic_identity"),
            terminal_reason=value.get("terminal_reason"),
            http_status=value.get("http_status"),
            content_type=value.get("content_type"),
        )
    except (KeyError, ValidationError) as exc:
        raise ConflictError("Stage 10 history-extension live result is invalid") from exc
    if any(value.get(key) != expected for key, expected in material.items()):
        raise ConflictError("Stage 10 history-extension live result binding is invalid")
    if value.get("result_sha256") != _sha256_json(material):
        raise ConflictError("Stage 10 history-extension live result digest is invalid")
    return value


def _ledger_record(result: Mapping[str, object]) -> Mapping[str, object]:
    keys = {
        "ordinal",
        "symbol",
        "window_id",
        "from",
        "to",
        "query",
        "disposition",
        "base_completion_sha256",
        "base_reconciliation_sha256",
        "intent_sha256",
        "result_sha256",
        "response_sha256",
        "response_byte_count",
        "row_count",
    }
    if result["disposition"] == "complete":
        keys |= {"publication_receipt_sha256", "semantic_identity"}
    elif result["disposition"] == "terminal_failure":
        keys |= {"terminal_reason", "http_status", "content_type"}
    return {key: result[key] for key in sorted(keys)}


def _failed_window_projection(result: Mapping[str, object]) -> Mapping[str, object]:
    if result.get("disposition") != "terminal_failure":
        raise ValidationError("Stage 10 history-extension failed window is invalid")
    return {
        "disposition": result["disposition"],
        "http_status": result["http_status"],
        "ordinal": result["ordinal"],
        "symbol": result["symbol"],
        "terminal_reason": result["terminal_reason"],
        "window_id": result["window_id"],
    }

class _SpoolReplayTransport:
    """A one-use local transport for parsing an already durable raw response."""

    def __init__(
        self,
        prepared: PreparedFmpStage10WindowCapture,
        response: CapturedFmpStage10Response,
    ) -> None:
        self._prepared = prepared
        self._response = response
        self._used = False

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response:
        if (
            self._used
            or path != FMP_STAGE10_PRICE_PATH
            or dict(query) != self._prepared.query
            or set(headers) != {"apikey"}
            or timeout_seconds != FMP_STAGE10_TIMEOUT_SECONDS
            or max_bytes != FMP_STAGE10_MAX_PRICE_BYTES
        ):
            raise ValidationError("Stage 10 history-extension spool replay is invalid")
        self._used = True
        return self._response


def _validate_terminal_error_body(body: bytes) -> None:
    """Accept only the reviewed FMP error-object envelope for terminal status."""

    try:
        parsed = loads_strict(body, max_bytes=FMP_STAGE10_MAX_PRICE_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ValidationError(
            "Stage 10 history-extension terminal provider response is invalid"
        ) from exc
    # Conservative explicit assumption: the stable FMP endpoint's reviewed
    # terminal error envelope is precisely one nonempty Error Message field.
    if (
        not isinstance(parsed, Mapping)
        or set(parsed) != {"Error Message"}
        or not isinstance(parsed["Error Message"], str)
        or not parsed["Error Message"].strip()
        or len(parsed["Error Message"]) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in parsed["Error Message"])
    ):
        raise ValidationError(
            "Stage 10 history-extension terminal provider response is invalid"
        )


def _capture_from_live_spool(
    intent: HistoryExtensionIntent,
    spool: _LiveSpool,
    body: bytes,
) -> FmpStage10WindowCapture | None:
    """Recover a durable 200 JSON capture without a second provider call."""

    if _authorized_402_response_matches(spool, body):
        return None
    if spool.status in _LIVE_TERMINAL_STATUS_REASON:
        if spool.content_type != "application/json":
            raise StoreUnavailableError("FMP provider response was unavailable")
        _validate_terminal_error_body(body)
        return None
    if spool.status != 200 or spool.content_type != "application/json":
        raise StoreUnavailableError("FMP provider response was unavailable")
    prepared = prepare_fmp_stage10_window_capture(intent.symbol, intent.window)
    response = CapturedFmpStage10Response(
        status=spool.status,
        content_type=spool.content_type,
        body=body,
    )
    # Reuse the canonical parser/capture seam with a local one-use adapter.
    from ..market.stage10_history_windows import capture_fmp_stage10_window

    return capture_fmp_stage10_window(
        prepared,
        api_key="local-spool-replay",
        transport=_SpoolReplayTransport(prepared, response),
    )


def _publication_receipt_sha256(receipt: object) -> tuple[IngestionReceipt, str]:
    if not isinstance(receipt, IngestionReceipt):
        raise ValidationError("Stage 10 history-extension publication receipt is invalid")
    if (
        receipt.outcome != "succeeded"
        or receipt.store != StoreRole.MARKET.value
        or receipt.dataset_id != "market.stage10.daily_prices"
        or receipt.run_id is None
        or receipt.artifact_id is None
        or receipt.snapshot_id is None
        or receipt.written_count < 0
        or receipt.warnings
    ):
        raise ValidationError("Stage 10 history-extension publication did not succeed")
    return receipt, _sha256_json(receipt.to_primitive())


def _recovered_publication_receipt(
    store_map: object,
    receipt: object,
) -> IngestionReceipt:
    """Convert an idempotent importer result to the actual committed receipt."""

    if not isinstance(receipt, IngestionReceipt):
        raise ValidationError("Stage 10 history-extension publication receipt is invalid")
    if receipt.outcome == "succeeded":
        return receipt
    if (
        receipt.outcome != "unchanged"
        or receipt.store != StoreRole.MARKET.value
        or receipt.dataset_id != "market.stage10.daily_prices"
        or receipt.run_id is not None
        or receipt.artifact_id is not None
        or receipt.snapshot_id is not None
        or receipt.written_count != 0
        or receipt.warnings
        or not isinstance(store_map, StoreMap)
    ):
        raise ValidationError("Stage 10 history-extension publication receipt is invalid")
    with read_connection(store_map, StoreRole.MARKET) as connection:
        row = connection.execute(
            """
            SELECT run_id, artifact_id, snapshot_id, written_count, warnings_json
            FROM ingestion_runs
            WHERE dataset_id=? AND semantic_identity=? AND status='succeeded'
            """,
            (receipt.dataset_id, receipt.semantic_identity),
        ).fetchone()
    if row is None:
        raise ConflictError("Stage 10 history-extension publication recovery is unavailable")
    try:
        warnings = loads_strict(str(row["warnings_json"]))
    except ValidationError as exc:
        raise ConflictError("Stage 10 history-extension publication recovery is invalid") from exc
    if not isinstance(warnings, list) or warnings:
        raise ConflictError("Stage 10 history-extension publication recovery is invalid")
    recovered = IngestionReceipt(
        outcome="succeeded",
        store=StoreRole.MARKET.value,
        dataset_id=receipt.dataset_id,
        semantic_identity=receipt.semantic_identity,
        run_id=str(row["run_id"]),
        artifact_id=str(row["artifact_id"]),
        snapshot_id=str(row["snapshot_id"]),
        written_count=int(row["written_count"]),
        warnings=(),
    )
    return recovered


def _publish_live_capture(
    proof: Stage10HistoryExtensionBaseProof,
    capture: FmpStage10WindowCapture,
    *,
    importer: object,
) -> tuple[str, str]:
    if not isinstance(importer, Stage10HistoryWindowImporter):
        raise ValidationError("Stage 10 history-extension importer binding is invalid")
    prepared = importer.prepare_window_capture(capture)
    receipt = _recovered_publication_receipt(
        proof.store_map,
        importer.publish_prepared(prepared),
    )
    successful, digest = _publication_receipt_sha256(receipt)
    return digest, successful.semantic_identity


def _validate_live_directory(
    directory: Path,
    *,
    suffix: str,
    label: str,
    maximum_bytes: int,
) -> tuple[Path, ...]:
    _require_private_directory(directory, label)
    entries = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    if len(entries) > HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT:
        raise ConflictError(f"Stage 10 history-extension {label} exceeds the reviewed bound")
    for path in entries:
        if _INTENT_ID.fullmatch(path.stem) is None or path.suffix != suffix:
            raise ConflictError(f"Stage 10 history-extension {label} contains an invalid entry")
        if suffix == ".raw":
            _require_raw_file(path, label, maximum_bytes)
        else:
            _require_private_file(path, label, maximum_bytes)
    return entries


def _validate_result_spool_binding(
    result: Mapping[str, object],
    spool: _LiveSpool,
    body: bytes,
) -> None:
    if (
        result.get("response_sha256") != spool.response_sha256
        or result.get("response_byte_count") != spool.response_byte_count
        or len(body) != spool.response_byte_count
        or hashlib.sha256(body).hexdigest() != spool.response_sha256
    ):
        raise ConflictError("Stage 10 history-extension live result spool binding is invalid")
    disposition = result.get("disposition")
    if disposition in {"complete", "complete_empty"}:
        if spool.status != 200 or spool.content_type != "application/json":
            raise ConflictError("Stage 10 history-extension completed response is invalid")
    elif disposition == "terminal_failure":
        standard_terminal = (
            spool.status in _LIVE_TERMINAL_STATUS_REASON
            and spool.content_type == "application/json"
            and result.get("terminal_reason") == _LIVE_TERMINAL_STATUS_REASON[spool.status]
            and result.get("http_status") == spool.status
            and result.get("content_type") == "application/json"
        )
        operator_terminal = (
            result.get("terminal_reason") == TRANSITION_402_TERMINAL_REASON
            and result.get("http_status") == TRANSITION_402_HTTP_STATUS
            and result.get("content_type") == TRANSITION_402_CONTENT_TYPE
            and spool.status == TRANSITION_402_HTTP_STATUS
            and spool.content_type == TRANSITION_402_CONTENT_TYPE
            and spool.response_sha256 == TRANSITION_402_RESPONSE_SHA256
            and spool.response_byte_count == TRANSITION_402_RESPONSE_BYTE_COUNT
            and len(body) == TRANSITION_402_RESPONSE_BYTE_COUNT
            and hashlib.sha256(body).hexdigest() == TRANSITION_402_RESPONSE_SHA256
        )
        operator_known_listed_empty = (
            result.get("terminal_reason") == TRANSITION_EMPTY_TERMINAL_REASON
            and result.get("http_status") == TRANSITION_EMPTY_HTTP_STATUS
            and result.get("content_type") == TRANSITION_EMPTY_CONTENT_TYPE
            and spool.status == TRANSITION_EMPTY_HTTP_STATUS
            and spool.content_type == TRANSITION_EMPTY_CONTENT_TYPE
            and spool.response_sha256 == TRANSITION_EMPTY_RESPONSE_SHA256
            and spool.response_byte_count == TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
            and len(body) == TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
            and hashlib.sha256(body).hexdigest() == TRANSITION_EMPTY_RESPONSE_SHA256
        )
        if (
            not standard_terminal
            and not operator_terminal
            and not operator_known_listed_empty
        ):
            raise ConflictError("Stage 10 history-extension terminal response is invalid")
    else:
        raise ConflictError("Stage 10 history-extension live result disposition is invalid")


def _read_live_resume(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
) -> tuple[str, ...]:
    value = _read_json(layout.resume, "live resume", _MAX_STATE_BYTES)
    required = {"contract", "plan_sha256", "recorded_result_ids", "version"}
    if (
        set(value) != required
        or value.get("contract") != _LIVE_RESUME_CONTRACT
        or value.get("version") != _LIVE_VERSION
        or value.get("plan_sha256") != plan.sha256
        or not isinstance(value.get("recorded_result_ids"), list)
        or any(
            not isinstance(item, str) or _INTENT_ID.fullmatch(item) is None
            for item in value["recorded_result_ids"]
        )
    ):
        raise ConflictError("Stage 10 history-extension live resume is invalid")
    ids = tuple(value["recorded_result_ids"])
    if ids != tuple(sorted(set(ids))):
        raise ConflictError("Stage 10 history-extension live resume is invalid")
    return ids


def _reconcile_live_journal(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    *,
    repair_resume: bool = True,
) -> dict[str, Mapping[str, object]]:
    """Validate immutable journal state and optionally repair the cache resume."""

    _validate_live_directory(
        layout.intents,
        suffix=".json",
        label="live intent journal",
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    _validate_live_directory(
        layout.spools,
        suffix=".json",
        label="live spool journal",
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    _validate_live_directory(
        layout.raw,
        suffix=".raw",
        label="live raw journal",
        maximum_bytes=_MAX_RESULT_RESPONSE_BYTES,
    )
    _validate_live_directory(
        layout.results,
        suffix=".json",
        label="live result journal",
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    _read_live_resume(layout, plan)
    records: dict[str, Mapping[str, object]] = {}
    for intent in plan.intents:
        intent_sha256 = _read_live_intent(layout, intent)
        spool = (
            _read_live_spool(layout, intent, intent_sha256=intent_sha256)
            if intent_sha256 is not None
            else None
        )
        result = (
            _read_live_result(layout, intent, intent_sha256=intent_sha256)
            if intent_sha256 is not None
            else None
        )
        if intent_sha256 is None:
            if spool is not None or result is not None:
                raise ConflictError("Stage 10 history-extension live journal lacks an intent")
            continue
        if spool is None:
            if result is not None:
                raise ConflictError("Stage 10 history-extension live result lacks a response spool")
            raise ConflictError(
                "Stage 10 history-extension has an unresolved issued request; automatic retry is forbidden"
            )
        spool_value, body = spool
        if result is not None:
            _validate_result_spool_binding(result, spool_value, body)
            records[intent.id] = result
    expected_ids = tuple(sorted(records))
    if _read_live_resume(layout, plan) != expected_ids:
        if repair_resume:
            _write_live_resume(layout, plan, expected_ids)
        else:
            raise ConflictError(
                "Stage 10 history-extension live resume cache is inconsistent"
            )
    return records


def _live_ledger_and_sidecars(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    records: Mapping[str, Mapping[str, object]],
) -> tuple[tuple[Mapping[str, object], ...], Mapping[str, bytes]]:
    """Derive the immutable prefix ledger only from authenticated journal files."""

    ledger: list[Mapping[str, object]] = []
    sidecars: dict[str, bytes] = {}
    prefix_closed = True
    for intent in plan.intents:
        record = records.get(intent.id)
        if record is None:
            prefix_closed = False
            continue
        if not prefix_closed:
            raise ConflictError(
                "Stage 10 history-extension closed journal is not a schedule prefix"
            )
        intent_sha256 = _read_live_intent(layout, intent)
        if intent_sha256 is None:
            raise ConflictError(
                "Stage 10 history-extension closed result lacks its intent"
            )
        spool = _read_live_spool(
            layout,
            intent,
            intent_sha256=intent_sha256,
        )
        if spool is None:
            raise ConflictError(
                "Stage 10 history-extension closed result lacks its raw sidecar"
            )
        spool_value, raw = spool
        _validate_result_spool_binding(record, spool_value, raw)
        if record.get("intent_sha256") != intent_sha256:
            raise ConflictError(
                "Stage 10 history-extension closed result intent binding is invalid"
            )
        ledger.append(_ledger_record(record))
        sidecars[intent_sha256] = raw
    if len(sidecars) != len(ledger):
        raise ConflictError(
            "Stage 10 history-extension raw sidecar intent binding is invalid"
        )
    return tuple(ledger), sidecars


def _validate_live_prefix(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    records: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Rebind every durable prefix outcome to current market facts without writes."""

    from .stage10_history_extension_repopulation import (
        validate_stage10_history_extension_prefix,
    )

    ledger, sidecars = _live_ledger_and_sidecars(layout, plan, records)
    authorization_proof = _live_authorization_proof_for_ledger(
        layout,
        plan,
        ledger,
        _extension_code_revision(proof),
    )
    return validate_stage10_history_extension_prefix(
        proof.store_map,
        proof.registry,
        proof.scope,
        base_reconciliation=proof.base_reconciliation,
        base_completion_sha256=proof.verified_base.completion_sha256,
        ledger_records=ledger,
        sidecar_raw_by_intent=sidecars,
        authorization_proof=authorization_proof,
    )


def _validate_newly_closed_live_result(
    layout: _LiveLayout,
    proof: Stage10HistoryExtensionBaseProof,
    intent: HistoryExtensionIntent,
    expected_result: Mapping[str, object],
    *,
    importer: object,
) -> None:
    """Re-read and independently bind one new closure before another request."""

    from ..market.stage10_history_importer import (
        STAGE10_CURRENCY_SEGMENT,
        STAGE10_DAILY_HISTORY_COLLECTOR_ID,
        STAGE10_DAILY_PRICES_DATASET_ID,
        STAGE10_PRICE_VARIANT,
    )

    if not isinstance(importer, Stage10HistoryWindowImporter):
        raise ValidationError(
            "Stage 10 history-extension importer binding is invalid"
        )
    intent_sha256 = _read_live_intent(layout, intent)
    if intent_sha256 is None:
        raise ConflictError(
            "Stage 10 history-extension newly closed result lacks its intent"
        )
    result = _read_live_result(
        layout,
        intent,
        intent_sha256=intent_sha256,
    )
    if result is None or dict(result) != dict(expected_result):
        raise ConflictError(
            "Stage 10 history-extension newly closed result is inconsistent"
        )
    spooled = _read_live_spool(
        layout,
        intent,
        intent_sha256=intent_sha256,
    )
    if spooled is None:
        raise ConflictError(
            "Stage 10 history-extension newly closed result lacks its sidecar"
        )
    spool, raw = spooled
    _validate_result_spool_binding(result, spool, raw)
    capture = _capture_from_live_spool(intent, spool, raw)
    disposition = result.get("disposition")
    if disposition == "terminal_failure":
        terminal_reason = result.get("terminal_reason")
        if terminal_reason in {
            TRANSITION_402_TERMINAL_REASON,
            TRANSITION_EMPTY_TERMINAL_REASON,
        }:
            _verified_live_authorization_proof(
                layout,
                build_history_extension_plan(proof.verified_base),
                _extension_code_revision(proof),
            )
        if terminal_reason == TRANSITION_EMPTY_TERMINAL_REASON:
            if (
                capture is None
                or capture.disposition != "complete_empty"
                or capture.rows
                or not _authorized_known_listed_empty_response_matches(spool, raw)
                or not _empty_window_listing_status(proof, intent)[0]
            ):
                raise ConflictError(
                    "Stage 10 history-extension known-listed empty result is invalid"
                )
            return
        if capture is not None:
            raise ConflictError(
                "Stage 10 history-extension terminal result has price rows"
            )
        return
    if capture is None:
        raise ConflictError(
            "Stage 10 history-extension completed result lacks a capture"
        )
    if disposition == "complete_empty":
        if capture.rows or result.get("row_count") != 0:
            raise ConflictError(
                "Stage 10 history-extension empty result is inconsistent"
            )
        _assert_empty_window_has_no_current_facts(proof, intent)
        return
    if disposition != "complete" or not capture.rows:
        raise ConflictError(
            "Stage 10 history-extension completed result is inconsistent"
        )

    window = intent.window
    request_scope: dict[str, object] = {
        **capture.prepared.request_scope(),
        "from": window.start_date,
        "to": window.end_date,
        "interval": proof.scope.price_history.interval,
        "history_policy": "explicit_inclusive_five_year_windows_from_1990",
        "price_variant": proof.scope.price_history.price_variant,
        "currency_policy": proof.scope.price_history.currency_policy,
        "volume_policy": proof.scope.price_history.volume_policy,
        "scope_manifest_sha256": proof.scope.manifest_sha256,
        "target_profile_id": proof.scope.target_profile_id,
    }
    expected_semantic_identity = _sha256_json(
        {
            "collector_id": STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
            "request_scope": request_scope,
            "normalized_window_history_sha256": capture.semantic_sha256,
        }
    )
    if result.get("semantic_identity") != expected_semantic_identity:
        raise ConflictError(
            "Stage 10 history-extension semantic identity is inconsistent"
        )

    with read_connection(proof.store_map, StoreRole.MARKET) as connection:
        retained = connection.execute(
            """
            SELECT capture.instrument_id, capture.provider_symbol,
                   capture.request_scope_json, capture.response_sha256,
                   capture.response_bytes, capture.semantic_identity,
                   capture.earliest_trade_date, capture.latest_trade_date,
                   capture.row_count, capture.run_id, capture.artifact_id,
                   capture.snapshot_id, run.dataset_id, run.status,
                   run.artifact_id AS run_artifact_id,
                   run.snapshot_id AS run_snapshot_id,
                   run.written_count, run.warnings_json
            FROM stage10_daily_price_captures AS capture
            JOIN ingestion_runs AS run ON run.run_id=capture.run_id
            WHERE capture.semantic_identity=?
            """,
            (expected_semantic_identity,),
        ).fetchone()
        if retained is None:
            raise ConflictError(
                "Stage 10 history-extension publication is not retained"
            )
        try:
            retained_scope = loads_strict(
                str(retained["request_scope_json"]), max_bytes=64 * 1024
            )
            warnings = loads_strict(
                str(retained["warnings_json"]), max_bytes=64 * 1024
            )
        except (ResourceLimitError, ValidationError) as exc:
            raise ConflictError(
                "Stage 10 history-extension publication evidence is invalid"
            ) from exc
        if (
            retained["provider_symbol"] != intent.symbol
            or retained_scope != request_scope
            or retained["response_sha256"] != capture.raw_bytes_sha256
            or bytes(retained["response_bytes"]) != raw
            or retained["semantic_identity"] != expected_semantic_identity
            or retained["earliest_trade_date"] != capture.rows[0].trade_date
            or retained["latest_trade_date"] != capture.rows[-1].trade_date
            or retained["row_count"] != len(capture.rows)
            or retained["dataset_id"] != STAGE10_DAILY_PRICES_DATASET_ID
            or retained["status"] != "succeeded"
            or retained["run_artifact_id"] != retained["artifact_id"]
            or retained["run_snapshot_id"] != retained["snapshot_id"]
            or warnings != []
        ):
            raise ConflictError(
                "Stage 10 history-extension publication evidence is inconsistent"
            )
        publication_receipt = IngestionReceipt(
            outcome="succeeded",
            store=StoreRole.MARKET.value,
            dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
            semantic_identity=expected_semantic_identity,
            run_id=str(retained["run_id"]),
            artifact_id=str(retained["artifact_id"]),
            snapshot_id=str(retained["snapshot_id"]),
            written_count=int(retained["written_count"]),
            warnings=(),
        )
        if result.get("publication_receipt_sha256") != _sha256_json(
            publication_receipt.to_primitive()
        ):
            raise ConflictError(
                "Stage 10 history-extension publication receipt is inconsistent"
            )
        current_rows = connection.execute(
            """
            SELECT current.trade_date, version.open_value, version.high_value,
                   version.low_value, version.close_value, version.volume
            FROM stage10_daily_prices AS current
            JOIN stage10_daily_price_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.instrument_id=? AND current.provider='fmp'
              AND current.price_variant=? AND current.currency_segment=?
              AND current.trade_date BETWEEN ? AND ?
            ORDER BY current.trade_date
            """,
            (
                retained["instrument_id"],
                STAGE10_PRICE_VARIANT,
                STAGE10_CURRENCY_SEGMENT,
                window.start_date,
                window.end_date,
            ),
        ).fetchall()
    expected_rows = {
        row.trade_date: row.semantic_mapping() for row in capture.rows
    }
    observed_rows = {
        str(row["trade_date"]): {
            "open": str(row["open_value"]),
            "high": str(row["high_value"]),
            "low": str(row["low_value"]),
            "close": str(row["close_value"]),
            "volume": int(row["volume"]),
        }
        for row in current_rows
    }
    if set(observed_rows) != set(expected_rows) or any(
        observed_rows[trade_date]
        != {
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": values["volume"],
        }
        for trade_date, values in expected_rows.items()
    ):
        raise ConflictError(
            "Stage 10 history-extension current window differs from its response"
        )


def _empty_window_listing_status(
    proof: Stage10HistoryExtensionBaseProof,
    intent: HistoryExtensionIntent,
) -> tuple[bool, bool]:
    """Return known-listing and unsafe-final-gap status for an empty window."""

    from ..market.stage10_history_importer import (
        STAGE10_CURRENCY_SEGMENT,
        STAGE10_PRICE_VARIANT,
    )

    if not isinstance(proof.store_map, StoreMap):
        raise ValidationError(
            "Stage 10 history-extension empty-window store binding is invalid"
        )
    with read_connection(proof.store_map, StoreRole.MARKET) as connection:
        instrument = connection.execute(
            """
            SELECT instrument_id, first_trade_date
            FROM stage10_instruments
            WHERE provider='fmp' AND provider_symbol=?
            """,
            (intent.symbol,),
        ).fetchone()
        if instrument is None:
            raise ValidationError(
                "Stage 10 history-extension empty-window instrument is missing"
            )
        earliest = connection.execute(
            """
            SELECT MIN(trade_date) AS earliest_trade_date
            FROM stage10_daily_prices
            WHERE instrument_id=? AND provider='fmp'
              AND price_variant=? AND currency_segment=?
            """,
            (
                instrument["instrument_id"],
                STAGE10_PRICE_VARIANT,
                STAGE10_CURRENCY_SEGMENT,
            ),
        ).fetchone()["earliest_trade_date"]
    first_trade_date = instrument["first_trade_date"]
    known_by_window_end = (
        earliest is not None and str(earliest) <= intent.window.end_date
    ) or (
        first_trade_date is not None
        and str(first_trade_date) <= intent.window.end_date
    )
    final_window_without_any_boundary = (
        intent.window == STAGE10_HISTORY_WINDOWS[-1]
        and earliest is None
        and first_trade_date is None
    )
    return known_by_window_end, final_window_without_any_boundary


def _assert_empty_window_has_no_current_facts(
    proof: Stage10HistoryExtensionBaseProof,
    intent: HistoryExtensionIntent,
) -> None:
    """Accept empty only while it remains consistent with pre-listing history."""

    known_by_window_end, final_window_without_any_boundary = (
        _empty_window_listing_status(proof, intent)
    )
    if known_by_window_end or final_window_without_any_boundary:
        raise ValidationError(
            "Stage 10 history-extension empty response is not proven pre-listing"
        )


def _recover_or_close_spooled_response(
    layout: _LiveLayout,
    proof: Stage10HistoryExtensionBaseProof,
    intent: HistoryExtensionIntent,
    *,
    intent_sha256: str,
    importer: object,
) -> Mapping[str, object]:
    recovered = _read_live_spool(layout, intent, intent_sha256=intent_sha256)
    if recovered is None:
        raise ConflictError("Stage 10 history-extension response spool is missing")
    spool, body = recovered
    existing = _read_live_result(layout, intent, intent_sha256=intent_sha256)
    if existing is not None:
        _validate_result_spool_binding(existing, spool, body)
        return existing
    operator_transition = _authorized_402_response_matches(spool, body)
    if operator_transition:
        plan = build_history_extension_plan(proof.verified_base)
        _verified_live_authorization_proof(
            layout,
            plan,
            _extension_code_revision(proof),
        )
    capture = _capture_from_live_spool(intent, spool, body)
    if capture is None:
        terminal_reason = (
            TRANSITION_402_TERMINAL_REASON
            if operator_transition
            else _LIVE_TERMINAL_STATUS_REASON[spool.status]
        )
        material = _live_result_material(
            intent,
            intent_sha256=intent_sha256,
            disposition="terminal_failure",
            response_sha256=spool.response_sha256,
            response_byte_count=spool.response_byte_count,
            row_count=0,
            terminal_reason=terminal_reason,
            http_status=spool.status,
            content_type=spool.content_type,
        )
    elif capture.disposition == "complete_empty":
        known_by_window_end, _ = _empty_window_listing_status(proof, intent)
        if known_by_window_end:
            if not _authorized_known_listed_empty_response_matches(spool, body):
                raise ConflictError(
                    "Stage 10 history-extension known-listed empty response differs"
                )
            plan = build_history_extension_plan(proof.verified_base)
            _verified_live_authorization_proof(
                layout,
                plan,
                _extension_code_revision(proof),
            )
            material = _live_result_material(
                intent,
                intent_sha256=intent_sha256,
                disposition="terminal_failure",
                response_sha256=spool.response_sha256,
                response_byte_count=spool.response_byte_count,
                row_count=0,
                terminal_reason=TRANSITION_EMPTY_TERMINAL_REASON,
                http_status=spool.status,
                content_type=spool.content_type,
            )
        else:
            _assert_empty_window_has_no_current_facts(proof, intent)
            material = _live_result_material(
                intent,
                intent_sha256=intent_sha256,
                disposition="complete_empty",
                response_sha256=spool.response_sha256,
                response_byte_count=spool.response_byte_count,
                row_count=0,
            )
    else:
        publication_digest, semantic_identity = _publish_live_capture(
            proof,
            capture,
            importer=importer,
        )
        material = _live_result_material(
            intent,
            intent_sha256=intent_sha256,
            disposition="complete",
            response_sha256=spool.response_sha256,
            response_byte_count=spool.response_byte_count,
            row_count=capture.row_count,
            publication_receipt_sha256=publication_digest,
            semantic_identity=semantic_identity,
        )
    return _write_live_result(layout, intent, material)

def _live_report(
    plan: HistoryExtensionPlan,
    records: Mapping[str, Mapping[str, object]],
    *,
    requests_issued: int,
    completion: str,
) -> LiveHistoryExtensionRunReport:
    sentinel = records.get(plan.intents[0].id)
    sentinel_status = "pending" if sentinel is None else str(sentinel["disposition"])
    failed = tuple(
        _failed_window_projection(record)
        for _, record in sorted(
            records.items(),
            key=lambda item: int(item[1]["ordinal"]),
        )
        if record["disposition"] == "terminal_failure"
    )
    return LiveHistoryExtensionRunReport(
        plan_sha256=plan.sha256,
        requests_issued=requests_issued,
        closed_request_count=len(records),
        terminal_failure_count=len(failed),
        sentinel_status=sentinel_status,
        completion=completion,
        failed_windows=failed,
    )


def _completion_attempt_id(plan: HistoryExtensionPlan) -> str:
    return "stage10-history-extension-v1-" + plan.sha256[:16]


def _completion_cohort_registry(proof: Stage10HistoryExtensionBaseProof) -> Registry:
    if not isinstance(proof.cohort_registry, Registry):
        raise ValidationError(
            "Stage 10 history-extension canonical cohort registry is invalid"
        )
    try:
        return stage11_registry_profile(proof.cohort_registry)
    except RegistryError as exc:
        raise ValidationError(
            "Stage 10 history-extension canonical cohort registry is invalid"
        ) from exc


def _extension_code_revision(proof: Stage10HistoryExtensionBaseProof) -> str:
    """Bind private candidate evidence to exact execution sources, never HEAD."""

    _completion_cohort_registry(proof)
    try:
        return stage10_history_extension_execution_revision()
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc



def _require_current_execution_revision(
    proof: Stage10HistoryExtensionBaseProof,
    execution_revision: str,
    *,
    enforce: bool,
) -> None:
    _require_sha256(execution_revision, "execution source digest")
    if enforce and _extension_code_revision(proof) != execution_revision:
        raise ConflictError(
            "Stage 10 history-extension execution sources changed"
        )


def _require_immutable_live_file(path: Path, label: str, maximum_bytes: int) -> None:
    _require_private_file(path, label, maximum_bytes)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConflictError(
            f"Stage 10 history-extension {label} is unreadable"
        ) from exc
    if info.st_nlink != 1:
        raise ConflictError(f"Stage 10 history-extension {label} is invalid")


def _live_authorization_proof_for_ledger(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    ledger: Sequence[Mapping[str, object]],
    execution_revision: str,
) -> Mapping[str, object] | None:
    required = any(
        record.get("disposition") == "terminal_failure"
        and record.get("terminal_reason") in {
            TRANSITION_402_TERMINAL_REASON,
            TRANSITION_EMPTY_TERMINAL_REASON,
        }
        for record in ledger
    )
    if not required:
        return None
    return _verified_live_authorization_proof(
        layout,
        plan,
        execution_revision,
    )


def _final_live_reconciliation(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    records: Mapping[str, Mapping[str, object]],
) -> tuple[object, tuple[Mapping[str, object], ...], Mapping[str, bytes]]:
    from .stage10_history_extension_repopulation import (
        reconcile_stage10_history_extension,
    )

    ledger, sidecars = _live_ledger_and_sidecars(layout, plan, records)
    authorization_proof = _live_authorization_proof_for_ledger(
        layout,
        plan,
        ledger,
        _extension_code_revision(proof),
    )
    if len(ledger) != HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT:
        raise ConflictError("Stage 10 history-extension ledger is incomplete")
    reconciliation = reconcile_stage10_history_extension(
        proof.store_map,
        proof.registry,
        proof.scope,
        base_reconciliation=proof.base_reconciliation,
        base_completion_sha256=proof.verified_base.completion_sha256,
        ledger_records=ledger,
        sidecar_raw_by_intent=sidecars,
        authorization_proof=authorization_proof,
    )
    return reconciliation, ledger, sidecars


def _live_authorization_proof_sha256(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    failed_windows: Sequence[Mapping[str, object]],
    execution_revision: str,
) -> str:
    """Rebind operator terminal outcomes to their immutable transition receipts."""

    reasons = {
        item.get("terminal_reason")
        for item in failed_windows
        if isinstance(item, Mapping)
    }
    if not {
        TRANSITION_402_TERMINAL_REASON,
        TRANSITION_EMPTY_TERMINAL_REASON,
    }.intersection(reasons):
        return _sha256_json([])
    proof = _verified_live_authorization_proof(
        layout,
        plan,
        execution_revision,
    )
    return _require_sha256(
        proof.get("sha256"),
        "authorization proof digest",
    )


def _live_completion_material(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    reconciliation: object,
    candidate: object,
) -> dict[str, object]:
    values = getattr(reconciliation, "to_primitive", None)
    candidate_values = getattr(candidate, "to_primitive", None)
    if not callable(values) or not callable(candidate_values):
        raise ValidationError("Stage 10 history-extension completion binding is invalid")
    reconciliation_primitive = values()
    candidate_primitive = candidate_values()
    if (
        not isinstance(reconciliation_primitive, Mapping)
        or not isinstance(candidate_primitive, Mapping)
        or reconciliation_primitive.get("base_completion_sha256")
        != proof.verified_base.completion_sha256
        or reconciliation_primitive.get("base_reconciliation_sha256")
        != proof.verified_base.reconciliation_sha256
    ):
        raise ValidationError("Stage 10 history-extension completion binding is invalid")
    required_counts = (
        "complete_window_count",
        "empty_window_count",
        "terminal_failure_count",
    )
    if any(
        isinstance(reconciliation_primitive.get(name), bool)
        or not isinstance(reconciliation_primitive.get(name), int)
        for name in required_counts
    ):
        raise ValidationError("Stage 10 history-extension completion binding is invalid")
    failed_windows = reconciliation_primitive.get("failed_windows")
    if not isinstance(failed_windows, list) or any(
        not isinstance(item, Mapping) for item in failed_windows
    ):
        raise ValidationError("Stage 10 history-extension completion binding is invalid")
    execution_revision = _extension_code_revision(proof)
    authorization_proof_sha256 = _live_authorization_proof_sha256(
        layout,
        plan,
        failed_windows,
        execution_revision,
    )
    operator_required = any(
        item.get("terminal_reason") in {
            TRANSITION_402_TERMINAL_REASON,
            TRANSITION_EMPTY_TERMINAL_REASON,
        }
        for item in failed_windows
    )
    expected_proof = (
        _verified_live_authorization_proof(layout, plan, execution_revision)
        if operator_required
        else None
    )
    if reconciliation_primitive.get("authorization_proof") != expected_proof:
        raise ValidationError(
            "Stage 10 history-extension reconciliation authorization differs"
        )
    return {
        "authorization_proof_sha256": authorization_proof_sha256,
        "base_completion_sha256": proof.verified_base.completion_sha256,
        "base_reconciliation_sha256": proof.verified_base.reconciliation_sha256,
        "candidate_receipt_sha256": candidate_primitive.get("receipt_sha256"),
        "code_revision": execution_revision,
        "cohort_registry_source_sha256": _completion_cohort_registry(
            proof
        ).source_sha256,
        "complete_window_count": reconciliation_primitive[
            "complete_window_count"
        ],
        "empty_window_count": reconciliation_primitive["empty_window_count"],
        "evidence_sha256": candidate_primitive.get("evidence_sha256"),
        "failed_windows": [dict(item) for item in failed_windows],
        "ledger_sha256": reconciliation_primitive.get("ledger_sha256"),
        "plan_sha256": plan.sha256,
        "raw_sidecar_manifest_sha256": reconciliation_primitive.get(
            "raw_sidecar_manifest_sha256"
        ),
        "terminal_failure_count": reconciliation_primitive[
            "terminal_failure_count"
        ],
    }


def _write_live_completion(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    reconciliation: object,
    candidate: object,
) -> Mapping[str, object]:
    material = _live_completion_material(
        layout, plan, proof, reconciliation, candidate
    )
    for name in (
        "authorization_proof_sha256",
        "candidate_receipt_sha256",
        "code_revision",
        "cohort_registry_source_sha256",
        "evidence_sha256",
        "ledger_sha256",
        "raw_sidecar_manifest_sha256",
    ):
        _require_sha256(material.get(name), f"completion {name}")
    if (
        material["complete_window_count"]
        + material["empty_window_count"]
        + material["terminal_failure_count"]
        != HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
        or len(material["failed_windows"]) != material["terminal_failure_count"]
    ):
        raise ValidationError("Stage 10 history-extension completion counts are invalid")
    receipt = {
        "contract": _LIVE_COMPLETION_CONTRACT,
        **material,
        "version": _LIVE_VERSION,
    }
    receipt["completion_sha256"] = _sha256_json(receipt)
    _write_json_exclusive(
        layout.completion,
        receipt,
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    _require_immutable_live_file(
        layout.completion,
        "live completion receipt",
        _MAX_RECEIPT_BYTES,
    )
    return receipt


def _read_live_completion(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
) -> Mapping[str, object] | None:
    if not layout.completion.exists() and not layout.completion.is_symlink():
        return None
    _require_immutable_live_file(
        layout.completion,
        "live completion receipt",
        _MAX_RECEIPT_BYTES,
    )
    value = _read_json(
        layout.completion,
        "live completion receipt",
        _MAX_RECEIPT_BYTES,
    )
    required = {
        "authorization_proof_sha256",
        "base_completion_sha256",
        "base_reconciliation_sha256",
        "code_revision",
        "candidate_receipt_sha256",
        "cohort_registry_source_sha256",
        "complete_window_count",
        "completion_sha256",
        "contract",
        "empty_window_count",
        "evidence_sha256",
        "failed_windows",
        "ledger_sha256",
        "plan_sha256",
        "raw_sidecar_manifest_sha256",
        "terminal_failure_count",
        "version",
    }
    material = {key: value[key] for key in required - {"completion_sha256"} if key in value}
    if (
        set(value) != required
        or value.get("contract") != _LIVE_COMPLETION_CONTRACT
        or value.get("version") != _LIVE_VERSION
        or value.get("plan_sha256") != plan.sha256
        or value.get("base_completion_sha256")
        != proof.verified_base.completion_sha256
        or value.get("base_reconciliation_sha256")
        != proof.verified_base.reconciliation_sha256
        or value.get("cohort_registry_source_sha256")
        != _completion_cohort_registry(proof).source_sha256
        or value.get("completion_sha256")
        != _sha256_json({"contract": _LIVE_COMPLETION_CONTRACT, **material, "version": _LIVE_VERSION})
    ):
        raise ConflictError("Stage 10 history-extension live completion is invalid")
    for name in (
        "authorization_proof_sha256",
        "candidate_receipt_sha256",
        "cohort_registry_source_sha256",
        "evidence_sha256",
        "ledger_sha256",
        "raw_sidecar_manifest_sha256",
        "completion_sha256",
    ):
        try:
            _require_sha256(value.get(name), f"completion {name}")
        except ValidationError as exc:
            raise ConflictError(
                "Stage 10 history-extension live completion is invalid"
            ) from exc
    counts = (
        value.get("complete_window_count"),
        value.get("empty_window_count"),
        value.get("terminal_failure_count"),
    )
    if (
        any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts)
        or sum(counts) != HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
        or not isinstance(value.get("failed_windows"), list)
        or len(value["failed_windows"]) != value["terminal_failure_count"]
        or any(not isinstance(item, Mapping) for item in value["failed_windows"])
    ):
        raise ConflictError("Stage 10 history-extension live completion is invalid")
    return value


def _read_live_candidate(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
) -> object:
    from .stage10_history_extension_repopulation import (
        Stage10HistoryExtensionCandidateReceipt,
    )

    _require_private_directory(layout.candidate, "live candidate state")
    directory = layout.candidate / "candidate-receipts"
    _require_private_directory(directory, "live candidate receipt directory")
    attempt_id = _completion_attempt_id(plan)
    expected_name = attempt_id + ".json"
    entries = {entry.name: entry for entry in directory.iterdir()}
    if set(entries) != {expected_name}:
        raise ConflictError("Stage 10 history-extension live candidate layout is invalid")
    path = entries[expected_name]
    _require_immutable_live_file(
        path,
        "live candidate receipt",
        4 * 1024 * 1024,
    )
    raw = _read_json(path, "live candidate receipt", 4 * 1024 * 1024)
    required = {
        "attempt_id",
        "candidate_state",
        "code_revision",
        "contract",
        "contract_version",
        "evidence",
        "evidence_sha256",
        "generated_at",
        "receipt_sha256",
    }
    if set(raw) != required:
        raise ConflictError("Stage 10 history-extension live candidate receipt is invalid")
    try:
        candidate = Stage10HistoryExtensionCandidateReceipt(
            attempt_id=raw["attempt_id"],
            generated_at=raw["generated_at"],
            code_revision=raw["code_revision"],
            candidate_state=raw["candidate_state"],
            evidence=raw["evidence"],
            evidence_sha256=raw["evidence_sha256"],
            receipt_sha256=raw["receipt_sha256"],
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ConflictError(
            "Stage 10 history-extension live candidate receipt is invalid"
        ) from exc
    primitive = candidate.to_primitive()
    if (
        raw.get("contract")
        != "quant_data.stage10_history_extension_candidate_receipt"
        or raw.get("contract_version") != "1.4.0"
        or primitive != dict(raw)
        or candidate.attempt_id != attempt_id
        or candidate.code_revision != _extension_code_revision(proof)
    ):
        raise ConflictError("Stage 10 history-extension live candidate receipt is invalid")
    return candidate


def _validate_live_candidate_reuse(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    reconciliation: object,
    execution_revision: str,
) -> object:
    from ..fingerprint import logical_manifest, mutation_fingerprint
    from .stage10_history_extension_repopulation import (
        Stage10HistoryExtensionEvidence,
    )
    from .health import all_store_health

    candidate = _read_live_candidate(layout, plan, proof)
    candidate_primitive = candidate.to_primitive()
    evidence = candidate_primitive.get("evidence")
    reconciliation_primitive = getattr(reconciliation, "to_primitive", lambda: None)()
    if not isinstance(evidence, Mapping) or not isinstance(reconciliation_primitive, Mapping):
        raise ConflictError("Stage 10 history-extension live candidate evidence is invalid")
    evidence_fields = {
        "authorization_proof",
        "backup_restore_outcome",
        "cohort_registry_schema_version",
        "cohort_registry_source_sha256",
        "cohort_registry_version",
        "contract",
        "contract_version",
        "raw_reparsed_equal",
        "reconciliation",
        "restored_health_sha256",
        "restored_logical_manifest_sha256",
        "restored_mutation_after_sha256",
        "restored_mutation_before_sha256",
        "restored_reads_unchanged",
        "restored_reconciliation_sha256",
        "sha256",
        "source_health_sha256",
        "source_logical_manifest_sha256",
        "source_mutation_after_sha256",
        "source_mutation_before_sha256",
        "source_reads_unchanged",
        "source_reconciliation_sha256",
        "source_restored_equal",
    }
    try:
        validated_evidence = Stage10HistoryExtensionEvidence(
            reconciliation=reconciliation,
            **{
                key: evidence[key]
                for key in evidence_fields
                if key
                not in {"contract", "contract_version", "reconciliation"}
            },
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ConflictError(
            "Stage 10 history-extension live candidate evidence is invalid"
        ) from exc
    if (
        set(evidence) != evidence_fields
        or evidence.get("contract") != "quant_data.stage10_history_extension_evidence"
        or evidence.get("contract_version") != "1.4.0"
        or validated_evidence.to_primitive() != dict(evidence)
    ):
        raise ConflictError("Stage 10 history-extension live candidate evidence is invalid")
    cohort_registry = _completion_cohort_registry(proof)
    current_health = _sha256_json(
        all_store_health(proof.store_map, cohort_registry).to_primitive()
    )
    current_manifest = logical_manifest(proof.store_map, cohort_registry).get("sha256")
    current_mutation = mutation_fingerprint(proof.store_map).get("sha256")
    if (
        candidate_primitive.get("code_revision") != execution_revision
        or evidence.get("sha256") != candidate.evidence_sha256
        or evidence.get("reconciliation") != reconciliation_primitive
        or evidence.get("source_reconciliation_sha256") != reconciliation.sha256
        or evidence.get("source_health_sha256") != current_health
        or evidence.get("source_logical_manifest_sha256") != current_manifest
        or evidence.get("source_mutation_before_sha256") != current_mutation
        or evidence.get("source_mutation_after_sha256") != current_mutation
    ):
        raise ConflictError(
            "Stage 10 history-extension completed candidate no longer matches current stores"
        )
    return candidate


def _revalidate_completed_live(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    execution_revision: str,
    *,
    enforce_execution_revision: bool,
) -> LiveHistoryExtensionRunReport:
    _require_current_execution_revision(
        proof, execution_revision, enforce=enforce_execution_revision
    )
    records = _reconcile_live_journal(layout, plan)
    reconciliation, _, _ = _final_live_reconciliation(layout, plan, proof, records)
    completion = _read_live_completion(layout, plan, proof)
    if completion is None:
        raise ConflictError("Stage 10 history-extension completion receipt is missing")
    candidate = _validate_live_candidate_reuse(
        layout, plan, proof, reconciliation, execution_revision
    )
    material = _live_completion_material(
        layout, plan, proof, reconciliation, candidate
    )
    if any(completion.get(key) != value for key, value in material.items()):
        raise ConflictError(
            "Stage 10 history-extension completion receipt no longer matches candidate evidence"
        )
    _require_current_execution_revision(
        proof, execution_revision, enforce=enforce_execution_revision
    )
    return _live_report(
        plan,
        records,
        requests_issued=0,
        completion="finalized",
    )


def _finalize_live_extension(
    layout: _LiveLayout,
    plan: HistoryExtensionPlan,
    proof: Stage10HistoryExtensionBaseProof,
    records: Mapping[str, Mapping[str, object]],
    *,
    now: Callable[[], object],
    execution_revision: str,
    enforce_execution_revision: bool,
) -> LiveHistoryExtensionRunReport:
    from .backup import backup_all, restore_all
    from .stage10_history_extension_repopulation import (
        PrivateStage10HistoryExtensionCandidateState,
        build_stage10_history_extension_evidence,
    )

    _require_current_execution_revision(
        proof, execution_revision, enforce=enforce_execution_revision
    )
    reconciliation, ledger, sidecars = _final_live_reconciliation(
        layout,
        plan,
        proof,
        records,
    )
    if layout.completion.exists() or layout.completion.is_symlink():
        return _revalidate_completed_live(
            layout,
            plan,
            proof,
            execution_revision,
            enforce_execution_revision=enforce_execution_revision,
        )
    roots = (layout.backup, layout.restored, layout.candidate)
    if any(path.exists() or path.is_symlink() for path in roots):
        # Receipt-last repair is safe only after the candidate receipt exists:
        # its independently authenticated evidence already binds the prior
        # backup/restore cohort.  An earlier partial local copy is ambiguous.
        if (
            layout.candidate.exists()
            and not layout.candidate.is_symlink()
            and layout.backup.exists()
            and layout.restored.exists()
        ):
            candidate = _validate_live_candidate_reuse(
                layout, plan, proof, reconciliation, execution_revision
            )
            _require_current_execution_revision(
                proof, execution_revision, enforce=enforce_execution_revision
            )
            _write_live_completion(layout, plan, proof, reconciliation, candidate)
            return _live_report(
                plan,
                records,
                requests_issued=0,
                completion="finalized",
            )
        raise ConflictError(
            "Stage 10 history-extension finalization has incomplete private artifacts"
        )
    cohort_registry = _completion_cohort_registry(proof)
    layout.backup.mkdir(mode=_ROOT_MODE)
    _require_private_directory(layout.backup, "live backup root")
    _fsync_directory(layout.root)
    backup = backup_all(
        proof.store_map,
        cohort_registry,
        target_root=layout.backup,
    )
    layout.restored.mkdir(mode=_ROOT_MODE)
    _require_private_directory(layout.restored, "live restored root")
    _fsync_directory(layout.root)
    restored = restore_all(
        backup,
        cohort_registry,
        target_root=layout.restored,
    )
    evidence = build_stage10_history_extension_evidence(
        proof.store_map,
        restored.store_map,
        proof.registry,
        proof.scope,
        base_reconciliation=proof.base_reconciliation,
        base_completion_sha256=proof.verified_base.completion_sha256,
        ledger_records=ledger,
        sidecar_raw_by_intent=sidecars,
        cohort_registry=cohort_registry,
        authorization_proof=reconciliation.authorization_proof,
    )
    _require_current_execution_revision(
        proof, execution_revision, enforce=enforce_execution_revision
    )
    candidate = PrivateStage10HistoryExtensionCandidateState(layout.candidate).publish(
        evidence,
        code_revision=execution_revision,
        now=now,
        attempt_id=_completion_attempt_id(plan),
    )
    _require_current_execution_revision(
        proof, execution_revision, enforce=enforce_execution_revision
    )
    _write_live_completion(layout, plan, proof, reconciliation, candidate)
    return _live_report(
        plan,
        records,
        requests_issued=0,
        completion="finalized",
    )


def run_live_history_extension_rehearsal(
    target_root: Path,
    *,
    proof: Stage10HistoryExtensionBaseProof,
    api_key: str,
    transport: object,
    importer: object,
    max_requests: int,
    now: Callable[[], object],
    execution_revision: str | None = None,
    require_completed_sentinel: bool = False,
    stop_after_completed_sentinel: bool = False,
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> LiveHistoryExtensionRunReport:
    """Run a bounded injected rehearsal of the real manual journal workflow.

    The injected objects make this function suitable for offline tests.  It
    never creates stores, chooses paths, retries a request, or performs final
    candidate completion.  The public CLI below supplies the exact approved
    base proof and hardened stdlib transport for the authorized manual run.
    """

    if not isinstance(target_root, Path) or not target_root.is_absolute():
        raise ValidationError("Stage 10 history-extension target root is invalid")
    if (
        not isinstance(proof, Stage10HistoryExtensionBaseProof)
        or not isinstance(api_key, str)
        or not api_key
        or len(api_key) > 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in api_key)
        or not callable(getattr(transport, "get", None))
        or not isinstance(importer, Stage10HistoryWindowImporter)
        or not callable(now)
        or isinstance(max_requests, bool)
        or not isinstance(max_requests, int)
        or not 1 <= max_requests <= HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
        or not isinstance(require_completed_sentinel, bool)
        or not isinstance(stop_after_completed_sentinel, bool)
        or (require_completed_sentinel and stop_after_completed_sentinel)
    ):
        raise ValidationError("Stage 10 history-extension live rehearsal bindings are invalid")
    plan = build_history_extension_plan(proof.verified_base)
    enforce_execution_revision = execution_revision is None
    current_revision = (
        _extension_code_revision(proof)
        if enforce_execution_revision
        else execution_revision
    )
    _require_sha256(current_revision, "execution source digest")
    layout = _initialize_or_open_live_layout(
        target_root, plan, current_revision
    )
    with _acquire_live_run_lock(layout):
        records = _reconcile_live_journal(layout, plan)
        sentinel = records.get(plan.intents[0].id)
        for recovered_intent in plan.intents:
            if recovered_intent.id in records:
                continue
            recovered_intent_sha256 = _read_live_intent(layout, recovered_intent)
            if recovered_intent_sha256 is None:
                continue
            if _read_live_spool(
                layout,
                recovered_intent,
                intent_sha256=recovered_intent_sha256,
            ) is None:
                continue
            # A durable spool left by a crash after the provider response,
            # including after publication but before the result receipt, is
            # repaired locally before any current-store proof or new request.
            records[recovered_intent.id] = _recover_or_close_spooled_response(
                layout,
                proof,
                recovered_intent,
                intent_sha256=recovered_intent_sha256,
                importer=importer,
            )
        # Recovery can materialize the entitlement sentinel after the initial
        # journal read. Re-read it before any decision to issue another call.
        sentinel = records.get(plan.intents[0].id)
        _write_live_resume(layout, plan, tuple(sorted(records)))
        if sentinel is not None and sentinel["disposition"] != "complete":
            # A failed entitlement sentinel intentionally cannot form a valid
            # extension prefix. Its authenticated journal/spool is terminal,
            # and no later request may be issued.
            return _live_report(
                plan,
                records,
                requests_issued=0,
                completion="sentinel_failed",
            )
        if _read_live_completion(layout, plan, proof) is not None:
            return _revalidate_completed_live(
                layout,
                plan,
                proof,
                current_revision,
                enforce_execution_revision=enforce_execution_revision,
            )
        if require_completed_sentinel and (
            sentinel is None or sentinel["disposition"] != "complete"
        ):
            raise ConflictError(
                "Stage 10 history-extension continuation requires the completed sentinel"
            )
        if stop_after_completed_sentinel and sentinel is not None:
            _validate_live_prefix(layout, plan, proof, records)
            return _live_report(
                plan,
                records,
                requests_issued=0,
                completion="partial",
            )
        # A resumed durable prefix receives one complete query-only proof before
        # any new provider work. Newly closed records below receive a bounded
        # per-window proof immediately, avoiding a multi-gigabyte full-prefix
        # rescan before each of the 5,032 authorized requests.
        if records:
            _validate_live_prefix(layout, plan, proof, records)
        pacer = _RequestPacer(monotonic=monotonic, sleeper=sleeper)
        issued = 0
        for intent in plan.intents:
            existing = records.get(intent.id)
            if existing is not None:
                continue
            _require_current_execution_revision(
                proof,
                current_revision,
                enforce=enforce_execution_revision,
            )
            intent_sha256 = _read_live_intent(layout, intent)
            if intent_sha256 is None:
                intent_sha256 = _write_live_intent(layout, intent, now=now)
                pacer.before_request()
                # The exact frozen request is the only provider call made by
                # this iteration.  No SQLite write lock is held here.
                response = transport.get(
                    path=FMP_STAGE10_PRICE_PATH,
                    query=intent.window.query(intent.symbol),
                    headers={"apikey": api_key},
                    timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
                    max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
                )
                if not isinstance(response, CapturedFmpStage10Response):
                    raise ValidationError(
                        "Stage 10 history-extension transport response is invalid"
                    )
                _spool_response(
                    layout,
                    intent,
                    intent_sha256=intent_sha256,
                    api_key=api_key,
                    response=response,
                )
                issued += 1
            # A durable spool can be parsed/published without reissuing a
            # provider request after any capture-to-publication crash.
            result = _recover_or_close_spooled_response(
                layout,
                proof,
                intent,
                intent_sha256=intent_sha256,
                importer=importer,
            )
            records[intent.id] = result
            _write_live_resume(layout, plan, tuple(sorted(records)))
            _validate_newly_closed_live_result(
                layout,
                proof,
                intent,
                result,
                importer=importer,
            )
            if intent.is_entitlement_sentinel and result["disposition"] != "complete":
                return _live_report(
                    plan,
                    records,
                    requests_issued=issued,
                    completion="sentinel_failed",
                )
            if issued >= max_requests and any(
                candidate.id not in records for candidate in plan.intents
            ):
                return _live_report(
                    plan,
                    records,
                    requests_issued=issued,
                    completion="partial",
                )
        _require_current_execution_revision(
            proof,
            current_revision,
            enforce=enforce_execution_revision,
        )
        return _finalize_live_extension(
            layout,
            plan,
            proof,
            records,
            now=now,
            execution_revision=current_revision,
            enforce_execution_revision=enforce_execution_revision,
        )

APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT: Final = Path(
    "/home/volatility/Python_Projects/Quant_Data_Infra"
)
APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT: Final = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
)
_STAGE10_SCOPE_PATH: Final = Path("config/stage10_market_scope.json")


class _LiveArgumentFailure(Exception):
    """A sanitized CLI rejection."""


class _LiveConfigurationFailure(Exception):
    """A sanitized pre-network configuration rejection."""


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


def _require_exact_live_directory(
    value: object,
    expected: Path,
    *,
    must_exist: bool,
) -> Path:
    if not isinstance(value, str) or value != str(expected):
        raise _LiveArgumentFailure
    supplied = Path(value)
    try:
        if not supplied.is_absolute() or _has_symlink_component(supplied):
            raise ValueError
        resolved = supplied.resolve(strict=False)
        expected_resolved = expected.resolve(strict=False)
        info = resolved.lstat() if must_exist else resolved.parent.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise _LiveArgumentFailure from exc
    if (
        resolved != expected_resolved
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
    ):
        raise _LiveArgumentFailure
    return resolved


def _read_live_api_key(environment: Mapping[str, str]) -> str:
    value = environment.get("FMP_API_KEY")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _LiveConfigurationFailure
    return value


def _base_reconciliation_from_candidate(candidate: object) -> object:
    """Rebuild the authenticated base reconciliation from its immutable receipt."""

    from .stage10_repopulation import Stage10MarketReconciliation

    primitive = (
        candidate.to_primitive().get("evidence", {}).get("reconciliation")
        if callable(getattr(candidate, "to_primitive", None))
        else None
    )
    if not isinstance(primitive, Mapping):
        raise ConflictError(
            "Stage 10 history-extension base candidate reconciliation is invalid"
        )
    registry = primitive.get("registry")
    migration = primitive.get("migration")
    if not isinstance(registry, Mapping) or not isinstance(migration, Mapping):
        raise ConflictError(
            "Stage 10 history-extension base candidate reconciliation is invalid"
        )
    try:
        reconciliation = Stage10MarketReconciliation(
            scope_manifest_sha256=primitive["scope_manifest_sha256"],
            target_profile_id=primitive["target_profile_id"],
            registry_schema_version=registry["schema_version"],
            registry_version=registry["registry_version"],
            registry_source_sha256=registry["source_sha256"],
            migration_id=migration["id"],
            migration_sha256=migration["sha256"],
            universe_summaries=tuple(primitive["universe_summaries"]),
            roster_count=primitive["roster_count"],
            roster_sha256=primitive["roster_sha256"],
            failed_records=tuple(primitive["failed_records"]),
            failed_tickers=tuple(primitive["failed_tickers"]),
            failed_symbol_count=primitive["failed_symbol_count"],
            successful_symbol_count=primitive["successful_symbol_count"],
            failure_manifest_sha256=primitive["failure_manifest_sha256"],
            price_coverage=tuple(primitive["price_coverage"]),
            universe_capture_count=primitive["universe_capture_count"],
            price_capture_count=primitive["price_capture_count"],
            current_price_count=primitive["current_price_count"],
            immutable_version_count=primitive["immutable_version_count"],
            historical_membership_inferred=primitive["historical_membership_inferred"],
            russell_excluded=primitive["russell_excluded"],
            raw_captures_reparsed=primitive["raw_captures_reparsed"],
            canonical_rows_match_raw=primitive["canonical_rows_match_raw"],
            correction_lineage_verified=primitive["correction_lineage_verified"],
            stores_unchanged=primitive["stores_unchanged"],
            sha256=primitive["sha256"],
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ConflictError(
            "Stage 10 history-extension base candidate reconciliation is invalid"
        ) from exc
    if reconciliation.to_primitive() != dict(primitive):
        raise ConflictError(
            "Stage 10 history-extension base candidate reconciliation is invalid"
        )
    return reconciliation


def _load_verified_stage10_base(
    target_root: Path,
    project_root: Path,
) -> Stage10HistoryExtensionBaseProof:
    """Use accepted base readers, then reprove only current market semantics."""

    from . import stage10_backfill as base_backfill
    from .stage10_repopulation import reconcile_stage10_market
    from ..registry import load_registry, stage10_registry_profile
    from ..market.stage10_scope import load_stage10_market_scope

    try:
        cohort_registry = load_registry(
            project_root / "config" / "system_registry.json",
            project_root=project_root,
            environment={},
        )
        if (
            getattr(cohort_registry, "schema_version", None) == "1.7.0"
            and getattr(cohort_registry, "registry_version", None)
            in {"2.9.0", "2.10.0"}
        ):
            registry = stage10_registry_profile(cohort_registry)
        else:
            registry = cohort_registry
        base_backfill._registry_preflight(registry)
        scope = load_stage10_market_scope(project_root / _STAGE10_SCOPE_PATH)
        base_backfill._scope_preflight(scope)
        layout = base_backfill._existing_layout(target_root)
        state = base_backfill._read_resume(layout, scope=scope, registry=registry)
        completed = base_backfill._read_completed_receipt(
            layout,
            state,
            scope=scope,
            registry=registry,
        )
        if completed is None:
            raise ConflictError("Stage 10 history-extension requires a completed base receipt")
        store_map = base_backfill._validate_target_store_map(
            base_backfill._store_map_for(layout.stores),
            layout.stores,
        )
        base_backfill._validate_materialized_store_files(store_map, layout.stores)
        candidate = base_backfill._read_candidate_receipt(
            layout,
            scope=scope,
            registry=registry,
        )
        candidate_reconciliation = _base_reconciliation_from_candidate(candidate)
        expected_failures = tuple(
            record.to_primitive() for record in state.failed_records
        )
        if (
            candidate_reconciliation.failure_manifest_sha256
            != completed.failure_manifest_sha256
            or candidate_reconciliation.registry_source_sha256
            != registry.source_sha256
            or dumps_strict(candidate_reconciliation.to_primitive()["failed_records"])
            != dumps_strict(list(expected_failures))
        ):
            raise ConflictError(
                "Stage 10 history-extension base candidate does not match the completed receipt"
            )
        extension_root = _extension_root(target_root)
        if extension_root.exists() or extension_root.is_symlink():
            # Extension captures are additive, so a base-only reconciliation
            # would correctly see new data but incorrectly reject resumption.
            # The immutable journal and its exact captured prefix are proved
            # later under the extension lock before a further request.
            _require_private_directory(extension_root, "extension namespace")
            _require_private_directory(
                extension_root / _LIVE_DIRECTORY,
                "live extension namespace",
            )
            reconciliation = candidate_reconciliation
        else:
            reconciliation = reconcile_stage10_market(
                store_map,
                registry,
                scope,
                failed_records=expected_failures,
            )
            if (
                dumps_strict(reconciliation.to_primitive())
                != dumps_strict(candidate_reconciliation.to_primitive())
            ):
                raise ConflictError(
                    "Stage 10 history-extension base market reconciliation no longer matches the completed candidate"
                )
        roster = base_backfill._validate_roster(
            base_backfill._default_roster_reader(store_map, scope),
            scope=scope,
            state=state,
        )
    except (ConflictError, ValidationError, RegistryError, MigrationError, OSError):
        raise
    except Exception as exc:
        raise _LiveConfigurationFailure from exc
    failed = tuple(record.symbol for record in state.failed_records)
    verified = VerifiedStage10Base(
        target_profile_id=scope.target_profile_id,
        scope_manifest_sha256=scope.manifest_sha256,
        registry_source_sha256=registry.source_sha256,
        completion_sha256=completed.sha256,
        reconciliation_sha256=reconciliation.sha256,
        failure_manifest_sha256=completed.failure_manifest_sha256,
        roster_symbols=roster,
        inherited_failed_symbols=failed,
    )
    return Stage10HistoryExtensionBaseProof(
        verified_base=verified,
        store_map=store_map,
        registry=registry,
        scope=scope,
        base_reconciliation=reconciliation,
        cohort_registry=cohort_registry,
    )


def authorize_stage10_history_extension_402_transition() -> Mapping[str, object]:
    """Authorize only the sealed live 402 spool, without provider or credential access."""

    project_root = APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT
    target_root = APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT
    proof = _load_verified_stage10_base(target_root, project_root)
    plan = build_history_extension_plan(proof.verified_base)
    new_execution_revision = _extension_code_revision(proof)
    if new_execution_revision == TRANSITION_402_OLD_EXECUTION_REVISION:
        raise ConflictError("Stage 10 history-extension transition source did not change")
    layout = _open_live_layout(
        target_root,
        plan,
        TRANSITION_402_OLD_EXECUTION_REVISION,
    )
    with _acquire_live_run_lock(layout):
        transition_path = _transition_402_path(layout)
        if transition_path.exists() or transition_path.is_symlink():
            value = _read_execution_transition_402(
                layout,
                plan,
                new_execution_revision,
            )
            if value is None:
                raise ConflictError("Stage 10 history-extension transition is unavailable")
            return value
        records = _reconcile_live_journal(layout, plan)
        expected_ids = {intent.id for intent in plan.intents[:TRANSITION_402_CLOSED_REQUEST_COUNT]}
        if len(records) != TRANSITION_402_CLOSED_REQUEST_COUNT or set(records) != expected_ids:
            raise ConflictError("Stage 10 history-extension transition prefix differs")
        intent, _, _ = _validate_transition_402_source_evidence(layout, plan)
        if _read_live_result(
            layout,
            intent,
            intent_sha256=TRANSITION_402_PENDING_INTENT_SHA256,
        ) is not None:
            raise ConflictError("Stage 10 history-extension transition result already exists")
        receipt = _transition_402_receipt(plan, new_execution_revision)
        _write_json_exclusive(
            transition_path,
            receipt,
            maximum_bytes=_MAX_RECEIPT_BYTES,
        )
        _require_immutable_live_file(
            transition_path,
            "execution transition receipt",
            _MAX_RECEIPT_BYTES,
        )
        value = _read_execution_transition_402(
            layout,
            plan,
            new_execution_revision,
        )
        if value is None:
            raise ConflictError("Stage 10 history-extension transition is unavailable")
        return value


def authorize_stage10_history_extension_empty_transition() -> Mapping[str, object]:
    """Authorize only the sealed known-listed empty spool without a live call."""

    project_root = APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT
    target_root = APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT
    proof = _load_verified_stage10_base(target_root, project_root)
    plan = build_history_extension_plan(proof.verified_base)
    new_execution_revision = _extension_code_revision(proof)
    if new_execution_revision == TRANSITION_EMPTY_OLD_EXECUTION_REVISION:
        raise ConflictError(
            "Stage 10 history-extension empty transition source did not change"
        )
    unopened = _live_layout(target_root)
    receipt_exists = (
        _transition_empty_path(unopened).exists()
        or _transition_empty_path(unopened).is_symlink()
    )
    layout = _open_live_layout(
        target_root,
        plan,
        new_execution_revision
        if receipt_exists
        else TRANSITION_EMPTY_OLD_EXECUTION_REVISION,
    )
    with _acquire_live_run_lock(layout):
        transition_path = _transition_empty_path(layout)
        if transition_path.exists() or transition_path.is_symlink():
            value = _read_execution_transition_empty(
                layout,
                plan,
                new_execution_revision,
            )
            if value is None:
                raise ConflictError(
                    "Stage 10 history-extension empty transition is unavailable"
                )
            return value
        records = _reconcile_live_journal(
            layout,
            plan,
            repair_resume=False,
        )
        expected_ids = {
            intent.id
            for intent in plan.intents[:TRANSITION_EMPTY_CLOSED_REQUEST_COUNT]
        }
        if (
            len(records) != TRANSITION_EMPTY_CLOSED_REQUEST_COUNT
            or set(records) != expected_ids
        ):
            raise ConflictError(
                "Stage 10 history-extension empty transition prefix differs"
            )
        intent, _, _ = _validate_transition_empty_source_evidence(layout, plan)
        if _read_live_result(
            layout,
            intent,
            intent_sha256=TRANSITION_EMPTY_PENDING_INTENT_SHA256,
        ) is not None:
            raise ConflictError(
                "Stage 10 history-extension empty transition result already exists"
            )
        for later in plan.intents[TRANSITION_EMPTY_PENDING_ORDINAL:]:
            if any(
                path.exists() or path.is_symlink()
                for path in (
                    _live_intent_path(layout, later),
                    _spool_path(layout, later),
                    _raw_path(layout, later),
                    _live_result_path(layout, later),
                )
            ):
                raise ConflictError(
                    "Stage 10 history-extension empty transition has later evidence"
                )
        if (
            layout.completion.exists()
            or layout.completion.is_symlink()
            or layout.candidate.exists()
            or layout.candidate.is_symlink()
        ):
            raise ConflictError(
                "Stage 10 history-extension empty transition candidate already exists"
            )
        if not _empty_window_listing_status(proof, intent)[0]:
            raise ConflictError(
                "Stage 10 history-extension empty transition lacks listing evidence"
            )
        receipt = _transition_empty_receipt(
            layout, plan, new_execution_revision
        )
        _write_json_exclusive(
            transition_path,
            receipt,
            maximum_bytes=_MAX_RECEIPT_BYTES,
        )
        _require_immutable_live_file(
            transition_path,
            "empty execution transition receipt",
            _MAX_RECEIPT_BYTES,
        )
        value = _read_execution_transition_empty(
            layout,
            plan,
            new_execution_revision,
        )
        if value is None:
            raise ConflictError(
                "Stage 10 history-extension empty transition is unavailable"
            )
        return value


def authorize_stage10_history_extension_empty_chain_transition() -> Mapping[str, object]:
    """Bind the reviewed 402/empty receipts to corrected evidence semantics."""

    project_root = APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT
    target_root = APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT
    proof = _load_verified_stage10_base(target_root, project_root)
    plan = build_history_extension_plan(proof.verified_base)
    new_execution_revision = _extension_code_revision(proof)
    if new_execution_revision == TRANSITION_CHAIN_OLD_EXECUTION_REVISION:
        raise ConflictError(
            "Stage 10 history-extension successor transition source did not change"
        )
    unopened = _live_layout(target_root)
    receipt_exists = (
        _transition_empty_chain_path(unopened).exists()
        or _transition_empty_chain_path(unopened).is_symlink()
    )
    layout = _open_live_layout(
        target_root,
        plan,
        new_execution_revision
        if receipt_exists
        else TRANSITION_CHAIN_OLD_EXECUTION_REVISION,
    )
    with _acquire_live_run_lock(layout):
        transition_path = _transition_empty_chain_path(layout)
        if transition_path.exists() or transition_path.is_symlink():
            value = _read_execution_transition_empty_chain(
                layout,
                plan,
                new_execution_revision,
            )
            if value is None:
                raise ConflictError(
                    "Stage 10 history-extension successor transition is unavailable"
                )
            return value
        records = _reconcile_live_journal(
            layout,
            plan,
            repair_resume=False,
        )
        expected_ids = {
            intent.id
            for intent in plan.intents[:TRANSITION_CHAIN_CLOSED_REQUEST_COUNT]
        }
        if (
            len(records) != TRANSITION_CHAIN_CLOSED_REQUEST_COUNT
            or set(records) != expected_ids
        ):
            raise ConflictError(
                "Stage 10 history-extension successor transition prefix differs"
            )
        authorization_chain = _transition_empty_chain_source(layout, plan)
        intent = _transition_empty_intent(plan)
        if _read_live_result(
            layout,
            intent,
            intent_sha256=TRANSITION_CHAIN_PENDING_INTENT_SHA256,
        ) is not None:
            raise ConflictError(
                "Stage 10 history-extension successor transition result already exists"
            )
        for later in plan.intents[TRANSITION_CHAIN_PENDING_ORDINAL:]:
            if any(
                path.exists() or path.is_symlink()
                for path in (
                    _live_intent_path(layout, later),
                    _spool_path(layout, later),
                    _raw_path(layout, later),
                    _live_result_path(layout, later),
                )
            ):
                raise ConflictError(
                    "Stage 10 history-extension successor transition has later evidence"
                )
        if (
            layout.completion.exists()
            or layout.completion.is_symlink()
            or layout.candidate.exists()
            or layout.candidate.is_symlink()
        ):
            raise ConflictError(
                "Stage 10 history-extension successor transition candidate already exists"
            )
        receipt = _transition_empty_chain_receipt(
            layout,
            plan,
            new_execution_revision,
        )
        if receipt.get("authorization_chain") != authorization_chain:
            raise ConflictError(
                "Stage 10 history-extension successor authorization differs"
            )
        _write_json_exclusive(
            transition_path,
            receipt,
            maximum_bytes=_MAX_RECEIPT_BYTES,
        )
        _require_immutable_live_file(
            transition_path,
            "successor execution transition receipt",
            _MAX_RECEIPT_BYTES,
        )
        value = _read_execution_transition_empty_chain(
            layout,
            plan,
            new_execution_revision,
        )
        if value is None:
            raise ConflictError(
                "Stage 10 history-extension successor transition is unavailable"
            )
        return value


def _preflight_public_live_mode(
    target_root: Path,
    proof: Stage10HistoryExtensionBaseProof,
    mode: str,
) -> LiveHistoryExtensionRunReport | None:
    """Validate phase state before credential access or transport creation."""

    plan = build_history_extension_plan(proof.verified_base)
    revision = _extension_code_revision(proof)
    root = _live_root(target_root)
    if not root.exists() and not root.is_symlink():
        if mode == "continue_after_sentinel":
            raise ConflictError(
                "Stage 10 history-extension continuation requires the sentinel phase"
            )
        return None
    layout = _open_live_layout(target_root, plan, revision)
    with _acquire_live_run_lock(layout):
        records = _reconcile_live_journal(layout, plan)
        # An issued request with no durable response can never be retried. Reject
        # that crash state before touching the credential environment or building
        # a transport. A complete spool may still be recovered locally below.
        for intent in plan.intents:
            if intent.id in records:
                continue
            intent_sha256 = _read_live_intent(layout, intent)
            if intent_sha256 is None:
                continue
            if _read_live_spool(
                layout, intent, intent_sha256=intent_sha256
            ) is None:
                raise ConflictError(
                    "Stage 10 history-extension has an unresolved issued request; automatic retry is forbidden"
                )
        sentinel = records.get(plan.intents[0].id)
        if _read_live_completion(layout, plan, proof) is not None:
            return _revalidate_completed_live(
                layout,
                plan,
                proof,
                revision,
                enforce_execution_revision=True,
            )
        if mode == "continue_after_sentinel":
            if sentinel is None or sentinel["disposition"] != "complete":
                raise ConflictError(
                    "Stage 10 history-extension continuation requires the completed sentinel"
                )
            _validate_live_prefix(layout, plan, proof, records)
            return None
        if sentinel is None:
            return None
        if sentinel["disposition"] != "complete":
            return _live_report(
                plan, records, requests_issued=0, completion="sentinel_failed"
            )
        _validate_live_prefix(layout, plan, proof, records)
        return _live_report(
            plan, records, requests_issued=0, completion="partial"
        )


def _default_live_bindings() -> Stage10HistoryExtensionBindings:
    return Stage10HistoryExtensionBindings(
        base_verifier=_load_verified_stage10_base,
        transport_factory=lambda _api_key, store_map, scope: StdlibFmpStage10Transport(
            store_map,
            scope,
        ),
        importer_factory=lambda store_map, registry, scope: Stage10HistoryWindowImporter(
            store_map,
            registry,
            scope,
        ),
    )


def run_stage10_history_extension(
    project_root: Path,
    target_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    bindings: Stage10HistoryExtensionBindings | None = None,
    now: Callable[[], object] = lambda: datetime.now(timezone.utc),
    mode: str,
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> LiveHistoryExtensionRunReport:
    """Run the exact full manual extension with no configurable scope knobs."""

    if (
        not isinstance(project_root, Path)
        or not isinstance(target_root, Path)
        or project_root != APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT
        or target_root != APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT
        or not callable(now)
        or mode not in {"sentinel_only", "continue_after_sentinel"}
    ):
        raise ValidationError("Stage 10 history-extension live roots are invalid")
    active_bindings = _default_live_bindings() if bindings is None else bindings
    if not isinstance(active_bindings, Stage10HistoryExtensionBindings):
        raise ValidationError("Stage 10 history-extension live bindings are invalid")
    proof = active_bindings.base_verifier(target_root, project_root)
    if not isinstance(proof, Stage10HistoryExtensionBaseProof):
        raise ValidationError("Stage 10 history-extension base verifier is invalid")
    preflight = _preflight_public_live_mode(target_root, proof, mode)
    if preflight is not None:
        return preflight
    key = _read_live_api_key(environment if environment is not None else os.environ)
    transport = active_bindings.transport_factory(key, proof.store_map, proof.scope)
    importer = active_bindings.importer_factory(
        proof.store_map,
        proof.registry,
        proof.scope,
    )
    return run_live_history_extension_rehearsal(
        target_root,
        proof=proof,
        api_key=key,
        transport=transport,
        importer=importer,
        max_requests=(
            1
            if mode == "sentinel_only"
            else HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT
        ),
        now=now,
        require_completed_sentinel=(mode == "continue_after_sentinel"),
        stop_after_completed_sentinel=(mode == "sentinel_only"),
        monotonic=monotonic,
        sleeper=sleeper,
    )


def _parse_live_cli(argv: Sequence[str] | None) -> tuple[str, str, str]:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 5:
        raise _LiveArgumentFailure
    mode_flag = values.pop()
    modes = {
        "--sentinel-only": "sentinel_only",
        "--continue-after-sentinel": "continue_after_sentinel",
    }
    if mode_flag not in modes:
        raise _LiveArgumentFailure
    parsed: dict[str, str] = {}
    for index in range(0, 4, 2):
        flag, value = values[index], values[index + 1]
        if flag not in {"--project-root", "--target-root"} or flag in parsed:
            raise _LiveArgumentFailure
        parsed[flag] = value
    if set(parsed) != {"--project-root", "--target-root"}:
        raise _LiveArgumentFailure
    return (
        parsed["--project-root"],
        parsed["--target-root"],
        modes[mode_flag],
    )


def _write_live_error(stream: TextIO, error: str, exit_code: int) -> None:
    stream.write(
        dumps_strict(
            {
                "contract": _LIVE_ERROR_CONTRACT,
                "error": error,
                "exit_code": exit_code,
                "version": _LIVE_VERSION,
            }
        )
        + "\n"
    )
    stream.flush()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    bindings: Stage10HistoryExtensionBindings | None = None,
    now: Callable[[], object] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], object] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Run only the exact authorized manual Stage 10 extension CLI."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        project_text, target_text, mode = _parse_live_cli(argv)
        project_root = _require_exact_live_directory(
            project_text,
            APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT,
            must_exist=True,
        )
        target_root = _require_exact_live_directory(
            target_text,
            APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT,
            must_exist=True,
        )
        report = run_stage10_history_extension(
            project_root,
            target_root,
            environment=environment,
            bindings=bindings,
            now=now,
            mode=mode,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        output.write(dumps_strict(report.to_primitive()) + "\n")
        output.flush()
        return 0
    except _LiveArgumentFailure:
        error, exit_code = "invalid_arguments", 64
    except (_LiveConfigurationFailure, CapabilityUnavailableError, RegistryError):
        error, exit_code = "invalid_configuration", 78
    except StoreUnavailableError:
        error, exit_code = "unavailable", 69
    except OSError:
        error, exit_code = "local_io", 74
    except ConflictError:
        error, exit_code = "conflict", 75
    except (ValidationError, MigrationError, ResourceLimitError):
        error, exit_code = "contract_failure", 70
    except Exception:
        error, exit_code = "internal_failure", 70
    _write_live_error(errors, error, exit_code)
    return exit_code


__all__ = (
    "HISTORY_EXTENSION_CONTRACT",
    "HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT",
    "HISTORY_EXTENSION_EXPECTED_ROSTER_COUNT",
    "HISTORY_EXTENSION_NAMESPACE",
    "HISTORY_EXTENSION_WINDOWS",
    "HistoryExtensionIntent",
    "HistoryExtensionPlan",
    "HistoryExtensionResultDraft",
    "HistoryExtensionRunReport",
    "HistoryExtensionWindow",
    "VerifiedStage10Base",
    "authorize_stage10_history_extension_402_transition",
    "authorize_stage10_history_extension_empty_transition",
    "authorize_stage10_history_extension_empty_chain_transition",
    "build_history_extension_plan",
    "main",
    "run_offline_history_extension_rehearsal",
)


if __name__ == "__main__":  # pragma: no cover - direct fail-closed module use
    raise SystemExit(main())
