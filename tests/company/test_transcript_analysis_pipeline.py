from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher, RawPage
from quant_data.company.transcript_analysis import (TranscriptAnalysisPublisher, TranscriptAnalysisRepository,
    read_source, digest, _response)
from quant_data.company.transcript_analysis_model import (configuration_for, request_identity_for,
    make_request, reservation_usd, MAX_REQUEST_BYTES, MAX_OUTPUT_TOKENS, OpenAITranscriptTransport)
from quant_data.company.transcript_analysis_registry import TABLES
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all, migrate_store
from quant_data.operations.transcript_analysis import TranscriptAnalysisJob, ready, apply_schema
from quant_data.registry import load_registry, transcript_analysis_registry_profile, collection_universe_registry_profile
from quant_data.stores import StoreMap, quiet_immutable_read_connection, writer_connection
from quant_data.tool_platform.generate import generated_bytes
from quant_data.fingerprint import mutation_fingerprint

ROOT = Path(__file__).resolve().parents[2]
SOURCE_AT = "2026-09-07T00:00:00.000000Z"
AT = "2026-09-08T00:00:00.000000Z"
LATER = "2026-09-08T00:01:00.000000Z"


def response(output, *, review=False, status="completed"):
    return dumps_strict({"id":"resp_fixture","model":"gpt-5.6-sol" if review else "gpt-5.6-terra",
        "status":status,"error":None,"incomplete_details":None,
        "output":[{"type":"message","role":"assistant","status":"completed",
                   "content":[{"type":"output_text","text":dumps_strict(output)}]}],
        "usage":{"input_tokens":100,"output_tokens":500,"total_tokens":600}}).encode()


class Transport:
    def __init__(self, values): self.values=list(values);self.calls=[]
    def request(self, raw):
        self.calls.append(json.loads(raw))
        if not self.values: raise AssertionError("Unexpected paid request")
        value=self.values.pop(0)
        if isinstance(value,BaseException): raise value
        return value


class TranscriptPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/tmp")
        root=Path(self.temp.name)
        self.stores=StoreMap.four_explicit(**{r:root/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        self.example=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V3.example.json").read_text())
        self.event={"id":"call_AAPL_2026_1","eventType":"EarningsCall","fiscalYear":2026,"fiscalQuarter":1,"hasTranscript":True}
        rows=[{"speakerName":{"m1":"Example CFO","a1":"Analyst One","a2":"Analyst Two"}[t["speaker_id"]],
               "speakerRole":"CFO" if t["speaker_role"]=="management" else "Analyst","text":t["text"]} for t in self.example["input"]["turns"]]
        self.raw=dumps_strict({"ticker":"AAPL","eventId":self.event["id"],"fiscalYear":2026,"fiscalQuarter":1,
            "eventTitle":"Fixture earnings call","callDate":"2026-02-01","offset":0,"turnCount":len(rows),
            "totalTurnCount":len(rows),"hasMore":False,"data":rows}).encode()
        EquiblesTranscriptPublisher(self.stores,self.registry).publish(symbol="AAPL",instrument_id="frozen-fixture-AAPL",event=self.event,
            pages=(RawPage(self.raw,SOURCE_AT,"equibles-transcripts/blobs/"+hashlib.sha256(self.raw).hexdigest()+".json"),))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.capture=c.execute("SELECT capture_id FROM company_equibles_transcripts").fetchone()[0]
        self.source=read_source(self.stores,self.capture)
        self.output=copy.deepcopy(self.example["model_output"])
        self.output["management_tone"]["by_speaker"][0]["speaker_id"]=self.source["turns"][0]["speaker_id"]
        self.publisher=TranscriptAnalysisPublisher(self.stores,self.registry)
        self.repository=TranscriptAnalysisRepository(self.stores)
        self.job=TranscriptAnalysisJob(self.stores,self.registry,root/"state",clock=lambda:AT)
        self.review={"schema_version":"transcript.review.v3","verdict":"accepted","summary":"Synthetic example agrees with the source.","findings":[]}

    def tearDown(self): self.temp.cleanup()

    def publish(self, output=None):
        req=request_identity_for(self.source)
        receipt=self.publisher.publish(source=self.source,request_identity=req,configuration=configuration_for(),
            raw_response=response(self.output if output is None else output),started_at=AT,completed_at=AT)
        return receipt,self.repository.find_request(req)

    def test_source_raw_text_roles_pages_and_stable_fingerprint(self):
        self.assertEqual(self.source,read_source(self.stores,self.capture))
        self.assertEqual([t["text"] for t in self.source["turns"]],[t["text"] for t in self.example["input"]["turns"]])
        self.assertEqual(self.source["turns"][0]["speaker_role"],"management")
        self.assertEqual(self.source["turns"][0]["section"],"prepared_remarks")
        self.assertEqual(self.source["turns"][1]["section"],"qa")
        self.assertEqual(self.source["turns"][0]["json_pointer"],"/data/0/text")
        self.assertEqual(self.source["source_available_at"],SOURCE_AT)

    def test_all_three_outputs_publish_with_lineage_and_zero_write_replay(self):
        receipt,row=self.publish()
        self.assertEqual(receipt.outcome,"succeeded")
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual([c.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in TABLES],[1,1,2,1,4,0])
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(c.execute("SELECT raw_body FROM company_equibles_transcript_pages").fetchone()[0],self.raw)
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish()[0].outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        result=self.repository.get(row["analysis_id"],as_of=AT)
        self.assertIn("analyst_focus",result["output"])
        self.assertTrue(all(e["capture_id"]==self.capture for e in result["evidence"]))

    def test_analysis_and_review_cutoffs_are_separate(self):
        _,row=self.publish()
        self.assertIsNone(self.repository.get(row["analysis_id"],as_of=SOURCE_AT))
        req=request_identity_for(self.source,review_analysis_id=row["analysis_id"])
        kwargs=dict(source=self.source,request_identity=req,configuration=configuration_for(review=True),raw_response=response(self.review,review=True),
            started_at=LATER,completed_at=LATER,analysis_id=row["analysis_id"])
        self.publisher.publish(**kwargs)
        self.assertEqual(self.repository.get(row["analysis_id"],as_of=AT)["reviews"],[])
        result=self.repository.get(row["analysis_id"],as_of=LATER)
        self.assertEqual(result["reviews"][0]["verdict"],"accepted")
        self.assertEqual(result["reviews"][0]["model"],"gpt-5.6-sol")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish(**kwargs).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_invalid_source_config_time_and_response_do_not_publish(self):
        base=dict(source=self.source,request_identity=request_identity_for(self.source),configuration=configuration_for(),
            raw_response=response(self.output),started_at=AT,completed_at=AT)
        variants=[{**base,"source":{**self.source,"symbol":"MSFT"}},
            {**base,"configuration":{**configuration_for(),"model":"gpt-6-astra"}},
            {**base,"started_at":"2020-01-01T00:00:00Z"},
            {**base,"raw_response":response(self.output,status="incomplete")},
            {**base,"request_identity":"0"*64}]
        before=mutation_fingerprint(self.stores)
        for kwargs in variants:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValidationError): self.publisher.publish(**kwargs)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_invalid_evidence_reference_failure_is_atomic(self):
        bad=copy.deepcopy(self.output);bad["guidance_claims"][0]["evidence"][0]["turn_id"]="missing-turn"
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError): self.publish(bad)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_different_output_cannot_overwrite_same_request(self):
        self.publish()
        other=copy.deepcopy(self.output);other["coverage_notes"]=["Additional note."]
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError): self.publish(other)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_every_analysis_table_is_append_only(self):
        _,row=self.publish()
        self.publisher.publish(source=self.source,request_identity=request_identity_for(self.source,review_analysis_id=row["analysis_id"]),
            configuration=configuration_for(review=True),raw_response=response(self.review,review=True),
            started_at=AT,completed_at=AT,analysis_id=row["analysis_id"])
        with writer_connection(self.stores,"company") as c:
            for table in TABLES:
                with self.assertRaises(sqlite3.IntegrityError): c.execute("DELETE FROM "+table)

    def test_exact_registry_predecessor_and_migration_rerun(self):
        prior=transcript_analysis_registry_profile(self.registry)
        self.assertEqual(prior.registry_version,"2.74.0")
        self.assertEqual(prior.source_sha256,"174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05")
        self.assertEqual(generated_bytes(ROOT)[0],(ROOT/"config/system_registry.json").read_bytes())
        before=mutation_fingerprint(self.stores)
        migrate_store(self.stores,self.registry,"company",applied_at=AT)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_prepare_uses_no_credentials_or_models_and_enforces_budget(self):
        with patch("quant_data.operations.transcript_analysis.read_project_credential",side_effect=AssertionError("No credential read")):
            before=mutation_fingerprint(self.stores)
            plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="3")
            self.assertEqual(plan["max_requests"],2)
            self.assertEqual(plan["model_requests_made"],0)
            self.assertEqual(before,mutation_fingerprint(self.stores))
            with self.assertRaises(ResourceLimitError): self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="0.01")

    def test_pilot_executes_terra_then_sol_and_second_run_uses_no_requests(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="3")
        transport=Transport([response(self.output),response(self.review,review=True)])
        report=self.job.execute(plan["plan_id"],transport)
        self.assertEqual(report["requests_this_run"],2)
        self.assertEqual([r["model"] for r in transport.calls],["gpt-5.6-terra","gpt-5.6-sol"])
        self.assertTrue(all(r["reasoning"]["effort"]=="high" and r["store"] is False and r["tools"]==[] for r in transport.calls))
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.job.execute(plan["plan_id"],Transport([]))["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_uncertain_attempt_is_reserved_and_never_retried(self):
        class PowerLoss(BaseException): pass
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        transport=Transport([PowerLoss()])
        with self.assertRaises(PowerLoss): self.job.execute(plan["plan_id"],transport)
        retry=Transport([])
        with self.assertRaises(ConflictError): self.job.execute(plan["plan_id"],retry)
        self.assertEqual(retry.calls,[])
        budget=json.loads((self.job.root/"budgets"/(plan["plan_id"]+".json")).read_text())
        self.assertEqual(list(budget.values()),[str(reservation_usd())])

    def test_received_response_recovers_after_publication_failure_without_network(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        with patch.object(self.job.publisher,"publish",side_effect=OSError("local failure")):
            with self.assertRaises(OSError): self.job.execute(plan["plan_id"],Transport([response(self.output)]))
        result=self.job.execute(plan["plan_id"],Transport([]))
        self.assertEqual(result["requests_this_run"],0)
        self.assertEqual(result["outcome"],"complete")

    def test_truncated_output_is_retained_but_not_published_or_retried(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        with self.assertRaises(ValidationError): self.job.execute(plan["plan_id"],Transport([response(self.output,status="incomplete")]))
        retry=Transport([])
        with self.assertRaises(ValidationError): self.job.execute(plan["plan_id"],retry)
        self.assertEqual(retry.calls,[])
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_transcript_analyses").fetchone()[0],0)

    def test_input_bound_rejects_instead_of_truncating(self):
        source=copy.deepcopy(self.source);source["turns"][0]["text"]="x"*MAX_REQUEST_BYTES
        with self.assertRaises(ResourceLimitError): make_request(source)

    def test_plan_path_and_configuration_tampering_are_rejected(self):
        with self.assertRaises(ValidationError): self.job._plan("../../.env")
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        path=self.job.root/"plans"/(plan["plan_id"]+".json")
        value=json.loads(path.read_text());value["max_requests"]=200;path.write_text(json.dumps(value))
        with self.assertRaises(ValidationError): self.job.execute(plan["plan_id"],Transport([]))

    def test_no_plaintext_credential_in_provider_failure_receipt(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        with self.assertRaises(RuntimeError): self.job.execute(plan["plan_id"],Transport([RuntimeError("SECRET-KEY")]))
        for path in self.job.root.rglob("*.json"):
            self.assertNotIn("SECRET-KEY",path.read_text())

    def test_company_only_activation_registers_dataset_and_replays_without_writes(self):
        root=Path(self.temp.name)/"prior"
        stores=StoreMap.four_explicit(**{r:root/(r+".sqlite") for r in ("market","macro","company","news")})
        # This activation command owns the historical transcript migration only.
        # Later company migrations need their own explicitly scoped activation.
        target=collection_universe_registry_profile(self.registry)
        self.assertEqual(target.registry_version,"2.75.0")
        prior=transcript_analysis_registry_profile(target)
        initialize_all(stores,prior)
        with self.assertRaises(ValidationError):ready(stores,target)
        before={r:stores.path(r).read_bytes() for r in ("market","macro","news")}
        self.assertEqual(apply_schema(stores,target,applied_at=AT)["outcome"],"ready")
        self.assertEqual(before,{r:stores.path(r).read_bytes() for r in before})
        current=mutation_fingerprint(stores)
        apply_schema(stores,target,applied_at=LATER)
        self.assertEqual(current,mutation_fingerprint(stores))
        with self.assertRaises(ValidationError):
            apply_schema(stores,self.registry,applied_at=LATER)
        self.assertEqual(current,mutation_fingerprint(stores))

    def test_ready_checks_ledger_semantics_before_network(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        from quant_data.operations.transcript_analysis import _stored_migrations
        with quiet_immutable_read_connection(self.stores,"company") as c:
            rows=_stored_migrations(c)
        rows[-1]=(*rows[-1][:3],"wrong",*rows[-1][4:])
        transport=Transport([])
        with patch("quant_data.operations.transcript_analysis._stored_migrations",return_value=rows):
            with self.assertRaises(ValidationError):self.job.execute(plan["plan_id"],transport)
        self.assertEqual(transport.calls,[])

    def test_new_plan_does_not_retry_a_failed_request(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        with self.assertRaises(RuntimeError):self.job.execute(plan["plan_id"],Transport([RuntimeError("failure")]))
        self.job.clock=lambda:LATER
        new=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        self.assertNotEqual(new["plan_id"],plan["plan_id"])
        transport=Transport([])
        with self.assertRaises(ConflictError):self.job.execute(new["plan_id"],transport)
        self.assertEqual(transport.calls,[])


    def test_response_recovers_after_ingestion_starts_and_records_failure(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        with patch("quant_data.ingestion._insert_control_write",side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.job.execute(plan["plan_id"],Transport([response(self.output)]))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_transcript_analyses").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_run_failures WHERE dataset_id='company.transcript.analysis'").fetchone()[0],1)
        transport=Transport([])
        report=self.job.execute(plan["plan_id"],transport)
        self.assertEqual(report["requests_this_run"],0)
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_transcript_analyses").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_runs WHERE dataset_id='company.transcript.analysis'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_run_failures WHERE dataset_id='company.transcript.analysis'").fetchone()[0],1)
        before=mutation_fingerprint(self.stores)
        self.job.execute(plan["plan_id"],Transport([]))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def source_variant(self, value):
        raw=dumps_strict(value).encode()
        EquiblesTranscriptPublisher(self.stores,self.registry).publish(
            symbol="AAPL",instrument_id="frozen-fixture-AAPL",event=self.event,
            pages=(RawPage(raw,SOURCE_AT,"equibles-transcripts/blobs/"+hashlib.sha256(raw).hexdigest()+".json"),))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            capture=c.execute("SELECT capture_id FROM company_equibles_transcripts WHERE capture_id<>?",(self.capture,)).fetchone()[0]
        return read_source(self.stores,capture)

    def test_placeholder_analysts_remain_unresolved_and_reader_exposes_warning(self):
        value=json.loads(self.raw)
        value["data"][1]["speakerName"]="Unknown Analyst"
        value["data"][3]["speakerName"]="Unidentified Analyst #2"
        self.source=self.source_variant(value)
        self.assertIsNone(self.source["turns"][1]["speaker_id"])
        self.assertIsNone(self.source["turns"][3]["speaker_id"])
        _,row=self.publish()
        result=self.repository.get(row["analysis_id"],as_of=AT)
        self.assertIn("unresolved_speaker_identity",result["source_warnings"])
        self.assertEqual(result["derived"]["distinct_identified_analyst_count"],0)
        self.assertEqual(result["derived"]["unresolved_analyst_question_block_count"],2)

    def test_complete_text_processing_preserves_unknown_role_warning(self):
        value=json.loads(self.raw)
        value["data"].append({"speakerName":"Fixture unresolved role","speakerRole":None,"text":"Can you clarify demand?"})
        value["turnCount"]+=1;value["totalTurnCount"]+=1
        self.source=self.source_variant(value)
        _,row=self.publish()
        result=self.repository.get(row["analysis_id"],as_of=AT)
        self.assertEqual(result["derived"]["processing_coverage"],"complete")
        self.assertIn("unresolved_speaker_role_or_section",result["source_warnings"])


    def test_missing_credential_does_not_record_an_unsent_attempt(self):
        class CredentialsTransport(Transport):
            configured=False
            def prepare_credentials(self):
                if not self.configured:raise ValidationError("Fixture credential unavailable")
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=False,max_usd="1")
        transport=CredentialsTransport([response(self.output)])
        with self.assertRaises(ValidationError):self.job.execute(plan["plan_id"],transport)
        self.assertEqual(transport.calls,[])
        self.assertEqual(list((self.job.root/"requests").iterdir()),[])
        self.assertEqual(list((self.job.root/"budgets").iterdir()),[])
        transport.configured=True
        self.assertEqual(self.job.execute(plan["plan_id"],transport)["requests_this_run"],1)
        transport.configured=False
        self.assertEqual(self.job.execute(plan["plan_id"],transport)["requests_this_run"],0)

    def test_review_failure_report_retains_the_completed_analysis_identifier(self):
        plan=self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,max_usd="3")
        transport=Transport([response(self.output),RuntimeError("fixture review unavailable")])
        with self.assertRaises(RuntimeError):self.job.execute(plan["plan_id"],transport)
        report=json.loads((self.job.root/"reports"/(plan["plan_id"]+".json")).read_text())
        self.assertEqual(report["requests_this_run"],2)
        self.assertEqual(len(report["results"]),1)
        self.assertEqual(report["results"][0]["analysis_outcome"],"published")
        self.assertIsNotNone(self.repository.get(report["results"][0]["analysis_id"],as_of=AT))


if __name__=="__main__": unittest.main()
