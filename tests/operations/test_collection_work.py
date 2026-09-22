from __future__ import annotations
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from quant_data.errors import ConflictError,ValidationError,ResourceLimitError
from quant_data.market.collection_bindings import load_bindings,PinnedCollection,BoundSubject,RetainedInstrument
from quant_data.operations.collection_plan import (
    AcquisitionUnit,ProviderBudget,RetainedResponse,build_plan,validate_unit,validate_budget,reusable)
from quant_data.operations.collection_queue import run_queue,QueueResponse
from quant_data.operations.collection_inventory import read_original,inventory_units
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.fingerprint import mutation_fingerprint

ROOT=Path(__file__).resolve().parents[2]
AT="2026-09-09T01:00:00Z"
BINDINGS=load_bindings(ROOT/"config/collection_bindings.json")

def selection(key="fmp_statements",symbols=("AAPL",)):
    binding=BINDINGS[key]
    return PinnedCollection(binding,"membership","mapping",tuple(
        BoundSubject(s,s,s,"instrument-"+s,"0000320193","resolved","evidence") for s in symbols),(),"1"*64)

def unit(symbol="AAPL",*,key="fmp_statements",endpoint="income-statement",mode="historical_backfill",window="initial"):
    provider=BINDINGS[key].provider
    subject="0000320193" if provider=="sec" else symbol
    parameters={"cik":subject} if provider=="sec" else {"symbol":symbol}
    if endpoint=="income-statement": parameters.update(period="annual",limit="100")
    return AcquisitionUnit(key,provider,endpoint,subject,tuple(sorted(parameters.items())),mode,window,(symbol,),"1"*64,
        max_response_bytes=1024,timeout_seconds=1)

def budget(provider="fmp",requests=10,daily=None):
    return ProviderBudget(provider,requests,4096,60,400,daily)

def plan(units=None,*,selections=None,budgets=None,retained=()):
    units=tuple(units or (unit(),))
    return build_plan(selections=selections or (selection(symbols=tuple(sorted({s for u in units for s in u.selected_symbols}))),),
        units=units,budgets=budgets or (budget(),),retained=retained,created_at=AT)

def clock(): return datetime(2026,9,9,1,tzinfo=timezone.utc)

class CollectionPlanTests(unittest.TestCase):
    def test_request_identity_survives_new_membership_and_historical_label(self):
        first=unit();second=replace(first,selection_sha256="2"*64,observation_window="renamed")
        self.assertEqual(first.request_id,second.request_id)
        self.assertEqual(first.unit_id,second.unit_id)
        self.assertNotEqual(first.unit_id,replace(first,mode="incremental",observation_window=AT).unit_id)

    def test_exact_retained_bytes_reused_but_missing_raw_blocks_repeat(self):
        u=unit()
        retained=RetainedResponse(u.request_id,"2026-09-08T01:00:00Z","2"*64,"blobs/x.json",
            True,"partial_history",True,True,"capture")
        result=plan(retained=(retained,))
        self.assertEqual(result.report()["retained_request_reuses"],1)
        self.assertEqual(result.report()["maximum_new_requests_by_provider"],{})
        self.assertEqual(result.reuse[0][1].coverage,"partial_history")
        result=plan(retained=(replace(retained,raw_verified=False),))
        self.assertEqual(len(result.blocked_units),1)
        self.assertEqual(result.report()["maximum_new_requests_by_provider"],{})
        fresh=replace(u,mode="incremental",observation_window=AT)
        self.assertIsNone(reusable(fresh,(retained,),cutoff=AT))
        self.assertIsNone(reusable(u,(replace(retained,captured_at="2026-09-10T00:00:00Z"),),cutoff=AT))

    def test_sec_share_classes_deduplicate_by_cik_without_losing_members(self):
        s=selection("sec_filings_companyfacts",("BRK.A","BRK.B"))
        u=unit("BRK.A",key="sec_filings_companyfacts",endpoint="companyfacts")
        u=replace(u,selected_symbols=("BRK.A","BRK.B"))
        result=plan((u,),selections=(s,),budgets=(budget("sec"),))
        self.assertEqual(len(result.units),1)
        self.assertEqual(len(result.units[0].selected_symbols),2)

    def test_request_symbol_and_scope_drift_are_rejected(self):
        for changed in (replace(unit(),parameters=(("symbol","MSFT"),)),
            replace(unit(),endpoint="unknown"),replace(unit(),provider="equibles"),
            replace(unit(),parameters=(("apikey","secret"),("symbol","AAPL")))):
            with self.subTest(changed=changed),self.assertRaises(ValidationError): validate_unit(changed)
        with self.assertRaises(ConflictError): plan((replace(unit(),selection_sha256="2"*64),))
        with self.assertRaises(ValidationError): plan((unit("MSFT"),),selections=(selection(),))

    def test_retained_etfs_are_eligible_but_arbitrary_prices_are_not(self):
        s=replace(selection("daily_prices"),retained=(RetainedInstrument("spy","SPY","etf","etfs"),))
        u=replace(unit("SPY",key="daily_prices",endpoint="historical-price-eod/full"),selected_symbols=())
        self.assertEqual(len(plan((u,),selections=(s,)).units),1)
        with self.assertRaises(ValidationError):
            plan((replace(u,subject="QQQ",parameters=(("symbol","QQQ"),)),),selections=(s,))

    def test_supported_matrix_excludes_options_and_macro(self):
        from quant_data.operations.collection_plan import ENDPOINTS
        self.assertEqual(set(ENDPOINTS),set(BINDINGS))
        self.assertNotIn("options",ENDPOINTS);self.assertNotIn("macro",ENDPOINTS)
        with self.assertRaises(ValidationError): validate_budget(budget("equibles",daily=100000))
        validate_budget(ProviderBudget("equibles",1000,400*1024*1024,3600,1000,100000))

    def test_duplicate_request_with_different_budget_is_not_silently_selected(self):
        with self.assertRaises(ConflictError):
            plan((unit(),replace(unit(),max_rows=999)))

    def test_prepared_unresolved_members_are_reported_and_not_fetched(self):
        s=selection(symbols=("AAPL","MSFT"))
        s=replace(s,subjects=(s.subjects[0],replace(s.subjects[1],status="unresolved",reason="unverified")))
        result=plan(selections=(s,))
        self.assertEqual(result.identity_gaps,(("fmp_statements","MSFT","unverified"),))
        self.assertFalse(result.report()["live_authorization"])

class CollectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir="/tmp")
        self.root=Path(self.tmp.name)/"fmp"
        self.calls=[];self.publications=[]
    def tearDown(self): self.tmp.cleanup()
    def fetch(self,**kwargs):
        self.calls.append(kwargs["unit"].unit_id)
        return QueueResponse(200,b"[]",AT,(("content-type","application/json"),))
    def publish(self,**kwargs):
        self.publications.append(kwargs)
        return {"outcome":"succeeded","written_count":1}
    def run_plan(self,p=None,**kw):
        return run_queue(root=self.root,plan=p or plan(),provider="fmp",fetch=kw.pop("fetch",self.fetch),
            publish=kw.pop("publish",self.publish),utcnow=clock,sleeper=lambda _:None,**kw)

    def test_exact_replay_causes_no_second_request_or_publication(self):
        result=self.run_plan()
        self.assertEqual((result["requests"],result["published"]),(1,1))
        before=(self.root/"ledger.json").read_bytes()
        result=self.run_plan()
        self.assertEqual((result["requests"],result["reused"]),(0,1))
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.publications),1)
        self.assertEqual(before,(self.root/"ledger.json").read_bytes())

    def test_publish_failure_recovers_bytes_without_network(self):
        def fail(**kwargs): raise RuntimeError("publication interrupted")
        with self.assertRaises(RuntimeError): self.run_plan(publish=fail)
        result=self.run_plan()
        self.assertEqual((result["requests"],result["published"]),(0,1))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.publications[0]["body"],b"[]")
        self.assertEqual(self.publications[0]["receipt"]["captured_at"],"2026-09-09T01:00:00.000000Z")

    def test_uncertain_transport_is_charged_and_never_retried(self):
        def fail(**kwargs): self.calls.append("failed");raise RuntimeError("secret-network-error")
        with self.assertRaisesRegex(ConflictError,"without retry"): self.run_plan(fetch=fail)
        state=json.loads((self.root/"ledger.json").read_bytes())
        self.assertEqual(state["usage"]["2026-09-09"]["charged_attempts"],1)
        self.assertIsNotNone(state["pending"])
        with self.assertRaisesRegex(ConflictError,"no retry"): self.run_plan()
        self.assertEqual(self.calls,["failed"])

    def test_pending_response_recovery_and_publication_receipt_recovery(self):
        self.run_plan()
        state=json.loads((self.root/"ledger.json").read_bytes())
        state["units"][unit().unit_id]["status"]="captured"
        from quant_data.operations.equibles_transcript_backfill import atomic
        atomic(self.root/"ledger.json",state,replace=True)
        result=self.run_plan()
        self.assertEqual(result["requests"],0)
        self.assertEqual(len(self.publications),1)

    def test_per_invocation_and_shared_daily_budgets_persist_across_plans(self):
        p=plan((unit(),unit("MSFT"),unit("NVDA")),budgets=(budget(requests=1,daily=2),))
        first=self.run_plan(p);second=self.run_plan(p);third=self.run_plan(p)
        self.assertEqual(first["outcome"],"invocation_budget")
        self.assertEqual(second["requests"],1)
        self.assertEqual(third["outcome"],"daily_budget")
        self.assertEqual(len(self.calls),2)

    def test_missing_original_evidence_blocks_without_acquisition(self):
        u=unit()
        retained=RetainedResponse(u.request_id,AT,"2"*64,"lost.json",True,"partial_history",False,True,"capture")
        result=self.run_plan(plan(retained=(retained,)))
        self.assertEqual(result["outcome"],"retained_evidence_required")
        self.assertEqual(self.calls,[])

    def test_corrupt_retained_bytes_stop_before_network(self):
        self.run_plan(publish=lambda **kw: {"outcome":"unchanged"})
        # A captured response is needed for publication after interruption.
        state=json.loads((self.root/"ledger.json").read_bytes())
        state["units"][unit().unit_id]["status"]="captured"
        from quant_data.operations.equibles_transcript_backfill import atomic
        atomic(self.root/"ledger.json",state,replace=True)
        (self.root/"publications"/(unit().unit_id+".json")).unlink()
        blob=next((self.root/"blobs").iterdir());blob.write_bytes(b"{}")
        with self.assertRaises(ConflictError): self.run_plan()
        self.assertEqual(len(self.calls),1)

    def test_credentials_and_unsuccessful_responses_are_not_retained(self):
        def echo(**kw): return QueueResponse(200,b'{"token":"test-secret"}',AT)
        with self.assertRaises(ConflictError): self.run_plan(fetch=echo,secret_values=("test-secret",))
        self.assertEqual(list((self.root/"blobs").iterdir()),[])
        self.assertNotIn("test-secret",(self.root/"ledger.json").read_text())

    def test_equibles_cannot_use_a_second_quota_ledger(self):
        with self.assertRaisesRegex(ValidationError,"existing shared checkpoint"):
            run_queue(root=self.root,plan=plan(),provider="equibles",fetch=self.fetch,publish=self.publish)

class CollectionInventoryTests(unittest.TestCase):
    def test_original_byte_hash_and_path_escape(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root=Path(tmp);body=b"[]";sha=hashlib.sha256(body).hexdigest()
            (root/"source.json").write_bytes(body)
            self.assertEqual(read_original(root,"source.json",sha),body)
            self.assertIsNone(read_original(root,"missing.json",sha))
            with self.assertRaises(ConflictError): read_original(root,"source.json","0"*64)
            with self.assertRaises(ValidationError): read_original(root,"../source.json",sha)
            (root/"link.json").symlink_to(root/"source.json")
            with self.assertRaises(ValidationError): read_original(root,"link.json",sha)

    def test_empty_all_family_inventory_uses_only_explicit_immutable_stores(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            stores=StoreMap.four_explicit(**{r:Path(tmp)/(r+".sqlite") for r in ("market","macro","company","news")})
            registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
            initialize_all(stores,registry)
            items=[unit(),unit(key="daily_prices",endpoint="historical-price-eod/full"),
                unit(key="sec_filings_companyfacts",endpoint="companyfacts"),unit(key="dividends_splits",endpoint="dividends"),
                unit(key="earnings_dates",endpoint="earnings"),unit(key="fmp_analyst_estimates",endpoint="analyst-estimates"),
                unit(key="fmp_distinct_inputs",endpoint="grades"),unit(key="equibles_transcripts",endpoint="earnings-call-catalogue")]
            items.append(replace(unit(key="news",endpoint="news/general-latest"),subject="shared_feed",parameters=()))
            items.append(replace(unit(key="sharadar_fundamentals",endpoint="SHARADAR/SF1"),
                parameters=(("dimension","ARQ"),("ticker","AAPL"))))
            before=mutation_fingerprint(stores)
            result=inventory_units(stores,units=items,cutoff=AT,source_root=Path(tmp))
            self.assertEqual(result.entries,())
            self.assertEqual(before,mutation_fingerprint(stores))
            self.assertFalse(any(x[0]=="sharadar_fundamentals" for x in result.unchecked))


class PopulatedCollectionInventoryTests(unittest.TestCase):
    def test_retained_fmp_response_reused_at_original_capture_with_cap_visible(self):
        from tests.company.test_fmp_research import FmpResearchTests
        fixture=FmpResearchTests("test_replay_is_zero_and_annual_q4_same_end_coexist")
        fixture.setUp()
        try:
            prepared=fixture.prepared([
                {"symbol":"NST","date":str(year)+"-12-31","calendarYear":str(year),"period":"FY","revenue":10}
                for year in (2023,2024,2025)])
            fixture.publisher.publish(prepared,request_id="inventory-fixture")
            u=replace(unit("NST"),parameters=(("limit","3"),("period","annual"),("symbol","NST")))
            root=Path(fixture.temp.name)/"evidence";root.mkdir()
            path=root/prepared.source_reference;path.parent.mkdir(parents=True);path.write_bytes(prepared.raw_body)
            before=mutation_fingerprint(fixture.store_map)
            result=inventory_units(fixture.store_map,units=(u,),cutoff=AT,source_root=root)
            self.assertEqual(len(result.entries),1)
            self.assertEqual(result.entries[0].raw_state,"verified")
            self.assertEqual(result.entries[0].coverage,"partial_history")
            self.assertEqual(result.retained[0].request_id,u.request_id)
            self.assertEqual(result.retained[0].captured_at,"2026-09-05T12:00:00.000000Z")
            self.assertEqual(plan((u,),retained=result.retained).report()["retained_request_reuses"],1)
            older=inventory_units(fixture.store_map,units=(u,),cutoff="2026-09-01T00:00:00Z",source_root=root)
            self.assertEqual(older.entries,())
            path.unlink()
            missing=inventory_units(fixture.store_map,units=(u,),cutoff=AT,source_root=root)
            self.assertEqual(missing.entries[0].raw_state,"missing")
            self.assertEqual(len(plan((u,),retained=missing.retained).blocked_units),1)
            self.assertEqual(before,mutation_fingerprint(fixture.store_map))
        finally: fixture.tearDown()

    def test_retained_analyst_pages_and_periods_keep_exact_request_identity(self):
        from tests.company.test_fmp_analyst_history import AnalystHistoryTests
        fixture=AnalystHistoryTests("test_forecast_dates_do_not_backdate_availability_and_periods_coexist")
        fixture.setUp()
        try:
            root=Path(fixture.temp.name)/"evidence";root.mkdir()
            for period,page,year in (("annual","0",2025),("annual","1",2024),("quarter","0",2025)):
                prepared=fixture.prepare([{"symbol":"NST","date":str(year)+"-12-31","epsAvg":2}],period=period,page=page)
                fixture.publisher.publish(prepared,request_id="inventory-fixture")
                path=root/prepared.source_reference;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(prepared.raw_body)
            u=replace(unit("NST",key="fmp_analyst_estimates",endpoint="analyst-estimates"),
                parameters=(("limit","100"),("page","0"),("period","annual"),("symbol","NST")))
            result=inventory_units(fixture.stores,units=(u,),cutoff=AT,source_root=root)
            self.assertEqual(len(result.entries),3)
            self.assertEqual(len({r.request_id for r in result.retained}),3)
            chosen=reusable(u,result.retained,cutoff=AT)
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen.coverage,"partial_history")
        finally: fixture.tearDown()
