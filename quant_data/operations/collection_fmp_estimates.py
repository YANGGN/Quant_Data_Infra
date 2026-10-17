"""Bounded annual/quarterly estimate page walks over original FMP responses."""
from ..ingestion import PublicationDeferred

from dataclasses import replace
from pathlib import Path
import time
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..company.fmp_analyst_history import parse_analyst_response
from ..market.collection_universe import _utc
from .collection_fmp import fmp_units,SelectedFmpPublisher,estimate_continuation
from .collection_targets import selected_fmp_subjects
from .collection_sec import requires_active_binding
from .collection_plan import validate_budget,build_plan
from .collection_queue import run_queue,_response,_read,atomic,_clock,_utcnow
from .equibles_transcript_backfill import private_directory,job_lock

def run_estimate_pages(*,root,stores,registry,selection,initial_unit,budget,fetch,
        retained=(),resolve_retained=None,secret_values=(),utcnow=_utcnow,monotonic=time.monotonic,sleeper=time.sleep):
    started=monotonic();validate_budget(budget)
    if budget.provider!="fmp" or budget.max_requests>10 or budget.max_total_bytes>10*1024*1024 or budget.max_run_seconds>600:
        raise ResourceLimitError("Estimate walk requires at most ten requests, 10 MiB and 600 seconds")
    expected=fmp_units(stores,selection,mode=initial_unit.mode,observation_window=initial_unit.observation_window,
        cutoff=_clock(utcnow),require_active=requires_active_binding(stores))
    if initial_unit not in expected.units or initial_unit.collection!="fmp_analyst_estimates":
        raise ConflictError("Estimate walk starts with its exact selected page-zero unit")
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:raise ValidationError("Estimate walk needs its resolved provider root")
    private_directory(root);requests=received=0;unit=initial_unit;seen_hashes=[];seen_dates=set();pages=0;publications=0
    with job_lock(root/"estimate-controller"):
        for index in range(10):
            seconds=int(budget.max_run_seconds-(monotonic()-started))
            if seconds<1:return {"outcome":"invocation_budget","requests":requests,"pages":pages}
            plan=build_plan(selections=(selection,),units=(unit,),retained=retained,created_at=_clock(utcnow),
                budgets=(replace(budget,max_requests=max(1,budget.max_requests-requests),
                    max_total_bytes=max(unit.max_response_bytes,budget.max_total_bytes-received),max_run_seconds=seconds),))
            cached=_response(root,unit.unit_id,unit)
            if cached is None and not plan.reuse and not plan.blocked_units and (
                requests>=budget.max_requests or received+unit.max_response_bytes>budget.max_total_bytes):
                return {"outcome":"invocation_budget","requests":requests,"pages":pages,"next_unit_id":unit.unit_id}
            report=run_queue(root=root,plan=plan,provider="fmp",fetch=fetch,publish=None,acquire_only=True,
                resolve_retained=resolve_retained,secret_values=secret_values,utcnow=utcnow,monotonic=monotonic,sleeper=sleeper)
            requests+=report.get("requests",0);received+=report.get("received_bytes",0)
            if report["outcome"] not in ("acquired","acquired_with_gaps","complete"):
                return {**report,"requests":requests,"received_bytes":received,"pages":pages}
            cached=_response(root,unit.unit_id,unit)
            if cached is None or cached[0]["status"]!=200:
                return {"outcome":"page_unavailable","requests":requests,"received_bytes":received,"pages":pages}
            receipt,body=cached;captured=_utc(receipt["captured_at"])
            if captured>_clock(utcnow):raise ConflictError("Estimate evidence exceeds current cutoff")
            subjects,_=selected_fmp_subjects(stores,selection,cutoff=captured)
            subject=next((s for s in subjects if s.symbol==unit.subject),None)
            if subject is None:raise ConflictError("Estimate issuer was not established at original capture")
            parsed=parse_analyst_response(body,endpoint=unit.endpoint,parameters=dict(unit.parameters),subject=subject,
                captured_at=receipt["captured_at"],source_reference="collection/fmp/blobs/"+receipt["content_sha256"]+".json")
            child,status=estimate_continuation(unit,parsed,seen_page_hashes=tuple(seen_hashes),seen_target_dates=tuple(sorted(seen_dates)))
            if index and captured<previous_capture:raise ConflictError("Estimate cursor walk goes backward in capture time")
            if monotonic()-started>=budget.max_run_seconds:
                return {"outcome":"invocation_budget","requests":requests,"pages":pages,"publication_status":"deferred_at_run_deadline"}
            publisher=SelectedFmpPublisher(stores=stores,registry=registry,selection=selection,units=(unit,),cutoff=_clock(utcnow))
            with job_lock(root):
                if monotonic()-started>=budget.max_run_seconds:
                    return {"outcome":"invocation_budget","requests":requests,"pages":pages,"publication_status":"deferred_at_run_deadline"}
                path=root/"publications"/(unit.unit_id+".json")
                old=_read(path)
                if old is not None and old.get("request_id")!=unit.request_id:raise ConflictError("Estimate publication receipt differs")
                if old is None:
                    try:
                        result=publisher(unit=unit,retained=None,receipt=receipt,body=body,
                            deadline=started+budget.max_run_seconds,monotonic=monotonic)
                    except PublicationDeferred:
                        if monotonic()-started<budget.max_run_seconds:raise
                        return {"outcome":"invocation_budget","requests":requests,"pages":pages,
                            "publication_status":"deferred_at_run_deadline"}
                    private_directory(root/"publications")
                    atomic(path,{"unit_id":unit.unit_id,"request_id":unit.request_id,"result":result})
                    publications+=1
            pages+=1;previous_capture=captured;seen_hashes.append(parsed.content_sha256)
            seen_dates.update(r.target_period_end for r in parsed.rows)
            if child is None:
                return {"outcome":status,"requests":requests,"received_bytes":received,"pages":pages,
                    "published":publications,"whole_history_complete":False}
            unit=child
        raise ResourceLimitError("Estimate walk did not terminate")
