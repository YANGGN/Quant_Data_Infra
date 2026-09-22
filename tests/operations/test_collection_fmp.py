import hashlib,json,unittest
from dataclasses import replace
from quant_data.company.fmp_analyst_history import parse_analyst_response
from quant_data.errors import ConflictError
from quant_data.market.collection_bindings import pin_binding
from quant_data.operations.collection_fmp import fmp_units,SelectedFmpPublisher,estimate_continuation,DISTINCT_REVIEW
from quant_data.stores import quiet_immutable_read_connection
from tests.operations import test_collection_sec as secfixtures

CUT="2026-09-09T05:00:00Z"
CAPTURE="2026-09-09T04:30:00Z"
class SelectedFmpTests(unittest.TestCase):
    def setUp(self):
        self.base=secfixtures.SelectedSecTests("runTest");self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.base.acquire();self.base.publish()
        self.f=self.base.f
    def work(self,key,**kw):
        s=pin_binding(self.f.stores,self.f.bindings[key])
        w=fmp_units(self.f.stores,s,mode="historical_backfill",observation_window="full-selection",cutoff=CUT,**kw)
        return s,w
    def publish(self,selection,units,unit,rows):
        raw=json.dumps(rows).encode()
        receipt={"request_id":unit.request_id,"unit_id":unit.unit_id,"status":200,
            "content_sha256":hashlib.sha256(raw).hexdigest(),"captured_at":CAPTURE}
        p=SelectedFmpPublisher(stores=self.f.stores,registry=self.f.registry,selection=selection,
            units=units,cutoff=CUT)
        return p(unit=unit,retained=None,receipt=receipt,body=raw)
    def test_statement_matrix_and_missing_issuer_are_explicit(self):
        s,w=self.work("fmp_statements")
        self.assertEqual(len(w.units),8)
        self.assertEqual(w.missing_issuer_ciks,("0001067983",))
        self.assertEqual({dict(u.parameters)["period"] for u in w.units},{"annual","quarter"})
        self.assertTrue(all(dict(u.parameters)["limit"]=="1000" for u in w.units))
        u=next(u for u in w.units if u.endpoint=="income-statement" and dict(u.parameters)["period"]=="annual")
        result=self.publish(s,w.units,u,[{"symbol":"AAPL","date":"2025-09-30","revenue":100}])
        self.assertEqual(result["outcome"],"succeeded");self.assertEqual(result["coverage"],"partial_history")
    def test_annual_quarter_estimates_use_only_the_analyst_family_and_keep_target_dates(self):
        s,w=self.work("fmp_analyst_estimates")
        self.assertEqual(len(w.units),2)
        for u in w.units:
            self.publish(s,w.units,u,[{"symbol":"AAPL","date":"2027-09-30","estimatedRevenueAvg":100}])
        with quiet_immutable_read_connection(self.f.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_fmp_analyst_captures").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT count(*) FROM company_fmp_research_snapshots").fetchone()[0],0)
    def test_actions_and_earnings_have_separate_units_and_do_not_schedule_estimates(self):
        for key,expected in (("dividends_splits",{"dividends","splits"}),("earnings_dates",{"earnings"})):
            s,w=self.work(key)
            self.assertEqual({u.endpoint for u in w.units},expected)
            for u in w.units:
                self.assertEqual(self.publish(s,w.units,u,[])["coverage"],"empty")
    def test_distinct_endpoints_require_explicit_selection(self):
        s,w=self.work("fmp_distinct_inputs")
        self.assertEqual(w.units,())
        s,w=self.work("fmp_distinct_inputs",distinct_endpoints=("grades-consensus","revenue-product-segmentation"))
        self.assertEqual(len(w.units),3)
        self.assertEqual(len(DISTINCT_REVIEW),7)
    def test_estimate_pagination_is_explicit_capped_and_detects_repeated_pages(self):
        s,w=self.work("fmp_analyst_estimates");u=w.units[0]
        publisher=SelectedFmpPublisher(stores=self.f.stores,registry=self.f.registry,selection=s,units=w.units,cutoff=CUT)
        small=replace(u,parameters=tuple(sorted({**dict(u.parameters),"limit":"1"}.items())))
        parsed=parse_analyst_response(b'[{"symbol":"AAPL","date":"2027-09-30","estimatedRevenueAvg":100}]',
            endpoint="analyst-estimates",parameters=dict(small.parameters),subject=publisher.subjects["AAPL"],
            captured_at=CAPTURE,source_reference="fixture/estimate-page.json")
        child,status=estimate_continuation(small,parsed)
        self.assertEqual(dict(child.parameters)["page"],"1")
        self.assertEqual(status,"continuation_required")
        with self.assertRaises(ConflictError):estimate_continuation(small,parsed,seen_page_hashes=(parsed.content_sha256,))
        last=replace(small,parameters=tuple(sorted({**dict(small.parameters),"page":"9"}.items())))
        with self.assertRaises(ConflictError):
            estimate_continuation(small,replace(parsed,raw_row_count=0))
        parsed=parse_analyst_response(parsed.raw_body,endpoint=parsed.endpoint,parameters=dict(last.parameters),
            subject=parsed.subject,captured_at=parsed.captured_at,source_reference=parsed.source_reference)
        self.assertEqual(estimate_continuation(last,parsed,seen_target_dates=("2028-09-30",)),(None,"page_cap_reached_incomplete"))


    def test_estimate_reordered_and_non_extending_dates_stop_continuation(self):
        s,w=self.work("fmp_analyst_estimates")
        publisher=SelectedFmpPublisher(stores=self.f.stores,registry=self.f.registry,selection=s,units=w.units,cutoff=CUT)
        u=replace(w.units[0],parameters=tuple(sorted({**dict(w.units[0].parameters),"page":"1","limit":"2"}.items())))
        def parse(dates):
            return parse_analyst_response(json.dumps([{"symbol":"AAPL","date":d,"estimatedRevenueAvg":1} for d in dates]).encode(),
                endpoint="analyst-estimates",parameters=dict(u.parameters),subject=publisher.subjects["AAPL"],
                captured_at=CAPTURE,source_reference="fixture/page.json")
        prior=("2026-09-30","2027-09-30")
        with self.assertRaisesRegex(ConflictError,"non-progressing"):
            estimate_continuation(u,parse(prior[::-1]),seen_target_dates=prior)
        with self.assertRaisesRegex(ConflictError,"oldest"):
            estimate_continuation(u,parse(("2027-09-30","2028-09-30")),seen_target_dates=prior)
        child,status=estimate_continuation(u,parse(("2024-09-30","2025-09-30")),seen_target_dates=prior)
        self.assertEqual(dict(child.parameters)["page"],"2")
        self.assertEqual(status,"continuation_required")


    def test_full_ambiguous_estimate_page_cannot_claim_pagination_progress(self):
        s,w=self.work("fmp_analyst_estimates")
        publisher=SelectedFmpPublisher(stores=self.f.stores,registry=self.f.registry,selection=s,units=w.units,cutoff=CUT)
        u=replace(w.units[0],parameters=tuple(sorted({**dict(w.units[0].parameters),"page":"1","limit":"2"}.items())))
        parsed=parse_analyst_response(
            b'[{"symbol":"AAPL","date":"2027-12-31","estimatedRevenueAvg":1},{"symbol":"AAPL","date":"2027-12-31","estimatedRevenueAvg":2}]',
            endpoint="analyst-estimates",parameters=dict(u.parameters),subject=publisher.subjects["AAPL"],
            captured_at=CAPTURE,source_reference="fixture/ambiguous-page.json")
        self.assertEqual(parsed.rows,())
        with self.assertRaisesRegex(ConflictError,"cannot establish"):
            estimate_continuation(u,parsed,seen_target_dates=("2027-12-31",))
