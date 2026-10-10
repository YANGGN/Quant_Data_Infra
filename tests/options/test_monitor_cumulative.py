"""Cumulative volume policy and durable alerts; only temporary offline stores."""
import copy, importlib.util, json, unittest
from datetime import timedelta
from unittest.mock import patch
from tests.options import test_monitor as f
from tests.options.test_monitor_baseline import day
from quant_data.options.monitor.model import aggregate,derive,alert_for,cumulative_alert_for
from quant_data.options.monitor.baseline import seed_cumulative
from quant_data.options.monitor.baseline_store import BaselineStore

class CumulativeTests(unittest.TestCase):
    setUp=f.MonitorTests.setUp
    job=f.MonitorTests.job
    def current(self,volume=1000,when=None):
        stamp=(when or f.FRIDAY).isoformat()
        return aggregate("SPY",f.SESSION,stamp,[{**r,"timestamp":stamp} for r in f.rows(volume)])
    def bases(self,count=20,value=300,minute=635):
        return [{"symbol":"SPY","session":(f.FRIDAY-timedelta(days=i+1)).date().isoformat(),
            "coverage":"complete","quality_flags":[],"cumulative_volume":value,
            "_cumulative_end_minute":minute} for i in range(count)]
    def result(self,**changes):
        args=dict(current=self.current(),previous=None,baselines=[],anchor=None,
            cadence=5,cumulative_baselines=self.bases(),cumulative_minute=635)
        args.update(changes)
        return derive(**args)
    def test_first_snapshot_has_independent_cumulative_ratio_and_alert(self):
        o=self.result()
        self.assertIsNone(o["interval_volume"]);self.assertIsNone(o["relative_volume"])
        self.assertEqual(o["cumulative_volume"],1500)
        self.assertEqual(o["cumulative_relative_volume"],5)
        self.assertEqual(o["cumulative_expected_volume"],300)
        self.assertEqual(o["cumulative_baseline_sessions"],20)
        self.assertIsNone(alert_for(o,None,enabled=True))
        a=cumulative_alert_for(o,None,enabled=True)
        self.assertEqual(a["kind"],"cumulative");self.assertIn("10:35 ET",a["reason"])
    def test_no_acceleration_iv_or_concentration_required(self):
        c=self.current();c["top5_share"]=None
        o=self.result(current=c)
        self.assertIsNone(o["iv_change"]);self.assertIsNone(o["acceleration"])
        self.assertIsNotNone(cumulative_alert_for(o,None,enabled=True))
    def test_threshold_minimum_volume_and_opt_out(self):
        for volume,expected,should in ((1000,500,True),(999,500,False),(300,100,False),(334,100,True)):
            o=self.result(current=self.current(volume),cumulative_baselines=self.bases(value=expected))
            self.assertEqual(cumulative_alert_for(o,None,enabled=True) is not None,should)
            self.assertIsNone(cumulative_alert_for(o,None,enabled=False))
    def test_twenty_distinct_prior_sessions_and_positive_median_required(self):
        for values,count in ((self.bases(19),19),(self.bases()+self.bases(),20)):
            o=self.result(cumulative_baselines=values)
            self.assertEqual(o["cumulative_baseline_sessions"],count)
            self.assertEqual(o["cumulative_relative_volume"] is not None,count>=20)
        o=self.result(cumulative_baselines=self.bases(value=0))
        self.assertEqual(o["cumulative_expected_volume"],0)
        self.assertIsNone(o["cumulative_relative_volume"])
        self.assertIsNone(cumulative_alert_for(o,None,enabled=True))
    def test_sixty_latest_sessions_and_no_cross_symbol_future_or_window(self):
        bases=self.bases(65)
        bases[-5:]=[{**b,"cumulative_volume":9000} for b in bases[-5:]]
        bad=[{**bases[0],**change} for change in (
            {"symbol":"QQQ"},{"session":f.SESSION},{"session":"2099-01-01"},
            {"session":"bad"},{"_cumulative_end_minute":640},
            {"cumulative_volume":None},{"cumulative_volume":-1},{"cumulative_volume":1.5},
            {"coverage":"incomplete"},{"quality_flags":["volume_correction"]})]
        o=self.result(cumulative_baselines=bases+bad)
        self.assertEqual(o["cumulative_baseline_sessions"],60)
        self.assertEqual(o["cumulative_expected_volume"],300)
    def test_gaps_and_cadence_switch_do_not_block_cumulative(self):
        for age,cadence in ((10,5),(5,10)):
            old=derive(self.current(100,f.FRIDAY-timedelta(minutes=age)),None,[],None,cadence=cadence)
            o=self.result(previous=old)
            self.assertTrue(set(o["quality_flags"])&{"interval_gap","cadence_changed"})
            self.assertEqual(o["cumulative_quality_flags"],[])
            self.assertIsNotNone(cumulative_alert_for(o,None,enabled=True))
    def test_incomplete_prior_does_not_block_complete_current_snapshot(self):
        old=self.current(100,f.FRIDAY-timedelta(minutes=5));old["coverage"]="incomplete"
        o=self.result(previous=old)
        self.assertIsNone(o["interval_volume"])
        self.assertIsNotNone(cumulative_alert_for(o,None,enabled=True))
    def test_either_side_correction_blocks_cumulative(self):
        for side in ("call_volume","put_volume"):
            old=self.current(100,f.FRIDAY-timedelta(minutes=5));c=self.current()
            c[side]=old[side]-1
            o=self.result(current=c,previous=old)
            self.assertIn("volume_correction",o["cumulative_quality_flags"])
            self.assertIsNone(o["cumulative_relative_volume"])
            self.assertIsNone(cumulative_alert_for(o,None,enabled=True))
    def test_snapshot_quality_blocks_but_count_warnings_do_not(self):
        for flag in ("incomplete_volume","future_row","contract_coverage_lost","coverage_comparison_unavailable"):
            c=self.current();c["quality_flags"]=[flag];o=self.result(current=c)
            self.assertIsNone(o["cumulative_relative_volume"])
            self.assertIsNone(cumulative_alert_for(o,None,enabled=True))
        for flag in ("contract_count_decreased","active_contract_count_decreased","coverage_counts_unavailable"):
            c=self.current();c["quality_flags"]=[flag]
            self.assertIsNotNone(cumulative_alert_for(self.result(current=c),None,enabled=True))
    def test_invalid_current_volume_or_session_time_blocked(self):
        for change in ({"call_volume":None},{"put_volume":-1},{"call_volume":1.2},
                       {"call_volume":True},{"coverage":"incomplete"},{"session":"2026-09-24"}):
            o=self.result(current={**self.current(),**change})
            self.assertIsNone(o["cumulative_relative_volume"])
            self.assertTrue(o["cumulative_quality_flags"])
    def test_exact_cutoff_tolerance_and_no_extrapolation(self):
        for seconds,valid in ((-60,True),(60,True),(61,False),(-61,False)):
            o=self.result(current=self.current(when=f.FRIDAY+timedelta(seconds=seconds)))
            self.assertEqual(o["cumulative_relative_volume"] is not None,valid)
        for minute in (570,634,980):
            o=self.result(cumulative_minute=minute)
            self.assertIn("cumulative_time_unmatched",o["cumulative_quality_flags"])
    def test_cumulative_alert_cooldown_and_distinct_identity(self):
        o=self.result()
        o.update(relative_volume=5,interval_volume=1500,acceleration=4,baseline_sessions=20)
        a=alert_for(o,None,enabled=True);b=cumulative_alert_for(o,None,enabled=True)
        self.assertNotEqual(a["id"],b["id"])
        for minutes,expected in ((29,False),(30,True)):
            last=(f.FRIDAY-timedelta(minutes=minutes)).isoformat()
            self.assertEqual(cumulative_alert_for(o,last,enabled=True) is not None,expected)
    def test_seed_sums_half_open_bars_and_honors_early_close(self):
        d=day(value=10)
        d["bins"][1]=[575,10000,20000]
        self.assertEqual(seed_cumulative(d,575)["cumulative_volume"],30)
        self.assertEqual(seed_cumulative(d,580)["cumulative_volume"],30030)
        self.assertIsNone(seed_cumulative(d,570))
        self.assertIsNone(seed_cumulative(day(close=780),785))
        self.assertEqual(seed_cumulative(day(close=780),780)["cumulative_volume"],1260)
    def test_reader_merges_seeds_and_live_without_duplicates_or_cadence_dependency(self):
        store=BaselineStore(self.root);store.initialize_baseline()
        store.save_day(day("2026-09-24",value=10))
        store.save_day(day(f.SESSION,value=10000))
        old=self.current();old.update(session="2026-09-24",captured_at="2026-09-24T10:35:20-04:00",
            _slot="2026-09-24T14:35:00+00:00",cadence_minutes=30,quality_flags=["interval_gap"])
        store.publish(old)
        items=store.cumulative_baselines("SPY",635,f.SESSION)
        self.assertEqual(len(items),1);self.assertEqual(items[0]["cumulative_volume"],1500)
        self.assertEqual(store.cumulative_baselines("QQQ",635,f.SESSION),[])
    def test_reader_excludes_bad_live_and_falls_back_to_seed(self):
        store=BaselineStore(self.root);store.initialize_baseline()
        store.save_day(day("2026-09-24"))
        for flag in ("volume_correction","contract_coverage_lost"):
            old=self.current();old.update(session="2026-09-24",captured_at="2026-09-24T10:35:00-04:00",
                _slot="2026-09-24T14:35:00+00:00",cadence_minutes=5,quality_flags=[flag])
            if flag=="contract_coverage_lost":continue
            store.publish(old)
        self.assertEqual(store.cumulative_baselines("SPY",635,f.SESSION)[0]["cumulative_volume"],390)
        self.assertEqual(store.cumulative_baselines("SPY",635,"2026-09-24"),[])
    def test_reader_matches_eastern_cutoff_across_dst(self):
        store=BaselineStore(self.root);store.initialize_baseline()
        for session,utc in (("2026-03-06","15:35:00"),("2026-03-09","14:35:00")):
            old=self.current();old.update(session=session,captured_at=session+"T"+utc+"+00:00",
                _slot=session+"T"+utc+"+00:00",cadence_minutes=5)
            store.publish(old)
        items=store.cumulative_baselines("SPY",635,"2026-03-10")
        self.assertEqual(len(items),2)
    def test_two_alerts_atomic_outbox_replay_and_independent_cooldowns(self):
        store=self.job().store;store.initialize();o=self.result()
        o.update(relative_volume=5,interval_volume=1500,acceleration=4)
        a=alert_for(o,None,enabled=True);b=cumulative_alert_for(o,None,enabled=True)
        self.assertTrue(store.publish(o,alerts=[a,b]))
        before=store.path.read_bytes();self.assertFalse(store.publish(o,alerts=[a,b]))
        self.assertEqual(before,store.path.read_bytes())
        self.assertEqual(len(store.pending("alert","9999",100)),2)
        self.assertEqual(store.last_alert("SPY",kind="interval"),o["captured_at"])
        self.assertEqual(store.last_alert("SPY",kind="cumulative"),o["captured_at"])
        c={**o,"captured_at":(f.FRIDAY+timedelta(minutes=5)).isoformat()}
        c["_slot"]=c["captured_at"];b2={**b,"id":"new-cumulative","detected_at":c["captured_at"]}
        store.publish(c,alerts=[b2])
        self.assertEqual(store.last_alert("SPY",kind="interval"),o["captured_at"])
        self.assertEqual(store.last_alert("SPY",kind="cumulative"),c["captured_at"])
    def test_alert_insert_failure_rolls_back_observation_and_outbox(self):
        store=self.job().store;store.initialize();o=self.result()
        a=cumulative_alert_for(o,None,enabled=True);store.publish(o,alerts=[a])
        before=store.path.read_bytes()
        later={**o,"captured_at":(f.FRIDAY+timedelta(minutes=5)).isoformat()}
        with self.assertRaises(Exception):store.publish(later,alerts=[a])
        self.assertEqual(before,store.path.read_bytes())
    def test_invalid_pair_time_or_identity_blocks_cumulative_before_correction(self):
        for changes in ({},{"captured_at":(f.FRIDAY+timedelta(minutes=5)).isoformat()},
                        {"captured_at":"2026-09-25T10:30:00"},
                        {"symbol":"QQQ"},{"session":"2026-09-24"}):
            old={**self.current(2000),**changes}
            o=self.result(previous=old)
            self.assertTrue(set(o["cumulative_quality_flags"]) &
                            {"interval_time_invalid","interval_session_changed"})
            self.assertIsNone(o["cumulative_relative_volume"])
            self.assertIsNone(cumulative_alert_for(o,None,enabled=True))

    def test_invalid_pair_flags_do_not_enter_cumulative_history(self):
        store=self.job().store;store.initialize()
        for days,flag in ((1,"interval_time_invalid"),(2,"interval_session_changed")):
            stamp=f.FRIDAY-timedelta(days=days)
            o=self.result()
            o.update(session=stamp.date().isoformat(),captured_at=stamp.isoformat(),
                     _slot=stamp.isoformat(),quality_flags=[flag])
            store.publish(o)
        self.assertEqual(store.cumulative_baselines("SPY",635,f.SESSION),[])

    def test_job_publishes_cumulative_on_first_snapshot_without_extra_provider_calls(self):
        store=BaselineStore(self.root);store.initialize_baseline()
        for i in range(20):
            store.save_day(day((f.FRIDAY-timedelta(days=i+1)).date().isoformat(),value=1))
        with patch("tests.options.test_monitor.rows",side_effect=lambda volume=100:[
            {"symbol":"SPY","expiration":"2026-10-02","strike":"700","timestamp":"2026-09-25T10:29:00-04:00","right":"CALL","volume":1000,"count":10},
            {"symbol":"SPY","expiration":"2026-10-02","strike":"700","timestamp":"2026-09-25T10:29:00-04:00","right":"PUT","volume":500,"count":10}]):
            r=self.job().run()
        self.assertEqual(r["requests"],5)
        payload=self.site.publications[-1];o=payload["observations"][0]
        self.assertEqual(o["cumulative_expected_volume"],39)
        self.assertEqual(payload["alerts"][0]["kind"],"cumulative")
        self.assertEqual(len(payload["alerts"]),1)
        self.assertEqual(self.job().run()["requests"],0)

if __name__=="__main__":unittest.main()
