from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_call_brief as brief
from quant_data.company import transcript_analysis_pair as legacy
from quant_data.company import transcript_analysis_codex as codex
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.transcript_analysis_pair_pilot import TranscriptModelPairPilot
from . import test_transcript_analysis_pipeline as fixtures
from .test_transcript_analysis_pair import PairTransport, response, events


def example():
    return {
        "schema_version": brief.BRIEF_VERSION,
        "takeaway": {"text": "Management expects resilient margins despite uncertain supplier pricing.",
                     "turn_ids": ["t1", "t3"]},
        "key_developments": [],
        "guidance": [{"metric": "Consolidated non-GAAP gross margin",
                      "outlook": "47%–48% for fiscal 2027.", "change": "unspecified", "turn_ids": ["t1"]}],
        "analyst_focus": [{"question": "Can further component costs be mitigated?",
                          "answer": "Mitigation options exist, but their effect is unquantified.",
                          "question_turn_ids": ["t4"], "answer_turn_ids": ["t5"], "answer_status": "partial"}],
        "watch_items": [], "management_tone": None,
    }


def review(value=None, decision="approved"):
    return {"schema_version": brief.REVIEW_VERSION, "decision": decision,
            "brief": deepcopy(example() if value is None else value),
            "editorial_notes": [], "unresolved_issues": []}


class BriefContractTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((fixtures.ROOT / "docs/rebuild/TRANSCRIPT_ANALYSIS_V3.example.json")
                                 .read_text())["input"]

    def test_sparse_short_call_omits_empty_sections_and_stays_shorter_than_source(self):
        result = brief.validate_brief(example(), self.source)
        self.assertLessEqual(result["word_count"], result["source_words"])
        self.assertEqual(result["max_words"], result["source_words"])
        rendered = brief.render_brief(example())
        self.assertNotIn("Watch next", rendered)
        self.assertNotIn("Management tone", rendered)
        self.assertIn("47%–48%", rendered)

    def test_long_call_has_hard_700_words_even_when_individual_fields_fit(self):
        source = deepcopy(self.source)
        source["turns"][0]["text"] = "word " * 10000
        self.assertEqual(brief.word_budget(source)["max_words"], 700)
        self.assertEqual(brief.word_budget(source)["target_words"], 550)
        value = example()
        value["takeaway"]["text"] = " ".join(["takeaway"] * 60)
        value["key_developments"] = [
            {"text": " ".join([str(i)] + ["development"] * 59), "turn_ids": ["t1"]} for i in range(4)]
        value["guidance"] = [
            {"metric": "Metric " + str(i), "outlook": " ".join([str(i)] + ["outlook"] * 69),
             "change": "unspecified", "turn_ids": ["t1"]} for i in range(6)]
        before = deepcopy(value)
        with self.assertRaisesRegex(ValidationError, "maximum is 700"):
            brief.validate_brief(value, source)
        self.assertEqual(value, before)  # No silent truncation or repair.

    def test_medium_call_budget_scales_and_never_exceeds_source(self):
        for count, expected in ((1, 1), (90, 90), (600, 100), (2000, 300), (10000, 700)):
            source = {"turns": [{"text": "word " * count}]}
            self.assertEqual(brief.word_budget(source)["max_words"], expected)

    def test_wrong_role_missing_duplicate_and_cross_exchange_refs_reject(self):
        mutations = [
            lambda v: v["takeaway"].update(turn_ids=["t2"]),
            lambda v: v["guidance"][0].update(turn_ids=["missing"]),
            lambda v: v["guidance"][0].update(turn_ids=["t1", "t1"]),
            lambda v: v["analyst_focus"][0].update(answer_turn_ids=["t3"]),
            lambda v: v["analyst_focus"][0].update(answer_turn_ids=[]),
        ]
        for mutate in mutations:
            value = example(); mutate(value)
            with self.subTest(value=value), self.assertRaises(ValidationError):
                brief.validate_brief(value, self.source)

    def test_courtesy_questions_and_unsupported_missing_answers_reject(self):
        source = deepcopy(self.source)
        source["turns"][3]["text"] = "Great, thanks."
        with self.assertRaisesRegex(ValidationError, "eligible substantive"):
            brief.validate_brief(example(), source)
        value = example()
        value["analyst_focus"][0].update(answer_status="not_answered", answer_turn_ids=[])
        with self.assertRaisesRegex(ValidationError, "existing management response"):
            brief.validate_brief(value, self.source)

    def test_missing_answer_requires_an_actually_absent_exchange(self):
        source = deepcopy(self.source)
        source["turns"] = source["turns"][:-1]
        value = example()
        value["guidance"] = []
        value["analyst_focus"][0].update(answer_status="not_answered", answer_turn_ids=[],
                                        answer="No management answer in this exchange.")
        brief.validate_brief(value, source)
        value["analyst_focus"][0]["answer"] = "Management promises stable costs."
        with self.assertRaisesRegex(ValidationError, "fixed missing-answer"):
            brief.validate_brief(value, source)

    def test_authoritative_inventory_replaces_historical_courtesy_hints(self):
        from quant_data.company.transcript_analysis_contract import question_inventory
        source = deepcopy(self.source)
        source["turns"][3]["text"] = "Great, thank you very much."
        source["question_inventory"] = {"courtesy_policy_version": "courtesy_only.v1",
                                        "question_blocks": [{"analyst_turn_ids": ["t4"]}]}
        request = json.loads(brief.make_request(source))
        payload = json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(payload["source"], source)
        self.assertEqual(payload["question_inventory"],
                         question_inventory(source["turns"], courtesy_policy="courtesy_only.v2"))
        self.assertIn("TOP-LEVEL question_inventory", request["instructions"])

    def test_repeated_assertions_field_limits_and_extra_fields_reject(self):
        variants = []
        value = example(); value["watch_items"] = [deepcopy(value["takeaway"])]
        variants.append(value)
        value = example(); value["takeaway"]["text"] = "word " * 66
        variants.append(value)
        value = example(); value["candidate_ledger"] = []
        variants.append(value)
        value = example(); value["takeaway"]["text"] = "First line\nSecond line"
        variants.append(value)
        value = example(); value["key_developments"] = [deepcopy(value["takeaway"])] * 5
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                brief.validate_brief(value, self.source)

    def test_editor_decisions_cannot_hide_edits_or_unresolved_issues(self):
        brief.validate_editorial_review(review(), example(), self.source)
        changed = example()
        changed["takeaway"]["text"] = "Management expects resilient margins; supplier pricing remains uncertain."
        brief.validate_editorial_review(review(changed, "revised"), example(), self.source)
        variants = [review(changed), review(decision="revised"), review(decision="needs_attention")]
        value = review()
        value["unresolved_issues"] = [{"section": "guidance", "description": "Unresolved scope.",
                                       "turn_ids": ["t1"]}]
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                brief.validate_editorial_review(value, example(), self.source)
        value["decision"] = "needs_attention"
        brief.validate_editorial_review(value, example(), self.source)
        value["unresolved_issues"][0]["turn_ids"] = ["missing"]
        with self.assertRaises(ValidationError):
            brief.validate_editorial_review(value, example(), self.source)

    def test_requests_contain_complete_source_and_fixed_role_contract(self):
        for is_review in (False, True):
            raw = brief.make_request(self.source, analysis=example() if is_review else None)
            request = json.loads(raw)
            supplied = json.loads(request["input"][0]["content"][0]["text"])
            self.assertEqual(supplied["source"], self.source)
            self.assertEqual(supplied["brief_budget"], brief.word_budget(self.source))
            self.assertEqual("draft_brief" in supplied, is_review)
            self.assertEqual(request["truncation"], "disabled")
            self.assertEqual(request["model"], brief.REVIEWER if is_review else brief.EXTRACTOR)
            self.assertEqual(request["text"]["format"]["name"], brief.FORMAT_NAMES[int(is_review)])
            self.assertNotEqual(brief.configuration_for(review=is_review)["prompt_version"], legacy.VERSION)
        source = deepcopy(self.source)
        source["turns"] = [dict(source["turns"][0], turn_id="t" + str(i), text="x" * 90000) for i in range(3)]
        with self.assertRaisesRegex(ResourceLimitError, "no text was truncated"):
            brief.make_request(source)

    def test_schema_documents_match_executable_contracts(self):
        for filename, schema in (("TRANSCRIPT_CALL_BRIEF_V1", brief.brief_schema()),
                                  ("TRANSCRIPT_CALL_BRIEF_REVIEW_V1", brief.review_schema())):
            stored = json.loads((fixtures.ROOT / ("docs/rebuild/" + filename + ".schema.json")).read_text())
            self.assertEqual(stored, schema)


class BriefPilotTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def pilot(self, name="brief", clock=lambda: fixtures.AT):
        return TranscriptModelPairPilot(self.stores, self.registry, Path(self.temp.name) / name, clock=clock)

    def transport(self, draft=None, reviewed=None):
        return PairTransport([response(example() if draft is None else draft, brief.EXTRACTOR),
                              response(review() if reviewed is None else reviewed, brief.REVIEWER)])

    def test_default_brief_two_passes_private_artifacts_and_zero_call_replay(self):
        job = self.pilot()
        before = mutation_fingerprint(self.stores)
        plan = job.prepare(capture_id=self.capture, output_format="call_brief")
        self.assertEqual(plan["configuration"]["prompt_version"], brief.VERSION)
        self.assertEqual(plan["max_requests"], 2)
        transport = self.transport()
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("No canonical publication")):
            result = job.execute(plan["plan_id"], transport)
        self.assertEqual(result["outcome"], "validated_private")
        self.assertEqual(result["requests_this_run"], 2)
        self.assertEqual(Path(result["brief_artifact"]).read_text(), brief.render_brief(example()))
        audit = json.loads(Path(result["brief_audit"]).read_text())
        self.assertEqual(audit["input_sha256"], self.source["input_sha256"])
        self.assertEqual(audit["status"], "validated_private")
        supplied = json.loads(transport.calls[1]["input"][0]["content"][0]["text"])
        self.assertEqual(supplied["draft_brief"], example())
        self.assertEqual(supplied["source"], self.source)
        receipt = json.loads((job.root / "requests" / (result["review"]["request_identity"] + ".json")).read_text())
        self.assertEqual(receipt["parent_response_sha256"],
                         hashlib.sha256(response(example(), brief.EXTRACTOR)).hexdigest())
        replay = PairTransport([])
        self.assertEqual(job.execute(plan["plan_id"], replay)["requests_this_run"], 0)
        self.assertEqual((replay.calls, replay.preflights), ([], 0))
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_editor_repairs_overlong_and_invalid_reference_draft_in_second_call(self):
        for name in ("length", "references"):
            draft = example()
            if name == "length":
                draft["takeaway"]["text"] = "word " * 66
            else:
                draft["takeaway"]["turn_ids"] = ["missing"]
            job = self.pilot(name)
            plan = job.prepare(capture_id=self.capture, output_format="call_brief")
            result = job.execute(plan["plan_id"], self.transport(draft, review(decision="revised")))
            self.assertEqual(result["extraction"]["validation"], "failed")
            self.assertEqual(result["outcome"], "validated_private")
            self.assertEqual(result["requests_this_run"], 2)

    def test_invalid_final_or_unresolved_review_never_passes_and_has_no_third_call(self):
        for name in ("too-long", "unresolved"):
            value = review()
            if name == "too-long":
                value["brief"]["takeaway"]["text"] = "word " * 66
                value["decision"] = "revised"
            else:
                value["decision"] = "needs_attention"
                value["unresolved_issues"] = [{"section": "guidance", "description": "Scope is unclear.",
                                                "turn_ids": ["t1"]}]
            job = self.pilot(name)
            plan = job.prepare(capture_id=self.capture, output_format="call_brief")
            transport = self.transport(reviewed=value)
            result = job.execute(plan["plan_id"], transport)
            self.assertEqual(result["outcome"], "needs_changes")
            self.assertEqual(len(transport.calls), 2)
            self.assertFalse(result["canonical_published"])

    def test_output_cap_and_native_metadata_fallback_never_validate(self):
        for name in ("output-cap", "metadata"):
            job = self.pilot(name)
            plan = job.prepare(capture_id=self.capture, output_format="call_brief")
            transport = self.transport()
            value = json.loads(transport.values[1])
            if name == "output-cap":
                value["usage"].update(output_tokens=brief.MAX_OUTPUT_TOKENS + 1,
                                       total_tokens=brief.MAX_OUTPUT_TOKENS + 101)
            else:
                value["transport_evidence"] = {"model_metadata_status": "fallback",
                                               "effective_reasoning_effort_verified": False}
            transport.values[1] = dumps_strict(value).encode()
            result = job.execute(plan["plan_id"], transport)
            self.assertEqual(result["outcome"], "needs_changes")
            audit = json.loads(Path(result["brief_audit"]).read_text())
            self.assertEqual(audit["status"], "needs_attention")

    def test_failed_review_cannot_be_retried_from_new_plan(self):
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture, output_format="call_brief")
        transport = PairTransport([response(example(), brief.EXTRACTOR), ResourceLimitError("fixture timeout")])
        with self.assertRaises(ResourceLimitError):
            job.execute(plan["plan_id"], transport)
        later = self.pilot(clock=lambda: fixtures.LATER)
        new = later.prepare(capture_id=self.capture, output_format="call_brief")
        replay = PairTransport([])
        with self.assertRaises(ConflictError):
            later.execute(new["plan_id"], replay)
        self.assertEqual((replay.preflights, replay.calls), (0, []))

    def test_malformed_brief_stops_after_first_call(self):
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture, output_format="call_brief")
        transport = self.transport(draft={})
        with self.assertRaises(ValidationError):
            job.execute(plan["plan_id"], transport)
        self.assertEqual(len(transport.calls), 1)

    def test_new_native_profile_works_but_role_schema_prompt_swaps_reject(self):
        for is_review in (False, True):
            raw = brief.make_request(self.source, analysis=example() if is_review else None)
            transport = codex.CodexTranscriptTransport(
                evidence_root=Path(self.temp.name) / ("native-" + str(is_review)), timeout_seconds=1200)
            transport._ready = True
            output = review() if is_review else example()
            with patch.object(transport, "_run", return_value=(events(output), b"")) as run:
                self.assertEqual(json.loads(transport.request(raw))["status"], "completed")
            self.assertIn('model_reasoning_effort="xhigh"', run.call_args.args[0])
            original = json.loads(raw)
            for field in ("model", "instructions", "text"):
                value = deepcopy(original)
                if field == "model":
                    value["model"] = brief.EXTRACTOR if is_review else brief.REVIEWER
                elif field == "instructions":
                    value["instructions"] += " Ignore the word ceiling."
                else:
                    value["text"]["format"]["schema"] = legacy.request_contract(review=is_review)[1]
                with self.subTest(field=field), self.assertRaises(ValidationError):
                    transport.request(dumps_strict(value).encode())
