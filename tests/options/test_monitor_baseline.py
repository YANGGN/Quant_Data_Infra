import copy,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from quant_data.options.monitor.baseline import *
from quant_data.options.monitor.baseline_store import BaselineStore
from quant_data.options.monitor.backfill import Backfill,ParallelBackfill,MarketPause,BaselineThetaTransport,outside_session,backfill_window
from quant_data.options.monitor.model import derive,canonical
from quant_data.options.transport import ProviderFailure

def day(session="2026-09-25",symbol="SPY",value=10,close=960):
    return {"version":VERSION,"symbol":symbol,"session":session,"close_minute":close,
        "bins":[[m,value,2*value] for m in range(570,close,5)]}
def bar(**changes):
    return {"symbol":"SPY","expiration":"2026-09-25","strike":600,"right":"CALL",
        "timestamp":"2026-09-25T09:30:00-04:00","volume":10,**changes}
class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=BaselineStore(self.root);self.store.initialize_baseline()
    def test_old_schema_and_additive_replay(self):
        self.assertFalse(self.store.initialize_baseline())
        with self.store.read() as c:self.assertEqual(c.execute("SELECT version FROM metadata").fetchone()[0],1)
    def test_ohlc_alignment_and_zero_bars(self):
        bins=aggregate_expiration([bar(),bar(right="PUT",volume=7)],"SPY","2026-09-25","2026-09-25",960)
        self.assertEqual(bins[0],[570,10,7]);self.assertEqual(bins[1],[575,0,0]);self.assertEqual(len(bins),78)
    def test_bad_provider_rows_are_not_zero(self):
        bad=[[],[bar(),bar()],[bar(volume=None)],[bar(volume=-1)],[bar(volume=1.5)],
            [bar(symbol="QQQ")],[bar(timestamp="2026-09-25T09:31:00-04:00")],
            [bar(timestamp="2026-09-24T09:30:00-04:00")],[bar(timestamp="2026-09-25T09:30:00")],
            [bar(expiration="2026-10-02")]]
        for rows in bad:
            with self.subTest(rows=rows),self.assertRaises(ValueError):aggregate_expiration(rows,"SPY","2026-09-25","2026-09-25",960)
    def test_short_session_does_not_invent_afternoon(self):
        d=day(close=780);self.assertIsNone(seed_interval(d,30,800))
        self.assertEqual(seed_interval(d,30,780)["interval_volume"],180)
    def test_shifted_thirty_minute_windows(self):
        self.assertEqual(seed_interval(day(),30,605)["interval_volume"],180)
        self.assertIsNone(seed_interval(day(),30,590))
        self.assertIsNone(seed_interval(day(),10,607))
    def test_calendar_holiday_and_early_close(self):
        rows={2026:[{"date":"2026-09-07","type":"full_close"},{"date":"2026-09-04","type":"early_close"}]}
        self.assertEqual(completed_sessions("2026-09-08",rows,1),[{"session":"2026-09-04","close_minute":780}])
        with self.assertRaises(ValueError):completed_sessions("2026-09-08",{},1)
    def test_immutable_days_and_no_live_side_effects(self):
        self.assertTrue(self.store.save_day(day()));self.assertFalse(self.store.save_day(day()))
        with self.assertRaisesRegex(ValueError,"conflict"):self.store.save_day(day(value=11))
        with self.store.read() as c:
            for table in ("observations","alerts","outbox"):
                self.assertEqual(c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)
    def test_prior_sessions_only_and_live_precedence(self):
        self.store.save_day(day());self.store.save_day(day("2026-09-28"))
        values=self.store.baselines("SPY",5,575,"2026-09-28")
        self.assertEqual([v["interval_volume"] for v in values],[30])
        payload={"symbol":"SPY","session":"2026-09-25","captured_at":"2026-09-25T09:35:00-04:00",
            "cadence_minutes":5,"coverage":"complete","interval_volume":99,"interval_minutes":5,
            "interval_start":"2026-09-25T09:30:00-04:00","quality_flags":[],"_fingerprint":"x"}
        self.store.publish(payload)
        values=self.store.baselines("SPY",5,575,"2026-09-28")
        self.assertEqual([v["interval_volume"] for v in values],[99])
        self.assertNotIn("_seed_end_minute",values[0])
    def test_seed_alignment(self):
        a=datetime.fromisoformat("2026-09-25T09:30:30-04:00");b=a+timedelta(minutes=5)
        self.assertTrue(seed_aligned(a,b,5,575));self.assertFalse(seed_aligned(a+timedelta(minutes=2),b+timedelta(minutes=2),5,575))
    def test_retention_and_admission_receipts(self):
        for i in range(95):self.store.save_day(day((datetime(2026,1,1)+timedelta(days=i)).date().isoformat()))
        self.store.reserve_part("SPY","2026-01-01","catalog","run","2026-05-01T00:00:00Z")
        self.store.prune("2026-05-01")
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_days").fetchone()[0],90)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests").fetchone()[0],1)
    def test_publish_ack_and_failure(self):
        self.store.save_day(day())
        class Site:
            def request(self,path,payload,**kwargs):
                self.path=path;return {"ok":True,"days":len(payload["days"])}
        site=Site();self.assertEqual(self.store.publish_days(site),1);self.assertEqual(self.store.pending_days(),[])
        self.assertEqual(site.path,"/api/collector/baselines")
    def plan(self,max_requests=5):
        return self.store.create_run({"symbols":["SPY"],"sessions":[{"session":"2026-09-25","close_minute":960}],
            "max_requests":max_requests,"max_bytes":1000000,"pilot":True,"deadline":"2026-09-30T23:00:00+00:00"})
    def test_checkpoint_resume_without_provider_repeat(self):
        calls=[]
        class Transport:
            def __init__(self,root,budget,**kw):self.budget=budget
            def close(self):pass
            def request(self,method,**params):
                self.budget.reserve_request();self.budget.add_bytes(10);calls.append(method)
                return [bar()],{"outcome":"success","response_bytes":10}
        identity=self.plan()
        for _ in range(2):
            job=Backfill(self.store,identity,factory=Transport,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
            job.day("SPY","2026-09-25",960);job.close()
        self.assertEqual(calls,["option_list_contracts","option_history_ohlc"])
        self.assertEqual(self.store.usage(identity,1000000),(2,20))
    def test_crashed_request_is_not_retried(self):
        identity=self.plan();self.store.reserve_part("SPY","2026-09-25","catalog",identity,"now")
        job=Backfill(self.store,identity,factory=lambda *a,**k:self.fail("network"),clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError,"unusable"):job.day("SPY","2026-09-25",960)
        self.assertEqual(job.budget.received_bytes,1000000)
    def test_resume_does_not_reset_request_cap(self):
        identity=self.plan(1);self.store.reserve_part("SPY","2026-09-25","catalog",identity,"now")
        self.store.finish_part("SPY","2026-09-25","catalog","success",["2026-09-25"],{"response_bytes":10})
        job=Backfill(self.store,identity,factory=lambda *a,**k:self.fail("network"),clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        with self.assertRaisesRegex(RuntimeError,"request_cap"):job.day("SPY","2026-09-25",960)
        self.assertFalse(self.store.has_day("SPY","2026-09-25"))
    def test_failure_does_not_publish_partial_chain(self):
        calls=[]
        class Transport:
            def __init__(self,root,budget,**kw):pass
            def close(self):pass
            def request(self,method,**params):
                calls.append(method)
                if method=="option_list_contracts":return [bar(),bar(expiration="2026-10-02")],{"response_bytes":10}
                if params["expiration"].isoformat()=="2026-10-02":raise ProviderFailure("unavailable",{"response_bytes":10})
                return [bar()],{"response_bytes":10}
        identity=self.plan();job=Backfill(self.store,identity,factory=Transport,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        with self.assertRaises(ProviderFailure):job.day("SPY","2026-09-25",960)
        self.assertFalse(self.store.has_day("SPY","2026-09-25"));self.assertEqual(self.store.pending_days(),[])
    def test_transport_rejects_broadening_before_auth(self):
        transport=BaselineThetaTransport.__new__(BaselineThetaTransport);transport.symbols={"SPY"}
        with self.assertRaises(ValueError):transport.request("option_snapshot_ohlc",symbol="SPY",date=date(2026,9,25))
        with self.assertRaises(ValueError):transport.request("option_list_contracts",symbol="QQQ",date=date(2026,9,25),request_type="trade")
    def test_off_hours_guard(self):
        self.assertFalse(outside_session(datetime(2026,9,30,14,tzinfo=timezone.utc)))
        self.assertTrue(outside_session(datetime(2026,9,30,21,tzinfo=timezone.utc)))

    def test_publication_frozen_deadline_and_timeout(self):
        identity=self.plan();self.store.save_day(day())
        class Site:
            def request(self,path,payload,**kwargs):
                self.timeout=kwargs["timeout"];return {"ok":True,"days":len(payload["days"])}
        site=Site()
        job=Backfill(self.store,identity,clock=lambda:datetime(2026,9,30,22,59,58,tzinfo=timezone.utc))
        self.assertEqual(job.publish(site),1);self.assertEqual(site.timeout,2)
        job.clock=lambda:datetime(2026,9,30,23,tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):job.publish(site)

    def test_checkpoint_read_waits_for_active_writer(self):
        import sqlite3,threading,time
        from quant_data.stores import StoreWriteLock
        ready=threading.Event()
        def writer():
            with StoreWriteLock(self.store.path):
                c=sqlite3.connect(self.store.path)
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO baseline_runs VALUES('concurrent','{}')")
                ready.set();time.sleep(.1);c.commit();c.close()
        thread=threading.Thread(target=writer);thread.start()
        self.assertTrue(ready.wait(2))
        try:self.assertEqual(self.store.plan("concurrent"),{})
        finally:thread.join()

    def test_publication_rechecks_deadline_after_waiting_for_read(self):
        identity=self.plan();self.store.save_day(day())
        now=[datetime(2026,9,30,22,59,58,tzinfo=timezone.utc)]
        job=Backfill(self.store,identity,clock=lambda:now[0])
        original=self.store.pending_days
        def delayed(limit=100,**kwargs):
            days=original(limit,**kwargs);now[0]+=timedelta(seconds=3);return days
        self.store.pending_days=delayed
        class Site:
            def request(self,*args,**kwargs):raise AssertionError("late POST")
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):job.publish(Site())

    def test_offhours_wait_discards_old_auth_before_next_request(self):
        plan=self.store.plan(self.plan());plan["pilot"]=False
        identity=self.store.create_run(plan)
        now=[datetime(2026,9,30,14,tzinfo=timezone.utc)];closed=[];created=[]
        class Old:
            def close(self):closed.append(True)
        class Transport:
            def __init__(self,*args,**kw):created.append(True)
            def request(self,*args,**kw):return [bar()],{"response_bytes":10}
            def close(self):pass
        job=Backfill(self.store,identity,factory=Transport,clock=lambda:now[0],wait=True,
            sleep=lambda _:now.__setitem__(0,datetime(2026,9,30,21,tzinfo=timezone.utc)))
        job.transport=Old()
        result=job.request("SPY","2026-09-25","catalog","option_list_contracts",lambda rows:expirations(rows,"SPY","2026-09-25"),symbol="SPY",date=date(2026,9,25),request_type="trade")
        self.assertEqual(result,["2026-09-25"]);self.assertEqual(closed,[True]);self.assertEqual(created,[True])
        job.close()


class ParallelBaselineTests(unittest.TestCase):
    setUp=BaselineTests.setUp
    def parallel_plan(self,requests=100,sessions=12,pilot=True):
        days=[{"session":(date(2026,9,25)-timedelta(days=i)).isoformat(),"close_minute":960} for i in range(sessions)]
        identity=self.store.create_run({"symbols":["SPY"],"sessions":days,"max_requests":requests,
            "max_bytes":1000000,"pilot":pilot,"deadline":"2026-09-30T23:00:00+00:00"})
        return identity

    def fake_provider(self,*,fail=False,on_request=None,synchronize=False):
        import threading,time
        state={"active":0,"peak":0,"calls":0,"factories":0,"created":[],"closed":[],"threads":set()}
        lock=threading.Lock();barrier=threading.Barrier(4) if synchronize else None
        def provider():
            state["factories"]+=1
            def factory(root,budget,**kwargs):
                class Transport:
                    def request(self,method,**params):
                        with lock:
                            state["active"]+=1;state["peak"]=max(state["peak"],state["active"])
                            state["calls"]+=1;call_number=state["calls"];state["threads"].add(threading.get_ident())
                        try:
                            budget.reserve_request()
                            if barrier is not None and call_number<=4:barrier.wait(timeout=5)
                            time.sleep(.015);budget.add_bytes(10)
                            if on_request:on_request()
                            if fail:raise ProviderFailure("UNAVAILABLE",{"response_bytes":10})
                            session=params["date"].isoformat()
                            return [bar(symbol=params["symbol"],expiration=session,timestamp=session+"T09:30:00-04:00")],{"response_bytes":10}
                        finally:
                            with lock:state["active"]-=1
                    def close(self):
                        self.assert_drained=state["active"]==0
                        if not self.assert_drained:raise AssertionError("closed an active pool")
                        state["closed"].append(self)
                value=Transport();state["created"].append(value);return value
            return factory
        return provider,state

    def fake_site(self):
        class Site:
            def __init__(self):self.calls=[]
            def request(self,path,payload,**kw):
                self.calls.append(payload);return {"ok":True,"days":len(payload["days"])}
        return Site()

    def test_ten_workers_four_calls_receipts_and_final_publication(self):
        identity=self.parallel_plan();factory,state=self.fake_provider(synchronize=True);site=self.fake_site()
        job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        result=job.run(lambda:site,report=lambda _:None);job.close()
        self.assertEqual((result["added"],result["skipped"]),(12,0));self.assertTrue(result["collection_complete"]);self.assertTrue(result["upload_complete"])
        self.assertEqual(state["peak"],4);self.assertGreater(len(state["threads"]),4)
        self.assertEqual(state["calls"],24);self.assertEqual(state["factories"],1)
        self.assertEqual(len(state["closed"]),len(state["created"]))
        self.assertEqual(self.store.usage(identity,1000000),(24,240))
        self.assertEqual(len(site.calls),1);self.assertEqual(len(site.calls[0]["days"]),12)
        with self.store.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome='started'").fetchone()[0],0)
        resumed=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        resumed.run(lambda:site,report=lambda _:None);resumed.close();self.assertEqual(state["calls"],24)

    def test_concurrent_request_cap_preserved_across_resume(self):
        identity=self.parallel_plan(requests=3);factory,state=self.fake_provider()
        for _ in range(2):
            job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
            with self.assertRaisesRegex(RuntimeError,"request_cap"):job.run(self.fake_site,report=lambda _:None)
            job.close()
        self.assertEqual(state["calls"],3);self.assertEqual(self.store.usage(identity,1000000),(3,30))
        with self.store.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome='started'").fetchone()[0],0)

    def test_failure_drains_admitted_calls_and_never_retries(self):
        identity=self.parallel_plan();factory,state=self.fake_provider(fail=True)
        job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        with self.assertRaises(ProviderFailure):job.run(self.fake_site,report=lambda _:None)
        job.close();self.assertLessEqual(state["calls"],4);self.assertEqual(state["active"],0)
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome='started'").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_days").fetchone()[0],0)
        self.assertEqual(self.store.usage(identity,1000000),(state["calls"],state["calls"]*10))

    def test_graceful_stop_drains_requests_before_closing(self):
        identity=self.parallel_plan();holder={}
        factory,state=self.fake_provider(on_request=lambda:holder["job"].stopping.set())
        job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc));holder["job"]=job
        with self.assertRaisesRegex(RuntimeError,"baseline_stopped"):job.run(self.fake_site,report=lambda _:None)
        job.close();self.assertLessEqual(state["calls"],4)
        with self.store.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome='started'").fetchone()[0],0)

    def test_daytime_drains_and_renews_session_without_repeating_parts(self):
        identity=self.parallel_plan(sessions=5,pilot=False)
        now=[datetime(2026,9,30,12,54,tzinfo=timezone.utc)];changed=[False]
        def shift():
            if not changed[0]:changed[0]=True;now[0]=datetime(2026,9,30,14,tzinfo=timezone.utc)
        factory,state=self.fake_provider(on_request=shift)
        job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:now[0],wait=True,
            sleep=lambda _:now.__setitem__(0,datetime(2026,9,30,21,tzinfo=timezone.utc)))
        result=job.run(self.fake_site,report=lambda _:None);job.close()
        self.assertEqual(result["added"],5);self.assertEqual(state["calls"],10);self.assertEqual(state["factories"],2)
        self.assertEqual(self.store.usage(identity,1000000),(10,100))

    def test_resume_with_all_days_saved_uploads_without_fetch(self):
        identity=self.parallel_plan(sessions=1);self.store.save_day(day())
        site=self.fake_site()
        def factory():return lambda *a,**k:self.fail("completed day fetched again")
        job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        job.run(lambda:site,report=lambda _:None);job.close()
        self.assertEqual(len(site.calls),1);self.assertEqual(self.store.pending_days(),[])

    def test_slow_day_does_not_block_next_ticker(self):
        import threading
        plan=self.store.plan(self.parallel_plan(sessions=2));plan["symbols"]=["SPY","QQQ"]
        identity=self.store.create_run(plan);next_ticker=threading.Event();started=[]
        first_session=plan["sessions"][0]["session"]
        def provider():
            def factory(root,budget,**kwargs):
                class Transport:
                    def request(self,method,**params):
                        budget.reserve_request();budget.add_bytes(10)
                        symbol=params["symbol"];session=params["date"].isoformat()
                        started.append((symbol,session,method))
                        if symbol=="QQQ":next_ticker.set()
                        if symbol=="SPY" and session==first_session and method=="option_history_ohlc":
                            if not next_ticker.wait(3):raise AssertionError("ticker barrier stalled idle worker")
                        return [bar(symbol=symbol,expiration=session,timestamp=session+"T09:30:00-04:00")],{"response_bytes":10}
                    def close(self):pass
                return Transport()
            return factory
        job=ParallelBackfill(self.store,identity,workers=2,api_concurrency=2,factory_provider=provider,
            clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        result=job.run(self.fake_site,report=lambda _:None)
        self.assertEqual(result["added"],4);self.assertTrue(result["upload_complete"])
        self.assertEqual(len(started),8);self.assertEqual(len(set(started)),8)
        self.assertTrue(next_ticker.is_set())

    def test_worker_channel_reused_across_tickers(self):
        plan=self.store.plan(self.parallel_plan(sessions=2));plan["symbols"]=["SPY","QQQ","IWM"]
        identity=self.store.create_run(plan);factory,state=self.fake_provider()
        job=ParallelBackfill(self.store,identity,workers=1,api_concurrency=1,factory_provider=factory,
            clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        result=job.run(self.fake_site,report=lambda _:None)
        self.assertEqual(result["added"],6);self.assertEqual(state["calls"],12)
        self.assertEqual(len(state["created"]),1);self.assertEqual(len(state["closed"]),1)
        self.assertEqual(state["factories"],1);self.assertEqual(state["active"],0)

    def test_pause_drains_across_ticker_boundary_without_repeat(self):
        plan=self.store.plan(self.parallel_plan(sessions=1,pilot=False));plan["symbols"]=["SPY","QQQ","IWM"]
        identity=self.store.create_run(plan)
        now=[datetime(2026,9,30,12,54,tzinfo=timezone.utc)];changed=[False]
        def shift():
            if not changed[0]:changed[0]=True;now[0]=datetime(2026,9,30,14,tzinfo=timezone.utc)
        factory,state=self.fake_provider(on_request=shift)
        job=ParallelBackfill(self.store,identity,workers=2,api_concurrency=2,factory_provider=factory,
            clock=lambda:now[0],wait=True,sleep=lambda _:now.__setitem__(0,datetime(2026,9,30,21,tzinfo=timezone.utc)))
        result=job.run(self.fake_site,report=lambda _:None)
        self.assertEqual(result["added"],3);self.assertEqual(state["calls"],6)
        self.assertEqual(state["factories"],2);self.assertEqual(state["active"],0)
        self.assertEqual(len(state["created"]),len(state["closed"]))
        self.assertEqual(self.store.usage(identity,1000000),(6,60))

    def test_standard_cap_and_worker_limit(self):
        identity=self.parallel_plan()
        for workers,api in ((11,4),(10,5),(0,4),(2,4)):
            with self.assertRaisesRegex(ValueError,"concurrency"):
                ParallelBackfill(self.store,identity,workers=workers,api_concurrency=api)

    def test_reserved_windows_for_existing_theta_units(self):
        for stamp in ("2026-09-30T08:55:00-04:00","2026-09-30T16:59:59-04:00","2026-09-30T07:10:00-04:00","2026-09-30T07:39:59-04:00","2026-10-04T02:55:00-04:00","2026-10-04T04:04:59-04:00"):
            self.assertFalse(backfill_window(datetime.fromisoformat(stamp)))
        for stamp in ("2026-09-30T08:54:59-04:00","2026-09-30T17:00:00-04:00","2026-09-30T07:09:59-04:00","2026-09-30T07:40:00-04:00","2026-10-04T02:54:59-04:00","2026-10-04T04:05:00-04:00"):
            self.assertTrue(backfill_window(datetime.fromisoformat(stamp)))

    def test_site_is_not_loaded_until_all_symbols_are_saved(self):
        plan=self.store.plan(self.parallel_plan(sessions=2));plan["symbols"]=["SPY","QQQ"]
        identity=self.store.create_run(plan);self.store.save_day(day(plan["sessions"][0]["session"]))
        events=[]
        job=ParallelBackfill(self.store,identity,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        def local_day(symbol,session,close_minute):
            self.assertNotIn("site",events)
            if self.store.has_day(symbol,session):return False
            events.append((symbol,session));return self.store.save_day(day(session,symbol))
        job.day=local_day
        def site_factory():
            events.append("site")
            self.assertEqual(self.store.collection_status(identity)["saved_days"],4)
            raise FileNotFoundError("Site configuration unavailable")
        result=job.run(site_factory,report=lambda _:None)
        self.assertTrue(result["collection_complete"]);self.assertFalse(result["upload_complete"])
        self.assertEqual(result["upload_error"],"FileNotFoundError");self.assertEqual(len(self.store.pending_days()),4)
        self.assertEqual(events[-1],"site");self.assertEqual(len(events),4)

    def test_final_timeout_preserves_all_local_days_and_upload_only_resumes(self):
        plan=self.store.plan(self.parallel_plan(sessions=30));plan["symbols"]=["SPY","QQQ"]
        identity=self.store.create_run(plan)
        for symbol in plan["symbols"]:
            for session in plan["sessions"]:self.store.save_day(day(session["session"],symbol))
        self.store.save_day(day("2026-09-25","AAPL")) # unrelated run remains pending
        requests=[]
        class Site:
            def request(self,path,payload,**kwargs):
                requests.append(payload)
                if len(requests)==2:raise TimeoutError("Site unavailable")
                return {"ok":True,"days":len(payload["days"])}
        job=Backfill(self.store,identity,factory=lambda *a,**k:self.fail("provider"),clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        first=job.upload(Site,report=lambda _:None)
        self.assertTrue(first["collection_complete"]);self.assertFalse(first["upload_complete"])
        self.assertEqual(first["uploaded_this_phase"],25);self.assertEqual(len(requests),2)
        self.assertEqual(len(self.store.pending_days(run_id=identity)),35)
        self.assertEqual(self.store.collection_status(identity)["saved_days"],60)
        site=self.fake_site();result=job.upload(lambda:site,report=lambda _:None)
        self.assertTrue(result["upload_complete"]);self.assertEqual(result["uploaded_this_phase"],35)
        self.assertEqual([len(x["days"]) for x in site.calls],[25,10])
        self.assertEqual(len(self.store.pending_days()),1);self.assertEqual(self.store.pending_days()[0]["symbol"],"AAPL")
        self.assertEqual(job.budget.requests,0)

    def test_provider_failure_or_cap_never_loads_site(self):
        for cap,fails in ((100,True),(1,False)):
            with self.subTest(cap=cap):
                identity=self.parallel_plan(requests=cap);factory,_=self.fake_provider(fail=fails)
                job=ParallelBackfill(self.store,identity,factory_provider=factory,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
                with self.assertRaises(RuntimeError):
                    job.run(lambda:self.fail("Site during incomplete collection"),report=lambda _:None)
                job.close()

    def test_upload_gate_distinguishes_missing_empty_failed_and_started(self):
        identity=self.parallel_plan(sessions=4);plan=self.store.plan(identity)
        sessions=[x["session"] for x in plan["sessions"]]
        self.store.save_day(day(sessions[0]))
        for session in sessions[1:]:self.store.reserve_part("SPY",session,"catalog",identity,"now")
        self.store.finish_part("SPY",sessions[1],"catalog","success",[],{"response_bytes":0})
        self.store.finish_part("SPY",sessions[2],"catalog","ValueError",None,{"response_bytes":0})
        job=Backfill(self.store,identity,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        result=job.upload(lambda:self.fail("unresolved admission"),report=lambda _:None)
        self.assertFalse(result["collection_complete"]);self.assertEqual(result["unresolved_days"],1)
        self.assertEqual(result["saved_days"],1);self.assertEqual(result["skipped_days"],2)
        self.store.finish_part("SPY",sessions[3],"catalog","success",[sessions[3]],{"response_bytes":0})
        result=job.upload(lambda:self.fail("unfinished chain"),report=lambda _:None)
        self.assertEqual(result["pending_days"],1);self.assertEqual(result["unresolved_days"],0)
        self.store.reserve_part("SPY",sessions[3],sessions[3],identity,"now")
        self.store.finish_part("SPY",sessions[3],sessions[3],"ValueError",None,{"response_bytes":0})
        site=self.fake_site();result=job.upload(lambda:site,report=lambda _:None)
        self.assertTrue(result["collection_complete"]);self.assertTrue(result["upload_complete"])
        self.assertEqual(result["skipped_days"],3)

    def test_upload_deadline_failure_does_not_load_site(self):
        identity=self.parallel_plan(sessions=1);plan=self.store.plan(identity)
        self.store.save_day(day(plan["sessions"][0]["session"]))
        job=Backfill(self.store,identity,clock=lambda:datetime(2026,9,30,23,tzinfo=timezone.utc))
        result=job.upload(lambda:self.fail("expired Site phase"),report=lambda _:None)
        self.assertFalse(result["upload_complete"]);self.assertEqual(len(self.store.pending_days()),1)

    def test_stop_at_end_does_not_start_upload(self):
        identity=self.parallel_plan(sessions=1);plan=self.store.plan(identity)
        self.store.save_day(day(plan["sessions"][0]["session"]))
        job=ParallelBackfill(self.store,identity,clock=lambda:datetime(2026,9,30,21,tzinfo=timezone.utc))
        job.stopping.set()
        result=job.upload(lambda:self.fail("stopped upload"),report=lambda _:None)
        self.assertFalse(result["upload_complete"]);self.assertTrue(result["collection_complete"])
