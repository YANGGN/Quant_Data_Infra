"""One prepared account-wide FMP allowance for selected and legacy transports.

This ledger records attempted GETs independently of source publication progress.
Account limits and initial usage must be supplied by a reviewed activation.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import time
import math
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..market.collection_universe import _utc
from .equibles_transcript_backfill import job_lock, private_directory, read_file
from .collection_queue import atomic

MAX_LEDGER_BYTES=4*1024*1024
PROJECT_ROOT=Path("/home/volatility/Python_Projects/Quant_Data_Infra")


@dataclass(frozen=True)
class FmpAllowance:
    daily_ceiling: int
    maintenance_reserve: int
    min_interval_milliseconds: int
    effective_at: str
    initial_day_charged: int
    evidence_reference: str

    def validate(self):
        bounds=((self.daily_ceiling,1,1000000),(self.maintenance_reserve,0,self.daily_ceiling),
            (self.min_interval_milliseconds,1,60000),(self.initial_day_charged,0,self.daily_ceiling))
        if any(type(value) is not int or not low<=value<=high for value,low,high in bounds):
            raise ValidationError("FMP account allowance is invalid")
        _utc(self.effective_at)
        if (not isinstance(self.evidence_reference,str) or not 1<=len(self.evidence_reference)<=256
            or any(ord(c)<32 for c in self.evidence_reference)):
            raise ValidationError("FMP allowance requires its reviewed evidence reference")
        return self

    @property
    def identity(self):
        return hashlib.sha256(dumps_strict(asdict(self)).encode()).hexdigest()


class FmpAccountGate:
    def __init__(self,root,policy,*,clock=lambda:datetime.now(timezone.utc),monotonic=time.monotonic,sleeper=time.sleep):
        if not isinstance(policy,FmpAllowance):raise ValidationError("FMP account policy is invalid")
        self.root=Path(root);self.policy=policy.validate()
        if not self.root.is_absolute() or self.root.resolve()!=self.root:
            raise ValidationError("FMP allowance root must be explicit and resolved")
        self.clock=clock;self.monotonic=monotonic;self.sleeper=sleeper

    def invoke(self,operation,*,request_identity,priority,deadline):
        if (not isinstance(request_identity,str) or len(request_identity)!=64
            or any(c not in "0123456789abcdef" for c in request_identity)
            or priority not in ("maintenance","backfill") or not callable(operation)
            or type(deadline) not in (int,float) or not math.isfinite(deadline)):
            raise ValidationError("FMP account request is invalid")
        with job_lock(self.root):
            path=self.root/"allowance.json"
            at=_utc(self.clock().isoformat())
            if at<_utc(self.policy.effective_at):
                raise ConflictError("FMP request predates its allowance activation")
            if path.exists():
                state=loads_strict(read_file(path,MAX_LEDGER_BYTES),max_bytes=MAX_LEDGER_BYTES)
            else:
                state={"contract":"quant_data.fmp_account_allowance.v1","policy_sha256":self.policy.identity,
                    "usage":{_utc(self.policy.effective_at)[:10]:self.policy.initial_day_charged},
                    "last_request_at":None,"pending":None,"stopped":None}
            if (not isinstance(state,dict) or set(state)!={"contract","policy_sha256","usage","last_request_at","pending","stopped"}
                or state["contract"]!="quant_data.fmp_account_allowance.v1"
                or state["policy_sha256"]!=self.policy.identity or not isinstance(state["usage"],dict)
                or len(state["usage"])>3660):
                raise ConflictError("FMP shared allowance state differs")
            from datetime import date
            for day,charged in state["usage"].items():
                try:valid=date.fromisoformat(day).isoformat()==day
                except (TypeError,ValueError):valid=False
                if not valid or type(charged) is not int or not 0<=charged<=self.policy.daily_ceiling:
                    raise ConflictError("FMP shared quota history is invalid")
            if state["pending"] is not None or state["stopped"] is not None:
                raise ConflictError("FMP shared allowance requires prior-attempt reconciliation")
            def save():
                raw=dumps_strict(state,max_bytes=MAX_LEDGER_BYTES).encode()
                atomic(path,raw,replace=True)
            delay=0
            if state["last_request_at"] is not None:
                elapsed=(datetime.fromisoformat(at.replace("Z","+00:00"))-
                    datetime.fromisoformat(_utc(state["last_request_at"]).replace("Z","+00:00"))).total_seconds()
                if elapsed<0:raise ConflictError("FMP allowance clock moved backward")
                delay=max(0,self.policy.min_interval_milliseconds/1000-elapsed)
            if self.monotonic()+delay>=deadline:
                raise ResourceLimitError("FMP allowance wait exceeds the request deadline")
            if delay:self.sleeper(delay)
            at=_utc(self.clock().isoformat());day=at[:10]
            if self.monotonic()>=deadline:
                raise ResourceLimitError("FMP allowance request deadline expired")
            used=state["usage"].get(day,0)
            if type(used) is not int or not 0<=used<=self.policy.daily_ceiling:
                raise ConflictError("FMP shared charged usage is invalid")
            ceiling=self.policy.daily_ceiling-(self.policy.maintenance_reserve if priority=="backfill" else 0)
            if used>=ceiling:
                raise ResourceLimitError("FMP account allowance reserved or exhausted")
            attempt=day+"-"+str(used+1)
            reservation={"request_identity":request_identity,"priority":priority,"charged_at":at,"attempt":attempt}
            private_directory(self.root/"attempts")
            state["usage"][day]=used+1;state["pending"]=reservation;state["last_request_at"]=at
            save()
            atomic(self.root/"attempts"/(attempt+".json"),reservation)
            # The caller's source queue still owns response retention and replay.
            # A crash here leaves a charged, uncertain attempt; never refund it.
            remaining=math.floor(deadline-self.monotonic())
            if remaining<1:
                state["pending"]=None
                state["stopped"]={"attempt":attempt,"status":"not_dispatched","observed_at":at}
                save()
                raise ResourceLimitError("FMP allowance expired before dispatch")
            result=operation(remaining)
            status=getattr(result,"status",None)
            if type(status) is not int or not 200<=status<=599:
                raise ValidationError("FMP account response lacks a valid HTTP status")
            if status in (401,403,429) or status>=500:
                state["stopped"]={"attempt":attempt,"status":status,"observed_at":_utc(self.clock().isoformat())}
            state["pending"]=None;save()
            return result


def host_allowance():
    """Prepared configuration adds no ledger, sleep, or provider effect."""
    from ..market.collection_bindings import parse_bindings
    path=PROJECT_ROOT/"config/collection_bindings.json"
    if not path.is_file():return None
    body=path.read_bytes()
    bindings=parse_bindings(body)
    raw=loads_strict(body,max_bytes=65536)
    selected=any(b.mode=="active" and b.provider=="fmp" for b in bindings.values())
    entry=raw.get("provider_allowances",{}).get("fmp")
    if entry is None or entry.get("mode")!="active":
        if selected:raise ConflictError("Selected FMP activation requires the shared account allowance")
        return None
    if set(entry)!={"mode","policy"} or not isinstance(entry["policy"],dict):
        raise ValidationError("FMP host allowance declaration is invalid")
    try:policy=FmpAllowance(**entry["policy"]).validate()
    except TypeError:raise ValidationError("FMP host allowance policy is invalid") from None
    return FmpAccountGate(PROJECT_ROOT/"data/.operations/provider-allowances/fmp",policy)


def invoke_host_fmp(operation,*,request_material,priority,timeout_seconds):
    """Request material must exclude credentials; it is hashed before retention."""
    gate=host_allowance()
    if gate is None:return operation(timeout_seconds)
    if type(timeout_seconds) is not int or timeout_seconds<1:
        raise ValidationError("FMP request timeout is invalid")
    def safe(value,depth=0):
        if depth>8:return False
        if isinstance(value,dict):
            return all(isinstance(key,str) and key.lower() not in (
                "apikey","api_key","authorization","token","headers","cookie")
                and safe(child,depth+1) for key,child in value.items())
        if isinstance(value,(tuple,list)):return all(safe(child,depth+1) for child in value)
        return value is None or isinstance(value,(str,int,bool))
    if not isinstance(request_material,dict) or not safe(request_material):
        raise ValidationError("FMP shared request identity contains a credential field")
    identity=hashlib.sha256(dumps_strict(request_material).encode()).hexdigest()
    deadline=time.monotonic()+timeout_seconds
    dispatched=False
    def dispatch(remaining):
        nonlocal dispatched
        dispatched=True
        return operation(remaining)
    while True:
        try:
            return gate.invoke(dispatch,request_identity=identity,priority=priority,deadline=deadline)
        except ConflictError as error:
            # These two gate failures happen before reservation/dispatch. A
            # competing account request or a backward wall-clock step can settle
            # within this request's original deadline. Never repeat an operation
            # that entered the network seam, even if it raises the same message.
            if dispatched or str(error) not in (
                "Equibles backfill is already running", "FMP allowance clock moved backward"):
                raise
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise ResourceLimitError("FMP account slot wait exceeded its original deadline") from None
            time.sleep(min(0.1,remaining))
