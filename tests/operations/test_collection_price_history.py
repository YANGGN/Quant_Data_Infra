import unittest
from datetime import date, timedelta
from quant_data.operations.collection_price_history import PriceCalendar, plan_missing_price_sessions
from quant_data.operations.collection_prices import price_units
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from tests.operations import test_collection_prices as fixtures

class PriceHistoryPlanTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SelectedPriceTests("runTest");self.f.setUp();self.addCleanup(self.f.doCleanups)
    def plan(self,calendars,cap=10,cutoff="2026-09-10T00:00:00Z"):
        return plan_missing_price_sessions(self.f.f.stores,self.f.scope,
            calendars=calendars,cutoff=cutoff,max_units=cap)
    def test_existing_price_separates_missing_windows_and_etfs_remain_eligible(self):
        self.f.publish();before=mutation_fingerprint(self.f.f.stores)
        dates=("2026-09-04","2026-09-08","2026-09-09")
        result=self.plan((PriceCalendar("AAPL",dates),PriceCalendar("SPY",dates)))
        by={row["symbol"]:row for row in result["coverage"]}
        self.assertEqual((by["AAPL"]["existing_sessions"],by["AAPL"]["missing_sessions"]),(1,2))
        self.assertEqual([w.sessions for w in result["windows"] if w.symbol=="AAPL"],
            [("2026-09-04",),("2026-09-09",)])
        self.assertEqual(len(result["windows"]),3)
        requests=price_units(self.f.f.stores,self.f.scope,windows=result["windows"],
            mode="historical_backfill",observation_window="explicit-history",cutoff="2026-09-10T00:00:00Z")
        self.assertEqual(len(requests),3)
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_all_existing_sessions_need_zero_units_and_future_capture_is_excluded(self):
        self.f.publish()
        dates=(PriceCalendar("AAPL",("2026-09-08",)),)
        self.assertEqual(self.plan(dates)["windows"],())
        early=self.plan(dates,cutoff="2026-09-09T02:30:00Z")
        self.assertEqual(len(early["windows"]),1)
    def test_explicit_calendar_and_unit_bounds_are_enforced(self):
        dates=tuple((date(2026,8,3)+timedelta(days=i)).isoformat() for i in range(14)
            if (date(2026,8,3)+timedelta(days=i)).weekday()<5)
        result=self.plan((PriceCalendar("AAPL",dates),))
        self.assertEqual([len(w.sessions) for w in result["windows"]],[5,5])
        with self.assertRaises(ResourceLimitError):self.plan((PriceCalendar("AAPL",dates),),cap=1)
        for calendars in ((PriceCalendar("UNKNOWN",dates),),(PriceCalendar("AAPL",("2026-09-10",)),),
            (PriceCalendar("AAPL",("2026-08-04","2026-08-03")),)):
            with self.assertRaises(ValidationError):self.plan(calendars)
