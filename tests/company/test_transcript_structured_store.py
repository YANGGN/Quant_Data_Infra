"""Temporary-store tests for immutable structured drafts and independent assessments."""
from copy import deepcopy
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from quant_data.company import transcript_structured_quality as quality
from quant_data.company import transcript_structured_call_terra_sol as profile
from quant_data.company.transcript_analysis import digest
from quant_data.company.transcript_structured_registry import add_declarations, DATASET, MIGRATION_ID
from quant_data.company.transcript_structured_store import (
    StructuredTranscriptPublisher, StructuredTranscriptRepository, prepare_output, prepare_assessment)
from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry, structured_transcript_registry_profile
from quant_data.stores import quiet_immutable_read_connection, writer_connection
from tests.company import test_transcript_analysis_pipeline as fixture
from tests.company.test_transcript_structured_call import example
from tests.company.test_transcript_analysis_pair import response

ROOT=Path(__file__).resolve().parents[2]
PUBLISHED="2026-09-13T03:00:00.000000Z"
LATER="2026-09-13T04:00:00.000000Z"

def candidate(root):
    raw=json.loads((ROOT/"config/system_registry.json").read_text())
    if raw["registry_version"]=="2.82.0":add_declarations(raw,ROOT)
    p=root/"candidate.json";p.write_text(json.dumps(raw,ensure_ascii=True,indent=1,sort_keys=True)+"\n")
    return load_registry(p,project_root=ROOT,environment={})

class StructuredStoreTests(unittest.TestCase):
    def setUp(self):
        fixture.TranscriptPipelineTests.setUp(self)
        self.old_registry=self.registry
        self.registry=candidate(Path(self.temp.name))
        migrate_and_register_store(self.stores,self.registry,"company",applied_at=PUBLISHED)
        self.pub=StructuredTranscriptPublisher(self.stores,self.registry)
        self.repo=StructuredTranscriptRepository(self.stores)
        self.draft=example(self.source)
        self.row=prepare_output(source=self.source,request_identity="a"*64,configuration=profile.configuration_for(),
            raw_response=response(self.draft,profile.EXTRACTOR),available_at=fixture.AT)
        assessment=quality.assess_brief(self.draft,self.source)
        self.assessment=prepare_assessment(self.row,kind="automatic",evaluator=quality.POLICY,reasoning_effort=None,
            outcome=assessment["status"],assessment=assessment,raw_evidence=dumps_strict(assessment).encode(),available_at=fixture.LATER)
    tearDown=fixture.TranscriptPipelineTests.tearDown

    def test_candidate_exact_predecessor_and_idempotent_migration(self):
        self.assertEqual(structured_transcript_registry_profile(self.registry).source_sha256,
                         "1d541b532e535124ea45d2af991541348319f7d7ced937114c39aaffc2ccdfc2")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(migrate_and_register_store(self.stores,self.registry,"company",applied_at=LATER),
                         tuple(m.id for m in self.registry.migrations if m.store=="company"))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT migration_id FROM schema_migrations ORDER BY ordinal DESC LIMIT 1").fetchone()[0],MIGRATION_ID)

    def test_exact_output_source_lineage_and_zero_write_replay(self):
        with patch("socket.create_connection",side_effect=AssertionError("No network")):
            receipt=self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        self.assertEqual(receipt.written_count,2)
        stored=self.repo.get(self.capture,as_of=PUBLISHED)
        self.assertEqual(stored["output"],self.draft)
        self.assertEqual(json.loads(stored["source_json"]),self.source)
        self.assertEqual(stored["review_status"],"unreviewed")
        before=mutation_fingerprint(self.stores)
        replay=self.pub.publish([self.row],[self.assessment],published_at=LATER)
        self.assertEqual((replay.outcome,replay.written_count,replay.run_id),("unchanged",0,None))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_transcript_analyses").fetchone()[0],0)

    def test_as_of_excludes_future_extraction_and_review(self):
        self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        self.assertIsNone(self.repo.get(self.capture,as_of=fixture.SOURCE_AT))
        self.assertEqual(self.repo.get(self.capture,as_of=fixture.AT)["assessments"],[])
        self.assertEqual(len(self.repo.get(self.capture,as_of=fixture.LATER)["assessments"]),1)

    def test_flagged_drafts_are_stored_without_becoming_accepted_facts(self):
        draft=deepcopy(self.draft);draft["call"]["symbol"]="WRONG"
        row=prepare_output(source=self.source,request_identity="b"*64,configuration=profile.configuration_for(),
            raw_response=response(draft,profile.EXTRACTOR),available_at=fixture.AT)
        assessment=quality.assess_brief(draft,self.source)
        self.assertEqual(assessment["status"],"blocked")
        evaluated=prepare_assessment(row,kind="automatic",evaluator=quality.POLICY,reasoning_effort=None,
            outcome="blocked",assessment=assessment,raw_evidence=dumps_strict(assessment).encode(),available_at=fixture.LATER)
        self.pub.publish([row],[evaluated],published_at=PUBLISHED)
        result=self.repo.get(self.capture,as_of=PUBLISHED)
        self.assertEqual(result["output"]["call"]["symbol"],"WRONG")
        self.assertEqual(result["assessments"][0]["outcome"],"blocked")
        self.assertEqual(result["review_status"],"unreviewed")

    def test_source_tamper_and_identity_conflict_write_nothing(self):
        bad_source=deepcopy(self.source);bad_source["turns"][0]["text"]="tampered"
        bad=prepare_output(source=bad_source,request_identity="b"*64,configuration=profile.configuration_for(),
            raw_response=response(self.draft,profile.EXTRACTOR),available_at=fixture.AT)
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):self.pub.publish([bad],[],published_at=PUBLISHED)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        changed=deepcopy(self.draft);changed["headline"]["message"]="Different content"
        other=prepare_output(source=self.source,request_identity="a"*64,configuration=profile.configuration_for(),
            raw_response=response(changed,profile.EXTRACTOR),available_at=fixture.AT)
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):self.pub.publish([other],[],published_at=LATER)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_failed_sol_package_stays_failed_and_original_remains_intact(self):
        review={"decision":"revised","brief":deepcopy(self.draft),"draft_findings":[]}
        eval=prepare_assessment(self.row,kind="sol_review",evaluator="gpt-5.6-sol",reasoning_effort="medium",
            outcome="failed",assessment={"review":review,"package_validation":{"status":"failed","reason":"label conflict"}},
            raw_evidence=response(review,profile.REVIEWER),available_at=fixture.LATER)
        self.pub.publish([self.row],[self.assessment,eval],published_at=PUBLISHED)
        stored=self.repo.get(self.capture,as_of=PUBLISHED)
        self.assertEqual(stored["output"],self.draft)
        self.assertEqual([r["outcome"] for r in stored["assessments"] if r["kind"]=="sol_review"],["failed"])
        self.assertEqual(stored["review_status"],"reviewed")

    def test_invalid_parent_time_or_evidence_fails_before_writes(self):
        with self.assertRaises(ValidationError):
            prepare_output(source=self.source,request_identity="a"*64,configuration=profile.configuration_for(),
                raw_response=response(self.draft,profile.EXTRACTOR),available_at="2020-01-01T00:00:00.000000Z")
        before=mutation_fingerprint(self.stores)
        changed=deepcopy(self.assessment);changed["assessment"]["status"]="tampered"
        with self.assertRaises((ConflictError,ValidationError)):
            self.pub.publish([self.row],[changed],published_at=PUBLISHED)
        with self.assertRaises(ValidationError):
            self.pub.publish([self.row],[self.assessment],published_at=fixture.AT)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_immutable_rows_and_transaction_rollback(self):
        with writer_connection(self.stores,"company") as c:
            c.execute("CREATE TRIGGER synthetic_abort BEFORE INSERT ON company_structured_transcript_assessments BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        with self.assertRaises(Exception):self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_structured_transcript_outputs").fetchone()[0],0)
        with writer_connection(self.stores,"company") as c:c.execute("DROP TRIGGER synthetic_abort")
        self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        with writer_connection(self.stores,"company") as c:
            for table in ("company_structured_transcript_outputs","company_structured_transcript_assessments"):
                with self.assertRaises(sqlite3.IntegrityError):c.execute("DELETE FROM "+table)
                with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE "+table+" SET available_at=?", (LATER,))

    def test_frozen_shards_roundtrip_and_tamper_rejection(self):
        from quant_data.operations.transcript_structured_import import freeze_batch,thaw_batch
        from quant_data.company.transcript_structured_store import validate_batch
        batch={"contract":"retained_structured_transcript_import.v1","history_plan_id":"f"*64,
               "outputs":[self.row],"assessments":[self.assessment],
               "semantic_identity":validate_batch([self.row],[self.assessment])}
        root=Path(self.temp.name)/"frozen"
        manifest=freeze_batch(root,batch)
        self.assertEqual(thaw_batch(root),batch)
        key=manifest["outputs"][0];p=root/"outputs"/(key+".json")
        row=json.loads(p.read_text());row["output"]["headline"]["message"]="tampered"
        p.write_text(dumps_strict(row))
        with self.assertRaises(ConflictError):thaw_batch(root)

    def test_assessment_append_preserves_original_and_previous_status(self):
        self.pub.publish([self.row],[self.assessment],published_at=PUBLISHED)
        review={"decision":"approved","brief":self.draft}
        evaluated=prepare_assessment(self.row,kind="sol_review",evaluator="gpt-5.6-sol",
            reasoning_effort="medium",outcome="passed",
            assessment={"review":review,"package_validation":{"status":"passed"}},
            raw_evidence=response(review,profile.REVIEWER),available_at=LATER)
        receipt=self.pub.publish([self.row],[self.assessment,evaluated],published_at=LATER)
        self.assertEqual(receipt.written_count,1)
        self.assertEqual(self.repo.get(self.capture,as_of=PUBLISHED)["review_status"],"unreviewed")
        now=self.repo.get(self.capture,as_of=LATER)
        self.assertEqual(now["output"],self.draft)
        self.assertEqual(len(now["assessments"]),2)
