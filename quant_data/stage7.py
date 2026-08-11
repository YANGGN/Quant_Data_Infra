"""Deterministic offline acceptance harness for Stage 7 operations.

The executable matrix below is intentionally an orchestration rehearsal.  Its
executors are injected mocks: it proves the runner's manual-only planning,
receipt, retry, lock, and backup boundaries without claiming provider parity
or making a live provider call.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from unittest import mock

from .errors import ConflictError, ValidationError
from .fingerprint import logical_manifest, mutation_fingerprint
from .fixtures import FixtureManifest
from .json_codec import dumps_strict, loads_strict
from .migrations import initialize_all
from .operations.backup import backup_all, restore_all
from .operations.fixture_executor import Stage7FixtureStepExecutor
from .operations.job_receipts import PrivateJobState
from .operations.job_retry import JobStepError
from .operations.job_runner import (
    PreparedStep,
    Stage7JobRunner,
    StepPublication,
    build_dry_run_plan,
)
from .registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage7_registry_profile,
)
from .stage1 import explicit_store_map
from .stage6 import run_clean_stage6_rebuild
from .stores import StoreMap, StoreRole, StoreWriteLock


_STAGE7_JOB_IDS = (
    "news-hourly",
    "sec-daily",
    "options-close",
    "macro-daily",
    "market-close",
    "expectations",
    "company-weekly",
    "macro-monthly",
)
_FORBIDDEN_PUBLIC_FIELDS = (
    "canonical_uri",
    "lock_key",
    "key_prefix",
    "physical_path",
    "state_root",
)
_FIXTURE_MANIFEST_PATH = Path("tests/fixtures/manifest.json")


def _sha256_text(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _sha256_primitive(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ConflictError("Stage 7 work root is too broad")
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 7 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 7 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _assert_identical(left: object, right: object, message: str) -> None:
    if dumps_strict(left) != dumps_strict(right):
        raise ValidationError(message)


def _assert_path_free(value: object, *, roots: tuple[Path, ...]) -> None:
    rendered = dumps_strict(value)
    if any(field in rendered for field in _FORBIDDEN_PUBLIC_FIELDS):
        raise ValidationError("Stage 7 public evidence exposed a physical lock or path field")
    if any(str(root) in rendered for root in roots):
        raise ValidationError("Stage 7 public evidence exposed an explicit work path")


@dataclass(slots=True)
class _OfflineGuard:
    """Fail closed if the deterministic harness reaches live-capable entry points."""

    blocked_calls: list[str] = field(default_factory=list)

    def blocked(self, name: str):  # type: ignore[no-untyped-def]
        def _raise(*args: object, **kwargs: object) -> None:
            del args, kwargs
            self.blocked_calls.append(name)
            raise AssertionError(f"Stage 7 offline guard blocked {name}")

        return _raise


@contextmanager
def _offline_execution_guard() -> Iterator[_OfflineGuard]:
    """Block network, scheduler, and process launch entry points for this run."""

    guard = _OfflineGuard()
    with (
        mock.patch.object(socket, "create_connection", guard.blocked("socket.create_connection")),
        mock.patch.object(socket.socket, "connect", guard.blocked("socket.socket.connect")),
        mock.patch.object(subprocess, "Popen", guard.blocked("subprocess.Popen")),
        mock.patch.object(subprocess, "run", guard.blocked("subprocess.run")),
        mock.patch.object(subprocess, "call", guard.blocked("subprocess.call")),
        mock.patch.object(subprocess, "check_call", guard.blocked("subprocess.check_call")),
        mock.patch.object(
            subprocess,
            "check_output",
            guard.blocked("subprocess.check_output"),
        ),
        mock.patch.object(os, "system", guard.blocked("os.system")),
        mock.patch.object(
            asyncio,
            "create_subprocess_exec",
            guard.blocked("asyncio.create_subprocess_exec"),
        ),
        mock.patch.object(
            asyncio,
            "create_subprocess_shell",
            guard.blocked("asyncio.create_subprocess_shell"),
        ),
    ):
        yield guard


@dataclass(slots=True)
class _FixedClock:
    """A deterministic wall/monotonic clock injected into every mock run."""

    seconds: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> str:
        value = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    def advance(self, seconds: float) -> None:
        self.seconds += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@dataclass(slots=True)
class _DeterministicRunIds:
    label: str
    index: int = 0

    def __call__(self, job_id: str, plan_sha256: str, requested_at: str) -> str:
        self.index += 1
        return _sha256_text(
            "stage7-mocked-run",
            self.label,
            str(self.index),
            job_id,
            plan_sha256,
            requested_at,
        )


@dataclass(slots=True)
class _MockedExecutor:
    """A no-I/O executor used only to exercise real Stage7JobRunner control flow."""

    label: str
    clock: _FixedClock
    prepare_actions: dict[str, list[object]] = field(default_factory=dict)
    prepare_advances: dict[str, list[float]] = field(default_factory=dict)
    publish_actions: dict[str, object] = field(default_factory=dict)
    events: list[tuple[str, str]] = field(default_factory=list)
    prepare_while_locked: bool = False
    sleep_while_locked: bool = False
    _publishing: bool = False

    def prepare(self, step: object, attempt: int) -> PreparedStep:
        step_id = str(getattr(step, "id"))
        if self._publishing:
            self.prepare_while_locked = True
            raise AssertionError("mocked preparation occurred while locks were active")
        self.events.append(("prepare", step_id))
        advances = self.prepare_advances.get(step_id)
        if advances:
            self.clock.advance(advances.pop(0))
        actions = self.prepare_actions.get(step_id)
        if actions:
            action = actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            if isinstance(action, PreparedStep):
                return action
            raise AssertionError("mocked prepare action was invalid")
        return PreparedStep(
            semantic_identity=_sha256_text("stage7-mocked-candidate", self.label, step_id, str(attempt)),
            payload=(self.label, step_id, attempt),
        )

    def publish(
        self,
        step: object,
        candidate: PreparedStep,
        held_locks: object,
    ) -> StepPublication:
        del candidate
        step_id = str(getattr(step, "id"))
        if not bool(getattr(held_locks, "active", False)):
            raise AssertionError("mocked publication did not receive active write locks")
        self.events.append(("publish", step_id))
        self._publishing = True
        try:
            action = self.publish_actions.get(step_id)
            if isinstance(action, BaseException):
                raise action
            if isinstance(action, StepPublication):
                return action
            return StepPublication("unchanged")
        finally:
            self._publishing = False

    def sleep(self, seconds: float) -> None:
        if self._publishing:
            self.sleep_while_locked = True
            raise AssertionError("mocked retry sleep occurred while locks were active")
        self.clock.sleep(seconds)


@dataclass(slots=True)
class _RecordingFixtureExecutor:
    """Observe real fixture publication without changing its control flow."""

    executor: Stage7FixtureStepExecutor
    events: list[tuple[str, str]] = field(default_factory=list)
    lock_roles: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)

    def prepare(self, step: object, attempt: int) -> PreparedStep:
        step_id = str(getattr(step, "id"))
        self.events.append(("prepare", step_id))
        return self.executor.prepare(step, attempt)

    def publish(
        self,
        step: object,
        candidate: PreparedStep,
        held_locks: object,
    ) -> StepPublication:
        step_id = str(getattr(step, "id"))
        if not bool(getattr(held_locks, "active", False)):
            raise AssertionError("real fixture publication did not receive active locks")
        roles = tuple(sorted(role.value for role in getattr(held_locks, "roles")))
        self.events.append(("publish", step_id))
        self.lock_roles.setdefault(step_id, []).append(roles)
        return self.executor.publish(step, candidate, held_locks)


def _job_catalog(registry: Registry) -> list[dict[str, object]]:
    return [
        {
            "id": job.id,
            "version": job.version,
            "calendar_mode": job.calendar["mode"],
            "scheduling_enabled": job.lifecycle["scheduling_enabled"],
            "logical_stores": list(job.required_stores),
            "success_marker": job.success_marker,
            "steps": [
                {
                    "id": step.id,
                    "version": step.version,
                    "depends_on": list(step.depends_on),
                    "dependency_policy": step.dependency_policy,
                    "read_stores": list(step.read_stores),
                    "write_stores": list(step.write_stores),
                    "timeout_seconds": step.timeout_seconds,
                    "retry_class": step.retry_class,
                    "if_new": step.if_new,
                    "identity_version": step.identity_version,
                }
                for step in job.steps
            ],
        }
        for job in registry.jobs
    ]


def _dry_run_catalog(registry: Registry, store_map: StoreMap) -> tuple[dict[str, str], dict[str, object]]:
    digests: dict[str, str] = {}
    plans: dict[str, object] = {}
    for job_id in _STAGE7_JOB_IDS:
        plan = build_dry_run_plan(registry, job_id, store_map)
        primitive = plan.to_primitive()
        if primitive["side_effects"] != {
            "provider_calls": 0,
            "database_opens": 0,
            "lock_acquisitions": 0,
            "state_writes": 0,
            "scheduler_mutations": 0,
        }:
            raise ValidationError("Stage 7 dry run reported an operational side effect")
        if plan.scheduling_enabled or plan.calendar_mode != "manual_fixture_only":
            raise ValidationError("Stage 7 dry run enabled scheduling")
        digests[job_id] = plan.sha256
        plans[job_id] = primitive
    return digests, {"plans": plans, "plan_sha256": digests}


def _normalize_receipt(receipt: object) -> dict[str, object]:
    """Retain operational outcomes while omitting lock key material and timing."""

    lock_order = getattr(receipt, "lock_order")
    steps = getattr(receipt, "steps")
    return {
        "job_id": getattr(receipt, "job_id"),
        "job_version": getattr(receipt, "job_version"),
        "calendar_decision": getattr(receipt, "calendar_decision"),
        "skip_reason": getattr(receipt, "skip_reason"),
        "logical_stores": list(getattr(receipt, "logical_stores")),
        "lock_order": [
            {"alias": item.alias, "outcome": item.outcome}
            for item in lock_order
        ],
        "steps": [
            {
                "step_id": item.step_id,
                "outcome": item.outcome,
                "attempts": item.attempts,
                "timeout": item.timeout,
                "exit_code": item.exit_code,
                "error_codes": list(item.error_codes),
                "error_messages": list(item.error_messages),
            }
            for item in steps
        ],
        "semantic_outcome": getattr(receipt, "semantic_outcome"),
        "committed_store_aliases": list(getattr(receipt, "committed_store_aliases")),
        "aggregate_exit_code": getattr(receipt, "aggregate_exit_code"),
        "warnings": list(getattr(receipt, "warnings")),
        "error_codes": list(getattr(receipt, "error_codes")),
        "error_messages": list(getattr(receipt, "error_messages")),
    }


def _assert_executor_protocol(executor: _MockedExecutor) -> None:
    if executor.prepare_while_locked or executor.sleep_while_locked:
        raise ValidationError("Stage 7 mock executor crossed the pre-lock boundary")
    first_publish = next(
        (index for index, event in enumerate(executor.events) if event[0] == "publish"),
        None,
    )
    if first_publish is not None and any(
        event[0] == "prepare" for event in executor.events[first_publish + 1 :]
    ):
        raise ValidationError("Stage 7 publication began before all preparation completed")


def _assert_private_evidence(
    state_root: Path,
    receipt: object,
    *,
    expect_marker: bool,
    roots: tuple[Path, ...],
) -> None:
    receipt_paths = sorted((state_root / "receipts").glob("*.json"))
    log_paths = sorted((state_root / "logs").glob("*.json"))
    marker_paths = sorted((state_root / "markers").glob("*.json"))
    if len(receipt_paths) != 1 or len(log_paths) != 1:
        raise ValidationError("Every non-dry Stage 7 result requires one receipt and log")
    if len(marker_paths) != int(expect_marker):
        raise ValidationError("Stage 7 monthly success marker outcome was incorrect")
    stored_receipt = loads_strict(receipt_paths[0].read_bytes())
    if dumps_strict(stored_receipt) != dumps_strict(getattr(receipt, "to_primitive")()):
        raise ValidationError("Stage 7 private receipt did not match the returned receipt")
    for evidence_path in (*receipt_paths, *log_paths, *marker_paths):
        rendered = evidence_path.read_text(encoding="utf-8")
        if any(str(root) in rendered for root in roots):
            raise ValidationError("Stage 7 private evidence exposed an explicit path")


def _run_mocked_case(
    *,
    label: str,
    job_id: str,
    registry: Registry,
    store_map: StoreMap,
    state_root: Path,
    prepare_actions: Mapping[str, list[object]] | None = None,
    prepare_advances: Mapping[str, list[float]] | None = None,
    publish_actions: Mapping[str, object] | None = None,
    calendar_decision: str = "eligible",
    skip_reason: str | None = None,
    marker_period: str | None = None,
    held_lock_role: StoreRole | None = None,
    roots: tuple[Path, ...],
) -> tuple[dict[str, object], _MockedExecutor]:
    """Execute one deterministic real-runner rehearsal with a mocked executor."""

    clock = _FixedClock()
    executor = _MockedExecutor(
        label=label,
        clock=clock,
        prepare_actions={key: list(value) for key, value in (prepare_actions or {}).items()},
        prepare_advances={key: list(value) for key, value in (prepare_advances or {}).items()},
        publish_actions=dict(publish_actions or {}),
    )
    runner = Stage7JobRunner(
        registry,
        store_map,
        PrivateJobState(state_root),
        executor,
        now=clock.now,
        monotonic=clock.monotonic,
        sleeper=executor.sleep,
        run_id_source=_DeterministicRunIds(label),
        code_revision="7" * 40,
        timezone_name="America/Toronto",
        lock_timeout_seconds=0.0,
    )
    if held_lock_role is None:
        receipt = runner.run(
            job_id,
            calendar_decision=calendar_decision,
            skip_reason=skip_reason,
            marker_period=marker_period,
        )
    else:
        with StoreWriteLock(store_map.path(held_lock_role), timeout_seconds=0.0):
            receipt = runner.run(
                job_id,
                calendar_decision=calendar_decision,
                skip_reason=skip_reason,
                marker_period=marker_period,
            )
    _assert_executor_protocol(executor)
    _assert_private_evidence(
        state_root,
        receipt,
        expect_marker=marker_period is not None and receipt.aggregate_exit_code == 0,
        roots=roots,
    )
    return {
        "receipt": _normalize_receipt(receipt),
        "mocked_prepare_calls": sum(
            1 for event, _ in executor.events if event == "prepare"
        ),
        "mocked_publish_calls": sum(
            1 for event, _ in executor.events if event == "publish"
        ),
        "retry_sleeps": len(clock.sleeps),
    }, executor


def _require_case(
    matrix: Mapping[str, Mapping[str, object]],
    label: str,
    *,
    outcome: str,
    exit_code: int,
) -> None:
    receipt = matrix[label]["receipt"]
    if not isinstance(receipt, Mapping):
        raise ValidationError("Stage 7 outcome matrix receipt is malformed")
    if receipt["semantic_outcome"] != outcome or receipt["aggregate_exit_code"] != exit_code:
        raise ValidationError(f"Stage 7 mocked {label} case did not produce its reviewed outcome")


def _mocked_outcome_matrix(
    *,
    registry: Registry,
    store_map: StoreMap,
    state_root: Path,
    roots: tuple[Path, ...],
) -> dict[str, object]:
    """Exercise every required mocked orchestration outcome through the runner."""

    matrix: dict[str, dict[str, object]] = {}

    changed_step = registry.job("news-hourly").steps[0]
    matrix["changed"], _ = _run_mocked_case(
        label="changed",
        job_id="news-hourly",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "changed",
        publish_actions={
            changed_step.id: StepPublication(
                "succeeded",
                committed_stores=changed_step.write_stores,
                ingestion_run_ids=(_sha256_text("stage7-mocked-ingestion", "changed"),),
            )
        },
        roots=roots,
    )

    matrix["unchanged"], _ = _run_mocked_case(
        label="unchanged",
        job_id="market-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "unchanged",
        roots=roots,
    )

    matrix["calendar_skipped"], skipped_executor = _run_mocked_case(
        label="calendar-skipped",
        job_id="news-hourly",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "calendar-skipped",
        calendar_decision="skipped",
        skip_reason="fixture calendar closed",
        roots=roots,
    )
    if skipped_executor.events:
        raise ValidationError("Calendar-skipped Stage 7 job invoked the mocked executor")

    partial_job = registry.job("macro-monthly")
    matrix["partial_commit"], _ = _run_mocked_case(
        label="partial-commit",
        job_id="macro-monthly",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "partial-commit",
        publish_actions={
            partial_job.steps[0].id: StepPublication(
                "succeeded",
                committed_stores=partial_job.steps[0].write_stores,
                ingestion_run_ids=(_sha256_text("stage7-mocked-ingestion", "partial"),),
            ),
            partial_job.steps[1].id: JobStepError(
                "internal",
                safe_message="mocked publication failed",
            ),
        },
        marker_period="2026-09",
        roots=roots,
    )

    retry_step = registry.job("market-close").steps[0]
    retry_error = lambda: JobStepError(
        "temporary",
        retryable=True,
        safe_message="mocked temporary failure",
    )
    matrix["retry_exhausted"], retry_executor = _run_mocked_case(
        label="retry-exhausted",
        job_id="market-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "retry-exhausted",
        prepare_actions={retry_step.id: [retry_error(), retry_error(), retry_error()]},
        roots=roots,
    )
    if retry_executor.sleep_while_locked:
        raise ValidationError("Stage 7 retry slept while a lock was active")

    matrix["lock_temporary"], lock_executor = _run_mocked_case(
        label="lock-temporary",
        job_id="market-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "lock-temporary",
        held_lock_role=StoreRole.MARKET,
        roots=roots,
    )
    if any(event == "publish" for event, _ in lock_executor.events):
        raise ValidationError("Stage 7 lock temporary case published without the complete lock set")

    timeout_step = registry.job("market-close").steps[0]
    matrix["timeout"], _ = _run_mocked_case(
        label="timeout",
        job_id="market-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "timeout",
        prepare_advances={timeout_step.id: [1_000.0]},
        roots=roots,
    )

    configuration_step = registry.job("market-close").steps[0]
    matrix["configuration"], _ = _run_mocked_case(
        label="configuration",
        job_id="market-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "configuration",
        prepare_actions={
            configuration_step.id: [
                JobStepError(
                    "configuration",
                    safe_message="mocked configuration unavailable",
                )
            ]
        },
        roots=roots,
    )

    dependency_job = registry.job("options-close")
    matrix["required_dependency_block"], dependency_executor = _run_mocked_case(
        label="required-dependency-block",
        job_id="options-close",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "required-dependency-block",
        publish_actions={
            dependency_job.steps[0].id: JobStepError(
                "temporary",
                safe_message="mocked predecessor failed",
            )
        },
        roots=roots,
    )
    if [event for event in dependency_executor.events if event[0] == "publish"] != [
        ("publish", dependency_job.steps[0].id)
    ]:
        raise ValidationError("Stage 7 required predecessor did not block its dependent step")

    independent_job = registry.job("macro-monthly")
    matrix["independent_continuation"], independent_executor = _run_mocked_case(
        label="independent-continuation",
        job_id="macro-monthly",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "independent-continuation",
        prepare_actions={
            independent_job.steps[0].id: [
                JobStepError(
                    "configuration",
                    safe_message="mocked configuration unavailable",
                )
            ]
        },
        marker_period="2026-10",
        roots=roots,
    )
    expected_independent = [
        ("publish", independent_job.steps[1].id),
        ("publish", independent_job.steps[2].id),
    ]
    if [event for event in independent_executor.events if event[0] == "publish"] != expected_independent:
        raise ValidationError("Stage 7 independent steps did not continue after a failure")

    matrix["monthly_success_marker"], _ = _run_mocked_case(
        label="monthly-success-marker",
        job_id="macro-monthly",
        registry=registry,
        store_map=store_map,
        state_root=state_root / "monthly-success-marker",
        marker_period="2026-08",
        roots=roots,
    )

    _require_case(matrix, "changed", outcome="changed", exit_code=0)
    _require_case(matrix, "unchanged", outcome="unchanged", exit_code=0)
    _require_case(matrix, "calendar_skipped", outcome="skipped", exit_code=0)
    _require_case(matrix, "partial_commit", outcome="partial", exit_code=70)
    _require_case(matrix, "retry_exhausted", outcome="failed", exit_code=75)
    _require_case(matrix, "lock_temporary", outcome="failed", exit_code=75)
    _require_case(matrix, "timeout", outcome="failed", exit_code=124)
    _require_case(matrix, "configuration", outcome="failed", exit_code=78)
    _require_case(matrix, "required_dependency_block", outcome="failed", exit_code=75)
    _require_case(matrix, "independent_continuation", outcome="failed", exit_code=78)
    _require_case(matrix, "monthly_success_marker", outcome="unchanged", exit_code=0)

    required_receipt = matrix["required_dependency_block"]["receipt"]
    if not isinstance(required_receipt, Mapping) or not any(
        step["error_codes"] == ["dependency_blocked"]
        for step in required_receipt["steps"]
    ):
        raise ValidationError("Stage 7 required dependency block was not receipted")
    return {
        "mode": "mocked_orchestration",
        "provider_parity": "not_claimed",
        "cases": matrix,
    }


def _real_fixture_replay(
    *,
    project: Path,
    root: Path,
    source: StoreMap,
    registry: Registry,
) -> dict[str, object]:
    """Replay all reviewed fixtures through the real runner with zero writes."""

    manifest = FixtureManifest.load(
        _FIXTURE_MANIFEST_PATH,
        project_root=project,
    )
    before = mutation_fingerprint(source)
    jobs: dict[str, object] = {}
    for job_id in _STAGE7_JOB_IDS:
        job = registry.job(job_id)
        clock = _FixedClock()
        executor = _RecordingFixtureExecutor(
            Stage7FixtureStepExecutor(source, manifest)
        )
        state_root = root / "fixture-replay-state" / job_id
        runner = Stage7JobRunner(
            registry,
            source,
            PrivateJobState(state_root),
            executor,
            now=clock.now,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            run_id_source=_DeterministicRunIds(f"real-fixture-{job_id}"),
            code_revision="7" * 40,
            timezone_name="America/Toronto",
            lock_timeout_seconds=0.0,
        )
        receipt = runner.run(
            job_id,
            marker_period="2026-11" if job_id == "macro-monthly" else None,
        )
        _assert_private_evidence(
            state_root,
            receipt,
            expect_marker=job_id == "macro-monthly",
            roots=(project, root),
        )
        normalized = _normalize_receipt(receipt)
        expected_roles = tuple(sorted(job.required_stores))
        if (
            receipt.semantic_outcome != "unchanged"
            or receipt.aggregate_exit_code != 0
            or any(step.outcome != "unchanged" for step in receipt.steps)
        ):
            raise ValidationError("Real Stage 7 fixture replay was not unchanged")
        if len(receipt.lock_order) != len(expected_roles):
            raise ValidationError("Real Stage 7 replay did not acquire its full lock set")
        for step in job.steps:
            if executor.lock_roles.get(step.id) != [expected_roles]:
                raise ValidationError("Real Stage 7 replay lock roles did not match the job")
        first_publish = next(
            (index for index, event in enumerate(executor.events) if event[0] == "publish"),
            None,
        )
        if first_publish is not None and any(
            event[0] == "prepare" for event in executor.events[first_publish + 1 :]
        ):
            raise ValidationError("Real Stage 7 replay published before preparation completed")
        if clock.sleeps:
            raise ValidationError("Real Stage 7 fixture replay retried unexpectedly")
        if before["sha256"] != mutation_fingerprint(source)["sha256"]:
            raise ValidationError("Real Stage 7 fixture replay changed a source store")
        jobs[job_id] = {
            "receipt": normalized,
            "lock_roles": list(expected_roles),
        }
    if before["sha256"] != mutation_fingerprint(source)["sha256"]:
        raise ValidationError("Real Stage 7 fixture replay changed the source sequence")
    return {
        "mode": "real_fixture_replay",
        "provider_parity": "not_claimed",
        "fixture_only": True,
        "jobs": jobs,
    }


def _backup_evidence(
    *,
    root: Path,
    source: StoreMap,
    registry: Registry,
) -> dict[str, object]:
    """Run copy-only online backup/restore and retain a path-free cohort digest."""

    source_before = mutation_fingerprint(source)
    source_logical_before = logical_manifest(source, registry)
    backup = backup_all(source, registry, target_root=root / "backup")
    source_after_backup = mutation_fingerprint(source)
    if source_before["sha256"] != source_after_backup["sha256"]:
        raise ValidationError("Stage 7 backup changed the source cohort")

    backup_before_restore = mutation_fingerprint(backup.store_map)
    restored = restore_all(backup, registry, target_root=root / "restored")
    backup_after_restore = mutation_fingerprint(backup.store_map)
    if backup_before_restore["sha256"] != backup_after_restore["sha256"]:
        raise ValidationError("Stage 7 restore changed the backup cohort")

    source_logical_after = logical_manifest(source, registry)
    backup_logical = logical_manifest(backup.store_map, registry)
    restored_logical = logical_manifest(restored.store_map, registry)
    if (
        source_logical_before["sha256"] != source_logical_after["sha256"]
        or source_logical_after["sha256"] != backup_logical["sha256"]
        or backup_logical["sha256"] != restored_logical["sha256"]
    ):
        raise ValidationError("Stage 7 backup/restore logical manifests diverged")
    if backup.source_health != backup.backup_health or restored.backup_health != restored.restored_health:
        raise ValidationError("Stage 7 backup/restore health cohorts diverged")

    source_after_reads = mutation_fingerprint(source)
    backup_after_reads = mutation_fingerprint(backup.store_map)
    restored_before_reads = mutation_fingerprint(restored.store_map)
    _ = logical_manifest(restored.store_map, registry)
    restored_after_reads = mutation_fingerprint(restored.store_map)
    if (
        source_after_reads["sha256"] != source_before["sha256"]
        or backup_after_reads["sha256"] != backup_before_restore["sha256"]
        or restored_before_reads["sha256"] != restored_after_reads["sha256"]
    ):
        raise ValidationError("Stage 7 logical comparison was not read-only")

    primitive: dict[str, object] = {
        "backup": backup.to_primitive(),
        "restore": restored.to_primitive(),
        "source_backup_equal": True,
        "backup_restored_equal": True,
        "source_neutral": True,
        "backup_neutral": True,
        "read_only_equality": True,
    }
    _assert_path_free(primitive, roots=(root,))
    return primitive


def run_clean_stage7_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Run Stage 6 plus the bounded manual-first Stage 7 rehearsal offline."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    roots = (project, root)
    with _offline_execution_guard() as offline_guard:
        stage6_evidence = run_clean_stage6_rebuild(
            project_root=project,
            work_root=root / "stage6",
        )
        registry = stage7_registry_profile(
            load_registry(
                project / CANONICAL_REGISTRY_PATH,
                project_root=project,
                environment={},
            )
        )
        if (
            registry.schema_version != "1.3.0"
            or registry.registry_version != "2.5.0"
            or tuple(job.id for job in registry.jobs) != _STAGE7_JOB_IDS
            or len(registry.jobs) != 8
            or registry.raw["exports"] != []
        ):
            raise ValidationError("Stage 7 requires the reviewed canonical 1.3.0/2.5.0 registry")
        if any(job.lifecycle["scheduling_enabled"] for job in registry.jobs):
            raise ValidationError("Stage 7 fixture registry unexpectedly enabled scheduling")

        source = explicit_store_map(
            root / "stage6" / "stage5" / "stage4" / "stage3" / "stage2" / "source"
        )
        stage6_restored = explicit_store_map(root / "stage6" / "stage5" / "stage4" / "restored")
        source_pre_stage7 = mutation_fingerprint(source)
        restored_pre_stage7 = mutation_fingerprint(stage6_restored)
        source_heads = initialize_all(source, registry)
        restored_heads = initialize_all(stage6_restored, registry)
        if source_heads != restored_heads:
            raise ValidationError("Stage 7 source and restored migration heads diverged")
        _assert_identical(
            source_pre_stage7,
            mutation_fingerprint(source),
            "Stage 7 registry initialization changed Stage 6 source evidence",
        )
        _assert_identical(
            restored_pre_stage7,
            mutation_fingerprint(stage6_restored),
            "Stage 7 registry initialization changed Stage 6 restored evidence",
        )

        source_before = mutation_fingerprint(source)
        restored_before = mutation_fingerprint(stage6_restored)
        dry_run_root = root / "dry-run-stores"
        dry_run_state_root = root / "dry-run-state"
        dry_run_map = explicit_store_map(dry_run_root)
        plan_digests, dry_run_catalog = _dry_run_catalog(registry, dry_run_map)
        if dry_run_root.exists() or dry_run_state_root.exists():
            raise ValidationError("Stage 7 dry run created database, lock, or private state")
        _assert_path_free(dry_run_catalog, roots=roots)

        mocked_matrix = _mocked_outcome_matrix(
            registry=registry,
            store_map=source,
            state_root=root / "private-state",
            roots=roots,
        )
        _assert_identical(
            source_before,
            mutation_fingerprint(source),
            "Mocked Stage 7 orchestration changed a source store",
        )
        _assert_identical(
            restored_before,
            mutation_fingerprint(stage6_restored),
            "Mocked Stage 7 orchestration changed a restored store",
        )

        real_fixture_replay = _real_fixture_replay(
            project=project,
            root=root,
            source=source,
            registry=registry,
        )
        _assert_identical(
            source_before,
            mutation_fingerprint(source),
            "Real Stage 7 fixture replay changed a source store",
        )

        backup_evidence = _backup_evidence(root=root, source=source, registry=registry)
        _assert_identical(
            source_before,
            mutation_fingerprint(source),
            "Stage 7 backup/restore changed the source store cohort",
        )
        _assert_identical(
            restored_before,
            mutation_fingerprint(stage6_restored),
            "Stage 7 backup/restore changed the Stage 6 restored cohort",
        )
        if offline_guard.blocked_calls:
            raise ValidationError("Stage 7 offline guard observed a live-capable call")

    catalog = _job_catalog(registry)
    job_catalog_sha256 = _sha256_primitive(catalog)
    dry_run_catalog_sha256 = _sha256_primitive(dry_run_catalog)
    outcome_matrix_sha256 = _sha256_primitive(mocked_matrix)
    receipt_manifest = mocked_matrix["cases"]
    if not isinstance(receipt_manifest, Mapping):
        raise ValidationError("Stage 7 mocked outcome matrix is malformed")
    receipt_manifest_sha256 = _sha256_primitive(receipt_manifest)
    backup_manifest_sha256 = _sha256_primitive(backup_evidence)
    _assert_path_free(real_fixture_replay, roots=roots)
    real_fixture_replay_manifest_sha256 = _sha256_primitive(real_fixture_replay)

    evidence: dict[str, object] = {
        "contract": "quant_data.stage7_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "stage6_evidence_sha256": stage6_evidence["sha256"],
        "job_ids": list(_STAGE7_JOB_IDS),
        "job_catalog_sha256": job_catalog_sha256,
        "dry_run_plan_sha256": plan_digests,
        "dry_run_catalog_sha256": dry_run_catalog_sha256,
        "outcome_matrix_sha256": outcome_matrix_sha256,
        "receipt_manifest_sha256": receipt_manifest_sha256,
        "backup_manifest_sha256": backup_manifest_sha256,
        "real_fixture_replay_manifest_sha256": real_fixture_replay_manifest_sha256,
        "migration_heads": source_heads,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "source_restored_equal": True,
        "backup_source_neutral": True,
        "backup_neutral": True,
        "backup_restored_equal": True,
        "backup_read_only_equality": True,
        "manual_fixture_only": True,
        "mocked_provider_calls_only": True,
        "real_fixture_replay_unchanged": True,
        "real_fixture_replay_no_write": True,
        "live_provider_calls": 0,
        "scheduler_installations": 0,
        "scheduler_starts": 0,
        "jobs_scheduling_enabled": 0,
        "exports": 0,
    }
    _assert_path_free(evidence, roots=roots)
    evidence["sha256"] = _sha256_primitive(evidence)
    return evidence


def compare_clean_stage7_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require byte-identical strict-JSON evidence from two empty roots."""

    first = run_clean_stage7_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage7_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    _assert_identical(first, second, "Two clean Stage 7 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage7_rebuilds", "run_clean_stage7_rebuild")
