"""Independent volatility arithmetic and gap/calendar tests."""
from decimal import Decimal
import math
import statistics
import unittest
from quant_data.tool_platform.volatility_comparison import trailing_volatility

class VolatilityComparisonTests(unittest.TestCase):
    def prices(self):
        return dict(zip(["2026-09-16","2026-09-17","2026-09-18","2026-09-21","2026-09-22","2026-09-23"],[100,102,101,103,104,102]))
    def test_sample_log_returns_match_independent_reference(self):
        p=self.prices();out=trailing_volatility(p,"2026-09-23",7)
        reference=statistics.stdev([math.log(b/a) for a,b in zip(p.values(),list(p.values())[1:])])*math.sqrt(252)
        self.assertAlmostEqual(float(out["realized_volatility"]),reference,places=12)
        self.assertEqual(out["return_count"],5)
    def test_gaps_zero_variance_and_future_invariance(self):
        p=self.prices();base=trailing_volatility(p,"2026-09-23",7)
        self.assertEqual(base,trailing_volatility(dict(p,**{"2026-09-24":500}),"2026-09-23",7))
        p.pop("2026-09-21")
        self.assertEqual(trailing_volatility(p,"2026-09-23",7)["reason"],"missing_daily_session")
        zero=trailing_volatility({d:100 for d in self.prices()},"2026-09-23",7)
        self.assertEqual(zero["realized_volatility"],0)
        self.assertIsNone(trailing_volatility(self.prices(),"2026-09-26",7)["realized_volatility"])
        self.assertEqual(trailing_volatility({},"2023-09-23",7)["reason"],"calendar_outside_supported_2024_2026")
