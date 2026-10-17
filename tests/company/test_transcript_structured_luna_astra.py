"""Synthetic checks for the Luna/high profile and Astra/high review boundary."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from quant_data.company import transcript_structured_call_luna_astra as profile
from quant_data.company import transcript_structured_call_terra_sol as previous
from quant_data.company import transcript_analysis_codex as codex
from quant_data.company.transcript_structured_store import prepare_output
from quant_data.errors import ValidationError, ConflictError, ResourceLimitError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.transcript_universe_batch import TranscriptUniverseBatch
from quant_data.operations.transcript_structured_import import exchange
from quant_data.operations.equibles_transcript_backfill import atomic
from tests.company import test_transcript_analysis_pipeline as fixture
from tests.company.test_transcript_analysis_pair import response, events
from tests.company.test_transcript_structured_call import example


class LunaAstraTests(unittest.TestCase):
    setUp=fixture.TranscriptPipelineTests.setUp
    tearDown=fixture.TranscriptPipelineTests.tearDown

    def batch(self, selected=profile):
        return TranscriptUniverseBatch(self.stores,self.registry,Path(self.temp.name)/"universe",
            Path(self.temp.name)/"pairs",extraction_profile=selected,
            clock=lambda:"2026-09-13T16:00:00.000000Z")

    def transport(self,calls, failure=False):
        class Native:
            backend=profile.BACKEND
            def _deadline_seconds(self):return 1200
            def prepare_credentials(self):pass
            def request(self,raw):
                request=json.loads(raw);calls.append(request)
                if failure:raise ResourceLimitError("Synthetic failed attempt")
                source=json.loads(request["input"][0]["content"][0]["text"])["source"]
                return response(example(source),request["model"])
        return Native

    def forbidden(self):
        raise AssertionError("No additional transport or credentials")

    def test_extraction_preserves_prompt_schema_source_and_budget(self):
        old=json.loads(previous.make_request(self.source))
        new=json.loads(profile.make_request(self.source))
        self.assertEqual(new.pop("model"),"gpt-5.6-luna");old.pop("model")
        self.assertEqual(new["reasoning"],{"effort":"high"})
        new["text"]["format"]["name"]=old["text"]["format"]["name"]
        self.assertEqual(new,old)
        self.assertEqual(profile.configuration_for()["prompt_sha256"],previous.configuration_for()["prompt_sha256"])
        self.assertNotEqual(profile.configuration_for(),previous.configuration_for())

    def test_astra_review_keeps_complete_source_original_and_project_goal(self):
        draft=example(self.source)
        request=json.loads(profile.make_request(self.source,analysis=draft))
        self.assertEqual((request["model"],request["reasoning"]["effort"]),("gpt-6-astra","high"))
        supplied=json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(supplied["source"],self.source)
        self.assertIn(draft,supplied.values())
        self.assertIn("ORIGINAL Luna draft",request["instructions"])
        self.assertIn("not an exhaustive transcript reconstruction",request["instructions"])

    def test_native_model_and_effort_are_pinned_and_no_api_fallback(self):
        for review in (False,True):
            native=codex.CodexTranscriptTransport(evidence_root=Path(self.temp.name)/str(review),timeout_seconds=1200)
            native._ready=True
            raw=profile.make_request(self.source,analysis=example(self.source) if review else None)
            with patch.object(native,"_run",return_value=(events(example(self.source)),b"")) as run:
                result=json.loads(native.request(raw))
            command=run.call_args.args[0]
            self.assertIn('model_reasoning_effort="high"',command)
            self.assertIn('forced_login_method="chatgpt"',command)
            self.assertTrue(any("request_max_retries=0" in item for item in command))
            self.assertEqual(result["model"],profile.REVIEWER if review else profile.EXTRACTOR)
            self.assertEqual(result["transport_evidence"]["transport"],"codex_exec.v2")

    def test_new_models_cannot_bypass_fixed_role_prompt_or_schema(self):
        native=codex.CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"reject")
        native._ready=True
        for review in (False,True):
            base=json.loads(profile.make_request(self.source,analysis=example(self.source) if review else None))
            for key in ("effort","prompt","format","schema","role"):
                request=deepcopy(base)
                if key=="effort":request["reasoning"]["effort"]="medium"
                elif key=="prompt":request["instructions"]="Generic request"
                elif key=="format":request["text"]["format"]["name"]="unknown"
                elif key=="schema":request["text"]["format"]["schema"]={}
                else:request["model"]=profile.EXTRACTOR if review else profile.REVIEWER
                with self.subTest(review=review,key=key),patch.object(native,"_run",side_effect=AssertionError("No model run")):
                    with self.assertRaises(ValidationError):native.request(dumps_strict(request).encode())

    def test_luna_outputs_publish_exactly_and_replay_without_calls(self):
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        self.assertEqual(plan["configuration"]["model"],"gpt-5.6-luna")
        result=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual((len(calls),result["canonical_outputs_stored"]),(1,1))
        entry=job.entries(plan)[0];report=json.loads(job.record_path(plan["plan_id"],self.capture).read_text())
        receipt,raw,draft=exchange(job.pair_root,report,stage="extraction",source=self.source,profile=profile)
        from quant_data.stores import quiet_immutable_read_connection
        with quiet_immutable_read_connection(self.stores,"company") as c:
            row=c.execute("SELECT model,reasoning_effort,raw_response FROM company_structured_transcript_outputs").fetchone()
            self.assertEqual((row[0],row[1],bytes(row[2])),("gpt-5.6-luna","high",raw))
        before=mutation_fingerprint(self.stores)
        job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_legacy_plan_cannot_silently_switch_models(self):
        old=self.batch(previous);plan=old.prepare(concurrency=1)
        with self.assertRaises(ConflictError):self.batch().plan(plan["plan_id"])
        self.assertEqual(old.plan(plan["plan_id"])["configuration"],previous.configuration_for())

    def test_failed_luna_attempt_is_not_retried(self):
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls,True))
        self.assertEqual(result["outcomes"],{"model_failed":1})
        job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual(len(calls),1)

    def test_publisher_rejects_unpinned_luna_configuration(self):
        config=profile.configuration_for();config["reasoning_effort"]="medium"
        with self.assertRaises(ValidationError):
            prepare_output(source=self.source,request_identity="a"*64,configuration=config,
                raw_response=response(example(self.source),profile.EXTRACTOR),available_at=fixture.AT)


if __name__=="__main__":unittest.main()
