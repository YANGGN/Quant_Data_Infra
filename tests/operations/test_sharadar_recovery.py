"""Offline regression checks for Sharadar's bounded timed recovery."""
from datetime import timedelta
from unittest.mock import patch
import unittest

from quant_data.errors import ConflictError
from quant_data.operations.collection_queue import QueueResponse, _read
from tests.operations.test_sharadar_selected_refresh import SelectedSharadarRefreshTests as Fixture


class SharadarRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.baseline()
        self.network = patch("socket.socket.connect", side_effect=AssertionError("offline"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.original = self.f.fetch
        self.failures = 0
        self.status = 500
        self.dispatches = []
        self.receipts = {}

    def fetch(self, *, unit, **kwargs):
        if dict(unit.parameters).get("dimension") == "MRQ" and self.failures:
            self.failures -= 1
            self.dispatches.append((self.f.now, self.f.elapsed, unit.unit_id))
            self.f.calls.append(unit)
            response = QueueResponse(self.status, b'{"error":"Internal Server Error"}', self.f.now.isoformat())
            self.f.sleep(1)
            return response
        if dict(unit.parameters).get("dimension") == "MRQ":
            self.dispatches.append((self.f.now, self.f.elapsed, unit.unit_id))
        return self.original(unit=unit, **kwargs)

    def assert_publication_responses_succeeded(self):
        publications=list((self.f.root/"direct-sf1-publications").glob("*.json"))
        self.assertEqual(len(publications),6)
        for path in publications:
            publication=_read(path)
            for unit_id in [*publication["unit_ids"],publication["metadata_unit_id"]]:
                response=_read(self.f.root/"responses"/(unit_id+".json"))
                self.assertEqual(response["status"],200,(path,unit_id))

    def test_pause_before_pass_cannot_extend_supervisor_deadline(self):
        from quant_data.operations import sharadar_selected_refresh as refresh
        original=refresh._run_refresh_pass
        def paused(**kwargs):
            self.f.sleep(3601)
            return original(**kwargs)
        with patch.object(refresh,"_run_refresh_pass",side_effect=paused):
            result=self.f.run_worker()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(result["requests"],0)
        self.assertEqual(self.f.calls,[])

    def test_two_delays_then_success_preserves_evidence_and_skips_completed(self):
        self.failures = 2
        self.f.fetch = self.fetch
        result = self.f.run_worker()
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["requests"], 9)
        self.assertEqual(_read(self.f.root/"refresh-completions"/"2026-09-10.json")["requests"],9)
        times = [item[0] for item in self.dispatches]
        self.assertGreaterEqual((times[1] - times[0]).total_seconds(), 600)
        self.assertGreaterEqual((times[2] - times[0]).total_seconds(), 1800)
        self.assertLess((times[2] - times[0]).total_seconds(), 1810)
        self.assertEqual(len(set(item[2] for item in self.dispatches)), 3)
        receipts = [_read(self.f.root / "responses" / (item[2] + ".json")) for item in self.dispatches]
        self.assertEqual([r["status"] for r in receipts], [500, 500, 200])
        self.assertEqual(len([u for u in self.f.calls if dict(u.parameters).get("dimension") == "ARQ"]), 1)
        self.assertIsNone(_read(self.f.root / "refresh-state.json")["pending"])
        self.assertEqual(result["details"]["retries_today"], 2)
        self.assert_publication_responses_succeeded()

    def test_exhaustion_survives_restart_then_next_day_recovers_fixed_window(self):
        self.failures = 100
        self.f.fetch = self.fetch
        result = self.f.run_worker()
        self.assertEqual(result["outcome"], "retry_next_day")
        count = len(self.f.calls)
        old_responses = {p.name:p.read_bytes() for p in (self.f.root/"responses").glob("*.json")}
        self.f.elapsed = 0
        self.assertEqual(self.f.run_worker()["outcome"], "retry_next_day")
        self.assertEqual(len(self.f.calls), count)
        self.f.now += timedelta(days=1)
        self.f.elapsed = 0
        self.failures = 0
        result = self.f.run_worker()
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["requests"], 3)  # MRQ/MRT/MRY, no repeated successes.
        self.assertTrue(all((self.f.root/"responses"/name).read_bytes() == body for name,body in old_responses.items()))
        self.assertEqual(_read(self.f.root/"refresh-state.json")["last_complete_date"], "2026-09-10")

    def check_non_server(self, status):
        self.status=status; self.failures=100; self.f.fetch=self.fetch
        result=self.f.run_worker()
        self.assertNotIn(result["outcome"],("succeeded","retry_next_day"))
        self.assertEqual(len(self.dispatches),1)
        self.assertEqual(result["details"]["http_status"],status)
        self.f.run_worker()
        self.assertEqual(len(self.dispatches),1)
        self.assertEqual(result["details"]["retries_today"],0)

    def test_unauthorized_is_not_retried(self):
        self.check_non_server(401)

    def test_forbidden_is_not_retried(self):
        self.check_non_server(403)

    def test_rate_limit_is_not_retried(self):
        self.check_non_server(429)

    def test_invocation_request_budget_includes_failure_and_no_extra_retry(self):
        self.failures = 100
        self.f.fetch = self.fetch
        result = self.f.run_worker(cap=5)
        self.assertEqual((result["outcome"],result["requests"]), ("invocation_budget",5))
        self.assertEqual(len(self.dispatches),1)

    def test_server_retry_after_is_respected_without_extending_deadline(self):
        def fetch(*,unit,**kwargs):
            if unit.endpoint.endswith("/fundamentals"):
                self.f.calls.append(unit)
                return QueueResponse(503,b'{}',self.f.now.isoformat(),(("retry-after","7200"),))
            return self.original(unit=unit,**kwargs)
        self.f.fetch=fetch
        result=self.f.run_worker()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(len(self.f.calls),2)
        self.assertLess(self.f.elapsed,3600)

    def test_short_deadline_does_not_wait_or_extend(self):
        from dataclasses import replace
        from quant_data.operations.sharadar_selected_refresh import run_refresh, BUDGET
        self.failures = 100
        self.f.fetch = self.fetch
        result=run_refresh(root=self.f.root,stores=self.f.fixture.stores,registry=self.f.fixture.registry,
            selection=self.f.fixture.selection,fetch=self.fetch,budget=replace(BUDGET,max_run_seconds=300),
            utcnow=lambda:self.f.now,monotonic=lambda:self.f.elapsed,sleeper=self.f.sleep)
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertLess(self.f.elapsed,300)
        self.assertEqual(len(self.dispatches),1)

    def test_retry_descriptions_before_any_partition(self):
        attempts=[]
        def fetch(*,unit,**kwargs):
            if unit.endpoint.endswith("/descriptions"):
                attempts.append(unit.unit_id)
                if len(attempts)==1:
                    self.f.calls.append(unit)
                    return QueueResponse(503,b'{}',self.f.now.isoformat())
            return self.original(unit=unit,**kwargs)
        self.f.fetch=fetch
        result=self.f.run_worker()
        self.assertEqual((result["outcome"],result["requests"]),("succeeded",8))
        self.assertEqual(len(attempts),2)
        self.assert_publication_responses_succeeded()
        self.assertEqual(_read(self.f.root/"responses"/(attempts[0]+".json"))["status"],503)

    def test_uncertain_transport_stays_reserved_and_is_not_retried(self):
        def fail(**kwargs):
            self.f.calls.append(kwargs["unit"])
            raise OSError("connection lost")
        self.f.fetch=fail
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        self.assertEqual(len(self.f.calls),1)
        self.assertIsNotNone(_read(self.f.root/"ledger.json")["pending"])

    def test_daily_ceiling_includes_retry_requests(self):
        from quant_data.operations.collection_queue import atomic
        self.failures=100; self.f.fetch=self.fetch
        self.assertEqual(self.f.run_worker(cap=5)["outcome"],"invocation_budget")
        ledger=_read(self.f.root/"ledger.json")
        ledger["usage"][self.f.now.date().isoformat()]["charged_attempts"]=1000
        atomic(self.f.root/"ledger.json",ledger,replace=True)
        prior=len(self.f.calls)
        result=self.f.run_worker()
        self.assertEqual(result["outcome"],"daily_budget")
        self.assertEqual(len(self.f.calls),prior)

    def test_reserved_retry_survives_restart_before_network_dispatch(self):
        from quant_data.operations.sharadar_recovery import SharadarRecovery
        self.failures=1; self.f.fetch=self.fetch
        self.f.run_worker(cap=5)
        original=self.f.calls[-1]
        recovery=SharadarRecovery(self.f.root,utcnow=lambda:self.f.now,
            monotonic=lambda:self.f.elapsed,sleeper=self.f.sleep,deadline=self.f.elapsed+3600)
        failed=recovery.response(original)[0]
        before=(self.f.root/"responses"/(original.unit_id+".json")).read_bytes()
        self.assertEqual(recovery.retry(),"retry")
        self.assertIsNone(recovery.response(original))
        self.assertEqual(self.f.run_worker()["outcome"],"succeeded")
        self.assertEqual((self.f.root/"responses"/(original.unit_id+".json")).read_bytes(),before)
        self.assertEqual(len(self.dispatches),2)

    def test_interrupted_retry_after_charge_does_not_dispatch_again(self):
        self.failures=1; self.f.fetch=self.fetch
        self.f.run_worker(cap=5)
        calls=[]
        def fail(**kwargs):
            calls.append(kwargs["unit"])
            raise OSError("lost after dispatch")
        self.f.fetch=fail
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        self.assertEqual(len(calls),1)

    def test_new_day_has_initial_attempt_and_only_two_timed_retries(self):
        self.failures=100; self.f.fetch=self.fetch
        self.assertEqual(self.f.run_worker()["outcome"],"retry_next_day")
        self.f.now+=timedelta(days=1); self.f.elapsed=0
        previous=len(self.dispatches)
        result=self.f.run_worker()
        self.assertEqual(result["outcome"],"retry_next_day")
        self.assertEqual(len(self.dispatches)-previous,3)
        self.assertEqual(result["requests"],3)
        self.assertEqual(result["details"]["retries_today"],2)

    def test_retry_selection_is_scoped_to_failed_page(self):
        # An offset page has a distinct retry selection; success is never invalidated.
        from dataclasses import replace
        from types import SimpleNamespace
        from quant_data.operations import collection_sharadar_direct as direct
        from quant_data.operations.sharadar_recovery import SharadarRecovery
        self.failures=1; self.f.fetch=self.fetch
        self.f.run_worker(cap=5)
        original=self.f.calls[-1]
        next_page=replace(original,parameters=tuple(sorted({**dict(original.parameters),"offset":"10000"}.items())))
        recovery=SharadarRecovery(self.f.root,utcnow=lambda:self.f.now,
            monotonic=lambda:self.f.elapsed,sleeper=self.f.sleep,deadline=self.f.elapsed+3600)
        recovery.response(original); recovery.retry()
        self.assertNotEqual(recovery.unit(original).unit_id,original.unit_id)
        self.assertEqual(recovery.unit(next_page),next_page)


if __name__ == "__main__":
    unittest.main()
