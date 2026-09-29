"""Evidence-summary behavior and exact predecessor compatibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_analysis_model as model
from quant_data.company.transcript_analysis_contract import (
    ANALYSIS_V2, REVIEW_V2, analysis_schema, review_schema, validate_analysis, validate_review,
)
from quant_data.company.transcript_analysis_request_v3 import GUIDANCE_CHECKLIST
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations import transcript_analysis as operation
import tests.company.test_transcript_analysis_pipeline as fixtures

ROOT = fixtures.ROOT

def example():
    return json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V2.example.json").read_text())

def review():
    return {"schema_version":REVIEW_V2,"verdict":"needs_changes","summary":"A qualification is missing.",
        "findings":[{"severity":"error","category":"guidance","output_pointer":"/guidance_claims/0",
            "description":"Supplier uncertainty should be reflected.","evidence":[{"turn_id":"t3",
            "evidence_summary":"Management notes uncertainty about supplier pricing."}],
            "proposed_correction":"Include that qualification."}]}

class SummaryEvidenceTests(unittest.TestCase):
    def test_paraphrases_and_repeated_words_are_summaries_without_offsets(self):
        fixture=example(); output=fixture["model_output"]; source=fixture["input"]["turns"]
        first=output["guidance_claims"][0]["evidence"][0]
        self.assertNotIn(first["evidence_summary"],source[0]["text"])
        source[0]["text"]+=" gross margin gross margin"
        checked=validate_analysis(output,source)
        self.assertEqual(checked["model_output"],output)
        self.assertTrue(checked["evidence"])
        for item in checked["evidence"]:
            self.assertEqual(item["evidence_kind"],"summary")
            self.assertIsNone(item["char_start"])
            self.assertIsNone(item["char_end"])
            self.assertNotIn("quote",item)
            original=next(t for t in source if t["turn_id"]==item["turn_id"])
            self.assertEqual(item["source_span"],{"kind":"turn","char_start":0,"char_end":len(original["text"])})
        first["evidence_summary"]="gross margin"
        validate_analysis(output,source)

    def test_summary_does_not_relax_source_roles_sections_or_exchange_membership(self):
        mutations=[
            (lambda x:x["guidance_claims"][0]["evidence"][0].update(turn_id="missing")),
            (lambda x:x["guidance_claims"][0]["evidence"][0].update(turn_id="t2")),
            (lambda x:x["management_tone"]["qa"]["supporting_evidence"][0].update(turn_id="t1")),
            (lambda x:x["analyst_focus"]["topics"][0]["answer_evidence"][0].update(turn_id="t1")),
            (lambda x:x["management_tone"]["by_speaker"][0].update(speaker_id="a1")),
        ]
        for mutate in mutations:
            fixture=example(); mutate(fixture["model_output"])
            with self.subTest(mutate=mutate),self.assertRaises(ValidationError):
                validate_analysis(fixture["model_output"],fixture["input"]["turns"])

    def test_summary_must_be_nonempty_and_old_quote_field_is_not_accepted(self):
        for value in ["", "   ", None, 3]:
            fixture=example();fixture["model_output"]["guidance_claims"][0]["evidence"][0]["evidence_summary"]=value
            with self.subTest(value=value),self.assertRaises(ValidationError):
                validate_analysis(fixture["model_output"],fixture["input"]["turns"])
        fixture=example();e=fixture["model_output"]["guidance_claims"][0]["evidence"][0]
        e["quote"]=e.pop("evidence_summary")
        with self.assertRaises(ValidationError):validate_analysis(fixture["model_output"],fixture["input"]["turns"])

    def test_numbers_periods_and_explicit_refusals_remain_checked(self):
        for update in [{"low":"49"},{"point":"1"},{"scale":"0"},{"scale":"NaN"},{"currency":"usd"}]:
            fixture=example();g=fixture["model_output"]["guidance_claims"][0];g["value"].update(update)
            with self.subTest(update=update),self.assertRaises(ValidationError):
                validate_analysis(fixture["model_output"],fixture["input"]["turns"])
        fixture=example();g=fixture["model_output"]["guidance_claims"][0]
        g["target"]["period_kind"]="multi_year"
        with self.assertRaises(ValidationError):validate_analysis(fixture["model_output"],fixture["input"]["turns"])
        fixture=example();g=fixture["model_output"]["guidance_claims"][0];g["management_action"]="not_provided"
        with self.assertRaises(ValidationError):validate_analysis(fixture["model_output"],fixture["input"]["turns"])
        g["value"].update(shape="none",low=None,high=None,point=None,scale=None)
        validate_analysis(fixture["model_output"],fixture["input"]["turns"])

    def test_review_accepts_paraphrases_but_requires_references_and_consistent_verdict(self):
        fixture=example();r=review()
        checked=validate_review(r,fixture["model_output"],fixture["input"]["turns"])
        self.assertEqual(checked["evidence"][0]["evidence_kind"],"summary")
        self.assertIsNone(checked["evidence"][0]["char_start"])
        for mutate in [
            lambda v:v["findings"][0]["evidence"][0].update(turn_id="missing"),
            lambda v:v["findings"][0]["evidence"][0].update(turn_id="t2"),
            lambda v:v["findings"][0].update(output_pointer="/guidance_claims/999"),
            lambda v:v.update(verdict="accepted"),
            lambda v:v.update(schema_version="unknown"),
        ]:
            r=review();mutate(r)
            with self.subTest(mutate=mutate),self.assertRaises(ValidationError):
                validate_review(r,fixture["model_output"],fixture["input"]["turns"])

    def test_unknown_analysis_version_is_rejected(self):
        fixture=example();fixture["model_output"]["schema_version"]="unknown"
        with self.assertRaises(ValidationError):validate_analysis(fixture["model_output"],fixture["input"]["turns"])

    def test_summary_v3_requests_use_strict_summary_schemas_and_the_same_checklist(self):
        source={"capture_id":"fixture","input_sha256":"1"*64}
        for reviewing in [False,True]:
            request=json.loads(model.make_request(source,analysis={"fixture":True} if reviewing else None,prompt_version=model.SUMMARY_PROMPT_VERSION))
            self.assertTrue(request["text"]["format"]["strict"])
            schema=request["text"]["format"]["schema"]
            self.assertEqual(schema["properties"]["schema_version"]["enum"],
                             [REVIEW_V2 if reviewing else ANALYSIS_V2])
            self.assertIn(GUIDANCE_CHECKLIST,request["instructions"])
            self.assertIn("evidence_summary",request["instructions"])
            self.assertNotIn("Cite exact source quotes",request["instructions"])
            self.assertNotIn("Quotes must occur exactly once",request["instructions"])
            self.assertNotIn("Include exact contiguous quotes",request["instructions"])
        self.assertIn(GUIDANCE_CHECKLIST,(ROOT/"docs/rebuild/TRANSCRIPT_GUIDANCE_CHECKLIST_V1.md").read_text())
        self.assertEqual(review_schema(REVIEW_V2),json.loads((ROOT/"docs/rebuild/TRANSCRIPT_REVIEW_V2.schema.json").read_text()))

    def test_all_pre_summary_requests_configs_outputs_and_resources_are_preserved(self):
        baseline=json.loads((Path(__file__).parent/"fixtures/transcript_request_pre_summary.json").read_text())
        for item in baseline["vectors"]:
            kwargs={"prompt_version":item["version"],"backend":item["backend"]}
            with self.subTest(version=item["version"],review=item["review"],backend=item["backend"]):
                self.assertEqual(model.configuration_for(review=item["review"],**kwargs),item["configuration"])
                self.assertEqual(model.request_identity_for(baseline["source"],
                    review_analysis_id="fixture-parent" if item["review"] else None,**kwargs),item["request_identity"])
                request=model.make_request(baseline["source"],analysis={"fixture":True} if item["review"] else None,
                                           prompt_version=item["version"])
                self.assertEqual(hashlib.sha256(request).hexdigest(),item["request_sha256"])
        legacy=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())
        self.assertEqual(validate_analysis(legacy["model_output"],legacy["input"]["turns"]),baseline["validation"])
        for name,digest in baseline["frozen_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest,name)
        legacy["model_output"]["guidance_claims"][0]["evidence"][0]["quote"]="a paraphrase"
        with self.assertRaises(ValidationError):validate_analysis(legacy["model_output"],legacy["input"]["turns"])


class SummaryPublicationTests(unittest.TestCase):
    setUp=fixtures.TranscriptPipelineTests.setUp
    tearDown=fixtures.TranscriptPipelineTests.tearDown
    publish=fixtures.TranscriptPipelineTests.publish

    def test_paraphrase_publishes_with_source_lineage_and_replays_without_writes(self):
        self.output["guidance_claims"][0]["evidence"][0]["evidence_summary"]="Expected adjusted margin is between 47 and 48 percent in fiscal 2027."
        receipt,row=self.publish()
        result=self.repository.get(row["analysis_id"],as_of=fixtures.AT)
        first=result["evidence"][0]
        self.assertEqual(first["evidence_kind"],"summary")
        self.assertEqual(first["capture_id"],self.capture)
        self.assertEqual(first["json_pointer"],"/data/0/text")
        self.assertEqual(first["page_sha256"],self.source["source_page_hashes"][0])
        self.assertIsNone(first["char_start"])
        self.assertEqual(first["source_span"],{"kind":"turn","char_start":0,"char_end":len(self.source["turns"][0]["text"])})
        self.assertEqual(json.loads(row["raw_response"]),json.loads(fixtures.response(self.output)))
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish()[0].outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_request_pinning_rejects_output_version_downgrade_without_writes(self):
        legacy=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())["model_output"]
        legacy["management_tone"]["by_speaker"][0]["speaker_id"]=self.source["turns"][0]["speaker_id"]
        before=mutation_fingerprint(self.stores)
        with self.assertRaisesRegex(ValidationError,"pinned request"):self.publish(legacy)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_review_summary_publishes_through_two_call_pipeline(self):
        self.review=review()
        self.review["schema_version"]="transcript.review.v3"
        self.review["findings"][0].update(finding_type="missing_qualification",related_claim_local_ids=["g1"],
                                        missing_information="Supplier pricing uncertainty.")
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="3")
        transport=fixtures.Transport([fixtures.response(self.output),fixtures.response(self.review,review=True)])
        report=self.job.execute(plan["plan_id"],transport)
        self.assertEqual(report["requests_this_run"],2)
        row=self.repository.find_request(model.request_identity_for(self.source))
        stored=self.repository.get(row["analysis_id"],as_of=fixtures.AT)
        self.assertEqual(stored["reviews"][0]["verdict"],"needs_changes")
        self.assertEqual(stored["reviews"][0]["evidence"][0]["evidence_kind"],"summary")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.job.execute(plan["plan_id"],fixtures.Transport([]))["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_previous_v2_plan_still_publishes_v1_quotes(self):
        old=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())["model_output"]
        old["management_tone"]["by_speaker"][0]["speaker_id"]=self.source["turns"][0]["speaker_id"]
        previous=operation.configuration_for
        def config(**kwargs):
            kwargs.setdefault("prompt_version",model.PREVIOUS_PROMPT_VERSION)
            return previous(**kwargs)
        with patch.object(operation,"configuration_for",side_effect=config):
            plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        self.job.execute(plan["plan_id"],fixtures.Transport([fixtures.response(old)]))
        row=self.repository.find_request(model.request_identity_for(self.source,prompt_version=model.PREVIOUS_PROMPT_VERSION))
        evidence=json.loads(row["evidence_json"])[0]
        self.assertIn("quote",evidence)
        self.assertIsInstance(evidence["char_start"],int)

if __name__=="__main__":
    unittest.main()
