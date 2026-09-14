from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_structured_call as structured
from quant_data.company import transcript_call_brief as previous
from quant_data.company import transcript_analysis_codex as codex
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.transcript_analysis_pair_pilot import TranscriptModelPairPilot
from . import test_transcript_analysis_pipeline as fixtures
from .test_transcript_analysis_pair import PairTransport, response, events


def measure(*, amount=None, low=None, high=None, unit=None, description=None, qualifier="none"):
    return {"kind": "range" if low is not None else "point" if amount is not None else
            "qualitative" if description else "not_provided",
            "amount": amount, "low": low, "high": high, "unit": unit,
            "description": description, "qualifier": qualifier}


def example(source):
    return {"schema_version": structured.BRIEF_VERSION, "call": structured.expected_call(source),
            "headline": {"message": "Management expects resilient margins despite uncertain supplier pricing.",
                         "source_turn_ids": ["t1", "t3"]},
            "reported_results": [],
            "guidance": [{"metric": "Consolidated non-GAAP gross margin", "period": "Fiscal 2027",
                          "previous": None, "current": measure(low="47", high="48", unit="%"),
                          "change": "unspecified", "condition": None, "source_turn_ids": ["t1"]}],
            "business_drivers": [], "analyst_focus": [], "watch_items": [], "management_tone": None}


def review(value, decision="approved"):
    return {"schema_version": structured.REVIEW_VERSION, "decision": decision, "brief": deepcopy(value),
            "editorial_notes": [], "unresolved_issues": []}


class StructuredContractTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((fixtures.ROOT / "docs/rebuild/TRANSCRIPT_ANALYSIS_V3.example.json").read_text())["input"]
        self.source.update(symbol="FIX", fiscal_year=2026, fiscal_quarter=1, capture_id="synthetic")
        self.value = example(self.source)

    def test_typed_values_and_fixed_tables_render_within_short_source_budget(self):
        result = structured.validate_brief(self.value, self.source)
        self.assertLessEqual(result["word_count"], result["source_words"])
        rendered = structured.render_brief(self.value)
        self.assertIn("| Metric / period | Previous | Current |", rendered)
        self.assertIn("47–48 %", rendered)
        self.assertNotIn("## Watch items", rendered)
        self.assertEqual(structured.format_value(None), "Not stated")
        self.assertEqual(structured.format_value(measure(amount="250", unit="million dollars",
                                                        qualifier="approximately")), "≈ 250 million dollars")

    def test_invalid_numbers_shapes_and_qualifiers_reject(self):
        cases = [
            measure(low="48", high="47", unit="%"),
            measure(amount="1e3", unit="%"),
            measure(amount="NaN", unit="%"),
            measure(amount="01", unit="%"),
            measure(amount="1", unit=None),
            measure(low="1", high="2", unit="%", qualifier="at_least"),
            measure(description="Growing", qualifier="approximately"),
            {**measure(amount="1", unit="%"), "high": "2"},
        ]
        for current in cases:
            value = deepcopy(self.value); value["guidance"][0]["current"] = current
            with self.subTest(current=current), self.assertRaises(ValidationError):
                structured.validate_brief(value, self.source)

    def test_guidance_change_and_comparable_units_cannot_contradict_values(self):
        for change, old, new in (
            ("raised", measure(amount="10", unit="%"), measure(amount="9", unit="%")),
            ("lowered", measure(low="10", high="20", unit="%"), measure(low="9", high="21", unit="%")),
            ("reiterated", measure(amount="10", unit="%"), measure(amount="11", unit="%")),
            ("raised", measure(amount="10", unit="million"), measure(amount="11", unit="billion")),
            ("withdrawn", None, measure(amount="10", unit="%")),
        ):
            value = deepcopy(self.value)
            value["guidance"][0].update(change=change, previous=old, current=new)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                structured.validate_brief(value, self.source)

    def test_unknown_and_withdrawn_guidance_never_becomes_zero(self):
        value = deepcopy(self.value)
        value["guidance"][0].update(period=None, current=measure(), change="withdrawn")
        structured.validate_brief(value, self.source)
        self.assertIsNone(value["guidance"][0]["current"]["amount"])
        self.assertIn("Not provided", structured.render_brief(value))

    def test_identity_role_duplicates_and_unknown_references_reject(self):
        for mutate in (
            lambda v: v["call"].update(symbol="OTHER"),
            lambda v: v["guidance"][0].update(source_turn_ids=["t2"]),
            lambda v: v["headline"].update(source_turn_ids=["missing"]),
            lambda v: v["headline"].update(source_turn_ids=["t1", "t1"]),
            lambda v: v["guidance"].append(deepcopy(v["guidance"][0])),
        ):
            value = deepcopy(self.value); mutate(value)
            with self.subTest(value=value), self.assertRaises(ValidationError):
                structured.validate_brief(value, self.source)

    def test_qa_links_require_real_substantive_exchanges(self):
        value = deepcopy(self.value); value["guidance"] = []
        value["analyst_focus"] = [{"topic": "Costs", "question": "Can further costs be mitigated?",
                                  "management_answer": "Mitigation remains unquantified.",
                                  "answer_status": "partial", "question_turn_ids": ["t4"],
                                  "answer_turn_ids": ["t5"]}]
        structured.validate_brief(value, self.source)
        for ids in (["t3"], [], ["missing"]):
            bad = deepcopy(value); bad["analyst_focus"][0]["answer_turn_ids"] = ids
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                structured.validate_brief(bad, self.source)
        source = deepcopy(self.source); source["turns"][3]["text"] = "Great, thanks."
        with self.assertRaisesRegex(ValidationError, "substantive"):
            structured.validate_brief(value, source)

    def test_word_and_row_limits_reject_without_truncation(self):
        value = deepcopy(self.value); value["headline"]["message"] = "word " * 26
        before = deepcopy(value)
        with self.assertRaises(ValidationError):
            structured.validate_brief(value, self.source)
        self.assertEqual(value, before)
        value = deepcopy(self.value); value["guidance"] *= 7
        with self.assertRaises(ValidationError):
            structured.validate_brief(value, self.source)
        value = deepcopy(self.value)
        value["business_drivers"] = [{"business": "Business " + str(i), "direction": "improving",
                                      "driver": " ".join([str(i)] + ["growth"] * 29),
                                      "source_turn_ids": ["t1"]} for i in range(4)]
        with self.assertRaisesRegex(ValidationError, "maximum"):
            structured.validate_brief(value, self.source)

    def test_editor_must_return_truthful_decision_and_valid_final(self):
        structured.validate_editorial_review(review(self.value), self.value, self.source)
        changed = deepcopy(self.value); changed["headline"]["message"] = "Margins face uncertain component costs."
        structured.validate_editorial_review(review(changed, "revised"), self.value, self.source)
        for output in (review(changed), review(self.value, "revised"), review(self.value, "needs_attention")):
            with self.assertRaises(ValidationError):
                structured.validate_editorial_review(output, self.value, self.source)

    def test_request_keeps_full_source_and_pins_new_contract_without_changing_old(self):
        raw = structured.make_request(self.source, analysis=self.value)
        request = json.loads(raw)
        supplied = json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(supplied["source"], self.source)
        self.assertEqual(supplied["trusted_call"], structured.expected_call(self.source))
        self.assertEqual(supplied["draft_brief"], self.value)
        self.assertEqual(request["model"], structured.REVIEWER)
        self.assertEqual(request["reasoning"], {"effort": "xhigh"})
        self.assertEqual(request["truncation"], "disabled")
        self.assertNotEqual(structured.VERSION, previous.VERSION)
        self.assertEqual(request["text"]["format"]["name"], structured.FORMAT_NAMES[1])
        stored = json.loads((fixtures.ROOT / "docs/rebuild/TRANSCRIPT_STRUCTURED_CALL_REVIEW_V1.schema.json").read_text())
        self.assertEqual(stored, structured.review_schema())


class StructuredPilotTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def pilot(self, name="structured", clock=lambda: fixtures.AT):
        return TranscriptModelPairPilot(self.stores, self.registry, Path(self.temp.name) / name, clock=clock)

    def test_default_two_call_structured_pair_private_artifacts_and_zero_call_replay(self):
        before = mutation_fingerprint(self.stores)
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture)
        self.assertEqual(plan["configuration"]["prompt_version"], structured.VERSION)
        value = example(self.source)
        transport = PairTransport([response(value, structured.EXTRACTOR),
                                   response(review(value), structured.REVIEWER)])
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("No canonical publication")):
            result = job.execute(plan["plan_id"], transport)
        self.assertEqual(result["outcome"], "validated_private")
        self.assertEqual(result["requests_this_run"], 2)
        self.assertEqual(Path(result["brief_artifact"]).read_text(), structured.render_brief(value))
        self.assertEqual(json.loads(Path(result["brief_audit"]).read_text())["profile"], structured.VERSION)
        again = PairTransport([])
        self.assertEqual(job.execute(plan["plan_id"], again)["requests_this_run"], 0)
        self.assertEqual((again.calls, again.preflights), ([], 0))
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_editor_can_fix_shape_valid_numeric_error_but_cannot_approve_it(self):
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture)
        value = example(self.source); bad = deepcopy(value)
        bad["guidance"][0]["current"]["low"] = "49"
        result = job.execute(plan["plan_id"], PairTransport([response(bad, structured.EXTRACTOR),
                             response(review(value, "revised"), structured.REVIEWER)]))
        self.assertEqual(result["extraction"]["validation"], "failed")
        self.assertEqual(result["outcome"], "validated_private")

    def test_failed_or_uncertain_review_has_no_new_plan_retry(self):
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture)
        with self.assertRaises(ResourceLimitError):
            job.execute(plan["plan_id"], PairTransport([response(example(self.source), structured.EXTRACTOR),
                                                        ResourceLimitError("fixture timeout")]))
        later = self.pilot(clock=lambda: fixtures.LATER)
        new = later.prepare(capture_id=self.capture)
        retry = PairTransport([])
        with self.assertRaises(ConflictError):
            later.execute(new["plan_id"], retry)
        self.assertEqual((retry.preflights, retry.calls), (0, []))

    def test_native_metadata_warning_and_invalid_final_never_pass(self):
        for case in ("metadata", "invalid", "output-cap", "unresolved"):
            job = self.pilot(case)
            plan = job.prepare(capture_id=self.capture)
            value = example(self.source); reviewed = review(value)
            if case == "invalid":
                reviewed["brief"]["call"]["symbol"] = "OTHER"
            if case == "unresolved":
                reviewed.update(decision="needs_attention", unresolved_issues=[
                    {"section":"guidance", "description":"Unclear source scope.", "turn_ids":["t1"]}])
            raw = json.loads(response(reviewed, structured.REVIEWER))
            if case == "metadata":
                raw["transport_evidence"] = {"model_metadata_status":"fallback", "effective_reasoning_effort_verified":False}
            if case == "output-cap":
                raw["usage"].update(output_tokens=32769,total_tokens=32869)
            result = job.execute(plan["plan_id"], PairTransport([response(value, structured.EXTRACTOR),
                                                                dumps_strict(raw).encode()]))
            self.assertEqual(result["outcome"], "needs_changes")
            self.assertEqual(result["requests_this_run"], 2)

    def test_native_transport_accepts_fixed_structured_roles_and_rejects_swaps(self):
        value = example(self.source)
        for is_review in (False, True):
            raw = structured.make_request(self.source, analysis=value if is_review else None)
            native = codex.CodexTranscriptTransport(evidence_root=Path(self.temp.name)/str(is_review), timeout_seconds=1200)
            native._ready = True
            with patch.object(native, "_run", return_value=(events(review(value) if is_review else value), b"")) as run:
                self.assertEqual(json.loads(native.request(raw))["status"], "completed")
            self.assertIn('model_reasoning_effort="xhigh"',run.call_args.args[0])
            bad=json.loads(raw); bad["model"]=structured.EXTRACTOR if is_review else structured.REVIEWER
            with self.assertRaises(ValidationError):
                native.request(dumps_strict(bad).encode())
