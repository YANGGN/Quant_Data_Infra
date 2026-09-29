"""Offline incremental refresh checks: temporary ledgers, fake HTTP, existing validators."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import copy,json,tempfile,unittest
from unittest.mock import patch
from quant_data.operations import equibles_refresh as live
from quant_data.operations import equibles_transcript_backfill as old
from quant_data.operations.equibles_paid_policy import operating_daily_cap,quota_bucket,apply_paid_headers
from quant_data.company.equibles_transcripts import validate_bundle
from quant_data.errors import ConflictError,ValidationError
from tests.operations import test_equibles_paid_policy as paid_fixtures
from tests.company.test_equibles_transcripts import FakeTransport,body,event

AT=datetime(2026,9,18,6,tzinfo=timezone.utc)

def info(symbol="A", dates=("2026-09-17",),history=()):
    return dict(instrument_id="frozen-"+symbol,earnings_dates=list(dates),
        calendar_captured_at="2026-09-18T01:00:00.000000Z",call_dates=list(history),known={},latest_capture=None)

def catalogue(symbol="A",has=True,call_date="2026-09-17",quarter=1):
    e=dict(event(symbol,quarter),callDate=call_date,hasTranscript=has)
    return json.dumps(dict(data=[e],meta=dict(offset=0,count=1,limit=100,hasMore=False))).encode()

class Publisher:
    def __init__(self):self.calls=[]
    def publish(self,**kwargs):
        values=validate_bundle(kwargs["symbol"],kwargs["instrument_id"],kwargs["event"],kwargs["pages"])
        self.calls.append(values)
        return SimpleNamespace(outcome="published",semantic_identity="1"*64)

class SelectionTests(unittest.TestCase):
    def test_first_check_waits_until_next_day(self):
        self.assertIsNone(live.classify(info(dates=("2026-09-18",)),dict(last_checked="2026-09-17"),AT.date()))
        self.assertEqual(live.classify(info(),{},AT.date())[0],"recent")
    def test_saved_call_resolves_calendar(self):
        value=info(history=("2026-09-17",));value["latest_capture"]="2026-09-18"
        self.assertIsNone(live.classify(value,{},AT.date()))
    def test_estimate_is_only_a_queue_hint(self):
        value=info(dates=(),history=("2025-12-17","2026-03-17","2026-06-17"))
        prior=copy.deepcopy(value)
        self.assertEqual(live.classify(value,dict(last_checked="2026-09-10"),AT.date())[0],"fallback")
        self.assertEqual(value,prior)
    def test_no_history_rotates_monthly(self):
        value=info(dates=())
        self.assertIsNone(live.classify(value,dict(last_checked="2026-09-01"),AT.date()))
        self.assertEqual(live.classify(value,dict(last_checked="2026-08-01"),AT.date()),("fallback",None))
    def test_unresolved_persists_past_calendar_window(self):
        self.assertEqual(live.classify(info(dates=()),dict(awaiting=True,next_due="2026-09-18",report_day="2026-08-01"),AT.date())[0],"delayed")
    def test_dst_due_slot_and_single_catchup(self):
        self.assertEqual(live.due_slot(datetime(2026,11,2,8,tzinfo=timezone.utc)).utcoffset(),timedelta(hours=-5))
        self.assertEqual(live.due_slot(AT).date(),AT.date())
        self.assertEqual(live.due_slot(AT-timedelta(hours=1)).date(),AT.date()-timedelta(days=1))
    def test_weekend_excludes_fresh_recent_and_selects_oldest_fallback(self):
        state=live.initial_state([],AT.isoformat());inventory={"A":info(),"B":info("B",dates=())}
        live.sync_queue(state,inventory,AT.date())
        selected=live.next_task(state,date(2026,9,19),dict(recent=0,delayed=0,fallback=0),dict(recent=0,delayed=0,fallback=100))
        self.assertEqual(selected,("B","fallback"))
    def test_partial_download_precedes_discovery_and_can_borrow(self):
        state=live.initial_state([],AT.isoformat())
        live.sync_queue(state,{"A":info(),"B":info("B",dates=())},AT.date())
        state["tasks"]["A"]["pages"]=["retained"]
        state["tasks"]["A"]["phase"]="download"
        self.assertEqual(live.next_task(state,AT.date(),dict(recent=80,delayed=0,fallback=0),dict(recent=80,delayed=15,fallback=5)),("A","recent"))
    def test_retry_dates_and_sunday_no_duplicate_check(self):
        state=live.initial_state([],AT.isoformat());live.sync_queue(state,{"A":info()},AT.date())
        live.finish_check(state,state["tasks"]["A"],AT.date(),True)
        self.assertEqual(state["tasks"]["A"]["due"],"2026-09-20")
        self.assertIsNone(live.next_task(state,date(2026,9,19),dict(recent=0,delayed=0,fallback=0),dict(recent=0,delayed=0,fallback=100)))

class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.f=paid_fixtures.EquiblesPaidPolicyTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.convert()
        self.account=self.f.root
        value=self.f.state()
        value.update(index=2,current=None,pending=None,blocked=None,completed={s:{"transcripts":0} for s in ("A","B")},operating_daily_cap=100)
        self.f.save(value)
        self.root=self.account.parent/"refresh";old.private_directory(self.root)
        old.atomic(self.root/"state.json",live.initial_state(value["roster"],AT.isoformat()))
        self.now=AT;self.elapsed=0;self.inventory={"A":info(),"B":info("B",dates=("2026-10-01",))}
        self.inventory["B"]["latest_capture"]="2026-09-17"
        self.pub=Publisher()
    def sleep(self,seconds):
        self.now+=timedelta(seconds=seconds);self.elapsed+=seconds
    def run_values(self,values,remaining=100):
        transport=FakeTransport(values,clock=lambda:self.now,remaining=remaining)
        result=live.run_refresh(self.root,self.account,self.pub,transport,self.inventory,
              clock=lambda:self.now,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        return result,transport
    def state(self):return live.load(self.root/"state.json")
    def save(self,state):old.atomic(self.root/"state.json",state,replace=True)
    def charge(self,count):
        account=self.f.state();account["usage"][AT.date().isoformat()]={"attempted":count,"remaining":100000-count}
        self.f.save(account)
    def test_discover_save_queue_once_and_repeat_no_calls(self):
        result,t=self.run_values([catalogue(),body()])
        self.assertEqual((result["saved"],len(t.paths)),(1,2))
        self.assertEqual(len(self.state()["analysis_queue"]),1)
        result,t=self.run_values([])
        self.assertEqual(len(t.paths),0)
    def test_skips_known_and_old_events(self):
        self.inventory["A"]["known"]["A:2020:1"]="existing"
        result,t=self.run_values([catalogue()])
        self.assertEqual((result["saved"],len(t.paths)),(0,1))
        self.assertEqual(self.pub.calls,[])
    def test_account_calls_leave_only_one_request_and_resume_download_tomorrow(self):
        self.charge(99)
        result,t=self.run_values([catalogue()])
        self.assertEqual((result["requests"],result["charged_today"]),(1,100))
        self.assertEqual(self.state()["tasks"]["A"]["phase"],"download")
        self.now+=timedelta(days=1)
        # Weekend continues a discovered download even before any page exists.
        result,t=self.run_values([body()])
        self.assertEqual(result["saved"],1)
    def test_provider_remaining_stops_below_local_cap(self):
        result,t=self.run_values([catalogue()],remaining=1)
        self.assertEqual(len(t.paths),1)
        self.assertEqual(result["saved"],0)
    def test_zero_calls_at_shared_cap(self):
        self.charge(100)
        result,t=self.run_values([])
        self.assertEqual((result["requests"],t.paths),(0,[]))
    def test_multi_page_saved_only_when_complete(self):
        self.charge(98)
        result,t=self.run_values([catalogue(),body(total=201,count=200)])
        self.assertEqual(result["saved"],0)
        self.assertEqual(self.state()["tasks"]["A"]["offset"],200)
        self.now+=timedelta(days=1)
        result,t=self.run_values([body(offset=200,total=201,count=1)])
        self.assertEqual(result["saved"],1)
        self.assertEqual(len(self.pub.calls[0]),2)
    def test_publication_failure_keeps_complete_pages_zero_call_recovery(self):
        with patch.object(self.pub,"publish",side_effect=ConflictError("Timed out acquiring the physical store lock")):
            result,t=self.run_values([catalogue(),body()])
        self.assertEqual(result["outcome"],"publication_deferred")
        result,t=self.run_values([])
        self.assertEqual((result["saved"],len(t.paths)),(1,0))
    def test_unavailable_backoff_has_no_same_day_loop(self):
        result,t=self.run_values([catalogue(has=False)])
        self.assertEqual(len(t.paths),1)
        self.assertEqual(self.state()["tasks"]["A"]["due"],"2026-09-20")
        result,t=self.run_values([])
        self.assertEqual(len(t.paths),0)
    def test_transient_failure_only_one_later_retry_then_held(self):
        failure=old.EquiblesTransportFailure("timeout")
        result,t=self.run_values([failure])
        self.assertEqual(len(t.paths),1)
        self.now+=timedelta(days=3)
        result,t=self.run_values([failure])
        self.assertEqual(result["blocked"],1)
        self.now+=timedelta(days=1)
        result,t=self.run_values([])
        self.assertEqual(t.paths,[])
        self.assertEqual(sum(b["attempted"] for b in self.f.state()["usage"].values()),2)
    def test_auth_failure_does_not_retry(self):
        with self.assertRaises(ConflictError):self.run_values([(401,b'{}')])
        self.assertTrue(self.state()["tasks"]["A"]["blocked"])
        result,t=self.run_values([])
        self.assertEqual(t.paths,[])
    def test_429_preserves_charge_and_stops(self):
        result,t=self.run_values([(429,b'{}')])
        self.assertEqual(result["outcome"],"provider_quota")
        self.assertEqual(result["charged_today"],1)
    def test_uncertain_crash_is_held_without_another_request(self):
        with self.assertRaises(RuntimeError):self.run_values([RuntimeError("process interruption")])
        self.assertIsNotNone(self.state()["pending"])
        result,t=self.run_values([])
        self.assertEqual(t.paths,[])
        self.assertEqual(result["blocked"],1)
        self.assertEqual(result["charged_today"],1)
    def test_retained_response_recovers_without_duplicate_get(self):
        original=live.settle_headers
        with patch.object(live,"settle_headers",side_effect=RuntimeError("interrupt after receipt")):
            with self.assertRaises(RuntimeError):self.run_values([catalogue()])
        self.assertIsNotNone(self.state()["pending"])
        result,t=self.run_values([body()])
        self.assertEqual(result["saved"],1)
        self.assertEqual(len(t.paths),1)
        self.assertEqual(result["charged_today"],2)
    def enable_second(self):
        self.inventory["B"]=info("B")
    def test_malformed_catalogue_holds_only_affected_ticker(self):
        self.enable_second()
        result,t=self.run_values([b'{}',catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,3))
        state=self.state()
        self.assertIsNone(state["pending"])
        self.assertIn("rejected_response_id",state["tasks"]["A"])
        self.now+=timedelta(days=1)
        result,t=self.run_values([])
        self.assertEqual(t.paths,[])
    def test_invalid_utf8_holds_only_affected_ticker(self):
        self.enable_second()
        result,t=self.run_values([bytes([255]),catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,3))
        self.assertIsNone(self.state()["pending"])
    def test_unavailable_without_fiscal_labels_uses_backoff_not_inference(self):
        self.enable_second()
        value=json.loads(catalogue(has=False))
        del value["data"][0]["fiscalYear"]
        del value["data"][0]["fiscalQuarter"]
        result,t=self.run_values([json.dumps(value).encode(),catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,0,3))
        self.assertEqual(self.state()["tasks"]["A"]["due"],"2026-09-20")
        self.assertEqual(self.state()["tasks"]["A"]["events"],[])
    def test_malformed_transcript_does_not_stop_other_ticker(self):
        self.enable_second()
        result,t=self.run_values([catalogue(),b'{}',catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,4))
        self.assertIsNone(self.state()["pending"])
    def test_recovered_invalid_response_is_held_without_repeated_request(self):
        with patch.object(live,"settle_headers",side_effect=RuntimeError("interrupt")):
            with self.assertRaises(RuntimeError):self.run_values([b'{}'])
        self.enable_second()
        result,t=self.run_values([catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,2))
        self.assertIsNone(self.state()["pending"])
    def test_invalid_bundle_does_not_stop_other_ticker(self):
        self.enable_second()
        original=live.validate_bundle
        def check(symbol,*args,**kwargs):
            if symbol=="A":raise ValidationError("inconsistent complete bundle")
            return original(symbol,*args,**kwargs)
        with patch.object(live,"validate_bundle",side_effect=check):
            result,t=self.run_values([catalogue(),body(),catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,4))
        self.assertEqual(self.state()["tasks"]["A"]["reason"],"bundle_ValidationError")
    def test_page_cap_stops_before_excess_get_and_continues_queue(self):
        self.enable_second()
        pages=[body(offset=i,total=51,count=1) for i in range(50)]
        result,t=self.run_values([catalogue(),*pages,catalogue("B"),body("B")])
        self.assertEqual((result["saved"],result["blocked"],len(t.paths)),(1,1,53))
        self.assertEqual(sum("/A/earnings-calls/" in path for path in t.paths),50)
        self.assertEqual(self.state()["tasks"]["A"]["reason"],"transcript_page_cap")
    def test_invalid_quota_stops_all_dispatch_until_review(self):
        self.enable_second()
        with patch.object(live,"settle_headers",side_effect=ValidationError("invalid quota")):
            with self.assertRaises(ValidationError):self.run_values([catalogue()])
        self.assertIsNotNone(self.state()["pending"])
        self.assertEqual(self.f.state()["usage"]["2026-09-18"]["attempted"],1)
    def test_fiscal_labels_from_provider_never_inferred_from_calendar_year(self):
        result,t=self.run_values([catalogue(quarter=4),body(quarter=4)])
        self.assertIn("/2020/4/",t.paths[1])
    def test_weekend_oldest_fallback_first(self):
        self.now=datetime(2026,9,19,6,tzinfo=timezone.utc)
        self.inventory={"A":info(dates=()),"B":info("B",dates=())}
        state=self.state();state["watch"]={"A":{"last_checked":"2026-08-10"},"B":{"last_checked":"2026-08-01"}};self.save(state)
        result,t=self.run_values([(404,b'{}'),(404,b'{}')])
        self.assertIn("/B/",t.paths[0])
        self.assertEqual(result["lane_calls"]["fallback"],2)

class SharedCapTests(unittest.TestCase):
    def test_default_unchanged_and_cap_preserves_old_charges(self):
        f=paid_fixtures.EquiblesPaidPolicyTests("runTest");f.setUp()
        try:
            f.convert();state=f.state()
            self.assertEqual(operating_daily_cap(state),100000)
            state["operating_daily_cap"]=100
            state["usage"]["2026-09-18"]={"attempted":150,"remaining":99900}
            bucket=quota_bucket(state,"2026-09-18")
            self.assertEqual((bucket["attempted"],bucket["remaining"]),(150,0))
            apply_paid_headers(state,bucket,limit=10000,remaining=9000,reset=1,
               receipt_at=AT.isoformat(),reservation_at=AT.isoformat())
            self.assertEqual(bucket["remaining"],0)
        finally:f.tearDown()
    def test_serial_and_parallel_existing_callers_honor_cap(self):
        from quant_data.operations import equibles_parallel_backfill as parallel
        f=paid_fixtures.EquiblesPaidPolicyTests("runTest");f.setUp()
        try:
            f.convert();state=f.state();state["operating_daily_cap"]=100
            state["usage"]["2026-09-18"]={"attempted":100,"remaining":99900};f.save(state)
            t=FakeTransport([],clock=lambda:AT)
            r=old.run(f.root,Publisher(),t,clock=lambda:AT,allow_paid=True,sleeper=lambda _:None)
            self.assertEqual(r["requests_this_run"],0)
            r=parallel.acquire_batch(f.root,t,max_requests=4,max_bytes=400*1024*1024,deadline=9999,
                 day="2026-09-18",clock=lambda:AT,monotonic=lambda:0,sleeper=lambda _:None)
            self.assertEqual(r["requests"],0);self.assertEqual(t.paths,[])
        finally:f.tearDown()
    def test_invalid_cap_rejected(self):
        for value in (True,0,100001,"100"):
            with self.assertRaises(ValidationError):operating_daily_cap({"operating_daily_cap":value})
