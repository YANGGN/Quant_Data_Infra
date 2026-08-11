"""Private, path-free operational receipts for manual-first job execution.

This module is deliberately independent from job planning and lock acquisition.
It validates only the bounded operational evidence that those components hand
to it, and it writes that evidence beneath an explicit private state root.
No constructor call creates filesystem state; publication is the sole mutating
operation in this module.
"""

from __future__ import annotations

import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, ensure_number_within_limits


RECEIPT_SCHEMA_VERSION = "1.0.0"
MAX_RECEIPT_BYTES = 64 * 1024
MAX_LOG_EVENTS = 64
MAX_LOG_FIELDS = 16
MAX_MESSAGE_LENGTH = 160
MAX_SEQUENCE_ITEMS = 64

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_LOCK_ALIAS_RE = re.compile(r"store-[1-4]\Z")
_KEY_PREFIX_RE = re.compile(r"[0-9a-f]{12}\Z")
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SEMVER_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_PERIOD_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
_CODE_REVISION_RE = re.compile(r"[0-9a-f]{7,64}\Z")
_TIMEZONE_COMPONENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]*\Z")

_STORE_ROLES = frozenset({"market", "macro", "company", "news"})
_LOCK_OUTCOMES = frozenset({"acquired", "timed_out", "failed", "skipped"})
_STEP_OUTCOMES = frozenset(
    {"succeeded", "unchanged", "skipped", "partial", "failed", "timed_out", "blocked"}
)
_SEMANTIC_OUTCOMES = frozenset(
    {"changed", "unchanged", "skipped", "partial", "failed", "succeeded"}
)
_EXIT_CODES = frozenset({0, 64, 69, 70, 74, 75, 78, 124})
_CALENDAR_DECISIONS = frozenset({"run", "eligible", "skipped", "not_applicable"})

_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:"
    r"://|(?:https?|ftp|file|data|mailto):|"
    r"(?:^|[^A-Za-z0-9_])(?:password|passwd|secret|token|api[ _-]?key|"
    r"authorization|bearer|cookie|credential|private[ _-]?key)(?:$|[^A-Za-z0-9_])|"
    r"(?:^|[^A-Za-z0-9_])(?:select|insert|update|delete|drop|alter|create|"
    r"pragma|attach|detach|vacuum|sqlite_master|from|where|join)(?:$|[^A-Za-z0-9_])"
    r")",
    re.IGNORECASE,
)
_FORBIDDEN_FIELD_KEY_RE = re.compile(
    r"(?:env(?:ironment)?|path|file|dir(?:ectory)?|sql|query|url|uri|"
    r"body|payload|content|raw|credential|secret|token|password|passwd|"
    r"authorization|cookie|command|header)",
    re.IGNORECASE,
)


def _require_text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValidationError(f"{field_name} must be a bounded normalized string")
    return value


def _require_identifier(value: object, field_name: str, *, maximum: int = 96) -> str:
    text = _require_text(value, field_name, maximum=maximum)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValidationError(f"{field_name} must be a lowercase safe identifier")
    return text


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name, maximum=64)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _require_safe_message(value: object, field_name: str) -> str:
    text = _require_text(value, field_name, maximum=MAX_MESSAGE_LENGTH)
    if not text.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValidationError(f"{field_name} must use printable ASCII text")
    if "/" in text or "\\" in text or "{" in text or "}" in text or "[" in text or "]" in text:
        raise ValidationError(f"{field_name} must not include path or provider-body material")
    if _FORBIDDEN_TEXT_RE.search(text) is not None:
        raise ValidationError(f"{field_name} contains prohibited sensitive material")
    return text


def _require_duration(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field_name} must be a finite Decimal")
    ensure_number_within_limits(value)
    exponent = value.as_tuple().exponent
    if abs(int(exponent)) > 6 or value < 0 or value > Decimal("86400"):
        raise ValidationError(f"{field_name} is outside the supported bounds")
    return value


def _require_exit_code(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in _EXIT_CODES:
        raise ValidationError(f"{field_name} is unsupported")
    return value


def _require_timestamp(value: object, field_name: str) -> tuple[str, datetime]:
    text = _require_text(value, field_name, maximum=40)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise ValidationError(f"{field_name} must be an offset-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(
            f"{text[:-1]}+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an offset-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an offset-aware ISO timestamp")
    return text, parsed


def _require_timezone(value: object) -> str:
    text = _require_text(value, "timezone", maximum=64)
    # IANA timezone names are the one permitted slash-bearing control value;
    # a component must never be empty, traversal-like, or a filesystem path.
    if any(
        not part
        or part in {".", ".."}
        or _TIMEZONE_COMPONENT_RE.fullmatch(part) is None
        for part in text.split("/")
    ):
        raise ValidationError("timezone must be a recognized IANA timezone")
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError("timezone must be a recognized IANA timezone") from exc
    return text


def validate_receipt_timestamp(value: object, field_name: str) -> tuple[str, datetime]:
    """Validate one receipt timestamp before any operational publication."""

    return _require_timestamp(value, field_name)


def validate_timezone_name(value: object) -> str:
    """Validate the configured IANA timezone before a job is attempted."""

    return _require_timezone(value)


def validate_code_revision(value: object) -> str | None:
    """Validate an optional source revision before a job is attempted."""

    if value is None:
        return None
    text = _require_text(value, "code_revision", maximum=64)
    if _CODE_REVISION_RE.fullmatch(text) is None:
        raise ValidationError("code_revision must be a lowercase source revision")
    return text


def validate_safe_message(value: object, field_name: str) -> str:
    """Apply the exact receipt-safe text policy at producer boundaries."""

    return _require_safe_message(value, field_name)


def validate_marker_period(value: object) -> str:
    """Validate the normalized monthly success-marker period."""

    text = _require_text(value, "period", maximum=7)
    if _PERIOD_RE.fullmatch(text) is None:
        raise ValidationError("period must be a normalized YYYY-MM value")
    return text


def _tuple_of(value: object, field_name: str, *, maximum: int) -> tuple[Any, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValidationError(f"{field_name} must be a tuple or list")
    items = tuple(value)
    if len(items) > maximum:
        raise ResourceLimitError(f"{field_name} exceeds the supported bound")
    return items


def _unique_texts(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return tuple(values)


def _validate_error_pairs(
    codes: object,
    messages: object,
    *,
    field_prefix: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    code_items = _tuple_of(codes, f"{field_prefix}_codes", maximum=16)
    message_items = _tuple_of(messages, f"{field_prefix}_messages", maximum=16)
    if len(code_items) != len(message_items):
        raise ValidationError(f"{field_prefix} codes and messages must have equal lengths")
    normalized_codes = tuple(
        _require_identifier(item, f"{field_prefix}_codes") for item in code_items
    )
    normalized_messages = tuple(
        _require_safe_message(item, f"{field_prefix}_messages") for item in message_items
    )
    return _unique_texts(normalized_codes, f"{field_prefix}_codes"), normalized_messages


def _primitive_list(values: Sequence[object]) -> list[object]:
    return list(values)


@dataclass(frozen=True, slots=True)
class LockWaitReceipt:
    """Path-free acquisition evidence for one host-assigned physical lock."""

    alias: str
    key_prefix: str
    wait_seconds: Decimal
    outcome: str

    def __post_init__(self) -> None:
        alias = _require_text(self.alias, "alias", maximum=16)
        if _LOCK_ALIAS_RE.fullmatch(alias) is None:
            raise ValidationError("alias must be a bounded store alias")
        key_prefix = _require_text(self.key_prefix, "key_prefix", maximum=12)
        if _KEY_PREFIX_RE.fullmatch(key_prefix) is None:
            raise ValidationError("key_prefix must be twelve lowercase hexadecimal characters")
        outcome = _require_identifier(self.outcome, "outcome", maximum=32)
        if outcome not in _LOCK_OUTCOMES:
            raise ValidationError("lock receipt outcome is unsupported")
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "key_prefix", key_prefix)
        object.__setattr__(self, "wait_seconds", _require_duration(self.wait_seconds, "wait_seconds"))
        object.__setattr__(self, "outcome", outcome)

    def to_primitive(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "key_prefix": self.key_prefix,
            "wait_seconds": self.wait_seconds,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class StepRunReceipt:
    """Bounded evidence for one planned execution step."""

    step_id: str
    outcome: str
    attempts: int
    elapsed_seconds: Decimal
    timeout: bool
    exit_code: int
    ingestion_run_ids: tuple[str, ...]
    error_codes: tuple[str, ...]
    error_messages: tuple[str, ...]

    def __post_init__(self) -> None:
        step_id = _require_identifier(self.step_id, "step_id")
        outcome = _require_identifier(self.outcome, "outcome", maximum=32)
        if outcome not in _STEP_OUTCOMES:
            raise ValidationError("step receipt outcome is unsupported")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or not 0 <= self.attempts <= 16:
            raise ValidationError("attempts must be an integer from zero through sixteen")
        if outcome in {"skipped", "blocked"} and self.attempts != 0:
            raise ValidationError("skipped or blocked steps must have zero attempts")
        if outcome not in {"skipped", "blocked"} and self.attempts == 0:
            raise ValidationError("attempted step outcomes require at least one attempt")
        if not isinstance(self.timeout, bool):
            raise ValidationError("timeout must be boolean")
        if self.timeout != (outcome == "timed_out"):
            raise ValidationError("timeout must match a timed_out step outcome")
        exit_code = _require_exit_code(self.exit_code, "exit_code")
        if outcome in {"succeeded", "unchanged", "skipped"} and exit_code != 0:
            raise ValidationError("successful or skipped steps require exit code zero")
        if outcome in {"partial", "failed", "timed_out", "blocked"} and exit_code == 0:
            raise ValidationError("unsuccessful step outcomes require a nonzero exit code")
        if outcome == "timed_out" and exit_code != 124:
            raise ValidationError("timed_out steps require exit code 124")
        run_ids = tuple(
            _require_sha256(item, "ingestion_run_ids")
            for item in _tuple_of(self.ingestion_run_ids, "ingestion_run_ids", maximum=MAX_SEQUENCE_ITEMS)
        )
        error_codes, error_messages = _validate_error_pairs(
            self.error_codes,
            self.error_messages,
            field_prefix="error",
        )
        if outcome in {"failed", "partial", "timed_out", "blocked"} and not error_codes:
            raise ValidationError("unsuccessful step outcomes require an error code and message")
        if outcome in {"succeeded", "unchanged", "skipped"} and error_codes:
            raise ValidationError("successful or skipped steps must not include errors")
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "elapsed_seconds", _require_duration(self.elapsed_seconds, "elapsed_seconds"))
        object.__setattr__(self, "exit_code", exit_code)
        object.__setattr__(self, "ingestion_run_ids", _unique_texts(run_ids, "ingestion_run_ids"))
        object.__setattr__(self, "error_codes", error_codes)
        object.__setattr__(self, "error_messages", error_messages)

    def to_primitive(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "elapsed_seconds": self.elapsed_seconds,
            "timeout": self.timeout,
            "exit_code": self.exit_code,
            "ingestion_run_ids": _primitive_list(self.ingestion_run_ids),
            "error_codes": _primitive_list(self.error_codes),
            "error_messages": _primitive_list(self.error_messages),
        }


@dataclass(frozen=True, slots=True)
class JobRunReceipt:
    """Immutable, private, path-free receipt for one manually triggered job."""

    schema_version: str
    run_id: str
    job_id: str
    job_version: str
    plan_sha256: str
    trigger_kind: str
    requested_at: str
    started_at: str
    finished_at: str
    timezone: str
    calendar_decision: str
    skip_reason: str | None
    logical_stores: tuple[str, ...]
    lock_order: tuple[LockWaitReceipt, ...]
    steps: tuple[StepRunReceipt, ...]
    semantic_outcome: str
    committed_store_aliases: tuple[str, ...]
    ingestion_run_ids: tuple[str, ...]
    aggregate_exit_code: int
    code_revision: str | None
    warnings: tuple[str, ...]
    error_codes: tuple[str, ...]
    error_messages: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise ValidationError("receipt schema_version is unsupported")
        run_id = _require_sha256(self.run_id, "run_id")
        job_id = _require_identifier(self.job_id, "job_id")
        job_version = _require_text(self.job_version, "job_version", maximum=32)
        if _SEMVER_RE.fullmatch(job_version) is None:
            raise ValidationError("job_version must be a normalized semantic version")
        plan_sha256 = _require_sha256(self.plan_sha256, "plan_sha256")
        if self.trigger_kind != "manual":
            raise ValidationError("only manual trigger_kind is authorized in Stage 7")
        requested_at, requested_time = _require_timestamp(self.requested_at, "requested_at")
        started_at, started_time = _require_timestamp(self.started_at, "started_at")
        finished_at, finished_time = _require_timestamp(self.finished_at, "finished_at")
        if not requested_time <= started_time <= finished_time:
            raise ValidationError("receipt timestamps must be chronological")
        timezone = _require_timezone(self.timezone)
        calendar_decision = _require_identifier(
            self.calendar_decision, "calendar_decision", maximum=32
        )
        if calendar_decision not in _CALENDAR_DECISIONS:
            raise ValidationError("calendar_decision is unsupported")
        if self.skip_reason is None:
            skip_reason = None
        else:
            skip_reason = _require_safe_message(self.skip_reason, "skip_reason")
        if calendar_decision == "skipped" and skip_reason is None:
            raise ValidationError("calendar-skipped jobs require skip_reason")
        if calendar_decision != "skipped" and skip_reason is not None:
            raise ValidationError("skip_reason is only valid for calendar-skipped jobs")
        logical_stores = tuple(
            _require_identifier(item, "logical_stores", maximum=16)
            for item in _tuple_of(self.logical_stores, "logical_stores", maximum=4)
        )
        if any(store not in _STORE_ROLES for store in logical_stores):
            raise ValidationError("logical_stores contains an unsupported store")
        _unique_texts(logical_stores, "logical_stores")
        lock_order = _tuple_of(self.lock_order, "lock_order", maximum=4)
        if not all(isinstance(item, LockWaitReceipt) for item in lock_order):
            raise ValidationError("lock_order must contain LockWaitReceipt values")
        lock_aliases = tuple(item.alias for item in lock_order)
        _unique_texts(lock_aliases, "lock_order aliases")
        if len({item.key_prefix for item in lock_order}) != len(lock_order):
            raise ValidationError("lock_order key prefixes must not repeat")
        steps = _tuple_of(self.steps, "steps", maximum=MAX_SEQUENCE_ITEMS)
        if not all(isinstance(item, StepRunReceipt) for item in steps):
            raise ValidationError("steps must contain StepRunReceipt values")
        _unique_texts(tuple(item.step_id for item in steps), "step ids")
        semantic_outcome = _require_identifier(
            self.semantic_outcome, "semantic_outcome", maximum=32
        )
        if semantic_outcome not in _SEMANTIC_OUTCOMES:
            raise ValidationError("semantic_outcome is unsupported")
        aggregate_exit_code = _require_exit_code(
            self.aggregate_exit_code, "aggregate_exit_code"
        )
        failed_step_outcomes = {"failed", "partial", "timed_out", "blocked"}
        has_failed_step = any(item.outcome in failed_step_outcomes for item in steps)
        if semantic_outcome in {"changed", "unchanged", "skipped", "succeeded"}:
            if aggregate_exit_code != 0 or has_failed_step:
                raise ValidationError("successful semantic outcomes require zero exit and no failed step")
        else:
            if aggregate_exit_code == 0:
                raise ValidationError("failed or partial semantic outcomes require a nonzero exit code")
        if semantic_outcome == "skipped" and calendar_decision != "skipped":
            raise ValidationError("skipped semantic outcome requires calendar-skipped decision")
        if calendar_decision == "skipped" and semantic_outcome != "skipped":
            raise ValidationError("calendar-skipped decision requires skipped semantic outcome")
        committed_aliases = tuple(
            _require_text(item, "committed_store_aliases", maximum=16)
            for item in _tuple_of(
                self.committed_store_aliases,
                "committed_store_aliases",
                maximum=4,
            )
        )
        if any(_LOCK_ALIAS_RE.fullmatch(item) is None for item in committed_aliases):
            raise ValidationError("committed_store_aliases must use bounded store aliases")
        _unique_texts(committed_aliases, "committed_store_aliases")
        if not set(committed_aliases).issubset(set(lock_aliases)):
            raise ValidationError("committed stores must be included in lock_order")
        ingestion_run_ids = tuple(
            _require_sha256(item, "ingestion_run_ids")
            for item in _tuple_of(
                self.ingestion_run_ids,
                "ingestion_run_ids",
                maximum=MAX_SEQUENCE_ITEMS,
            )
        )
        _unique_texts(ingestion_run_ids, "ingestion_run_ids")
        code_revision = validate_code_revision(self.code_revision)
        warnings = tuple(
            _require_safe_message(item, "warnings")
            for item in _tuple_of(self.warnings, "warnings", maximum=16)
        )
        error_codes, error_messages = _validate_error_pairs(
            self.error_codes,
            self.error_messages,
            field_prefix="error",
        )
        if semantic_outcome in {"partial", "failed"} and not error_codes:
            raise ValidationError("failed or partial jobs require an error code and message")
        if semantic_outcome in {"changed", "unchanged", "skipped", "succeeded"} and error_codes:
            raise ValidationError("successful jobs must not include error codes or messages")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "job_version", job_version)
        object.__setattr__(self, "plan_sha256", plan_sha256)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "calendar_decision", calendar_decision)
        object.__setattr__(self, "skip_reason", skip_reason)
        object.__setattr__(self, "logical_stores", logical_stores)
        object.__setattr__(self, "lock_order", lock_order)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "semantic_outcome", semantic_outcome)
        object.__setattr__(self, "committed_store_aliases", committed_aliases)
        object.__setattr__(self, "ingestion_run_ids", ingestion_run_ids)
        object.__setattr__(self, "aggregate_exit_code", aggregate_exit_code)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "error_codes", error_codes)
        object.__setattr__(self, "error_messages", error_messages)

    def to_primitive(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "job_version": self.job_version,
            "plan_sha256": self.plan_sha256,
            "trigger_kind": self.trigger_kind,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timezone": self.timezone,
            "calendar_decision": self.calendar_decision,
            "skip_reason": self.skip_reason,
            "logical_stores": _primitive_list(self.logical_stores),
            "lock_order": [item.to_primitive() for item in self.lock_order],
            "steps": [item.to_primitive() for item in self.steps],
            "semantic_outcome": self.semantic_outcome,
            "committed_store_aliases": _primitive_list(self.committed_store_aliases),
            "ingestion_run_ids": _primitive_list(self.ingestion_run_ids),
            "aggregate_exit_code": self.aggregate_exit_code,
            "code_revision": self.code_revision,
            "warnings": _primitive_list(self.warnings),
            "error_codes": _primitive_list(self.error_codes),
            "error_messages": _primitive_list(self.error_messages),
        }


class PrivateJobState:
    """Private receipt/log state rooted at one caller-selected absolute path."""

    def __init__(self, state_root: str | Path, *, dry_run: bool = False) -> None:
        if isinstance(state_root, str) and not state_root.strip():
            raise ValidationError("state_root must be an explicit absolute directory")
        if not isinstance(state_root, (str, Path)):
            raise ValidationError("state_root must be an explicit absolute directory")
        candidate = Path(state_root)
        if not candidate.is_absolute():
            raise ValidationError("state_root must be an explicit absolute directory")
        try:
            root = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError("state_root must be an explicit absolute directory") from exc
        if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
            raise ValidationError("state_root is too broad")
        if root.exists() and not root.is_dir():
            raise ValidationError("state_root must be a directory")
        if not isinstance(dry_run, bool):
            raise ValidationError("dry_run must be boolean")
        self._state_root = root
        self._dry_run = dry_run

    @property
    def state_root(self) -> Path:
        """The explicit private root; accessing it never creates state."""

        return self._state_root

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def _ensure_private_directory(self, directory: Path) -> None:
        if self._dry_run:
            raise ValidationError("dry-run state cannot publish operational evidence")
        try:
            self._state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self._state_root.is_dir() or self._state_root.is_symlink():
                raise OSError("private state root is not a directory")
            os.chmod(self._state_root, 0o700)
            directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            if not directory.is_dir() or directory.is_symlink():
                raise OSError("private state directory is not a directory")
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise OSError("Private state directory publication failed") from exc

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _publish_bytes(self, directory_name: str, final_name: str, payload: bytes) -> Path:
        if not payload or len(payload) > MAX_RECEIPT_BYTES:
            raise ResourceLimitError("Private operational evidence exceeds the supported bound")
        directory = self._state_root / directory_name
        final_path = directory / final_name
        self._ensure_private_directory(directory)
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Private operational evidence already exists")
        temporary_path = directory / f".{final_name}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        linked = False
        complete = False
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic, no-replace publication of the fully fsynced staged file.
            os.link(temporary_path, final_path)
            linked = True
            os.unlink(temporary_path)
            os.chmod(final_path, 0o600)
            self._fsync_directory(directory)
            complete = True
            return final_path
        except FileExistsError as exc:
            raise ConflictError("Private operational evidence already exists") from exc
        except OSError as exc:
            raise OSError("Private operational evidence publication failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not complete and linked:
                try:
                    final_path.unlink(missing_ok=True)
                    self._fsync_directory(directory)
                except OSError:
                    pass
            if not complete:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _preflight_target(self, directory_name: str, final_name: str) -> Path:
        directory = self._state_root / directory_name
        final_path = directory / final_name
        if self._dry_run:
            raise ValidationError("dry-run state cannot publish operational evidence")
        if self._state_root.exists() and (
            not self._state_root.is_dir() or self._state_root.is_symlink()
        ):
            raise ValidationError("private state root is unavailable")
        if directory.exists() and (not directory.is_dir() or directory.is_symlink()):
            raise ValidationError("private state directory is unavailable")
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Private operational evidence already exists")
        return final_path

    def preflight_job_evidence(
        self,
        run_id: str,
        job_id: str,
        marker_period: str | None,
    ) -> None:
        """Validate every immutable evidence target without creating state."""

        normalized_run_id = _require_sha256(run_id, "run_id")
        normalized_job_id = _require_identifier(job_id, "job_id")
        self._preflight_target("logs", f"{normalized_run_id}.json")
        self._preflight_target("receipts", f"{normalized_run_id}.json")
        if marker_period is not None:
            if normalized_job_id != "macro-monthly":
                raise ValidationError("marker period is only valid for macro-monthly")
            period = validate_marker_period(marker_period)
            self._preflight_target("markers", f"macro-monthly-{period}.json")

    @staticmethod
    def _strict_payload(value: object) -> bytes:
        try:
            encoded = dumps_strict(value, max_bytes=MAX_RECEIPT_BYTES)
        except (ValidationError, ResourceLimitError):
            raise
        return f"{encoded}\n".encode("utf-8")

    def publish_receipt(self, receipt: JobRunReceipt) -> Path:
        """Atomically publish one immutable receipt for a non-dry job run."""

        if not isinstance(receipt, JobRunReceipt):
            raise ValidationError("publish_receipt requires JobRunReceipt")
        return self._publish_bytes(
            "receipts",
            f"{receipt.run_id}.json",
            self._strict_payload(receipt.to_primitive()),
        )

    @staticmethod
    def _validate_log_scalar(value: object, field_name: str) -> object:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value.bit_length() > 53:
                raise ValidationError(f"{field_name} integer exceeds the supported bound")
            return value
        if isinstance(value, Decimal):
            return _require_duration(value, field_name)
        if isinstance(value, str):
            return _require_safe_message(value, field_name)
        raise ValidationError(f"{field_name} must be a bounded scalar")

    @classmethod
    def _validate_log_events(cls, events: object) -> list[dict[str, object]]:
        entries = _tuple_of(events, "events", maximum=MAX_LOG_EVENTS)
        normalized: list[dict[str, object]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping) or set(entry) != {"event", "fields"}:
                raise ValidationError("Each log event must contain only event and fields")
            event_name = _require_identifier(entry["event"], "event", maximum=64)
            fields = entry["fields"]
            if not isinstance(fields, Mapping) or len(fields) > MAX_LOG_FIELDS:
                raise ValidationError("Log event fields exceed the supported bound")
            normalized_fields: dict[str, object] = {}
            for key, value in fields.items():
                field_name = _require_identifier(key, "log field", maximum=64)
                if _FORBIDDEN_FIELD_KEY_RE.search(field_name) is not None:
                    raise ValidationError("Log field name is prohibited")
                normalized_fields[field_name] = cls._validate_log_scalar(
                    value,
                    f"events[{index}].{field_name}",
                )
            normalized.append({"event": event_name, "fields": normalized_fields})
        return normalized

    def publish_log(self, run_id: str, events: object) -> Path:
        """Atomically publish one bounded immutable structured event log."""

        normalized_run_id = _require_sha256(run_id, "run_id")
        normalized_events = self._validate_log_events(events)
        return self._publish_bytes(
            "logs",
            f"{normalized_run_id}.json",
            self._strict_payload(normalized_events),
        )

    def publish_monthly_success_marker(self, receipt: JobRunReceipt, period: str) -> Path:
        """Publish the immutable marker only after a complete macro-monthly success."""

        if not isinstance(receipt, JobRunReceipt):
            raise ValidationError("publish_monthly_success_marker requires JobRunReceipt")
        if receipt.job_id != "macro-monthly":
            raise ValidationError("Monthly success markers require the macro-monthly job")
        if (
            receipt.aggregate_exit_code != 0
            or receipt.semantic_outcome not in {"succeeded", "changed", "unchanged"}
        ):
            raise ValidationError("Monthly success markers require a successful zero-exit receipt")
        if not receipt.steps or any(
            step.outcome not in {"succeeded", "unchanged"} for step in receipt.steps
        ):
            raise ValidationError("Monthly success markers require every step to complete")
        normalized_period = validate_marker_period(period)
        marker = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "marker_kind": "macro_monthly_success",
            "period": normalized_period,
            "run_id": receipt.run_id,
            "job_id": receipt.job_id,
            "job_version": receipt.job_version,
            "plan_sha256": receipt.plan_sha256,
            "finished_at": receipt.finished_at,
            "aggregate_exit_code": receipt.aggregate_exit_code,
            "semantic_outcome": receipt.semantic_outcome,
        }
        return self._publish_bytes(
            "markers",
            f"macro-monthly-{normalized_period}.json",
            self._strict_payload(marker),
        )


    def publish_job_evidence(
        self,
        receipt: JobRunReceipt,
        events: object,
        marker_period: str | None,
    ) -> tuple[Path, Path | None, Path]:
        """Publish one logical evidence bundle with the receipt as commit point."""

        if not isinstance(receipt, JobRunReceipt):
            raise ValidationError("publish_job_evidence requires JobRunReceipt")
        marker_required = (
            receipt.job_id == "macro-monthly"
            and receipt.aggregate_exit_code == 0
            and receipt.semantic_outcome in {"succeeded", "changed", "unchanged"}
        )
        effective_period = marker_period if marker_required else None
        if marker_required and effective_period is None:
            raise ValidationError("successful macro-monthly evidence requires a marker period")
        if not marker_required and marker_period is not None:
            raise ValidationError("marker period is invalid for this receipt")

        normalized_events = self._validate_log_events(events)
        self._strict_payload(normalized_events)
        self._strict_payload(receipt.to_primitive())
        self.preflight_job_evidence(receipt.run_id, receipt.job_id, effective_period)

        created: list[Path] = []
        marker_path: Path | None = None
        try:
            log_path = self.publish_log(receipt.run_id, normalized_events)
            created.append(log_path)
            if marker_required:
                assert effective_period is not None
                marker_path = self.publish_monthly_success_marker(
                    receipt,
                    effective_period,
                )
                created.append(marker_path)
            receipt_path = self.publish_receipt(receipt)
            return log_path, marker_path, receipt_path
        except Exception:
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                    self._fsync_directory(path.parent)
                except OSError:
                    pass
            raise


__all__ = [
    "MAX_LOG_EVENTS",
    "MAX_LOG_FIELDS",
    "MAX_MESSAGE_LENGTH",
    "MAX_RECEIPT_BYTES",
    "RECEIPT_SCHEMA_VERSION",
    "JobRunReceipt",
    "LockWaitReceipt",
    "PrivateJobState",
    "StepRunReceipt",
    "validate_code_revision",
    "validate_marker_period",
    "validate_receipt_timestamp",
    "validate_safe_message",
    "validate_timezone_name",
]
