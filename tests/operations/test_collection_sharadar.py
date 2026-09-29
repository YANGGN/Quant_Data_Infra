from datetime import datetime,timezone,timedelta
from dataclasses import replace
from pathlib import Path
import unittest
from quant_data.operations.collection_sharadar import sf1_units,run_partition,inspect_partition,publish_partition
from quant_data.operations.collection_plan import ProviderBudget
from quant_data.operations.collection_queue import QueueResponse
from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from tests.company import test_sharadar_repository as repository
from tests.company import test_sharadar_sf1 as sf

class SelectedSharadarTests(unittest.TestCase):
    def setUp(self):
        self.f=repository.SharadarRepositoryTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        self.root=Path(self.f.tmp.name)/"private-sharadar"
        self.now=datetime(2026,9,9,5,tzinfo=timezone.utc);self.elapsed=0
        self.schema=sf.metadata()
        self.units=sf1_units(self.f.stores,self.f.selection,schema=self.schema,mode="historical_backfill",
            observation_window="full-history",cutoff=self.now.isoformat())
        self.unit=next(u for u in self.units if dict(u.parameters)["dimension"]=="ARQ")
        self.calls=[];self.bad_cursor=False;self.fail=False
    def clock(self):return self.now
    def sleep(self,n):
        self.elapsed+=n;self.now+=timedelta(seconds=n)
    def fetch(self,*,unit,**kw):
        self.calls.append(unit)
        if unit.endpoint.endswith("/metadata"):body=self.schema.metadata_body
        elif "qopts.cursor_id" not in dict(unit.parameters):
            body=sf.page([sf.row()],cursor="next",parameters=dict(unit.parameters)).body
        else:
            if self.fail:return QueueResponse(429,b'{"error":"quota"}',self.now.isoformat(),(("retry-after","60"),))
            body=sf.page([sf.row(datekey="2026-08-01")],cursor="next" if self.bad_cursor else None,
                parameters=dict(unit.parameters)).body
        result=QueueResponse(200,body,self.now.isoformat())
        self.sleep(1)
        return result
    def run_worker(self,cap=3):
        return run_partition(root=self.root,stores=self.f.stores,registry=self.f.registry,
            selection=self.f.selection,initial_unit=self.unit,budget=ProviderBudget("sharadar",cap,64*1024*1024,600,1000),
            fetch=self.fetch,utcnow=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
    def test_all_six_dimensions_and_complete_cursor_replay_without_get_or_write(self):
        self.assertEqual(len(self.units),6)
        self.assertEqual({dict(u.parameters)["dimension"] for u in self.units},sf.DIMENSIONS)
        result=self.run_worker()
        self.assertEqual((result["outcome"],result["requests"],result["pages"]),("succeeded",3,2))
        self.assertEqual(self.f.counts()["company_sharadar_sf1_versions"],2)
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.run_worker()["requests"],0)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
    def test_one_request_continuations_preserve_partial_pages_and_publish_only_complete(self):
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.run_worker(1)["outcome"],"invocation_budget")
        self.assertEqual(self.run_worker(1)["outcome"],"invocation_budget")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.assertEqual(self.run_worker(1)["outcome"],"succeeded")
        self.assertEqual(len(self.calls),3)
    def test_repeated_cursor_is_retained_but_cannot_publish(self):
        before=mutation_fingerprint(self.f.stores);self.bad_cursor=True
        with self.assertRaisesRegex(ConflictError,"cursor repeats"):self.run_worker()
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.assertEqual(len(self.calls),3)
        with self.assertRaises(ConflictError):self.run_worker()
        self.assertEqual(len(self.calls),3)
    def test_systemic_http_failure_has_no_implicit_retry(self):
        self.fail=True
        self.assertEqual(self.run_worker()["outcome"],"provider_http_failure")
        self.assertEqual(self.run_worker()["outcome"],"page_http_failure")
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.f.counts()["company_sharadar_sf1_versions"],0)
    def test_snapshot_substitution_and_future_cutoff_reject_before_get(self):
        changed=replace(self.unit,selection_sha256="f"*64)
        with self.assertRaises(ConflictError):
            run_partition(root=self.root,stores=self.f.stores,registry=self.f.registry,selection=self.f.selection,
                initial_unit=changed,budget=ProviderBudget("sharadar",1,64*1024*1024,600,1000),fetch=self.fetch,utcnow=self.clock)
        self.assertEqual(self.calls,[])
        self.run_worker()
        with self.assertRaises(ConflictError):
            inspect_partition(root=self.root,stores=self.f.stores,selection=self.f.selection,initial_unit=self.unit,
                cutoff="2026-09-09T04:00:00Z")


    def test_expired_inspection_cannot_publish_retained_complete_pages(self):
        from unittest.mock import patch
        from quant_data.operations import collection_sharadar as implementation
        with patch.object(implementation,"publish_partition",return_value={"outcome":"retained_only"}):
            self.run_worker()
        before=mutation_fingerprint(self.f.stores);original=implementation.inspect_partition
        def slow(**kw):
            result=original(**kw);self.elapsed+=301;return result
        with patch.object(implementation,"inspect_partition",side_effect=slow):
            result=self.run_worker()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(result["requests"],0)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.assertEqual(len(self.calls),3)

    def test_publication_reparse_and_physical_lock_expiry_preserve_private_pages(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from quant_data.operations import collection_sharadar as implementation
        from quant_data.company import sharadar_repository as source
        with patch.object(implementation,"publish_partition",return_value={"outcome":"retained_only"}):
            self.run_worker()
        before=mutation_fingerprint(self.f.stores)
        original_prepare=source.prepare_partition
        def delayed_prepare(*args,**kwargs):
            result=original_prepare(*args,**kwargs);self.elapsed+=601;return result
        with patch.object(source,"prepare_partition",side_effect=delayed_prepare):
            result=self.run_worker()
        self.assertEqual((result["outcome"],result["requests"],result["written_count"]),("invocation_budget",0,0))
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        original_lock=source.acquire_write_session
        @contextmanager
        def delayed_lock(*args,**kwargs):
            with original_lock(*args,**kwargs) as locks:
                self.elapsed+=601
                yield locks
        with patch.object(source,"acquire_write_session",delayed_lock):
            result=self.run_worker()
        self.assertEqual((result["outcome"],result["requests"],result["written_count"]),("invocation_budget",0,0))
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        result=self.run_worker()
        self.assertEqual((result["outcome"],result["requests"]),("succeeded",0))
        self.assertEqual(len(self.calls),3)

    def test_initial_selection_validation_is_inside_total_runtime(self):
        from unittest.mock import patch
        from quant_data.operations import collection_sharadar as source
        original=source._validate_initial
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);self.elapsed+=601;return result
        before=mutation_fingerprint(self.f.stores)
        with patch.object(source,"_validate_initial",side_effect=delayed):
            result=self.run_worker()
        self.assertEqual((result["outcome"],result["requests"]),("invocation_budget",0))
        self.assertEqual(self.calls,[])
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
