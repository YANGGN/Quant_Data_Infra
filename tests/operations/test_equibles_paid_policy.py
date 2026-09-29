from dataclasses import replace
from datetime import datetime,timezone,timedelta
import hashlib,json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from quant_data.errors import ConflictError,ResourceLimitError,ValidationError
from quant_data.market.collection_bindings import load_bindings,BoundSubject,PinnedCollection
from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations.equibles_paid_policy import (
    POLICY,convert_checkpoint,migrate_checkpoint,validate_checkpoint,quota_bucket,apply_paid_headers)
from quant_data.inspector_equibles import read_equibles_progress
from tests.company.test_equibles_transcripts import FakeTransport,FakePublisher,catalog,body

ROOT=Path(__file__).resolve().parents[2]
AT=datetime(2026,9,9,1,tzinfo=timezone.utc)
BINDING=load_bindings(ROOT/"config/collection_bindings.json")["equibles_transcripts"]

def selection(symbols=("A","B"),unresolved=()):
    return PinnedCollection(BINDING,"membership","mapping",
        tuple(BoundSubject(s,s,s,"frozen-"+s,None,"resolved","retained proof") for s in symbols)
        +tuple(BoundSubject(s,None,None,None,None,"unresolved","not supported") for s in unresolved),
        (),"1"*64)

class PaidTransport(FakeTransport):
    def __init__(self,values,remaining=99900,clock=lambda:AT):
        super().__init__(values,clock=clock,remaining=remaining)
    def request(self,path):
        status,headers,raw=super().request(path)
        headers["x-ratelimit-limit"]="100000"
        return status,headers,raw

class EquiblesPaidPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir="/tmp");self.root=Path(self.tmp.name)/"private"
        with patch.object(job,"now",lambda:AT-timedelta(days=1)):
            job.freeze(self.root,[{"symbol":s,"instrument_id":"frozen-"+s} for s in ("A","B")])
    def tearDown(self):self.tmp.cleanup()
    def state(self):return json.loads((self.root/"state.json").read_bytes())
    def save(self,state):job.atomic(self.root/"state.json",state,replace=True)
    def convert(self,selected=None):
        return migrate_checkpoint(self.root,selected or selection(),transitioned_at=AT.isoformat())
    def test_same_day_conversion_preserves_attempts_and_archives_old_zero_remaining(self):
        old=self.state();old["usage"][AT.date().isoformat()]={"attempted":100,"remaining":0}
        self.save(old);raw=(self.root/"state.json").read_bytes()
        result=self.convert()
        self.assertEqual(result["provider_requests"],0)
        current=self.state();bucket=current["usage"]["2026-09-09"]
        self.assertEqual(bucket["attempted"],100);self.assertEqual(bucket["remaining"],99900)
        self.assertEqual(bucket["prior_policy_bucket"],old["usage"]["2026-09-09"])
        self.assertEqual(current["quota_policy"]["legacy_usage"],old["usage"])
        self.assertEqual((self.root/current["legacy_checkpoint_reference"]).read_bytes(),raw)
        before=(self.root/"state.json").read_bytes()
        self.assertEqual(self.convert()["outcome"],"unchanged")
        self.assertEqual(before,(self.root/"state.json").read_bytes())

    def test_current_subject_partial_pages_and_outside_completed_history_survive(self):
        old=self.state();old["index"]=1
        old["completed"]={"A":{"transcripts":2,"events":2,"status":"complete_available_history","finished_at":AT.isoformat()}}
        old["transcripts"]=2
        old["current"]={"catalog":[{"id":"B-1"}],"catalog_done":False,"event_index":0,
            "events":[],"pages":["retained-page"],"offset":2,"captured":0}
        old["pending"]={"path":"/v1/stocks/B/investor-events?eventType=EarningsCall&limit=100&offset=1",
            "attempt":"2026-09-09-001","started_at":AT.isoformat()}
        old["usage"]["2026-09-09"]={"attempted":1,"remaining":99}
        old["blocked"]={"error":"ConflictError","reason":"needs reconciliation","at":AT.isoformat()}
        self.save(old);self.convert(selection(("B","C"),("D",)))
        new=self.state()
        self.assertEqual([r["symbol"] for r in new["roster"]],["B","C"])
        self.assertEqual(new["index"],0)
        self.assertEqual(new["current"],old["current"])
        self.assertEqual(new["pending"],old["pending"])
        self.assertEqual(new["blocked"],old["blocked"])
        self.assertEqual(new["completed"],old["completed"])
        self.assertEqual(new["selection"]["selected_members"],3)
        self.assertEqual(len(new["selection"]["identity_gaps"]),1)

    def test_unselected_pending_attempt_and_changed_instrument_fail_without_mutation(self):
        old=self.state();old["pending"]={"path":"uncertain","attempt":"old","started_at":AT.isoformat()}
        self.save(old);before=(self.root/"state.json").read_bytes()
        with self.assertRaises(ConflictError):self.convert(selection(("B",)))
        self.assertEqual(before,(self.root/"state.json").read_bytes())
        wrong=selection()
        wrong=replace(wrong,subjects=(replace(wrong.subjects[0],instrument_id="different"),wrong.subjects[1]))
        with self.assertRaises(ConflictError):self.convert(wrong)
        self.assertEqual(before,(self.root/"state.json").read_bytes())

    def test_paid_checkpoint_needs_explicit_activation_and_respects_fresh_headers(self):
        old=self.state();old["usage"]["2026-09-09"]={"attempted":100,"remaining":0};self.save(old)
        self.convert()
        transport=PaidTransport([catalog("A"),body("A"),catalog("B"),body("B")])
        with self.assertRaises(ConflictError):
            job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None)
        self.assertEqual(transport.paths,[])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["requests_this_run"],4)
        self.assertEqual(report["quota"]["attempted"],104)
        self.assertEqual(report["quota"]["remaining"],99896)
        self.assertTrue(report["provider_paid_allowance_verified"])
        self.assertEqual(report["contract"],"quant_data.equibles_transcript_backfill.v2")
        status=read_equibles_progress(self.root,observed_at=datetime.now(timezone.utc)+timedelta(days=1))
        self.assertTrue(status["available"])
        self.assertEqual(status["quota_daily_ceiling"],100000)
        self.assertEqual(status["quota_used"],104)
        self.assertEqual(status["resolved_subjects"],2)

    def test_legacy_headers_do_not_suppress_or_verify_new_entitlement(self):
        self.convert();state=self.state();bucket=quota_bucket(state,"2026-09-09")
        result=apply_paid_headers(state,bucket,limit=100,remaining=0,reset=0,
            receipt_at=(AT-timedelta(seconds=1)).isoformat(),reservation_at=(AT-timedelta(seconds=2)).isoformat())
        self.assertFalse(result);self.assertEqual(bucket["remaining"],100000)
        self.assertFalse(state["quota_policy"]["provider_paid_allowance_verified"])
        apply_paid_headers(state,bucket,limit=100000,remaining=7,reset=0,
            receipt_at=AT.isoformat(),reservation_at=AT.isoformat())
        self.assertEqual(bucket["remaining"],7)

    def test_provider_remaining_and_daily_cap_stop_before_new_requests(self):
        self.convert()
        report=job.run(self.root,FakePublisher(),PaidTransport([catalog()],remaining=1),
            clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(report["requests_this_run"],1)
        state=self.state();bucket=state["usage"]["2026-09-09"]
        bucket["attempted"]=100000;bucket["remaining"]=1;self.save(state)
        transport=PaidTransport([])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(transport.paths,[])

    def test_stale_response_recovery_preserves_pending_and_never_refetches_it(self):
        path="/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0"
        old=self.state();old["pending"]={"path":path,"attempt":"old","started_at":(AT-timedelta(seconds=2)).isoformat()}
        old["usage"]["2026-09-09"]={"attempted":100,"remaining":0}
        reset=int((AT.replace(hour=0)+timedelta(days=1)).timestamp())
        headers={"content-type":"application/json","x-ratelimit-limit":"100","x-ratelimit-remaining":"0","x-ratelimit-reset":str(reset)}
        job.retain_response(self.root,path,catalog(),(AT-timedelta(seconds=1)).isoformat(),headers,200)
        self.save(old);self.convert()
        transport=PaidTransport([body("A"),catalog("B"),body("B")])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"complete")
        self.assertNotIn(path,transport.paths)
        self.assertEqual(report["quota"]["attempted"],103)

    def test_missing_quota_headers_on_404_requires_verification_before_more_work(self):
        self.convert()
        class Missing:
            def __init__(self):self.paths=[]
            def request(self,path):
                self.paths.append(path);return 404,{"content-type":"application/json"},b'{"error":"not found"}'
        transport=Missing()
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"quota_verification_required")
        self.assertEqual(len(transport.paths),1)
        self.assertFalse(report["provider_paid_allowance_verified"])
        self.assertEqual(report["completed_tickers"],1)

    def test_quota_day_changes_do_not_refund_prior_attempts(self):
        self.convert();state=self.state();old=quota_bucket(state,"2026-09-09")
        old["attempted"]=100000;old["remaining"]=0
        following=quota_bucket(state,"2026-09-10")
        self.assertEqual(following["attempted"],0)
        self.assertEqual(following["remaining"],100000)
        self.assertEqual(old["attempted"],100000)


    def test_thousand_request_invocation_continues_from_the_next_selected_subject(self):
        symbols=tuple("S"+str(i).zfill(4) for i in range(1001))
        self.convert(selection(symbols))
        empty=json.dumps({"data":[],"meta":{"offset":0,"count":0,"limit":100,"hasMore":False}}).encode()
        transport=PaidTransport([empty]*1000,remaining=100000)
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"run_resource_limit")
        self.assertEqual(report["requests_this_run"],1000)
        self.assertEqual(report["completed_tickers"],1000)
        self.assertEqual(report["current_ticker"],"S1000")
        next_transport=PaidTransport([empty],remaining=99000)
        final=job.run(self.root,FakePublisher(),next_transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(final["outcome"],"complete")
        self.assertEqual(final["requests_this_run"],1)
        self.assertEqual(final["quota"]["attempted"],1001)
        self.assertIn("/S1000/",next_transport.paths[0])
        self.assertNotIn(next_transport.paths[0],transport.paths)

    def test_symbol_alias_cannot_restart_an_existing_instrument_history(self):
        old=self.state();old["index"]=1
        old["completed"]={"A":{"transcripts":1,"events":1,"status":"complete_available_history","finished_at":AT.isoformat()}}
        old["transcripts"]=1;self.save(old)
        alias=selection(("C",))
        alias=replace(alias,subjects=(replace(alias.subjects[0],instrument_id="frozen-A"),))
        before=(self.root/"state.json").read_bytes()
        with self.assertRaisesRegex(ConflictError,"progress-preserving"):
            self.convert(alias)
        self.assertEqual(before,(self.root/"state.json").read_bytes())

    def test_legacy_429_keeps_charge_without_restoring_old_daily_quota(self):
        path="/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0"
        old=self.state()
        old["pending"]={"path":path,"attempt":"old","started_at":(AT-timedelta(seconds=2)).isoformat()}
        old["usage"]["2026-09-09"]={"attempted":100,"remaining":0}
        headers={"content-type":"application/json","x-ratelimit-limit":"100","x-ratelimit-remaining":"0",
            "x-ratelimit-reset":str(int((AT.replace(hour=0)+timedelta(days=1)).timestamp()))}
        job.retain_response(self.root,path,b'{"error":"quota"}',(AT-timedelta(seconds=1)).isoformat(),headers,429,"old")
        self.save(old);self.convert()
        transport=PaidTransport([])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"quota_verification_required")
        self.assertEqual(transport.paths,[])
        self.assertEqual(report["quota"]["attempted"],100)
        self.assertEqual(report["quota"]["remaining"],99900)
        self.assertFalse(report["provider_paid_allowance_verified"])
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.state()["quota_policy"]["legacy_usage"]["2026-09-09"]["remaining"],0)

    def test_midnight_response_updates_its_verified_window_and_preserves_original_charge(self):
        self.convert(selection(("A",)))
        state=self.state()
        started=AT.replace(hour=23,minute=59,second=59)
        received=started+timedelta(seconds=2)
        path="/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0"
        state["pending"]={"path":path,"attempt":"crossing","started_at":started.isoformat()}
        bucket=quota_bucket(state,"2026-09-09");bucket["attempted"]=1;bucket["remaining"]=99999
        self.save(state)
        headers={"content-type":"application/json","x-ratelimit-limit":"100000","x-ratelimit-remaining":"99997",
            "x-ratelimit-reset":str(int((received.replace(hour=0,minute=0,second=0)+timedelta(days=1)).timestamp()))}
        empty=json.dumps({"data":[],"meta":{"offset":0,"count":0,"limit":100,"hasMore":False}}).encode()
        job.retain_response(self.root,path,empty,received.isoformat(),headers,200,"crossing")
        transport=PaidTransport([])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:received,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(transport.paths,[])
        new=self.state()
        self.assertEqual(new["usage"]["2026-09-09"]["attempted"],1)
        self.assertEqual(new["usage"]["2026-09-10"]["attempted"],0)
        self.assertEqual(new["usage"]["2026-09-10"]["remaining"],99997)
        self.assertTrue(new["usage"]["2026-09-10"]["paid_headers_verified"])
        self.assertIsNone(new["blocked"])

    def test_retired_partial_failure_remains_visible_when_selection_is_complete(self):
        old=self.state();old["index"]=1
        old["completed"]={"A":{"transcripts":0,"events":0,"status":"complete_available_history","finished_at":AT.isoformat()}}
        old["current"]={"catalog":[],"catalog_done":False,"event_index":0,"events":[],"pages":[],"offset":0,"captured":0}
        old["blocked"]={"error":"ConflictError","reason":"retained failure","at":AT.isoformat()}
        self.save(old);self.convert(selection(("A",)))
        transport=PaidTransport([])
        report=job.run(self.root,FakePublisher(),transport,clock=lambda:AT,sleeper=lambda _:None,allow_paid=True)
        self.assertEqual(report["outcome"],"blocked")
        self.assertEqual(report["completed_tickers"],1)
        self.assertIsNone(report["current_ticker"])
        status=read_equibles_progress(self.root,observed_at=datetime.now(timezone.utc)+timedelta(days=1))
        self.assertTrue(status["available"]);self.assertEqual(status["outcome"],"blocked")
        self.assertEqual(transport.paths,[])
        self.assertEqual(self.state()["deferred_progress"]["B"],
            {"instrument_id":"frozen-B","progress":old["current"]})
