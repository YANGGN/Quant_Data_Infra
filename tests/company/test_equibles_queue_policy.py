"""Pure selection invariants shared by collection and read-only queue planning."""
from copy import deepcopy
from datetime import date
import unittest

from quant_data.company.equibles_queue_policy import lane_quotas, next_task
from quant_data.operations import equibles_refresh
from quant_data.tool_platform import collection_plan

TODAY = date(2026, 9, 25)


def state(**tasks):
    return {"tasks": {symbol: dict(due="2026-09-24", queued_at="2026-09-24",
            blocked=False, **values) for symbol, values in tasks.items()}, "watch": {}}


class EquiblesQueuePolicyTests(unittest.TestCase):
    def test_lane_budgets_are_fresh_and_preserve_weekend_allocation(self):
        self.assertEqual(lane_quotas(TODAY), dict(recent=80, delayed=15, fallback=5))
        for day in (date(2026, 9, 26), date(2026, 9, 27)):
            self.assertEqual(lane_quotas(day), dict(recent=0, delayed=0, fallback=100))
        changed = lane_quotas(TODAY)
        changed["recent"] = 0
        self.assertEqual(lane_quotas(TODAY)["recent"], 80)

    def test_partial_work_precedes_protected_discovery_without_mutation(self):
        value = state(A=dict(lane="recent", phase="catalogue"),
                      B=dict(lane="fallback", phase="download"))
        before = deepcopy(value)
        self.assertEqual(next_task(value, TODAY, dict(recent=0, delayed=0, fallback=5),
                                   lane_quotas(TODAY)), ("B", "fallback"))
        self.assertEqual(value, before)

    def test_unused_lanes_then_oldest_checked_order_and_borrowing(self):
        value = state(A=dict(lane="recent", phase="catalogue"),
                      B=dict(lane="delayed", phase="catalogue"))
        value["watch"] = {"A": {"last_checked": "2026-08-01"},
                           "B": {"last_checked": "2026-09-01"}}
        self.assertEqual(next_task(value, TODAY, dict(recent=80, delayed=0, fallback=0),
                                   lane_quotas(TODAY)), ("B", "delayed"))
        self.assertEqual(next_task(value, TODAY, lane_quotas(TODAY),
                                   lane_quotas(TODAY)), ("A", "recent"))
        value["tasks"]["A"]["blocked"] = True
        value["tasks"]["B"]["due"] = "2026-09-28"
        self.assertIsNone(next_task(value, TODAY, lane_quotas(TODAY), lane_quotas(TODAY)))

    def test_weekend_admits_delayed_and_partial_work_as_fallback(self):
        weekend = date(2026, 9, 26)
        value = state(A=dict(lane="recent", phase="catalogue"),
                      B=dict(lane="delayed", phase="catalogue"),
                      C=dict(lane="recent", phase="publish"))
        spent = dict(recent=0, delayed=0, fallback=0)
        self.assertEqual(next_task(value, weekend, spent, lane_quotas(weekend)), ("C", "fallback"))
        del value["tasks"]["C"]
        self.assertEqual(next_task(value, weekend, spent, lane_quotas(weekend)), ("B", "fallback"))
        del value["tasks"]["B"]
        self.assertIsNone(next_task(value, weekend, spent, lane_quotas(weekend)))

    def test_existing_caller_date_boundaries_are_preserved(self):
        value = state(A=dict(lane="recent", phase="catalogue"))
        value["tasks"]["A"]["due"] = "2026-09-24T23:00:00Z"
        spent = dict(recent=0, delayed=0, fallback=0)
        self.assertEqual(equibles_refresh.next_task(value, TODAY, spent, lane_quotas(TODAY)),
                         ("A", "recent"))
        with self.assertRaises(ValueError):
            collection_plan._next_saved_task(value, TODAY, spent, lane_quotas(TODAY))
