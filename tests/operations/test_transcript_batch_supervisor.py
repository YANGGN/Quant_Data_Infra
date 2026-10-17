"""Focused temporary-store tests for bounded Luna pause recovery."""
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations.transcript_batch_supervisor import supervise, _restartable, LOCK_TIMEOUT
from quant_data.operations.transcript_universe_batch import load
from tests.operations import test_transcript_universe_batch as fixtures


class SupervisorTests(unittest.TestCase):
    setUp = fixtures.UniverseBatchTests.setUp
    tearDown = fixtures.UniverseBatchTests.tearDown
    batch = fixtures.UniverseBatchTests.batch
    transport = fixtures.UniverseBatchTests.transport
    forbidden = fixtures.UniverseBatchTests.forbidden

    def test_lock_pause_waits_then_publishes_same_response_once(self):
        job = self.batch(); plan = job.prepare(); calls = []; sleeps = []
        publish = job.publisher.publish; failures = [True]
        def flaky(*args, **kwargs):
            if failures:
                failures.pop()
                raise ConflictError(LOCK_TIMEOUT)
            return publish(*args, **kwargs)
        with patch.object(job.publisher, "publish", side_effect=flaky):
            result = supervise(job, plan["plan_id"], self.transport(calls),
                               concurrency=1, sleep=sleeps.append, now=lambda: 100)
        self.assertEqual((result["status"], len(calls), result["automatic_restarts"]),
                         ("completed", 1, 1))
        self.assertEqual(sleeps, [180])
        before = mutation_fingerprint(self.stores)
        replay = supervise(job, plan["plan_id"], self.forbidden,
                           concurrency=1, sleep=sleeps.append, now=lambda: 100)
        self.assertEqual(replay["automatic_restarts"], 1)
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_restart_limit_persists_and_does_not_repeat_model_call(self):
        job = self.batch(); plan = job.prepare(); calls = []; sleeps = []
        with patch.object(job.publisher, "publish", side_effect=ConflictError(LOCK_TIMEOUT)):
            result = supervise(job, plan["plan_id"], self.transport(calls), concurrency=1,
                               max_restarts=2, sleep=sleeps.append, now=lambda: 100)
        self.assertEqual((result["supervisor_status"], result["automatic_restarts"]),
                         ("restart_limit_reached", 2))
        self.assertEqual((len(calls), sleeps), (1, [180, 180]))
        done = supervise(job, plan["plan_id"], self.forbidden, concurrency=1,
                         max_restarts=2, sleep=sleeps.append, now=lambda: 100)
        self.assertEqual((done["status"], done["automatic_restarts"]), ("completed", 2))

    def test_interrupted_backoff_retains_restart_reservation(self):
        job = self.batch(); plan = job.prepare(); calls = []
        with patch.object(job.publisher, "publish", side_effect=ConflictError(LOCK_TIMEOUT)):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                supervise(job, plan["plan_id"], self.transport(calls), concurrency=1,
                          sleep=lambda _: (_ for _ in ()).throw(RuntimeError("interrupted")),
                          now=lambda: 100)
        state = load(job.root/"runs"/plan["plan_id"]/"supervisor"/"state.json")
        self.assertEqual((state["restarts_used"], state["pending_restart"]), (1, True))
        sleeps = []
        result = supervise(job, plan["plan_id"], self.forbidden, concurrency=1,
                           sleep=sleeps.append, now=lambda: 160)
        self.assertEqual((result["status"], result["automatic_restarts"], sleeps),
                         ("completed", 1, [120]))

    def test_unknown_publication_conflict_is_held_without_restart(self):
        job = self.batch(); plan = job.prepare(); calls = []; sleeps = []
        with patch.object(job.publisher, "publish", side_effect=ConflictError("Source changed")):
            result = supervise(job, plan["plan_id"], self.transport(calls), concurrency=1,
                               sleep=sleeps.append)
        self.assertEqual((result["status"], result["automatic_restarts"], sleeps),
                         ("paused_publication", 0, []))

    def test_ready_lock_timeout_is_retried_before_any_model_call(self):
        job = self.batch(); plan = job.prepare(); calls = []; sleeps = []
        ready = job.ready; attempts = [True]
        def flaky():
            if attempts:
                attempts.pop()
                raise ConflictError(LOCK_TIMEOUT)
            return ready()
        with patch.object(job, "ready", side_effect=flaky):
            result = supervise(job, plan["plan_id"], self.transport(calls), concurrency=1,
                               sleep=sleeps.append, now=lambda: 100)
        self.assertEqual((result["status"], len(calls), sleeps), ("completed", 1, [180]))

    def test_permanent_transport_and_budget_pauses_are_not_restarted(self):
        temporary = {"x": {"transport_failed": True, "retryable_transport_failure": True}}
        self.assertTrue(_restartable({"status": "paused_after_transport_failures"}, temporary, set()))
        self.assertFalse(_restartable({"status": "paused_after_transport_failures"}, temporary, {"x"}))
        temporary["x"]["retryable_transport_failure"] = False
        self.assertFalse(_restartable({"status": "paused_after_transport_failures"}, temporary, set()))
        for status in ("paused_budget", "paused_output", "completed", "completed_with_failures"):
            self.assertFalse(_restartable({"status": status}, {}, set()))

    def test_invalid_restart_bounds_are_rejected_before_access(self):
        job = self.batch()
        with patch.object(job, "plan", side_effect=AssertionError("No access")):
            for kwargs in ({"max_restarts": 11}, {"max_restarts": True},
                           {"restart_delay_seconds": 0}, {"concurrency": 0}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                    supervise(job, "unused", self.forbidden, **kwargs)


if __name__ == "__main__":
    unittest.main()
