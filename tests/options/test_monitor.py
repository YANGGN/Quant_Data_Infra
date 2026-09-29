"""Offline monitor boundaries; every store and credential fixture is temporary."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import json,os,tempfile,unittest
from quant_data.options.monitor.policy import load_policy,load_universe,validate_watchlist,market_window,previous_session,slot_for,NY
from quant_data.options.monitor.model import aggregate,derive,alert_for
from quant_data.options.monitor.store import MonitorStore
from quant_data.options.monitor.job import MonitorJob
from quant_data.options.monitor.site import connection,SiteClient
from quant_data.options.monitor.push import validate_subscription

FRIDAY=datetime(2026,9,25,14,35,tzinfo=timezone.utc)
SESSION="2026-09-25"

def rows(volume=100):
    base={"symbol":"SPY","expiration":"2026-10-02","strike":"700","timestamp":"2026-09-25T10:29:00-04:00"}
    return [dict(base,right="CALL",volume=volume,count=5),
            dict(base,right="PUT",volume=volume//2,count=3)]

class FakeTransport:
    def __init__(self,root,budget,*,symbols):
        self.budget=budget;self.calls=[];self.symbols=symbols
    def request(self,method,**params):
        self.budget.reserve_request();self.budget.add_bytes(40);self.calls.append((method,params))
        if method=="calendar_year":data=[{"date":"2026-01-01","type":"full_close"}]
        elif method=="calendar_on_date":
            data=[{"date":SESSION,"type":"open","open":"09:30:00","close":"16:00:00"}]
        elif method=="option_snapshot_ohlc":
            data=[{**r,"symbol":params["symbol"]} for r in rows()]
        elif method=="option_snapshot_open_interest":
            data=[{**r,"symbol":params["symbol"],"open_interest":1000,"timestamp":"2026-09-25T06:30:00-04:00"} for r in rows()]
        elif method=="option_snapshot_greeks_first_order":
            data=[]
        else:raise AssertionError(method)
        return data,{"method":method,"outcome":"success","captured_at":FRIDAY.isoformat(),
                     "rows":len(data),"response_bytes":40,"response_sha256":"a"*64,"messages":1,"elapsed_seconds":0}
    def close(self):pass

class FakeSite:
    def __init__(self):
        self.publications=[]
    def config_get(self):return {"schema_version":1,"watchlist":[],"push_subscriptions":[]}
    def publish(self,payload):
        self.publications.append(payload);return {"ok":True}

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="options-monitor-test-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/"config").mkdir()
        (self.root/"config/options_monitor.json").write_text(json.dumps({
            "contract":"quant_data.options_monitor.v1","schema_version":1,
            "max_symbols":3000,"watchlist_max":100,"cadences":[5,10,15,30],
            "max_requests_per_cycle":2500,"max_response_bytes_per_cycle":268435456,
            "max_seconds_per_cycle":240,"max_requests_per_session":60000,
            "max_response_bytes_per_session":8589934592,"max_workers":3,
            "retries":0,"baseline_min_sessions":20,"retain_sessions":90}))
        (self.root/"config/options_monitor_universe.json").write_text(json.dumps(
            {"schema_version":1,"members":[{"symbol":"SPY","name":"S&P 500","group":"market","cadence_minutes":5}]}))
        private=self.root/"data/.operations/options-monitor"
        private.mkdir(parents=True)
        path=private/"connection.json"
        path.write_text(json.dumps({"site_url":"https://quant-options-monitor.yanggainan.chatgpt.site",
             "dispatch_token":"d"*32,"collector_token":"c"*32}))
        path.chmod(0o600)
        self.site=FakeSite();self.transports=[]
    def job(self,clock=lambda:FRIDAY):
        def factory(root,budget,*,symbols):
            t=FakeTransport(root,budget,symbols=symbols);self.transports.append(t);return t
        return MonitorJob(self.root,transport_factory=factory,site_factory=lambda config:self.site,clock=clock)
    def test_before_and_after_market_have_no_provider_requests(self):
        for when in (datetime(2026,9,25,12,0,tzinfo=timezone.utc),
                     datetime(2026,9,25,21,0,tzinfo=timezone.utc)):
            self.site.publications.clear()
            result=self.job(clock=lambda when=when:when).run()
            self.assertEqual(result["status"],"market_closed")
            self.assertEqual(self.site.publications[-1]["collector"]["status"],"market_closed")
        self.assertEqual(self.transports,[])
    def test_weekend_has_no_provider_requests_and_sends_closed_heartbeat(self):
        saturday=FRIDAY+timedelta(days=1)
        result=self.job(clock=lambda:saturday).run()
        self.assertEqual(result["status"],"market_closed")
        self.assertEqual(self.transports,[])
        self.assertEqual(self.site.publications[0]["collector"]["status"],"market_closed")
    def test_history_sync_uses_closed_cycle_only_and_no_provider(self):
        job=self.job(clock=lambda:FRIDAY+timedelta(days=1))
        job.policy["daily_history"]={"enabled":True}
        with patch("quant_data.options.monitor.history.sync_history",return_value={"status":"up_to_date"}) as sync:
            result=job.run()
        self.assertEqual(result["daily_history"]["status"],"up_to_date")
        self.assertEqual(sync.call_args.kwargs["max_batches"],1)
        self.assertIn("deadline",sync.call_args.kwargs)
        self.assertEqual(self.transports,[])
        job=self.job();job.policy["daily_history"]={"enabled":True}
        with patch("quant_data.options.monitor.history.sync_history") as sync:
            result=job.run()
        sync.assert_not_called()
        self.assertEqual(result["observations"],1)
    def test_history_failure_does_not_turn_closed_cycle_into_live_work(self):
        job=self.job(clock=lambda:FRIDAY+timedelta(days=1))
        job.policy["daily_history"]={"enabled":True}
        with patch("quant_data.options.monitor.history.sync_history",side_effect=ValueError("fixture")):
            result=job.run()
        self.assertEqual(result["daily_history"],{"status":"unavailable","reason":"ValueError"})
        self.assertEqual(result["requests"],0)
        self.assertEqual(self.transports,[])
    def test_missing_runtime_dependency_does_not_reserve_provider_attempt(self):
        job=self.job()
        class MissingRuntime:
            @staticmethod
            def preflight():raise ModuleNotFoundError("dotenv")
            def __init__(self,*args,**kwargs):raise AssertionError("must not initialize")
        job.transport_factory=MissingRuntime
        with self.assertRaisesRegex(ModuleNotFoundError,"dotenv"):job.run()
        with job.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],0)
        heartbeat=self.site.publications[-1]["collector"]
        self.assertEqual(heartbeat["status"],"failed")
        self.assertNotIn("dotenv",heartbeat["message"])
        self.assertEqual(self.transports,[])

    def test_calendar_initialization_failure_is_reported_without_replay(self):
        job=self.job()
        def fail(*args,**kwargs):raise RuntimeError("private fixture detail")
        job.transport_factory=fail
        with self.assertRaises(RuntimeError):job.run()
        self.assertEqual(self.site.publications[-1]["collector"]["status"],"failed")
        self.assertNotIn("private fixture detail",json.dumps(self.site.publications))
        with job.store.read() as c:
            before=[tuple(row) for row in c.execute("SELECT * FROM attempts")]
        result=self.job().run()
        self.assertEqual(result["status"],"calendar_attempted")
        self.assertEqual(self.site.publications[-1]["collector"]["status"],"failed")
        self.assertEqual(self.transports,[])
        with job.store.read() as c:
            self.assertEqual([tuple(row) for row in c.execute("SELECT * FROM attempts")],before)

    def test_failure_heartbeat_outage_preserves_original_exception(self):
        job=self.job()
        class MissingRuntime:
            @staticmethod
            def preflight():raise ModuleNotFoundError("dotenv")
        job.transport_factory=MissingRuntime
        with patch.object(self.site,"publish",side_effect=RuntimeError("site offline")):
            with self.assertRaisesRegex(ModuleNotFoundError,"dotenv"):job.run()

    def test_single_slot_attempt_survives_restart(self):
        first=self.job().run()
        self.assertEqual(first["observations"],1)
        self.assertEqual(first["requests"],5)
        self.assertEqual(len(self.site.publications[-1]["observations"]),1)
        self.assertEqual(self.site.publications[-1]["observations"][0]["leaders"][0]["right"],"call")
        second=self.job().run()
        self.assertEqual(second["observations"],0)
        self.assertEqual(second["requests"],0)
        with MonitorStore(self.root).read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],5)
    def test_store_replay_and_checksum_guard(self):
        store=MonitorStore(self.root);store.initialize()
        current=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows())
        observation=derive(current,None,[],None,cadence=5)
        self.assertTrue(store.publish(observation))
        before=store.path.read_bytes()
        self.assertFalse(store.publish(observation))
        self.assertEqual(before,store.path.read_bytes())
        store.write("UPDATE metadata SET schema_sha256=?",("0"*64,))
        with self.assertRaisesRegex(ValueError,"checksum"):
            with store.read():pass
    def test_missing_zero_and_correction_suppress_intervals_and_alerts(self):
        prior=aggregate("SPY",SESSION,(FRIDAY-timedelta(minutes=5)).isoformat(),rows(100))
        current=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(80))
        result=derive(current,prior,[],None,cadence=5)
        self.assertIsNone(result["interval_volume"])
        self.assertIn("volume_correction",result["quality_flags"])
        self.assertIsNone(alert_for(result,None,enabled=True))
        missing=rows();missing[0]["volume"]=None
        value=aggregate("SPY",SESSION,FRIDAY.isoformat(),missing)
        self.assertIsNone(value["call_volume"])
        self.assertEqual(value["coverage"],"incomplete")
        self.assertEqual(derive(value,None,[],None,cadence=5)["status"],"incomplete")
        empty=aggregate("SPY",SESSION,FRIDAY.isoformat(),[])
        self.assertEqual(derive(empty,None,[],None,cadence=5)["status"],"no_data")
        zero=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(0))
        self.assertEqual(zero["call_volume"],0)
        self.assertIsNone(zero["top5_share"])
    def test_side_correction_suppresses_positive_total_and_alert(self):
        before=rows(1000);before[1]["volume"]=500
        after=rows(900);after[1]["volume"]=2500
        previous=aggregate("SPY",SESSION,(FRIDAY-timedelta(minutes=5)).isoformat(),before)
        current=aggregate("SPY",SESSION,FRIDAY.isoformat(),after)
        result=derive(current,previous,[{"interval_volume":50,"interval_minutes":5,"coverage":"complete","quality_flags":[]} for _ in range(20)],None,cadence=5)
        self.assertIsNone(result["interval_volume"])
        self.assertIsNone(result["relative_volume"])
        self.assertIn("volume_correction",result["quality_flags"])
        self.assertIsNone(alert_for(result,None,enabled=True))
    def test_iv_quote_time_symbol_and_matched_maturity(self):
        def quote(right,delta,iv,days=30,stamp="2026-09-25T10:34:00-04:00",symbol="SPY"):
            return {"symbol":symbol,"expiration":(FRIDAY.astimezone(NY).date()+timedelta(days=days)).isoformat(),
                    "strike":"100","right":right,"implied_vol":iv,"delta":delta,
                    "underlying_price":"100","underlying_timestamp":"2026-09-25T10:34:00-04:00",
                    "timestamp":stamp,"bid":"1.00","ask":"1.10"}
        call=quote("CALL",.25,.20);put=quote("PUT",-.25,.30)
        valid=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[call,put])
        self.assertAlmostEqual(valid["skew"],10)
        stale=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
            {**call,"timestamp":"2026-09-25T10:20:00-04:00"},
            {**put,"timestamp":"2026-09-25T10:20:00-04:00"}])
        self.assertIsNone(stale["skew"])
        self.assertIsNone(stale["atm_iv_30"])
        missing=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
            {**call,"timestamp":None},{**put,"timestamp":None}])
        self.assertIsNone(missing["atm_iv_30"])
        mismatched=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
            call,quote("PUT",-.25,.30,days=7)])
        self.assertIsNone(mismatched["skew"])
        bracketed=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
            quote("CALL",.25,.20,days=20),quote("PUT",-.25,.30,days=20),
            quote("CALL",.25,.25,days=40),quote("PUT",-.25,.35,days=40)])
        def fixed30(a,b):
            return ((a*a*20+(b*b*40-a*a*20)*.5)/30)**.5
        self.assertAlmostEqual(bracketed["skew"],(fixed30(.30,.35)-fixed30(.20,.25))*100)
        future=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
            {**call,"timestamp":"2026-09-25T10:36:00-04:00"}])
        self.assertIsNone(future["atm_iv_30"])
        with self.assertRaisesRegex(ValueError,"iv_cross_symbol"):
            aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),iv_rows=[
                quote("CALL",.25,.20,symbol="OTHER")])
    def test_prior_only_baseline_and_alert_gate(self):
        previous=aggregate("SPY",SESSION,(FRIDAY-timedelta(minutes=5)).isoformat(),rows(100))
        current=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(1100))
        bases=[{"interval_volume":50,"interval_minutes":5,"coverage":"complete","quality_flags":[]} for _ in range(19)]
        result=derive(current,previous,bases,None,cadence=5)
        self.assertIsNone(result["relative_volume"])
        result=derive(current,previous,bases+[{"interval_volume":50,"interval_minutes":5,"coverage":"complete","quality_flags":[]}],None,cadence=5)
        self.assertGreater(result["relative_volume"],3)
        self.assertIsNotNone(alert_for(result,None,enabled=True))
        self.assertIsNone(alert_for(result,FRIDAY.isoformat(),enabled=True))
    def test_scope_clock_and_connection_fail_closed(self):
        universe=load_universe(self.root/"config/options_monitor_universe.json")
        with self.assertRaisesRegex(ValueError,"site_watch_scope"):
            validate_watchlist({"schema_version":1,"watchlist":[{"symbol":"OTHER","cadence_minutes":5,"alerts_enabled":True}]},universe)
        with self.assertRaisesRegex(ValueError,"site_origin"):
            SiteClient({"site_url":"https://example.com","dispatch_token":"d"*32,"collector_token":"c"*32})
        credential=self.root/"data/.operations/options-monitor/connection.json"
        credential.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"permissions"):connection(credential)
        clock=market_window(FRIDAY.astimezone(NY).date(),
            [{"date":SESSION,"type":"early_close","open":"09:30:00","close":"13:00:00"}],extended=False)
        self.assertIsNone(slot_for(datetime(2026,9,25,18,0,tzinfo=timezone.utc),5,*clock))

    def test_prior_session_ohlc_does_not_poison_current_volume_or_identity(self):
        inactive={**rows()[0],"strike":"690","timestamp":"2026-09-24T15:00:00-04:00","volume":9000,"count":900}
        original=dict(inactive)
        first=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()+[inactive])
        self.assertEqual((first["call_volume"],first["put_volume"]),(100,50))
        self.assertEqual(first["coverage"],"complete")
        self.assertIn("prior_session_ohlc_excluded",first["quality_flags"])
        self.assertNotIn("future_row",first["quality_flags"])
        self.assertEqual(inactive,original)
        now=FRIDAY+timedelta(minutes=5)
        activated={**inactive,"timestamp":now.isoformat(),"volume":20,"count":2}
        second=aggregate("SPY",SESSION,now.isoformat(),rows(120)+[activated])
        self.assertEqual(first["_fingerprint"],second["_fingerprint"])
        previous=derive(first,None,[],None,cadence=5)
        result=derive(second,previous,[],None,cadence=5)
        self.assertEqual(result["interval_volume"],50)
        self.assertNotIn("contract_universe_changed",result["quality_flags"])

    def test_all_prior_session_or_unknown_time_is_not_current_zero_activity(self):
        old=[{**r,"timestamp":"2026-09-24T15:00:00-04:00"} for r in rows()]
        value=aggregate("SPY",SESSION,FRIDAY.isoformat(),old)
        self.assertIsNone(value["call_volume"])
        self.assertEqual(value["coverage"],"incomplete")
        for stamp in (None,"2026-09-25T10:00:00",(FRIDAY+timedelta(minutes=1)).isoformat()):
            with self.subTest(stamp=stamp):
                bad={**rows()[0],"strike":"690","timestamp":stamp}
                value=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()+[bad])
                self.assertIsNone(value["call_volume"])
                self.assertIn("future_row",value["quality_flags"])

    def test_prior_session_duplicates_and_population_removal_remain_guarded(self):
        old={**rows()[0],"strike":"690","timestamp":"2026-09-24T15:00:00-04:00"}
        first=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()+[old,dict(old)])
        self.assertEqual(first["_contracts"],3)
        with self.assertRaisesRegex(ValueError,"duplicate_conflict"):
            aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()+[old,{**old,"volume":99}])
        second=aggregate("SPY",SESSION,(FRIDAY+timedelta(minutes=5)).isoformat(),rows(120))
        result=derive(second,derive(first,None,[],None,cadence=5),[],None,cadence=5)
        self.assertIn("contract_universe_changed",result["quality_flags"])
        self.assertIsNone(result["interval_volume"])

    def test_job_uses_one_cycle_factory_for_control_and_workers(self):
        created=[];factory_calls=[];cycle_calls=[]
        def cycle_factory():
            cycle_calls.append(1)
            def create(root,budget,*,symbols):
                factory_calls.append(1)
                transport=FakeTransport(root,budget,symbols=symbols)
                created.append(transport)
                return transport
            return create
        class SessionFactory:
            session_factory=staticmethod(cycle_factory)
            def __init__(self,*args,**kwargs):raise AssertionError("unshared factory used")
        job=self.job();job.transport_factory=SessionFactory
        result=job.run()
        self.assertEqual(result["observations"],1)
        self.assertEqual(len(cycle_calls),1)
        self.assertEqual(len(factory_calls),2)
        self.assertEqual(sum(len(t.calls) for t in created),5)

    def test_calendar_year_prior_session_and_oi_report_time(self):
        from datetime import date
        holiday=[{"date":"2026-09-24","type":"full_close"}]
        self.assertEqual(previous_session(date(2026,9,25),{2026:holiday}).isoformat(),"2026-09-23")
        self.assertIsNone(previous_session(date(2026,9,25),{}))
        oi=[{**r,"open_interest":1000,"timestamp":"2026-09-25T06:30:00-04:00"} for r in rows()]
        value=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),oi_rows=oi,
            oi_effective_session="2026-09-23")
        self.assertEqual(value["oi_effective_date"],"2026-09-23")
        self.assertIsNotNone(value["volume_oi"])
        for row in oi:row["timestamp"]="2026-09-24T06:30:00-04:00"
        stale=aggregate("SPY",SESSION,FRIDAY.isoformat(),rows(),oi_rows=oi,
            oi_effective_session="2026-09-23")
        self.assertIsNone(stale["open_interest"])
    def test_push_allowlist_and_durable_dedup(self):
        sub={"id":"browser-1","endpoint":"https://fcm.googleapis.com/fcm/send/token",
             "keys":{"p256dh":"abc_123","auth":"def_456"}}
        self.assertEqual(validate_subscription(sub)["endpoint"],sub["endpoint"])
        for endpoint in ("https://127.0.0.1/push","https://example.com/push",
                         "https://fcm.googleapis.com.evil.test/push","http://fcm.googleapis.com/push"):
            with self.assertRaisesRegex(ValueError,"allowlist"):
                validate_subscription({**sub,"endpoint":endpoint})
        store=MonitorStore(self.root);store.initialize()
        self.assertTrue(store.reserve_push("a1","browser-1",FRIDAY.isoformat()))
        self.assertFalse(store.reserve_push("a1","browser-1",FRIDAY.isoformat()))
        store.finish_push("a1","browser-1","failed")
        with store.read() as c:
            self.assertEqual(c.execute("SELECT outcome FROM push_attempts").fetchone()[0],"failed")
    def test_three_workers_and_multiple_site_batches(self):
        members=[{"symbol":f"T{i:03d}","name":f"Ticker {i}","group":"equity","cadence_minutes":30}
                 for i in range(101)]
        (self.root/"config/options_monitor_universe.json").write_text(
            json.dumps({"schema_version":1,"members":members}))
        result=self.job(clock=lambda:FRIDAY+timedelta(minutes=20)).run()
        self.assertEqual(result["observations"],101,result)
        self.assertEqual(result["publish_batches"],2)
        self.assertLessEqual(len(self.transports),4) # one closed calendar client plus three workers
        self.assertEqual([len(p["observations"]) for p in self.site.publications],[100,1])
    def test_capacity_admits_priority_before_reserving_attempts(self):
        members=[{"symbol":"AAAA","name":"Ordinary","group":"equity","cadence_minutes":30},
                 {"symbol":"SPY","name":"S&P 500","group":"market","cadence_minutes":5}]
        (self.root/"config/options_monitor_universe.json").write_text(
            json.dumps({"schema_version":1,"members":members}))
        job=self.job(clock=lambda:FRIDAY+timedelta(minutes=20))
        with patch.object(job,"_usage",return_value=(59996,0)):
            result=job.run()
        self.assertEqual(result["requests"],4,result)
        self.assertEqual(result["capacity_deferred"],1)
        self.assertEqual([x["symbol"] for x in self.site.publications[-1]["observations"]],["SPY"])
        with MonitorStore(self.root).read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts WHERE symbol='AAAA'").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts WHERE symbol='SPY'").fetchone()[0],2)
    def test_unreceipted_attempt_charges_worst_case_session_bytes(self):
        store=MonitorStore(self.root);store.initialize()
        store.reserve(SESSION,"SPY","2026-09-25T10:35:00-04:00","option_snapshot_ohlc",FRIDAY.isoformat())
        used,received=self.job()._usage(SESSION)
        self.assertEqual(used,1)
        self.assertEqual(received,268435456)
    def test_retention_keeps_90_observations_but_only_7_request_sessions(self):
        store=MonitorStore(self.root);store.initialize()
        sessions=[];day=FRIDAY.date()-timedelta(days=145)
        while len(sessions)<96:
            if day.weekday()<5:sessions.append(day.isoformat())
            day+=timedelta(days=1)
        for session in sessions:
            observation=derive(aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()),None,[],None,cadence=5)
            observation.update(session=session,captured_at=session+"T14:35:00+00:00",_slot=session+"T14:35:00+00:00")
            store.reserve(session,"SPY","slot","test",session)
            store.finish(session,"SPY","slot","test","success",{"response_bytes":40})
            store.publish(observation)
        store.prune(sessions[-1])
        with store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],90)
            self.assertEqual(c.execute("SELECT MIN(session) FROM observations").fetchone()[0],sessions[-90])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],7)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],7)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],90)
            self.assertEqual(c.execute("SELECT response_bytes FROM (SELECT json_extract(payload,'$.response_bytes') AS response_bytes FROM receipts WHERE session=?)",(sessions[-1],)).fetchone()[0],40)
        store.prune(sessions[-1])
        self.assertTrue(store.attempted(sessions[-1],"SPY","slot","test"))

    def test_acknowledgment_removes_delivery_copy_without_losing_replay_guard(self):
        store=MonitorStore(self.root);store.initialize()
        observation=derive(aggregate("SPY",SESSION,FRIDAY.isoformat(),rows()),None,[],None,cadence=5)
        store.publish(observation)
        pending=store.pending("observation",FRIDAY.isoformat())
        self.assertEqual(len(pending),1)
        store.mark_published([pending[0][0]])
        self.assertEqual(store.pending("observation",FRIDAY.isoformat()),[])
        self.assertFalse(store.publish(observation))
        with store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)

    def test_personal_push_opt_out_does_not_suppress_another_user(self):
        import time
        job=self.job();job.store.initialize();sent=[]
        job.push_sender=lambda subscription,alert,key:sent.append(subscription["id"])
        base={"endpoint":"https://fcm.googleapis.com/test","keys":{"p256dh":"test","auth":"test"}}
        subscriptions=[dict(base,id="off",disabled_symbols=["SPY"]),dict(base,id="on",disabled_symbols=[])]
        alert={"id":"personal-alert","symbol":"SPY","title":"Test","reason":"Test"}
        self.assertEqual(job._push([alert],subscriptions,"fixture",time.monotonic()+60),1)
        self.assertEqual(sent,["on"])

    def test_fair_due_slot_moves_and_caps(self):
        self.job().run()
        later=FRIDAY+timedelta(minutes=5)
        result=self.job(clock=lambda:later).run()
        self.assertEqual(result["observations"],1)
        self.assertEqual(result["requests"],2)
        with MonitorStore(self.root).read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],7)

if __name__=="__main__":unittest.main()
