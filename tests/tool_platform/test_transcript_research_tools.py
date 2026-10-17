"""Public transcript research checks with temporary stores and no network."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from quant_data.boundary import Stage1Application
from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher, RawPage
from quant_data.company.transcript_analysis import read_source
from quant_data.company.transcript_structured_store import prepare_output, prepare_assessment
from quant_data.company import transcript_structured_call_terra_sol as profile
from quant_data.company import transcript_structured_quality as quality
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import transcript_research_registry_profile, transcript_tools_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import writer_connection, quiet_immutable_read_connection
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.generate import generated_bytes
from quant_data.tool_platform import transcript_research_access as access
from quant_data.tool_platform.transcript_research_contracts import HISTORY, SEARCH, TARGET, KINDS, parse
from tests.company import test_transcript_structured_store as fixture
from tests.company.test_transcript_structured_call import example
from tests.company.test_transcript_analysis_pair import response
from tests.tool_platform.test_transcript_access import fields, metrics, CUTOFF, SOURCE

ROOT = Path(__file__).resolve().parents[2]

class TranscriptResearchTests(unittest.TestCase):
    def setUp(self):
        self.seed = fixture.StructuredStoreTests()
        self.seed.setUp()
        self.addCleanup(self.seed.tearDown)
        self.stores, self.registry = self.seed.stores, self.seed.registry
        self.seed.pub.publish([self.seed.row], [self.seed.assessment], published_at=CUTOFF)
        self.capture = self.seed.capture
        self.app = Stage1Application(self.stores, self.registry)

    def invoke(self, tool, arguments, version=None):
        version = version or ("2.0.0" if tool == TARGET else "1.0.0")
        request = {"api_version":"1.0","tool":tool,"tool_version":version,"arguments":arguments}
        before = mutation_fingerprint(self.stores)
        out, err = io.StringIO(), io.StringIO()
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            code = run(["call"], stdin=io.BytesIO(dumps_strict(request).encode()), stdout=out,
                       stderr=err, application=self.app)
        self.assertEqual(before, mutation_fingerprint(self.stores))
        self.assertEqual(err.getvalue(), "")
        result = loads_strict(out.getvalue())
        if code == 0:
            validate_schema(result["result"], self.registry.tool(tool, version)["output_schema"])
            self.assertNotIn(self.seed.temp.name, dumps_strict(result))
            self.assertEqual(result["result"]["truncation"]["next_cursor"], metrics(result["result"])["next_cursor"])
        return code, result

    def call(self, tool, **args):
        code, response = self.invoke(tool, args)
        self.assertEqual(code, 0, response)
        return response["result"]

    def add_call(self, quarter=2, day="2026-03-01", symbol="AAPL", captured=SOURCE,
                 event_id=None, summary=True, blocked=False, headline=None):
        body = json.loads(self.seed.raw)
        event_id = event_id or f"call_{symbol}_2026_{quarter}"
        event = {**self.seed.event, "id":event_id, "fiscalQuarter":quarter}
        body.update(ticker=symbol, eventId=event_id, fiscalQuarter=quarter, callDate=day)
        body["data"][0]["text"] += " retained second capture " + str(quarter)
        raw = dumps_strict(body).encode()
        EquiblesTranscriptPublisher(self.stores, self.registry).publish(symbol=symbol,
            instrument_id="fixture-"+symbol, event=event,
            pages=(RawPage(raw, captured, "equibles-transcripts/blobs/"+hashlib.sha256(raw).hexdigest()+".json"),))
        with quiet_immutable_read_connection(self.stores, "company") as c:
            capture = c.execute("SELECT capture_id FROM company_equibles_transcripts WHERE symbol=? AND event_id=? ORDER BY captured_at DESC,capture_id DESC LIMIT 1",
                                (symbol,event_id)).fetchone()[0]
        if summary:
            source = read_source(self.stores, capture)
            draft = example(source)
            if headline is not None: draft["headline"]["message"] = headline
            if blocked: draft["call"]["symbol"] = "WRONG"
            row = prepare_output(source=source, request_identity=hashlib.sha256(raw).hexdigest(),
                configuration=profile.configuration_for(), raw_response=response(draft, profile.EXTRACTOR),
                available_at="2026-09-09T00:00:00.000000Z")
            findings = quality.assess_brief(draft, source)
            assessment = prepare_assessment(row, kind="automatic", evaluator=quality.POLICY,
                reasoning_effort=None, outcome=findings["status"], assessment=findings,
                raw_evidence=dumps_strict(findings).encode(), available_at="2026-09-09T00:01:00.000000Z")
            self.seed.pub.publish([row],[assessment],published_at=CUTOFF)
        return capture

    def test_discovery_and_exact_predecessor_preserve_all_existing_tools(self):
        previous = transcript_research_registry_profile(self.registry)
        self.assertEqual(previous.revision,"2.89.0")
        self.assertEqual(previous.source_sha256,"c4293d0c5985429d97a7c6def93a8b8a2c841b02318bb7408818a9e924b9de1b")
        self.assertEqual(transcript_tools_registry_profile(self.registry).revision,"2.85.0")
        for entry in previous.tools:
            self.assertEqual(self.registry.tool(entry["id"],"1.0.0"),entry)
        self.assertEqual(self.registry.tool(TARGET),self.registry.tool(TARGET,"1.0.0"))
        for tool, version in ((HISTORY,"1.0.0"),(SEARCH,"1.0.0"),(TARGET,"2.0.0")):
            out = io.StringIO()
            self.assertEqual(run(["describe",tool,"--tool-version",version],stdout=out,stderr=io.StringIO(),application=self.app),0)
            for args in self.registry.tool(tool,version)["examples"]: parse(KINDS[tool],args)
        rb, frozen, versioned = generated_bytes(ROOT)
        self.assertEqual(rb,(ROOT/"config/system_registry.json").read_bytes())
        self.assertEqual(frozen,(ROOT/"quant_data/generated/tool_contract_schemas_v1.json").read_bytes())
        self.assertEqual(versioned,(ROOT/"quant_data/generated/tool_contract_schemas_v2.json").read_bytes())

    def test_history_orders_actual_dates_and_preserves_sections_and_fiscal_labels(self):
        newer = self.add_call(quarter=4,day="2026-03-01")
        older = self.add_call(quarter=3,day="2025-12-01")
        result = self.call(HISTORY,ticker="AAPL",sections=["guidance"],limit=3)
        rows = [fields(r) for r in result["records"]]
        self.assertEqual([r["capture_id"] for r in rows],[newer,self.capture,older])
        self.assertEqual([r["fiscal_quarter"] for r in rows],[4,1,3])
        self.assertEqual(json.loads(rows[1]["sections_json"]),{"guidance":self.seed.draft["guidance"]})
        self.assertEqual(rows[1]["call_date_basis"],"raw_page.callDate")
        self.assertIn("automatic_assessments_json",rows[1])
        limited = self.call(HISTORY,ticker="AAPL",limit=1)
        self.assertEqual(fields(limited["records"][0])["capture_id"],newer)
        self.assertTrue(limited["truncation"]["has_more"])
        self.assertIsNone(limited["truncation"]["next_cursor"])
        self.assertEqual(limited["truncation"]["total_known_count"],3)

    def test_quality_filters_and_missing_extraction_are_explicit(self):
        self.add_call(blocked=True)
        self.add_call(quarter=3,summary=False)
        result = self.call(HISTORY,ticker="AAPL")
        self.assertEqual(len(result["records"]),1)
        self.assertEqual(metrics(result)["excluded_blocked"],1)
        self.assertEqual(metrics(result)["missing_extractions"],1)
        all_rows = self.call(HISTORY,ticker="AAPL",include_blocked=True)["records"]
        self.assertEqual(len(all_rows),2)
        self.assertEqual(fields(all_rows[0])["automatic_quality_status"],"blocked")

    def test_latest_capture_per_event_does_not_fallback_to_old_summary(self):
        self.add_call(quarter=1,event_id=self.seed.event["id"],captured="2026-09-08T00:00:00.000000Z",summary=False)
        result = self.call(HISTORY,ticker="AAPL")
        self.assertEqual(result["records"],[])
        self.assertEqual(metrics(result)["missing_extractions"],1)

    def test_search_literal_sections_and_original_evidence(self):
        result = self.call(SEARCH,query="MARGIN",tickers=["AAPL"],sections=["guidance"])
        row = fields(result["records"][0])
        matches = json.loads(row["matches_json"])
        evidence = json.loads(row["evidence_json"])
        self.assertEqual(matches[0]["section"],"guidance")
        self.assertEqual(matches[0]["source_turn_ids"],["t1"])
        raw = json.loads(self.seed.raw)["data"][0]
        self.assertEqual(evidence[0]["text_excerpt"],raw["text"][:800])
        self.assertEqual(evidence[0]["speaker"]["speakerName"],raw["speakerName"])
        self.assertEqual(evidence[0]["source_sha256"],hashlib.sha256(self.seed.raw).hexdigest())
        self.assertEqual(self.call(SEARCH,query="margin",sections=["watch_items"])["records"],[])

    def test_search_empty_scan_has_continuation_and_scope_is_pinned(self):
        # Force a bounded scan with no matches and additional captures.
        self.add_call(symbol="ZZZ",captured="2026-09-07T01:00:00.000000Z")
        with patch.object(access,"SEARCH_SCAN_LIMIT",1):
            first = self.call(SEARCH,query="retained absent topic")
        cursor = metrics(first)["next_cursor"]
        self.assertEqual(first["records"],[])
        self.assertTrue(cursor)
        self.assertFalse(metrics(first)["selection_complete"])
        second = self.call(SEARCH,query="retained absent topic",cursor=cursor)
        self.assertIsNone(metrics(second)["next_cursor"])
        code,_ = self.invoke(SEARCH,{"query":"margin","cursor":cursor})
        self.assertEqual(code,2)

    def test_targeted_turns_context_deduplicates_and_retains_original(self):
        result = self.call(TARGET,capture_id=self.capture,turn_ids=["t3","t1"],context_turns=1)
        rows = [fields(r) for r in result["records"]]
        self.assertEqual([r["turn_id"] for r in rows],["t1","t2","t3","t4"])
        self.assertEqual([r["requested"] for r in rows],[True,False,True,False])
        self.assertEqual([json.loads(r["raw_turn_json"]) for r in rows],json.loads(self.seed.raw)["data"][:4])
        code,_ = self.invoke(TARGET,{"capture_id":self.capture,"turn_ids":["t9999"]})
        self.assertEqual(code,2)

    def test_asof_raw_model_assessment_and_future_cutoffs(self):
        self.assertEqual(self.call(HISTORY,ticker="AAPL",mode="as_of",as_of=SOURCE)["records"],[])
        rows = self.call(HISTORY,ticker="AAPL",mode="as_of",as_of=fixture.fixture.AT)["records"]
        self.assertEqual(fields(rows[0])["automatic_quality_status"],"unassessed")
        raw = self.call(TARGET,capture_id=self.capture,turn_ids=["t1"],mode="as_of",as_of=SOURCE)
        self.assertEqual(len(raw["records"]),1)
        for tool,args in ((HISTORY,{"ticker":"AAPL"}),(TARGET,{"capture_id":self.capture,"turn_ids":["t1"]})):
            code,_ = self.invoke(tool,{**args,"mode":"as_of","as_of":"2199-01-01T00:00:00Z"})
            self.assertEqual(code,2)

    def test_invalid_inputs_reject_before_any_store_read(self):
        cases=[(SEARCH,{"query":"  "}), (SEARCH,{"query":"margin","tickers":["AAPL","AAPL"]}),
            (HISTORY,{"ticker":"AAPL","start_date":"2026-02-30"}),
            (HISTORY,{"ticker":"AAPL","limit":True}),
            (HISTORY,{"ticker":"AAPL","db_path":"/tmp/other"}),
            (HISTORY,{"ticker":"AAPL","mode":"as_of","as_of":"2026-01-01"}),
            (TARGET,{"capture_id":self.capture,"turn_ids":["t1","t1"]}),
            (TARGET,{"capture_id":self.capture,"turn_ids":["t01"]}),
            (TARGET,{"capture_id":self.capture,"turn_ids":["t1"],"context_turns":3})]
        with patch.object(access.retained,"connection",side_effect=AssertionError("Must not open")):
            for tool,args in cases:
                with self.subTest(tool=tool,args=args):
                    self.assertEqual(self.invoke(tool,args)[0],2)

    def test_call_date_filter_does_not_use_fiscal_labels(self):
        self.add_call(quarter=4,day="2026-01-01")
        rows = self.call(HISTORY,ticker="AAPL",start_date="2026-02-01",end_date="2026-02-01")["records"]
        self.assertEqual([fields(r)["capture_id"] for r in rows],[self.capture])
        with patch.object(access,"_day",return_value=None):
            unknown = self.call(HISTORY,ticker="AAPL")
            self.assertTrue(all(fields(r)["call_date"] is None for r in unknown["records"]))
            filtered = self.call(HISTORY,ticker="AAPL",start_date="2020-01-01")
            self.assertEqual(filtered["records"],[])
            self.assertEqual(metrics(filtered)["unknown_call_dates"],2)

    def test_blocked_draft_keeps_invalid_citations_and_failed_review_separate(self):
        source = self.seed.source
        draft = deepcopy(self.seed.draft)
        draft["headline"]["source_turn_ids"] = ["bad-reference", "t9999"]
        row = prepare_output(source=source, request_identity="f"*64, configuration=profile.configuration_for(),
            raw_response=response(draft,profile.EXTRACTOR), available_at="2026-09-09T00:00:00.000000Z")
        findings = quality.assess_brief(draft,source)
        self.assertEqual(findings["status"],"blocked")
        automatic = prepare_assessment(row,kind="automatic",evaluator=quality.POLICY,reasoning_effort=None,
            outcome="blocked",assessment=findings,raw_evidence=dumps_strict(findings).encode(),
            available_at="2026-09-09T00:01:00.000000Z")
        review_doc = {"decision":"revised","brief":draft,"draft_findings":[]}
        review = prepare_assessment(row,kind="sol_review",evaluator="gpt-5.6-sol",reasoning_effort="medium",
            outcome="failed",assessment={"review":review_doc,"package_validation":{"status":"failed"}},
            raw_evidence=response(review_doc,profile.REVIEWER),available_at="2026-09-09T00:02:00.000000Z")
        self.seed.pub.publish([row],[automatic,review],published_at=CUTOFF)
        result=self.call(SEARCH,query="margin",sections=["headline"],include_blocked=True)
        fields_out=fields(result["records"][0])
        self.assertEqual(fields_out["automatic_quality_status"],"blocked")
        self.assertEqual(fields_out["latest_review_outcome"],"failed")
        self.assertEqual(json.loads(fields_out["evidence_json"]),[])
        self.assertEqual(set(json.loads(fields_out["unresolved_turn_ids_json"])),{"bad-reference","t9999"})
        self.assertEqual(json.loads(fields_out["matches_json"])[0]["item"],draft["headline"])

    def test_resource_bounds_and_checksums_reject_explicitly(self):
        with patch.object(access,"MAX_READ_BYTES",1):
            self.assertEqual(self.invoke(HISTORY,{"ticker":"AAPL"})[0],2)
        with patch.object(access,"MAX_HISTORY_CAPTURES",0):
            self.assertEqual(self.invoke(HISTORY,{"ticker":"AAPL"})[0],2)
        with writer_connection(self.stores,"company") as c:
            triggers=c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='company_equibles_transcript_pages'").fetchall()
            for row in triggers: c.execute('DROP TRIGGER "'+row[0]+'"')
            c.execute("UPDATE company_equibles_transcript_pages SET raw_body=?",(b"{}",))
        code,result = self.invoke(TARGET,{"capture_id":self.capture,"turn_ids":["t1"]})
        self.assertEqual(code,2)
        self.assertIn("checksum",dumps_strict(result))
