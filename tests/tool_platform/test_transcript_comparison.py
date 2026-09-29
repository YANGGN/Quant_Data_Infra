from decimal import Decimal
from types import SimpleNamespace
import unittest
from quant_data.tool_platform.transcript_comparison import invoke,compare_section
from quant_data.tool_platform.retained_research_common import unpack
from quant_data.tool_platform.context import FixedClock,ExecutionBudget
from tests.company.test_transcript_structured_call import measure
from tests.tool_platform import test_transcript_research_tools as fixtures

class TranscriptComparisonTests(unittest.TestCase):
    def test_matching_numeric_units_and_summary_omissions(self):
        a=dict(metric="Margin",period="FY2027",current=measure(low="40",high="42",unit="%"))
        b=dict(a,current=measure(low="41",high="44",unit="%"))
        value=compare_section("guidance",[a],[b])[0]
        self.assertEqual(value["numeric_change"]["low_change"],1)
        self.assertEqual(value["numeric_change"]["high_change"],2)
        self.assertIsNone(compare_section("guidance",[a],[dict(b,current=measure(low="41",high="44",unit="USD"))])[0]["numeric_change"])
        self.assertEqual(compare_section("guidance",[a],[])[0]["status"],"only_in_before_summary")
        self.assertIsNone(compare_section("guidance",[a],[b],numeric_allowed=False)[0]["numeric_change"])
    def test_actual_citations_quality_and_missing_extraction(self):
        f=fixtures.TranscriptResearchTests();f.setUp();self.addCleanup(f.doCleanups)
        older=f.add_call(quarter=2,day="2026-03-01")
        newer=f.add_call(quarter=3,day="2026-06-01",headline="New retained margin outlook")
        context=SimpleNamespace(store_map=f.stores,clock=FixedClock(Decimal(1),"2026-09-28T22:00:00Z"),
            budget=ExecutionBudget(),checkpoint=lambda:None)
        args=dict(symbol="AAPL",before_capture_id=older,after_capture_id=newer,sections=["headline","guidance"])
        value=invoke(args,context,f.registry)
        self.assertEqual(value.status,"ok")
        calls=[unpack(r) for r in value.records if r.record_type=="compared_call"]
        self.assertEqual({r["side"] for r in calls},{"before","after"})
        self.assertTrue(all("automatic_quality_status" in r and "review_status" in r for r in calls))
        citations=[unpack(r) for r in value.records if r.record_type=="comparison_evidence"]
        self.assertTrue(citations)
        self.assertTrue(all(r["source_sha256"] and r["turn_id"] for r in citations))
        unavailable=f.add_call(quarter=4,day="2026-09-01",summary=False)
        self.assertEqual(invoke(dict(args,after_capture_id=unavailable),context,f.registry).status,"not_established")
