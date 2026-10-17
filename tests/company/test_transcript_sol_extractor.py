from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_transcript_analysis_pipeline as fixtures
from quant_data.company import transcript_analysis_model as model
from quant_data.company import transcript_analysis_codex as codex
from quant_data.operations.transcript_analysis_pilot import TranscriptExtractionPilot
from quant_data.operations.equibles_transcript_backfill import atomic
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint

ROOT=Path(__file__).resolve().parents[2]


class SolSelectionTests(unittest.TestCase):
    def test_all_sixteen_prior_requests_and_identities_remain_exact(self):
        baseline=json.loads((ROOT/"tests/company/fixtures/transcript_request_pre_sol_extractor.json").read_text())
        self.assertEqual(len(baseline["vectors"]),16)
        for v in baseline["vectors"]:
            args={"backend":v["backend"],"prompt_version":v["version"]}
            with self.subTest(version=v["version"],backend=v["backend"],review=v["review"]):
                self.assertEqual(model.configuration_for(review=v["review"],**args),v["configuration"])
                self.assertEqual(model.request_identity_for(baseline["source"],
                    review_analysis_id="fixture-parent" if v["review"] else None,**args),v["request_identity"])
                self.assertEqual(hashlib.sha256(model.make_request(baseline["source"],
                    analysis={"fixture":True} if v["review"] else None,prompt_version=v["version"])).hexdigest(),v["request_sha256"])

    def test_sol_changes_only_the_model_and_its_identity(self):
        source={"capture_id":"fixture","input_sha256":"a"*64,"turns":[]}
        terra=json.loads(model.make_request(source))
        sol=json.loads(model.make_request(source,extractor_model=model.REVIEWER))
        self.assertEqual(sol.pop("model"),"gpt-5.6-sol")
        terra.pop("model")
        self.assertEqual(sol,terra)
        a=model.configuration_for(backend="codex_subscription")
        b=model.configuration_for(backend="codex_subscription",extractor_model=model.REVIEWER)
        self.assertEqual({k:v for k,v in a.items() if k!="model"},{k:v for k,v in b.items() if k!="model"})
        self.assertEqual(b["reasoning_effort"],"high")
        self.assertEqual(b["request_timeout_seconds"],1200)
        self.assertNotEqual(model.request_identity_for(source,backend="codex_subscription"),
                            model.request_identity_for(source,backend="codex_subscription",extractor_model=model.REVIEWER))

    def test_selector_rejects_unsupported_models_legacy_sol_and_review_overrides(self):
        source={"capture_id":"fixture","input_sha256":"a"*64,"turns":[]}
        for selected in ["gpt-6-astra","gpt-5.6-luna","",{},True]:
            with self.subTest(selected=selected),self.assertRaises(ValidationError):
                model.make_request(source,extractor_model=selected)
        for version in [model.LEGACY_PROMPT_VERSION,model.PREVIOUS_PROMPT_VERSION,model.SUMMARY_PROMPT_VERSION]:
            with self.subTest(version=version),self.assertRaises(ValidationError):
                model.make_request(source,prompt_version=version,extractor_model=model.REVIEWER)
        with self.assertRaises(ValidationError):
            model.make_request(source,analysis={},extractor_model=model.REVIEWER)

    def test_optional_api_estimate_uses_sol_rates_without_changing_legacy_rates(self):
        usage={"input_tokens":1000,"output_tokens":1000}
        self.assertEqual(model.usage_usd(usage,extractor_model=model.REVIEWER),model.usage_usd(usage,review=True))
        self.assertEqual(model.reservation_usd(extractor_model=model.REVIEWER),model.reservation_usd(review=True))
        self.assertLess(model.reservation_usd(),model.reservation_usd(extractor_model=model.REVIEWER))


class PilotTransport(fixtures.Transport):
    backend="codex_subscription"
    def __init__(self,values):
        super().__init__(values)
        self.preflights=0
    def _deadline_seconds(self):return 1200
    def prepare_credentials(self):self.preflights+=1


class SolPilotTests(unittest.TestCase):
    setUp=fixtures.TranscriptPipelineTests.setUp
    tearDown=fixtures.TranscriptPipelineTests.tearDown

    def pilot(self,clock=lambda:fixtures.AT):
        return TranscriptExtractionPilot(self.stores,self.registry,Path(self.temp.name)/"pilot",clock=clock)

    def sol_response(self,output=None):
        # Same output envelope, with Sol as extractor rather than review schema.
        return fixtures.response(self.output if output is None else output,review=True)

    def test_preparation_is_private_one_call_and_has_no_database_effect(self):
        before=mutation_fingerprint(self.stores)
        plan=self.pilot().prepare(capture_id=self.capture)
        self.assertEqual(plan["max_requests"],1)
        self.assertEqual(plan["model_requests_made"],0)
        self.assertEqual(plan["publication"],"private_only")
        self.assertEqual(plan["configuration"]["model"],model.REVIEWER)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_valid_sol_output_is_retained_and_replays_without_credentials_calls_or_writes(self):
        job=self.pilot();plan=job.prepare(capture_id=self.capture)
        before=mutation_fingerprint(self.stores)
        transport=PilotTransport([self.sol_response()])
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("Private pilot cannot publish")):
            result=job.execute(plan["plan_id"],transport)
        self.assertEqual(result["outcome"],"validated_private")
        self.assertFalse(result["canonical_published"])
        self.assertEqual(result["requests_this_run"],1)
        self.assertEqual(transport.calls[0]["model"],model.REVIEWER)
        self.assertEqual(len(transport.calls),1)
        saved=job.root/"responses"/(result["request_identity"]+".json")
        self.assertEqual(saved.read_bytes(),self.sol_response())
        replay=PilotTransport([])
        again=job.execute(plan["plan_id"],replay)
        self.assertEqual(again["requests_this_run"],0)
        self.assertEqual(replay.preflights,0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_invalid_coverage_is_retained_and_replayed_without_retry(self):
        job=self.pilot();plan=job.prepare(capture_id=self.capture)
        bad=deepcopy(self.output);bad["guidance_coverage"]["reviewed_management_turn_ids"].pop()
        raw=self.sol_response(bad);transport=PilotTransport([raw])
        before=mutation_fingerprint(self.stores)
        result=job.execute(plan["plan_id"],transport)
        self.assertEqual(result["outcome"],"validation_failed")
        self.assertIn("reviewed_management_turn_ids",result["issues"][0]["pointer"])
        self.assertEqual((job.root/"responses"/(result["request_identity"]+".json")).read_bytes(),raw)
        again=job.execute(plan["plan_id"],PilotTransport([]))
        self.assertEqual(again["outcome"],"validation_failed")
        self.assertEqual(again["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_output_schema_downgrade_and_wrong_model_are_rejected(self):
        for change in ["schema","model"]:
            with self.subTest(change=change):
                job=TranscriptExtractionPilot(self.stores,self.registry,Path(self.temp.name)/change,clock=lambda:fixtures.AT)
                plan=job.prepare(capture_id=self.capture)
                output=deepcopy(self.output)
                if change=="schema":output["schema_version"]="transcript.analysis.v2"
                raw=self.sol_response(output) if change=="schema" else fixtures.response(output)
                result=job.execute(plan["plan_id"],PilotTransport([raw]))
                self.assertEqual(result["outcome"],"validation_failed")
                self.assertFalse(result["canonical_published"])

    def test_failed_attempt_cannot_retry_even_with_a_new_plan(self):
        job=self.pilot();plan=job.prepare(capture_id=self.capture)
        failed=PilotTransport([ResourceLimitError("fixture timeout")])
        with self.assertRaises(ResourceLimitError):
            job.execute(plan["plan_id"],failed)
        newjob=self.pilot(clock=lambda:fixtures.LATER)
        newplan=newjob.prepare(capture_id=self.capture)
        self.assertNotEqual(plan["plan_id"],newplan["plan_id"])
        replay=PilotTransport([])
        with self.assertRaises(ConflictError):
            newjob.execute(newplan["plan_id"],replay)
        self.assertEqual(replay.preflights,0)
        self.assertEqual(replay.calls,[])

    def test_transport_mismatch_fails_before_credentials(self):
        job=self.pilot();plan=job.prepare(capture_id=self.capture)
        for kind in ["backend","deadline"]:
            transport=PilotTransport([])
            if kind=="backend":transport.backend="openai_api"
            else:transport._deadline_seconds=lambda:180
            with self.subTest(kind=kind),self.assertRaises(ValidationError):
                job.execute(plan["plan_id"],transport)
            self.assertEqual(transport.preflights,0)

    def test_rehashed_plan_cannot_broaden_scope_or_change_source(self):
        job=self.pilot();prepared=job.prepare(capture_id=self.capture)
        original=json.loads((job.root/"plans"/(prepared["plan_id"]+".json")).read_text())
        for field,value in [("max_requests",2),("publication","canonical"),("backend","openai_api"),("input_sha256","0"*64)]:
            plan=deepcopy(original);plan[field]=value
            from quant_data.operations import transcript_analysis as m
            identifier=m.digest(plan);atomic(job.root/"plans"/(identifier+".json"),plan)
            transport=PilotTransport([])
            with self.subTest(field=field),self.assertRaises((ValidationError,ConflictError)):
                job.execute(identifier,transport)
            self.assertEqual(transport.preflights,0)
            self.assertEqual(transport.calls,[])

    def test_response_hash_tampering_prevents_replay(self):
        job=self.pilot();plan=job.prepare(capture_id=self.capture)
        result=job.execute(plan["plan_id"],PilotTransport([self.sol_response()]))
        path=job.root/"responses"/(result["request_identity"]+".json")
        atomic(path,self.sol_response()+b" ",replace=True)
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],PilotTransport([]))

    def test_canonical_publisher_continues_to_reject_sol_configuration(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            self.publisher.publish(source=self.source,
                request_identity=model.request_identity_for(self.source,extractor_model=model.REVIEWER),
                configuration=model.configuration_for(extractor_model=model.REVIEWER),
                raw_response=self.sol_response(),started_at=fixtures.AT,completed_at=fixtures.AT)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_native_transport_accepts_sol_extraction_with_unchanged_schema(self):
        raw=model.make_request(self.source,extractor_model=model.REVIEWER)
        transport=codex.CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"native",timeout_seconds=1200)
        transport._ready=True
        events=(json.dumps({"type":"turn.started"})+"\n"+
            json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(self.output)}})+"\n"+
            json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":500}})+"\n").encode()
        with patch.object(transport,"_run",return_value=(events,b"")) as run:
            response=json.loads(transport.request(raw))
        args=run.call_args.args
        self.assertEqual(args[0][args[0].index("--model")+1],"gpt-5.6-sol")
        self.assertEqual(json.loads(args[2])["source"],self.source)
        self.assertEqual(response["model"],"gpt-5.6-sol")
