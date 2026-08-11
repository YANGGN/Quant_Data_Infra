from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError, RegistryError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
from quant_data.operations.job_receipts import PrivateJobState
from quant_data.operations.job_retry import JobStepError
from quant_data.operations.job_runner import (
    PreparedStep,
    Stage7JobRunner,
    StepPublication,
    build_dry_run_plan,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class _Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.sleeps: list[float] = []

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


class _RunIds:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self, job_id: str, plan_sha256: str, requested_at: str) -> str:
        self.index += 1
        return _sha(f"{job_id}:{plan_sha256}:{requested_at}:{self.index}")


class _Executor:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.events: list[tuple[str, str]] = []
        self.prepare_actions: dict[str, list[object]] = {}
        self.prepare_advances: dict[str, list[float]] = {}
        self.publish_advances: dict[str, float] = {}
        self.publications: dict[str, object] = {}
        self.held_role_sets: list[frozenset[str]] = []

    def prepare(self, step, attempt: int) -> PreparedStep:
        self.events.append(("prepare", step.id))
        advances = self.prepare_advances.get(step.id)
        if advances:
            self.clock.advance(advances.pop(0))
        actions = self.prepare_actions.get(step.id)
        if actions:
            action = actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            if isinstance(action, PreparedStep):
                return action
        return PreparedStep(
            semantic_identity=_sha(f"{step.id}:{attempt}"),
            payload=(step.id, attempt),
        )

    def publish(self, step, candidate, held_locks) -> StepPublication:
        self.events.append(("publish", step.id))
        self.assert_candidate(candidate)
        if not held_locks.active:
            raise AssertionError("publish received an inactive lock capability")
        self.held_role_sets.append(
            frozenset(role.value for role in held_locks.roles)
        )
        if step.id in self.publish_advances:
            self.clock.advance(self.publish_advances[step.id])
        action = self.publications.get(step.id)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, StepPublication):
            return action
        return StepPublication("unchanged")

    @staticmethod
    def assert_candidate(candidate: object) -> None:
        if not isinstance(candidate, PreparedStep):
            raise AssertionError("executor received an invalid prepared candidate")


class Stage7JobRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        store_map: StoreMap,
        state_root: Path,
        executor: _Executor,
        clock: _Clock,
        lock_timeout_seconds: float = 0.2,
        active_timeout_cap_seconds: float | None = None,
    ) -> Stage7JobRunner:
        return Stage7JobRunner(
            self.registry,
            store_map,
            PrivateJobState(state_root),
            executor,
            now=clock.now,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            run_id_source=_RunIds(),
            code_revision="d" * 40,
            lock_timeout_seconds=lock_timeout_seconds,
            active_timeout_cap_seconds=active_timeout_cap_seconds,
        )

    def test_dry_run_is_path_free_stable_and_has_zero_side_effects(self) -> None:
        first_root = self.root / "first"
        second_root = self.root / "second"
        first_map = _store_map(first_root)
        second_map = _store_map(second_root)
        first = build_dry_run_plan(self.registry, "options-close", first_map)
        second = build_dry_run_plan(self.registry, "options-close", second_map)

        self.assertEqual(dumps_strict(first.to_primitive()), dumps_strict(second.to_primitive()))
        self.assertEqual(first.sha256, second.sha256)
        rendered = dumps_strict(first.to_primitive())
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("sqlite-", rendered)
        self.assertEqual(
            [(item.alias, item.role) for item in first.locks],
            [("store-1", "macro"), ("store-2", "market")],
        )
        self.assertFalse(first_root.exists())
        self.assertFalse(second_root.exists())

        clock = _Clock()
        state_root = self.root / "state"
        executor = _Executor(clock)
        runner = self._runner(
            store_map=first_map,
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        with self.assertRaises(RegistryError):
            runner.dry_run("unknown-job")
        self.assertFalse(state_root.exists())
        self.assertEqual(executor.events, [])

    def test_calendar_skip_publishes_private_evidence_without_provider_or_lock(self) -> None:
        clock = _Clock()
        store_root = self.root / "skip-stores"
        state_root = self.root / "skip-state"
        executor = _Executor(clock)
        runner = self._runner(
            store_map=_store_map(store_root),
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        receipt = runner.run(
            "news-hourly",
            calendar_decision="skipped",
            skip_reason="calendar closed",
        )
        self.assertEqual(receipt.semantic_outcome, "skipped")
        self.assertEqual(receipt.aggregate_exit_code, 0)
        self.assertEqual(executor.events, [])
        self.assertEqual(receipt.lock_order, ())
        self.assertFalse((store_root / ".quant_data_locks").exists())
        self.assertEqual(len(tuple((state_root / "receipts").glob("*.json"))), 1)
        self.assertEqual(len(tuple((state_root / "logs").glob("*.json"))), 1)

    def test_options_job_prepares_all_steps_then_holds_both_locks_for_publication(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        job = self.registry.job("options-close")
        for step in job.steps:
            executor.publications[step.id] = StepPublication(
                "succeeded",
                committed_stores=step.write_stores,
                ingestion_run_ids=(_sha(f"run:{step.id}"),),
            )
        runner = self._runner(
            store_map=_store_map(self.root / "options"),
            state_root=self.root / "options-state",
            executor=executor,
            clock=clock,
        )
        receipt = runner.run("options-close")

        self.assertEqual(
            executor.events,
            [
                ("prepare", job.steps[0].id),
                ("prepare", job.steps[1].id),
                ("publish", job.steps[0].id),
                ("publish", job.steps[1].id),
            ],
        )
        self.assertTrue(
            all(roles == frozenset({"macro", "market"}) for roles in executor.held_role_sets)
        )
        self.assertEqual(receipt.semantic_outcome, "changed")
        self.assertEqual(receipt.aggregate_exit_code, 0)
        self.assertEqual(len(receipt.lock_order), 2)
        self.assertNotIn(str(self.root), dumps_strict(receipt.to_primitive()))

    def test_retry_timeout_and_configuration_failures_are_bounded_before_locks(self) -> None:
        step_id = self.registry.job("market-close").steps[0].id

        retry_clock = _Clock()
        retry_executor = _Executor(retry_clock)
        retry_executor.prepare_actions[step_id] = [
            JobStepError(
                "rate_limited",
                retryable=True,
                retry_after_seconds=0.5,
                safe_message="bounded rate limit",
            )
        ]
        retry_runner = self._runner(
            store_map=_store_map(self.root / "retry"),
            state_root=self.root / "retry-state",
            executor=retry_executor,
            clock=retry_clock,
        )
        retry_receipt = retry_runner.run("market-close")
        self.assertEqual(retry_receipt.steps[0].attempts, 2)
        self.assertEqual(retry_receipt.semantic_outcome, "unchanged")
        self.assertEqual(retry_clock.sleeps, [0.5])
        self.assertEqual(
            [event[0] for event in retry_executor.events],
            ["prepare", "prepare", "publish"],
        )

        config_clock = _Clock()
        config_executor = _Executor(config_clock)
        config_executor.prepare_actions[step_id] = [
            JobStepError(
                "configuration",
                safe_message="required configuration unavailable",
            )
        ]
        config_runner = self._runner(
            store_map=_store_map(self.root / "config"),
            state_root=self.root / "config-state",
            executor=config_executor,
            clock=config_clock,
        )
        config_receipt = config_runner.run("market-close")
        self.assertEqual(config_receipt.aggregate_exit_code, 78)
        self.assertEqual(config_receipt.steps[0].attempts, 1)
        self.assertFalse(any(event[0] == "publish" for event in config_executor.events))

        timeout_clock = _Clock()
        timeout_executor = _Executor(timeout_clock)
        timeout_executor.prepare_advances[step_id] = [1000]
        timeout_runner = self._runner(
            store_map=_store_map(self.root / "timeout"),
            state_root=self.root / "timeout-state",
            executor=timeout_executor,
            clock=timeout_clock,
        )
        timeout_receipt = timeout_runner.run("market-close")
        self.assertEqual(timeout_receipt.aggregate_exit_code, 124)
        self.assertEqual(timeout_receipt.steps[0].outcome, "timed_out")
        self.assertFalse(any(event[0] == "publish" for event in timeout_executor.events))

    def test_publication_timeout_is_124_and_reports_a_partial_commit(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        job = self.registry.job("market-close")
        step = job.steps[0]
        executor.publications[step.id] = StepPublication(
            "succeeded",
            committed_stores=("market",),
            ingestion_run_ids=(_sha("late market run"),),
        )
        executor.publish_advances[step.id] = 1000
        runner = self._runner(
            store_map=_store_map(self.root / "publish-timeout"),
            state_root=self.root / "publish-timeout-state",
            executor=executor,
            clock=clock,
        )
        receipt = runner.run("market-close")
        self.assertEqual(receipt.aggregate_exit_code, 124)
        self.assertEqual(receipt.semantic_outcome, "partial")
        self.assertEqual(receipt.steps[0].outcome, "timed_out")
        self.assertEqual(receipt.committed_store_aliases, ("store-1",))

    def test_dependency_failure_blocks_dependent_but_independent_steps_continue(self) -> None:
        options_clock = _Clock()
        options_executor = _Executor(options_clock)
        options_job = self.registry.job("options-close")
        options_executor.publications[options_job.steps[0].id] = JobStepError(
            "temporary",
            safe_message="temporary fixture failure",
        )
        options_runner = self._runner(
            store_map=_store_map(self.root / "dependency"),
            state_root=self.root / "dependency-state",
            executor=options_executor,
            clock=options_clock,
        )
        options_receipt = options_runner.run("options-close")
        self.assertEqual(options_receipt.aggregate_exit_code, 75)
        self.assertEqual(
            [event for event in options_executor.events if event[0] == "publish"],
            [("publish", options_job.steps[0].id)],
        )
        self.assertEqual(options_receipt.steps[1].error_codes, ("dependency_blocked",))

        monthly_clock = _Clock()
        monthly_executor = _Executor(monthly_clock)
        monthly_job = self.registry.job("macro-monthly")
        monthly_executor.prepare_actions[monthly_job.steps[0].id] = [
            JobStepError(
                "configuration",
                safe_message="required configuration unavailable",
            )
        ]
        monthly_runner = self._runner(
            store_map=_store_map(self.root / "independent"),
            state_root=self.root / "independent-state",
            executor=monthly_executor,
            clock=monthly_clock,
        )
        monthly_receipt = monthly_runner.run("macro-monthly")
        self.assertEqual(monthly_receipt.aggregate_exit_code, 78)
        self.assertEqual(
            [event[1] for event in monthly_executor.events if event[0] == "publish"],
            [monthly_job.steps[1].id, monthly_job.steps[2].id],
        )

    def test_partial_commit_and_exit_precedence_are_honest_and_never_mark_success(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        job = self.registry.job("macro-monthly")
        executor.publications[job.steps[0].id] = StepPublication(
            "succeeded",
            committed_stores=("macro",),
            ingestion_run_ids=(_sha("macro changed"),),
        )
        executor.prepare_actions[job.steps[1].id] = [
            JobStepError(
                "configuration",
                safe_message="required configuration unavailable",
            )
        ]
        executor.prepare_actions[job.steps[2].id] = [
            JobStepError(
                "timeout",
                safe_message="step timeout elapsed",
            )
        ]
        state_root = self.root / "partial-state"
        runner = self._runner(
            store_map=_store_map(self.root / "partial"),
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        receipt = runner.run("macro-monthly")
        self.assertEqual(receipt.semantic_outcome, "partial")
        self.assertEqual(receipt.aggregate_exit_code, 78)
        self.assertEqual(receipt.committed_store_aliases, ("store-1",))
        self.assertFalse((state_root / "markers").exists())

    def test_lock_contention_maps_to_temporary_failure_without_publication(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        store_map = _store_map(self.root / "contended")
        runner = self._runner(
            store_map=store_map,
            state_root=self.root / "contended-state",
            executor=executor,
            clock=clock,
            lock_timeout_seconds=0,
        )
        with StoreWriteLock(store_map.path(StoreRole.MARKET)):
            receipt = runner.run("market-close")
        self.assertEqual(receipt.aggregate_exit_code, 75)
        self.assertEqual(receipt.semantic_outcome, "failed")
        self.assertFalse(any(event[0] == "publish" for event in executor.events))

    def test_unchanged_job_does_not_mutate_initialized_stores_and_monthly_marker_is_valid(self) -> None:
        store_map = _store_map(self.root / "initialized")
        initialize_all(store_map, self.registry)
        before = mutation_fingerprint(store_map)

        clock = _Clock()
        executor = _Executor(clock)
        runner = self._runner(
            store_map=store_map,
            state_root=self.root / "unchanged-state",
            executor=executor,
            clock=clock,
        )
        receipt = runner.run("macro-monthly", marker_period="2026-08")
        after = mutation_fingerprint(store_map)
        self.assertEqual(dumps_strict(before), dumps_strict(after))
        self.assertEqual(receipt.semantic_outcome, "unchanged")
        marker = self.root / "unchanged-state" / "markers" / "macro-monthly-2026-08.json"
        self.assertTrue(marker.is_file())

    def test_runtime_metadata_marker_and_state_fail_before_attempt(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        store_map = _store_map(self.root / "preflight-stores")
        state_root = self.root / "preflight-state"

        with self.assertRaises(ValidationError):
            Stage7JobRunner(
                self.registry,
                store_map,
                PrivateJobState(state_root),
                executor,
                now=clock.now,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
                timezone_name="Not/AZone",
            )
        with self.assertRaises(ValidationError):
            Stage7JobRunner(
                self.registry,
                store_map,
                PrivateJobState(state_root),
                executor,
                now=clock.now,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
                code_revision="not-a-revision",
            )

        runner = self._runner(
            store_map=store_map,
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        with self.assertRaises(ValidationError):
            runner.run("macro-monthly", marker_period="2026-99")
        with self.assertRaises(ValidationError):
            runner.run("market-close", marker_period="2026-08")
        self.assertEqual(executor.events, [])
        self.assertFalse(state_root.exists())
        self.assertFalse((self.root / "preflight-stores").exists())

        dry_runner = Stage7JobRunner(
            self.registry,
            store_map,
            PrivateJobState(self.root / "dry-state", dry_run=True),
            executor,
            now=clock.now,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            code_revision="d" * 40,
        )
        with self.assertRaises(ValidationError):
            dry_runner.run("market-close")
        self.assertEqual(executor.events, [])
        self.assertFalse((self.root / "dry-state").exists())

    def test_lock_io_failure_returns_74_receipt_without_publication(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        state_root = self.root / "lock-io-state"
        runner = self._runner(
            store_map=_store_map(self.root / "lock-io-stores"),
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        with mock.patch(
            "quant_data.operations.job_runner.acquire_write_session",
            side_effect=OSError("simulated lock input failure"),
        ):
            receipt = runner.run("market-close")
        self.assertEqual(receipt.aggregate_exit_code, 74)
        self.assertEqual(receipt.semantic_outcome, "failed")
        self.assertEqual(receipt.steps[0].error_codes, ("io",))
        self.assertFalse(any(event[0] == "publish" for event in executor.events))
        self.assertEqual(len(tuple((state_root / "receipts").glob("*.json"))), 1)

    def test_blocking_prepare_and_publish_are_actively_timed_out(self) -> None:
        class BlockingPrepare(_Executor):
            def prepare(self, step, attempt: int) -> PreparedStep:
                time.sleep(1)
                return super().prepare(step, attempt)

        prepare_clock = _Clock()
        prepare_executor = BlockingPrepare(prepare_clock)
        prepare_runner = Stage7JobRunner(
            self.registry,
            _store_map(self.root / "blocking-prepare"),
            PrivateJobState(self.root / "blocking-prepare-state"),
            prepare_executor,
            now=prepare_clock.now,
            monotonic=time.monotonic,
            sleeper=time.sleep,
            run_id_source=_RunIds(),
            code_revision="d" * 40,
            active_timeout_cap_seconds=0.05,
        )
        started = time.monotonic()
        prepare_receipt = prepare_runner.run("market-close")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(prepare_receipt.aggregate_exit_code, 124)
        self.assertFalse(any(event[0] == "publish" for event in prepare_executor.events))

        class BlockingPublish(_Executor):
            def publish(self, step, candidate, held_locks) -> StepPublication:
                time.sleep(1)
                return super().publish(step, candidate, held_locks)

        publish_clock = _Clock()
        publish_executor = BlockingPublish(publish_clock)
        publish_map = _store_map(self.root / "blocking-publish")
        publish_runner = Stage7JobRunner(
            self.registry,
            publish_map,
            PrivateJobState(self.root / "blocking-publish-state"),
            publish_executor,
            now=publish_clock.now,
            monotonic=time.monotonic,
            sleeper=time.sleep,
            run_id_source=_RunIds(),
            code_revision="d" * 40,
            active_timeout_cap_seconds=0.05,
        )
        started = time.monotonic()
        publish_receipt = publish_runner.run("market-close")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(publish_receipt.aggregate_exit_code, 124)
        self.assertEqual(publish_receipt.steps[0].outcome, "timed_out")
        with StoreWriteLock(publish_map.path(StoreRole.MARKET), timeout_seconds=0):
            pass

    def test_marker_preflight_and_bundle_failure_leave_no_false_success(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        state_root = self.root / "marker-state"
        marker_dir = state_root / "markers"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / "macro-monthly-2026-08.json"
        marker.write_text("existing", encoding="utf-8")
        runner = self._runner(
            store_map=_store_map(self.root / "marker-stores"),
            state_root=state_root,
            executor=executor,
            clock=clock,
        )
        with self.assertRaises(ConflictError):
            runner.run("macro-monthly", marker_period="2026-08")
        self.assertEqual(executor.events, [])
        self.assertEqual(marker.read_text(encoding="utf-8"), "existing")
        self.assertFalse((state_root / "receipts").exists())
        self.assertFalse((state_root / "logs").exists())

        failure_clock = _Clock()
        failure_executor = _Executor(failure_clock)
        failure_state = PrivateJobState(self.root / "marker-failure-state")
        failure_runner = Stage7JobRunner(
            self.registry,
            _store_map(self.root / "marker-failure-stores"),
            failure_state,
            failure_executor,
            now=failure_clock.now,
            monotonic=failure_clock.monotonic,
            sleeper=failure_clock.sleep,
            run_id_source=_RunIds(),
            code_revision="d" * 40,
        )
        with mock.patch.object(
            failure_state,
            "publish_monthly_success_marker",
            side_effect=OSError("simulated marker failure"),
        ):
            with self.assertRaises(JobStepError) as raised:
                failure_runner.run("macro-monthly", marker_period="2026-08")
        self.assertEqual(raised.exception.exit_code, 74)
        for directory in ("logs", "receipts", "markers"):
            target = failure_state.state_root / directory
            self.assertFalse(target.exists() and any(target.iterdir()))

    def test_private_receipt_io_failure_raises_74_and_cannot_report_success(self) -> None:
        clock = _Clock()
        executor = _Executor(clock)
        state = PrivateJobState(self.root / "io-state")
        runner = Stage7JobRunner(
            self.registry,
            _store_map(self.root / "io"),
            state,
            executor,
            now=clock.now,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            run_id_source=_RunIds(),
            code_revision="d" * 40,
        )
        with mock.patch.object(
            state,
            "publish_receipt",
            side_effect=OSError("simulated local failure"),
        ):
            with self.assertRaises(JobStepError) as raised:
                runner.run("news-hourly")
        self.assertEqual(raised.exception.exit_code, 74)
        self.assertFalse((self.root / "io-state" / "receipts").exists())
        logs = self.root / "io-state" / "logs"
        self.assertFalse(logs.exists() and any(logs.iterdir()))


if __name__ == "__main__":
    unittest.main()
