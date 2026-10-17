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
from ..errors import ConflictError, ResourceLimitError, ValidationError, StoreUnavailableError
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


class FmpRequestNotDispatched(ConflictError):
    """The host admission gate rejected this request before the HTTP callback."""
    code = "fmp_not_dispatched"


class FmpRequestFailed(StoreUnavailableError):
    """One completed failed callback; no usable response and no automatic retry."""
    code = "fmp_request_failed"
    def __init__(self, http_status=None):
        self.http_status = http_status
        super().__init__("FMP request failed; attempt flagged without retry")


class FmpGateFailure(OSError):
    """Account persistence or credential-safety failure; do not isolate as HTTP."""
    code = "fmp_gate_failure"


class FmpAccountGate:
    def __init__(self,root,policy,*,clock=lambda:datetime.now(timezone.utc),monotonic=time.monotonic,sleeper=time.sleep):
        if not isinstance(policy,FmpAllowance):raise ValidationError("FMP account policy is invalid")
        self.root=Path(root);self.policy=policy.validate()
        if not self.root.is_absolute() or self.root.resolve()!=self.root:
            raise ValidationError("FMP allowance root must be explicit and resolved")
        self.clock=clock;self.monotonic=monotonic;self.sleeper=sleeper

    def reconcile_not_dispatched(self, *, expected_attempt, expected_state_sha256):
        """Explicit evidence-pinned repair of the old proven-unsent stop only.

        No provider/credential/store access, refunds, retry, or policy change.
        The caller must already have authorization for this exact repair.
        """
        import re
        if (not isinstance(expected_attempt, str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[1-9][0-9]*", expected_attempt) is None
                or not isinstance(expected_state_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_state_sha256) is None):
            raise ValidationError("FMP reconciliation identity is invalid")
        with job_lock(self.root):
            path = self.root/"allowance.json"
            before = read_file(path, MAX_LEDGER_BYTES)
            if hashlib.sha256(before).hexdigest() != expected_state_sha256:
                raise ConflictError("FMP reconciliation state changed")
            state = loads_strict(before, max_bytes=MAX_LEDGER_BYTES)
            stopped = state.get("stopped")
            reservation_path = self.root/"attempts"/(expected_attempt+".json")
            reservation_bytes = read_file(reservation_path, MAX_LEDGER_BYTES)
            reservation = loads_strict(reservation_bytes, max_bytes=MAX_LEDGER_BYTES)
            day, number = expected_attempt.rsplit("-", 1)
            if (state.get("contract") != "quant_data.fmp_account_allowance.v1"
                    or set(state) != {"contract","policy_sha256","usage","last_request_at","pending","stopped"}
                    or state["policy_sha256"] != self.policy.identity or state["pending"] is not None
                    or not isinstance(stopped, dict)
                    or set(stopped) != {"attempt","status","observed_at"}
                    or stopped["attempt"] != expected_attempt or stopped["status"] != "not_dispatched"
                    or set(reservation) != {"request_identity","priority","charged_at","attempt"}
                    or reservation["attempt"] != expected_attempt
                    or reservation["priority"] not in {"maintenance","backfill"}
                    or not isinstance(reservation["request_identity"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", reservation["request_identity"]) is None
                    or stopped["observed_at"] != reservation["charged_at"]
                    or state["last_request_at"] != reservation["charged_at"]
                    or _utc(reservation["charged_at"])[:10] != day
                    or state["usage"].get(day) != int(number)
                    or not 1 <= int(number) <= self.policy.daily_ceiling):
                raise ConflictError("FMP reconciliation is not a proven undispatched attempt")
            after = {**state, "stopped": None}
            after_bytes = dumps_strict(after, max_bytes=MAX_LEDGER_BYTES).encode()
            receipt = {"contract":"quant_data.fmp_not_dispatched_reconciliation.v1",
                       "attempt":expected_attempt,"before_sha256":expected_state_sha256,
                       "before_text":before.decode("utf-8"),
                       "after_sha256":hashlib.sha256(after_bytes).hexdigest(),
                       "reservation_sha256":hashlib.sha256(reservation_bytes).hexdigest(),
                       "provider_requests":0,"refunded_charges":0}
            private_directory(self.root/"reconciliations")
            # Preserve evidence before clearing stopped. If the final save fails,
            # the same pinned input can safely finish the exact same transition.
            atomic(self.root/"reconciliations"/(expected_attempt+".json"), receipt)
            atomic(path, after_bytes, replace=True)
            return receipt

    def _validate_state(self, state):
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


    def _abandon_pending(self, state, before):
        """Flag an orphan or failed request under the account lock; never refund."""
        import re
        self._validate_state(state)
        pending = state["pending"]
        if state["stopped"] is not None:
            raise ConflictError("FMP account has an explicit provider stop")
        if isinstance(pending, dict) and pending.get("contract") == "quant_data.fmp_parallel_pending.v1":
            return self._abandon_parallel(state, before)
        if (not isinstance(pending, dict)
                or set(pending) != {"request_identity", "priority", "charged_at", "attempt"}
                or not isinstance(pending["attempt"], str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[1-9][0-9]*", pending["attempt"]) is None
                or not isinstance(pending["request_identity"], str)
                or re.fullmatch(r"[0-9a-f]{64}", pending["request_identity"]) is None
                or pending["priority"] not in {"maintenance", "backfill"}):
            raise ConflictError("FMP pending reservation is invalid")
        day, number = pending["attempt"].rsplit("-", 1)
        if (_utc(pending["charged_at"])[:10] != day
                or state["last_request_at"] != pending["charged_at"]
                or state["usage"].get(day) != int(number)):
            raise ConflictError("FMP pending reservation accounting differs")
        reservation_path = self.root/"attempts"/(pending["attempt"]+".json")
        # A crash between ledger save and companion receipt leaves the ledger
        # authoritative. If the companion exists it must agree exactly.
        if reservation_path.exists() and loads_strict(
                read_file(reservation_path, MAX_LEDGER_BYTES)) != pending:
            raise ConflictError("FMP pending attempt evidence differs")
        after = {**state, "pending": None}
        after_bytes = dumps_strict(after, max_bytes=MAX_LEDGER_BYTES).encode()
        receipt = {"contract": "quant_data.fmp_uncertain_attempt.v1",
                   **pending, "outcome": "unknown", "automatic_retry": False,
                   "before_text": before.decode("utf-8"),
                   "before_sha256": hashlib.sha256(before).hexdigest(),
                   "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
                   "provider_requests": 0, "refunded_charges": 0}
        private_directory(self.root/"uncertain")
        atomic(self.root/"uncertain"/(pending["attempt"]+".json"), receipt)
        atomic(self.root/"allowance.json", after_bytes, replace=True)
        state["pending"] = None
        return receipt

    def _abandon_parallel(self, state, before):
        """Retire orphaned parallel admissions only while owning the account lock."""
        import re
        pending = state["pending"]
        attempts = pending.get("attempts")
        if (set(pending) != {"contract", "attempts"} or not isinstance(attempts, dict)
                or not 1 <= len(attempts) <= 100):
            raise ConflictError("FMP parallel pending reservation is invalid")
        seen = set()
        for key, reservation in attempts.items():
            if (not isinstance(reservation, dict)
                    or set(reservation) != {"request_identity","priority","charged_at","attempt","unit_id"}
                    or reservation["unit_id"] != key or not isinstance(key, str)
                    or re.fullmatch(r"[0-9a-f]{64}", key) is None
                    or not isinstance(reservation["request_identity"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", reservation["request_identity"]) is None
                    or not isinstance(reservation["attempt"], str)
                    or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[1-9][0-9]*", reservation["attempt"]) is None
                    or reservation["attempt"] in seen
                    or reservation["priority"] not in {"maintenance", "backfill"}):
                raise ConflictError("FMP parallel attempt identity differs")
            seen.add(reservation["attempt"])
            day, number = reservation["attempt"].rsplit("-", 1)
            if (_utc(reservation["charged_at"])[:10] != day
                    or _utc(state["last_request_at"]) < _utc(reservation["charged_at"])
                    or state["usage"].get(day, 0) < int(number)):
                raise ConflictError("FMP parallel attempt accounting differs")
            path = self.root/"attempts"/(reservation["attempt"]+".json")
            if path.exists() and loads_strict(read_file(path, MAX_LEDGER_BYTES)) != reservation:
                raise ConflictError("FMP parallel attempt evidence differs")
        after = {**state, "pending": None}
        after_bytes = dumps_strict(after, max_bytes=MAX_LEDGER_BYTES).encode()
        receipt = {"contract":"quant_data.fmp_uncertain_parallel.v1", "attempts":attempts,
                   "outcome":"unknown", "automatic_retry":False,
                   "before_text":before.decode("utf-8"),
                   "before_sha256":hashlib.sha256(before).hexdigest(),
                   "after_sha256":hashlib.sha256(after_bytes).hexdigest(),
                   "provider_requests":0, "refunded_charges":0}
        private_directory(self.root/"uncertain")
        atomic(self.root/"uncertain"/("parallel-"+receipt["before_sha256"]+".json"), receipt)
        atomic(self.root/"allowance.json", after_bytes, replace=True)
        state["pending"] = None
        return receipt

    def reconcile_pending(self, *, expected_attempt, expected_state_sha256):
        """Evidence-pinned, zero-request retirement of one uncertain admission."""
        with job_lock(self.root):
            before = read_file(self.root/"allowance.json", MAX_LEDGER_BYTES)
            if hashlib.sha256(before).hexdigest() != expected_state_sha256:
                raise ConflictError("FMP reconciliation state changed")
            state = loads_strict(before, max_bytes=MAX_LEDGER_BYTES)
            self._validate_state(state)
            if not isinstance(state["pending"], dict) or state["pending"].get("attempt") != expected_attempt:
                raise ConflictError("FMP reconciliation attempt changed")
            return self._abandon_pending(state, before)

    def invoke(self,operation,*,request_identity,priority,deadline):
        try:
            return self._invoke(operation,request_identity=request_identity,priority=priority,deadline=deadline)
        except OSError:
            raise FmpGateFailure("FMP account persistence or credential safety failed") from None

    def _invoke(self,operation,*,request_identity,priority,deadline):
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
            self._validate_state(state)
            if state["stopped"] is not None:
                raise ConflictError("FMP shared allowance requires prior-attempt reconciliation")
            if state["pending"] is not None:
                # Holding this lock proves the previous owner is no longer active.
                self._abandon_pending(state, read_file(path, MAX_LEDGER_BYTES))
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
                # Keep the conservative charge and terminal evidence, without
                # stopping independent future requests account-wide.
                private_directory(self.root/"not-dispatched")
                atomic(self.root/"not-dispatched"/(attempt+".json"),
                    {"contract":"quant_data.fmp_not_dispatched.v1",**reservation,
                     "reason":"deadline_before_dispatch"})
                state["pending"]=None
                save()
                raise ResourceLimitError("FMP allowance expired before dispatch")
            try:
                result=operation(remaining)
                status=getattr(result,"status",None)
                if type(status) is not int or not 200<=status<=599:
                    raise ValidationError("FMP account response lacks a valid HTTP status")
            except Exception as error:
                status=getattr(error,"http_status",None)
                status=status if type(status) is int and 100<=status<=599 else None
                credential_failure=getattr(error,"category",None)=="credential_exposure"
                try:
                    if status in (401,403,429) or credential_failure:
                        state["stopped"]={"attempt":attempt,"status":"credential_exposure" if credential_failure else status,
                                          "observed_at":_utc(self.clock().isoformat())}
                        state["pending"]=None;save()
                    else:
                        self._abandon_pending(state, read_file(path, MAX_LEDGER_BYTES))
                except Exception:
                    raise FmpGateFailure("FMP account failure evidence could not be saved") from None
                if credential_failure:
                    raise FmpGateFailure("FMP response failed credential safety") from None
                raise FmpRequestFailed(status) from None
            if status in (401,403,429):
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
        try:
            return operation(remaining)
        except FmpRequestNotDispatched:
            # A callback cannot manufacture proof that it was never entered.
            raise ConflictError("FMP dispatched callback failed") from None
    while True:
        try:
            return gate.invoke(dispatch,request_identity=identity,priority=priority,deadline=deadline)
        except ResourceLimitError as error:
            if dispatched:
                raise
            raise FmpRequestNotDispatched("FMP admission limit prevented dispatch") from error
        except ConflictError as error:
            # These two gate failures happen before reservation/dispatch. A
            # competing account request or a backward wall-clock step can settle
            # within this request's original deadline. Never repeat an operation
            # that entered the network seam, even if it raises the same message.
            if dispatched:
                raise
            if str(error) not in (
                "Equibles backfill is already running", "FMP allowance clock moved backward"):
                raise FmpRequestNotDispatched("FMP account gate blocked dispatch") from error
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise FmpRequestNotDispatched("FMP admission deadline prevented dispatch") from None
            time.sleep(min(0.1,remaining))
