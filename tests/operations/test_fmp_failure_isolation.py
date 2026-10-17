"""FMP failures remain charged gaps while independent finite work proceeds."""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant_data.errors import ConflictError, ValidationError
from quant_data.operations import collection_provider_policy as policy
from quant_data.operations import collection_queue as queue
from tests.operations import test_collection_provider_policy as gf
from tests.operations import test_collection_work as qf


class GateIsolationTests(unittest.TestCase):
    def setUp(self):
        self.f = gf.FmpSharedAllowanceTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def invoke(self, operation):
        return self.f.gate().invoke(operation, request_identity="e"*64,
                                   priority="maintenance", deadline=60)

    def interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(Mock(side_effect=KeyboardInterrupt()))
        return (self.f.root/"allowance.json").read_bytes()

    def test_interrupted_owner_is_flagged_under_lock_before_next_request(self):
        before = self.interrupt()
        old_attempt = (self.f.root/"attempts/2026-09-09-2.json").read_bytes()
        self.f.invoke("maintenance")
        receipt = json.loads((self.f.root/"uncertain/2026-09-09-2.json").read_bytes())
        self.assertEqual(receipt["before_text"].encode(), before)
        self.assertEqual(receipt["before_sha256"], hashlib.sha256(before).hexdigest())
        self.assertFalse(receipt["automatic_retry"])
        self.assertEqual(receipt["refunded_charges"], 0)
        self.assertEqual(self.f.state()["usage"]["2026-09-09"], 3)
        self.assertEqual((self.f.root/"attempts/2026-09-09-2.json").read_bytes(), old_attempt)
        self.assertEqual(len(self.f.calls), 1)

    def test_invalid_response_is_flagged_without_leaking_exception_or_blocking(self):
        with self.assertRaises(policy.FmpRequestFailed):
            self.invoke(lambda _: SimpleNamespace(status=None))
        self.f.invoke("maintenance")
        self.assertEqual(len(self.f.calls), 1)
        self.assertIsNone(self.f.state()["pending"])

    def test_http_500_is_request_local_but_auth_and_rate_limits_still_stop(self):
        self.f.invoke("maintenance", 500)
        self.f.invoke("maintenance")
        self.assertIsNone(self.f.state()["stopped"])
        for status in (401, 403, 429):
            with self.subTest(status=status):
                f = gf.FmpSharedAllowanceTests("runTest")
                f.setUp()
                try:
                    f.invoke("maintenance", status)
                    with self.assertRaises(ConflictError): f.invoke("maintenance")
                    self.assertEqual(len(f.calls), 1)
                finally: f.doCleanups()

    def test_receipt_failure_does_not_silently_clear_pending_or_dispatch_next(self):
        before = self.interrupt()
        original = policy.atomic
        def fail(path, body, **kw):
            if path.parent.name == "uncertain": raise OSError("disk")
            return original(path, body, **kw)
        with patch.object(policy, "atomic", side_effect=fail):
            with self.assertRaises(OSError): self.f.invoke("maintenance")
        self.assertEqual((self.f.root/"allowance.json").read_bytes(), before)
        self.assertEqual(self.f.calls, [])

    def test_crash_after_receipt_before_clear_resumes_idempotently(self):
        before = self.interrupt()
        original = policy.atomic
        def fail(path, body, **kw):
            if path.name == "allowance.json": raise OSError("disk")
            return original(path, body, **kw)
        with patch.object(policy, "atomic", side_effect=fail):
            with self.assertRaises(OSError): self.f.invoke("maintenance")
        receipt = (self.f.root/"uncertain/2026-09-09-2.json").read_bytes()
        self.assertEqual((self.f.root/"allowance.json").read_bytes(), before)
        self.f.invoke("maintenance")
        self.assertEqual((self.f.root/"uncertain/2026-09-09-2.json").read_bytes(), receipt)

    def test_explicit_reconciliation_checks_hash_and_attempt_without_network(self):
        before = self.interrupt()
        digest = hashlib.sha256(before).hexdigest()
        for attempt, sha in (("other", digest), ("2026-09-09-2", "0"*64)):
            with self.assertRaises(ConflictError):
                self.f.gate().reconcile_pending(expected_attempt=attempt, expected_state_sha256=sha)
        result = self.f.gate().reconcile_pending(expected_attempt="2026-09-09-2",
                                               expected_state_sha256=digest)
        self.assertEqual(result["provider_requests"], 0)
        expected = json.loads(before);expected["pending"] = None
        self.assertEqual(self.f.state(), expected)
        self.assertEqual(self.f.calls, [])

    def test_tampered_companion_or_charge_blocks_recovery(self):
        self.interrupt()
        original = self.f.state()
        for delta in ({"usage":{"2026-09-09":4}},
                      {"pending":{**original["pending"],"request_identity":"f"*64}},
                      {"stopped":{"status":429}}):
            state = {**original, **delta}
            policy.atomic(self.f.root/"allowance.json", state, replace=True)
            with self.assertRaises(ConflictError): self.f.invoke("maintenance")
        self.assertEqual(self.f.calls, [])

    def test_missing_companion_after_ledger_save_is_preserved_in_failure_receipt(self):
        self.interrupt()
        (self.f.root/"attempts/2026-09-09-2.json").unlink()
        self.f.invoke("maintenance")
        self.assertTrue((self.f.root/"uncertain/2026-09-09-2.json").exists())
        self.assertEqual(self.f.state()["usage"]["2026-09-09"], 3)


class QueueIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/"queue"
        self.units = (qf.unit("AAPL"), qf.unit("MSFT"))
        self.plan = qf.plan(self.units)
        self.published = []
        self.calls = []

    def response(self):
        return queue.QueueResponse(200, b"[]", qf.AT)

    def publish(self, **kw):
        self.published.append(kw["unit"].subject)
        return {"outcome":"succeeded"}

    def run_plan(self, fetch, plan=None):
        return queue.run_queue(root=self.root, plan=plan or self.plan, provider="fmp",
                               fetch=fetch, publish=self.publish, utcnow=qf.clock,
                               sleeper=lambda _:None)

    def state(self):
        return json.loads((self.root/"ledger.json").read_bytes())

    def test_first_timeout_continues_and_replay_never_repeats_failed_unit(self):
        def fetch(**kw):
            self.calls.append(kw["unit"].subject)
            if kw["unit"].subject == "AAPL": raise TimeoutError("apikey=must-not-leak")
            return self.response()
        result = self.run_plan(fetch)
        self.assertEqual(self.calls, ["AAPL", "MSFT"])
        self.assertEqual(self.published, ["MSFT"])
        self.assertEqual(result["outcome"], "complete_with_gaps")
        self.assertEqual(result["uncertain_bytes"], 1024)
        self.assertEqual(self.state()["usage"]["2026-09-09"]["charged_attempts"], 2)
        self.assertIsNone(self.state()["pending"])
        for p in (self.root/"failures").glob("*.json"):
            self.assertNotIn("must-not-leak", p.read_text())
        repeat = Mock(side_effect=AssertionError("No retry"))
        replay = self.run_plan(repeat)
        repeat.assert_not_called()
        self.assertEqual((replay["requests"], replay["reused"]), (0, 1))
        self.assertEqual(replay["failed_units"][0]["error"], "fmp_request_failed")

    def test_interrupted_request_recovers_and_skips_same_unit(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_plan(Mock(side_effect=KeyboardInterrupt()))
        result = self.run_plan(lambda **kw:self.calls.append(kw["unit"].subject) or self.response())
        self.assertEqual(self.calls, ["MSFT"])
        self.assertEqual(result["outcome"], "complete_with_gaps")
        self.assertEqual(self.state()["usage"]["2026-09-09"]["charged_attempts"], 2)

    def test_failure_receipt_precedes_clear_and_write_failure_blocks_next_call(self):
        original = queue.atomic
        def fail(path, body, **kw):
            if path.parent.name == "failures": raise OSError("disk")
            return original(path, body, **kw)
        fetch = Mock(side_effect=TimeoutError())
        with patch.object(queue, "atomic", side_effect=fail):
            with self.assertRaises(OSError): self.run_plan(fetch)
        fetch.assert_called_once()
        self.assertIsNotNone(self.state()["pending"])
        result = self.run_plan(lambda **kw:self.response())
        self.assertEqual(result["requests"], 1)
        self.assertEqual(self.published, ["MSFT"])

    def test_uncertain_bytes_cannot_extend_the_invocation_byte_budget(self):
        p = qf.plan(self.units, budgets=(replace(qf.budget(),max_total_bytes=1024),))
        fetch = Mock(side_effect=TimeoutError())
        result = self.run_plan(fetch, p)
        self.assertEqual(result["outcome"], "invocation_budget")
        fetch.assert_called_once()
        self.assertEqual(self.state()["usage"]["2026-09-09"]["uncertain_bytes"], 1024)

    def test_unusable_response_and_500_are_isolated(self):
        for response in (queue.QueueResponse(200,b"",qf.AT),
                         queue.QueueResponse(500,b"{}",qf.AT)):
            with self.subTest(status=response.status):
                with tempfile.TemporaryDirectory(dir="/tmp") as folder:
                    fetch=Mock(side_effect=[response, self.response()])
                    result=queue.run_queue(root=Path(folder)/"queue",plan=self.plan,provider="fmp",
                                          fetch=fetch,publish=self.publish,utcnow=qf.clock,sleeper=lambda _:None)
                    self.assertEqual((result["requests"],result["published"]), (2,1))
                    self.assertEqual(result["outcome"],"complete_with_gaps")

    def test_retention_io_and_publication_failures_still_stop(self):
        fetch = Mock(return_value=self.response())
        with patch.object(queue,"_retain",side_effect=OSError("disk")):
            with self.assertRaises(OSError):self.run_plan(fetch)
        fetch.assert_called_once()
        self.assertIsNotNone(self.state()["pending"])

    def test_non_fmp_transport_uncertainty_still_preserves_existing_contract(self):
        u=qf.unit(key="sec_filings_companyfacts",endpoint="companyfacts")
        p=qf.plan((u,),selections=(qf.selection("sec_filings_companyfacts"),),budgets=(qf.budget("sec"),))
        fetch=Mock(side_effect=TimeoutError())
        args=dict(root=self.root,plan=p,provider="sec",fetch=fetch,publish=self.publish,utcnow=qf.clock)
        with self.assertRaises(ConflictError):queue.run_queue(**args)
        with self.assertRaises(ConflictError):queue.run_queue(**args)
        fetch.assert_called_once()

class ParallelIsolationTests(unittest.TestCase):
    def setUp(self):
        from tests.operations.test_collection_parallel_prices import ParallelPriceTests
        self.f = ParallelPriceTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_dead_parallel_owner_does_not_block_serial_client(self):
        from quant_data.operations.collection_parallel_prices import acquire_batch
        f = self.f
        class Ticket:
            def __init__(self, *args): pass
            def ready(self): return True
            def result(self): raise KeyboardInterrupt()
            def close(self): pass
        with self.assertRaises(KeyboardInterrupt):
            acquire_batch(root=f.root/"batch",gate=f.gate,units=f.units(2),
                          credential="synthetic",deadline=100,max_total_bytes=100000,
                          ticket_factory=Ticket,clock=f.clock,monotonic=lambda:f.elapsed,sleeper=f.sleep)
        before = (f.gate.root/"allowance.json").read_bytes()
        f.gate.invoke(lambda _:SimpleNamespace(status=200),request_identity="f"*64,
                      priority="maintenance",deadline=100)
        receipts=list((f.gate.root/"uncertain").glob("parallel-*.json"))
        self.assertEqual(len(receipts),1)
        self.assertEqual(json.loads(receipts[0].read_bytes())["before_text"].encode(),before)
        self.assertIsNone(f.state()["pending"])
        self.assertEqual(f.state()["usage"]["2026-09-10"],7)

    def test_worker_failure_then_success_continue_without_account_stop(self):
        from quant_data.operations.collection_parallel_prices import acquire_batch
        f = self.f
        seen = []
        class Ticket:
            def __init__(self,u,*args): self.u=u;seen.append(u.subject)
            def ready(self): return True
            def result(self):
                if self.u.subject=="T000":raise OSError("credential-secret")
                return queue.QueueResponse(200,b"[]",f.clock().isoformat())
            def close(self): pass
        result=acquire_batch(root=f.root/"batch",gate=f.gate,units=f.units(3),
                             credential="synthetic",deadline=100,max_total_bytes=100000,
                             ticket_factory=Ticket,clock=f.clock,monotonic=lambda:f.elapsed,sleeper=f.sleep)
        self.assertEqual(seen,["T000","T001","T002"])
        self.assertEqual((result["retained_responses"],result["failed_requests"]),(2,1))
        self.assertEqual(result["outcome"],"complete_with_gaps")
        self.assertIsNone(f.state()["pending"]);self.assertIsNone(f.state()["stopped"])
        receipt=json.loads(next((f.root/"batch").glob("*.failure.json")).read_bytes())
        self.assertFalse(receipt["retryable"])
        self.assertNotIn("credential-secret",str(receipt))

    def test_active_account_lock_never_retires_another_owners_request(self):
        from quant_data.operations.equibles_transcript_backfill import job_lock
        f = self.f
        before=(f.gate.root/"allowance.json").read_bytes()
        with job_lock(f.gate.root):
            with self.assertRaises(ConflictError):
                f.gate.invoke(Mock(),request_identity="f"*64,priority="maintenance",deadline=100)
        self.assertEqual((f.gate.root/"allowance.json").read_bytes(),before)

class NestedGateSafetyTests(unittest.TestCase):
    def setUp(self):
        self.f=gf.FmpSharedAllowanceTests("runTest");self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_gate_receipt_io_failure_is_not_isolated_by_queue(self):
        f=self.f
        callback=Mock(side_effect=TimeoutError("secret"))
        def fetch(**kw):
            return f.gate().invoke(callback,request_identity="f"*64,priority="maintenance",deadline=60)
        original=policy.atomic
        def fail(path, body, **kw):
            if path.parent.name=="uncertain":raise OSError("disk full")
            return original(path,body,**kw)
        root=Path(f.temp.name)/"queue"
        with patch.object(policy,"atomic",side_effect=fail):
            with self.assertRaises(policy.FmpGateFailure):
                queue.run_queue(root=root,plan=qf.plan((qf.unit(),qf.unit("MSFT"))),
                                provider="fmp",fetch=fetch,publish=lambda **kw:{"outcome":"succeeded"},
                                utcnow=qf.clock,sleeper=lambda _:None)
        callback.assert_called_once()
        self.assertIsNotNone(f.state()["pending"])
        self.assertIsNotNone(json.loads((root/"ledger.json").read_bytes())["pending"])

    def test_unusable_auth_rate_responses_preserve_real_transport_status(self):
        import time
        from quant_data.operations.collection_transport import SelectedProviderFetch
        from tests.operations.test_collection_transport import FakeConnection, FakeResponse
        for status in (401,403,429):
            with self.subTest(status=status),tempfile.TemporaryDirectory(dir="/tmp") as folder:
                gate=policy.FmpAccountGate(Path(folder)/"account",self.f.policy,
                                          clock=lambda:gf.AT,monotonic=time.monotonic)
                with patch.object(policy,"host_allowance",return_value=gate), \
                     patch("quant_data.operations.collection_transport.http.client.HTTPSConnection",FakeConnection), \
                     patch.object(FakeResponse,"status",status), \
                     patch.object(FakeResponse,"getheader",return_value="invalid"):
                    result=queue.run_queue(root=Path(folder)/"queue",
                            plan=qf.plan((replace(qf.unit(),timeout_seconds=5),replace(qf.unit("MSFT"),timeout_seconds=5))),provider="fmp",
                            fetch=SelectedProviderFetch("fmp","synthetic"),
                            publish=lambda **kw:self.fail("No publication"),
                            utcnow=qf.clock,sleeper=lambda _:None)
                state=json.loads((gate.root/"allowance.json").read_bytes())
                self.assertEqual(result["outcome"],"provider_http_failure")
                self.assertEqual(result["requests"],1)
                self.assertEqual(state["stopped"]["status"],status)
                self.assertIsNone(state["pending"])
