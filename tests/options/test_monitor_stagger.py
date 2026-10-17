"""Staggering invariants on temporary stores, fake provider and fake Site only."""
from collections import Counter
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch
from tests.options import test_monitor as fixtures
from quant_data.options.monitor.policy import radar_offset, NY
from quant_data.options.monitor.model import aggregate, derive

START=fixtures.FRIDAY-timedelta(minutes=5)  # 10:30 ET

class StaggerTests(unittest.TestCase):
    setUp=fixtures.MonitorTests.setUp
    job=fixtures.MonitorTests.job

    def symbols(self,offset,count=1):
        found=[f"T{i:04d}" for i in range(1000) if radar_offset(f"T{i:04d}")==offset]
        return found[:count]

    def universe(self,members):
        (self.root/"config/options_monitor_universe.json").write_text(
            json.dumps({"schema_version":1,"members":members}))

    def equities(self,symbols):
        self.universe([{"symbol":s,"name":s,"group":"equity","cadence_minutes":30} for s in symbols])

    def ohlc(self):
        return [p["symbol"] for t in self.transports for method,p in t.calls if method=="option_snapshot_ohlc"]

    def test_phase_is_stable_and_all_six_groups_are_used(self):
        self.assertEqual({s:radar_offset(s) for s in ("AAAA","T000","COHR","MU","CRCL","SBET")},
                         {"AAAA":15,"T000":0,"COHR":20,"MU":10,"CRCL":0,"SBET":15})
        offsets=[radar_offset(f"T{i:04d}") for i in range(1000)]
        self.assertEqual(set(offsets),set(range(0,30,5)))
        self.assertLess(max(Counter(offsets).values()),220)

    def test_each_group_runs_once_per_half_hour_and_restart_does_not_replay(self):
        symbols={offset:self.symbols(offset)[0] for offset in range(0,30,5)}
        self.equities(symbols.values())
        opening=START-timedelta(hours=1)
        for minute in range(0,60,5):
            now=opening+timedelta(minutes=minute)
            before=len(self.ohlc())
            result=self.job(clock=lambda:now).run()
            self.assertEqual(result["observations"],1,(minute,result))
            self.assertEqual(self.ohlc()[before:],[symbols[minute%30]])
            self.assertEqual(self.job(clock=lambda:now).run()["requests"],0)
        self.assertEqual(Counter(self.ohlc()),Counter({s:2 for s in symbols.values()}))
        with self.job().store.read() as c:
            for row in c.execute("SELECT symbol,slot FROM attempts WHERE method='option_snapshot_ohlc'"):
                self.assertEqual(datetime.fromisoformat(row["slot"]).minute%30,0)
            for row in c.execute("SELECT symbol,slot,captured_at FROM observations"):
                slot=datetime.fromisoformat(row["slot"])
                self.assertEqual(slot.minute%30,radar_offset(row["symbol"]))
                self.assertEqual(slot,datetime.fromisoformat(row["captured_at"]))

    def test_unstarted_group_can_carry_across_half_hour_boundary(self):
        symbol=self.symbols(25)[0];self.equities([symbol])
        now=START+timedelta(minutes=30)
        self.assertEqual(self.job(clock=lambda:now).run()["observations"],1)
        with self.job().store.read() as c:
            row=c.execute("SELECT slot,captured_at FROM observations").fetchone()
            self.assertEqual(datetime.fromisoformat(row["slot"]),START+timedelta(minutes=25))
            self.assertEqual(datetime.fromisoformat(row["captured_at"]),now)
        self.assertEqual(self.job(clock=lambda:now+timedelta(minutes=5)).run()["observations"],0)
        self.assertEqual(self.job(clock=lambda:now+timedelta(minutes=25)).run()["observations"],1)

    def test_old_success_and_failure_are_not_retried_at_new_phase(self):
        symbols=self.symbols(25,2);self.equities(symbols)
        store=self.job().store;store.initialize()
        for symbol,outcome in zip(symbols,("success","UNAVAILABLE")):
            slot=START.astimezone(NY).isoformat()
            store.reserve(fixtures.SESSION,symbol,slot,"option_snapshot_ohlc",START.isoformat())
            store.finish(fixtures.SESSION,symbol,slot,"option_snapshot_ohlc",outcome,{"response_bytes":40})
        self.assertEqual(self.job(clock=lambda:START+timedelta(minutes=25)).run()["observations"],0)
        self.assertEqual(self.ohlc(),[])
        self.assertEqual(self.job(clock=lambda:START+timedelta(minutes=55)).run()["observations"],2)

    def test_rollout_does_not_catch_up_behind_newer_aligned_attempt(self):
        symbol=self.symbols(25)[0];self.equities([symbol])
        now=START+timedelta(minutes=30)
        store=self.job().store;store.initialize()
        slot=now.astimezone(NY).isoformat()
        store.reserve(fixtures.SESSION,symbol,slot,"option_snapshot_ohlc",now.isoformat())
        store.finish(fixtures.SESSION,symbol,slot,"option_snapshot_ohlc","success",{"response_bytes":40})
        self.assertEqual(self.job(clock=lambda:now).run()["observations"],0)
        self.assertEqual(self.ohlc(),[])

    def test_watchlist_all_cadences_liquidity_and_alerts_bypass_radar_phase(self):
        symbols=self.symbols(25,6)
        self.universe([{"symbol":s,"name":s,"group":"equity","cadence_minutes":30} for s in symbols]+[
            {"symbol":"SPY","name":"Market","group":"market","cadence_minutes":5},
            {"symbol":"XLF","name":"Sector","group":"sector","cadence_minutes":10}])
        config={"schema_version":1,"watchlist":[{"symbol":s,"cadence_minutes":cadence,"alerts_enabled":True}
            for s,cadence in zip(symbols,(5,10,15,30))],"push_subscriptions":[]}
        self.site.config_get=lambda:config
        job=self.job(clock=lambda:START)
        with patch.object(job.store,"top_volume_symbols",return_value={symbols[4]}),patch.object(
            job.store,"last_alert",side_effect=lambda s:(START-timedelta(minutes=1)).isoformat() if s==symbols[5] else None):
            result=job.run()
        self.assertEqual(result["observations"],8,result)
        published={r["symbol"]:r for p in self.site.publications for r in p["observations"]}
        self.assertEqual([published[s]["cadence_minutes"] for s in symbols],[5,10,15,30,15,5])
        greeks={p["symbol"] for t in self.transports for method,p in t.calls if method=="option_snapshot_greeks_first_order"}
        self.assertTrue(set(symbols[:4])|{"SPY","XLF"} <= greeks)

    def test_return_from_priority_does_not_replay_existing_phase(self):
        symbol=self.symbols(25)[0];self.equities([symbol])
        config={"schema_version":1,"watchlist":[{"symbol":symbol,"cadence_minutes":5,"alerts_enabled":True}],"push_subscriptions":[]}
        self.site.config_get=lambda:config
        now=START+timedelta(minutes=25)
        self.assertEqual(self.job(clock=lambda:now).run()["observations"],1)
        config["watchlist"]=[]
        self.assertEqual(self.job(clock=lambda:now+timedelta(minutes=5)).run()["observations"],0)
        self.assertEqual(self.ohlc(),[symbol])
        self.assertEqual(self.job(clock=lambda:now+timedelta(minutes=30)).run()["observations"],1)

    def test_capacity_deferred_work_remains_eligible_next_five_minute_tick(self):
        symbols=self.symbols(5,3);self.equities(symbols)
        job=self.job(clock=lambda:START+timedelta(minutes=5))
        with patch.object(job,"_usage",return_value=(59997,0)):
            result=job.run()
        self.assertEqual(result["observations"],1,result)
        self.assertEqual(result["capacity_deferred"],2)
        result=self.job(clock=lambda:START+timedelta(minutes=10)).run()
        self.assertEqual(result["observations"],2,result)
        self.assertEqual(Counter(self.ohlc()),Counter({s:1 for s in symbols}))

    def test_baselines_use_observation_phase_and_exclude_legacy_time(self):
        symbol=self.symbols(25)[0];self.equities([symbol])
        job=self.job(clock=lambda:START+timedelta(minutes=25));store=job.store;store.initialize()
        for days,minute in ((1,25),(2,0)):
            stamp=START-timedelta(days=days)+timedelta(minutes=minute)
            value=derive(aggregate(symbol,fixtures.SESSION,fixtures.FRIDAY.isoformat(),
                [{**r,"symbol":symbol} for r in fixtures.rows()]),None,[],None,cadence=30)
            value.update(session=stamp.date().isoformat(),captured_at=stamp.isoformat(),
                         _slot=stamp.astimezone(NY).isoformat(),interval_volume=100,interval_minutes=30)
            store.publish(value)
        with patch.object(store,"top_volume_symbols",return_value=set()),patch.object(store,"baselines",wraps=store.baselines) as lookup:
            job.run()
        self.assertEqual(lookup.call_args.args,(symbol,30,10*60+55,fixtures.SESSION))
        baseline=store.baselines(symbol,30,10*60+55,fixtures.SESSION)
        self.assertEqual(len(baseline),1)
        self.assertEqual(baseline[0]["session"],(START-timedelta(days=1)).date().isoformat())

    def test_early_close_does_not_force_all_groups_or_run_after_close(self):
        symbols={offset:self.symbols(offset)[0] for offset in range(0,30,5)}
        self.equities(symbols.values())
        store=self.job().store;store.initialize()
        store.save_calendar(fixtures.SESSION,[{"date":fixtures.SESSION,"type":"early_close","open":"09:30:00","close":"13:00:00"}])
        opening=START-timedelta(hours=1)
        for minute in range(0,215,5):
            result=self.job(clock=lambda:opening+timedelta(minutes=minute)).run()
            self.assertEqual(result["observations"],1,(minute,result))
        before=list(self.ohlc())
        self.assertEqual(self.job(clock=lambda:opening+timedelta(minutes=215)).run()["observations"],0)
        self.assertEqual(self.ohlc(),before)
        self.assertEqual(Counter(before),Counter({s:8 if offset==0 else 7 for offset,s in symbols.items()}))

if __name__=="__main__":unittest.main()
