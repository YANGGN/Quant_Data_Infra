from decimal import Decimal
import unittest
from quant_data.tool_platform.event_distribution import response,aligned_index,summary

class EventDistributionTests(unittest.TestCase):
    def test_date_only_and_timestamp_alignment(self):
        days=["2026-09-18","2026-09-21","2026-09-22","2026-09-23","2026-09-24"]
        prices=dict(zip(days,[100,110,121,133.1,140]))
        r=response("2026-09-21",days,prices,2)
        self.assertEqual(r["first_response_session"],"2026-09-22")
        self.assertAlmostEqual(float(r["cumulative_return"]),.21)
        self.assertEqual(aligned_index("2026-09-21T08:30:00-04:00",days)[0],1)
        self.assertEqual(aligned_index("2026-09-21T15:00:00-04:00",days)[0],2)
        self.assertEqual(aligned_index("2026-09-21T08:30:00",days),(2,"source_date_next_session_timezone_unverified"))
    def test_exclusions_missing_prices_and_distribution(self):
        days=["2026-09-18","2026-09-21","2026-09-22"]
        out=response("2026-09-18",days,{days[0]:100,days[2]:110},2)
        self.assertEqual(out["reason"],"missing_prices_in_event_window")
        self.assertIsNone(response("2026-09-22",days,{},1)["cumulative_return"])
        r=summary([Decimal("-.1"),None,Decimal(".1"),Decimal(".3")])
        self.assertEqual(r["sample_size"],3);self.assertEqual(r["mean"],Decimal(".1"))
        self.assertIsNone(summary([None])["mean"])
