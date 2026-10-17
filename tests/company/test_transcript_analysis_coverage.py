"""Structured coverage references, reconciliation and precise review findings."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.company import transcript_analysis_model as model
from quant_data.company.transcript_analysis_contract import validate_analysis, validate_review
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
import tests.company.test_transcript_analysis_pipeline as fixtures

ROOT=fixtures.ROOT

def example():
    return json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V3.example.json").read_text())

def review():
    return {"schema_version":"transcript.review.v3","verdict":"needs_changes","summary":"An assumption needs detail.",
        "findings":[{"severity":"error","category":"guidance","output_pointer":"/guidance_claims/0",
            "description":"Clarify the supplier-pricing assumption.","evidence":[{"turn_id":"t3",
            "evidence_summary":"Supplier pricing remains uncertain."}],"proposed_correction":"Retain the assumption.",
            "finding_type":"missing_qualification","related_claim_local_ids":["g1"],
            "missing_information":"The supplier-pricing qualification."}]}

class CoverageTests(unittest.TestCase):
    def check(self,value):
        return validate_analysis(value["model_output"],value["input"]["turns"])

    def test_multiple_mentions_merge_with_source_evidence_and_qa_links(self):
        value=example();result=self.check(value)
        self.assertEqual(result["derived"]["guidance_coverage"],
            {"reviewed_management_turn_count":3,"candidate_count":3,"excluded_candidate_count":0,
             "linked_claim_count":1,"qa_candidate_count":2,"linked_topic_candidate_count":2})
        self.assertEqual(len(result["model_output"]["guidance_claims"]),1)
        self.assertEqual({e["turn_id"] for e in result["model_output"]["guidance_claims"][0]["evidence"]},{"t1","t3","t5"})

    def test_management_turn_accounting_must_be_complete_and_unique(self):
        for ids in [["t1","t3"],["t1","t3","t5","t5"],["t1","t2","t3","t5"]]:
            value=example();value["model_output"]["guidance_coverage"]["reviewed_management_turn_ids"]=ids
            with self.subTest(ids=ids),self.assertRaises(ValidationError):self.check(value)

    def test_candidates_need_unique_ids_valid_sources_and_nonempty_summaries(self):
        for update in [{"candidate_local_id":"c2"},{"source_turn_ids":["missing"]},
                       {"source_turn_ids":["t2"]},{"source_turn_ids":[]},{"summary":" "}]:
            value=example();value["model_output"]["guidance_coverage"]["candidates"][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValidationError):self.check(value)

    def test_candidates_cannot_drop_or_mislink_claims_and_refinements(self):
        for update in [{"claim_local_ids":[]},{"claim_local_ids":["missing"]},{"claim_local_ids":["g1","g1"]},
                       {"exclusion_reason":"Unexplained mixed disposition."}]:
            value=example();value["model_output"]["guidance_coverage"]["candidates"][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValidationError):self.check(value)
        value=example();value["model_output"]["guidance_claims"][0]["evidence"].pop()
        with self.assertRaisesRegex(ValidationError,"source turns"):self.check(value)
        value=example();claim=deepcopy(value["model_output"]["guidance_claims"][0]);claim["claim_local_id"]="g2"
        value["model_output"]["guidance_claims"].append(claim)
        with self.assertRaisesRegex(ValidationError,"Every guidance claim"):self.check(value)

    def test_exclusions_are_explicit_and_do_not_create_guidance(self):
        value=example();items=value["model_output"]["guidance_coverage"]["candidates"]
        items.append({"candidate_local_id":"c4","kind":"other","source_turn_ids":["t1"],
            "summary":"General confidence in the cost plan.","disposition":"excluded","claim_local_ids":[],
            "exclusion_reason":"Generic conviction, not a separate measurable outlook."})
        self.assertEqual(self.check(value)["derived"]["guidance_coverage"]["excluded_candidate_count"],1)
        for update in [{"exclusion_reason":None},{"claim_local_ids":["g1"]}]:
            changed=deepcopy(value);changed["model_output"]["guidance_coverage"]["candidates"][-1].update(update)
            with self.subTest(update=update),self.assertRaises(ValidationError):self.check(changed)

    def test_qa_guidance_must_link_to_the_correct_topic(self):
        for links in [[],["c2"],["c2","c3","missing"],["c1","c2","c3"],["c2","c3","c3"]]:
            value=example();value["model_output"]["analyst_focus"]["topics"][0]["guidance_candidate_ids"]=links
            with self.subTest(links=links),self.assertRaises(ValidationError):self.check(value)

    def test_empty_guidance_is_valid_when_accounting_and_tone_are_consistent(self):
        from tests.company.test_transcript_analysis_contract import insufficient
        value=example();o=value["model_output"];value["input"]["turns"]=value["input"]["turns"][:1]
        value["input"]["turns"][0]["text"]="This concludes our prepared remarks."
        o["guidance_claims"]=[]
        o["guidance_coverage"]={"reviewed_management_turn_ids":["t1"],"candidates":[]}
        o["analyst_focus"]={"qa_observed":"absent","question_blocks":[],"topics":[],"ambiguities":[]}
        o["management_tone"].update(overall=insufficient(),prepared_remarks=insufficient(),qa=insufficient(),by_speaker=[])
        self.assertEqual(self.check(value)["derived"]["guidance_coverage"]["candidate_count"],0)

    def test_review_distinguishes_missing_qualification_and_acceptable_exclusion(self):
        v=example();r=review();validate_review(r,v["model_output"],v["input"]["turns"])
        for update in [{"related_claim_local_ids":[]},{"related_claim_local_ids":["missing"]},
                       {"missing_information":None},{"proposed_correction":None},
                       {"finding_type":"acceptable_exclusion"},{"category":"management_tone"}]:
            changed=review();changed["findings"][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValidationError):
                validate_review(changed,v["model_output"],v["input"]["turns"])
        r["verdict"]="accepted"
        r["findings"][0].update(finding_type="acceptable_exclusion",severity="warning",related_claim_local_ids=[],
                               missing_information=None,proposed_correction=None)
        validate_review(r,v["model_output"],v["input"]["turns"])

    def test_prompt_v4_pins_new_schemas_and_preserves_all_prior_requests(self):
        baseline=json.loads((Path(__file__).parent/"fixtures/transcript_request_pre_coverage.json").read_text())
        for i in baseline["vectors"]:
            args={"backend":i["backend"],"prompt_version":i["version"]}
            with self.subTest(version=i["version"],backend=i["backend"],review=i["review"]):
                self.assertEqual(model.configuration_for(review=i["review"],**args),i["configuration"])
                self.assertEqual(model.request_identity_for(baseline["source"],
                    review_analysis_id="fixture-parent" if i["review"] else None,**args),i["request_identity"])
                self.assertEqual(hashlib.sha256(model.make_request(baseline["source"],
                    analysis={"fixture":True} if i["review"] else None,prompt_version=i["version"])).hexdigest(),i["request_sha256"])
        for reviewing in [False,True]:
            request=json.loads(model.make_request(baseline["source"],analysis={"fixture":True} if reviewing else None))
            expected="transcript.review.v3" if reviewing else "transcript.analysis.v3"
            self.assertEqual(request["text"]["format"]["schema"]["properties"]["schema_version"]["enum"],[expected])
            self.assertEqual(request["reasoning"],{"effort":"high"})
            self.assertIn("guidance_candidate_ids",request["instructions"])


class CoveragePublicationTests(unittest.TestCase):
    setUp=fixtures.TranscriptPipelineTests.setUp
    tearDown=fixtures.TranscriptPipelineTests.tearDown

    def test_coverage_and_precise_review_persist_and_replay_without_calls(self):
        self.review=review()
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="3")
        result=self.job.execute(plan["plan_id"],fixtures.Transport([
            fixtures.response(self.output),fixtures.response(self.review,review=True)]))
        row=self.repository.find_request(model.request_identity_for(self.source))
        stored=self.repository.get(row["analysis_id"],as_of=fixtures.AT)
        self.assertEqual(stored["output"]["guidance_coverage"],self.output["guidance_coverage"])
        self.assertEqual(stored["reviews"][0]["output"]["findings"][0]["finding_type"],"missing_qualification")
        self.assertEqual(result["requests_this_run"],2)
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.job.execute(plan["plan_id"],fixtures.Transport([]))["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_missing_coverage_retains_response_but_does_not_publish_or_retry(self):
        o=deepcopy(self.output);o["guidance_coverage"]["reviewed_management_turn_ids"].pop()
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        raw=fixtures.response(o);before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):self.job.execute(plan["plan_id"],fixtures.Transport([raw]))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        req=model.request_identity_for(self.source)
        self.assertEqual((self.job.root/"responses"/(req+".json")).read_bytes(),raw)
        with self.assertRaises(ValidationError):self.job.execute(plan["plan_id"],fixtures.Transport([]))

if __name__=="__main__":unittest.main()
