"""Deterministic, pre-lock retry policy for Stage 7 fixture rehearsals.

This module deliberately has no clock, sleeper, provider, database, or lock
dependency.  The runner supplies elapsed-time and sleep behavior explicitly so
that every retry decision is bounded and testable before a write lock exists.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Final

from ..errors import ValidationError


MAX_PREPARE_ATTEMPTS: Final[int] = 3
MAX_RETRY_DELAY_SECONDS: Final[Decimal] = Decimal("30")

# Stage 7 has one frozen retry class.  The values are intentionally small and
# deterministic: no jitter is used in an offline rehearsal.
_COLLECTOR_DECLARED_POLICY: Final["RetryPolicy"]

_ERROR_EXIT_CODES: Final = MappingProxyType(
    {
        "invalid_plan": 64,
        "unavailable": 69,
        "internal": 70,
        "schema": 70,
        "io": 74,
        "lock_conflict": 75,
        "temporary": 75,
        "transport": 75,
        "rate_limited": 75,
        "timeout": 124,
        "configuration": 78,
    }
)
RETRYABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {"temporary", "transport", "rate_limited"}
)
NON_RETRYABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    set(_ERROR_EXIT_CODES) - set(RETRYABLE_ERROR_CODES)
)

_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_MESSAGE_RE = re.compile(
    r"(?:"
    r"://|(?:https?|ftp|file|data|mailto):|"
    r"(?:^|[^A-Za-z0-9_])(?:password|passwd|secret|token|api[ _-]?key|"
    r"authorization|bearer|cookie|credential|private[ _-]?key)(?:$|[^A-Za-z0-9_])|"
    r"(?:^|[^A-Za-z0-9_])(?:select|insert|update|delete|drop|alter|create|"
    r"pragma|attach|detach|vacuum|sqlite_master|from|where|join)(?:$|[^A-Za-z0-9_])"
    r")",
    re.IGNORECASE,
)


def _decimal_seconds(value: object, field_name: str, *, maximum: Decimal) -> Decimal:
    """Normalize finite nonnegative duration input without ambient time."""

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValidationError(f"{field_name} must be a finite nonnegative duration")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError(f"{field_name} must be a finite nonnegative duration")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a finite nonnegative duration") from exc
    if not result.is_finite() or result < 0 or result > maximum:
        raise ValidationError(f"{field_name} is outside the supported bounds")
    return result


def _safe_message(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("safe_message must be bounded printable ASCII text")
    text = value.strip()
    if not text or len(text) > 160 or not text.isascii():
        raise ValidationError("safe_message must be bounded printable ASCII text")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValidationError("safe_message must be bounded printable ASCII text")
    if any(character in text for character in ("/", "\\", "{", "}", "[", "]")):
        raise ValidationError("safe_message must not include paths or provider material")
    if _SAFE_MESSAGE_RE.search(text) is not None:
        raise ValidationError("safe_message contains prohibited sensitive material")
    return text


class JobStepError(Exception):
    """One bounded, receipt-safe terminal classification from a step executor.

    Executors must raise this rather than propagating provider bodies, paths, or
    implementation exceptions.  A retry is possible only for explicitly
    classified transient prepare failures.
    """

    __slots__ = ("code", "exit_code", "retryable", "retry_after_seconds", "safe_message")

    def __init__(
        self,
        code: str,
        exit_code: int | None = None,
        *,
        retryable: bool = False,
        retry_after_seconds: int | float | Decimal | None = None,
        safe_message: str = "step failed",
    ) -> None:
        if not isinstance(code, str) or _ERROR_CODE_RE.fullmatch(code) is None:
            raise ValidationError("Job step error code is invalid")
        expected_exit_code = _ERROR_EXIT_CODES.get(code)
        if expected_exit_code is None:
            raise ValidationError("Job step error code is unsupported")
        if exit_code is None:
            exit_code = expected_exit_code
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != expected_exit_code:
            raise ValidationError("Job step error exit code does not match its classification")
        if not isinstance(retryable, bool):
            raise ValidationError("Job step error retryable must be boolean")
        if retryable and code not in RETRYABLE_ERROR_CODES:
            raise ValidationError("Only transient step errors may be retryable")
        if retry_after_seconds is None:
            normalized_retry_after = None
        else:
            if not retryable:
                raise ValidationError("Retry-after requires a retryable step error")
            normalized_retry_after = _decimal_seconds(
                retry_after_seconds,
                "retry_after_seconds",
                maximum=MAX_RETRY_DELAY_SECONDS,
            )
        message = _safe_message(safe_message)
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.retryable = retryable
        self.retry_after_seconds = normalized_retry_after
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Immutable bounded exponential retry policy used only before locks."""

    retry_class: str
    max_attempts: int = MAX_PREPARE_ATTEMPTS
    initial_delay_seconds: Decimal = Decimal("0.25")
    max_delay_seconds: Decimal = Decimal("4")
    max_elapsed_seconds: Decimal = Decimal("30")

    def __post_init__(self) -> None:
        if self.retry_class != "collector_declared":
            raise ValidationError("Unsupported Stage 7 retry class")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= MAX_PREPARE_ATTEMPTS
        ):
            raise ValidationError("Retry attempts must be bounded from one through three")
        initial_delay = _decimal_seconds(
            self.initial_delay_seconds,
            "initial_delay_seconds",
            maximum=MAX_RETRY_DELAY_SECONDS,
        )
        max_delay = _decimal_seconds(
            self.max_delay_seconds,
            "max_delay_seconds",
            maximum=MAX_RETRY_DELAY_SECONDS,
        )
        max_elapsed = _decimal_seconds(
            self.max_elapsed_seconds,
            "max_elapsed_seconds",
            maximum=Decimal("300"),
        )
        if initial_delay <= 0 or max_delay <= 0 or max_elapsed <= 0:
            raise ValidationError("Retry durations must be positive")
        if initial_delay > max_delay:
            raise ValidationError("Initial retry delay cannot exceed its bound")
        object.__setattr__(self, "initial_delay_seconds", initial_delay)
        object.__setattr__(self, "max_delay_seconds", max_delay)
        object.__setattr__(self, "max_elapsed_seconds", max_elapsed)

    def delay_after(
        self,
        error: JobStepError,
        *,
        completed_attempts: int,
        remaining_seconds: int | float | Decimal | None = None,
    ) -> Decimal | None:
        """Return the next pre-lock delay, or ``None`` when retry must stop.

        ``completed_attempts`` counts failed prepare calls already made.  An
        explicit Retry-After is honored exactly only when it fits the policy and
        remaining deadline; otherwise no retry starts.
        """

        if not isinstance(error, JobStepError):
            raise ValidationError("Retry policy requires JobStepError")
        if isinstance(completed_attempts, bool) or not isinstance(completed_attempts, int):
            raise ValidationError("completed_attempts must be an integer")
        if completed_attempts < 1:
            raise ValidationError("completed_attempts must be positive")
        if not error.retryable or completed_attempts >= self.max_attempts:
            return None
        if remaining_seconds is None:
            remaining = self.max_elapsed_seconds
        else:
            remaining = _decimal_seconds(
                remaining_seconds,
                "remaining_seconds",
                maximum=Decimal("300"),
            )
        exponential = min(
            self.initial_delay_seconds * (Decimal(2) ** (completed_attempts - 1)),
            self.max_delay_seconds,
        )
        delay = error.retry_after_seconds if error.retry_after_seconds is not None else exponential
        if delay > self.max_delay_seconds or delay > remaining:
            return None
        return delay


_COLLECTOR_DECLARED_POLICY = RetryPolicy("collector_declared")


def retry_policy_for(retry_class: str) -> RetryPolicy:
    """Return the frozen retry policy for a validated Stage 7 declaration."""

    if retry_class != "collector_declared":
        raise ValidationError("Unsupported Stage 7 retry class")
    return _COLLECTOR_DECLARED_POLICY


__all__ = [
    "MAX_PREPARE_ATTEMPTS",
    "MAX_RETRY_DELAY_SECONDS",
    "NON_RETRYABLE_ERROR_CODES",
    "RETRYABLE_ERROR_CODES",
    "JobStepError",
    "RetryPolicy",
    "retry_policy_for",
]
