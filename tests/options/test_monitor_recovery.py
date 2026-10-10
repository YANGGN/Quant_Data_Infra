"""Offline failure-path coverage for the explicitly enabled baseline recovery policy."""
import json,tempfile,threading,unittest
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from quant_data.options.monitor.backfill import ParallelBackfill
from quant_data.options.monitor.baseline_store import BaselineStore,CHECKSUM,RETRY_POLICY,attempt_part
from quant_data.options.transport import ProviderFailure
from tests.options.test_monitor_baseline import bar,day

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=BaselineStore(Path(self.temp.name));self.store.initialize_baseline()
        self.now=datetime(2026,9,30,21,tzinfo=timezone.utc);self.waits=[];self.events=[]
    def plan(self,symbols=("SPY",),requests=1000,pilot=True,deadline="2026-09-30T23:00:00+00:00"):
        return self.store.create_run({"symbols":list(symbols),"sessions":[{"session":"2026-09-25","close_minute":960}],
            "max_requests":requests,"max_bytes":1000000,"pilot":pilot,"deadline":deadline})
    def sleep(self,seconds):
        self.waits.append(seconds);self.now+=timedelta(seconds=seconds)
    def provider(self,choose):
        state={"calls":[],"active":0,"peak":0,"closed":0,"factories":0};lock=threading.Lock()
        def provider():
            state["factories"]+=1
            def create(root,budget,**kwargs):
                class Transport:
                    def request(inner,method,**params):
                        budget.reserve_request();budget.add_bytes(10)
                        key=(params["symbol"],params["date"].isoformat(),"catalog" if method=="option_list_contracts" else params["expiration"].isoformat())
                        with lock:
                            state["calls"].append(key);attempt=state["calls"].count(key)
                            state["active"]+=1;state["peak"]=max(state["peak"],state["active"])
                        try:
                            code=choose(key,attempt)
                            if code:raise ProviderFailure(code,{"response_bytes":10,"outcome":code,"elapsed_seconds":0})
                            return [bar(symbol=key[0],expiration=key[1],timestamp=key[1]+"T09:30:00-04:00")],{"response_bytes":10}
                        finally:
                            with lock:state["active"]-=1
                    def close(inner):
                        if state["active"]:raise AssertionError("closed with active requests")
                        state["closed"]+=1
                return Transport()
            return create
        return provider,state
    def site(self):
        class Site:
            def request(inner,path,payload,**kwargs):return {"ok":True,"days":len(payload["days"])}
        return Site()
    def job(self,identity,provider,workers=2):
        return ParallelBackfill(self.store,identity,workers=workers,api_concurrency=min(workers,4),
            factory_provider=provider,clock=lambda:self.now,sleep=self.sleep,wait=True)
    def enable(self,identity):
        original=self.store.plan(identity);self.store.configure_recovery(identity)
        self.assertEqual(self.store.plan(identity),original)
    def test_isolated_transient_retries_and_preserves_receipts(self):
        identity=self.plan();self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAVAILABLE" if key[2]!="catalog" and n==1 else None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertTrue(result["upload_complete"]);self.assertEqual(result["saved_days"],1)
        self.assertEqual(len(state["calls"]),3);self.assertGreaterEqual(sum(self.waits),5)
        self.assertEqual(self.store.usage(identity,1000000),(3,30))
        with self.store.read() as c:
            rows=[tuple(r) for r in c.execute("SELECT part,outcome,payload FROM baseline_requests ORDER BY part")]
        self.assertIn(("2026-09-25","ProviderFailure",None),rows)
        self.assertIn(("2026-09-25#retry2","success",None),rows)
        self.assertTrue(any(x.get("phase")=="retrying" for x in self.events))
    def test_exhausted_ticker_does_not_stop_other_tickers(self):
        identity=self.plan(("SPY","QQQ"));self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAVAILABLE" if key[0]=="SPY" else None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertTrue(result["upload_complete"]);self.assertEqual(result["saved_days"],1)
        self.assertEqual(result["skipped_days"],1);self.assertTrue(self.store.has_day("QQQ","2026-09-25"))
        self.assertEqual(state["calls"].count(("SPY","2026-09-25","catalog")),3)
        before=len(state["calls"])
        self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(len(state["calls"]),before)
        self.assertEqual(self.store.usage(identity,1000000),(5,50))
    def test_resume_prior_failed_part_reuses_successful_catalog(self):
        identity=self.plan();self.enable(identity);stamp=self.now.isoformat()
        self.store.reserve_part("SPY","2026-09-25","catalog",identity,stamp)
        self.store.finish_part("SPY","2026-09-25","catalog","success",["2026-09-25"],{"response_bytes":10})
        self.store.reserve_part("SPY","2026-09-25","2026-09-25",identity,stamp)
        self.store.finish_part("SPY","2026-09-25","2026-09-25","ProviderFailure",None,{"response_bytes":0,"outcome":"UNAVAILABLE"})
        with self.store.read() as c:
            original=tuple(c.execute("SELECT * FROM baseline_requests WHERE part='2026-09-25'").fetchone())
        provider,state=self.provider(lambda key,n:None)
        self.assertEqual(self.store.collection_status(identity)["retry_pending_days"],1)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["calls"],[("SPY","2026-09-25","2026-09-25")])
        self.assertEqual(result["saved_days"],1);self.assertEqual(self.store.usage(identity,1000000),(3,20))
        with self.store.read() as c:self.assertEqual(tuple(c.execute("SELECT * FROM baseline_requests WHERE part='2026-09-25'").fetchone()),original)
    def test_restart_delay_starts_after_authentication_and_failed_rpc(self):
        identity=self.plan();self.enable(identity);admitted=self.now
        self.store.reserve_part("SPY","2026-09-25","catalog",identity,admitted.isoformat())
        self.store.finish_part("SPY","2026-09-25","catalog","ProviderFailure",None,
            {"response_bytes":0,"outcome":"UNAVAILABLE","captured_at":(admitted+timedelta(seconds=10)).isoformat(),"elapsed_seconds":1})
        self.now=admitted+timedelta(seconds=11)
        provider,state=self.provider(lambda key,n:None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(sum(self.waits),5)
        self.assertEqual(result["saved_days"],1);self.assertEqual(len(state["calls"]),2)

    def test_request_cap_counts_retries_and_survives_restart(self):
        identity=self.plan(requests=2);self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAVAILABLE")
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"request_cap"):
                self.job(identity,provider).run(lambda:self.fail("upload before collection"),report=self.events.append)
        self.assertEqual(len(state["calls"]),2);self.assertEqual(self.store.usage(identity,1000000),(2,20))
    def test_retry_waits_through_market_hours(self):
        identity=self.plan(pilot=False,deadline="2026-10-01T23:00:00+00:00");self.enable(identity)
        def choose(key,n):
            if len(state["calls"])==1:
                self.now=datetime(2026,10,1,13,tzinfo=timezone.utc);return "UNAVAILABLE"
            self.assertGreaterEqual(self.now.hour,21);return None
        provider,state=self.provider(choose)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(len(state["calls"]),3)
        self.assertTrue(any(x.get("reason")=="market_hours" for x in self.events))
    def test_permanent_authentication_error_still_stops(self):
        identity=self.plan();self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAUTHENTICATED")
        with self.assertRaises(ProviderFailure):
            self.job(identity,provider).run(lambda:self.fail("upload on auth error"),report=self.events.append)
        self.assertEqual(len(state["calls"]),1);self.assertEqual(state["active"],0)
    def test_unresolved_or_unknown_transfer_never_retried(self):
        for outcome,receipt in (("started",None),("ProviderFailure",{"outcome":"UNAVAILABLE","response_bytes":None})):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as directory:
                self.store=BaselineStore(Path(directory));self.store.initialize_baseline()
                identity=self.plan();self.enable(identity)
                self.store.reserve_part("SPY","2026-09-25","catalog",identity,self.now.isoformat())
                if receipt:self.store.finish_part("SPY","2026-09-25","catalog",outcome,None,receipt)
                provider,state=self.provider(lambda key,n:None)
                with self.assertRaisesRegex(RuntimeError,"response_byte_cap"):
                    self.job(identity,provider).run(self.site,report=self.events.append)
                self.assertEqual(state["calls"],[]);self.assertEqual(self.store.usage(identity,1000000),(1,1000000))
    def test_other_run_failure_is_excluded(self):
        original=self.plan(requests=5)
        self.store.reserve_part("SPY","2026-09-25","catalog",original,self.now.isoformat())
        self.store.finish_part("SPY","2026-09-25","catalog","ProviderFailure",None,{"response_bytes":0,"outcome":"UNAVAILABLE"})
        current=self.plan(requests=100);self.enable(current)
        provider,state=self.provider(lambda key,n:None)
        result=self.job(current,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["skipped_days"],1);self.assertEqual(state["calls"],[])
    def test_sustained_outage_opens_longer_cooldown(self):
        identity=self.plan(tuple("S"+str(i) for i in range(6)));self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAVAILABLE")
        result=self.job(identity,provider,workers=4).run(self.site,report=self.events.append)
        self.assertEqual(result["skipped_days"],6);self.assertEqual(len(state["calls"]),18)
        self.assertLessEqual(state["peak"],4);self.assertEqual(state["active"],0)
        waits=[x for x in self.events if x.get("reason")=="provider_backoff"]
        self.assertTrue(waits);self.assertGreaterEqual(sum(self.waits),60)
        with self.store.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome='started'").fetchone()[0],0)
    def test_deadline_during_backoff_stops_without_retry(self):
        identity=self.plan(deadline=(self.now+timedelta(seconds=1)).isoformat());self.enable(identity)
        provider,state=self.provider(lambda key,n:"UNAVAILABLE")
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):
            self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(len(state["calls"]),1)
    def test_recovery_policy_replay_and_schema_preservation(self):
        identity=self.plan();self.enable(identity);self.enable(identity)
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT checksum FROM baseline_metadata").fetchone()[0],CHECKSUM)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_runs").fetchone()[0],2)
        self.assertEqual(self.store.recovery_policy(identity),RETRY_POLICY)
    def test_completion_gate_recovery_in_progress(self):
        identity=self.plan();self.enable(identity);symbol="SPY";session="2026-09-25";part="catalog"
        self.store.reserve_part(symbol,session,part,identity,self.now.isoformat())
        self.store.finish_part(symbol,session,part,"ProviderFailure",None,{"response_bytes":0,"outcome":"UNAVAILABLE"})
        status=self.store.collection_status(identity)
        self.assertEqual((status["pending_days"],status["skipped_days"],status["retry_pending_days"]),(1,0,1))
        self.store.reserve_retry(symbol,session,part,identity,self.now.isoformat(),2)
        self.assertEqual(self.store.collection_status(identity)["unresolved_days"],1)
        with self.assertRaisesRegex(ValueError,"forbidden"):self.store.reserve_retry(symbol,session,part,identity,self.now.isoformat(),3)
        self.store.finish_part(symbol,session,attempt_part(part,2),"success",[session],{"response_bytes":10})
        self.assertEqual(self.store.collection_status(identity)["pending_days"],1)
        self.store.save_day(day());self.assertTrue(self.store.collection_status(identity)["collection_complete"])
