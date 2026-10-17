"""Independent arithmetic and missingness checks for retained revisions."""
from decimal import Decimal
from types import SimpleNamespace
import unittest
from quant_data.tool_platform.consensus_research import compare,revisions,history
from quant_data.tool_platform.retained_research_common import unpack
from quant_data.tool_platform.context import FixedClock,ExecutionBudget
from tests.company import test_fmp_analyst_history as fixture

def row(value,**kw):
    return dict(issuer_id="issuer",instrument_id="instrument",request_period="quarter",
        target_period_end="2026-12-31",currency="USD",estimate_basis="GAAP",payload=dict(epsAvg=value),**kw)

class ConsensusKernelTests(unittest.TestCase):
    def test_negative_zero_missing_and_incompatible(self):
        self.assertEqual(compare(row(-2),row(-1),"epsAvg")["change_pct"],50)
        self.assertIsNone(compare(row(0),row(2),"epsAvg")["change_pct"])
        self.assertEqual(compare(row(0),row(2),"epsAvg")["percent_reason"],"zero_baseline")
        self.assertEqual(compare(row(None),row(2),"epsAvg")["exclusion_reason"],"missing_numeric_estimate")
        for key,value,reason in [("currency","EUR","currency_changed"),
            ("estimate_basis","SOURCE_UNSPECIFIED","estimate_basis_not_comparable"),
            ("target_period_end","2027-03-31","incompatible_identity_or_period")]:
            right=row(3);right[key]=value
            self.assertEqual(compare(row(2),right,"epsAvg")["exclusion_reason"],reason)
    def test_revision_membership_cutoff_and_stable_id(self):
        f=fixture.AnalystHistoryTests();f.setUp();self.addCleanup(f.tearDown)
        a=dict(symbol="NST",date="2026-12-31",epsAvg=2,revenueAvg=100,currency="USD",estimateBasis="GAAP")
        f.pub([a],period="quarter")
        f.pub([dict(a,epsAvg=3)],period="quarter",at="2026-09-08T00:00:00Z")
        context=SimpleNamespace(store_map=f.stores,clock=FixedClock(Decimal(1),"2026-09-28T22:00:00Z"),
            budget=ExecutionBudget(),checkpoint=lambda:None)
        args=dict(symbol="NST",period="quarter",start_date="2026-01-01",end_date="2027-01-01")
        out=revisions(args,context,f.registry)
        changes=[unpack(r) for r in out.records if r.record_type=="estimate_revision"]
        self.assertEqual([r["direction"] for r in changes],["up","unchanged"])
        self.assertEqual(changes[0]["change_pct"],50)
        early=revisions(dict(args,as_of="2026-09-07T23:00:00Z"),context,f.registry)
        self.assertEqual(early.status,"not_established")
        again=revisions(args,context,f.registry)
        self.assertEqual(out.to_primitive(),again.to_primitive())
        selected=history(dict(args,include_analyst_context=False),context,f.registry)
        self.assertEqual(len(selected.records),2)
