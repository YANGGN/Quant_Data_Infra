"""Offline cross-process provider admission and finite daytime backfill coverage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import json, multiprocessing, tempfile, threading, time, unittest

from quant_data.options.monitor.admission import ThetaAdmission, BackgroundBusy, daytime
from quant_data.options.monitor.backfill import ParallelBackfill, MarketPause, backfill_window
from quant_data.options.monitor.baseline_store import BaselineStore, DAYTIME_POLICY, CHECKSUM
from tests.options.test_monitor_baseline import bar

DAY = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
NIGHT = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)


def hold_background(root, ready, release):
    with ThetaAdmission(root).background(DAY):
        ready.set()
        release.wait(10)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="monitor-admission-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gate = ThetaAdmission(self.root)

    def test_daytime_clock_and_existing_job_guards(self):
        for stamp in ("2026-10-01T08:55:00-04:00", "2026-10-01T16:14:59-04:00",
                      "2026-11-02T09:30:00-05:00"):
            self.assertTrue(daytime(datetime.fromisoformat(stamp)))
        for stamp in ("2026-10-01T08:54:59-04:00", "2026-10-01T16:15:00-04:00",
                      "2026-10-03T10:00:00-04:00"):
            self.assertFalse(daytime(datetime.fromisoformat(stamp)))
        self.assertFalse(backfill_window(DAY))
        self.assertTrue(backfill_window(DAY, allow_daytime=True))
        for stamp in ("2026-10-01T19:15:00-04:00", "2026-10-04T03:00:00-04:00"):
            self.assertFalse(backfill_window(datetime.fromisoformat(stamp), allow_daytime=True))

    def test_live_intent_drains_existing_call_and_excludes_new_calls_across_processes(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=hold_background, args=(str(self.root), ready, release))
        process.start()
        self.addCleanup(lambda: process.is_alive() and process.terminate())
        self.assertTrue(ready.wait(5))
        live_entered, live_release = threading.Event(), threading.Event()
        def live():
            with self.gate.live(deadline=time.monotonic()+5):
                live_entered.set()
                live_release.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(live)
            try:
                end = time.monotonic()+2
                while not self.gate.live_pending() and time.monotonic()<end:time.sleep(.005)
                self.assertTrue(self.gate.live_pending())
                self.assertFalse(live_entered.is_set())
                with self.assertRaisesRegex(BackgroundBusy, "live_collection"):
                    with self.gate.background(DAY):self.fail("lower priority admitted")
                release.set()
                self.assertTrue(live_entered.wait(2))
                with self.assertRaisesRegex(BackgroundBusy, "live_collection"):
                    with self.gate.background(DAY):self.fail("login overlap")
            finally:
                release.set();live_release.set()
            future.result(2)
        process.join(2)
        self.assertEqual(process.exitcode, 0)
        with self.gate.background(DAY) as epoch:self.assertEqual(len(epoch), 32)

    def test_deadline_and_exception_release_live_intent(self):
        with self.gate.background(DAY):
            with self.assertRaisesRegex(RuntimeError, "admission_deadline"):
                with self.gate.live(deadline=time.monotonic()+.05):self.fail("admitted")
        self.assertFalse(self.gate.live_pending())
        with self.assertRaisesRegex(ValueError, "fixture"):
            with self.gate.live(deadline=time.monotonic()+1):raise ValueError("fixture")
        self.assertFalse(self.gate.live_pending())
        with self.gate.background(DAY):pass

    def test_killed_background_owner_releases_capacity_without_stale_lease(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=hold_background, args=(str(self.root), ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            process.terminate();process.join(3)
            with self.gate.live(deadline=time.monotonic()+1):pass
            with self.gate.background(DAY):pass
        finally:
            if process.is_alive():process.terminate();process.join(2)

    def test_aliases_share_gate_and_capacity_transition_drains(self):
        alias=self.root/"alias";alias.symlink_to(self.root,target_is_directory=True)
        other=ThetaAdmission(alias)
        with self.gate.background(NIGHT):
            with other.background(NIGHT):
                with self.assertRaisesRegex(BackgroundBusy,"background_capacity"):
                    with other.background(DAY):self.fail("day call overlapped night calls")
        with self.gate.background(DAY):
            with self.assertRaisesRegex(BackgroundBusy,"background_capacity"):
                with other.background(DAY):self.fail("two daytime calls")
            with self.assertRaisesRegex(BackgroundBusy,"background_capacity"):
                with other.background(NIGHT):self.fail("night call overlapped daytime call")


class DaytimeBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="daytime-baseline-")
        self.addCleanup(self.temp.cleanup)
        self.store=BaselineStore(Path(self.temp.name));self.store.initialize_baseline()
        self.now=DAY

    def make(self, symbols=("S0",), requests=1000):
        identity=self.store.create_run({"symbols":list(symbols),
            "sessions":[{"session":"2026-09-25","close_minute":960}],
            "max_requests":requests,"max_bytes":1000000,"pilot":False,
            "deadline":"2026-10-02T23:00:00+00:00"})
        original=self.store.plan(identity)
        self.store.configure_daytime(identity)
        self.assertEqual(self.store.plan(identity),original)
        return identity

    def factory(self, burst=0):
        state={"active":0,"peak":0,"calls":0,"factories":0,"epochs":[]}
        lock=threading.Lock();first_burst=threading.Event();arrivals=0
        def provider():
            state["factories"]+=1
            def create(root,budget,**kwargs):
                class Transport:
                    def request(inner,method,**params):
                        nonlocal arrivals
                        budget.reserve_request();budget.add_bytes(10)
                        with lock:
                            state["active"]+=1;state["calls"]+=1
                            state["peak"]=max(state["peak"],state["active"])
                            arrivals+=1;position=arrivals
                            if arrivals>=burst:first_burst.set()
                        try:
                            if burst and position<=burst and not first_burst.wait(3):
                                raise AssertionError("expected concurrent fixture admissions")
                            time.sleep(.04)
                            session=params["date"].isoformat()
                            return [bar(symbol=params["symbol"],expiration=session,
                                timestamp=session+"T09:30:00-04:00")],{"response_bytes":10}
                        finally:
                            with lock:state["active"]-=1
                    def close(inner):
                        if state["active"]:raise AssertionError("closed with active request")
                return Transport()
            return create
        return provider,state

    def job(self, identity, provider):
        return ParallelBackfill(self.store,identity,factory_provider=provider,
            workers=10,api_concurrency=4,clock=lambda:self.now,wait=True)

    def site(self):
        class Site:
            def request(inner,path,payload,**kwargs):return {"ok":True,"days":len(payload["days"])}
        return Site()

    def test_daytime_is_one_call_and_offhours_use_four_without_replay(self):
        for stamp,peak in ((DAY,1),(NIGHT,4)):
            with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as directory:
                self.store=BaselineStore(Path(directory));self.store.initialize_baseline();self.now=stamp
                identity=self.make(tuple("S"+str(i) for i in range(8)))
                provider,state=self.factory(burst=peak if peak>1 else 0)
                result=self.job(identity,provider).run(self.site,report=lambda _:None)
                self.assertTrue(result["upload_complete"]);self.assertEqual(result["saved_days"],8)
                self.assertEqual(state["peak"],peak);self.assertEqual(state["calls"],16)
                self.job(identity,provider).run(self.site,report=lambda _:None)
                self.assertEqual(state["calls"],16)
                self.assertEqual(self.store.usage(identity,1000000),(16,160))

    def test_today_pause_allows_four_and_tomorrow_restores_one_without_replay(self):
        identity=self.make(tuple("S"+str(i) for i in range(8)))
        ThetaAdmission(self.store.root).pause_today(self.now,identity)
        provider,state=self.factory(burst=4);job=self.job(identity,provider)
        self.assertEqual(job.request_capacity(),4)
        result=job.run(self.site,report=lambda _:None)
        self.assertEqual(state["peak"],4);self.assertEqual(result["saved_days"],8)
        self.now+=timedelta(days=1)
        tomorrow=self.job(identity,provider);self.assertEqual(tomorrow.request_capacity(),1)
        tomorrow.run(self.site,report=lambda _:None)
        self.assertEqual(state["calls"],16);self.assertEqual(self.store.usage(identity,1000000),(16,160))

    def test_live_busy_leaves_no_durable_attempt_and_resume_renews_factory(self):
        identity=self.make();provider,state=self.factory();job=self.job(identity,provider)
        with ThetaAdmission(self.store.root).live(deadline=time.monotonic()+2):
            with self.assertRaises(MarketPause):job.day("S0","2026-09-25",960)
            self.assertEqual(self.store.usage(identity,1000000),(0,0))
            self.assertEqual(state["calls"],0)
        result=job.run(self.site,report=lambda _:None)
        self.assertEqual(result["saved_days"],1);self.assertGreaterEqual(state["factories"],2)

    def test_completed_live_cycle_between_background_calls_invalidates_auth(self):
        identity=self.make();provider,state=self.factory();job=self.job(identity,provider)
        result=job.request("S0","2026-09-25","catalog","option_list_contracts",
            lambda _rows:["2026-09-25"],symbol="S0",date=DAY.date())
        self.assertEqual(state["calls"],1)
        with ThetaAdmission(self.store.root).live(deadline=time.monotonic()+1):pass
        with self.assertRaisesRegex(MarketPause,"session_changed"):
            job.day("S0","2026-09-25",960)
        self.assertEqual(state["calls"],1)
        self.assertEqual(self.store.usage(identity,1000000),(1,10))
        job.close()
        result=job.run(self.site,report=lambda _:None)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["calls"],2)
        self.assertEqual(state["factories"],2)

    def test_daytime_policy_idempotence_cap_and_deadline_preserved(self):
        identity=self.make(requests=1)
        self.assertEqual(self.store.configure_daytime(identity),DAYTIME_POLICY)
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_runs WHERE id=?",
                ("daytime:"+identity,)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT checksum FROM baseline_metadata").fetchone()[0],CHECKSUM)
        provider,state=self.factory()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"request_cap"):
                self.job(identity,provider).run(self.site,report=lambda _:None)
        self.assertEqual(state["calls"],1)
        self.now=datetime(2026,10,3,tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError,"duration_cap|request_cap"):
            self.job(identity,provider).run(self.site,report=lambda _:None)
        self.assertEqual(state["calls"],1)

    def test_waiting_admission_is_cancellable_before_durable_reservation(self):
        identity=self.make();provider,state=self.factory();job=self.job(identity,provider)
        with ThetaAdmission(self.store.root).background(DAY),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(job.day,"S0","2026-09-25",960)
            time.sleep(.05);job.stopping.set()
            with self.assertRaisesRegex(RuntimeError,"baseline_stopped"):future.result(2)
        self.assertEqual(state["calls"],0);self.assertEqual(self.store.usage(identity,1000000),(0,0))


if __name__ == "__main__":
    unittest.main()
