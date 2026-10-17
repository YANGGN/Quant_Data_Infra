"""Offline retry, evidence, and concurrent-budget checks; no live models."""
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from quant_data.company.transcript_analysis_codex import CodexTranscriptTransport
from quant_data.company.transcript_analysis_model import make_request
from quant_data.company.transcript_analysis_retry import (
    AttemptBudget, AttemptBudgetExhausted, RetryingCodexTransport, transient_failure,
)
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.operations.equibles_transcript_backfill import atomic
from tests.company.test_transcript_analysis_codex import events


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = make_request({"fixture": "retry"})
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.calls, self.sleeps = [], []
        self.budget = AttemptBudget(self.root/"budget.json", plan_id="fixture", limit=5, initial_calls=0)
        self.base = CodexTranscriptTransport(evidence_root=self.root/"native", environment={})
        self.base._ready = True
        self.wrapper = RetryingCodexTransport(self.base, self.budget, sleep=self.sleeps.append, jitter=lambda: 0)

    def run_attempts(self, outcomes):
        iterator = iter(outcomes)
        def run(transport, command, directory, prompt, run_root):
            outcome = next(iterator)
            self.calls.append(run_root)
            if outcome == "ok":
                raw = events()
                atomic(run_root/"stdout.jsonl", raw)
                atomic(run_root/"finished.json", {"exit_code": 0, "failure": None})
                return raw, b""
            failure = "timeout" if outcome == "timeout" else None
            raw = b"" if failure else (json.dumps({"type": "error", "message": outcome})+"\n").encode()
            atomic(run_root/"stdout.jsonl", raw)
            atomic(run_root/"finished.json", {"exit_code": 1, "failure": failure})
            raise ResourceLimitError("timeout") if failure else ValidationError("native failure")
        return patch.object(CodexTranscriptTransport, "_run", run)

    def test_capacity_then_success_retains_both_attempts(self):
        with self.run_attempts(["Selected model is at capacity.", "ok"]):
            result = self.wrapper.request(self.raw)
        self.assertEqual(json.loads(result)["status"], "completed")
        self.assertEqual((self.wrapper.attempt_count, self.budget.state["used"], self.sleeps), (2, 2, [30]))
        original = self.root/"native"/self.sha
        self.assertIn("at capacity", (original/"stdout.jsonl").read_text())
        self.assertEqual(self.calls[1], original/"retries"/"attempt-2"/self.sha)
        self.assertTrue((self.calls[1]/"started.json").is_file())

    def test_two_retries_have_bounded_backoff_and_three_attempt_limit(self):
        with self.run_attempts(["429 Too Many Requests", "stream disconnected before completion", "ok"]):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.sleeps, self.budget.state["retries"]), (3, [30, 120], 2))

    def test_exhausted_transient_failure_is_terminal_after_three(self):
        with self.run_attempts(["Selected model is at capacity."]*3), self.assertRaises(ValidationError):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.wrapper.attempt_count, self.sleeps), (3, 3, [30, 120]))
        self.assertTrue(all((p/"finished.json").exists() for p in self.calls))

    def test_global_budget_blocks_retry_before_another_native_call(self):
        self.budget.state["limit"] = 1
        self.budget._save()
        with self.run_attempts(["Selected model is at capacity."]), self.assertRaises(AttemptBudgetExhausted):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.wrapper.attempt_count, self.budget.state["used"]), (1, 1, 1))

    def test_authentication_failure_is_not_retried(self):
        with self.run_attempts(["Authentication failed"]), self.assertRaises(ValidationError):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.sleeps), (1, []))

    def test_timeout_can_retry_after_finished_receipt(self):
        with self.run_attempts(["timeout", "ok"]):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.sleeps), (2, [30]))

    def test_prior_request_cannot_be_reentered_or_recharged(self):
        with self.run_attempts(["ok"]):
            self.wrapper.request(self.raw)
        before = self.budget.path.read_bytes()
        with self.assertRaises(ConflictError):
            self.wrapper.request(self.raw)
        self.assertEqual(self.budget.path.read_bytes(), before)

    def test_concurrent_reservations_never_exceed_durable_cap(self):
        def reserve(_):
            try:
                self.budget.reserve(retry=False)
                return 1
            except AttemptBudgetExhausted:
                return 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(sum(pool.map(reserve, range(40))), 5)
        retained = AttemptBudget(self.budget.path, plan_id="fixture", limit=5, initial_calls=0)
        self.assertEqual(retained.state["used"], 5)
        with self.assertRaises(AttemptBudgetExhausted):
            retained.reserve(retry=True)

    def test_interrupted_reservation_is_not_refunded(self):
        self.budget.reserve(retry=False)
        retained = AttemptBudget(self.budget.path, plan_id="fixture", limit=5, initial_calls=0)
        self.assertEqual(retained.state["used"], 1)
        with self.assertRaises(ConflictError):
            AttemptBudget(self.budget.path, plan_id="different", limit=5, initial_calls=0)

    def test_unknown_malformed_completed_and_tool_events_are_not_retryable(self):
        root = self.root/"attempt";root.mkdir()
        atomic(root/"finished.json", {"exit_code": 1, "failure": None})
        for raw in [b"broken", b"{}", b'{"type":1}', b'{"type":"error","message":"unknown"}',
                    events()+b'{"type":"error","message":"429"}\n',
                    b'{"type":"item.completed","item":{"type":"tool_call"}}\n{"type":"error","message":"429"}\n']:
            with self.subTest(raw=raw):
                atomic(root/"stdout.jsonl", raw, replace=True)
                self.assertFalse(transient_failure(root))

    def test_timeout_with_permanent_auth_error_is_not_retryable(self):
        root=self.root/"attempt";root.mkdir()
        atomic(root/"finished.json", {"exit_code": 1, "failure": "timeout"})
        atomic(root/"stdout.jsonl", b'{"type":"error","message":"authentication failed"}\n')
        self.assertFalse(transient_failure(root))

    def test_native_request_timeout_message_retries_then_succeeds(self):
        with self.run_attempts(["request timed out", "ok"]):
            result = self.wrapper.request(self.raw)
        self.assertEqual(json.loads(result)["status"], "completed")
        self.assertEqual((len(self.calls), self.sleeps, self.budget.state["used"]), (2, [30], 2))

    def test_exhausted_request_timeouts_remain_eligible_for_delayed_continuation(self):
        with self.run_attempts(["request timed out"] * 3), self.assertRaises(ValidationError):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.sleeps), (3, [30, 120]))
        self.assertTrue(self.wrapper.retryable_failure)

    def test_request_timeout_with_auth_error_stays_terminal(self):
        with self.run_attempts(["request timed out: authentication failed"]), self.assertRaises(ValidationError):
            self.wrapper.request(self.raw)
        self.assertEqual((len(self.calls), self.sleeps), (1, []))
        self.assertFalse(self.wrapper.retryable_failure)
