"""Finite versioned extraction of saved transcript history with incremental DB publication."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
from pathlib import Path
import re

from . import transcript_analysis as m
from .equibles_transcript_backfill import atomic, private_directory, read_file, job_lock
from .transcript_analysis_pair_pilot import TranscriptModelPairPilot, ROOT as PAIR_ROOT, CONTRACT as PAIR_CONTRACT, _error
from .transcript_structured_import import exchange
from ..company import transcript_structured_call_terra_sol as legacy_profile
from ..company import transcript_structured_call_luna_astra as profile
from ..company import transcript_structured_quality as quality
from ..company.transcript_analysis import read_source
from ..company.transcript_structured_store import StructuredTranscriptPublisher, prepare_output, prepare_assessment
from ..company.transcript_structured_registry import DATASET, TABLES
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..company.transcript_analysis_retry import (
    POLICY as RETRY_POLICY, AttemptBudget, AttemptBudgetExhausted, RetryingCodexTransport,
)
from ..json_codec import dumps_strict, loads_strict
from ..stores import acquire_write_session, quiet_immutable_read_connection

ROOT = m.STATE_ROOT / "universe-batches"
CONTRACT = "transcript_saved_universe.v1"
MAX_ENTRIES = 100000
MAX_RUNTIME_CONCURRENCY = 100
PAGE_SIZE = 500
HEX = re.compile(r"[a-f0-9]{64}\Z")
CAPTURE = re.compile(r"equibles_transcript_[a-f0-9]{32}\Z")
TERMINAL = {"stored", "already_stored", "preflight_failed", "model_failed", "output_failed"}

def load(path):
    return loads_strict(read_file(Path(path),32*1024*1024).decode(),max_bytes=32*1024*1024)

def quarter(row):
    return row["symbol"],row["fiscal_year"],row["fiscal_quarter"]

def select_pending(connection):
    rows=[dict(r) for r in connection.execute("""
      SELECT capture_id,symbol,fiscal_year,fiscal_quarter,captured_at FROM (
        SELECT capture_id,symbol,fiscal_year,fiscal_quarter,captured_at,
          row_number() OVER(PARTITION BY symbol,fiscal_year,fiscal_quarter
            ORDER BY captured_at DESC,capture_id DESC) AS rank
        FROM company_equibles_transcripts)
      WHERE rank=1 ORDER BY symbol,fiscal_year,fiscal_quarter""")]
    done={tuple(r) for r in connection.execute("""
      SELECT DISTINCT t.symbol,t.fiscal_year,t.fiscal_quarter
      FROM company_structured_transcript_outputs o
      JOIN company_equibles_transcripts t ON t.capture_id=o.capture_id""")}
    return [r for r in rows if quarter(r) not in done],len(done),len(rows)

class TranscriptUniverseBatch:
    def __init__(self,stores,registry,root,pair_root,*,clock=m.now,extraction_profile=profile,lock_timeout_seconds=60):
        if type(lock_timeout_seconds) is not int or not 1 <= lock_timeout_seconds <= 600:
            raise ValidationError("Invalid transcript lock timeout")
        self.lock_timeout_seconds=lock_timeout_seconds
        self.stores,self.registry,self.root,self.pair_root=stores,registry,Path(root),Path(pair_root)
        if extraction_profile not in (legacy_profile,profile):
            raise ValidationError("Unknown universe extraction profile")
        self.profile=extraction_profile
        self.clock=clock
        self.pair=TranscriptModelPairPilot(stores,registry,self.pair_root,clock=clock)
        self.publisher=StructuredTranscriptPublisher(stores,registry,lock_timeout_seconds=lock_timeout_seconds)

    def ready(self):
        with acquire_write_session(self.stores,("company",),timeout_seconds=self.lock_timeout_seconds):
            with quiet_immutable_read_connection(self.stores,"company") as c:
                actual=[tuple(r) for r in c.execute("SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal")]
                expected=[(d.id,d.sha256) for d in self.registry.migrations if d.store=="company"]
                names={r[0] for r in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
                d=next(d for d in self.registry.datasets_for("company") if d.id==DATASET)
                row=c.execute("SELECT store_role,layer,schema_version,relations_json,active FROM dataset_registry WHERE dataset_id=?",(DATASET,)).fetchone()
                identity=c.execute("SELECT identity_sha256 FROM dataset_identity_contracts WHERE dataset_id=?",(DATASET,)).fetchone()
                if (actual!=expected or not set(TABLES)<=names or row is None
                    or tuple(row)!=(d.store,d.layer,d.schema_version,dumps_strict(list(d.relations)),int(d.active))
                    or identity is None or identity[0]!=d.identity_sha256):
                    raise ConflictError("Structured transcript storage is not ready")

    def prepare(self,rows=None,*,already_stored=0,selection_at=None,concurrency=3,on_progress=None,retry_policy=None):
        if retry_policy not in (None,RETRY_POLICY):
            raise ValidationError("Unknown transcript retry policy")
        if type(concurrency) is not int or not 1<=concurrency<=3:
            raise ValidationError("Use one to three model workers")
        self.ready()
        with job_lock(self.root):
            for name in ("plans","pages","sources","runs"):
                private_directory(self.root/name)
            if rows is None:
                with acquire_write_session(self.stores,("company",),timeout_seconds=self.lock_timeout_seconds):
                    with quiet_immutable_read_connection(self.stores,"company") as c:
                        rows,already_stored,_=select_pending(c)
                selection_at=self.clock()
            if (not isinstance(rows,list) or not 1<=len(rows)<=MAX_ENTRIES
                or len({quarter(r) for r in rows})!=len(rows)
                or len({r["capture_id"] for r in rows})!=len(rows)
                or any(not isinstance(r["capture_id"],str) or not CAPTURE.fullmatch(r["capture_id"]) for r in rows)):
                raise ValidationError("Saved-universe selection is empty, duplicated or too large")
            pages=[];page=[];ready_count=0
            for index,row in enumerate(rows,1):
                item={k:row[k] for k in ("capture_id","symbol","fiscal_year","fiscal_quarter","captured_at")}
                try:
                    with acquire_write_session(self.stores,("company",),timeout_seconds=self.lock_timeout_seconds):
                        source=read_source(self.stores,item["capture_id"])
                    if quarter(source)!=quarter(item) or source["source_available_at"]!=item["captured_at"]:
                        raise ConflictError("Selected transcript identity changed")
                    request=self.profile.make_request(source)
                    source_hash=m.digest(source)
                    atomic(self.root/"sources"/(source_hash+".json"),source)
                    request_hash=m.digest(request)
                    identity=m.digest({"contract":PAIR_CONTRACT,"configuration":self.profile.configuration_for(),
                                       "request_sha256":request_hash,"parent_response_sha256":None})
                    item.update(ready=True,source_sha256=source_hash,input_sha256=source["input_sha256"],
                                request_sha256=request_hash,request_identity=identity)
                    ready_count+=1
                except (ValidationError,ResourceLimitError) as exc:
                    item.update(ready=False,**_error(exc))
                page.append(item)
                if len(page)==PAGE_SIZE or index==len(rows):
                    sha=m.digest(page);atomic(self.root/"pages"/(sha+".json"),page)
                    pages.append({"sha256":sha,"count":len(page)});page=[]
                    if on_progress:on_progress({"prepared":index,"total":len(rows),"ready":ready_count})
            plan={"contract":CONTRACT,"created_at":self.clock(),"selection_at":selection_at,
                  "selection":"latest_saved_capture_per_source_labelled_quarter_excluding_already_stored_quarters",
                  "pages":pages,"total_transcripts":len(rows),"ticker_count":len({r["symbol"] for r in rows}),
                  "already_stored_quarters":already_stored,"configuration":self.profile.configuration_for(),
                  "assessment_policy":quality.POLICY,"concurrency":concurrency,"max_model_calls":ready_count,
                  "max_review_calls":0,"automatic_retries":0,"publication":DATASET}
            if retry_policy is not None:
                plan.update(retry_policy=RETRY_POLICY,automatic_retries=2,max_model_calls=ready_count*3)
            identifier=m.digest(plan);atomic(self.root/"plans"/(identifier+".json"),plan)
            return {"plan_id":identifier,**plan}

    def plan(self,identifier):
        if not isinstance(identifier,str) or not HEX.fullmatch(identifier):
            raise ValidationError("Invalid universe plan identifier")
        plan=load(self.root/"plans"/(identifier+".json"))
        if (m.digest(plan)!=identifier or plan["contract"]!=CONTRACT
            or plan["configuration"]!=self.profile.configuration_for() or plan["assessment_policy"]!=quality.POLICY
            or plan["publication"]!=DATASET or plan["max_review_calls"]!=0
            or not ((plan["automatic_retries"]==0 and "retry_policy" not in plan)
                    or (plan["automatic_retries"]==2 and plan.get("retry_policy")==RETRY_POLICY))
            or type(plan["concurrency"]) is not int or not 1<=plan["concurrency"]<=3
            or not 1<=plan["total_transcripts"]<=MAX_ENTRIES):
            raise ConflictError("Frozen universe plan changed")
        return plan

    def entries(self,plan):
        entries=[]
        for item in plan["pages"]:
            sha=item["sha256"]
            if not isinstance(sha,str) or not HEX.fullmatch(sha):
                raise ConflictError("Invalid universe page identity")
            page=load(self.root/"pages"/(sha+".json"))
            if m.digest(page)!=sha or len(page)!=item["count"] or not 1<=len(page)<=PAGE_SIZE:
                raise ConflictError("Frozen universe page changed")
            entries.extend(page)
        if (len(entries)!=plan["total_transcripts"]
            or len({e["capture_id"] for e in entries})!=len(entries)
            or any(not isinstance(e["capture_id"],str) or not CAPTURE.fullmatch(e["capture_id"]) for e in entries)
            or len({quarter(e) for e in entries})!=len(entries)
            or sum(e["ready"] is True for e in entries)*(1+plan["automatic_retries"])!=plan["max_model_calls"]):
            raise ConflictError("Universe membership or call cap changed")
        return entries

    def source(self,entry):
        sha=entry["source_sha256"]
        if not isinstance(sha,str) or not HEX.fullmatch(sha):raise ConflictError("Invalid source identity")
        source=load(self.root/"sources"/(sha+".json"))
        request=self.profile.make_request(source)
        identity=m.digest({"contract":PAIR_CONTRACT,"configuration":self.profile.configuration_for(),
                           "request_sha256":m.digest(request),"parent_response_sha256":None})
        if (m.digest(source)!=sha or source["input_sha256"]!=entry["input_sha256"]
            or source["capture_id"]!=entry["capture_id"] or quarter(source)!=quarter(entry)
            or m.digest(request)!=entry["request_sha256"] or identity!=entry["request_identity"]):
            raise ConflictError("Frozen source or extraction request changed")
        return source

    def existing(self,entry):
        with acquire_write_session(self.stores,("company",),timeout_seconds=self.lock_timeout_seconds):
            with quiet_immutable_read_connection(self.stores,"company") as c:
                row=c.execute("""SELECT o.analysis_id FROM company_structured_transcript_outputs o
                  JOIN company_equibles_transcripts t ON t.capture_id=o.capture_id
                  WHERE t.symbol=? AND t.fiscal_year=? AND t.fiscal_quarter=? LIMIT 1""",quarter(entry)).fetchone()
                return None if row is None else row[0]

    def record_path(self,identifier,capture):
        if not isinstance(capture,str) or not CAPTURE.fullmatch(capture):raise ConflictError("Invalid capture identity")
        return self.root/"runs"/identifier/"records"/(capture+".json")

    def consumed(self,identifier,entry):
        path=self.pair_root/"requests"/(entry["request_identity"]+".json")
        if not path.exists():return 0
        receipt=load(path)
        if receipt.get("plan_id")!=identifier:return 0
        count=receipt.get("model_attempts",1)
        if type(count) is not int or not 0<=count<=3:
            raise ConflictError("Invalid retained model-attempt count")
        return count

    def one(self,identifier,entry,transport_factory):
        directory=self.root/"runs"/identifier
        target=self.record_path(identifier,entry["capture_id"])
        prepared=directory/"prepared"/(entry["capture_id"]+".json")
        record=load(target) if target.exists() else {
            "plan_id":identifier,"capture_id":entry["capture_id"],"symbol":entry["symbol"],
            "entry_sha256":m.digest(entry),"requests_this_run":0,"requests_consumed":0}
        if record.get("plan_id")!=identifier or record.get("entry_sha256")!=m.digest(entry):
            raise ConflictError("Retained item result differs from this plan")
        if record.get("status") in TERMINAL:return record
        record.pop("stop_new_calls",None)
        record.pop("transport_failed",None)
        record.pop("retryable_transport_failure",None)
        phase="input"
        try:
            if not entry["ready"]:
                record.update(status="preflight_failed",reason=entry.get("reason",entry.get("error_type")))
            else:
                source=self.source(entry)
                if prepared.exists():
                    saved=load(prepared)
                    if (saved["output"]["source"]!=source
                        or saved["output"]["request_identity"]!=entry["request_identity"]):
                        raise ConflictError("Prepared publication differs from frozen input")
                    evidence={"extraction":{"request_identity":entry["request_identity"],
                        "raw_response_sha256":m.digest(saved["output"]["raw_response"].encode())}}
                    receipt,raw,draft=exchange(self.pair_root,evidence,stage="extraction",source=source,profile=self.profile)
                    if (raw.decode()!=saved["output"]["raw_response"] or draft!=saved["output"]["output"]
                        or receipt["completed_at"]!=saved["output"]["available_at"]
                        or receipt["configuration"]!=saved["output"]["configuration"]):
                        raise ConflictError("Prepared output differs from original model evidence")
                else:
                    found=self.existing(entry)
                    if found:
                        record.update(status="already_stored",analysis_id=found)
                        atomic(target,record,replace=True);return record
                    phase="model"
                    transport=transport_factory()
                    if transport.backend!=self.profile.BACKEND or transport._deadline_seconds()!=self.profile.REQUEST_TIMEOUT_SECONDS:
                        raise ValidationError("Universe transport differs from the fixed profile")
                    draft,_=self.pair._exchange(self.profile.make_request(source),stage="extraction",
                        parent_response_sha256=None,identifier=identifier,report=record,
                        transport=transport,profile=self.profile)
                    phase="output"
                    receipt,raw,checked=exchange(self.pair_root,record,stage="extraction",source=source,profile=self.profile)
                    if checked!=draft:raise ConflictError("Extracted output differs from saved response")
                    output=prepare_output(source=source,request_identity=receipt["request_identity"],
                        configuration=receipt["configuration"],raw_response=raw,available_at=receipt["completed_at"])
                    assessed=quality.assess_brief(output["output"],source)
                    assessment=prepare_assessment(output,kind="automatic",evaluator=quality.POLICY,
                        reasoning_effort=None,outcome=assessed["status"],assessment=assessed,
                        raw_evidence=dumps_strict(assessed).encode(),available_at=self.clock())
                    saved={"output":output,"assessment":assessment}
                    atomic(prepared,saved)
                phase="publication"
                receipt=self.publisher.publish([saved["output"]],[saved["assessment"]],published_at=self.clock())
                record.update(status="stored",analysis_id=saved["output"]["analysis_id"],
                    output_semantic_identity=saved["output"]["semantic_identity"],
                    assessment_id=saved["assessment"]["assessment_id"],
                    quality_status=saved["assessment"]["outcome"],publication=asdict(receipt))
        except Exception as exc:
            if isinstance(exc,AttemptBudgetExhausted):
                phase="budget"
            status={"input":"input_failed","model":"model_failed","output":"output_failed",
                    "publication":"publication_failed","budget":"budget_exhausted"}[phase]
            record.update(status=status,phase=phase,**_error(exc))
            record["stop_new_calls"]=phase in ("input","publication","budget")
            record["transport_failed"]=phase=="model" and record.get("extraction",{}).get("raw_response_sha256") is None
            if phase=="model":
                record["retryable_transport_failure"]=bool(getattr(locals().get("transport"),"retryable_failure",False))
        if entry["ready"]:record["requests_consumed"]=self.consumed(identifier,entry)
        record["completed_at"]=self.clock()
        atomic(target,record,replace=True)
        return record

    def summary(self,identifier,plan,records,*,status):
        counts=Counter(r["status"] for r in records.values())
        return {"plan_id":identifier,"at":self.clock(),"status":status,
            "total_transcripts":plan["total_transcripts"],"ticker_count":plan["ticker_count"],
            "already_stored_before_run":plan["already_stored_quarters"],"finished":len(records),
            "remaining":plan["total_transcripts"]-len(records),"outcomes":dict(counts),
            "quality":dict(Counter(r["quality_status"] for r in records.values() if r["status"]=="stored")),
            "model_calls":sum(r.get("requests_consumed",0) for r in records.values()),
            "max_model_calls":plan["max_model_calls"],"review_calls":0,"automatic_retries":0,
            "canonical_outputs_stored":counts["stored"],"failed_outputs_retained_privately":counts["output_failed"]}

    def status(self,identifier):
        plan=self.plan(identifier);entries=self.entries(plan);records={}
        for entry in entries:
            path=self.record_path(identifier,entry["capture_id"])
            if path.exists():records[entry["capture_id"]]=load(path)
        path=self.root/"runs"/identifier/"progress.json"
        state=load(path)["status"] if path.exists() else "prepared"
        result=self.summary(identifier,plan,records,status=state)
        budget_path=self.root/"runs"/identifier/"retry-budget.json"
        if budget_path.exists():
            budget=load(budget_path)
            result.update(model_calls=budget["used"],automatic_retries=budget["retries"],
                          retry_policy=RETRY_POLICY,max_attempts_per_transcript=3)
        return result

    def execute(self,identifier,transport_factory,*,on_progress=None,concurrency=None,retry_policy=None):
        if retry_policy not in (None,RETRY_POLICY):
            raise ValidationError("Unknown transcript retry policy")
        # A runtime override preserves the frozen population, request identities and call cap.
        if concurrency is not None and (type(concurrency) is not int or not 1<=concurrency<=MAX_RUNTIME_CONCURRENCY):
            raise ValidationError("Runtime concurrency must be an integer from 1 to 100")
        plan=self.plan(identifier);entries=self.entries(plan)
        if plan["automatic_retries"] and retry_policy!=plan["retry_policy"]:
            raise ConflictError("Use the frozen plan retry policy")
        workers=plan["concurrency"] if concurrency is None else concurrency
        self.ready()
        with job_lock(self.root),job_lock(self.pair_root):
            directory=self.root/"runs"/identifier
            for path in (directory,directory/"records",directory/"prepared"):
                private_directory(path)
            for name in ("requests","inputs","responses","outputs","reports"):
                private_directory(self.pair_root/name)
            records={};pending=[]
            for entry in entries:
                path=self.record_path(identifier,entry["capture_id"])
                if path.exists():
                    record=load(path)
                    if record.get("plan_id")!=identifier or record.get("entry_sha256")!=m.digest(entry):
                        raise ConflictError("Retained result membership changed")
                    records[entry["capture_id"]]=record
                if entry["capture_id"] not in records or records[entry["capture_id"]]["status"] not in TERMINAL:
                    pending.append(entry)
            pending.sort(key=lambda e:not (directory/"prepared"/(e["capture_id"]+".json")).exists())
            budget_path=directory/"retry-budget.json"
            if retry_policy is None and budget_path.exists():
                raise ConflictError("Resume this batch with its retained retry policy")
            budget=None
            if retry_policy is not None:
                budget=AttemptBudget(budget_path,plan_id=identifier,limit=plan["max_model_calls"],
                                     initial_calls=sum(self.consumed(identifier,e) for e in entries))
            consecutive=0;stopped=None;futures={}
            def progress(status):
                value=self.summary(identifier,plan,records,status=status)
                value.update(configured_concurrency=plan["concurrency"],effective_concurrency=workers,in_flight=len(futures))
                if budget is not None:
                    with budget.lock:
                        value.update(model_calls=budget.state["used"],automatic_retries=budget.state["retries"],
                                     retry_policy=RETRY_POLICY,max_attempts_per_transcript=3)
                if value["model_calls"]>plan["max_model_calls"]:raise ConflictError("Model call cap exceeded")
                atomic(directory/"progress.json",value,replace=True)
                if on_progress:on_progress(value)
                return value
            progress("running")
            iterator=iter(pending)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                def one_transport():
                    transport=transport_factory()
                    return transport if budget is None else RetryingCodexTransport(transport,budget)
                def submit():
                    nonlocal stopped
                    entry=next(iterator,None)
                    if entry is None:return
                    retained=(directory/"prepared"/(entry["capture_id"]+".json")).exists()
                    if budget is not None and budget.state["used"]>=plan["max_model_calls"] and not retained:
                        stopped="paused_budget"
                        return
                    futures[pool.submit(self.one,identifier,entry,one_transport)]=entry
                for _ in range(workers):submit()
                progress("running")
                while futures:
                    finished,_=wait(futures,return_when=FIRST_COMPLETED)
                    for future in finished:
                        entry=futures.pop(future)
                        record=future.result();records[entry["capture_id"]]=record
                        if record.get("stop_new_calls"):stopped="paused_"+record.get("phase","input")
                        if record.get("transport_failed"):
                            consecutive+=1
                        elif record["status"] in ("stored","already_stored"):
                            consecutive=0
                        if consecutive>=3:stopped=stopped or "paused_after_transport_failures"
                    if stopped is None:
                        for _ in finished:submit()
                    progress(stopped or "running")
            final=progress(stopped or ("completed_with_failures" if any(r["status"] not in ("stored","already_stored") for r in records.values()) else "completed"))
            atomic(directory/"result.json",final,replace=True)
            return final

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","execute","status"))
    parser.add_argument("--plan-id")
    parser.add_argument("--concurrency",type=int,help="Explicit execution-only worker override, 1 to 100")
    args=parser.parse_args(argv)
    if args.concurrency is not None and args.action!="execute":parser.error("--concurrency applies only to execute")
    registry=m.load_registry(m.PROJECT_ROOT/"config/system_registry.json",project_root=m.PROJECT_ROOT,environment={})
    selected=profile
    if args.action!="prepare":
        config=load(ROOT/"plans"/(args.plan_id+".json"))["configuration"] if isinstance(args.plan_id,str) and HEX.fullmatch(args.plan_id) else None
        selected=next((p for p in (legacy_profile,profile) if p.configuration_for()==config),None)
        if selected is None:raise ValidationError("Unknown frozen universe profile")
    job=TranscriptUniverseBatch(m.fixed_stores(),registry,ROOT,PAIR_ROOT,extraction_profile=selected)
    if args.action=="prepare":
        value=job.prepare(on_progress=lambda v:print(dumps_strict(v),flush=True))
    elif args.action=="status":value=job.status(args.plan_id)
    else:
        from ..company.transcript_analysis_codex import CodexTranscriptTransport
        value=job.execute(args.plan_id,lambda:CodexTranscriptTransport(evidence_root=m.STATE_ROOT/"codex",
            timeout_seconds=selected.REQUEST_TIMEOUT_SECONDS),on_progress=lambda v:print(dumps_strict(v),flush=True),concurrency=args.concurrency)
    print(dumps_strict(value),flush=True)
    return 0 if value.get("status","prepared") in ("prepared","completed","completed_with_failures") else 75

if __name__=="__main__":raise SystemExit(main())
