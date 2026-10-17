"""Regression checks for versioned model-facing transcript constraints."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_analysis_model as model
from quant_data.company.transcript_analysis_contract import _schema, _guidance_value
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations import transcript_analysis as operation
from quant_data.operations.equibles_transcript_backfill import atomic
import tests.company.test_transcript_analysis_pipeline as fixtures


class RequestVersionTests(unittest.TestCase):
    def test_v1_configuration_request_and_identity_are_exactly_preserved(self):
        fixture=json.loads((Path(__file__).parent/"fixtures/transcript_request_v1.json").read_text())
        for v in fixture["vectors"]:
            with self.subTest(backend=v["backend"],review=v["review"]):
                args={"backend":v["backend"],"prompt_version":model.LEGACY_PROMPT_VERSION}
                self.assertEqual(model.configuration_for(review=v["review"],**args),v["configuration"])
                self.assertEqual(model.request_identity_for(fixture["source"],
                    review_analysis_id="fixture-parent" if v["review"] else None,**args),v["request_identity"])
                raw=model.make_request(fixture["source"],analysis={"fixture":True} if v["review"] else None,
                    prompt_version=model.LEGACY_PROMPT_VERSION)
                self.assertEqual(hashlib.sha256(raw).hexdigest(),v["request_sha256"])

    def test_new_requests_bind_corrected_schema_high_effort_and_twenty_minutes(self):
        source={"capture_id":"fixture","input_sha256":"1"*64}
        current=model.configuration_for(backend="codex_subscription")
        old=model.configuration_for(backend="codex_subscription",prompt_version=model.LEGACY_PROMPT_VERSION)
        self.assertNotEqual(current["schema_sha256"],old["schema_sha256"])
        self.assertNotEqual(current["prompt_sha256"],old["prompt_sha256"])
        self.assertEqual(current["reasoning_effort"],"high")
        self.assertEqual(current["request_timeout_seconds"],1200)
        self.assertEqual(current["model"],"gpt-5.6-terra")
        self.assertEqual(model.configuration_for(review=True)["model"],"gpt-5.6-sol")
        self.assertNotEqual(model.request_identity_for(source),
            model.request_identity_for(source,prompt_version=model.LEGACY_PROMPT_VERSION))
        raw=json.loads(model.make_request(source))
        self.assertEqual(raw["reasoning"],{"effort":"high"})
        self.assertEqual(raw["tools"],[])
        with self.assertRaises(ValidationError):
            model.make_request(source,prompt_version="unregistered")

    def test_model_schema_does_not_modify_the_canonical_or_legacy_schema(self):
        before=model.analysis_schema()
        schema=model.request_contract()[1]
        self.assertEqual(model.analysis_schema(),before)
        self.assertEqual(model.request_contract(prompt_version=model.LEGACY_PROMPT_VERSION)[1],before)
        schema["$defs"].clear()
        self.assertTrue(model.request_contract()[1]["$defs"])

    def test_numeric_nulls_and_wrong_bound_slots_have_no_model_schema_branch(self):
        schema=model.request_contract()[1]
        branches=schema["$defs"]["value"]["anyOf"]
        value=deepcopy(json.loads((fixtures.ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())["model_output"]["guidance_claims"][0]["value"])
        def matches(v):
            matched=[]
            for branch in branches:
                try: _schema(v,branch)
                except ValidationError: continue
                matched.append(branch)
            return matched
        self.assertEqual(len(matches(value)),1)
        value["scale"]=None
        self.assertEqual(matches(value),[])
        value.update(shape="lower_bound",point="1",low=None,high=None,scale="1")
        self.assertEqual(matches(value),[])
        value.update(point=None,low="1")
        self.assertEqual(len(matches(value)),1)

    def test_nonfiscal_targets_cannot_match_with_inferred_fiscal_labels(self):
        branches=model.request_contract()[1]["$defs"]["guidance_claim"]["properties"]["target"]["anyOf"]
        target={"period_text":"second half of 2026","period_kind":"other","fiscal_year":2026,"fiscal_quarter":None}
        def matches(value):
            for branch in branches:
                try: _schema(value,branch)
                except ValidationError: continue
                return True
            return False
        self.assertFalse(matches(target))
        target["fiscal_year"]=None
        self.assertTrue(matches(target))
        target.update(period_kind="fiscal_quarter",fiscal_year=2026,fiscal_quarter=3)
        self.assertTrue(matches(target))


class VersionedPipelineTests(unittest.TestCase):
    setUp=fixtures.TranscriptPipelineTests.setUp
    tearDown=fixtures.TranscriptPipelineTests.tearDown

    def test_legacy_plan_executes_legacy_bytes_then_replays_without_calls_or_writes(self):
        self.output=deepcopy(json.loads((fixtures.ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())["model_output"])
        self.output["management_tone"]["by_speaker"][0]["speaker_id"]=self.source["turns"][0]["speaker_id"]
        self.review.pop("schema_version")
        original=operation.configuration_for
        def legacy(**kwargs):
            kwargs.setdefault("prompt_version",model.LEGACY_PROMPT_VERSION)
            return original(**kwargs)
        with patch.object(operation,"configuration_for",side_effect=legacy):
            plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="2")
        transport=fixtures.Transport([fixtures.response(self.output),fixtures.response(self.review,review=True)])
        result=self.job.execute(plan["plan_id"],transport)
        self.assertEqual(result["requests_this_run"],2)
        self.assertEqual(transport.calls[0],json.loads(model.make_request(self.source,prompt_version=model.LEGACY_PROMPT_VERSION)))
        self.assertEqual(json.loads(self.repository.find_request(model.request_identity_for(self.source,
            prompt_version=model.LEGACY_PROMPT_VERSION))["configuration_json"])["prompt_version"],model.LEGACY_PROMPT_VERSION)
        before=mutation_fingerprint(self.stores)
        replay=self.job.execute(plan["plan_id"],fixtures.Transport([]))
        self.assertEqual(replay["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_rejected_draft_retains_raw_bytes_and_reports_the_exact_pointer(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        invalid=deepcopy(self.output)
        invalid["guidance_claims"][0]["value"]["scale"]=None
        raw=fixtures.response(invalid)
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            self.job.execute(plan["plan_id"],fixtures.Transport([raw]))
        report=json.loads((self.job.root/"reports"/(plan["plan_id"]+".json")).read_text())
        self.assertEqual(report["validation_issues"][0]["pointer"],"/guidance_claims/0/value/scale")
        response_path=self.job.root/"responses"/(model.request_identity_for(self.source)+".json")
        self.assertEqual(response_path.read_bytes(),raw)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with self.assertRaises(ValidationError):
            self.job.execute(plan["plan_id"],fixtures.Transport([]))

    def test_mixed_prompt_versions_and_wrong_deadline_fail_before_a_model_call(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,backend="codex_subscription")
        class WrongDeadline(fixtures.Transport):
            backend="codex_subscription"
            def _deadline_seconds(self): return 180
        transport=WrongDeadline([])
        with self.assertRaises(ValidationError):
            self.job.execute(plan["plan_id"],transport)
        self.assertEqual(transport.calls,[])
        stored=self.job._plan(plan["plan_id"])
        stored["reviewer_configuration"]=model.configuration_for(review=True,backend="codex_subscription",
            prompt_version=model.LEGACY_PROMPT_VERSION)
        identifier=operation.digest(stored)
        atomic(self.job.root/"plans"/(identifier+".json"),stored)
        with self.assertRaises(ValidationError): self.job._plan(identifier)


if __name__=="__main__": unittest.main()
