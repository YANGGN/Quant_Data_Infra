"""Regression cases from fresh collection-queue review; explicit temporary stores."""
from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from quant_data.errors import ResourceLimitError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations import collection_queue as queue
from quant_data.operations.collection_inventory import inventory_units
from quant_data.operations.collection_plan import RetainedResponse
from tests.operations import test_collection_work as fixtures

class CollectionReviewRegressionTests(unittest.TestCase):
    def queue_fixture(self):
        f=fixtures.CollectionQueueTests("test_exact_replay_causes_no_second_request_or_publication")
        f.setUp();self.addCleanup(f.tearDown);return f

    def test_retained_dispatch_and_post_pacing_respect_invocation_deadline(self):
        f=self.queue_fixture()
        first,second=fixtures.unit(),fixtures.unit("MSFT")
        refs=tuple(RetainedResponse(u.request_id,fixtures.AT,"2"*64,"retained.json",
            True,"partial_history",True,True,u.subject) for u in (first,second))
        p=fixtures.plan((first,second),budgets=(replace(fixtures.budget(),max_run_seconds=1),),retained=refs)
        elapsed=[0];calls=[]
        def publish(**kw):
            calls.append(kw["unit"].subject);elapsed[0]+=2
            return {"outcome":"reused"}
        result=f.run_plan(p,publish=publish,monotonic=lambda:elapsed[0])
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(calls,["AAPL"])
        self.assertEqual(f.calls,[])
        # An actual pacing delay may outlive the earlier budget calculation.
        g=self.queue_fixture();elapsed[0]=0
        p=fixtures.plan((first,second),budgets=(replace(fixtures.budget(),max_run_seconds=2),))
        def delay(_): elapsed[0]+=3
        result=queue.run_queue(root=g.root,plan=p,provider="fmp",fetch=g.fetch,publish=g.publish,
            utcnow=fixtures.clock,monotonic=lambda:elapsed[0],sleeper=delay)
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(len(g.calls),1)

    def test_confirmed_404_is_terminal_for_its_unit_without_poisoning_provider(self):
        f=self.queue_fixture()
        def missing(**kw):
            f.calls.append(kw["unit"].unit_id)
            return queue.QueueResponse(404,b'{"error":"not found"}',fixtures.AT)
        result=f.run_plan(fetch=missing)
        self.assertEqual(result["outcome"],"complete_with_gaps")
        state=json.loads((f.root/"ledger.json").read_text())
        self.assertIsNone(state["pending"])
        self.assertEqual(state["usage"]["2026-09-09"]["charged_attempts"],1)
        self.assertEqual(state["units"][fixtures.unit().unit_id]["http_status"],404)
        self.assertEqual(len(list((f.root/"responses").iterdir())),1)
        result=f.run_plan(fixtures.plan((fixtures.unit("MSFT"),)))
        self.assertEqual(result["outcome"],"complete")
        self.assertEqual(len(f.calls),2)
        result=f.run_plan()
        self.assertEqual(result["requests"],0)
        self.assertEqual(result["outcome"],"complete_with_gaps")

    def test_after_response_retention_crash_accounts_bytes_once_on_original_day(self):
        f=self.queue_fixture()
        original=queue._retain
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            raise SystemExit("simulated power loss after retained response")
        with patch.object(queue,"_retain",crash),self.assertRaises(SystemExit):
            f.run_plan()
        before=json.loads((f.root/"ledger.json").read_text())
        self.assertEqual(before["usage"]["2026-09-09"]["charged_attempts"],1)
        self.assertEqual(before["usage"]["2026-09-09"]["received_bytes"],0)
        result=f.run_plan()
        self.assertEqual((result["requests"],result["published"]),(0,1))
        after=json.loads((f.root/"ledger.json").read_text())
        self.assertEqual(after["usage"]["2026-09-09"]["received_bytes"],2)
        self.assertIsNone(after["pending"])
        f.run_plan()
        final=json.loads((f.root/"ledger.json").read_text())
        self.assertEqual(final["usage"],after["usage"])
        self.assertEqual(len(f.calls),1)

    def test_future_microsecond_transcript_is_excluded_and_total_bound_is_shared(self):
        from tests.company.test_equibles_transcripts import PublisherTests,event,body,page
        f=PublisherTests("test_raw_bytes_nullable_turns_capture_cutoff_replay_and_immutability")
        f.setUp();self.addCleanup(f.tearDown)
        for quarter,micro in ((1,1),(2,2)):
            f.publisher.publish(symbol="A",instrument_id="frozen-A",event=event(quarter=quarter),
                pages=(page(body(quarter=quarter),f"2026-09-09T01:00:00.00000{micro}Z"),))
        u=fixtures.unit("A",key="equibles_transcripts",endpoint="earnings-call-catalogue")
        before=mutation_fingerprint(f.stores)
        result=inventory_units(f.stores,units=(u,),cutoff="2026-09-09T01:00:00.000001Z")
        self.assertEqual(len(result.entries),1)
        self.assertEqual(result.entries[0].endpoint,"call:A-1")
        with patch("quant_data.operations.collection_inventory.MAX_INVENTORY_ROWS",1):
            with self.assertRaises(ResourceLimitError):
                inventory_units(f.stores,units=(u,),cutoff="2026-09-09T01:00:00.000003Z")
        self.assertEqual(before,mutation_fingerprint(f.stores))

    def test_canonical_commit_before_queue_receipt_replays_without_another_get_or_write(self):
        from tests.company.test_fmp_research import FmpResearchTests
        f=FmpResearchTests("test_replay_is_zero_and_annual_q4_same_end_coexist")
        f.setUp();self.addCleanup(f.tearDown)
        prepared=f.prepared([{"symbol":"NST","date":"2025-12-31","calendarYear":"2025","period":"FY","revenue":10}])
        u=replace(fixtures.unit("NST"),parameters=(("limit","3"),("period","annual"),("symbol","NST")))
        p=fixtures.plan((u,));root=Path(f.temp.name)/"queue";gets=[]
        def fetch(**kwargs):
            gets.append(kwargs["unit"].request_id)
            return queue.QueueResponse(200,prepared.raw_body,prepared.captured_at)
        def publish(**kwargs):
            self.assertEqual(kwargs["body"],prepared.raw_body)
            result=f.publisher.publish(prepared,request_id="queue-real-publisher")
            return {"outcome":result.outcome,"written_count":result.written_count}
        original=queue.atomic
        def crash(path,*args,**kwargs):
            if path.parent.name=="publications":
                raise SystemExit("simulated crash after canonical commit")
            return original(path,*args,**kwargs)
        with patch.object(queue,"atomic",crash),self.assertRaises(SystemExit):
            queue.run_queue(root=root,plan=p,provider="fmp",fetch=fetch,publish=publish,
                utcnow=fixtures.clock,sleeper=lambda _:None)
        committed=mutation_fingerprint(f.store_map)
        result=queue.run_queue(root=root,plan=p,provider="fmp",fetch=fetch,publish=publish,
            utcnow=fixtures.clock,sleeper=lambda _:None)
        self.assertEqual(result["requests"],0)
        self.assertEqual(gets,[u.request_id])
        self.assertEqual(committed,mutation_fingerprint(f.store_map))
        receipt=json.loads(next((root/"publications").iterdir()).read_text())
        self.assertEqual(receipt["result"]["outcome"],"unchanged")


    def test_recovered_429_preserves_account_stop_and_retry_after_for_other_plans(self):
        f=self.queue_fixture()
        p=fixtures.plan((fixtures.unit(),fixtures.unit("MSFT")))
        original=queue._retain
        def fetch(**kwargs):
            f.calls.append(kwargs["unit"].unit_id)
            return queue.QueueResponse(429,b'{"error":"rate limit"}',fixtures.AT,(("retry-after","3600"),))
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            raise SystemExit("simulated power loss before HTTP failure settlement")
        with patch.object(queue,"_retain",crash),self.assertRaises(SystemExit):
            f.run_plan(p,fetch=fetch)
        result=f.run_plan(p)
        self.assertEqual((result["outcome"],result["requests"]),("provider_http_failure",0))
        self.assertEqual(len(f.calls),1)
        state=json.loads((f.root/"ledger.json").read_text())
        self.assertIsNone(state["pending"])
        self.assertEqual(state["usage"]["2026-09-09"]["charged_attempts"],1)
        self.assertEqual(state["usage"]["2026-09-09"]["received_bytes"],len(b'{"error":"rate limit"}'))
        self.assertEqual(state["provider_backoff"]["not_before"],"2026-09-09T02:00:00.000000Z")
        other=f.run_plan(fixtures.plan((fixtures.unit("MSFT"),)))
        self.assertEqual((other["outcome"],other["requests"]),("provider_http_failure",0))
        self.assertEqual(len(f.calls),1)


    def test_retry_after_never_shortens_a_long_valid_provider_wait(self):
        receipt={"captured_at":"2026-09-09T01:00:00.000000Z","headers":{"retry-after":"864000"}}
        self.assertEqual(queue._retry_at(receipt),"2026-09-19T01:00:00.000000Z")
        receipt["headers"]["retry-after"]="9"*100
        with self.assertRaises(ResourceLimitError): queue._retry_at(receipt)
