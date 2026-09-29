"""Offline temporary-store coverage for the full-universe extraction/publication runner."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
import threading
from unittest.mock import patch
from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher,RawPage
from quant_data.company import transcript_structured_call_terra_sol as profile
from quant_data.errors import ConflictError,ResourceLimitError,ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.equibles_transcript_backfill import atomic
from quant_data.operations.transcript_universe_batch import TranscriptUniverseBatch,select_pending,load
from quant_data.stores import quiet_immutable_read_connection
from tests.company import test_transcript_analysis_pipeline as fixture
from tests.company.test_transcript_structured_call import example
from tests.company.test_transcript_analysis_pair import response

class UniverseBatchTests(unittest.TestCase):
    setUp=fixture.TranscriptPipelineTests.setUp
    tearDown=fixture.TranscriptPipelineTests.tearDown

    def batch(self):
        root=Path(self.temp.name)
        return TranscriptUniverseBatch(self.stores,self.registry,root/"universe",root/"pairs",
                                       clock=lambda:"2026-09-13T15:00:00.000000Z",extraction_profile=profile)

    def add(self,symbol="AAPL",year=2026,quarter=2):
        event={**self.event,"id":f"call_{symbol}_{year}_{quarter}","fiscalYear":year,"fiscalQuarter":quarter}
        raw=json.loads(self.raw);raw.update(ticker=symbol,eventId=event["id"],fiscalYear=year,fiscalQuarter=quarter)
        body=dumps_strict(raw).encode()
        EquiblesTranscriptPublisher(self.stores,self.registry).publish(symbol=symbol,instrument_id="frozen-fixture-"+symbol,event=event,
            pages=(RawPage(body,fixture.SOURCE_AT,"equibles-transcripts/blobs/"+hashlib.sha256(body).hexdigest()+".json"),))

    def transport(self,calls,mode=None):
        class Auto:
            backend=profile.BACKEND
            def _deadline_seconds(self):return 1200
            def prepare_credentials(self):pass
            def request(self,raw):
                req=json.loads(raw);source=json.loads(req["input"][0]["content"][0]["text"])["source"]
                calls.append((source["capture_id"],req["model"],req["reasoning"]["effort"]))
                if mode=="fail":raise ResourceLimitError("Synthetic transport failure")
                value=example(source)
                if mode=="malformed" and source["fiscal_quarter"]==2:value.pop("headline")
                if mode=="flagged":value["call"]["symbol"]="WRONG"
                return response(value,profile.EXTRACTOR)
        return Auto

    def forbidden(self):
        raise AssertionError("No extra model request or credential access")

    def test_full_saved_selection_has_no_ten_ticker_limit_and_excludes_stored(self):
        for symbol in ("AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK"):
            self.add(symbol)
        job=self.batch()
        with patch("quant_data.operations.transcript_universe_batch.PAGE_SIZE",3):
            plan=job.prepare(concurrency=1)
            entries=job.entries(plan)
            self.assertEqual((plan["ticker_count"],len(entries)),(12,12))
        self.assertEqual(plan["max_model_calls"],12)
        self.assertEqual(plan["max_review_calls"],0)
        calls=[]
        one=job.prepare(rows=[entries[0]],concurrency=1)
        result=job.execute(one["plan_id"],self.transport(calls))
        self.assertEqual(result["canonical_outputs_stored"],1)
        with quiet_immutable_read_connection(self.stores,"company") as c:
            pending,done,total=select_pending(c)
        self.assertEqual((len(pending),done,total),(11,1,12))

    def test_each_result_publishes_and_replay_has_zero_calls_and_zero_db_changes(self):
        self.add();self.add(quarter=3)
        job=self.batch();plan=job.prepare(concurrency=3);calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual((result["status"],result["canonical_outputs_stored"],result["model_calls"]),("completed",3,3))
        self.assertTrue(all(c[1:]==("gpt-5.6-terra","high") for c in calls))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_structured_transcript_outputs").fetchone()[0],3)
            self.assertEqual(c.execute("SELECT count(*) FROM company_structured_transcript_assessments").fetchone()[0],3)
        before=mutation_fingerprint(self.stores)
        again=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual(again["model_calls"],3)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_malformed_output_does_not_stop_later_quarters(self):
        self.add();self.add(quarter=3)
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls,"malformed"))
        self.assertEqual(len(calls),3)
        self.assertEqual(result["status"],"completed_with_failures")
        self.assertEqual(result["outcomes"],{"stored":2,"output_failed":1})
        self.assertEqual(len(list((job.pair_root/"responses").glob("*.json"))),3)

    def test_schema_shaped_flagged_drafts_are_stored_with_their_flags(self):
        job=self.batch();plan=job.prepare(concurrency=1)
        result=job.execute(plan["plan_id"],self.transport([],"flagged"))
        self.assertEqual(result["quality"],{"blocked":1})
        self.assertEqual(result["canonical_outputs_stored"],1)

    def test_three_transport_failures_pause_unattempted_work_without_retries(self):
        for year,q in ((2026,2),(2026,3),(2026,4),(2025,1)):self.add(year=year,quarter=q)
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        result=job.execute(plan["plan_id"],self.transport(calls,"fail"))
        self.assertEqual((len(calls),result["remaining"],result["model_calls"]),(3,2,3))
        self.assertEqual(result["status"],"paused_after_transport_failures")
        previous={c[0] for c in calls}
        resumed=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(len(calls),5)
        self.assertTrue(previous.isdisjoint({c[0] for c in calls[3:]}))
        self.assertEqual(resumed["outcomes"],{"model_failed":3,"stored":2})

    def test_publication_failure_recovers_saved_response_without_model_repeat(self):
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        with patch.object(job.publisher,"publish",side_effect=RuntimeError("Synthetic DB interruption")):
            first=job.execute(plan["plan_id"],self.transport(calls))
        self.assertEqual(first["status"],"paused_publication")
        second=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual((second["status"],second["canonical_outputs_stored"],len(calls)),("completed",1,1))
        self.assertEqual(second["model_calls"],1)

    def test_crash_after_commit_replays_prepared_publication_without_new_run(self):
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        job.execute(plan["plan_id"],self.transport(calls))
        entry=job.entries(plan)[0]
        job.record_path(plan["plan_id"],entry["capture_id"]).unlink()
        before=mutation_fingerprint(self.stores)
        result=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual(result["canonical_outputs_stored"],1)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.assertEqual(len(calls),1)

    def test_prepared_response_tampering_is_blocked_before_publication(self):
        job=self.batch();plan=job.prepare(concurrency=1)
        with patch.object(job.publisher,"publish",side_effect=RuntimeError("Synthetic interruption")):
            job.execute(plan["plan_id"],self.transport([]))
        entry=job.entries(plan)[0]
        path=job.root/"runs"/plan["plan_id"]/"prepared"/(entry["capture_id"]+".json")
        value=load(path);value["output"]["raw_response"]=value["output"]["raw_response"].replace("completed","forged")
        atomic(path,value,replace=True)
        result=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual(result["status"],"paused_input")
        self.assertEqual(result["canonical_outputs_stored"],0)

    def test_frozen_source_tampering_stops_before_any_model_call(self):
        job=self.batch();plan=job.prepare(concurrency=1);entry=job.entries(plan)[0]
        path=job.root/"sources"/(entry["source_sha256"]+".json")
        source=load(path);source["turns"][0]["text"]="changed";atomic(path,source,replace=True)
        result=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual((result["status"],result["model_calls"]),("paused_input",0))

    def test_source_preflight_failure_does_not_discard_valid_quarters(self):
        self.add()
        job=self.batch()
        from quant_data.company.transcript_analysis import read_source
        def reader(stores,capture):
            if capture==self.capture:raise ResourceLimitError("Synthetic incomplete source")
            return read_source(stores,capture)
        with patch("quant_data.operations.transcript_universe_batch.read_source",side_effect=reader):
            plan=job.prepare(concurrency=1)
        self.assertEqual(plan["max_model_calls"],1)
        result=job.execute(plan["plan_id"],self.transport([]))
        self.assertEqual(result["outcomes"],{"preflight_failed":1,"stored":1})


    def test_runtime_concurrency_bounds_rejected_before_any_store_or_model_access(self):
        job=self.batch()
        with patch.object(job,"plan",side_effect=AssertionError("Invalid override must fail before plan access")):
            for value in (0,101,-1,True,1.5,"100"):
                with self.subTest(value=value),self.assertRaises(ValidationError):
                    job.execute("a"*64,self.forbidden,concurrency=value)

    def test_hundred_concurrent_workers_preserve_frozen_plan_and_replay(self):
        for year in range(1920,2019):self.add(year=year,quarter=1)
        job=self.batch();plan=job.prepare(concurrency=3)
        original=load(job.root/"plans"/(plan["plan_id"]+".json"))
        calls=[];barrier=threading.Barrier(100);mutex=threading.Lock()
        active=0;peak=0
        base=self.transport(calls)
        class Hundred(base):
            def request(self,raw):
                nonlocal active,peak
                with mutex:active+=1;peak=max(peak,active)
                try:
                    barrier.wait(timeout=30)
                    return super().request(raw)
                finally:
                    with mutex:active-=1
        progress=[]
        result=job.execute(plan["plan_id"],Hundred,concurrency=100,on_progress=progress.append)
        self.assertEqual((peak,len(calls),len({c[0] for c in calls})),(100,100,100))
        self.assertEqual((result["canonical_outputs_stored"],result["model_calls"],result["effective_concurrency"]),(100,100,100))
        self.assertEqual(result["configured_concurrency"],3)
        self.assertEqual(max(p["in_flight"] for p in progress),100)
        self.assertTrue(all(p["in_flight"]<=100 for p in progress))
        self.assertEqual(load(job.root/"plans"/(plan["plan_id"]+".json")),original)
        before=mutation_fingerprint(self.stores)
        replay=job.execute(plan["plan_id"],self.forbidden)
        self.assertEqual((replay["model_calls"],replay["effective_concurrency"]),(100,3))
        fifty=job.execute(plan["plan_id"],self.forbidden,concurrency=50)
        self.assertEqual((fifty["model_calls"],fifty["effective_concurrency"]),(100,50))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_hundred_transport_failures_pause_after_threshold_without_retries(self):
        for year in range(1920,2022):self.add(year=year,quarter=1)
        job=self.batch();plan=job.prepare(concurrency=3);calls=[];barrier=threading.Barrier(100)
        mutex=threading.Lock();arrivals=0
        base=self.transport(calls,"fail")
        class FailingHundred(base):
            def request(self,raw):
                nonlocal arrivals
                with mutex:
                    arrivals+=1
                    first_wave=arrivals<=100
                if first_wave:barrier.wait(timeout=30)
                return super().request(raw)
        result=job.execute(plan["plan_id"],FailingHundred,concurrency=100)
        self.assertEqual(result["status"],"paused_after_transport_failures")
        # Up to two replacement calls may start before the third failure is observed.
        attempted=len(calls)
        self.assertGreaterEqual(attempted,100)
        self.assertLessEqual(attempted,102)
        self.assertEqual(len({c[0] for c in calls}),attempted)
        self.assertEqual((result["remaining"],result["model_calls"],result["in_flight"]),(103-attempted,attempted,0))
        previous={c[0] for c in calls}
        resumed=job.execute(plan["plan_id"],self.transport(calls),concurrency=100)
        self.assertEqual((len(calls),len({c[0] for c in calls})),(103,103))
        self.assertTrue(previous.isdisjoint({c[0] for c in calls[attempted:]}))
        self.assertEqual(resumed["outcomes"],{"model_failed":attempted,"stored":103-attempted})

    def test_retry_policy_recovers_transient_failure_and_counts_every_attempt(self):
        from quant_data.company.transcript_analysis_codex import CodexTranscriptTransport
        from quant_data.company.transcript_analysis_retry import POLICY, RetryingCodexTransport
        from tests.company.test_transcript_analysis_codex import events
        self.add();self.add(quarter=3)
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        def factory():
            t=CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"native",environment={},timeout_seconds=1200)
            t._ready=True
            return t
        def run(transport,command,directory,prompt,run_root):
            source=json.loads(prompt)["source"];calls.append(source["capture_id"])
            if len(calls)==1:
                atomic(run_root/"finished.json",{"exit_code":1,"failure":None})
                atomic(run_root/"stdout.jsonl",b'{"type":"error","message":"Selected model is at capacity."}\n')
                raise ValidationError("native transient failure")
            raw=events(example(source))
            atomic(run_root/"finished.json",{"exit_code":0,"failure":None})
            atomic(run_root/"stdout.jsonl",raw)
            return raw,b""
        original=RetryingCodexTransport.__init__
        def init(wrapper,transport,budget):
            original(wrapper,transport,budget,sleep=lambda _:None,jitter=lambda:0)
        with patch.object(CodexTranscriptTransport,"_run",run), patch.object(RetryingCodexTransport,"__init__",init):
            result=job.execute(plan["plan_id"],factory,retry_policy=POLICY)
        self.assertEqual((result["model_calls"],result["automatic_retries"],len(calls)),(3,1,3))
        self.assertEqual(result["canonical_outputs_stored"],2)
        self.assertEqual(result["status"],"paused_budget")
        receipts=[load(path) for path in (job.pair_root/"requests").glob("*.json")]
        self.assertEqual(sorted(r["model_attempts"] for r in receipts),[1,2])
        self.assertEqual(job.status(plan["plan_id"])["automatic_retries"],1)
        before=mutation_fingerprint(self.stores)
        again=job.execute(plan["plan_id"],self.forbidden,retry_policy=POLICY)
        self.assertEqual((again["model_calls"],again["canonical_outputs_stored"]),(3,2))
        self.assertEqual(mutation_fingerprint(self.stores),before)
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],self.forbidden)

    def test_retry_policy_exact_cap_completes_without_spurious_pause(self):
        from quant_data.company.transcript_analysis_codex import CodexTranscriptTransport
        from quant_data.company.transcript_analysis_retry import POLICY
        from tests.company.test_transcript_analysis_codex import events
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        def factory():
            t=CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"native",environment={},timeout_seconds=1200)
            t._ready=True
            return t
        def run(transport,command,directory,prompt,run_root):
            source=json.loads(prompt)["source"];calls.append(source["capture_id"])
            return events(example(source)),b""
        with patch.object(CodexTranscriptTransport,"_run",run):
            result=job.execute(plan["plan_id"],factory,retry_policy=POLICY)
        self.assertEqual((result["status"],result["canonical_outputs_stored"],result["model_calls"]),("completed",1,1))
        self.assertEqual(result["automatic_retries"],0)
        before=mutation_fingerprint(self.stores)
        replay=job.execute(plan["plan_id"],self.forbidden,retry_policy=POLICY)
        self.assertEqual(replay["status"],"completed")
        self.assertEqual(mutation_fingerprint(self.stores),before)

    def test_retry_policy_rejects_unknown_policy_before_store_access(self):
        job=self.batch()
        with patch.object(job,"plan",side_effect=AssertionError("No store or plan access")):
            with self.assertRaises(ValidationError):
                job.execute("unused",self.forbidden,retry_policy="unbounded")

    def test_invalid_capture_path_is_rejected(self):
        with self.assertRaises(ConflictError):
            self.batch().record_path("a"*64,"../../outside")


    def test_explicit_retry_plan_has_finite_three_attempt_budget(self):
        from quant_data.company.transcript_analysis_retry import POLICY
        job=self.batch();plan=job.prepare(concurrency=1,retry_policy=POLICY)
        self.assertEqual((plan["automatic_retries"],plan["max_model_calls"]),(2,3))
        self.assertEqual(job.plan(plan["plan_id"])["retry_policy"],POLICY)
        self.assertEqual(len(job.entries(plan)),1)
        with self.assertRaises(ConflictError):
            job.execute(plan["plan_id"],self.forbidden)

    def test_retained_publication_recovers_after_model_budget_is_exhausted(self):
        from quant_data.company.transcript_analysis_codex import CodexTranscriptTransport
        from quant_data.company.transcript_analysis_retry import POLICY
        from tests.company.test_transcript_analysis_codex import events
        job=self.batch();plan=job.prepare(concurrency=1);calls=[]
        def factory():
            t=CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"native",environment={},timeout_seconds=1200)
            t._ready=True
            return t
        def run(transport,command,directory,prompt,run_root):
            source=json.loads(prompt)["source"];calls.append(source["capture_id"])
            return events(example(source)),b""
        with patch.object(CodexTranscriptTransport,"_run",run), patch.object(
                job.publisher,"publish",side_effect=ConflictError("Timed out acquiring the physical store lock")):
            first=job.execute(plan["plan_id"],factory,retry_policy=POLICY)
        self.assertEqual((first["status"],first["model_calls"]),("paused_publication",1))
        resumed=job.execute(plan["plan_id"],self.forbidden,retry_policy=POLICY)
        self.assertEqual((resumed["status"],resumed["model_calls"],len(calls)),("completed",1,1))

    def test_longer_lock_wait_is_explicit_and_reaches_publisher(self):
        from quant_data.operations import transcript_universe_batch as module
        from quant_data.company import transcript_structured_store as store_module
        root=Path(self.temp.name)
        job=TranscriptUniverseBatch(self.stores,self.registry,root/"longwait",root/"longpairs",
            clock=lambda:"2026-09-13T15:00:00.000000Z",extraction_profile=profile,lock_timeout_seconds=300)
        acquire=module.acquire_write_session
        with patch.object(module,"acquire_write_session",wraps=acquire) as waits:
            plan=job.prepare(concurrency=1)
        self.assertTrue(waits.call_args_list)
        self.assertTrue(all(c.kwargs["timeout_seconds"]==300 for c in waits.call_args_list))
        with patch.object(store_module,"acquire_write_session",wraps=acquire) as waits:
            result=job.execute(plan["plan_id"],self.transport([]))
        self.assertEqual(result["status"],"completed")
        self.assertTrue(waits.call_args_list)
        self.assertTrue(all(c.kwargs["timeout_seconds"]==300 for c in waits.call_args_list))
        for timeout in (0,True,601):
            with self.assertRaises(ValidationError):
                TranscriptUniverseBatch(self.stores,self.registry,root/"invalid",root/"invalid-pair",
                                        lock_timeout_seconds=timeout)

if __name__=="__main__":unittest.main()
