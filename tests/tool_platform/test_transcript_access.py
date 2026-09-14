"""Public retained-transcript contracts: cutoff, pagination and read-only boundaries."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher, RawPage
from quant_data.company.transcript_structured_store import prepare_output, prepare_assessment
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import transcript_tools_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import writer_connection
from quant_data.tool_platform import transcript_access as access
from quant_data.tool_platform.generate import generated_bytes
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.transcript_contracts import TOOLS, KINDS, parse
from tests.company import test_transcript_structured_store as fixture
from tests.company.test_transcript_analysis_pair import response

ROOT = Path(__file__).resolve().parents[2]
SOURCE = fixture.fixture.SOURCE_AT
COMPLETE = fixture.fixture.AT
ASSESSED = fixture.fixture.LATER
CUTOFF = fixture.PUBLISHED

def fields(record):
    return {item["name"]: item["value"] for item in record["fields"]}

def metrics(result):
    return {item["name"]: item["value"] for item in result["diagnostics"][0]["metrics"]}


class TranscriptAccessTests(unittest.TestCase):
    def setUp(self):
        # Existing publishers establish realistic lineage in explicitly temporary stores.
        self.seed = fixture.StructuredStoreTests()
        self.seed.setUp()
        self.addCleanup(self.seed.tearDown)
        self.stores, self.registry = self.seed.stores, self.seed.registry
        self.capture = self.seed.capture
        self.seed.pub.publish([self.seed.row], [self.seed.assessment], published_at=CUTOFF)
        self.app = Stage1Application(self.stores, self.registry)

    def invoke(self, tool, arguments):
        request = {"api_version": "1.0", "tool": tool, "tool_version": "1.0.0",
                   "arguments": arguments}
        stdout, stderr = io.StringIO(), io.StringIO()
        before = mutation_fingerprint(self.stores)
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            code = run(["call"], stdin=io.BytesIO(dumps_strict(request).encode()),
                       stdout=stdout, stderr=stderr, application=self.app)
        self.assertEqual(before, mutation_fingerprint(self.stores))
        self.assertEqual(stderr.getvalue(), "")
        return code, loads_strict(stdout.getvalue())

    def successful(self, tool, **args):
        code, response = self.invoke(tool, args)
        self.assertEqual(code, 0, response)
        validate_schema(response["result"], self.registry.tool(tool, "1.0.0")["output_schema"])
        for key in ("input_schema_sha256", "output_schema_sha256", "registry_schema_sha256"):
            self.assertRegex(response["receipt"][key], "^[a-f0-9]{64}$")
        self.assertNotIn(self.seed.temp.name, dumps_strict(response))
        self.assertEqual(response["result"]["truncation"]["next_cursor"], metrics(response["result"])["next_cursor"])
        return response["result"]

    def publish_raw(self, symbol="MSFT", quarter=2, captured=SOURCE):
        body = json.loads(self.seed.raw)
        body.update(ticker=symbol, fiscalQuarter=quarter, eventId=f"call_{symbol}_2026_{quarter}")
        body["data"][0]["text"] = "Original — text" + chr(10) + "with Unicode and whitespace."
        body["data"][0]["speakerName"] = None
        raw = dumps_strict(body).encode()
        event = {**self.seed.event, "id": body["eventId"], "fiscalQuarter": quarter}
        EquiblesTranscriptPublisher(self.stores, self.registry).publish(
            symbol=symbol, instrument_id="fixture-" + symbol, event=event,
            pages=(RawPage(raw, captured, "equibles-transcripts/blobs/" + hashlib.sha256(raw).hexdigest() + ".json"),))
        return body

    def test_discovery_examples_and_exact_predecessor(self):
        previous = transcript_tools_registry_profile(self.registry)
        self.assertEqual(previous.revision, "2.85.0")
        self.assertEqual(previous.source_sha256,
            "1b46ab14244717708470bdafcd840fee804c66cff2a572c6a99bba7a482afff2")
        self.assertEqual({t["id"]: t for t in self.registry.tools if t["id"] not in TOOLS},
                         {t["id"]: t for t in previous.tools})
        for tool in TOOLS:
            out = io.StringIO()
            self.assertEqual(run(["describe", tool, "--tool-version", "1.0.0"],
                stdout=out, stderr=io.StringIO(), application=self.app), 0)
            description = loads_strict(out.getvalue())
            self.assertIn(tool, dumps_strict(description))
            contract = self.registry.tool(tool, "1.0.0")
            for example in contract["examples"]:
                validate_schema(example, contract["input_schema"])
                parse(KINDS[tool], example)
            self.assertFalse(contract["live_capability"]["possible"])
        rb, frozen, versioned = generated_bytes(ROOT)
        self.assertEqual(rb, (ROOT / "config/system_registry.json").read_bytes())
        self.assertEqual(frozen, (ROOT / "quant_data/generated/tool_contract_schemas_v1.json").read_bytes())
        self.assertEqual(versioned, (ROOT / "quant_data/generated/tool_contract_schemas_v2.json").read_bytes())

    def test_raw_pages_reassemble_exact_provider_turns_and_lineage(self):
        expected = json.loads(self.seed.raw)["data"]
        cursor = None
        turns = []
        while True:
            args = {"capture_id": self.capture, "limit": 1, "mode": "as_of", "as_of": SOURCE}
            if cursor: args["cursor"] = cursor
            result = self.successful(TOOLS[1], **args)
            self.assertEqual(result["records"][0]["record_type"], "transcript_metadata")
            self.assertEqual(metrics(result)["cutoff"], SOURCE)
            selected = [fields(r) for r in result["records"][1:]]
            turns.extend(selected)
            cursor = metrics(result)["next_cursor"]
            self.assertEqual(result["truncation"]["applied"], bool(cursor))
            if not cursor: break
        self.assertEqual([json.loads(t["raw_turn_json"]) for t in turns], expected)
        self.assertEqual([t["text"] for t in turns], [t["text"] for t in expected])
        self.assertEqual([t["turn_id"] for t in turns], [f"t{i+1}" for i in range(len(expected))])
        self.assertTrue(all(t["source_sha256"] == hashlib.sha256(self.seed.raw).hexdigest() for t in turns))

    def test_search_filters_pagination_and_missing_ticker(self):
        self.publish_raw()
        result = self.successful(TOOLS[0], mode="as_of", as_of=CUTOFF, limit=1)
        self.assertEqual(fields(result["records"][0])["symbol"], "AAPL")
        cursor = metrics(result)["next_cursor"]
        self.assertIsNotNone(cursor)
        second = self.successful(TOOLS[0], mode="as_of", as_of=CUTOFF, limit=100, cursor=cursor)
        self.assertEqual([fields(r)["symbol"] for r in second["records"]], ["MSFT"])
        self.assertFalse(fields(second["records"][0])["extraction_available"])
        self.assertIsNone(metrics(second)["next_cursor"])
        filtered = self.successful(TOOLS[0], ticker="MSFT", fiscal_year=2026, fiscal_quarter=2)
        self.assertEqual(len(filtered["records"]), 1)
        missing = self.successful(TOOLS[0], ticker="NONE")
        self.assertEqual((missing["status"], missing["records"]), ("not_established", []))
        code, _ = self.invoke(TOOLS[0], {"mode": "as_of", "as_of": CUTOFF, "cursor": cursor, "ticker": "MSFT"})
        self.assertEqual(code, 2)

    def test_unicode_and_unknown_speaker_remain_original(self):
        original = self.publish_raw()
        search = self.successful(TOOLS[0], ticker="MSFT")
        capture = fields(search["records"][0])["capture_id"]
        result = self.successful(TOOLS[1], capture_id=capture)
        self.assertEqual(json.loads(fields(result["records"][1])["raw_turn_json"]), original["data"][0])
        self.assertEqual(fields(result["records"][1])["text"], original["data"][0]["text"])

    def test_cutoff_excludes_source_output_and_assessment_independently(self):
        for tool in TOOLS:
            args = {} if tool == TOOLS[0] else {"capture_id": self.capture}
            result = self.successful(tool, **args, mode="as_of", as_of="2026-09-06T23:59:59Z")
            self.assertEqual((result["status"], result["records"]), ("not_established", []))
        missing = self.successful(TOOLS[2], capture_id=self.capture, mode="as_of", as_of=SOURCE)
        self.assertEqual(missing["status"], "not_established")
        self.assertEqual(fields(missing["records"][0])["reason"], "no_extraction_available_at_cutoff")
        first = self.successful(TOOLS[2], capture_id=self.capture, mode="as_of", as_of=COMPLETE)
        self.assertEqual(len(first["records"]), 1)
        self.assertEqual(fields(first["records"][0])["automatic_quality_status"], "unassessed")
        self.assertEqual(json.loads(fields(first["records"][0])["structured_json"]), self.seed.draft)
        assessed = self.successful(TOOLS[2], capture_id=self.capture, mode="as_of", as_of=ASSESSED)
        self.assertEqual(len(assessed["records"]), 2)
        self.assertEqual(json.loads(fields(assessed["records"][1])["assessment_json"]), self.seed.assessment["assessment"])
        self.assertEqual(fields(assessed["records"][0])["review_status"], "unreviewed")

    def test_flagged_original_is_not_replaced_or_approved_by_review(self):
        draft = deepcopy(self.seed.draft)
        draft["call"]["symbol"] = "WRONG"
        row = prepare_output(source=self.seed.source, request_identity="b"*64,
            configuration=fixture.profile.configuration_for(),
            raw_response=response(draft, fixture.profile.EXTRACTOR), available_at=ASSESSED)
        quality = fixture.quality.assess_brief(draft, self.seed.source)
        automatic = prepare_assessment(row, kind="automatic", evaluator=fixture.quality.POLICY,
            reasoning_effort=None, outcome="blocked", assessment=quality,
            raw_evidence=dumps_strict(quality).encode(), available_at=ASSESSED)
        review = prepare_assessment(row, kind="sol_review", evaluator="gpt-5.6-sol",
            reasoning_effort="medium", outcome="failed", assessment={"review": {"decision":"revised"}, "package_validation":{"status":"failed"}},
            raw_evidence=response({"decision":"revised"}, fixture.profile.REVIEWER), available_at=CUTOFF)
        self.seed.pub.publish([row], [automatic, review], published_at=CUTOFF)
        before = self.successful(TOOLS[2], capture_id=self.capture, mode="as_of", as_of=ASSESSED)
        self.assertEqual(fields(before["records"][0])["review_status"], "unreviewed")
        result = self.successful(TOOLS[2], capture_id=self.capture, mode="as_of", as_of=CUTOFF)
        meta = fields(result["records"][0])
        self.assertEqual(json.loads(meta["structured_json"]), draft)
        self.assertEqual((meta["automatic_quality_status"], meta["review_status"], meta["latest_review_outcome"]),
                         ("blocked", "review_recorded", "failed"))

    def test_assessment_cursor_pins_analysis_and_cutoff(self):
        review = prepare_assessment(self.seed.row, kind="sol_review", evaluator="gpt-5.6-sol",
            reasoning_effort="medium", outcome="failed", assessment={"review": {"decision":"revised"}, "package_validation":{"status":"failed"}},
            raw_evidence=response({"decision":"revised"}, fixture.profile.REVIEWER), available_at=CUTOFF)
        self.seed.pub.publish([self.seed.row], [review], published_at=CUTOFF)
        first = self.successful(TOOLS[2], capture_id=self.capture, limit=1)
        cursor = metrics(first)["next_cursor"]
        self.assertIsNotNone(cursor)
        revised = prepare_output(source=self.seed.source, request_identity="c"*64,
            configuration=fixture.profile.configuration_for(),
            raw_response=response(self.seed.draft, fixture.profile.EXTRACTOR), available_at=CUTOFF)
        self.seed.pub.publish([revised], [], published_at=CUTOFF)
        second = self.successful(TOOLS[2], capture_id=self.capture, limit=20, cursor=cursor)
        self.assertEqual(fields(first["records"][0])["analysis_id"], fields(second["records"][0])["analysis_id"])
        self.assertEqual(metrics(first)["cutoff"], metrics(second)["cutoff"])
        self.assertEqual(fields(second["records"][1])["assessment_id"], review["assessment_id"])
        self.assertIsNone(metrics(second)["next_cursor"])

    def test_bad_inputs_reject_before_any_store_access(self):
        cases = [
            (TOOLS[0], {"ticker":"aapl"}), (TOOLS[0], {"ticker":"AAPL;SELECT"}),
            (TOOLS[0], {"fiscal_year":True}), (TOOLS[0], {"fiscal_quarter":5}),
            (TOOLS[0], {"limit":101}), (TOOLS[0], {"ticker":None}),
            (TOOLS[0], {"cursor":"invalid"}), (TOOLS[0], {"path":"/tmp/other"}),
            (TOOLS[0], {"sql":"SELECT 1"}), (TOOLS[0], {"mode":"as_of"}),
            (TOOLS[0], {"as_of":SOURCE}),
            (TOOLS[0], {"mode":"as_of","as_of":"2026-09-07"}),
            (TOOLS[0], {"mode":"as_of","as_of":"2026-09-07T00:00:00"}),
            (TOOLS[0], {"mode":"as_of","as_of":"9999-01-01T00:00:00Z"}),
            (TOOLS[1], {"capture_id":"../company.sqlite"}),
            (TOOLS[1], {"capture_id":self.capture,"ticker":"AAPL"}),
            (TOOLS[2], {"capture_id":self.capture,"limit":21}),
        ]
        with patch.object(access, "connection", side_effect=AssertionError("Store opened")):
            for tool, args in cases:
                with self.subTest(tool=tool, args=args):
                    code, result = self.invoke(tool, args)
                    self.assertEqual(code, 2, result)
                    self.assertIn("error", result)

    def test_byte_limits_fail_without_mutation(self):
        with patch.object(access, "MAX_SELECTED_BYTES", 1):
            for tool in TOOLS[1:]:
                code, response = self.invoke(tool, {"capture_id":self.capture})
                self.assertEqual(code, 2, response)
                self.assertIn("error", response)

    def test_corrupt_raw_page_fails_without_returning_partial_text(self):
        # Corruption is introduced only in this test's disposable store.
        with writer_connection(self.stores, "company") as c:
            triggers = c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='company_equibles_transcript_pages'").fetchall()
            for row in triggers:
                c.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
            c.execute("UPDATE company_equibles_transcript_pages SET raw_body=?", (b"corrupt",))
        code, response = self.invoke(TOOLS[1], {"capture_id":self.capture})
        self.assertEqual(code, 2, response)
        self.assertNotIn("result", response)

    def test_turn_page_crosses_provider_pages_without_skips(self):
        original = json.loads(self.seed.raw)
        turns = original["data"]
        self.assertGreater(len(turns), 3)
        event = {**self.seed.event, "id":"call_AAPL_2026_3", "fiscalQuarter":3}
        pages = []
        for offset, selected in ((0, turns[:2]), (2, turns[2:])):
            body = {**original, "eventId":event["id"], "fiscalQuarter":3,
                "offset":offset, "turnCount":len(selected), "data":selected,
                "hasMore":offset + len(selected) < len(turns)}
            raw = dumps_strict(body).encode()
            pages.append(RawPage(raw, SOURCE, "equibles-transcripts/blobs/" + hashlib.sha256(raw).hexdigest() + ".json"))
        EquiblesTranscriptPublisher(self.stores, self.registry).publish(
            symbol="AAPL", instrument_id="fixture-AAPL", event=event, pages=tuple(pages))
        found = self.successful(TOOLS[0], ticker="AAPL", fiscal_quarter=3)
        capture = fields(found["records"][0])["capture_id"]
        first = self.successful(TOOLS[1], capture_id=capture, limit=3)
        raw_rows = [fields(r) for r in first["records"][1:]]
        self.assertEqual([r["page_index"] for r in raw_rows], [0,0,1])
        second = self.successful(TOOLS[1], capture_id=capture, limit=100,
            cursor=first["truncation"]["next_cursor"])
        raw_rows.extend(fields(r) for r in second["records"][1:])
        self.assertEqual([json.loads(r["raw_turn_json"]) for r in raw_rows], turns)
        self.assertIsNone(second["truncation"]["next_cursor"])
