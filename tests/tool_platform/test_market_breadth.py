from decimal import Decimal
import unittest
from quant_data.tool_platform.market_breadth import calculate

class BreadthTests(unittest.TestCase):
    def test_denominators_gaps_highs_lows_and_leader_ties(self):
        days=["2026-09-18","2026-09-21","2026-09-22","2026-09-23"]
        values={s:dict(zip(days,p)) for s,p in {"A":[10,11,12,13],"B":[10,9,8,7],"C":[None,None,None,None],"D":[10,11,12,13]}.items()}
        out,members=calculate(values,days,days[0],[2],2,2)
        last=out[-1]
        self.assertEqual(last["advance_decline_eligible"],3)
        self.assertEqual(last["new_highs"],2);self.assertEqual(last["new_lows"],1)
        self.assertEqual(last["leaders"],["A","D"])
        self.assertEqual(last["moving_averages"]["2"]["percent_above"],Decimal(200)/3)
        c=next(r for r in members if r["symbol"]=="C")
        self.assertIsNone(c["advance"])
        self.assertEqual(next(r for r in members if r["symbol"]=="A")["leader_streak_sessions"],2)
    def test_future_prices_cannot_change_earlier_breadth(self):
        days=["2026-09-21","2026-09-22","2026-09-23"]
        base={"A":dict(zip(days,[100,100,100]))}
        before=calculate(base,days,days[0],[2],2,1)[0]
        base["A"]["2026-09-24"]=200
        after=calculate(base,days+["2026-09-24"],days[0],[2],2,1)[0]
        self.assertEqual(before,after[:3])
        self.assertEqual(before[-1]["unchanged"],1)
        self.assertIsNone(before[-1]["advance_decline_ratio"])

    def test_mixed_values_and_decimal_rounding_match_preoptimization_output(self):
        # Frozen from the preoptimization implementation on September 29, 2026.
        # Covers gaps, invalid values, ordering, eligibility and rounded sums.
        from datetime import date, timedelta
        from decimal import localcontext
        import hashlib, json
        days=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(45)]
        values=[None,False,"NaN","Infinity",-1,0,"1e100",
                Decimal("100.12345678901234567890123456"),"101.987654321987654321",
                105,99,100,101,102,103,104,105,106,107,108,109,110]
        prices={symbol:{day:values[(i*3+j)%len(values)] for j,day in enumerate(days)}
                for i,symbol in enumerate(["D","B","C","A"])}
        with localcontext() as ctx:
            ctx.prec=12
            output=calculate(prices,days,days[3],[2,5,20],5,3)
        digest=hashlib.sha256(json.dumps(output,default=str,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        self.assertEqual(digest, "2ba68d54e4aec2cffe4583f865df84098bda2304247a72c7584e13d6fc3695e5")
