"""Private replay-safe acquisition queue for exact, finite single HTTP units.

This worker owns no provider credential or URL resolver. Host adapters supply one
bounded request and a canonical publisher. Equibles uses its existing shared
checkpoint ledger and cannot be dispatched here.
"""
from __future__ import annotations

from ..ingestion import PublicationDeferred
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import math
import os
import time
from urllib.parse import quote
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..market.collection_universe import _utc as _utc_value
from ..json_codec import loads_strict
from .collection_plan import AcquisitionUnit, CollectionPlan, validate_unit, validate_budget, digest, reusable
from .equibles_transcript_backfill import private_directory, job_lock, read_file, fsync_directory

STATE_BOUND=64*1024*1024
SAFE_HEADERS=frozenset(("content-type","date","etag","last-modified","x-ratelimit-limit","x-ratelimit-remaining","x-ratelimit-reset","retry-after"))

class CredentialExposureError(ValidationError):
    """A response contains credential material and cannot be treated as a gap."""


@dataclass(frozen=True)
class QueueResponse:
    status: int
    body: bytes
    captured_at: str
    headers: tuple[tuple[str,str],...] = ()
    redirected: bool = False

def atomic(path,body,*,replace=False):
    from ..json_codec import dumps_strict
    if not isinstance(body,bytes): body=dumps_strict(body,max_bytes=STATE_BOUND).encode()
    if len(body)>STATE_BOUND: raise ResourceLimitError("Collection private artifact exceeds bound")
    if path.exists():
        old=read_file(path,STATE_BOUND)
        if old==body: return
        if not replace: raise ConflictError("Collection immutable artifact differs")
    temporary=path.with_name(path.name+".tmp-"+str(os.getpid()))
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as handle:
            handle.write(body);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path);fsync_directory(path.parent)
    finally:
        if temporary.exists(): temporary.unlink()

def provider_stop(status, provider=None):
    return status in (401,403,429) or (provider != "fmp" and status>=500)

def _retry_at(receipt):
    from email.utils import parsedate_to_datetime
    at=datetime.fromisoformat(receipt["captured_at"].replace("Z","+00:00"))
    value=receipt["headers"].get("retry-after")
    boundary=at.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
    if value is not None:
        try:
            target=at+timedelta(seconds=int(value)) if value.isascii() and value.isdigit() else parsedate_to_datetime(value)
            if target.tzinfo is not None and at<target:
                boundary=target.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ResourceLimitError("Collection Retry-After exceeds representable time; provider remains stopped") from exc
        except (ValueError,TypeError): pass
    return boundary.isoformat(timespec="microseconds").replace("+00:00","Z")

def _utcnow():
    return datetime.now(timezone.utc)

class BoundedForwardClock:
    """Wait for a backward host-clock step to settle without inventing time."""
    def __init__(self,clock,*,deadline,monotonic=time.monotonic,sleeper=time.sleep,last=None):
        self.clock=clock;self.deadline=deadline;self.monotonic=monotonic;self.sleeper=sleeper
        self.last=datetime.fromisoformat(last.replace('Z','+00:00')) if last else None
    def __call__(self):
        while True:
            value=self.clock()
            if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValidationError('Collection queue clock is invalid')
            if self.last is None or value>=self.last:
                self.last=value;return value
            remaining=self.deadline-self.monotonic()
            if remaining<=0:raise ResourceLimitError('Host clock recovery exceeded the original invocation deadline')
            self.sleeper(min(0.1,remaining))

def _clock(utcnow):
    from ..market.collection_universe import _utc
    value=utcnow()
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Collection queue clock is invalid")
    return _utc(value.isoformat())

def _read(path):
    if not path.exists(): return None
    return loads_strict(read_file(path,STATE_BOUND),max_bytes=STATE_BOUND)

def _state(root,provider):
    path=root/"ledger.json"
    state=_read(path)
    if state is None:
        state={"contract":"quant_data.collection_queue.v1","provider":provider,"usage":{},"units":{},"pending":None,"last_request_at":None,"provider_backoff":None}
    if isinstance(state,dict): state.setdefault("provider_backoff",None)
    if (not isinstance(state,dict) or state.get("contract")!="quant_data.collection_queue.v1"
        or state.get("provider")!=provider or set(state)!={"contract","provider","usage","units","pending","last_request_at","provider_backoff"}
        or not isinstance(state["usage"],dict) or not isinstance(state["units"],dict)):
        raise ConflictError("Collection provider ledger differs")
    if len(state["units"])>200000 or len(state["usage"])>3660:
        raise ResourceLimitError("Collection provider ledger exceeds bound")
    return state

def _save(root,state):
    from ..json_codec import dumps_strict
    raw=dumps_strict(state,max_bytes=STATE_BOUND).encode()
    if len(raw)>STATE_BOUND: raise ResourceLimitError("Collection provider ledger exceeds bound")
    atomic(root/"ledger.json",raw,replace=True)

def _response(root,key,unit):
    receipt=_read(root/"responses"/(key+".json"))
    if receipt is None: return None
    if (receipt.get("unit_id")!=unit.unit_id or receipt.get("request_id")!=unit.request_id
        or receipt.get("request")!=unit.request_material() or type(receipt.get("status")) is not int
        or not 200<=receipt["status"]<=599):
        raise ConflictError("Collection cached request differs")
    sha=receipt.get("content_sha256")
    import re
    if not isinstance(sha,str) or not re.fullmatch("[a-f0-9]{64}",sha):
        raise ConflictError("Collection cached digest is invalid")
    body=read_file(root/"blobs"/(sha+".json"),unit.max_response_bytes)
    if len(body)!=receipt["byte_count"] or hashlib.sha256(body).hexdigest()!=sha:
        raise ConflictError("Collection cached bytes differ")
    return receipt,body

def _retain(root,unit,response,secrets):
    from ..market.collection_universe import _utc
    if (not isinstance(response,QueueResponse) or isinstance(response.status,bool)
        or not isinstance(response.status,int) or not 200<=response.status<=599
        or response.redirected or not isinstance(response.body,bytes)
        or (response.status==200 and not response.body) or len(response.body)>unit.max_response_bytes):
        raise ValidationError("Collection response is not a bounded successful response")
    captured=_utc(response.captured_at)
    for value in secrets:
        if not isinstance(value,str) or not value:
            raise ValidationError("Collection credential redaction input is invalid")
        if value.encode() in response.body or quote(value,safe="").encode() in response.body:
            raise CredentialExposureError("Collection response echoed a credential")
    headers={}
    for k,v in response.headers:
        if not isinstance(k,str) or not isinstance(v,str) or len(v)>4096 or any(ord(c)<32 for c in v):
            raise ValidationError("Collection response header is invalid")
        if k.lower() in SAFE_HEADERS:
            if any(secret in v or quote(secret,safe="") in v for secret in secrets):
                raise CredentialExposureError("Collection response header echoed a credential")
            headers[k.lower()]=v
    sha=hashlib.sha256(response.body).hexdigest()
    receipt={"contract":"quant_data.collection_response.v1","unit_id":unit.unit_id,
        "request_id":unit.request_id,"request":unit.request_material(),"status":response.status,
        "content_sha256":sha,"byte_count":len(response.body),"captured_at":captured,"headers":headers}
    atomic(root/"blobs"/(sha+".json"),response.body)
    atomic(root/"responses"/(unit.unit_id+".json"),receipt)
    return receipt,response.body

def _isolatable_sec_failure(diagnostic):
    # A closed transport diagnostic, never arbitrary exception text or store errors.
    return (isinstance(diagnostic, dict)
        and set(diagnostic) == {"contract", "category", "http_status", "retryable"}
        and diagnostic["contract"] == "quant_data.provider_failure.v1"
        and diagnostic["category"] in {"network_error", "timeout", "response_too_large",
            "invalid_response", "unexpected_redirect", "unexpected_content_type"}
        and diagnostic["retryable"] is False
        and (diagnostic["http_status"] is None or
            (type(diagnostic["http_status"]) is int and 100 <= diagnostic["http_status"] <= 599
             and not provider_stop(diagnostic["http_status"], "sec"))))


def run_queue(*,root,plan,provider,fetch,publish,utcnow=_utcnow,monotonic=time.monotonic,
              sleeper=time.sleep,secret_values=(),acquire_only=False,resolve_retained=None,deadline=None,
              isolate_sec_transport_failures=False):
    """Reserve before GET, retain before publication, never retry uncertain GETs.

    The root is a private host path shared by this provider's expanded queues.
    Cross-store publication is deliberately not represented as atomic.
    """
    started=monotonic()
    if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline)):
        raise ValidationError("Collection absolute deadline is invalid")
    if type(acquire_only) is not bool or (acquire_only and publish is not None):
        raise ValidationError("Acquisition-only queues must not claim canonical publication")
    if (type(isolate_sec_transport_failures) is not bool or
            (isolate_sec_transport_failures and (provider != "sec" or not acquire_only))):
        raise ValidationError("SEC request isolation requires a SEC acquisition-only queue")
    if not isinstance(plan,CollectionPlan):
        raise ValidationError("Collection queue needs a frozen plan")
    if provider=="equibles":
        raise ValidationError("Equibles must use its existing shared checkpoint ledger")
    budgets=[b for b in plan.budgets if b.provider==provider]
    if len(budgets)!=1: raise ValidationError("Collection provider budget is missing")
    budget=validate_budget(budgets[0])
    units=tuple(validate_unit(u) for u in plan.units if u.provider==provider)
    if any(u.max_requests!=1 for u in units):
        raise ValidationError("Paged partitions require explicit child request units")
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValidationError("Collection queue root must be explicit and resolved")
    if not units:
        return {"outcome":"complete","requests":0,"published":0,"reused":0}
    plan_id=plan.plan_id
    effective_deadline=min(started+budget.max_run_seconds,deadline) if deadline is not None else started+budget.max_run_seconds
    last_request=None;requests=received=published=reused=0;acquired=0;failed=[];uncertain_bytes=0
    reused_units=dict(plan.reuse)
    blocked_units=dict(plan.blocked_units)
    with job_lock(root):
        for name in ("plans","attempts","responses","blobs","publications"):
            private_directory(root/name)
        atomic(root/"plans"/(plan_id+".json"),plan.material())
        state=_state(root,provider)
        def settle(receipt):
            pending=state["pending"]
            if (pending is None or pending["unit_id"]!=receipt["unit_id"]
                or pending["request_id"]!=receipt["request_id"]):
                raise ConflictError("Collection response lacks its original reservation")
            bucket=state["usage"][pending["day"]]
            bucket["received_bytes"]+=receipt["byte_count"]
            state["units"][pending["unit_id"]]={"status":"captured" if receipt["status"]==200 else "failed",
                "request_id":pending["request_id"],"http_status":receipt["status"]}
            if receipt["status"]==429:
                state["provider_backoff"]={"unit_id":receipt["unit_id"],"http_status":429,"not_before":_retry_at(receipt)}
            state["pending"]=None
            _save(root,state)
        def fail_pending(unit, http_status=None, diagnostic=None):
            nonlocal uncertain_bytes
            reservation = state["pending"]
            if (not isinstance(reservation, dict)
                    or reservation.get("unit_id") != unit.unit_id
                    or reservation.get("request_id") != unit.request_id
                    or digest(reservation.get("unit")) != digest(asdict(unit))):
                raise ConflictError("Collection uncertain reservation differs")
            day = reservation.get("day")
            bucket = state["usage"].get(day)
            if (not isinstance(bucket, dict) or type(reservation.get("attempt")) is not int
                    or reservation["attempt"] < 1
                    or bucket.get("charged_attempts") != reservation["attempt"]
                    or _utc_value(reservation.get("charged_at"))[:10] != day
                    or state["last_request_at"] != reservation["charged_at"]):
                raise ConflictError("Collection uncertain accounting differs")
            companion = root/"attempts"/(day+"-"+str(reservation["attempt"])+".json")
            if companion.exists() and digest(_read(companion)) != digest(reservation):
                raise ConflictError("Collection uncertain attempt evidence differs")
            private_directory(root/"failures")
            code = "sec_request_failed" if diagnostic is not None else "fmp_request_failed"
            detail = {"provider_failure": diagnostic} if diagnostic is not None else {}
            receipt = {"contract":"quant_data.collection_failed_request.v1",
                       **reservation, "error":code, "http_status":http_status,
                       "automatic_retry":False, "reserved_bytes":unit.max_response_bytes, **detail}
            atomic(root/"failures"/(unit.unit_id+".json"), receipt)
            # Unknown response bytes consume the full request bound, separately
            # from measured bytes. Never gain budget by failing a request.
            bucket["uncertain_bytes"] = bucket.get("uncertain_bytes", 0) + unit.max_response_bytes
            state["units"][unit.unit_id] = {"status":"failed", "request_id":unit.request_id,
                                           "error":code, "http_status":http_status, **detail}
            state["pending"] = None
            _save(root, state)
            uncertain_bytes += unit.max_response_bytes

        def exhausted(required=0):
            return monotonic()+required>=effective_deadline
        def bounded():
            return {"outcome":"invocation_budget","requests":requests,"received_bytes":received,
                "published":published,"reused":reused,"uncertain_bytes":uncertain_bytes,"failed_units":list(failed)}
        # Recover retained responses first; isolate abandoned FMP requests without retry.
        pending=state["pending"]
        if pending is not None:
            matching=next((u for u in units if u.unit_id==pending["unit_id"]),None)
            if matching is None:
                raw=pending.get("unit")
                if not isinstance(raw,dict): raise ConflictError("A pending provider request requires its original plan")
                raw={**raw,"parameters":tuple(tuple(x) for x in raw["parameters"]),"selected_symbols":tuple(raw["selected_symbols"])}
                matching=validate_unit(AcquisitionUnit(**raw))
                if matching.unit_id!=pending["unit_id"] or matching.provider!=provider:
                    raise ConflictError("Pending collection request identity differs")
            retained=_response(root,matching.unit_id,matching)
            if retained is None:
                if provider == "sec" and isolate_sec_transport_failures:
                    receipt = _read(root/"failures"/(matching.unit_id+".json"))
                    diagnostic = receipt.get("provider_failure") if isinstance(receipt, dict) else None
                    if not _isolatable_sec_failure(diagnostic):
                        raise ConflictError("A pending provider request has no retained response; no retry")
                    # The immutable receipt must exactly match the pending reservation.
                    fail_pending(matching, diagnostic["http_status"], diagnostic)
                elif provider == "fmp":
                    fail_pending(matching)
                else:
                    raise ConflictError("A pending provider request has no retained response; no retry")
                if not any(u.unit_id == matching.unit_id for u in units):
                    failed.append({"unit_id":matching.unit_id, **state["units"][matching.unit_id]})
            else:
                settle(retained[0])
            if retained is not None and provider_stop(retained[0]["status"], provider):
                return {"outcome":"provider_http_failure","requests":requests,"received_bytes":received,
                    "published":published,"reused":reused,"failed_units":[{"unit_id":matching.unit_id,"http_status":retained[0]["status"]}]}
        backoff=state["provider_backoff"]
        if backoff is not None and _clock(utcnow)<backoff["not_before"]:
            return {"outcome":"provider_http_failure","requests":0,"received_bytes":0,"published":0,"reused":0,
                "retry_at":backoff["not_before"],"failed_units":[backoff]}
        for unit in units:
            if exhausted(): return bounded()
            if unit.unit_id in blocked_units:
                return {"outcome":"retained_evidence_required","unit_id":unit.unit_id,
                    "requests":requests,"published":published,"reused":reused}
            existing=state["units"].get(unit.unit_id)
            if existing and existing["request_id"]!=unit.request_id:
                raise ConflictError("Collection unit identity drifted")
            publication=_read(root/"publications"/(unit.unit_id+".json"))
            if publication is not None and publication.get("request_id")!=unit.request_id:
                raise ConflictError("Collection publication receipt differs")
            if publication is not None and not acquire_only:
                if publication.get("request_id")!=unit.request_id:
                    raise ConflictError("Collection publication receipt differs")
                if existing is None or existing["status"]!="published":
                    state["units"][unit.unit_id]={"status":"published","request_id":unit.request_id}
                    _save(root,state)
                reused+=1;continue
            if existing and existing["status"]=="published" and not acquire_only:
                raise ConflictError("Collection publication receipt is missing")
            if existing and existing["status"]=="failed":
                failed.append({"unit_id":unit.unit_id,"http_status":existing.get("http_status"),
                               **({"error":existing["error"]} if "error" in existing else {}),
                               **({"provider_failure":existing["provider_failure"]} if "provider_failure" in existing else {})})
                if existing.get("http_status") is not None and provider_stop(existing["http_status"], provider):
                    return {"outcome":"provider_http_failure","requests":requests,"received_bytes":received,
                        "published":published,"reused":reused,"failed_units":failed}
                continue
            retained=_response(root,unit.unit_id,unit)
            if retained is None and unit.unit_id in reused_units:
                # Host publisher receives a typed reference; it must reverify the
                # original bytes and preserve the original capture timestamp.
                item=reused_units[unit.unit_id]
                if reusable(unit,(item,),cutoff=plan.created_at) is None:
                    raise ConflictError("Collection reuse differs from the exact request or cutoff")
                if acquire_only:
                    if resolve_retained is None:
                        raise ConflictError("Acquisition reuse requires the original response resolver")
                    response=resolve_retained(item)
                    if (not isinstance(response,QueueResponse) or response.status!=200
                        or _utc_value(response.captured_at)!=_utc_value(item.captured_at)
                        or hashlib.sha256(response.body).hexdigest()!=item.content_sha256):
                        raise ConflictError("Acquisition reuse differs from original response evidence")
                    retained=_retain(root,unit,response,tuple(secret_values))
                    state["units"][unit.unit_id]={"status":"captured","request_id":unit.request_id,
                        "retained_capture_id":item.capture_id}
                    _save(root,state);acquired+=1;reused+=1
                    continue
                try:
                    result=publish(unit=unit,retained=item,receipt=None,body=None)
                except PublicationDeferred:
                    if not exhausted():raise
                    return {**bounded(),"publication_status":"deferred_at_run_deadline"}
                if not isinstance(result,dict) or result.get("outcome") not in ("succeeded","unchanged","reused"):
                    raise ValidationError("Collection retained publication did not succeed")
                atomic(root/"publications"/(unit.unit_id+".json"),
                    {"unit_id":unit.unit_id,"request_id":unit.request_id,"result":result,"retained_capture_id":item.capture_id})
                state["units"][unit.unit_id]={"status":"published","request_id":unit.request_id}
                _save(root,state);reused+=1;continue
            if retained is None:
                if publication is not None or (existing and existing["status"] in ("captured","published")):
                    return {"outcome":"retained_evidence_required","unit_id":unit.unit_id,
                        "requests":requests,"published":published,"reused":reused}
                at=_clock(utcnow);day=at[:10]
                bucket=state["usage"].setdefault(day,{"charged_attempts":0,"received_bytes":0})
                if budget.daily_ceiling is not None and bucket["charged_attempts"]>=budget.daily_ceiling:
                    return {"outcome":"daily_budget","requests":requests,"received_bytes":received,"published":published,"reused":reused}
                delay=0 if last_request is None else max(0,budget.min_interval_milliseconds/1000-(monotonic()-last_request))
                if state["last_request_at"] is not None:
                    elapsed=(datetime.fromisoformat(at.replace("Z","+00:00"))-datetime.fromisoformat(
                        state["last_request_at"].replace("Z","+00:00"))).total_seconds()
                    if elapsed<0: raise ConflictError("Collection request clock moved backwards")
                    delay=max(delay,budget.min_interval_milliseconds/1000-elapsed)
                remaining=effective_deadline-monotonic()
                if (requests>=budget.max_requests or received+uncertain_bytes+unit.max_response_bytes>budget.max_total_bytes
                    or remaining<delay+unit.timeout_seconds):
                    return bounded()
                if delay: sleeper(delay)
                if exhausted(unit.timeout_seconds): return bounded()
                at=_clock(utcnow);day=at[:10]
                bucket=state["usage"].setdefault(day,{"charged_attempts":0,"received_bytes":0})
                if budget.daily_ceiling is not None and bucket["charged_attempts"]>=budget.daily_ceiling:
                    return {"outcome":"daily_budget","requests":requests,"received_bytes":received,"published":published,"reused":reused}
                reservation={"unit_id":unit.unit_id,"request_id":unit.request_id,"day":day,
                    "charged_at":at,"attempt":bucket["charged_attempts"]+1,"plan_id":plan_id,"unit":asdict(unit)}
                # State reservation is authoritative; a missing companion receipt
                # after interruption remains an uncertain charged request.
                bucket["charged_attempts"]+=1
                state["pending"]=reservation;state["last_request_at"]=at;_save(root,state)
                atomic(root/"attempts"/(day+"-"+str(reservation["attempt"])+".json"),reservation)
                requests+=1;last_request=monotonic()
                from .collection_provider_policy import FmpRequestNotDispatched, FmpGateFailure
                try:
                    response=fetch(unit=unit,timeout_seconds=unit.timeout_seconds,max_bytes=unit.max_response_bytes)
                except FmpRequestNotDispatched:
                    if provider != "fmp":
                        raise
                    # Preserve the charge and terminal unit; never retry it.
                    private_directory(root/"not-dispatched")
                    atomic(root/"not-dispatched"/(unit.unit_id+".json"),
                        {"contract":"quant_data.collection_not_dispatched.v1",
                         **reservation,"reason":"fmp_admission_blocked"})
                    state["units"][unit.unit_id]={"status":"failed","request_id":unit.request_id,
                                                "error":"fmp_not_dispatched"}
                    state["pending"]=None
                    _save(root,state)
                    return {"outcome":"provider_admission_blocked","requests":requests,
                            "received_bytes":received,"published":published,"reused":reused,
                            "failed_units":[*failed,{"unit_id":unit.unit_id,
                                                    "error":"fmp_not_dispatched"}]}
                except FmpGateFailure:
                    raise
                except Exception as error:
                    from .collection_transport import ProviderRequestFailure
                    if (isolate_sec_transport_failures and type(error) is ProviderRequestFailure
                            and _isolatable_sec_failure(error.diagnostic())):
                        diagnostic = error.diagnostic()
                        fail_pending(unit, error.http_status, diagnostic)
                        failed.append({"unit_id":unit.unit_id, "error":"sec_request_failed",
                            "http_status":error.http_status, "provider_failure":diagnostic})
                        continue
                    if provider == "fmp":
                        status=getattr(error,"http_status",None)
                        status=status if type(status) is int and 100<=status<=599 else None
                        fail_pending(unit, status)
                        failed.append({"unit_id":unit.unit_id,"error":"fmp_request_failed","http_status":status})
                        if status in (401,403,429):
                            return {**bounded(),"outcome":"provider_http_failure"}
                        continue
                    state["units"][unit.unit_id]={"status":"failed","request_id":unit.request_id}
                    _save(root,state)
                    raise ConflictError("Collection acquisition failed; charged reservation retained without retry") from None
                try:
                    retained=_retain(root,unit,response,tuple(secret_values))
                except (ValidationError, ResourceLimitError) as error:
                    if provider == "fmp" and isinstance(error, CredentialExposureError):
                        raise FmpGateFailure("Collection response failed credential safety") from None
                    if provider != "fmp":
                        state["units"][unit.unit_id]={"status":"failed","request_id":unit.request_id}
                        _save(root,state)
                        raise ConflictError("Collection acquisition failed; charged reservation retained without retry") from None
                    fail_pending(unit)
                    failed.append({"unit_id":unit.unit_id,"error":"fmp_request_failed"})
                    continue
                received+=len(retained[1])
                settle(retained[0])
            if retained[0]["status"]!=200:
                failed.append({"unit_id":unit.unit_id,"http_status":retained[0]["status"]})
                if provider_stop(retained[0]["status"], provider):
                    return {"outcome":"provider_http_failure","requests":requests,"received_bytes":received,
                        "published":published,"reused":reused,"failed_units":failed}
                continue
            if acquire_only:
                acquired+=1
                continue
            if exhausted(): return bounded()
            receipt,body=retained
            try:
                result=publish(unit=unit,retained=None,receipt=receipt,body=body)
            except PublicationDeferred:
                if not exhausted():raise
                return {**bounded(),"publication_status":"deferred_at_run_deadline"}
            if not isinstance(result,dict) or result.get("outcome") not in ("succeeded","unchanged","reused"):
                raise ValidationError("Collection publication did not succeed")
            atomic(root/"publications"/(unit.unit_id+".json"),
                {"unit_id":unit.unit_id,"request_id":unit.request_id,"result":result})
            state["units"][unit.unit_id]={"status":"published","request_id":unit.request_id}
            _save(root,state);published+=1
    result={"outcome":"complete_with_gaps" if failed else "complete","requests":requests,"received_bytes":received,"published":published,"reused":reused,"uncertain_bytes":uncertain_bytes,"failed_units":failed}
    if acquire_only:
        result.update(outcome="acquired_with_gaps" if failed else "acquired",acquired=acquired)
    return result
