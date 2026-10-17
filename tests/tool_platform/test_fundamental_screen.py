from decimal import Decimal
import json
import unittest
from quant_data.tool_platform.fundamental_screen import project,rank_rows

def source(revenue,year=2026,currency="USD",dimension="ARQ"):
    return dict(values_json=json.dumps(dict(revenue=revenue,currency=currency,netmargin=".2",pe=12)),missingness_json="{}",
        reportperiod=f"{year}-06-27",calendardate=f"{year}-06-30",dimension=dimension,source_datekey=f"{year}-07-31",
        available_at="2026-09-01T00:00:00Z",version_id=str(year))

class FundamentalScreenTests(unittest.TestCase):
    def test_growth_requires_same_basis_currency_and_exact_prior_period(self):
        value=project([source(120),source(100,2025)],["revenue_growth_yoy","netmargin","de"])
        self.assertEqual(value["metrics"]["revenue_growth_yoy"],Decimal(".2"))
        self.assertIsNone(value["metrics"]["de"])
        for old in (source(100,2025,"EUR"),source(100,2025,dimension="MRQ"),source(0,2025)):
            self.assertIsNone(project([source(120),old],["revenue_growth_yoy"])["metrics"]["revenue_growth_yoy"])
    def test_criteria_ties_and_distinct_currency_cohorts(self):
        rows=[]
        for symbol,revenue,currency in [("A",120,"USD"),("B",120,"USD"),("C",100,"USD"),("D",900,"EUR")]:
            rows.append(dict(symbol=symbol,**project([source(revenue,currency=currency)],["revenue","de"])))
        values=rank_rows(rows,[dict(metric="revenue",minimum=110)],"revenue",False)
        by={r["symbol"]:r for r in values}
        self.assertEqual([by[s]["rank"] for s in ("A","B","C","D")],[1,1,3,1])
        self.assertFalse(by["C"]["passes"])
        self.assertIsNone(by["D"]["percentile"])
        rank_rows(rows,[dict(metric="de",maximum=1)],"revenue",False)
        self.assertTrue(all(not r["passes"] for r in rows))
