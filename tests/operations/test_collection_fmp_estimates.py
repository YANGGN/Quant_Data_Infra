import json,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from dataclasses import replace
from quant_data.operations.collection_fmp_estimates import run_estimate_pages
from quant_data.operations.collection_plan import ProviderBudget
from quant_data.operations.collection_queue import QueueResponse
from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.stores import quiet_immutable_read_connection
from tests.operations import test_collection_fmp as fixtures
class SelectedEstimateWalkTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SelectedFmpTests("runTest");self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.selection,work=self.f.work("fmp_analyst_estimates");self.unit=work.units[0]
        self.root=Path(self.f.f.temp.name)/"estimate-walk";self.now=datetime(2026,9,9,6,tzinfo=timezone.utc)
        self.elapsed=0;self.calls=[];self.repeat=False;self.expire=False;self.fail=False
    def sleep(self,n):self.elapsed+=n;self.now+=timedelta(seconds=n)
    def fetch(self,*,unit,**kwargs):
        self.calls.append(unit);page=int(dict(unit.parameters)["page"])
        if self.fail and page:return QueueResponse(429,b'{"error":"quota"}',self.now.isoformat(),(("retry-after","60"),))
        rows=[{"symbol":"AAPL","date":str(2027-i)+"-09-30","estimatedRevenueAvg":i+100} for i in range(100)] if page==0 or self.repeat else []
        result=QueueResponse(200,json.dumps(rows).encode(),self.now.isoformat())
        self.sleep(601 if self.expire else 1);return result
    def run_worker(self,cap=2,unit=None):
        return run_estimate_pages(root=self.root,stores=self.f.f.stores,registry=self.f.f.registry,
            selection=self.selection,initial_unit=unit or self.unit,budget=ProviderBudget("fmp",cap,10*1024*1024,600,1000),
            fetch=self.fetch,utcnow=lambda:self.now,monotonic=lambda:self.elapsed,sleeper=self.sleep)
    def test_complete_page_walk_replays_without_get_or_canonical_change(self):
        result=self.run_worker();self.assertEqual(result["outcome"],"short_page_history_unverified")
        self.assertEqual((result["requests"],result["pages"]),(2,2));self.assertFalse(result["whole_history_complete"])
        before=mutation_fingerprint(self.f.f.stores)
        self.assertEqual(self.run_worker()["requests"],0);self.assertEqual(len(self.calls),2)
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_one_request_budget_resumes_without_refetching_first_page(self):
        self.assertEqual(self.run_worker(1)["outcome"],"invocation_budget")
        self.assertEqual(self.run_worker(1)["outcome"],"short_page_history_unverified")
        self.assertEqual([dict(u.parameters)["page"] for u in self.calls],["0","1"])
    def test_nonprogressing_page_is_retained_before_error_and_never_published_or_retried(self):
        self.repeat=True
        with self.assertRaisesRegex(ConflictError,"repeated"):self.run_worker()
        before=mutation_fingerprint(self.f.f.stores)
        with self.assertRaises(ConflictError):self.run_worker()
        self.assertEqual(len(self.calls),2);self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
        with quiet_immutable_read_connection(self.f.f.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_fmp_analyst_captures").fetchone()[0],1)
    def test_bounds_reject_before_get_and_expired_response_stays_private(self):
        with self.assertRaises(ConflictError):self.run_worker(unit=replace(self.unit,max_response_bytes=2*1024*1024))
        self.assertEqual(self.calls,[]);self.expire=True;before=mutation_fingerprint(self.f.f.stores)
        self.assertEqual(self.run_worker()["publication_status"],"deferred_at_run_deadline")
        self.assertEqual(len(self.calls),1);self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_systemic_http_failure_keeps_charged_request_without_retry(self):
        self.fail=True
        self.assertEqual(self.run_worker()["outcome"],"provider_http_failure")
        self.assertEqual(self.run_worker()["outcome"],"provider_http_failure")
        self.assertEqual(len(self.calls),2)


    def assert_deadline_deferred_without_publication(self, result, before):
        self.assertEqual(result["publication_status"], "deferred_at_run_deadline")
        self.assertEqual(result["requests"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(before, mutation_fingerprint(self.f.f.stores))
        self.assertFalse((self.root/"publications"/(self.unit.unit_id+".json")).exists())

    def test_expiry_during_repeated_analyst_validation_prevents_publication(self):
        from unittest.mock import patch
        from quant_data.company import fmp_analyst_history as analyst
        original = analyst.parse_analyst_response
        def delayed(*args, **kwargs):
            parsed = original(*args, **kwargs)
            self.sleep(601)
            return parsed
        before = mutation_fingerprint(self.f.f.stores)
        with patch.object(analyst, "parse_analyst_response", side_effect=delayed):
            self.assert_deadline_deferred_without_publication(self.run_worker(), before)
        # The retained first page resumes without a second GET for that page.
        self.assertEqual(self.run_worker()["outcome"], "short_page_history_unverified")
        self.assertEqual([dict(u.parameters)["page"] for u in self.calls], ["0", "1"])

    def test_expiry_waiting_for_physical_company_lock_prevents_publication(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from quant_data.company import fmp_analyst_history as analyst
        original = analyst.acquire_write_session
        @contextmanager
        def delayed(*args, **kwargs):
            with original(*args, **kwargs) as locks:
                self.sleep(601)
                yield locks
        before = mutation_fingerprint(self.f.f.stores)
        with patch.object(analyst, "acquire_write_session", delayed):
            self.assert_deadline_deferred_without_publication(self.run_worker(), before)
