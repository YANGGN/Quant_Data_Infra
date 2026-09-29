from datetime import datetime,date
import unittest
from quant_data.tool_platform.research_coverage import readiness
from quant_data.tool_platform.collection_plan import project,next_slot,CONTRACT

class ResearchSupportTests(unittest.TestCase):
    def test_readiness_missing_is_not_empty_or_certified(self):
        r=readiness(dict(prices="available",options="source_unavailable",earnings="available"))
        self.assertEqual(r["implied_realized"]["missing_inputs"],["options"])
        self.assertEqual(r["earnings_briefing"]["status"],"input_gaps")
    def test_plan_respects_weekend_backoff_quota_and_does_not_mutate(self):
        import copy
        def task(s,lane,due="2026-09-27"):
            return dict(symbol=s,instrument_id=s,lane=lane,due=due,phase="catalogue",blocked=False,queued_at="2026-09-20")
        state=dict(contract=CONTRACT,roster=[],watch={},tasks={
            "A":task("A","recent"),"B":task("B","delayed"),"C":task("C","fallback","2026-10-01")},daily={})
        account=dict(roster=[],operating_daily_cap=100,usage={"2026-09-27":dict(attempted=100,remaining=0)})
        before=copy.deepcopy((state,account))
        rows,meta,more=project(state,account,date(2026,9,27),datetime.fromisoformat("2026-09-27T12:00:00+00:00"),100)
        by={r["symbol"]:r for r in rows}
        self.assertTrue(by["B"]["eligible"])
        self.assertEqual(by["A"]["reason"],"saved_lane_budget_or_weekend_policy")
        self.assertEqual(by["C"]["reason"],"backoff_until_due_date")
        self.assertEqual(meta["remaining_request_budget"],0)
        self.assertEqual((state,account),before)
    def test_next_slot_uses_eastern_time_and_skips_nonexistent_dst_wall_time(self):
        self.assertEqual(next_slot(datetime.fromisoformat("2026-09-28T12:00:00+00:00")),"2026-09-29T02:00:00-04:00")
        self.assertEqual(next_slot(datetime.fromisoformat("2026-03-08T05:00:00+00:00")),"2026-03-09T02:00:00-04:00")
