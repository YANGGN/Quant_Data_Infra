"""Bounded Nasdaq SF1 cursor acquisition, original evidence reuse and publication.

The host supplies transport and explicit budgets. No credential, schedule, store
default, bulk export URL, or automatic failed-request retry is introduced.
"""
from ..ingestion import PublicationDeferred

from dataclasses import dataclass,replace,asdict
from pathlib import Path
import time
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..company.sharadar_sf1 import parse_metadata,parse_page,prepare_partition,_parameters,DIMENSIONS,MAX_BYTES
from ..company.sharadar_repository import SharadarSf1Publisher
from ..market.collection_universe import _utc
from .collection_targets import validate_selection
from .collection_sec import requires_active_binding
from .collection_plan import AcquisitionUnit,ProviderBudget,build_plan,validate_unit,validate_budget,digest
from .collection_queue import run_queue,_response,_read,atomic,_clock,_utcnow
from .equibles_transcript_backfill import private_directory,job_lock

MAX_PAGES=100
MAX_ROWS=100000
MAX_PARTITION_BYTES=256*1024*1024

def metadata_unit(selection,*,mode,observation_window):
    return validate_unit(AcquisitionUnit("sharadar_fundamentals","sharadar","SHARADAR/SF1/metadata",
        "SHARADAR/SF1",(),mode,observation_window,(),selection.scope_sha256,
        max_response_bytes=MAX_BYTES,max_rows=10000,timeout_seconds=30))

def sf1_units(stores,selection,*,schema,mode,observation_window,cutoff,batch_size=25,lastupdated=None):
    validate_selection(stores,selection,cutoff=cutoff,collection="sharadar_fundamentals")
    if type(batch_size) is not int or not 1<=batch_size<=100:
        raise ResourceLimitError("SF1 ticker batch size must be between one and 100")
    date_filters={}
    if lastupdated is not None:
        if not isinstance(lastupdated,tuple) or len(lastupdated)!=2:
            raise ValidationError("SF1 incremental acquisition needs a bounded lastupdated date window")
        date_filters={"lastupdated.gte":lastupdated[0],"lastupdated.lte":lastupdated[1]}
    groups={}
    for s in selection.eligible:
        groups.setdefault((s.provider_symbol,s.provider_subject),set()).add(s.source_symbol)
    by_ticker={}
    for ticker,subject in groups:
        if ticker in by_ticker and by_ticker[ticker]!=subject:
            raise ConflictError("SF1 selected ticker has conflicting permanent identities")
        by_ticker[ticker]=subject
    items=sorted(groups)
    result=[]
    for pos in range(0,len(items),batch_size):
        batch=items[pos:pos+batch_size]
        for dimension in sorted(DIMENSIONS):
            params={"ticker":",".join(sorted(t for t,_ in batch)),"dimension":dimension,
                "qopts.per_page":"10000",**date_filters}
            params=_parameters(params,schema)
            result.append(validate_unit(AcquisitionUnit("sharadar_fundamentals","sharadar","SHARADAR/SF1",
                ",".join(sorted({s for _,s in batch})),params,mode,observation_window,
                tuple(sorted({s for key in batch for s in groups[key]})),selection.scope_sha256,
                max_response_bytes=MAX_BYTES,max_rows=10000,timeout_seconds=30)))
    return tuple(result)

@dataclass(frozen=True)
class Sf1Progress:
    outcome: str
    metadata_unit: AcquisitionUnit
    next_unit: AcquisitionUnit | None
    partition: object | None
    page_units: tuple[AcquisitionUnit,...]
    captured_bytes: int

def _validate_initial(stores,selection,unit,cutoff):
    validate_selection(stores,selection,cutoff=cutoff,collection="sharadar_fundamentals",
        require_active=requires_active_binding(stores))
    validate_unit(unit)
    if unit.endpoint!="SHARADAR/SF1" or "qopts.cursor_id" in dict(unit.parameters):
        raise ValidationError("SF1 cursor walk starts with one explicit first-page request")
    # The normal manifest builder validates all subject/security associations.
    build_plan(selections=(selection,),units=(unit,),
        budgets=(ProviderBudget("sharadar",1,MAX_PARTITION_BYTES,7200,1000),),created_at=cutoff)

def inspect_partition(*,root,stores,selection,initial_unit,cutoff):
    """Read exact retained pages, returning the one next permissible request."""
    _validate_initial(stores,selection,initial_unit,cutoff)
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValidationError("SF1 private root must be explicit and resolved")
    meta=metadata_unit(selection,mode=initial_unit.mode,observation_window=initial_unit.observation_window)
    evidence=_response(root,meta.unit_id,meta)
    if evidence is None:return Sf1Progress("metadata_required",meta,meta,None,(),0)
    receipt,body=evidence
    if receipt["status"]!=200:return Sf1Progress("metadata_http_failure",meta,None,None,(),len(body))
    if _utc(receipt["captured_at"])>_utc(cutoff):raise ConflictError("SF1 metadata is beyond cutoff")
    schema=parse_metadata(body,captured_at=receipt["captured_at"],
        source_reference="collection/sharadar/blobs/"+receipt["content_sha256"]+".json")
    _parameters(dict(initial_unit.parameters),schema)
    pages=[];units=[];received=len(body);unit=initial_unit
    for index in range(MAX_PAGES):
        evidence=_response(root,unit.unit_id,unit)
        if evidence is None:
            return Sf1Progress("page_required",meta,unit,None,tuple(units),received)
        receipt,body=evidence
        if receipt["status"]!=200:
            return Sf1Progress("page_http_failure",meta,None,None,tuple(units),received+len(body))
        if _utc(receipt["captured_at"])>_utc(cutoff):raise ConflictError("SF1 page is beyond cutoff")
        received+=len(body)
        if received>MAX_PARTITION_BYTES:raise ResourceLimitError("SF1 metadata and pages exceed partition byte cap")
        page=parse_page(body,schema=schema,parameters=dict(unit.parameters),captured_at=receipt["captured_at"],
            source_reference="collection/sharadar/blobs/"+receipt["content_sha256"]+".json")
        pages.append(page);units.append(unit)
        partition=prepare_partition(pages,max_pages=MAX_PAGES,max_rows=MAX_ROWS,max_bytes=MAX_PARTITION_BYTES)
        if partition.transport_complete:
            return Sf1Progress("ready",meta,None,partition,tuple(units),received)
        params={**dict(unit.parameters),"qopts.cursor_id":page.next_cursor}
        unit=replace(initial_unit,parameters=tuple(sorted(params.items())))
    return Sf1Progress("page_cap_reached_incomplete",meta,None,None,tuple(units),received)

def publish_partition(*,root,stores,registry,selection,initial_unit,cutoff,deadline=None,monotonic=time.monotonic):
    root=Path(root)
    with job_lock(root):
        if deadline is not None and monotonic()>=deadline:
            return {"outcome":"invocation_budget","written_count":0}
        progress=inspect_partition(root=root,stores=stores,selection=selection,initial_unit=initial_unit,cutoff=cutoff)
        if deadline is not None and monotonic()>=deadline:
            return {"outcome":"invocation_budget","written_count":0}
        if progress.outcome!="ready":
            return {"outcome":progress.outcome,"pages":len(progress.page_units),"written_count":0,
                "next_unit":None if progress.next_unit is None else asdict(progress.next_unit)}
        try:
            result=SharadarSf1Publisher(stores,registry).publish(progress.partition,selection=selection,
                ingested_at=cutoff,deadline=deadline,monotonic=monotonic)
        except PublicationDeferred:
            if deadline is None or monotonic()<deadline:raise
            return {"outcome":"invocation_budget","written_count":0}
        receipt={"outcome":result.outcome,"capture_id":result.snapshot_id,"written_count":result.written_count,
            "pages":len(progress.page_units),"transport_complete":True,"remote_coherence":"unproven",
            "selection_sha256":selection.scope_sha256,"acquisition_id":progress.partition.acquisition_id,
            "unit_ids":[u.unit_id for u in progress.page_units],"metadata_unit_id":progress.metadata_unit.unit_id}
        private_directory(root/"sf1-publications")
        # First successful receipt is immutable; canonical replay may return no new snapshot.
        path=root/"sf1-publications"/(digest({"acquisition":progress.partition.acquisition_id,"selection":selection.scope_sha256})+".json")
        old=_read(path)
        if old is None:atomic(path,receipt)
        return receipt

def run_partition(*,root,stores,registry,selection,initial_unit,budget,fetch,retained=(),resolve_retained=None,
                  secret_values=(),utcnow=_utcnow,monotonic=time.monotonic,sleeper=time.sleep):
    """Continue one frozen partition within a shared provider ledger and run cap."""
    started=monotonic()
    validate_budget(budget)
    if budget.provider!="sharadar" or budget.max_requests>101 or budget.max_total_bytes>MAX_PARTITION_BYTES:
        raise ResourceLimitError("SF1 partition requires at most 101 requests and 256 MiB")
    _validate_initial(stores,selection,initial_unit,_clock(utcnow))
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValidationError("SF1 private root must be explicit and resolved")
    private_directory(root)
    requests=received=0
    # Controller ownership is distinct from the provider queue's short critical sections.
    with job_lock(root/"sf1-controller"):
        for step in range(MAX_PAGES+2):
            progress=inspect_partition(root=root,stores=stores,selection=selection,initial_unit=initial_unit,cutoff=_clock(utcnow))
            if monotonic()-started>=budget.max_run_seconds:
                return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,"written_count":0}
            if progress.outcome=="ready":
                result=publish_partition(root=root,stores=stores,registry=registry,selection=selection,
                    initial_unit=initial_unit,cutoff=_clock(utcnow),deadline=started+budget.max_run_seconds,monotonic=monotonic)
                return {**result,"requests":requests,"received_bytes":received}
            unit=progress.next_unit
            if unit is None:return {"outcome":progress.outcome,"requests":requests,"received_bytes":received}
            seconds=int(budget.max_run_seconds-(monotonic()-started))
            if (requests>=budget.max_requests or received+unit.max_response_bytes>budget.max_total_bytes
                or progress.captured_bytes+unit.max_response_bytes>MAX_PARTITION_BYTES
                or seconds<=unit.timeout_seconds):
                return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,
                    "next_unit":asdict(unit),"pages":len(progress.page_units)}
            remaining=replace(budget,max_requests=budget.max_requests-requests,
                max_total_bytes=budget.max_total_bytes-received,max_run_seconds=seconds)
            plan=build_plan(selections=(selection,),units=(unit,),budgets=(remaining,),retained=retained,created_at=_clock(utcnow))
            report=run_queue(root=root,plan=plan,provider="sharadar",fetch=fetch,publish=None,acquire_only=True,
                resolve_retained=resolve_retained,secret_values=secret_values,utcnow=utcnow,monotonic=monotonic,sleeper=sleeper)
            requests+=report.get("requests",0);received+=report.get("received_bytes",0)
            if report["outcome"] not in ("acquired","acquired_with_gaps","complete"):
                return {**report,"requests":requests,"received_bytes":received}
        raise ResourceLimitError("SF1 bounded controller did not terminate")
