"""Finite SF1 INDICATORS cursor acquisition through the shared Nasdaq ledger."""
from ..ingestion import PublicationDeferred

from dataclasses import dataclass,replace,asdict
from pathlib import Path
import time
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..company.sharadar_definitions import parse_definition_metadata,parse_definition_page,prepare_definition_snapshot
from ..company.sharadar_definition_repository import SharadarDefinitionPublisher
from ..market.collection_universe import _utc
from .collection_targets import validate_selection
from .collection_sec import requires_active_binding
from .collection_plan import AcquisitionUnit,ProviderBudget,validate_unit,validate_budget,build_plan
from .collection_queue import run_queue,_response,_clock,_utcnow
from .equibles_transcript_backfill import private_directory,job_lock
MAX_BYTES=32*1024*1024

def definition_unit(selection,*,mode,observation_window,metadata=False,cursor=None):
    endpoint="SHARADAR/INDICATORS";subject=endpoint+":SF1";params={"table":"SF1","qopts.per_page":"10000"}
    if metadata:endpoint+="/metadata";subject="SHARADAR/INDICATORS";params={}
    elif cursor is not None:params["qopts.cursor_id"]=cursor
    return validate_unit(AcquisitionUnit("sharadar_fundamentals","sharadar",endpoint,subject,tuple(sorted(params.items())),
        mode,observation_window,(),selection.scope_sha256,max_response_bytes=16*1024*1024,max_rows=10000,timeout_seconds=30))

@dataclass(frozen=True)
class DefinitionProgress:
    outcome: str
    next_unit: object
    snapshot: object
    retained_bytes: int
    page_count: int

def inspect_definitions(*,root,selection,mode,observation_window,cutoff):
    at=_utc(cutoff)
    meta=definition_unit(selection,mode=mode,observation_window=observation_window,metadata=True)
    evidence=_response(Path(root),meta.unit_id,meta)
    if evidence is None:return DefinitionProgress("metadata_required",meta,None,0,0)
    receipt,body=evidence
    if receipt["status"]!=200:return DefinitionProgress("metadata_http_failure",None,None,len(body),0)
    if _utc(receipt["captured_at"])>at:raise ConflictError("Definition metadata exceeds cutoff")
    schema=parse_definition_metadata(body,captured_at=receipt["captured_at"],source_reference="collection/sharadar/blobs/"+receipt["content_sha256"]+".json")
    received=len(body);pages=[];cursor=None;seen=set()
    for i in range(10):
        unit=definition_unit(selection,mode=mode,observation_window=observation_window,cursor=cursor)
        evidence=_response(Path(root),unit.unit_id,unit)
        if evidence is None:return DefinitionProgress("page_required",unit,None,received,i)
        receipt,body=evidence;received+=len(body)
        if received>MAX_BYTES:raise ResourceLimitError("Definition evidence exceeds 32 MiB")
        if receipt["status"]!=200:return DefinitionProgress("page_http_failure",None,None,received,i)
        if _utc(receipt["captured_at"])>at:raise ConflictError("Definition page exceeds cutoff")
        page=parse_definition_page(body,schema=schema,parameters=dict(unit.parameters),captured_at=receipt["captured_at"],
            source_reference="collection/sharadar/blobs/"+receipt["content_sha256"]+".json")
        pages.append(page)
        if sum(len(p.rows) for p in pages)>10000:raise ResourceLimitError("Definitions exceed 10000 rows")
        if i and page.captured_at<pages[i-1].captured_at:raise ConflictError("Definition pages cannot go backward in capture time")
        cursor=page.next_cursor
        if cursor is None:return DefinitionProgress("ready",None,prepare_definition_snapshot(pages),received,len(pages))
        if cursor in seen:raise ConflictError("Definition cursor repeats")
        seen.add(cursor)
    return DefinitionProgress("page_cap_reached_incomplete",None,None,received,len(pages))

def run_definitions(*,root,stores,registry,selection,mode,observation_window,budget,fetch,
        retained=(),resolve_retained=None,secret_values=(),utcnow=_utcnow,monotonic=time.monotonic,sleeper=time.sleep):
    started=monotonic();validate_budget(budget)
    if budget.provider!="sharadar" or budget.max_requests>11 or budget.max_total_bytes>MAX_BYTES or budget.max_run_seconds>600:
        raise ResourceLimitError("Definition invocation requires at most 11 requests, 32 MiB and 600 seconds")
    validate_selection(stores,selection,cutoff=_clock(utcnow),collection="sharadar_fundamentals",
        require_active=requires_active_binding(stores))
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:raise ValidationError("Definitions need an explicit resolved private root")
    private_directory(root);requests=received=0
    with job_lock(root/"definitions-controller"):
        for step in range(12):
            progress=inspect_definitions(root=root,selection=selection,mode=mode,observation_window=observation_window,cutoff=_clock(utcnow))
            remaining=int(budget.max_run_seconds-(monotonic()-started))
            if remaining<1:return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,"written_count":0}
            if progress.outcome=="ready":
                with job_lock(root):
                    if monotonic()-started>=budget.max_run_seconds:
                        return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,"written_count":0}
                    try:
                        result=SharadarDefinitionPublisher(stores,registry).publish(progress.snapshot,ingested_at=_clock(utcnow),
                            deadline=started+budget.max_run_seconds,monotonic=monotonic)
                    except PublicationDeferred:
                        if monotonic()-started<budget.max_run_seconds:raise
                        return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,"written_count":0}
                return {"outcome":result.outcome,"written_count":result.written_count,"snapshot_id":result.snapshot_id,
                    "requests":requests,"received_bytes":received,"pages":progress.page_count,"transport_complete":True,
                    "remote_coherence":"unproven"}
            unit=progress.next_unit
            if unit is None:return {"outcome":progress.outcome,"requests":requests,"received_bytes":received,"written_count":0}
            if (requests>=budget.max_requests or received+unit.max_response_bytes>budget.max_total_bytes
                or progress.retained_bytes+unit.max_response_bytes>MAX_BYTES or remaining<=unit.timeout_seconds):
                return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,"next_unit":asdict(unit),"written_count":0}
            plan=build_plan(selections=(selection,),units=(unit,),created_at=_clock(utcnow),retained=retained,
                budgets=(replace(budget,max_requests=budget.max_requests-requests,
                    max_total_bytes=budget.max_total_bytes-received,max_run_seconds=remaining),))
            report=run_queue(root=root,plan=plan,provider="sharadar",fetch=fetch,publish=None,acquire_only=True,
                resolve_retained=resolve_retained,secret_values=secret_values,utcnow=utcnow,monotonic=monotonic,sleeper=sleeper)
            requests+=report.get("requests",0);received+=report.get("received_bytes",0)
            if report["outcome"] not in ("acquired","acquired_with_gaps","complete"):
                return {**report,"requests":requests,"received_bytes":received}
        raise ResourceLimitError("Definition controller did not terminate")
