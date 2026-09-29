"""Compose selected publishers with the shared finite provider queue.

Host scheduling and operational activation remain explicit. This module never
migrates stores, discovers providers, edits bindings, or resets completed work.
"""
from ..ingestion import PublicationDeferred

from dataclasses import replace
from pathlib import Path
from ..errors import ConflictError,ValidationError,ResourceLimitError
from ..market.collection_universe import _utc
from .collection_targets import validate_selection
from .collection_sec import requires_active_binding,publish_sec_pairs
from .collection_fmp import SelectedFmpPublisher
from .collection_prices import SelectedPricePublisher
from .collection_plan import build_plan
from .collection_queue import run_queue,_clock,_utcnow,_response
from .collection_queue import QueueResponse
from .collection_plan import ProviderBudget
import time

def run_selected_plan(*,root,stores,registry,selections,plan,provider,fetch,
        price_collector=None,price_requests=(),resolve_retained=None,secret_values=(),
        utcnow=_utcnow,monotonic=time.monotonic,sleeper=time.sleep):
    """Run a finite FMP or SEC plan, then return explicit canonical outcomes."""
    started=monotonic()
    if provider not in ("fmp","sec"):
        raise ValidationError("Sharadar and Equibles require their complete-partition controllers")
    if _utc(plan.created_at)>_clock(utcnow):
        raise ConflictError("Selected runtime cannot use a future execution manifest")
    selections=tuple(selections)
    by_binding={s.binding.id:s for s in selections}
    if len(by_binding)!=len(selections):raise ValidationError("Selected runtime bindings repeat")
    for selection in selections:
        validate_selection(stores,selection,cutoff=plan.created_at,collection=selection.binding.id,
            require_active=requires_active_binding(stores))
    rebuilt=build_plan(selections=selections,units=plan.units,budgets=plan.budgets,
        retained=tuple(r for _,r in plan.reuse),created_at=plan.created_at)
    if (rebuilt.units!=plan.units or rebuilt.budgets!=plan.budgets
        or rebuilt.identity_gaps!=plan.identity_gaps or rebuilt.omitted_collections!=plan.omitted_collections
        or any(u.provider!=provider for u in plan.units)):
        raise ConflictError("Selected runtime differs from its frozen unit manifest")
    if len(plan.budgets)!=1 or plan.budgets[0].provider!=provider:
        raise ValidationError("Selected runtime requires one explicit shared provider budget")
    budget=plan.budgets[0];deadline=started+budget.max_run_seconds
    def dispatch(selected_plan,**kwargs):
        remaining=int(deadline-monotonic())
        if remaining<1:return {"outcome":"invocation_budget","requests":0,"received_bytes":0,"published":0,"reused":0}
        adjusted=replace(selected_plan,budgets=(replace(selected_plan.budgets[0],max_run_seconds=remaining),))
        return run_queue(root=root,plan=adjusted,provider=provider,fetch=fetch,utcnow=utcnow,
            monotonic=monotonic,sleeper=sleeper,secret_values=secret_values,deadline=deadline,**kwargs)
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValidationError("Selected runtime requires its explicit resolved shared provider root")
    if provider=="sec":
        if set(by_binding)!={"sec_filings_companyfacts"} or any(u.endpoint not in ("submissions","companyfacts") for u in plan.units):
            raise ValidationError("Selected SEC runtime requires explicit paired source endpoints")
        report=dispatch(plan,publish=None,acquire_only=True,resolve_retained=resolve_retained)
        if monotonic()>=deadline:
            return {**report,"publications":[],"publication_status":"deferred_at_run_deadline"}
        receipts=publish_sec_pairs(root=root,stores=stores,registry=registry,
            selection=by_binding["sec_filings_companyfacts"],units=plan.units,cutoff=_clock(utcnow),deadline=deadline,monotonic=monotonic)
        return {**report,"publications":list(receipts),"coverage_basis":"recent_submissions_and_supported_companyfacts"}
    publishers={}
    has_prices=any(u.collection=="daily_prices" for u in plan.units)
    if has_prices:
        if set(by_binding)!={"daily_prices"} or price_collector is None:
            raise ValidationError("Daily prices require their selected collector and separate session plan")
        publishers["daily_prices"]=SelectedPricePublisher(stores=stores,selection=by_binding["daily_prices"],
            cutoff=plan.created_at,collector=price_collector,requests=price_requests,resolve_retained=resolve_retained)
        if (len(price_requests)!=len(plan.units) or
            {u.unit_id:u for u,_ in price_requests}!={u.unit_id:u for u in plan.units}):
            raise ConflictError("Daily price windows differ from the execution manifest")
        current=any(u.mode=="incremental" for u in plan.units)
        if current:
            if any(u.mode!="incremental" for u in plan.units):
                raise ValidationError("Price current-session and backfill work require separate manifests")
            if not any(u.subject=="AAPL" for u in plan.units):
                raise ValidationError("Current-session price work requires the AAPL sentinel")
            windows={w.start for _,w in price_requests}
            if len(windows)!=1 or any(w.start!=w.end or w.sessions!=(w.start,) for _,w in price_requests):
                raise ValidationError("Current-session prices need one identical explicit session per instrument")
    else:
        current=False
        for key,selection in by_binding.items():
            units=tuple(u for u in plan.units if u.collection==key)
            publishers[key]=SelectedFmpPublisher(stores=stores,registry=registry,selection=selection,
                units=units,cutoff=plan.created_at,resolve_retained=resolve_retained)
    def publish(**kw):
        return publishers[kw["unit"].collection](**kw,deadline=deadline,monotonic=monotonic)
    initial_requests=initial_bytes=0
    if current:
        sentinel=next(u for u in plan.units if u.subject=="AAPL")
        budget=next(b for b in plan.budgets if b.provider=="fmp")
        sentinel_plan=replace(plan,units=(sentinel,),budgets=(replace(budget,max_requests=1),),
            reuse=tuple(item for item in plan.reuse if item[0]==sentinel.unit_id),
            blocked_units=tuple(item for item in plan.blocked_units if item[0]==sentinel.unit_id))
        def original(item):
            if resolve_retained is None:raise ConflictError("Price sentinel requires original retained evidence")
            return QueueResponse(200,resolve_retained(item),item.captured_at)
        checked=dispatch(sentinel_plan,publish=None,acquire_only=True,resolve_retained=original)
        initial_requests=checked.get("requests",0);initial_bytes=checked.get("received_bytes",0)
        if sentinel_plan.blocked_units:
            return {**checked,"outcome":"price_session_not_established","sentinel":"AAPL","coverage":"blocked"}
        cached=_response(root,sentinel.unit_id,sentinel)
        if cached is None or cached[0]["status"]!=200:
            return {**checked,"outcome":"price_session_not_established","sentinel":"AAPL","coverage":"incomplete"}
        if monotonic()>=deadline:
            return {**checked,"outcome":"invocation_budget","publication_status":"deferred_at_run_deadline"}
        try:
            result=publish(unit=sentinel,retained=None,receipt=cached[0],body=cached[1])
        except PublicationDeferred:
            if monotonic()<deadline:raise
            return {"outcome":"invocation_budget","publication_status":"deferred_at_run_deadline",
                "requests":initial_requests,"received_bytes":initial_bytes,"published":0}
        if result.get("coverage")=="empty":
            return {"outcome":"price_session_not_established","sentinel":"AAPL","coverage":"incomplete",
                "requests":initial_requests,"received_bytes":initial_bytes}
        remaining_requests=budget.max_requests-initial_requests
        remaining_bytes=budget.max_total_bytes-initial_bytes
        remaining_seconds=int(budget.max_run_seconds-(monotonic()-started))
        if remaining_requests<1 or remaining_bytes<max(u.max_response_bytes for u in plan.units) or remaining_seconds<1:
            return {"outcome":"invocation_budget","requests":initial_requests,"received_bytes":initial_bytes,"sentinel":"available"}
        plan=replace(plan,budgets=(replace(budget,max_requests=remaining_requests,max_total_bytes=remaining_bytes,
            max_run_seconds=remaining_seconds),))
    report=dispatch(plan,publish=publish)
    return {**report,"requests":report.get("requests",0)+initial_requests,
        "received_bytes":report.get("received_bytes",0)+initial_bytes}
