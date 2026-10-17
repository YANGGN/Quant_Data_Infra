import json
import unittest
from dataclasses import replace
from datetime import timedelta
from quant_data.errors import ConflictError
from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations.equibles_catalogue import begin_catalogue_epoch, continuation_decision
from tests.operations import test_equibles_paid_policy as fixtures
from tests.operations.test_equibles_paid_policy import PaidTransport, selection, AT
from tests.company.test_equibles_transcripts import FakePublisher, catalog, body

class EquiblesCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EquiblesPaidPolicyTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.convert();self.root=self.f.root;self.publisher=FakePublisher();self.clock=AT
    def run_job(self, values):
        transport=PaidTransport(values,clock=lambda:self.clock)
        result=job.run(self.root,self.publisher,transport,clock=lambda:self.clock,
            sleeper=lambda _:None,allow_paid=True)
        return result,transport
    def complete(self):
        report,transport=self.run_job([catalog("A"),body("A"),catalog("B"),body("B")])
        self.assertEqual(report["outcome"],"complete")
    def begin(self):
        self.clock+=timedelta(seconds=2)
        return begin_catalogue_epoch(self.root,started_at=self.clock.isoformat())
    def test_catalogue_epoch_only_fetches_new_calls_and_preserves_total_and_quota(self):
        self.complete();prior=(self.root/"state.json").read_bytes()
        usage=self.f.state()["usage"];original=(self.root/"responses"/(job.request_id(
            "/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0")+".json")).read_bytes()
        epoch=self.begin();state=self.f.state()
        self.assertEqual(state["usage"],usage)
        self.assertEqual((self.root/state["catalogue_epoch"]["previous_checkpoint"]).read_bytes(),prior)
        self.assertEqual(self.publisher.calls,[("A",1),("B",1)])
        result,transport=self.run_job([catalog("A",(1,2)),body("A",2),catalog("B")])
        self.assertEqual(result["outcome"],"complete")
        self.assertEqual((result["requests_this_run"],result["transcripts"]),(3,3))
        self.assertEqual(self.publisher.calls,[("A",1),("B",1),("A",2)])
        self.assertEqual(result["quota"]["attempted"],7)
        self.assertEqual(len([p for p in transport.paths if "/speakers?" in p]),1)
        self.assertEqual((self.root/"responses"/(job.request_id(
            "/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0")+".json")).read_bytes(),original)
        self.assertEqual(begin_catalogue_epoch(self.root,started_at=self.clock.isoformat())["outcome"],"unchanged")
        self.assertEqual(self.run_job([])[0]["requests_this_run"],0)
        self.begin()
        result,transport=self.run_job([catalog("A",(1,2)),catalog("B")])
        self.assertEqual((result["requests_this_run"],result["transcripts"]),(2,3))
        self.assertEqual(len(self.publisher.calls),3)
    def test_refresh_cannot_reset_partial_or_uncertain_work(self):
        before=(self.root/"state.json").read_bytes()
        with self.assertRaises(ConflictError):self.begin()
        self.assertEqual(before,(self.root/"state.json").read_bytes())
        self.complete();epoch=self.begin()
        result,transport=self.run_job([RuntimeError("interrupted")])
        self.assertEqual(result["outcome"],"blocked")
        pending=self.f.state()["pending"];self.assertIsNotNone(pending)
        with self.assertRaises(ConflictError):self.begin()
        result,transport=self.run_job([])
        self.assertEqual(result["outcome"],"blocked");self.assertEqual(transport.paths,[])
        self.assertEqual(pending,self.f.state()["pending"])
    def test_changed_completed_event_id_blocks_without_refetching_transcript(self):
        self.complete();self.begin();value=json.loads(catalog("A"))
        value["data"][0]["id"]="A-reassigned"
        result,transport=self.run_job([json.dumps(value).encode()])
        self.assertEqual(result["outcome"],"blocked")
        self.assertEqual(len(transport.paths),1);self.assertEqual(len(self.publisher.calls),2)
    def test_saved_404_gap_is_not_retried_by_catalogue_refresh(self):
        result,_=self.run_job([catalog("A"),(404,b'{"error":"missing"}'),catalog("B"),body("B")])
        self.assertEqual(result["outcome"],"complete")
        self.begin();result,transport=self.run_job([catalog("A"),catalog("B")])
        self.assertEqual(result["outcome"],"complete")
        self.assertEqual((result["requests_this_run"],result["transcripts"],result["skipped_quarters"]),(2,1,1))
        self.assertTrue(all("/investor-events?" in p for p in transport.paths))
    def test_later_membership_preserves_paid_quota_and_returning_completed_member(self):
        self.complete();old=self.f.state()
        chosen=replace(selection(("B","C")),scope_sha256="2"*64)
        self.clock+=timedelta(seconds=1)
        from quant_data.operations.equibles_paid_policy import migrate_checkpoint
        migrate_checkpoint(self.root,chosen,transitioned_at=self.clock.isoformat())
        state=self.f.state()
        self.assertEqual(state["quota_policy"],old["quota_policy"])
        self.assertEqual(state["usage"],old["usage"])
        self.assertEqual(state["index"],1);self.assertEqual(state["retired_roster"]["A"]["instrument_id"],"frozen-A")
        self.run_job([catalog("C"),body("C")])
        returning=replace(selection(("A","B","C")),scope_sha256="3"*64)
        self.clock+=timedelta(seconds=1)
        migrate_checkpoint(self.root,returning,transitioned_at=self.clock.isoformat())
        self.assertEqual(self.f.state()["index"],3)
        self.assertEqual(self.run_job([])[0]["requests_this_run"],0)
    def test_continuation_distinguishes_new_work_from_failed_or_quota_work(self):
        base={"contract":"quant_data.equibles_transcript_backfill.v2"}
        def decide(outcome,**extra):
            return continuation_decision({**base,"outcome":outcome,**extra},observed_at=AT.isoformat())
        self.assertEqual(decide("run_resource_limit")["action"],"continue")
        self.assertEqual(decide("blocked")["action"],"review")
        self.assertEqual(decide("quota_verification_required")["action"],"review")
        self.assertEqual(decide("provider_quota",quota={"reset":0})["action"],"review")
        self.assertEqual(decide("daily_quota",quota={"reset":int((AT+timedelta(days=1)).timestamp())})["action"],"defer")


    def test_finite_continuations_resume_new_work_in_same_day(self):
        from quant_data.operations.equibles_catalogue import run_continuations
        elapsed=[0]
        def sleep(seconds):
            elapsed[0]+=seconds;self.clock+=timedelta(seconds=seconds)
        transport=PaidTransport([catalog("A"),body("A"),catalog("B"),body("B")],
            clock=lambda:self.clock)
        result=run_continuations(self.root,self.publisher,transport,
            max_invocations=2,max_requests=4,max_run_seconds=120,max_total_bytes=32*1024*1024,
            requests_per_invocation=2,clock=lambda:self.clock,monotonic=lambda:elapsed[0],sleeper=sleep)
        self.assertEqual((result["outcome"],result["requests"],result["invocations"]),("complete",4,2))
        self.assertEqual(self.f.state()["usage"]["2026-09-09"]["attempted"],4)
        self.assertEqual(self.publisher.calls,[("A",1),("B",1)])

    def test_finite_continuations_stop_after_failure_without_retry(self):
        from quant_data.operations.equibles_catalogue import run_continuations
        transport=PaidTransport([RuntimeError("interrupted")],clock=lambda:self.clock)
        result=run_continuations(self.root,self.publisher,transport,
            max_invocations=2,max_requests=4,max_run_seconds=120,max_total_bytes=32*1024*1024,
            requests_per_invocation=2,clock=lambda:self.clock,monotonic=lambda:0,sleeper=lambda _:None)
        self.assertEqual((result["outcome"],result["requests"],result["invocations"]),("blocked",1,1))
        self.assertEqual(len(transport.paths),1)
        self.assertIsNotNone(self.f.state()["pending"])

    def test_expired_physical_publication_lock_keeps_call_private_for_resume(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from tests.company import test_equibles_transcripts as canonical
        from quant_data.company import equibles_transcripts as source
        from quant_data.fingerprint import mutation_fingerprint
        target=canonical.PublisherTests("runTest");target.setUp();self.addCleanup(target.tearDown)
        elapsed=[0];original=source.acquire_write_session
        @contextmanager
        def delayed(*args,**kwargs):
            with original(*args,**kwargs) as locks:
                elapsed[0]=3601
                yield locks
        transport=PaidTransport([catalog("A"),body("A")],clock=lambda:self.clock)
        before=mutation_fingerprint(target.stores)
        with patch.object(source,"acquire_write_session",delayed):
            result=job.run(self.root,target.publisher,transport,clock=lambda:self.clock,
                monotonic=lambda:elapsed[0],sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(result["outcome"],"runtime_limit")
        self.assertEqual((result["requests_this_run"],result["transcripts"]),(2,0))
        self.assertEqual(before,mutation_fingerprint(target.stores))
        self.assertIsNone(self.f.state()["pending"])
        self.assertIsNone(self.f.state()["blocked"])

    def test_returning_partial_member_preserves_real_canonical_call_count(self):
        from tests.company import test_equibles_transcripts as canonical
        from quant_data.operations.equibles_paid_policy import migrate_checkpoint
        from quant_data.stores import quiet_immutable_read_connection
        target=canonical.PublisherTests("runTest");target.setUp();self.addCleanup(target.tearDown)
        def run(values,cap=1000):
            transport=PaidTransport(values,clock=lambda:self.clock)
            result=job.run(self.root,target.publisher,transport,clock=lambda:self.clock,
                sleeper=lambda _:None,allow_paid=True,max_requests=cap)
            return result,transport
        report,_=run([catalog("A",(1,2)),body("A",1)],2)
        self.assertEqual(report["outcome"],"run_resource_limit")
        self.assertEqual(self.f.state()["current"]["captured"],1)
        self.clock+=timedelta(seconds=1)
        migrate_checkpoint(self.root,replace(selection(("B",)),scope_sha256="2"*64),
            transitioned_at=self.clock.isoformat())
        self.assertEqual(self.f.state()["deferred_progress"]["A"]["progress"]["captured"],1)
        self.assertEqual(run([catalog("B"),body("B")])[0]["outcome"],"complete")
        self.clock+=timedelta(seconds=1)
        migrate_checkpoint(self.root,replace(selection(("A","B")),scope_sha256="3"*64),
            transitioned_at=self.clock.isoformat())
        self.assertEqual(self.f.state()["current"]["captured"],1)
        report,transport=run([body("A",2)])
        self.assertEqual((report["outcome"],report["transcripts"]),("complete",3))
        self.assertEqual(len(transport.paths),1)
        with quiet_immutable_read_connection(target.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_equibles_transcripts").fetchone()[0],3)
        self.assertEqual(self.begin()["known_calls"],3)

    def test_outer_deadline_includes_checkpoint_validation(self):
        from unittest.mock import patch
        from quant_data.operations import equibles_paid_policy as policy
        from quant_data.operations.equibles_catalogue import run_continuations
        elapsed=[0];original=policy.validate_checkpoint
        def delayed(state):
            result=original(state);elapsed[0]=61;return result
        transport=PaidTransport([],clock=lambda:self.clock)
        with patch.object(policy,"validate_checkpoint",side_effect=delayed):
            report=run_continuations(self.root,self.publisher,transport,max_invocations=2,
                max_requests=4,max_run_seconds=60,max_total_bytes=32*1024*1024,
                clock=lambda:self.clock,monotonic=lambda:elapsed[0],sleeper=lambda _:None)
        self.assertEqual(report["requests"],0);self.assertEqual(transport.paths,[])
        self.assertIsNone(self.f.state()["pending"])

    def test_deadline_during_reservation_retains_undispatched_evidence_and_resumes(self):
        from unittest.mock import patch
        from quant_data.operations.equibles_catalogue import run_continuations
        elapsed=[0];original=job.atomic
        def delayed(path,value,**kwargs):
            result=original(path,value,**kwargs)
            if path==self.root/"state.json" and isinstance(value,dict) and value.get("pending") is not None:
                elapsed[0]=61
            return result
        transport=PaidTransport([],clock=lambda:self.clock)
        with patch.object(job,"atomic",side_effect=delayed):
            report=run_continuations(self.root,self.publisher,transport,max_invocations=2,
                max_requests=4,max_run_seconds=60,max_total_bytes=32*1024*1024,
                clock=lambda:self.clock,monotonic=lambda:elapsed[0],sleeper=lambda _:None)
        self.assertEqual(report["requests"],0);self.assertEqual(transport.paths,[])
        state=self.f.state()
        self.assertIsNone(state["pending"]);self.assertIsNone(state["blocked"])
        self.assertEqual(state["usage"]["2026-09-09"]["attempted"],1)
        self.assertEqual(state["usage"]["2026-09-09"]["verification_requests"],0)
        self.assertEqual(state["undispatched_reservations"],1)
        self.assertEqual(len(list((self.root/"attempts").glob("*.not-dispatched.json"))),1)
        result,_=self.run_job([catalog("A"),body("A"),catalog("B"),body("B")])
        self.assertEqual((result["outcome"],result["requests_this_run"],result["quota"]["attempted"]),
            ("complete",4,5))

    def test_selected_count_includes_deferred_partial_members_waiting_behind_current(self):
        from quant_data.operations.equibles_paid_policy import migrate_checkpoint
        def choose(symbols,sha):
            self.clock+=timedelta(seconds=1)
            migrate_checkpoint(self.root,replace(selection(symbols),scope_sha256=sha*64),
                transitioned_at=self.clock.isoformat())
        def run(values,cap):
            return job.run(self.root,self.publisher,PaidTransport(values,clock=lambda:self.clock),
                clock=lambda:self.clock,sleeper=lambda _:None,allow_paid=True,max_requests=cap)
        self.assertEqual(run([catalog("A",(1,2)),body("A",1)],2)["outcome"],"run_resource_limit")
        choose(("B","C"),"2")
        self.assertEqual(run([catalog("B",(1,2)),body("B",1)],2)["outcome"],"run_resource_limit")
        choose(("C",),"3")
        choose(("A","B","C"),"4")
        report=run([catalog("C")],1)
        self.assertEqual(report["current_ticker"],"C")
        self.assertEqual(report["transcripts"],2)
        self.assertEqual(report["selected_transcripts"],2)
        self.assertEqual(set(self.f.state()["deferred_progress"]),{"A","B"})

    def test_catalogue_epoch_preserves_calls_of_a_removed_partial_subject(self):
        from quant_data.operations.equibles_paid_policy import migrate_checkpoint
        transport=PaidTransport([catalog("A",(1,2)),body("A",1)],clock=lambda:self.clock)
        result=job.run(self.root,self.publisher,transport,clock=lambda:self.clock,
            sleeper=lambda _:None,allow_paid=True,max_requests=2)
        self.assertEqual(result["transcripts"],1)
        self.clock+=timedelta(seconds=1)
        migrate_checkpoint(self.root,replace(selection(("B",)),scope_sha256="2"*64),
            transitioned_at=self.clock.isoformat())
        report,_=self.run_job([catalog("B"),body("B")])
        self.assertEqual((report["outcome"],report["transcripts"]),("complete",2))
        self.assertEqual(self.begin()["known_calls"],2)
        self.assertEqual(self.f.state()["deferred_progress"]["A"]["progress"]["captured"],1)
        # A remains outside the roster, while B's current catalogue skips its old call.
        report,transport=self.run_job([catalog("B")])
        self.assertEqual((report["outcome"],report["requests_this_run"],report["transcripts"]),("complete",1,2))
        self.assertEqual(len(transport.paths),1)
        self.assertEqual(report["selected_transcripts"],1)

    def test_epoch_rejects_backdated_selection_and_deferred_page_evidence(self):
        from quant_data.operations.equibles_paid_policy import migrate_checkpoint
        # Complete A first, then retain B/Q1 with B/Q2 unfinished.
        self.assertEqual(job.run(self.root,self.publisher,PaidTransport([catalog("A"),body("A")],
            clock=lambda:self.clock),clock=lambda:self.clock,sleeper=lambda _:None,
            allow_paid=True,max_requests=2)["transcripts"],1)
        self.clock+=timedelta(minutes=10)
        self.assertEqual(job.run(self.root,self.publisher,PaidTransport([catalog("B",(1,2)),body("B",1)],
            clock=lambda:self.clock),clock=lambda:self.clock,sleeper=lambda _:None,
            allow_paid=True,max_requests=2)["transcripts"],2)
        self.clock+=timedelta(minutes=1)
        migrate_checkpoint(self.root,replace(selection(("A",)),scope_sha256="5"*64),
            transitioned_at=self.clock.isoformat())
        before=(self.root/"state.json").read_bytes()
        with self.assertRaises(ConflictError):
            begin_catalogue_epoch(self.root,started_at=(AT+timedelta(minutes=5)).isoformat())
        self.assertEqual((self.root/"state.json").read_bytes(),before)
        # An anomalously early transition must not bypass original capture times.
        state=self.f.state()
        state["selection_transition"]["transitioned_at"]=(AT+timedelta(minutes=1)).isoformat()
        job.atomic(self.root/"state.json",state,replace=True)
        before=(self.root/"state.json").read_bytes()
        with self.assertRaisesRegex(ConflictError,"deferred call acquisition"):
            begin_catalogue_epoch(self.root,started_at=(AT+timedelta(minutes=5)).isoformat())
        self.assertEqual((self.root/"state.json").read_bytes(),before)
        self.assertEqual(self.begin()["known_calls"],2)
