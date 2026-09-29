"""Synthetic-only checks for finite history extraction and reproducible sample review."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher, RawPage
from quant_data.company import transcript_structured_call_terra_sol as profile
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.equibles_transcript_backfill import atomic
from quant_data.operations.transcript_history_batch import TranscriptHistoryBatch, sample_ids
from tests.company import test_transcript_analysis_pipeline as fixtures
from tests.company.test_transcript_structured_call import example, review
from tests.company.test_transcript_analysis_pair import response


class HistoryBatchTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def batch(self):
        return TranscriptHistoryBatch(self.stores,self.registry,
            Path(self.temp.name)/"history",Path(self.temp.name)/"pair",clock=lambda:fixtures.AT)

    def extra_quarters(self, quarters=(2,3)):
        for quarter in quarters:
            event={**self.event,"id":"call_AAPL_2026_"+str(quarter),"fiscalQuarter":quarter}
            raw=json.loads(self.raw)
            raw.update(eventId=event["id"],fiscalQuarter=quarter)
            EquiblesTranscriptPublisher(self.stores,self.registry).publish(
                symbol="AAPL",instrument_id="frozen-fixture-AAPL",event=event,
                pages=(RawPage(dumps_strict(raw).encode(),fixtures.SOURCE_AT,"equibles-transcripts/blobs/"+hashlib.sha256(dumps_strict(raw).encode()).hexdigest()+".json"),))

    def transport(self, calls, *, fail_capture=None, malformed_capture=None):
        class Auto:
            backend=profile.BACKEND
            def _deadline_seconds(self):return 1200
            def prepare_credentials(self):pass
            def request(self, raw):
                req=json.loads(raw)
                supplied=json.loads(req["input"][0]["content"][0]["text"])
                source=supplied["source"]
                calls.append((source["capture_id"],req["model"],req["reasoning"]["effort"]))
                if source["capture_id"]==fail_capture:
                    raise ResourceLimitError("Synthetic timeout")
                value=example(source)
                if req["model"]==profile.EXTRACTOR:
                    if source["capture_id"]==malformed_capture:
                        value["headline"]["message"]="word "*26
                    return response(value,profile.EXTRACTOR)
                draft=supplied["draft_brief"]
                return response(review(value,"approved" if draft==value else "revised"),profile.REVIEWER)
        return Auto

    def prepare(self, job, **kwargs):
        return job.prepare(ticker_count=1,sample_count=1,seed="a"*64,**kwargs)

    def test_full_history_and_no_canonical_publication_or_repeat_calls(self):
        self.extra_quarters()
        job=self.batch();before=mutation_fingerprint(self.stores)
        plan=self.prepare(job,concurrency=3)
        self.assertEqual(len(plan["entries"]),3)
        self.assertEqual(plan["max_extraction_calls"],3)
        calls=[]
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("No canonical writes")):
            result=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["automated_passed"],3)
        self.assertEqual(result["sample_reviews_finished"],1)
        self.assertEqual(len(calls),4)
        self.assertEqual([c[1] for c in calls], [profile.EXTRACTOR]*3+[profile.REVIEWER])
        self.assertEqual([c[2] for c in calls], ["high"]*3+["medium"])
        replay=[]
        again=job.execute(plan["plan_id"],self.transport(replay))
        self.assertEqual(replay,[])
        self.assertEqual(again["model_calls"],4)  # Historical total retained on replay.
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_invalid_extraction_does_not_stop_other_quarters_or_trigger_unsampled_reviews(self):
        self.extra_quarters()
        job=self.batch();plan=self.prepare(job,concurrency=1)
        calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls,malformed_capture=self.capture))
        self.assertEqual(result["extractions_finished"],3)
        self.assertEqual(result["automated_passed"],2)
        self.assertEqual(result["extractions_needing_attention"],1)
        self.assertEqual(len(calls),4)
        self.assertEqual(result["schema_shaped"],3)

    def test_uncertain_failure_is_retained_without_resume_retry(self):
        job=self.batch();plan=self.prepare(job)
        calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls,fail_capture=self.capture))
        self.assertEqual((len(calls),result["sample_reviews_finished"]),(1,0))
        replay=[]
        job.execute(plan["plan_id"],self.transport(replay))
        self.assertEqual(replay,[])

    def test_frozen_source_tampering_rejects_before_any_model_access(self):
        job=self.batch();plan=self.prepare(job)
        path=job.root/"sources"/(plan["entries"][0]["source_sha256"]+".json")
        value=json.loads(path.read_text());value["turns"][0]["text"]+=" changed"
        atomic(path,value,replace=True)
        calls=[]
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(calls,[])

    def test_frozen_plan_tampering_rejects_before_any_model_access(self):
        job=self.batch();plan=self.prepare(job)
        path=job.root/"plans"/(plan["plan_id"]+".json")
        value=json.loads(path.read_text());value["max_extraction_calls"]+=1
        atomic(path,value,replace=True)
        calls=[]
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(calls,[])

    def test_sample_selection_is_stable_unique_and_independent_of_completion_order(self):
        values=["capture-"+str(i) for i in range(20)]
        selected=sample_ids(values,"a"*64,5)
        self.assertEqual(selected,sample_ids(list(reversed(values)),"a"*64,5))
        self.assertEqual(len(set(selected)),5)
        self.assertNotEqual(selected,sample_ids(values,"b"*64,5))

    def test_saved_sample_cannot_be_replaced_by_a_different_selection(self):
        job=self.batch();plan=self.prepare(job)
        calls=[];job.execute(plan["plan_id"],self.transport(calls))
        path=job.root/"runs"/plan["plan_id"]/"sample.json"
        value=json.loads(path.read_text());value["capture_ids"]=[]
        atomic(path,value,replace=True)
        replay=[]
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],self.transport(replay))
        self.assertEqual(replay,[])

    def test_request_bound_failure_is_visible_and_uses_no_model_calls(self):
        job=self.batch()
        with patch.object(profile,"make_request",side_effect=ResourceLimitError("Complete source too large")):
            plan=self.prepare(job)
        self.assertEqual(plan["max_extraction_calls"],0)
        calls=[];result=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(calls,[])
        self.assertEqual(result["extractions_needing_attention"],1)

    def test_foreign_transport_or_deadline_is_rejected(self):
        job=self.batch();plan=self.prepare(job)
        calls=[];factory=self.transport(calls)
        original=factory._deadline_seconds
        factory._deadline_seconds=lambda self:1800
        result=job.execute(plan["plan_id"],factory)
        self.assertEqual(calls,[])
        self.assertEqual(result["extractions_needing_attention"],1)
        factory._deadline_seconds=original

    def test_three_transport_failures_stop_further_queued_requests(self):
        self.extra_quarters((2,3,4))
        job=self.batch();plan=self.prepare(job,concurrency=1)
        calls=[]
        class Failing:
            backend=profile.BACKEND
            def _deadline_seconds(self):return 1200
            def prepare_credentials(self):pass
            def request(self,raw):
                calls.append(raw)
                raise ResourceLimitError("Synthetic provider unavailable")
        result=job.execute(plan["plan_id"],Failing)
        self.assertEqual(result["status"],"stopped_after_transport_failures")
        self.assertEqual(len(calls),3)
        self.assertEqual(result["sample_reviews_finished"],0)
