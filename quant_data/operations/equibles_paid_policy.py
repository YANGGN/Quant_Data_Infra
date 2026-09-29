"""Versioned Equibles selection and paid-quota conversion; no provider work.

The host invokes this transition deliberately. Immutable legacy checkpoint bytes
remain beside the converted state. Membership changes do not reset subject
progress, raw responses, reservations, failures, or charged attempts.
"""
from __future__ import annotations
import copy
from datetime import datetime,timezone,timedelta
import hashlib
import re
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..json_codec import loads_strict,dumps_strict
from ..market.collection_bindings import PinnedCollection
from ..market.collection_universe import _utc

PAID_DAILY_CEILING=100000
PAID_REQUESTS_PER_RUN=1000
MAX_SELECTED=5000
POLICY={"version":2,"daily_ceiling":PAID_DAILY_CEILING,"max_requests_per_run":PAID_REQUESTS_PER_RUN,
    "max_run_seconds":3600,"max_total_bytes":400*1024*1024,"minimum_spacing_seconds":1}

def _integer(value,low,high,name):
    if type(value) is not int or not low<=value<=high:
        raise ValidationError("Equibles "+name+" is invalid")
    return value

def operating_daily_cap(state, default=PAID_DAILY_CEILING):
    """Optional account allocation; entitlement and historical charges are unchanged."""
    cap = state.get("operating_daily_cap", default)
    _integer(cap, 1, PAID_DAILY_CEILING, "operating daily cap")
    return min(default, cap)


def validate_checkpoint(state):
    from .equibles_transcript_backfill import validate_symbol
    if not isinstance(state,dict) or type(state.get("version")) is not int or state["version"] not in (1,2):
        raise ValidationError("Equibles checkpoint version is invalid")
    operating_daily_cap(state)
    roster=state.get("roster")
    maximum=519 if state["version"]==1 else MAX_SELECTED
    if not isinstance(roster,list) or not (1 if state["version"]==1 else 0)<=len(roster)<=maximum:
        raise ResourceLimitError("Equibles checkpoint roster exceeds its bound")
    symbols=set();instruments=set()
    for row in roster:
        if not isinstance(row,dict): raise ValidationError("Equibles roster row is invalid")
        symbol=validate_symbol(row.get("symbol"))
        instrument=row.get("instrument_id")
        if not isinstance(instrument,str) or not instrument or len(instrument)>256:
            raise ValidationError("Equibles instrument identity is invalid")
        if symbol in symbols or instrument in instruments:
            raise ConflictError("Equibles checkpoint has duplicate security identities")
        symbols.add(symbol);instruments.add(instrument)
    index=_integer(state.get("index"),0,len(roster),"checkpoint index")
    completed=state.get("completed")
    if not isinstance(completed,dict) or len(completed)>MAX_SELECTED+519:
        raise ValidationError("Equibles completed histories are invalid")
    if any(row["symbol"] not in completed for row in roster[:index]):
        raise ConflictError("Equibles completed prefix differs from its progress records")
    if index<len(roster) and roster[index]["symbol"] in completed:
        raise ConflictError("Equibles current subject was already completed")
    if index==len(roster) and (state.get("current") is not None or state.get("pending") is not None):
        raise ConflictError("Equibles terminal checkpoint retains current work")
    _integer(state.get("transcripts"),0,(MAX_SELECTED+519)*1000,"transcript count")
    usage=state.get("usage")
    if not isinstance(usage,dict) or len(usage)>3660:
        raise ValidationError("Equibles quota history is invalid")
    for day,bucket in usage.items():
        try: parsed=datetime.strptime(day,"%Y-%m-%d")
        except (ValueError,TypeError) as exc: raise ValidationError("Equibles quota date is invalid") from exc
        if parsed.date().isoformat()!=day or not isinstance(bucket,dict):
            raise ValidationError("Equibles quota bucket is invalid")
        _integer(bucket.get("attempted"),0,PAID_DAILY_CEILING,"charged attempts")
        _integer(bucket.get("remaining"),0,PAID_DAILY_CEILING,"saved remaining quota")
    if state["version"]==2:
        if state.get("quota_policy",{}).get("bounds")!=POLICY:
            raise ValidationError("Equibles paid policy differs")
        scope=state.get("selection")
        if not isinstance(scope,dict) or set(scope)!={"membership_snapshot_id","mapping_id","scope_sha256","selected_members","identity_gaps","source_members_by_symbol"}:
            raise ValidationError("Equibles selected scope is invalid")
        _integer(scope["selected_members"],0,MAX_SELECTED,"selected members")
        if not re.fullmatch("[a-f0-9]{64}",scope["scope_sha256"]):
            raise ValidationError("Equibles selected scope hash is invalid")
        if len(roster)>scope["selected_members"]:
            raise ValidationError("Equibles resolved subjects exceed selection")
        _utc(state["quota_policy"]["transitioned_at"])
    deferred=state.get("deferred_progress",{})
    if not isinstance(deferred,dict) or len(deferred)>MAX_SELECTED+519:
        raise ResourceLimitError("Deferred transcript progress exceeds its bound")
    for symbol,saved in deferred.items():
        validate_symbol(symbol)
        if (not isinstance(saved,dict) or set(saved)!={"instrument_id","progress"}
            or not isinstance(saved["instrument_id"],str) or not saved["instrument_id"]
            or not isinstance(saved["progress"],dict) or symbol in completed):
            raise ValidationError("Deferred transcript progress is invalid")
    from .equibles_catalogue import validate_epoch
    validate_epoch(state)
    return state

def convert_checkpoint(body,selection,*,transitioned_at):
    from .equibles_transcript_backfill import validate_symbol
    at=_utc(transitioned_at)
    if not isinstance(body,bytes) or len(body)>32*1024*1024:
        raise ResourceLimitError("Equibles legacy checkpoint exceeds bound")
    old=loads_strict(body,max_bytes=32*1024*1024)
    validate_checkpoint(old)
    if not isinstance(selection,PinnedCollection) or selection.binding.id!="equibles_transcripts":
        raise ValidationError("Equibles conversion needs its pinned collection")
    if len(selection.subjects)>MAX_SELECTED:
        raise ResourceLimitError("Equibles selection exceeds bound")
    if old["version"]==2:
        if old["selection"]["scope_sha256"]==selection.scope_sha256:
            return old,False
    if _utc(old.get("selection_transition",{}).get("transitioned_at",old["created_at"]))>at:
        raise ConflictError("Equibles transition predates its checkpoint")
    by_symbol={};members={}
    for row in selection.eligible:
        symbol=validate_symbol(row.provider_symbol)
        instrument=row.instrument_id
        if not isinstance(instrument,str) or not instrument:
            raise ValidationError("Equibles selected instrument is unresolved")
        existing=by_symbol.get(symbol)
        if existing is not None and existing["instrument_id"]!=instrument:
            raise ConflictError("Equibles selected provider identity is ambiguous")
        by_symbol[symbol]={"symbol":symbol,"instrument_id":instrument}
        members.setdefault(symbol,[]).append(row.source_symbol)
    old_rows={**old.get("retired_roster",{}),**{r["symbol"]:r for r in old["roster"]}}
    old_instruments={r["instrument_id"]:r["symbol"] for r in old_rows.values()}
    if any(s in old["completed"] and s not in old_rows for s in by_symbol):
        raise ConflictError("Returning transcript member lacks its original security identity")
    for symbol,row in by_symbol.items():
        prior_symbol=old_instruments.get(row["instrument_id"])
        if prior_symbol is not None and prior_symbol!=symbol:
            raise ConflictError("Equibles symbol transition needs an explicit progress-preserving identity conversion")
        if symbol in old_rows and old_rows[symbol]["instrument_id"]!=row["instrument_id"]:
            raise ConflictError("Equibles selected symbol changed instrument identity")
    current_symbol=old["roster"][old["index"]]["symbol"] if old["index"]<len(old["roster"]) else None
    if old.get("pending") is not None and current_symbol not in by_symbol:
        raise ConflictError("An unselected pending Equibles attempt requires original-scope reconciliation")
    # Deliberately rebuild the cursor from stable identities, never re-sort an
    # enlarged roster under the old positional index.
    finished=[r["symbol"] for r in old["roster"][:old["index"]] if r["symbol"] in by_symbol]
    pending=[r["symbol"] for r in old["roster"][old["index"]:] if r["symbol"] in by_symbol]
    in_roster={r["symbol"] for r in old["roster"]}
    returning=sorted(s for s in by_symbol if s not in in_roster and s in old["completed"])
    additions=sorted(s for s in by_symbol if s not in in_roster and s not in old["completed"])
    finished+=returning
    order=finished+pending+additions
    state=copy.deepcopy(old)
    deferred=copy.deepcopy(old.get("deferred_progress",{}))
    previous=old.get("retired_current")
    if previous is not None:
        saved={"instrument_id":previous["instrument_id"],"progress":copy.deepcopy(previous["progress"])}
        if previous["symbol"] in deferred and deferred[previous["symbol"]]!=saved:
            raise ConflictError("Retired transcript cursors conflict")
        deferred[previous["symbol"]]=saved
    if old["current"] is not None and current_symbol not in by_symbol:
        saved={"instrument_id":old_rows[current_symbol]["instrument_id"],"progress":copy.deepcopy(old["current"])}
        if current_symbol in deferred and deferred[current_symbol]!=saved:
            raise ConflictError("Retired transcript cursor would be replaced")
        deferred[current_symbol]=saved
    for symbol,saved in deferred.items():
        if symbol in by_symbol and saved["instrument_id"]!=by_symbol[symbol]["instrument_id"]:
            raise ConflictError("Returning partial transcript member changed identity")
    current=copy.deepcopy(old["current"]) if current_symbol in by_symbol else None
    if current is None and len(finished)<len(order) and order[len(finished)] in deferred:
        current=deferred.pop(order[len(finished)])["progress"]
    state.update(version=2,roster=[by_symbol[s] for s in order],index=len(finished),
        current=current,retired_current=None,deferred_progress=deferred)
    state["retired_roster"]={s:r for s,r in old_rows.items() if s not in by_symbol}
    if len(state["retired_roster"])>MAX_SELECTED+519:
        raise ResourceLimitError("Retired Equibles identity history exceeds its bound")
    legacy_sha=hashlib.sha256(body).hexdigest()
    state["selection"]={"membership_snapshot_id":selection.membership_snapshot_id,"mapping_id":selection.mapping_id,
        "scope_sha256":selection.scope_sha256,"selected_members":len(selection.subjects),
        "identity_gaps":[{"source_symbol":r.source_symbol,"status":r.status,"reason":r.reason} for r in selection.gaps],
        "source_members_by_symbol":{s:sorted(v) for s,v in sorted(members.items())}}
    if old["version"]==1:
        state["quota_policy"]={"bounds":dict(POLICY),"transitioned_at":at,"legacy_state_sha256":legacy_sha,
            "legacy_usage":copy.deepcopy(old["usage"]),"provider_paid_allowance_verified":False}
        state["legacy_checkpoint_reference"]="transitions/"+legacy_sha+".v1.json"
    state["selection_transition"]={"transitioned_at":at,"previous_state_sha256":legacy_sha,
        "previous_checkpoint_reference":"transitions/"+legacy_sha+".v"+str(old["version"])+".json"}
    quota_bucket(state,at[:10])
    validate_checkpoint(state)
    return state,True

def quota_bucket(state,day):
    """Current policy is applied once; earlier charges and old quota remain evidence."""
    cap=PAID_DAILY_CEILING if state["version"]==2 else 100
    bucket=state["usage"].setdefault(day,{"attempted":0,"remaining":cap})
    if state["version"]==2 and bucket.get("policy_version")!=2:
        previous=copy.deepcopy(bucket)
        used=_integer(bucket["attempted"],0,PAID_DAILY_CEILING,"charged attempts")
        bucket.update(policy_version=2,prior_policy_bucket=previous,
            remaining=max(0,cap-used),provider_limit=None,provider_remaining=None,
            provider_observed_at=None,paid_headers_verified=False,verification_requests=0)
    bucket["remaining"] = min(bucket["remaining"], max(0, operating_daily_cap(state, cap)-bucket["attempted"]))
    return bucket

def apply_paid_headers(state,bucket,*,limit,remaining,reset,receipt_at,reservation_at):
    """Stale pre-transition headers cannot establish paid-plan remaining quota."""
    at=_utc(receipt_at)
    transition=state["quota_policy"]["transitioned_at"]
    if _utc(reservation_at)<transition or at<transition:
        return False
    bucket["provider_limit"]=limit
    bucket["provider_remaining"]=remaining
    bucket["provider_observed_at"]=at
    bucket["paid_headers_verified"]=True
    bucket["remaining"]=min(max(0,operating_daily_cap(state)-bucket["attempted"]),remaining)
    bucket["reset"]=reset
    state["quota_policy"]["provider_paid_allowance_verified"]=limit>=PAID_DAILY_CEILING
    return True

def migrate_checkpoint(root,selection,*,transitioned_at):
    from .equibles_transcript_backfill import job_lock,read_file,private_directory,atomic
    with job_lock(root):
        body=read_file(root/"state.json",32*1024*1024)
        state,changed=convert_checkpoint(body,selection,transitioned_at=transitioned_at)
        if not changed:
            return {"outcome":"unchanged","scope_sha256":selection.scope_sha256,"provider_requests":0}
        private_directory(root/"transitions")
        atomic(root/state["selection_transition"]["previous_checkpoint_reference"],body)
        transition={"contract":"quant_data.equibles_checkpoint_transition.v2","transitioned_at":_utc(transitioned_at),
            "previous_state_sha256":state["selection_transition"]["previous_state_sha256"],"selection":state["selection"],
            "bounds":POLICY,"provider_requests":0,"recurring_unit_changes":0}
        transition_id=hashlib.sha256(dumps_strict(transition).encode()).hexdigest()
        atomic(root/"transitions"/(transition_id+".v2.json"),transition)
        atomic(root/"state.json",state,replace=True)
        return {"outcome":"converted","selected_members":len(selection.subjects),
            "resolved_subjects":len(state["roster"]),"completed_selected_subjects":state["index"],
            "identity_gaps":len(selection.gaps),"provider_requests":0,"recurring_unit_changes":0}
