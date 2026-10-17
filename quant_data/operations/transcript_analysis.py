"""Manual, manifest-bounded transcript analysis. Help and reads never call models."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sys
import time

from ..company.transcript_analysis import (TranscriptAnalysisPublisher, TranscriptAnalysisRepository,
    read_source, select_captures, digest, _response, DATASET)
from ..company.transcript_analysis_model import (OpenAITranscriptTransport, configuration_for,
    make_request, request_identity_for, reservation_usd, usage_usd, PRICE_VERSION,
    MAX_REQUEST_BYTES)
from ..company.transcript_analysis_registry import MIGRATION_ID, TABLES
from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..migrations import migrate_and_register_store
from ..registry import load_registry
from ..stores import StoreMap, quiet_immutable_read_connection
from .equibles_transcript_backfill import atomic, read_file, private_directory, job_lock

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
STATE_ROOT = PROJECT_ROOT / "data/.operations/transcript-analysis"
MAX_CALLS = 20
MAX_RUN_SECONDS = 3600


def now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")


def fixed_stores():
    return StoreMap.from_mapping({r:PROJECT_ROOT/"data"/(r+".sqlite") for r in ("market","macro","company","news")})


def money(value):
    try: result = Decimal(str(value))
    except (InvalidOperation,ValueError): raise ValidationError("USD budget is invalid") from None
    if not result.is_finite() or not Decimal("0") < result <= Decimal("100"):
        raise ValidationError("USD budget must be positive and at most 100")
    return result


def _migration_rows(registry):
    return [(m.id,m.store,m.ordinal,m.resource,m.sha256,m.reconstruction_state)
            for m in registry.migrations if m.store == "company"]


def _stored_migrations(connection):
    return [tuple(r) for r in connection.execute(
        "SELECT migration_id,store_role,ordinal,resource,sha256,reconstruction_state "
        "FROM schema_migrations ORDER BY ordinal")]


def ready(stores, registry):
    declaration = next(d for d in registry.datasets_for("company") if d.id==DATASET)
    with quiet_immutable_read_connection(stores,"company") as c:
        actual = _stored_migrations(c)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        dataset = c.execute(
            "SELECT store_role,layer,schema_version,relations_json,active FROM dataset_registry WHERE dataset_id=?",
            (DATASET,)).fetchone()
        identity = c.execute(
            "SELECT identity_sha256 FROM dataset_identity_contracts WHERE dataset_id=?",(DATASET,)).fetchone()
    expected_dataset = (declaration.store,declaration.layer,declaration.schema_version,
                        dumps_strict(list(declaration.relations)),int(declaration.active))
    if (actual != _migration_rows(registry) or not set(TABLES)<=names or dataset is None
            or tuple(dataset) != expected_dataset or identity is None or identity[0] != declaration.identity_sha256):
        raise ValidationError("Company transcript-analysis migration is not ready; no model request was made")


def apply_schema(stores, registry, *, applied_at):
    expected = _migration_rows(registry)
    with quiet_immutable_read_connection(stores,"company") as c:
        actual = _stored_migrations(c)
    if actual not in (expected,expected[:-1]) or expected[-1][0]!=MIGRATION_ID:
        raise ValidationError("Company predecessor migration ledger differs")
    migrate_and_register_store(stores,registry,"company",applied_at=applied_at)
    ready(stores,registry)
    return {"outcome":"ready","migration_id":MIGRATION_ID,"model_requests_made":0}


class TranscriptAnalysisJob:
    def __init__(self, stores, registry, state_root, *, clock=now):
        self.stores,self.registry,self.root,self.clock = stores,registry,state_root,clock
        self.repository = TranscriptAnalysisRepository(stores)
        self.publisher = TranscriptAnalysisPublisher(stores,registry)

    def prepare(self, *, symbol, limit, review_pilot, max_usd=None, backend="openai_api", offset=0):
        if type(review_pilot) is not bool: raise ValidationError("Pilot review flag is invalid")
        extractor_config = configuration_for(backend=backend)
        version = extractor_config["prompt_version"]
        subscription = backend == "codex_subscription"
        if subscription and max_usd is not None:
            raise ValidationError("Subscription plans use request bounds, not API dollar budgets")
        budget = None if subscription else money(max_usd)
        captures = select_captures(self.stores,symbol=symbol,limit=limit,offset=offset)
        sources = [read_source(self.stores,c) for c in captures]
        for source in sources: make_request(source,prompt_version=version)
        reservation = len(sources)*(reservation_usd(prompt_version=version)+ (reservation_usd(review=True,prompt_version=version) if review_pilot else 0))
        if not subscription and reservation > budget:
            raise ResourceLimitError("Budget is below the conservative reservation: "+str(reservation)+" USD")
        plan = {"contract":"transcript_analysis_plan.v1","created_at":self.clock(),"symbol":symbol,
            "captures":[{"capture_id":s["capture_id"],"input_sha256":s["input_sha256"]} for s in sources],
            "review_pilot":review_pilot,"max_requests":len(sources)*(2 if review_pilot else 1),
            "max_usd":None if subscription else str(budget),
            "reserved_upper_usd":None if subscription else str(reservation),
            "pricing_version":None if subscription else PRICE_VERSION,
            "extractor_configuration":extractor_config,
            "reviewer_configuration":configuration_for(review=True,backend=backend,prompt_version=version) if review_pilot else None}
        if subscription:
            plan.update(contract="transcript_analysis_plan.v2",backend=backend)
        identifier = digest(plan)
        with job_lock(self.root):
            private_directory(self.root/"plans")
            atomic(self.root/"plans"/(identifier+".json"),plan)
        return {"plan_id":identifier,**plan,"model_requests_made":0,"price_note":("Uses shared Codex subscription allowance; no API-key fallback. CLI output tokens are validated after completion, not capped at the provider."
            if subscription else "Conservative estimate at pinned standard rates; hard limits also bound requests and tokens.")}

    def _plan(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r"[a-f0-9]{64}",identifier):
            raise ValidationError("Analysis plan identifier is invalid")
        plan = loads_strict(read_file(self.root/"plans"/(identifier+".json"),65536).decode())
        keys={"contract","created_at","symbol","captures","review_pilot","max_requests","max_usd","reserved_upper_usd","pricing_version","extractor_configuration","reviewer_configuration"}
        if not isinstance(plan,dict):
            raise ValidationError("Analysis plan differs from its immutable identity")
        subscription=plan.get("contract")=="transcript_analysis_plan.v2"
        if subscription: keys.add("backend")
        if (set(plan)!=keys or digest(plan)!=identifier
                or (subscription and plan.get("backend")!="codex_subscription")
                or (not subscription and plan["contract"]!="transcript_analysis_plan.v1")):
            raise ValidationError("Analysis plan differs from its immutable identity")
        backend=plan.get("backend","openai_api")
        captures=plan["captures"]
        if not isinstance(captures,list) or not 1<=len(captures)<=10 or type(plan["review_pilot"]) is not bool:
            raise ValidationError("Analysis plan scope is invalid")
        if (any(not isinstance(x,dict) or set(x)!={"capture_id","input_sha256"}
                or not isinstance(x["capture_id"],str) or not isinstance(x["input_sha256"],str)
                or not re.fullmatch(r"[a-f0-9]{64}",x["input_sha256"]) for x in captures)
                or len({x["capture_id"] for x in captures})!=len(captures)):
            raise ValidationError("Analysis plan repeats a capture")
        if type(plan["max_requests"]) is not int or plan["max_requests"]!=len(captures)*(2 if plan["review_pilot"] else 1) or plan["max_requests"]>MAX_CALLS:
            raise ValidationError("Analysis plan request bound is invalid")
        version = plan["extractor_configuration"].get("prompt_version") if isinstance(plan["extractor_configuration"],dict) else None
        if plan["pricing_version"]!=(None if subscription else PRICE_VERSION) or plan["extractor_configuration"]!=configuration_for(backend=backend,prompt_version=version) or plan["reviewer_configuration"]!=(configuration_for(review=True,backend=backend,prompt_version=version) if plan["review_pilot"] else None):
            raise ValidationError("Analysis plan configuration changed")
        reservation=len(captures)*(reservation_usd(prompt_version=version)+(reservation_usd(review=True,prompt_version=version) if plan["review_pilot"] else 0))
        if subscription:
            if plan["max_usd"] is not None or plan["reserved_upper_usd"] is not None:
                raise ValidationError("Subscription plan cannot claim a dollar reservation")
        elif Decimal(plan["reserved_upper_usd"])!=reservation or money(plan["max_usd"])<reservation:
            raise ValidationError("Analysis plan reservation differs")
        return plan

    def execute(self, identifier, transport):
        ready(self.stores,self.registry)
        started=time.monotonic()
        new_requests=0
        with job_lock(self.root):
            plan=self._plan(identifier)
            backend=plan.get("backend","openai_api")
            subscription=backend=="codex_subscription"
            version=plan["extractor_configuration"]["prompt_version"]
            deadline=plan["extractor_configuration"].get("request_timeout_seconds",180)
            if hasattr(transport,"_deadline_seconds") and transport._deadline_seconds()!=deadline:
                raise ValidationError("Transport deadline differs from the prepared plan")
            if getattr(transport,"backend","openai_api")!=backend:
                raise ValidationError("Transport does not match the approved billing backend")
            for name in ("requests","responses","budgets","reports"):
                private_directory(self.root/name)
            budget_path=self.root/"budgets"/(identifier+".json")
            budget=loads_strict(read_file(budget_path,65536).decode()) if budget_path.exists() else {}
            if not isinstance(budget,dict): raise ValidationError("Analysis budget ledger is invalid")
            permitted_reserves={Decimal("0")} if subscription else {reservation_usd(prompt_version=version),reservation_usd(review=True,prompt_version=version)}
            if any(not re.fullmatch(r"[a-f0-9]{64}",k) or Decimal(v) not in permitted_reserves for k,v in budget.items()):
                raise ValidationError("Analysis budget reservation is invalid")
            results=[]
            def step(source,parent=None):
                nonlocal new_requests
                review=parent is not None
                parent_id=parent["analysis_id"] if parent else None
                req=request_identity_for(source,review_analysis_id=parent_id,backend=backend,prompt_version=version)
                existing=self.repository.find_request(req,review=review)
                if existing is not None: return existing,"cached"
                raw_request=make_request(source,analysis=loads_strict(parent["output_json"]) if parent else None,prompt_version=version)
                receipt_path=self.root/"requests"/(req+".json")
                response_path=self.root/"responses"/(req+".json")
                receipt=loads_strict(read_file(receipt_path,65536).decode()) if receipt_path.exists() else None
                if receipt is not None and (receipt.get("request_identity")!=req or receipt.get("request_sha256")!=digest(raw_request)):
                    raise ConflictError("Retained model request identity differs")
                if receipt is not None and response_path.exists():
                    raw_response=read_file(response_path,4*1024*1024)
                    if receipt.get("response_sha256") and receipt["response_sha256"]!=digest(raw_response):
                        raise ConflictError("Retained model response hash differs")
                    response_at=receipt.get("completed_at") or self.clock()
                elif receipt is not None:
                    raise ConflictError("A prior model attempt is failed or uncertain; no automatic retry")
                else:
                    if time.monotonic()-started>MAX_RUN_SECONDS-deadline:
                        raise ResourceLimitError("Analysis invocation reached its time bound")
                    # Credential-only preflight must precede a durable model attempt.
                    # Cached results and retained responses never need credentials.
                    preflight=getattr(transport,"prepare_credentials",None)
                    if preflight is not None:
                        preflight()
                    reserve=Decimal("0") if subscription else reservation_usd(review=review,prompt_version=version)
                    if req not in budget:
                        if len(budget)>=plan["max_requests"] or (not subscription and sum(Decimal(v) for v in budget.values())+reserve>money(plan["max_usd"])):
                            raise ResourceLimitError("Analysis plan budget is exhausted")
                        budget[req]=str(reserve);atomic(budget_path,budget,replace=True)
                    receipt={"request_identity":req,"request_sha256":digest(raw_request),"plan_id":identifier,
                        "status":"pending","started_at":self.clock(),"reservation_usd":None if subscription else str(reserve)}
                    if subscription:
                        receipt["backend"]=backend
                    atomic(receipt_path,receipt)
                    try:
                        new_requests+=1
                        raw_response=transport.request(raw_request)
                        if not isinstance(raw_response,bytes) or not 1<=len(raw_response)<=4*1024*1024:
                            raise ResourceLimitError("Model response exceeds its byte bound")
                        response_at=self.clock()
                        atomic(response_path,raw_response)
                        receipt.update(status="received",completed_at=response_at,response_sha256=digest(raw_response))
                        atomic(receipt_path,receipt,replace=True)
                    except Exception:
                        receipt.update(status="failed_or_uncertain",completed_at=self.clock())
                        atomic(receipt_path,receipt,replace=True)
                        raise
                output,usage=_response(raw_response,"gpt-5.6-sol" if review else "gpt-5.6-terra")
                if usage["input_tokens"]>MAX_REQUEST_BYTES or usage["output_tokens"]>plan["reviewer_configuration" if review else "extractor_configuration"]["max_output_tokens"]:
                    raise ResourceLimitError("Reported model usage exceeds the reserved token bound")
                self.publisher.publish(source=source,request_identity=req,configuration=configuration_for(review=review,backend=backend,prompt_version=version),
                    raw_response=raw_response,started_at=receipt["started_at"],completed_at=response_at,analysis_id=parent_id)
                receipt.update(status="published",usage=usage,estimated_usage_usd=None if subscription else str(usage_usd(usage,review=review)))
                atomic(receipt_path,receipt,replace=True)
                return self.repository.find_request(req,review=review),"published"
            try:
                for entry in plan["captures"]:
                    source=read_source(self.stores,entry["capture_id"])
                    if source["input_sha256"]!=entry["input_sha256"] or source["symbol"]!=plan["symbol"]:
                        raise ConflictError("Prepared transcript input changed")
                    row,outcome=step(source)
                    item={"capture_id":source["capture_id"],"analysis_id":row["analysis_id"],"analysis_outcome":outcome}
                    results.append(item)
                    if plan["review_pilot"]:
                        reviewed,review_outcome=step(source,row)
                        item.update(review_id=reviewed["review_id"],review_outcome=review_outcome,verdict=reviewed["verdict"])
                status="complete"
            except Exception as exc:
                report={"plan_id":identifier,"outcome":"blocked","error_type":type(exc).__name__,"requests_this_run":new_requests,
                    "reserved_usd":None if subscription else str(sum(Decimal(v) for v in budget.values())),"results":results}
                if isinstance(exc,ValidationError) and exc.issues:
                    report["validation_issues"]=[issue.to_dict() for issue in exc.issues]
                atomic(self.root/"reports"/(identifier+".json"),report,replace=True)
                raise
            report={"plan_id":identifier,"outcome":status,"requests_this_run":new_requests,
                "reserved_usd":None if subscription else str(sum(Decimal(v) for v in budget.values())),"results":results}
            atomic(self.root/"reports"/(identifier+".json"),report,replace=True)
            return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    sub.add_parser("apply-schema",help="Apply only the reviewed company analysis migration; no model calls")
    prepare=sub.add_parser("prepare",help="Freeze a bounded stored-transcript selection; no model calls")
    prepare.add_argument("--symbol",required=True)
    prepare.add_argument("--limit",required=True,type=int)
    prepare.add_argument("--max-usd",help="Required only for explicit openai_api plans")
    prepare.add_argument("--backend",choices=("codex_subscription","openai_api"),default="codex_subscription")
    prepare.add_argument("--offset",type=int,default=0)
    prepare.add_argument("--review-pilot",action="store_true")
    execute=sub.add_parser("execute",help="Execute an explicit immutable plan with its request/token/spend bounds")
    execute.add_argument("--plan-id",required=True)
    show=sub.add_parser("show",help="Read a stored analysis and eligible reviews at an explicit cutoff")
    show.add_argument("--analysis-id",required=True)
    show.add_argument("--as-of",required=True)
    args=parser.parse_args(argv)
    try:
        registry=load_registry(PROJECT_ROOT/"config/system_registry.json",project_root=PROJECT_ROOT,environment={})
        stores=fixed_stores()
        if args.action=="apply-schema":
            result=apply_schema(stores,registry,applied_at=now())
        elif args.action=="show":
            result=TranscriptAnalysisRepository(stores).get(args.analysis_id,as_of=args.as_of)
        else:
            job=TranscriptAnalysisJob(stores,registry,STATE_ROOT)
            if args.action=="prepare":
                result=job.prepare(symbol=args.symbol,limit=args.limit,review_pilot=args.review_pilot,
                    max_usd=args.max_usd,backend=args.backend,offset=args.offset)
            else:
                # Plan and migration rejection precede credential resolution.
                plan=job._plan(args.plan_id);ready(stores,registry)
                class LazyTransport:
                    def __init__(self):
                        self.client=None
                    def prepare_credentials(self):
                        if self.client is None:
                            key=read_project_credential(project_root=PROJECT_ROOT,name="OPENAI_API_KEY",environment=os.environ)
                            self.client=OpenAITranscriptTransport(key)
                    def request(self,raw):
                        if self.client is None:
                            raise ValidationError("Model credentials were not preflighted")
                        return self.client.request(raw)
                if plan.get("backend")=="codex_subscription":
                    from ..company.transcript_analysis_codex import CodexTranscriptTransport
                    transport=CodexTranscriptTransport(evidence_root=STATE_ROOT/"codex",
                        timeout_seconds=plan["extractor_configuration"].get("request_timeout_seconds",180))
                else:
                    transport=LazyTransport()
                result=job.execute(args.plan_id,transport)
        print(dumps_strict(result));return 0
    except Exception as exc:
        error={"error":"transcript_analysis_failed","error_type":type(exc).__name__}
        if isinstance(exc,ValidationError) and exc.issues:
            error["validation_issues"]=[issue.to_dict() for issue in exc.issues]
        print(dumps_strict(error));return 75


if __name__=="__main__": raise SystemExit(main())
