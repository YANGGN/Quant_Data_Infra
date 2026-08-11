"""Acceptance coverage for the bounded Stage 7 manual fixture wrapper."""

from __future__ import annotations

import io
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.migrations import initialize_all
from quant_data.operations import manual_job
from quant_data.operations.job_retry import JobStepError
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _StubReceipt:
    job_id: str
    semantic_outcome: str
    aggregate_exit_code: int

    def to_primitive(self) -> dict[str, object]:
        return {
            "aggregate_exit_code": self.aggregate_exit_code,
            "job_id": self.job_id,
            "semantic_outcome": self.semantic_outcome,
        }


class _StubRunner:
    def __init__(self, result: _StubReceipt | BaseException) -> None:
        self._result = result
        self.calls: list[tuple[str, str | None]] = []

    def run(self, job_id: str, *, marker_period: str | None = None) -> _StubReceipt:
        self.calls.append((job_id, marker_period))
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class ManualJobWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _argv(
        self,
        *,
        job: str = "market-close",
        store_name: str = "stores",
        state_name: str = "state",
        dry_run: bool = False,
        marker_period: str | None = None,
    ) -> list[str]:
        values = [
            "--project-root",
            str(PROJECT_ROOT),
            "--store-root",
            str(self.root / store_name),
            "--state-root",
            str(self.root / state_name),
            "--job",
            job,
        ]
        if dry_run:
            values.append("--dry-run")
        if marker_period is not None:
            values.extend(("--marker-period", marker_period))
        return values

    @staticmethod
    def _payload(text: str) -> dict[str, Any]:
        payload = loads_strict(text)
        if not isinstance(payload, dict):
            raise AssertionError("manual wrapper emitted a non-object JSON payload")
        return payload

    @staticmethod
    def _forbidden_factory(calls: dict[str, int], name: str):
        def factory(*args: object, **kwargs: object) -> object:
            del args, kwargs
            calls[name] += 1
            raise AssertionError(f"{name} must not be constructed")

        return factory

    def test_dry_run_is_strict_deterministic_path_free_and_runtime_free(self) -> None:
        calls = {
            "store_map": 0,
            "manifest": 0,
            "state": 0,
            "executor": 0,
            "runner": 0,
            "health": 0,
        }

        def store_map_factory(root: str | Path):
            calls["store_map"] += 1
            return explicit_store_map(root)

        def run_once(store_name: str, state_name: str) -> str:
            output = io.StringIO()
            errors = io.StringIO()
            exit_code = manual_job.main(
                self._argv(
                    store_name=store_name,
                    state_name=state_name,
                    dry_run=True,
                ),
                stdout=output,
                stderr=errors,
                store_map_factory=store_map_factory,
                fixture_manifest_loader=self._forbidden_factory(calls, "manifest"),
                state_factory=self._forbidden_factory(calls, "state"),
                executor_factory=self._forbidden_factory(calls, "executor"),
                runner_factory=self._forbidden_factory(calls, "runner"),
                health_check=self._forbidden_factory(calls, "health"),
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(errors.getvalue(), "")
            return output.getvalue()

        first = run_once("first-stores", "first-state")
        second = run_once("second-stores", "second-state")

        self.assertEqual(first, second)
        payload = self._payload(first)
        self.assertEqual(payload["contract"], "quant_data.stage7_dry_run_plan")
        self.assertEqual(
            payload["side_effects"],
            {
                "database_opens": 0,
                "lock_acquisitions": 0,
                "provider_calls": 0,
                "scheduler_mutations": 0,
                "state_writes": 0,
            },
        )
        self.assertEqual(dumps_strict(payload), first.rstrip("\n"))
        self.assertNotIn(str(self.root), first)
        self.assertNotIn(str(PROJECT_ROOT), first)
        self.assertNotIn("/tmp", first)
        self.assertEqual(
            calls,
            {
                "store_map": 2,
                "manifest": 0,
                "state": 0,
                "executor": 0,
                "runner": 0,
                "health": 0,
            },
        )
        for name in ("first-stores", "first-state", "second-stores", "second-state"):
            self.assertFalse((self.root / name).exists())

    def test_unknown_or_hostile_arguments_fail_safely_before_runtime_factories(self) -> None:
        cases = (
            (
                "unknown_job",
                self._argv(job="unknown-job", store_name="unknown-stores"),
                "invalid_request",
                "unknown-job",
            ),
            (
                "extra_argument",
                self._argv(store_name="extra-stores")
                + ["--not-authorized=https://token.invalid/secret"],
                "invalid_arguments",
                "token.invalid",
            ),
        )
        for label, arguments, expected_error, hostile_text in cases:
            with self.subTest(label=label):
                calls = {
                    "store_map": 0,
                    "manifest": 0,
                    "state": 0,
                    "executor": 0,
                    "runner": 0,
                    "health": 0,
                }
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = manual_job.main(
                    arguments,
                    stdout=output,
                    stderr=errors,
                    store_map_factory=self._forbidden_factory(calls, "store_map"),
                    fixture_manifest_loader=self._forbidden_factory(calls, "manifest"),
                    state_factory=self._forbidden_factory(calls, "state"),
                    executor_factory=self._forbidden_factory(calls, "executor"),
                    runner_factory=self._forbidden_factory(calls, "runner"),
                    health_check=self._forbidden_factory(calls, "health"),
                )

                self.assertEqual(exit_code, 64)
                self.assertEqual(output.getvalue(), "")
                payload = self._payload(errors.getvalue())
                self.assertEqual(payload["contract"], "quant_data.stage7_manual_error")
                self.assertEqual(payload["exit_code"], 64)
                self.assertEqual(payload["error"]["code"], expected_error)
                self.assertNotIn(str(self.root), errors.getvalue())
                self.assertNotIn(hostile_text, errors.getvalue())
                self.assertEqual(calls, {name: 0 for name in calls})

    def test_stub_receipts_preserve_aggregate_exit_and_io_exception_maps_to_74(self) -> None:
        for exit_code in (0, 75, 78, 124):
            with self.subTest(exit_code=exit_code):
                output = io.StringIO()
                errors = io.StringIO()
                stub = _StubRunner(
                    _StubReceipt(
                        "market-close",
                        "unchanged" if exit_code == 0 else "failed",
                        exit_code,
                    )
                )
                code = manual_job.main(
                    self._argv(
                        store_name=f"stub-{exit_code}-stores",
                        state_name=f"stub-{exit_code}-state",
                    ),
                    stdout=output,
                    stderr=errors,
                    fixture_manifest_loader=lambda *args, **kwargs: object(),
                    state_factory=lambda *args, **kwargs: object(),
                    executor_factory=lambda *args, **kwargs: object(),
                    runner_factory=lambda *args, **kwargs: stub,
                    health_check=lambda *args, **kwargs: None,
                )
                self.assertEqual(code, exit_code)
                self.assertEqual(errors.getvalue(), "")
                payload = self._payload(output.getvalue())
                self.assertEqual(payload["aggregate_exit_code"], exit_code)
                self.assertEqual(payload["receipt"]["aggregate_exit_code"], exit_code)
                self.assertEqual(stub.calls, [("market-close", None)])
                self.assertFalse((self.root / f"stub-{exit_code}-stores").exists())
                self.assertFalse((self.root / f"stub-{exit_code}-state").exists())

        output = io.StringIO()
        errors = io.StringIO()
        code = manual_job.main(
            self._argv(store_name="io-stores", state_name="io-state"),
            stdout=output,
            stderr=errors,
            fixture_manifest_loader=lambda *args, **kwargs: object(),
            state_factory=lambda *args, **kwargs: object(),
            executor_factory=lambda *args, **kwargs: object(),
            runner_factory=lambda *args, **kwargs: _StubRunner(
                JobStepError("io", safe_message="local fixture output unavailable")
            ),
            health_check=lambda *args, **kwargs: None,
        )
        self.assertEqual(code, 74)
        self.assertEqual(output.getvalue(), "")
        payload = self._payload(errors.getvalue())
        self.assertEqual(payload["exit_code"], 74)
        self.assertEqual(payload["error"]["code"], "io")
        self.assertNotIn(str(self.root), errors.getvalue())

    def test_marker_period_for_nonmonthly_job_fails_before_store_or_state_construction(self) -> None:
        calls = {
            "store_map": 0,
            "manifest": 0,
            "state": 0,
            "executor": 0,
            "runner": 0,
            "health": 0,
        }
        output = io.StringIO()
        errors = io.StringIO()
        exit_code = manual_job.main(
            self._argv(
                store_name="marker-stores",
                state_name="marker-state",
                marker_period="2026-08",
            ),
            stdout=output,
            stderr=errors,
            store_map_factory=self._forbidden_factory(calls, "store_map"),
            fixture_manifest_loader=self._forbidden_factory(calls, "manifest"),
            state_factory=self._forbidden_factory(calls, "state"),
            executor_factory=self._forbidden_factory(calls, "executor"),
            runner_factory=self._forbidden_factory(calls, "runner"),
            health_check=self._forbidden_factory(calls, "health"),
        )
        self.assertEqual(exit_code, 64)
        self.assertEqual(output.getvalue(), "")
        payload = self._payload(errors.getvalue())
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertEqual(calls, {name: 0 for name in calls})
        self.assertFalse((self.root / "marker-stores").exists())
        self.assertFalse((self.root / "marker-state").exists())

    def test_real_market_close_changes_once_then_replays_without_store_mutation(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        store_root = self.root / "real-stores"
        state_root = self.root / "real-state"
        store_map = explicit_store_map(store_root)
        initialize_all(store_map, registry)

        first_output = io.StringIO()
        first_errors = io.StringIO()
        first_code = manual_job.main(
            self._argv(store_name="real-stores", state_name="real-state"),
            stdout=first_output,
            stderr=first_errors,
        )
        self.assertEqual(first_code, 0)
        self.assertEqual(first_errors.getvalue(), "")
        first = self._payload(first_output.getvalue())
        self.assertEqual(first["contract"], "quant_data.stage7_manual_result")
        self.assertEqual(first["job_id"], "market-close")
        self.assertEqual(first["semantic_outcome"], "changed")
        self.assertEqual(first["aggregate_exit_code"], 0)
        self.assertEqual(first["receipt"]["semantic_outcome"], "changed")
        self.assertEqual(first["receipt"]["committed_store_aliases"], ["store-1"])
        self.assertNotIn(str(self.root), first_output.getvalue())
        self.assertNotIn(str(store_root), first_output.getvalue())
        after_first = mutation_fingerprint(store_map)

        second_output = io.StringIO()
        second_errors = io.StringIO()
        second_code = manual_job.main(
            self._argv(store_name="real-stores", state_name="real-state"),
            stdout=second_output,
            stderr=second_errors,
        )
        self.assertEqual(second_code, 0)
        self.assertEqual(second_errors.getvalue(), "")
        second = self._payload(second_output.getvalue())
        self.assertEqual(second["semantic_outcome"], "unchanged")
        self.assertEqual(second["aggregate_exit_code"], 0)
        self.assertEqual(second["receipt"]["committed_store_aliases"], [])
        self.assertNotIn(str(self.root), second_output.getvalue())
        after_second = mutation_fingerprint(store_map)
        self.assertEqual(dumps_strict(after_first), dumps_strict(after_second))


if __name__ == "__main__":
    unittest.main()
