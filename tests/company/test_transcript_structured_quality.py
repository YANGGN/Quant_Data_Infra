from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from quant_data.company import transcript_structured_quality as quality
from quant_data.company import transcript_structured_call as legacy
from quant_data.company import transcript_analysis_codex as codex
from quant_data.errors import ConflictError,ResourceLimitError,ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.operations.transcript_quality_resample import TranscriptQualityResample
from .test_transcript_structured_call import example,measure
from .test_transcript_analysis_pair import response,events,PairTransport

ROOT=Path(__file__).resolve().parents[2]


def reviewed(value,decision="approved"):
    return {"schema_version":quality.REVIEW_VERSION,"decision":decision,"brief":deepcopy(value),
            "draft_findings":[],"source_uncertainties":[],"unresolved_issues":[],"editorial_notes":[]}


class TieredQualityTests(unittest.TestCase):
    def setUp(self):
        self.source=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V3.example.json").read_text())["input"]
        self.source.update(symbol="FIX",fiscal_year=2026,fiscal_quarter=1,capture_id="synthetic")
        for turn in self.source["turns"]:turn.setdefault("section","qa")
        self.source["turns"][0]["text"]+=" business "*1500
        self.value=example(self.source)

    def test_shared_comparison_labels_are_valid_and_legacy_remains_frozen(self):
        self.value["reported_results"]=[{"metric":name,"period":"2026 Q1","value":measure(amount="10",unit="million"),
            "comparison":"year over year","source_turn_ids":["t1"]} for name in ("Revenue","Operating profit")]
        self.assertNotEqual(quality.assess_brief(self.value,self.source)["status"],"blocked")
        with self.assertRaisesRegex(ValidationError,"repeated assertions"):
            legacy.validate_brief(self.value,self.source)

    def test_unknown_roles_are_uncertainty_without_relabelling_or_dropping_content(self):
        self.source["turns"][0]["speaker_role"]="unknown"
        original=deepcopy(self.source);draft=deepcopy(self.value)
        assessment=quality.validate_brief(self.value,self.source)
        self.assertEqual(assessment["status"],"accepted_with_flags")
        self.assertTrue(any(f["category"]=="source_metadata" for f in assessment["findings"]))
        self.assertEqual(self.source,original);self.assertEqual(self.value,draft)

    def test_malformed_numeric_value_is_retained_as_blocker_without_stopping_assessment(self):
        self.value["guidance"][0]["current"]=measure(amount="10",unit="million")
        self.value["guidance"][0]["current"]["amount"]=None
        original=deepcopy(self.value)
        assessed=quality.assess_brief(self.value,self.source)
        self.assertEqual(assessed["status"],"blocked")
        self.assertIsNone(assessed["word_count"])
        self.assertTrue(any(f["category"]=="number" for f in assessed["findings"]))
        request=json.loads(quality.make_request(self.source,analysis=self.value))
        self.assertEqual(json.loads(request["input"][0]["content"][0]["text"])["draft_brief"],original)
        self.assertEqual(self.value,original)

    def test_known_analyst_or_missing_citation_remains_blocking(self):
        for ids in (["t2"],["missing"]):
            value=deepcopy(self.value);value["guidance"][0]["source_turn_ids"]=ids
            self.assertEqual(quality.assess_brief(value,self.source)["status"],"blocked")

    def test_wrong_identity_bad_range_and_contradictory_guidance_are_blockers(self):
        for change in ("identity","range","withdrawn","direction"):
            value=deepcopy(self.value)
            if change=="identity":value["call"]["symbol"]="OTHER"
            elif change=="range":value["guidance"][0]["current"].update(low="49",high="48")
            elif change=="withdrawn":value["guidance"][0]["change"]="withdrawn"
            else:value["guidance"][0].update(previous=measure(low="48",high="49",unit="%"),change="raised")
            with self.subTest(change=change):
                self.assertEqual(quality.assess_brief(value,self.source)["status"],"blocked")

    def test_identical_metric_duplicate_warns_conflicting_values_block(self):
        value=deepcopy(self.value);value["guidance"].append(deepcopy(value["guidance"][0]))
        self.assertEqual(quality.assess_brief(value,self.source)["status"],"accepted_with_flags")
        value["guidance"][1]["current"]["high"]="49"
        self.assertEqual(quality.assess_brief(value,self.source)["status"],"blocked")

    def test_modest_overruns_warn_gross_overruns_block(self):
        self.value["headline"]["message"]=" ".join(["growth"]*28)
        result=quality.assess_brief(self.value,self.source)
        self.assertEqual(result["status"],"accepted_with_flags")
        self.value["headline"]["message"]=" ".join(["growth"]*40)
        self.assertEqual(quality.assess_brief(self.value,self.source)["status"],"blocked")

    def test_uncertainty_does_not_require_failed_review(self):
        self.source["turns"][0]["speaker_role"]="unknown"
        value=reviewed(self.value)
        value["source_uncertainties"]=[{"category":"source_metadata","section":"guidance",
            "description":"Speaker role is missing.","turn_ids":["t1"]}]
        result=quality.validate_editorial_review(value,self.value,self.source)
        self.assertEqual(result["status"],"accepted_with_flags")

    def test_numeric_findings_cannot_be_downgraded_to_editorial(self):
        value=reviewed(self.value)
        value["draft_findings"]=[{"severity":"editorial","category":"number","pointer":"/guidance/0/current",
            "description":"Wrong value.","turn_ids":["t1"],"corrected":False}]
        with self.assertRaisesRegex(ValidationError,"severity"):
            quality.validate_editorial_review(value,self.value,self.source)

    def test_bad_finding_pointer_and_false_correction_claim_reject(self):
        for pointer,corrected in (("/nonexistent",False),("/guidance",True)):
            value=reviewed(self.value)
            value["draft_findings"]=[{"severity":"editorial","category":"style","pointer":pointer,
                "description":"Wording.","turn_ids":["t1"],"corrected":corrected}]
            with self.subTest(pointer=pointer),self.assertRaises(ValidationError):
                quality.validate_editorial_review(value,self.value,self.source)

    def test_unresolved_substantive_issue_needs_attention_but_can_be_retained(self):
        value=reviewed(self.value,"needs_attention")
        value["unresolved_issues"]=[{"category":"number","section":"guidance","description":"Unable to verify value.","turn_ids":["t1"]}]
        quality.validate_editorial_review(value,self.value,self.source)
        value["decision"]="approved"
        with self.assertRaises(ValidationError):
            quality.validate_editorial_review(value,self.value,self.source)

    def test_full_source_original_draft_and_tiered_assessment_are_in_review_request(self):
        request=json.loads(quality.make_request(self.source,analysis=self.value))
        supplied=json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(supplied["source"],self.source)
        self.assertEqual(supplied["draft_brief"],self.value)
        self.assertIn("draft_automated_assessment",supplied)
        self.assertEqual((request["model"],request["reasoning"]["effort"]),(quality.REVIEWER,"medium"))
        self.assertNotEqual(quality.VERSION,legacy.VERSION)

    def test_native_transport_accepts_fixed_tiered_roles_and_rejects_role_swap(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            for is_review in (False,True):
                native=codex.CodexTranscriptTransport(evidence_root=Path(folder)/str(is_review),timeout_seconds=1200)
                native._ready=True
                raw=quality.make_request(self.source,analysis=self.value if is_review else None)
                with patch.object(native,"_run",return_value=(events(reviewed(self.value) if is_review else self.value),b"")):
                    self.assertEqual(json.loads(native.request(raw))["status"],"completed")
                changed=json.loads(raw);changed["model"]=quality.EXTRACTOR if is_review else quality.REVIEWER
                with self.assertRaises(ValidationError):native.request(dumps_strict(changed).encode())


class TieredResampleTests(unittest.TestCase):
    setUp=TieredQualityTests.setUp
    # Only resampling cases use this fixture; inherited quality cases remain in their own class.
    def entries(self):
        result=[]
        for i in range(6):
            source=deepcopy(self.source);source["capture_id"]="synthetic-"+str(i)
            result.append({"source":source,"draft":example(source),"parent_response_sha256":str(i)*64})
        return result

    def test_fresh_random_reviews_only_and_zero_call_replay(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            job=TranscriptQualityResample(Path(folder)/"sample",Path(folder)/"pair")
            plan=job.prepare(self.entries(),excluded_ids=["synthetic-0"],count=5,seed="b"*64)
            self.assertNotIn("synthetic-0",[r["capture_id"] for r in plan["entries"]])
            calls=[]
            class Auto:
                backend=quality.BACKEND
                def prepare_credentials(self):pass
                def _deadline_seconds(self):return 1200
                def request(self,raw):
                    request=json.loads(raw);calls.append(request)
                    supplied=json.loads(request["input"][0]["content"][0]["text"])
                    return response(reviewed(supplied["draft_brief"]),quality.REVIEWER)
            result=job.execute(plan["plan_id"],Auto)
            self.assertEqual(result["reviews_passed"],5)
            self.assertEqual(result["new_review_calls"],5)
            self.assertTrue(all(r["model"]==quality.REVIEWER and r["reasoning"]["effort"]=="medium" for r in calls))
            calls.clear()
            replay=job.execute(plan["plan_id"],Auto)
            self.assertEqual((replay["new_review_calls"],calls),(0,[]))

    def test_uncertain_review_is_not_retried(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            job=TranscriptQualityResample(Path(folder)/"sample",Path(folder)/"pair")
            plan=job.prepare(self.entries(),excluded_ids=[],count=1,seed="c"*64)
            calls=[]
            class Failing:
                backend=quality.BACKEND
                def prepare_credentials(self):pass
                def _deadline_seconds(self):return 1200
                def request(self,raw):
                    calls.append(raw);raise ResourceLimitError("Synthetic timeout")
            self.assertEqual(job.execute(plan["plan_id"],Failing)["reviews_needing_attention"],1)
            job.execute(plan["plan_id"],Failing)
            self.assertEqual(len(calls),1)

    def test_frozen_source_tamper_stops_before_model_call(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            job=TranscriptQualityResample(Path(folder)/"sample",Path(folder)/"pair")
            plan=job.prepare(self.entries(),excluded_ids=[],count=1,seed="c"*64)
            row=plan["entries"][0];path=job.root/"inputs"/(row["input_sha256"]+".json")
            entry=json.loads(path.read_text());entry["source"]["symbol"]="OTHER"
            path.write_text(dumps_strict(entry))
            with self.assertRaises(ConflictError):
                job.execute(plan["plan_id"],lambda: (_ for _ in ()).throw(AssertionError("No model")))
