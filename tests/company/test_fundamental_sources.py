import json,unittest
from decimal import Decimal
from dataclasses import replace
from quant_data.company.fundamental_sources import read_selected_fundamentals
from quant_data.company.sharadar_repository import SharadarSf1Publisher
from quant_data.market.collection_identity_preparation import prepare_evidenced_mappings,identity_locators
from quant_data.market.collection_mappings import IdentityEvidence,CollectionMappingPublisher
from quant_data.market.collection_bindings import pin_binding
from quant_data.errors import ConflictError
from quant_data.tool_platform.context import ToolExecutionContext,ExecutionBudget,CapabilitySet,Deadline,CancellationToken,FixedClock
from tests.operations import test_collection_fmp as fixtures
from tests.company import test_sharadar_sf1 as sf

CUT="2026-09-09T06:00:00Z"
class FundamentalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.SelectedFmpTests("runTest");self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.f=self.base.f
        self.selections={k:pin_binding(self.f.stores,self.f.bindings[k]) for k in ("fmp_statements","sec_filings_companyfacts")}
        self.raw=b'{"datatable":{"columns":[{"name":"table","type":"String"},{"name":"ticker","type":"String"},{"name":"permaticker","type":"String"}],"data":[["SF1","AAPL","199059"]]},"meta":{"next_cursor_id":null}}'
        e=IdentityEvidence(self.raw,"fixture/tickers.json","2026-09-09T05:00:00Z","sharadar")
        p=prepare_evidenced_mappings(self.f.stores,membership_snapshot_id=self.selections["fmp_statements"].membership_snapshot_id,
            provider="sharadar",evidence=(e,),locators=identity_locators(e),captured_at="2026-09-09T05:00:00Z",source_reference="fixture/map.json")
        CollectionMappingPublisher(self.f.stores,self.f.registry).publish(p)
        self.selections["sharadar_fundamentals"]=pin_binding(self.f.stores,self.f.bindings["sharadar_fundamentals"])
        self.context=ToolExecutionContext(self.f.stores,"test","1"*64,"request","1.0.0","company.get_research_inputs",
            "1.0.0","test","1.0.0",ExecutionBudget(max_rows=1000,max_operations=1000,max_series=10),
            CapabilitySet(),Deadline(),CancellationToken(),FixedClock(Decimal("0"),CUT))
    def read(self,**kw):
        return read_selected_fundamentals(self.context,self.f.registry,source_symbol="AAPL",selections=self.selections,knowledge_cutoff=CUT,**kw)
    def test_all_sources_preserve_values_reporting_basis_and_original_versions(self):
        s,w=self.base.work("fmp_statements")
        u=next(u for u in w.units if u.endpoint=="income-statement" and dict(u.parameters)["period"]=="annual")
        self.base.publish(s,w.units,u,[{"symbol":"AAPL","date":"2025-09-30","revenue":10}])
        SharadarSf1Publisher(self.f.stores,self.f.registry).publish(sf.partition(sf.page(
            [sf.row(dimension="ARQ",revenue="11"),sf.row(dimension="MRQ",revenue="12")],captured_at="2026-09-09T05:30:00Z")),
            selection=self.selections["sharadar_fundamentals"],ingested_at=CUT)
        result=self.read()
        self.assertEqual(result["policy"],"source_separated_no_implicit_fallback")
        self.assertEqual(result["sources"]["fmp"]["status"],"available")
        self.assertIn('"revenue":10',result["sources"]["fmp"]["rows"][0]["payload_json"])
        self.assertEqual({r["dimension"]:r["values"]["revenue"] for r in result["sources"]["sharadar"]["rows"]},{"ARQ":"11","MRQ":"12"})
        self.assertEqual(result["sources"]["sec"]["availability_basis"],"existing_sec_source_availability")
        self.assertEqual(result["identity_link_status"],"partial")
    def test_source_native_sharadar_is_not_silently_used_as_missing_fmp(self):
        result=self.read()
        self.assertEqual(result["sources"]["fmp"]["status"],"not_established")
        self.assertEqual(result["sources"]["sharadar"]["status"],"not_established")
        self.assertEqual(result["metric_equivalence"],"not_asserted")
    def test_forged_source_pin_fails_before_any_source_read(self):
        selection=self.selections["sharadar_fundamentals"]
        self.selections["sharadar_fundamentals"]=replace(selection,scope_sha256="f"*64)
        with self.assertRaises(ConflictError):self.read()


    def test_ticker_reuse_does_not_attribute_old_facts_to_a_new_permanent_subject(self):
        SharadarSf1Publisher(self.f.stores,self.f.registry).publish(
            sf.partition(sf.page(captured_at="2026-09-09T05:30:00Z")),
            selection=self.selections["sharadar_fundamentals"],ingested_at="2026-09-09T05:30:00Z")
        self.assertEqual(len(self.read()["sources"]["sharadar"]["rows"]),1)
        e=IdentityEvidence(self.raw.replace(b"199059",b"999999"),"fixture/new-ticker.json","2026-09-09T05:45:00Z","sharadar")
        p=prepare_evidenced_mappings(self.f.stores,membership_snapshot_id=self.selections["fmp_statements"].membership_snapshot_id,
            provider="sharadar",evidence=(e,),locators=identity_locators(e),captured_at="2026-09-09T05:45:00Z",source_reference="fixture/new-map.json")
        CollectionMappingPublisher(self.f.stores,self.f.registry).publish(p)
        self.selections["sharadar_fundamentals"]=pin_binding(self.f.stores,self.f.bindings["sharadar_fundamentals"])
        result=self.read()["sources"]["sharadar"]
        self.assertEqual(result["provider_subject"],"999999")
        self.assertEqual(result["rows"],[])
