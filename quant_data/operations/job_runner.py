"""Deterministic manual-only Stage 7 job rehearsal orchestration."""

from __future__ import annotations

import hashlib
import math
import signal
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Protocol

from ..errors import (
    ConflictError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import JobDeclaration, JobStepDeclaration, Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, acquire_write_session
from .job_receipts import (
    RECEIPT_SCHEMA_VERSION,
    JobRunReceipt,
    LockWaitReceipt,
    PrivateJobState,
    StepRunReceipt,
    validate_code_revision,
    validate_marker_period,
    validate_receipt_timestamp,
    validate_safe_message,
    validate_timezone_name,
)
from .job_retry import JobStepError, retry_policy_for


_EXIT_PRECEDENCE = (74, 78, 64, 124, 70, 75, 69)
_SHA256_HEX = frozenset("0123456789abcdef")
_PUBLICATION_OUTCOMES = frozenset({"succeeded", "unchanged", "partial"})


def _sha256_text(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _require_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _safe_text(value: object, field_name: str, *, maximum: int = 160) -> str:
    text = validate_safe_message(value, field_name)
    if len(text) > maximum:
        raise ValidationError(f"{field_name} exceeds the supported bound")
    return text


def _elapsed_decimal(value: float) -> Decimal:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError("Monotonic elapsed time must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValidationError("Monotonic elapsed time must be finite")
    return Decimal(str(round(max(0.0, normalized), 6)))


class _ActiveStepTimeout(TimeoutError):
    pass


def _validate_timeout_runtime() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise ValidationError("Stage 7 active timeouts require the main thread")
    required = ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
    if any(not hasattr(signal, name) for name in required):
        raise ValidationError("Stage 7 active timeouts require the Linux signal runtime")
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    if remaining > 0 or interval > 0:
        raise ValidationError("Stage 7 cannot run under an existing real-time alarm")


@contextmanager
def _active_timeout(seconds: float):
    if not math.isfinite(seconds) or seconds <= 0:
        raise _ActiveStepTimeout("active step deadline elapsed")
    _validate_timeout_runtime()
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(signum: int, frame: object) -> None:
        del signum, frame
        raise _ActiveStepTimeout("active step deadline elapsed")

    signal.signal(signal.SIGALRM, _raise_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _finished_timestamp(started_at: str, elapsed_seconds: float) -> str:
    parsed = datetime.fromisoformat(
        f"{started_at[:-1]}+00:00" if started_at.endswith("Z") else started_at
    )
    finished = parsed + timedelta(seconds=max(0.0, elapsed_seconds))
    value = finished.isoformat(timespec="seconds")
    return value.replace("+00:00", "Z")


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_run_id(job_id: str, plan_sha256: str, requested_at: str) -> str:
    return hashlib.sha256(
        uuid.uuid4().bytes
        + b"\x00"
        + job_id.encode("utf-8")
        + b"\x00"
        + plan_sha256.encode("ascii")
        + b"\x00"
        + requested_at.encode("ascii")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DryRunLock:
    alias: str
    role: str

    def to_primitive(self) -> dict[str, str]:
        return {"alias": self.alias, "role": self.role}


@dataclass(frozen=True, slots=True)
class DryRunStep:
    id: str
    version: str
    collector_id: str
    depends_on: tuple[str, ...]
    dependency_policy: str
    read_stores: tuple[str, ...]
    write_stores: tuple[str, ...]
    timeout_seconds: int
    retry_class: str
    if_new: bool
    identity_version: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "collector_id": self.collector_id,
            "depends_on": list(self.depends_on),
            "dependency_policy": self.dependency_policy,
            "read_stores": list(self.read_stores),
            "write_stores": list(self.write_stores),
            "timeout_seconds": self.timeout_seconds,
            "retry_class": self.retry_class,
            "if_new": self.if_new,
            "identity_version": self.identity_version,
        }


@dataclass(frozen=True, slots=True)
class DryRunPlan:
    contract: str
    contract_version: str
    job_id: str
    job_version: str
    calendar_mode: str
    scheduling_enabled: bool
    timeout_seconds: int
    logical_stores: tuple[str, ...]
    locks: tuple[DryRunLock, ...]
    steps: tuple[DryRunStep, ...]
    exit_code_policy_id: str
    exit_code_precedence: tuple[int, ...]

    def to_primitive(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "contract_version": self.contract_version,
            "job_id": self.job_id,
            "job_version": self.job_version,
            "calendar_mode": self.calendar_mode,
            "scheduling_enabled": self.scheduling_enabled,
            "timeout_seconds": self.timeout_seconds,
            "logical_stores": list(self.logical_stores),
            "locks": [item.to_primitive() for item in self.locks],
            "steps": [item.to_primitive() for item in self.steps],
            "exit_code_policy_id": self.exit_code_policy_id,
            "exit_code_precedence": list(self.exit_code_precedence),
            "side_effects": {
                "provider_calls": 0,
                "database_opens": 0,
                "lock_acquisitions": 0,
                "state_writes": 0,
                "scheduler_mutations": 0,
            },
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(
            dumps_strict(self.to_primitive()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedStep:
    semantic_identity: str
    payload: object

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_identity",
            _require_sha256(self.semantic_identity, "semantic_identity"),
        )
        if self.payload is None:
            raise ValidationError("Prepared step payload cannot be null")


@dataclass(frozen=True, slots=True)
class StepPublication:
    outcome: str
    committed_stores: tuple[str, ...] = ()
    ingestion_run_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in _PUBLICATION_OUTCOMES:
            raise ValidationError("Step publication outcome is unsupported")
        stores = tuple(StoreRole(store).value for store in self.committed_stores)
        if len(set(stores)) != len(stores):
            raise ValidationError("Step publication stores must be unique")
        run_ids = tuple(
            _require_sha256(value, "ingestion_run_ids")
            for value in self.ingestion_run_ids
        )
        if len(set(run_ids)) != len(run_ids):
            raise ValidationError("Step publication run identifiers must be unique")
        warnings = tuple(_safe_text(value, "warning") for value in self.warnings)
        if len(set(warnings)) != len(warnings):
            raise ValidationError("Step publication warnings must be unique")
        if self.outcome == "unchanged" and (stores or run_ids):
            raise ValidationError("Unchanged publication cannot report persistent writes")
        if self.outcome == "succeeded" and not stores:
            raise ValidationError("Succeeded publication must report a committed store")
        object.__setattr__(self, "committed_stores", stores)
        object.__setattr__(self, "ingestion_run_ids", run_ids)
        object.__setattr__(self, "warnings", warnings)


class OfflineStepExecutor(Protocol):
    def prepare(self, step: JobStepDeclaration, attempt: int) -> PreparedStep:
        """Fetch/parse/stage one bounded fixture candidate before locks."""

    def publish(
        self,
        step: JobStepDeclaration,
        candidate: PreparedStep,
        held_locks: HeldWriteLocks,
    ) -> StepPublication:
        """Revalidate and publish one candidate under the complete lock set."""


@dataclass(slots=True)
class _StepState:
    declaration: JobStepDeclaration
    prepared: PreparedStep | None = None
    attempts: int = 0
    elapsed_seconds: float = 0.0
    receipt: StepRunReceipt | None = None
    publication: StepPublication | None = None


def build_dry_run_plan(
    registry: Registry,
    job_id: str,
    store_map: StoreMap,
) -> DryRunPlan:
    """Build a sanitized deterministic plan without creating any state."""

    if not isinstance(registry, Registry):
        raise ValidationError("Dry run requires a validated registry")
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Dry run requires an explicit four-store map")
    job = registry.job(job_id)
    store_map.validate_distinct()
    identities_by_role = {
        identity.role.value: identity for identity in store_map.identities()
    }
    ordered = tuple(
        sorted(
            (identities_by_role[role] for role in job.required_stores),
            key=lambda identity: identity.canonical_uri,
        )
    )
    locks = tuple(
        DryRunLock(alias=f"store-{index}", role=identity.role.value)
        for index, identity in enumerate(ordered, start=1)
    )
    calendar_mode = str(job.calendar["mode"])
    scheduling_enabled = job.lifecycle["scheduling_enabled"]
    if (
        calendar_mode != "manual_fixture_only"
        or scheduling_enabled is not False
        or str(job.lifecycle["execution_mode"]) != "manual_fixture_only"
    ):
        raise ValidationError("Stage 7 dry run requires a disabled manual fixture job")
    precedence = tuple(int(value) for value in job.exit_code_policy["precedence"])
    if precedence != _EXIT_PRECEDENCE:
        raise ValidationError("Stage 7 exit-code precedence drifted")
    return DryRunPlan(
        contract="quant_data.stage7_dry_run_plan",
        contract_version="1.0.0",
        job_id=job.id,
        job_version=job.version,
        calendar_mode=calendar_mode,
        scheduling_enabled=False,
        timeout_seconds=job.timeout_seconds,
        logical_stores=job.required_stores,
        locks=locks,
        steps=tuple(
            DryRunStep(
                id=step.id,
                version=step.version,
                collector_id=step.collector_id,
                depends_on=step.depends_on,
                dependency_policy=step.dependency_policy,
                read_stores=step.read_stores,
                write_stores=step.write_stores,
                timeout_seconds=step.timeout_seconds,
                retry_class=step.retry_class,
                if_new=step.if_new,
                identity_version=step.identity_version,
            )
            for step in job.steps
        ),
        exit_code_policy_id=str(job.exit_code_policy["id"]),
        exit_code_precedence=precedence,
    )


class Stage7JobRunner:
    """Run one injected offline job rehearsal with complete-set locking."""

    def __init__(
        self,
        registry: Registry,
        store_map: StoreMap,
        private_state: PrivateJobState,
        executor: OfflineStepExecutor,
        *,
        now: Callable[[], str] = _default_now,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], None],
        run_id_source: Callable[[str, str, str], str] = _default_run_id,
        code_revision: str | None = None,
        timezone_name: str = "America/Toronto",
        lock_timeout_seconds: float = 5.0,
        active_timeout_cap_seconds: float | None = None,
    ) -> None:
        if not isinstance(registry, Registry):
            raise ValidationError("Runner requires a validated registry")
        if not isinstance(store_map, StoreMap):
            raise ValidationError("Runner requires an explicit store map")
        if not isinstance(private_state, PrivateJobState):
            raise ValidationError("Runner requires explicit private state")
        if not callable(now) or not callable(monotonic) or not callable(sleeper):
            raise ValidationError("Runner clock and sleeper must be injected callables")
        if not callable(run_id_source):
            raise ValidationError("Runner run-id source must be callable")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, (int, float))
            or not math.isfinite(float(lock_timeout_seconds))
            or lock_timeout_seconds < 0
        ):
            raise ValidationError("Runner lock timeout must be finite and nonnegative")
        if active_timeout_cap_seconds is not None and (
            isinstance(active_timeout_cap_seconds, bool)
            or not isinstance(active_timeout_cap_seconds, (int, float))
            or not math.isfinite(float(active_timeout_cap_seconds))
            or float(active_timeout_cap_seconds) <= 0
        ):
            raise ValidationError("Runner active timeout cap must be finite and positive")
        self.registry = registry
        self.store_map = store_map
        self.private_state = private_state
        self.executor = executor
        self._now = now
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._run_id_source = run_id_source
        self._code_revision = validate_code_revision(code_revision)
        self._timezone_name = validate_timezone_name(timezone_name)
        self._lock_timeout_seconds = float(lock_timeout_seconds)
        self._active_timeout_cap_seconds = (
            None
            if active_timeout_cap_seconds is None
            else float(active_timeout_cap_seconds)
        )

    def dry_run(self, job_id: str) -> DryRunPlan:
        return build_dry_run_plan(self.registry, job_id, self.store_map)

    def _clock(self) -> float:
        value = self._monotonic()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValidationError("Runner monotonic clock is invalid")
        return float(value)

    def _remaining_call_seconds(
        self,
        job: JobDeclaration,
        state: _StepState,
        *,
        job_started: float,
        current: float,
    ) -> float:
        remaining = min(
            state.declaration.timeout_seconds - state.elapsed_seconds,
            job.timeout_seconds - max(0.0, current - job_started),
        )
        if self._active_timeout_cap_seconds is not None:
            remaining = min(remaining, self._active_timeout_cap_seconds)
        return float(remaining)

    def _prepare(
        self,
        job: JobDeclaration,
        state: _StepState,
        *,
        job_started: float,
    ) -> None:
        policy = retry_policy_for(state.declaration.retry_class)
        step_started = self._clock()
        while True:
            state.attempts += 1
            attempt_started = self._clock()
            error: JobStepError | None = None
            try:
                remaining_call = self._remaining_call_seconds(
                    job,
                    state,
                    job_started=job_started,
                    current=attempt_started,
                )
                with _active_timeout(remaining_call):
                    candidate = self.executor.prepare(
                        state.declaration,
                        state.attempts,
                    )
                if not isinstance(candidate, PreparedStep):
                    raise JobStepError(
                        "schema",
                        safe_message="prepared step contract is invalid",
                    )
                state.prepared = candidate
            except _ActiveStepTimeout:
                error = JobStepError(
                    "timeout",
                    safe_message="step preparation timeout elapsed",
                )
            except JobStepError as exc:
                error = exc
            except OSError:
                error = JobStepError(
                    "io",
                    safe_message="step preparation local input failed",
                )
            except StoreUnavailableError:
                error = JobStepError(
                    "unavailable",
                    safe_message="required fixture source is unavailable",
                )
            except ValidationError:
                error = JobStepError(
                    "schema",
                    safe_message="prepared step validation failed",
                )
            except Exception:
                error = JobStepError(
                    "internal",
                    safe_message="step preparation failed",
                )
            attempt_finished = self._clock()
            state.elapsed_seconds += max(0.0, attempt_finished - attempt_started)
            total_step = attempt_finished - step_started
            total_job = attempt_finished - job_started
            if (
                total_step > state.declaration.timeout_seconds
                or total_job > job.timeout_seconds
            ):
                error = JobStepError(
                    "timeout",
                    safe_message="step preparation timeout elapsed",
                )
                state.prepared = None
            if error is None:
                return
            remaining = min(
                state.declaration.timeout_seconds - total_step,
                job.timeout_seconds - total_job,
            )
            delay = policy.delay_after(
                error,
                completed_attempts=state.attempts,
                remaining_seconds=max(0.0, remaining),
            )
            if delay is None:
                outcome = "timed_out" if error.exit_code == 124 else "failed"
                state.receipt = StepRunReceipt(
                    step_id=state.declaration.id,
                    outcome=outcome,
                    attempts=state.attempts,
                    elapsed_seconds=_elapsed_decimal(state.elapsed_seconds),
                    timeout=outcome == "timed_out",
                    exit_code=error.exit_code,
                    ingestion_run_ids=(),
                    error_codes=(error.code,),
                    error_messages=(error.safe_message,),
                )
                state.prepared = None
                return
            sleep_started = self._clock()
            self._sleeper(float(delay))
            sleep_finished = self._clock()
            slept = max(float(delay), max(0.0, sleep_finished - sleep_started))
            state.elapsed_seconds += slept

    @staticmethod
    def _dependencies_succeeded(
        state: _StepState,
        states_by_id: dict[str, _StepState],
    ) -> bool:
        return all(
            states_by_id[dependency].prepared is not None
            and states_by_id[dependency].receipt is None
            for dependency in state.declaration.depends_on
        )

    @staticmethod
    def _dependency_exit(
        state: _StepState,
        states_by_id: dict[str, _StepState],
    ) -> int:
        for dependency in state.declaration.depends_on:
            receipt = states_by_id[dependency].receipt
            if receipt is not None and receipt.exit_code:
                return receipt.exit_code
        return 70

    def _prepare_all(
        self,
        job: JobDeclaration,
        *,
        job_started: float,
    ) -> tuple[_StepState, ...]:
        states = tuple(_StepState(step) for step in job.steps)
        states_by_id = {state.declaration.id: state for state in states}
        for state in states:
            if (
                state.declaration.depends_on
                and not self._dependencies_succeeded(state, states_by_id)
            ):
                exit_code = self._dependency_exit(state, states_by_id)
                state.receipt = StepRunReceipt(
                    step_id=state.declaration.id,
                    outcome="blocked",
                    attempts=0,
                    elapsed_seconds=Decimal("0"),
                    timeout=False,
                    exit_code=exit_code,
                    ingestion_run_ids=(),
                    error_codes=("dependency_blocked",),
                    error_messages=("required predecessor failed",),
                )
                continue
            self._prepare(job, state, job_started=job_started)
        return states

    @staticmethod
    def _publication_error(exc: BaseException) -> JobStepError:
        if isinstance(exc, JobStepError):
            return exc
        if isinstance(exc, OSError):
            return JobStepError(
                "io",
                safe_message="step publication local input failed",
            )
        if isinstance(exc, StoreUnavailableError):
            return JobStepError(
                "unavailable",
                safe_message="required store is unavailable",
            )
        if isinstance(exc, ConflictError):
            return JobStepError(
                "lock_conflict",
                safe_message="publication lock conflict",
            )
        if isinstance(exc, ValidationError):
            return JobStepError(
                "schema",
                safe_message="step publication validation failed",
            )
        return JobStepError(
            "internal",
            safe_message="step publication failed",
        )

    @staticmethod
    def _locked_aliases(
        held_locks: HeldWriteLocks,
    ) -> tuple[dict[str, str], tuple[LockWaitReceipt, ...]]:
        aliases: dict[str, str] = {}
        receipts: list[LockWaitReceipt] = []
        for index, (identity, wait_seconds) in enumerate(
            zip(
                held_locks.identities,
                held_locks.acquisition_wait_seconds,
                strict=True,
            ),
            start=1,
        ):
            alias = f"store-{index}"
            aliases[identity.role.value] = alias
            receipts.append(
                LockWaitReceipt(
                    alias=alias,
                    key_prefix=identity.lock_key[:12],
                    wait_seconds=_elapsed_decimal(wait_seconds),
                    outcome="acquired",
                )
            )
        return aliases, tuple(receipts)

    def _publish_all(
        self,
        job: JobDeclaration,
        states: tuple[_StepState, ...],
        *,
        job_started: float,
    ) -> tuple[tuple[LockWaitReceipt, ...], dict[str, str]]:
        states_by_id = {state.declaration.id: state for state in states}
        publishable = [state for state in states if state.prepared is not None]
        if not publishable:
            return (), {}

        aliases: dict[str, str] = {}
        lock_order: tuple[LockWaitReceipt, ...] = ()
        try:
            with acquire_write_session(
                self.store_map,
                job.required_stores,
                timeout_seconds=self._lock_timeout_seconds,
            ) as held_locks:
                if not held_locks.active or set(held_locks.roles) != {
                    StoreRole(role) for role in job.required_stores
                }:
                    raise ConflictError("Complete Stage 7 write-lock set is unavailable")
                aliases, lock_order = self._locked_aliases(held_locks)
                for state in states:
                    if state.prepared is None or state.receipt is not None:
                        continue
                    if state.declaration.depends_on and any(
                        states_by_id[dependency].receipt is not None
                        and states_by_id[dependency].receipt.outcome
                        not in {"succeeded", "unchanged"}
                        for dependency in state.declaration.depends_on
                    ):
                        exit_code = self._dependency_exit(state, states_by_id)
                        state.receipt = StepRunReceipt(
                            step_id=state.declaration.id,
                            outcome="failed",
                            attempts=state.attempts,
                            elapsed_seconds=_elapsed_decimal(state.elapsed_seconds),
                            timeout=False,
                            exit_code=exit_code,
                            ingestion_run_ids=(),
                            error_codes=("dependency_blocked",),
                            error_messages=("required predecessor failed",),
                        )
                        continue
                    if not all(
                        held_locks.holds(role)
                        for role in state.declaration.write_stores
                    ):
                        raise ConflictError("Prepared step is outside the held lock set")
                    publish_started = self._clock()
                    try:
                        remaining_call = self._remaining_call_seconds(
                            job,
                            state,
                            job_started=job_started,
                            current=publish_started,
                        )
                        with _active_timeout(remaining_call):
                            publication = self.executor.publish(
                                state.declaration,
                                state.prepared,
                                held_locks,
                            )
                        if not isinstance(publication, StepPublication):
                            raise ValidationError("Executor returned an invalid publication")
                        if not set(publication.committed_stores).issubset(
                            set(state.declaration.write_stores)
                        ):
                            raise ValidationError(
                                "Executor reported an undeclared committed store"
                            )
                        if not all(
                            held_locks.holds(role)
                            for role in publication.committed_stores
                        ):
                            raise ConflictError(
                                "Executor committed outside the held lock set"
                            )
                        state.publication = publication
                        publish_finished = self._clock()
                        state.elapsed_seconds += max(
                            0.0, publish_finished - publish_started
                        )
                        outcome = publication.outcome
                        if (
                            publish_finished - job_started > job.timeout_seconds
                            or state.elapsed_seconds
                            > state.declaration.timeout_seconds
                        ):
                            state.receipt = StepRunReceipt(
                                step_id=state.declaration.id,
                                outcome="timed_out",
                                attempts=state.attempts,
                                elapsed_seconds=_elapsed_decimal(
                                    state.elapsed_seconds
                                ),
                                timeout=True,
                                exit_code=124,
                                ingestion_run_ids=publication.ingestion_run_ids,
                                error_codes=("timeout",),
                                error_messages=("step publication timeout elapsed",),
                            )
                        elif outcome == "partial":
                            state.receipt = StepRunReceipt(
                                step_id=state.declaration.id,
                                outcome="partial",
                                attempts=state.attempts,
                                elapsed_seconds=_elapsed_decimal(
                                    state.elapsed_seconds
                                ),
                                timeout=False,
                                exit_code=70,
                                ingestion_run_ids=publication.ingestion_run_ids,
                                error_codes=("partial_publication",),
                                error_messages=("step publication incomplete",),
                            )
                        else:
                            state.receipt = StepRunReceipt(
                                step_id=state.declaration.id,
                                outcome=outcome,
                                attempts=state.attempts,
                                elapsed_seconds=_elapsed_decimal(
                                    state.elapsed_seconds
                                ),
                                timeout=False,
                                exit_code=0,
                                ingestion_run_ids=publication.ingestion_run_ids,
                                error_codes=(),
                                error_messages=(),
                            )
                    except Exception as exc:
                        try:
                            publication_finished = self._clock()
                        except ValidationError:
                            publication_finished = publish_started
                        state.elapsed_seconds += max(
                            0.0, publication_finished - publish_started
                        )
                        error = (
                            JobStepError(
                                "timeout",
                                safe_message="step publication timeout elapsed",
                            )
                            if isinstance(exc, _ActiveStepTimeout)
                            else self._publication_error(exc)
                        )
                        state.receipt = StepRunReceipt(
                            step_id=state.declaration.id,
                            outcome="timed_out" if error.exit_code == 124 else "failed",
                            attempts=max(1, state.attempts),
                            elapsed_seconds=_elapsed_decimal(
                                state.elapsed_seconds
                            ),
                            timeout=error.exit_code == 124,
                            exit_code=error.exit_code,
                            ingestion_run_ids=(),
                            error_codes=(error.code,),
                            error_messages=(error.safe_message,),
                        )
                return lock_order, aliases
        except (ConflictError, OSError, RuntimeError) as exc:
            if isinstance(exc, ConflictError):
                error = JobStepError(
                    "lock_conflict",
                    safe_message="complete lock set unavailable",
                )
            elif isinstance(exc, OSError):
                error = JobStepError(
                    "io",
                    safe_message="write lock local input failed",
                )
            else:
                error = JobStepError(
                    "internal",
                    safe_message="write lock lifecycle failed",
                )
            for state in publishable:
                if state.receipt is None or state.receipt.exit_code == 0:
                    publication = state.publication
                    run_ids = (
                        publication.ingestion_run_ids
                        if publication is not None
                        else ()
                    )
                    committed = bool(
                        publication is not None and publication.committed_stores
                    )
                    state.receipt = StepRunReceipt(
                        step_id=state.declaration.id,
                        outcome="partial" if committed else "failed",
                        attempts=max(1, state.attempts),
                        elapsed_seconds=_elapsed_decimal(state.elapsed_seconds),
                        timeout=False,
                        exit_code=error.exit_code,
                        ingestion_run_ids=run_ids,
                        error_codes=(error.code,),
                        error_messages=(error.safe_message,),
                    )
            return lock_order, aliases

    @staticmethod
    def _aggregate_exit(receipts: tuple[StepRunReceipt, ...]) -> int:
        failures = {receipt.exit_code for receipt in receipts if receipt.exit_code}
        for exit_code in _EXIT_PRECEDENCE:
            if exit_code in failures:
                return exit_code
        return 0

    @staticmethod
    def _unique(values: list[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    def _build_receipt(
        self,
        *,
        job: JobDeclaration,
        plan: DryRunPlan,
        run_id: str,
        requested_at: str,
        started_at: str,
        finished_at: str,
        calendar_decision: str,
        skip_reason: str | None,
        states: tuple[_StepState, ...],
        lock_order: tuple[LockWaitReceipt, ...],
        aliases: dict[str, str],
    ) -> JobRunReceipt:
        receipts = tuple(
            state.receipt
            for state in states
            if state.receipt is not None
        )
        if len(receipts) != len(states):
            raise ValidationError("Stage 7 step receipts are incomplete")
        aggregate_exit_code = self._aggregate_exit(receipts)
        committed_roles: list[str] = []
        ingestion_run_ids: list[str] = []
        warnings: list[str] = []
        for state in states:
            if state.publication is None:
                continue
            committed_roles.extend(state.publication.committed_stores)
            ingestion_run_ids.extend(state.publication.ingestion_run_ids)
            warnings.extend(state.publication.warnings)
        committed_roles_tuple = self._unique(committed_roles)
        if aggregate_exit_code:
            semantic_outcome = "partial" if committed_roles_tuple else "failed"
        elif calendar_decision == "skipped":
            semantic_outcome = "skipped"
        elif committed_roles_tuple:
            semantic_outcome = "changed"
        else:
            semantic_outcome = "unchanged"
        job_errors: dict[str, str] = {}
        for receipt in receipts:
            for code, message in zip(
                receipt.error_codes,
                receipt.error_messages,
                strict=True,
            ):
                job_errors.setdefault(code, message)
        committed_aliases = tuple(
            aliases[role]
            for role in aliases
            if role in committed_roles_tuple
        )
        return JobRunReceipt(
            schema_version=RECEIPT_SCHEMA_VERSION,
            run_id=run_id,
            job_id=job.id,
            job_version=job.version,
            plan_sha256=plan.sha256,
            trigger_kind="manual",
            requested_at=requested_at,
            started_at=started_at,
            finished_at=finished_at,
            timezone=self._timezone_name,
            calendar_decision=calendar_decision,
            skip_reason=skip_reason,
            logical_stores=job.required_stores,
            lock_order=lock_order,
            steps=receipts,
            semantic_outcome=semantic_outcome,
            committed_store_aliases=committed_aliases,
            ingestion_run_ids=self._unique(ingestion_run_ids),
            aggregate_exit_code=aggregate_exit_code,
            code_revision=self._code_revision,
            warnings=self._unique(warnings),
            error_codes=tuple(job_errors),
            error_messages=tuple(job_errors.values()),
        )

    def _publish_private_evidence(
        self,
        receipt: JobRunReceipt,
        *,
        marker_period: str | None,
    ) -> None:
        events = (
            {
                "event": "job_finished",
                "fields": {
                    "exit_code": receipt.aggregate_exit_code,
                    "outcome": receipt.semantic_outcome,
                    "step_count": len(receipt.steps),
                },
            },
        )
        try:
            self.private_state.publish_job_evidence(
                receipt,
                events,
                marker_period,
            )
        except (OSError, ConflictError, ResourceLimitError, ValidationError) as exc:
            raise JobStepError(
                "io",
                safe_message="private state publication failed",
            ) from exc

    def run(
        self,
        job_id: str,
        *,
        calendar_decision: str = "eligible",
        trigger_kind: str = "manual",
        skip_reason: str | None = None,
        marker_period: str | None = None,
    ) -> JobRunReceipt:
        """Execute one manual fixture rehearsal and publish private evidence."""

        if trigger_kind != "manual":
            raise ValidationError("Stage 7 authorizes only manual trigger_kind")
        if calendar_decision not in {"run", "eligible", "skipped"}:
            raise ValidationError("Stage 7 calendar decision is unsupported")
        if calendar_decision == "skipped":
            skip_reason = _safe_text(
                skip_reason or "calendar not eligible",
                "skip_reason",
            )
        elif skip_reason is not None:
            raise ValidationError("skip_reason is only valid for a skipped job")

        _validate_timeout_runtime()
        if self.private_state.dry_run:
            raise ValidationError("Operational execution requires non-dry private state")
        plan = self.dry_run(job_id)
        job = self.registry.job(job_id)
        requested_at, requested_time = validate_receipt_timestamp(
            self._now(),
            "requested_at",
        )
        started_at, started_time = validate_receipt_timestamp(
            self._now(),
            "started_at",
        )
        if requested_time > started_time:
            raise ValidationError("Runner timestamps must be chronological")
        run_id = _require_sha256(
            self._run_id_source(job.id, plan.sha256, requested_at),
            "run_id",
        )
        effective_marker_period: str | None = None
        if job.id == "macro-monthly" and calendar_decision != "skipped":
            effective_marker_period = validate_marker_period(
                marker_period or started_at[:7]
            )
        elif marker_period is not None:
            raise ValidationError(
                "marker_period is only valid for a running macro-monthly job"
            )
        self.private_state.preflight_job_evidence(
            run_id,
            job.id,
            effective_marker_period,
        )
        job_started = self._clock()
        if calendar_decision == "skipped":
            states = tuple(_StepState(step) for step in job.steps)
            for state in states:
                state.receipt = StepRunReceipt(
                    step_id=state.declaration.id,
                    outcome="skipped",
                    attempts=0,
                    elapsed_seconds=Decimal("0"),
                    timeout=False,
                    exit_code=0,
                    ingestion_run_ids=(),
                    error_codes=(),
                    error_messages=(),
                )
            lock_order: tuple[LockWaitReceipt, ...] = ()
            aliases: dict[str, str] = {}
        else:
            states = self._prepare_all(job, job_started=job_started)
            lock_order, aliases = self._publish_all(
                job,
                states,
                job_started=job_started,
            )
        try:
            job_finished = self._clock()
        except ValidationError:
            job_finished = job_started + max(
                (state.elapsed_seconds for state in states),
                default=0.0,
            )
        finished_at = _finished_timestamp(
            started_at,
            max(0.0, job_finished - job_started),
        )
        receipt = self._build_receipt(
            job=job,
            plan=plan,
            run_id=run_id,
            requested_at=requested_at,
            started_at=started_at,
            finished_at=finished_at,
            calendar_decision=calendar_decision,
            skip_reason=skip_reason,
            states=states,
            lock_order=lock_order,
            aliases=aliases,
        )
        marker_for_receipt = (
            effective_marker_period
            if (
                receipt.job_id == "macro-monthly"
                and receipt.aggregate_exit_code == 0
                and receipt.semantic_outcome in {"changed", "unchanged", "succeeded"}
            )
            else None
        )
        self._publish_private_evidence(
            receipt,
            marker_period=marker_for_receipt,
        )
        return receipt


__all__ = [
    "DryRunLock",
    "DryRunPlan",
    "DryRunStep",
    "OfflineStepExecutor",
    "PreparedStep",
    "Stage7JobRunner",
    "StepPublication",
    "build_dry_run_plan",
]
