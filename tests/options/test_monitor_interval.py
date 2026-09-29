"""Full-chain cumulative intervals and quality gates; no live requests."""
from datetime import timedelta
from unittest import TestCase
from unittest.mock import patch
from tests.options import test_monitor as fixtures
from quant_data.options.monitor.model import aggregate,derive,alert_for,baseline_eligible

class IntervalTests(TestCase):
    setUp=fixtures.MonitorTests.setUp
    job=fixtures.MonitorTests.job
    def pair(self):
        old=aggregate("SPY",fixtures.SESSION,(fixtures.FRIDAY-timedelta(minutes=5)).isoformat(),fixtures.rows(100))
        old=derive(old,None,[],None,cadence=5)
        current=aggregate("SPY",fixtures.SESSION,fixtures.FRIDAY.isoformat(),fixtures.rows(200))
        return old,current
    def test_new_contract_contributes_full_current_volume(self):
        old,_=self.pair()
        new={**fixtures.rows()[0],"strike":"710","timestamp":fixtures.FRIDAY.isoformat(),"volume":300}
        current=aggregate("SPY",fixtures.SESSION,fixtures.FRIDAY.isoformat(),fixtures.rows(200)+[new])
        result=derive(current,old,[],None,cadence=5)
        self.assertEqual((result["interval_call_volume"],result["interval_put_volume"],result["interval_volume"]),(400,50,450))
        self.assertEqual(result["interval_minutes"],5)
        self.assertEqual(result["interval_start"],old["captured_at"])
        self.assertEqual(result["quality_flags"].count("contract_universe_changed"),1)
        self.assertNotIn("contract_universe_changed",current["quality_flags"])
        self.assertEqual(result["contract_count"],3)
        self.assertEqual(result["active_contract_count"],3)
    def test_coverage_loss_blocks_even_when_total_volume_increases(self):
        old,current=self.pair();old["contract_count"]=3
        result=derive(current,old,[],None,cadence=5)
        self.assertIsNone(result["interval_volume"])
        self.assertIn("contract_coverage_lost",result["quality_flags"])
        old,current=self.pair();old["active_contract_count"]=3
        self.assertIsNone(derive(current,old,[],None,cadence=5)["interval_volume"])
    def test_old_snapshot_without_counts_requires_one_comparable_pair(self):
        old,current=self.pair();old.pop("contract_count");old.pop("active_contract_count");current["_fingerprint"]="new"
        result=derive(current,old,[],None,cadence=5)
        self.assertIsNone(result["interval_volume"])
        self.assertIn("coverage_comparison_unavailable",result["quality_flags"])
    def test_delayed_interval_is_displayable_but_not_alert_eligible(self):
        old,current=self.pair();current["captured_at"]=(fixtures.FRIDAY+timedelta(minutes=5)).isoformat()
        result=derive(current,old,[],None,cadence=5)
        self.assertEqual(result["interval_volume"],150)
        self.assertEqual(result["interval_minutes"],10)
        self.assertIn("interval_gap",result["quality_flags"])
        self.assertFalse(baseline_eligible(result,5))
        self.assertIsNone(result["relative_volume"])
        self.assertIsNone(alert_for({**result,"relative_volume":10,"interval_volume":10000,"concentration":1},None,enabled=True))
    def test_cadence_switch_keeps_actual_delta_out_of_baseline(self):
        old,current=self.pair();old["cadence_minutes"]=10
        result=derive(current,old,[],None,cadence=5)
        self.assertEqual(result["interval_volume"],150)
        self.assertIn("cadence_changed",result["quality_flags"])
        self.assertFalse(baseline_eligible(result,5))
    def test_first_incomplete_and_session_boundary_have_no_interval(self):
        old,current=self.pair()
        self.assertIsNone(derive(current,None,[],None,cadence=5)["interval_volume"])
        old["session"]="2026-09-24"
        self.assertIsNone(derive(current,old,[],None,cadence=5)["interval_volume"])
        old,current=self.pair();old["coverage"]="incomplete"
        self.assertIsNone(derive(current,old,[],None,cadence=5)["interval_volume"])
    def test_nonpositive_elapsed_and_side_correction_are_rejected(self):
        old,current=self.pair();current["captured_at"]=old["captured_at"]
        self.assertIn("interval_time_invalid",derive(current,old,[],None,cadence=5)["quality_flags"])
        old,current=self.pair();current["call_volume"]=99;current["put_volume"]=1000
        result=derive(current,old,[],None,cadence=5)
        self.assertIsNone(result["interval_volume"])
        self.assertIn("volume_correction",result["quality_flags"])
    def test_valid_zero_is_preserved(self):
        old,current=self.pair();current["call_volume"]=old["call_volume"];current["put_volume"]=old["put_volume"]
        result=derive(current,old,[],None,cadence=5)
        self.assertEqual((result["interval_call_volume"],result["interval_put_volume"],result["interval_volume"]),(0,0,0))
    def test_matching_new_contract_interval_can_feed_alerts(self):
        old,current=self.pair();current["_fingerprint"]="changed";current["contract_count"]+=1
        current.update(call_volume=5000,put_volume=1000,top5_share=.8)
        bases=[{"interval_volume":50,"interval_minutes":5,"coverage":"complete","quality_flags":[]} for _ in range(20)]
        result=derive(current,old,bases,None,cadence=5)
        self.assertEqual(result["baseline_sessions"],20)
        self.assertIsNotNone(alert_for(result,None,enabled=True))
    def test_baseline_reader_excludes_delayed_and_cadence_changed_intervals(self):
        store=self.job().store;store.initialize()
        old,current=self.pair();value=derive(current,old,[],None,cadence=5)
        for days,minutes,flags in ((1,5,[]),(2,10,["interval_gap"]),(3,5,["cadence_changed"])):
            stamp=fixtures.FRIDAY-timedelta(days=days)
            saved={**value,"session":stamp.date().isoformat(),"captured_at":stamp.isoformat(),"_slot":stamp.isoformat(),"interval_minutes":minutes,"quality_flags":flags}
            store.publish(saved)
        self.assertEqual(len(store.baselines("SPY",5,10*60+35,fixtures.SESSION)),1)
    def test_job_persists_new_interval_fields_and_compact_counts(self):
        self.job().run()
        self.job(clock=lambda:fixtures.FRIDAY+timedelta(minutes=5)).run()
        last=self.site.publications[-1]["observations"][0]
        self.assertEqual(last["interval_volume"],0)
        self.assertEqual(last["interval_call_volume"],0)
        self.assertEqual(last["contract_count"],2)
        self.assertTrue(last["interval_start"])
        self.assertFalse(any(k.startswith("_") for k in last))
