"""Additive private monitor extension; v1 operational tables remain byte-for-byte unchanged."""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
import json,sqlite3
from quant_data.stores import StoreWriteLock
from quant_data.errors import ConflictError
from .store import MonitorStore
from .model import canonical
from .baseline import validate_day

DDL="""
CREATE TABLE baseline_metadata(version INTEGER NOT NULL, checksum TEXT NOT NULL);
CREATE TABLE baseline_days(symbol TEXT NOT NULL,session TEXT NOT NULL,payload TEXT NOT NULL,
 digest TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(symbol,session));
CREATE INDEX baseline_sessions ON baseline_days(session);
CREATE TABLE baseline_runs(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE baseline_requests(symbol TEXT NOT NULL,session TEXT NOT NULL,part TEXT NOT NULL,
 run_id TEXT NOT NULL,started_at TEXT NOT NULL,outcome TEXT NOT NULL,payload TEXT,receipt TEXT,
 PRIMARY KEY(symbol,session,part));
"""
CHECKSUM=sha256(DDL.encode()).hexdigest()
RETRY_POLICY={"version":1,"max_attempts":3,"retry_codes":["DEADLINE_EXCEEDED","UNAVAILABLE"],
              "delays_seconds":[5,30],"circuit_max_seconds":300}
SESSION_POLICY={"version":1,"retry_codes":["SESSION_TIMEOUT","SESSION_CONNECTION_ERROR","SESSION_SETUP_NOT_DISPATCHED"],
                "delays_seconds":[30,120],"max_attempts":3}
INTERNAL_POLICY={"version":1,"retry_codes":["INTERNAL"]}
RESILIENT_POLICY={"version":1,"max_attempts":20,"central_session_recovery":True,
                  "connection_max_attempts":20,"connection_delays_seconds":[30,60,120,300]}
CONNECTION_TRANSIENT=("SESSION_TIMEOUT","SESSION_CONNECTION_ERROR","SESSION_INTERRUPTED")
DAYTIME_POLICY={"version":1,"daytime_concurrency":1,"offhours_concurrency":4,
                "live_priority":"exclusive_cycle","timezone":"America/New_York",
                "daytime_start":"08:55","daytime_end":"16:15"}
def request_fingerprint(row):
    fields=("symbol","session","part","run_id","started_at","outcome","payload","receipt")
    return sha256(canonical({k:row[k] for k in fields}).encode()).hexdigest()

def session_amendment(c,run_id):
    row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("session-recovery:"+run_id,)).fetchone()
    if row is None:return None
    value=json.loads(row[0])
    if set(value)!={"policy","reconciliations","evidence_sha256"} or value["policy"]!=SESSION_POLICY:
        raise ValueError("baseline_session_policy_conflict")
    return value

def interruption_amendment(c,run_id):
    """Only explicitly selected, immutable interrupted admissions may omit unknown bytes."""
    row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("interrupted-recovery:"+run_id,)).fetchone()
    if row is None:return None
    value=json.loads(row[0])
    validate_interruption(c,run_id,value)
    return value

def validate_interruption(c,run_id,value):
    if (not isinstance(value,dict) or set(value)!={"version","requests","evidence_sha256"}
            or type(value["version"]) is not int or value["version"]!=1
            or not isinstance(value["requests"],list) or not 1<=len(value["requests"])<=4):
        raise ValueError("baseline_interruption_policy_conflict")
    evidence=value["evidence_sha256"]
    if not isinstance(evidence,str) or len(evidence)!=64 or any(ch not in "0123456789abcdef" for ch in evidence):
        raise ValueError("baseline_interruption_evidence")
    plan_row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(run_id,)).fetchone()
    if not plan_row:raise ValueError("baseline_run_missing")
    plan=json.loads(plan_row[0]);sessions={d["session"] for d in plan["sessions"]}
    policy=effective_recovery(c,run_id)
    if not policy or policy["max_attempts"]<2:raise ValueError("baseline_recovery_policy_missing")
    seen=set()
    for item in value["requests"]:
        if (not isinstance(item,dict) or set(item)!={"symbol","session","part","request_sha256"}
                or not all(isinstance(v,str) for v in item.values())):
            raise ValueError("baseline_interruption_scope")
        key=(item["symbol"],item["session"],item["part"])
        if key in seen:raise ValueError("baseline_interruption_duplicate")
        seen.add(key)
        # This exception covers original expiry requests only, never catalogs or later retries.
        if (key[0] not in plan["symbols"] or key[1] not in sessions
                or len(key[2])!=10 or key[2]<key[1]):
            raise ValueError("baseline_interruption_scope")
        try:datetime.strptime(key[2],"%Y-%m-%d")
        except ValueError:raise ValueError("baseline_interruption_scope") from None
        parent=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",key).fetchone()
        if (parent is None or parent["run_id"]!=run_id or parent["outcome"]!="started"
                or parent["receipt"] is not None or parent["payload"] is not None
                or request_fingerprint(parent)!=item["request_sha256"]):
            raise ValueError("baseline_interruption_parent_changed")

def approved_interruption(row,amendment):
    if amendment is None:return False
    for item in amendment["requests"]:
        if (row["symbol"],row["session"],row["part"])==(item["symbol"],item["session"],item["part"]):
            if request_fingerprint(row)!=item["request_sha256"]:
                raise ValueError("baseline_interruption_parent_changed")
            return True
    return False

def auth_retry_once(c,symbol,session,part):
    """Resolve one explicitly approved cross-run auth retry without changing its parent."""
    row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("auth-retry-once:"+symbol+":"+session+":"+part,)).fetchone()
    if row is None:return None
    value=json.loads(row[0]);validate_auth_retry(c,value,symbol,session,part)
    return value

def validate_auth_retry(c,value,symbol,session,part):
    fields={"version","run_id","source_run_id","symbol","session","part","after_sha256","evidence_sha256","attempt"}
    if (not isinstance(value,dict) or set(value)!=fields or type(value["version"]) is not int or value["version"]!=1
            or type(value["attempt"]) is not int or value["attempt"]!=2
            or any(not isinstance(value[k],str) for k in fields-{"version","attempt"})
            or (value["symbol"],value["session"],value["part"])!=(symbol,session,part)):
        raise ValueError("baseline_auth_retry_policy_conflict")
    for name in ("after_sha256","evidence_sha256"):
        if len(value[name])!=64 or any(x not in "0123456789abcdef" for x in value[name]):
            raise ValueError("baseline_auth_retry_evidence")
    if len(part)!=10 or part<session:raise ValueError("baseline_auth_retry_scope")
    try:datetime.strptime(part,"%Y-%m-%d")
    except ValueError:raise ValueError("baseline_auth_retry_scope") from None
    for run in (value["run_id"],value["source_run_id"]):
        row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(run,)).fetchone()
        if row is None:raise ValueError("baseline_run_missing")
        plan=json.loads(row[0])
        if symbol not in plan["symbols"] or session not in {x["session"] for x in plan["sessions"]}:
            raise ValueError("baseline_auth_retry_scope")
    policy=effective_recovery(c,value["run_id"])
    if not policy or policy["max_attempts"]<2:raise ValueError("baseline_recovery_policy_missing")
    parent=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",(symbol,session,part)).fetchone()
    if (parent is None or parent["run_id"]!=value["source_run_id"] or parent["outcome"]!="ProviderFailure"
            or parent["payload"] is not None or request_fingerprint(parent)!=value["after_sha256"]):
        raise ValueError("baseline_auth_retry_parent_changed")
    receipt=json.loads(parent["receipt"]) if parent["receipt"] else {}
    if (receipt.get("outcome")!="UNAUTHENTICATED" or receipt.get("method")!="option_history_ohlc"
            or type(receipt.get("response_bytes")) is not int or receipt["response_bytes"]<0):
        raise ValueError("baseline_auth_retry_ineligible")

def effective_recovery(c,run_id):
    row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("recovery:"+run_id,)).fetchone()
    if row is None:return None
    policy=json.loads(row[0])
    if policy!=RETRY_POLICY:raise ValueError("baseline_recovery_policy_conflict")
    amendment=session_amendment(c,run_id)
    if amendment:
        policy={**policy,"retry_codes":policy["retry_codes"]+SESSION_POLICY["retry_codes"],
                "session_delays_seconds":SESSION_POLICY["delays_seconds"]}
    internal=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("internal-recovery:"+run_id,)).fetchone()
    if internal:
        if json.loads(internal[0])!=INTERNAL_POLICY:raise ValueError("baseline_internal_policy_conflict")
        policy={**policy,"retry_codes":policy["retry_codes"]+INTERNAL_POLICY["retry_codes"]}
    resilient=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("resilient-recovery:"+run_id,)).fetchone()
    if resilient:
        if json.loads(resilient[0])!=RESILIENT_POLICY:raise ValueError("baseline_resilient_policy_conflict")
        policy={**policy,**RESILIENT_POLICY}
    return policy

def connection_state(c,run_id):
    """Append-only authentication admissions, separate from contract attempts."""
    prefix="connection-start:"+run_id+":"
    starts=dict(c.execute("SELECT id,payload FROM baseline_runs WHERE id LIKE ? ORDER BY id",(prefix+"%",)))
    end_prefix="connection-finish:"+run_id+":"
    ends=dict(c.execute("SELECT id,payload FROM baseline_runs WHERE id LIKE ?",(end_prefix+"%",)))
    if starts or ends:
        policy=effective_recovery(c,run_id)
        if not policy or not policy.get("central_session_recovery"):raise ValueError("baseline_connection_policy_missing")
    failures=0;outcome=None;when=None
    for sequence,(key,raw) in enumerate(starts.items(),1):
        if key!=prefix+f"{sequence:06d}":raise ValueError("baseline_connection_sequence")
        value=json.loads(raw)
        if set(value)!={"version","started_at"} or value["version"]!=1:raise ValueError("baseline_connection_record")
        when=value["started_at"]
        if datetime.fromisoformat(when).tzinfo is None:raise ValueError("baseline_connection_clock")
        end=ends.pop(end_prefix+f"{sequence:06d}",None)
        if end is None:
            # Owner loss before finishing login cannot have dispatched option
            # data: workers start only after the successful completion receipt.
            outcome="SESSION_INTERRUPTED";failures+=1
        else:
            end=json.loads(end)
            if (set(end)!={"version","started_sha256","finished_at","outcome"} or end["version"]!=1
                or end["started_sha256"]!=sha256(raw.encode()).hexdigest()
                or not isinstance(end["outcome"],str)):
                raise ValueError("baseline_connection_record")
            when=end["finished_at"]
            if datetime.fromisoformat(when).tzinfo is None:raise ValueError("baseline_connection_clock")
            outcome=end["outcome"]
            failures=0 if outcome=="success" else failures+1
        if failures>=RESILIENT_POLICY["connection_max_attempts"] and sequence<len(starts):
            raise ValueError("baseline_connection_limit_bypassed")
        if outcome not in (None,"success",*CONNECTION_TRANSIENT) and sequence<len(starts):
            raise ValueError("baseline_connection_terminal_bypassed")
    if ends:raise ValueError("baseline_connection_without_start")
    return {"admissions":len(starts),"consecutive_failures":failures,"last_outcome":outcome,"last_at":when,
            "pending":outcome not in (None,"success"),
            "blocked":failures>=RESILIENT_POLICY["connection_max_attempts"] or outcome not in (None,"success",*CONNECTION_TRANSIENT)}

def reconciled_setup(c,row,amendment=None):
    amendment=amendment if amendment is not None else session_amendment(c,row["run_id"])
    if amendment is None:return False
    for item in amendment["reconciliations"]:
        if (item["symbol"],item["session"],item["part"])==(row["symbol"],row["session"],row["part"]):
            if request_fingerprint(row)!=item["request_sha256"]:raise ValueError("baseline_reconciliation_changed")
            return True
    return False

def attempt_part(part,attempt):
    return part if attempt==1 else f"{part}#retry{attempt}"
def logical_part(part):
    return part.rsplit("#retry",1)[0]
def attempt_state(c,symbol,session,part):
    names=[attempt_part(part,n) for n in range(1,22)]
    rows={r["part"]:dict(r) for r in c.execute(
        "SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part IN ("+",".join("?" for _ in names)+")",
        (symbol,session,*names))}
    if not rows:return None
    original=rows.get(part)
    if original is None:raise ValueError("baseline_retry_without_original")
    auth_grant=auth_retry_once(c,symbol,session,part)
    if auth_grant and any(name in rows for name in names[2:]):raise ValueError("baseline_auth_retry_attempt_limit")
    extra=False
    grant=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("setup-retry-once:"+original["run_id"],)).fetchone()
    if grant:
        grant=json.loads(grant[0])
        if set(grant)!={"version","symbol","session","part","after_sha256","attempt"} or grant["version"]!=1 or grant["attempt"]!=4:
            raise ValueError("baseline_setup_retry_policy_conflict")
        if (symbol,session,part)==(grant["symbol"],grant["session"],grant["part"]):
            parent=rows.get(names[2])
            if parent is None or parent["run_id"]!=original["run_id"] or request_fingerprint(parent)!=grant["after_sha256"]:
                raise ValueError("baseline_setup_retry_parent_changed")
            extra=True
    policy=effective_recovery(c,original["run_id"])
    interrupted=interruption_amendment(c,original["run_id"])
    storage_retry=None
    storage_key="storage-retry:"+original["run_id"]+":"+symbol+":"+session+":"+part
    grant_row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(storage_key,)).fetchone()
    if grant_row:
        grant_value=json.loads(grant_row[0])
        if (set(grant_value)!={"version","attempt","after_sha256","evidence_sha256"}
                or grant_value["version"]!=1 or type(grant_value["attempt"]) is not int
                or not policy or not 2<=grant_value["attempt"]<=policy["max_attempts"]):
            raise ValueError("baseline_storage_retry_policy_conflict")
        parent=rows.get(attempt_part(part,grant_value["attempt"]-1))
        if (parent is None or parent["run_id"]!=original["run_id"]
                or request_fingerprint(parent)!=grant_value["after_sha256"]
                or not storage_failure(parent)):
            raise ValueError("baseline_storage_retry_parent_changed")
        evidence=grant_value["evidence_sha256"]
        if not isinstance(evidence,str) or len(evidence)!=64 or any(x not in "0123456789abcdef" for x in evidence):
            raise ValueError("baseline_storage_retry_evidence")
        storage_retry=grant_value["attempt"]-1
    limit=policy["max_attempts"] if policy else 3
    for attempt,name in enumerate(names,1):
        if name in rows and attempt>3:
            if (attempt>max(limit,4 if extra else 3) or rows[name]["run_id"]!=original["run_id"]):
                raise ValueError("baseline_setup_retry_unauthorized")
    for attempt in range(20,0,-1):
        if names[attempt-1] in rows:
            value=rows[names[attempt-1]]
            adjusted=reconciled_setup(c,value)
            value["interrupted_retry"]=approved_interruption(value,interrupted)
            if auth_grant:
                if attempt==2 and value["run_id"]!=auth_grant["run_id"]:raise ValueError("baseline_auth_retry_owner_changed")
                value["auth_retry_once"]=auth_grant
            value["attempt"]=attempt;value["original_run_id"]=original["run_id"]
            value["extra_setup_retry"]=extra and attempt==3
            value["storage_retry"]=storage_retry==attempt
            value["payload"]=json.loads(value["payload"]) if value["payload"] is not None else None
            value["receipt"]=json.loads(value["receipt"]) if value["receipt"] is not None else None
            if adjusted:
                value["outcome"]="ProviderFailure"
                value["receipt"]={**value["receipt"],"response_bytes":0,"outcome":"SESSION_SETUP_NOT_DISPATCHED",
                                  "stage":"session_setup","data_dispatched":False}
            return value
def storage_failure(row):
    """Only a fully measured successful response lost at the local save boundary."""
    receipt=json.loads(row["receipt"]) if isinstance(row["receipt"],str) else row["receipt"]
    digest=(receipt or {}).get("response_sha256")
    return bool(row["outcome"]=="ConflictError" and row["payload"] is None and receipt
        and receipt.get("outcome")=="success"
        and receipt.get("method") in ("option_history_ohlc","option_list_contracts")
        and type(receipt.get("response_bytes")) is int and receipt["response_bytes"]>=0
        and type(receipt.get("rows")) is int and receipt["rows"]>0
        and isinstance(digest,str) and len(digest)==64
        and all(x in "0123456789abcdef" for x in digest))

def blocked_setup(state,run_id,policy):
    receipt=state.get("receipt") or {}
    return (state["original_run_id"]==run_id and state["run_id"]==run_id
            and state["outcome"]!="success" and receipt.get("stage")=="session_setup"
            and receipt.get("data_dispatched") is False and not retryable(state,run_id,policy))

def retryable(state,run_id,policy):
    if state.get("auth_retry_once"):
        grant=state["auth_retry_once"]
        return bool(policy and run_id==grant["run_id"] and state["attempt"]==1
                    and state["original_run_id"]==grant["source_run_id"] and state["run_id"]==grant["source_run_id"]
                    and state["outcome"]=="ProviderFailure")
    if state.get("interrupted_retry") is True:
        return bool(policy and state["original_run_id"]==run_id and state["run_id"]==run_id
                    and state["attempt"]==1 and state["attempt"]<policy["max_attempts"]
                    and state["outcome"]=="started" and state["receipt"] is None and state["payload"] is None)
    receipt=state.get("receipt") or {}
    count=receipt.get("response_bytes")
    return bool(policy and state["original_run_id"]==run_id and state["run_id"]==run_id
                and (state["outcome"]=="ProviderFailure" or (state["outcome"]=="ConflictError" and state.get("storage_retry") is True))
                and (state["attempt"]<policy["max_attempts"] or state.get("extra_setup_retry") is True)
                and (receipt.get("outcome") in policy["retry_codes"] or state.get("storage_retry") is True)
                and type(count) is int and count>=0
                and (not receipt.get("outcome","").startswith("SESSION_") or
                     (receipt.get("stage")=="session_setup" and receipt.get("data_dispatched") is False and count==0)))


def check_extension(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='baseline_metadata'").fetchone():return False
    if [tuple(r) for r in c.execute("SELECT version,checksum FROM baseline_metadata")]!=[(1,CHECKSUM)]:
        raise ValueError("baseline_schema_checksum")
    return True

class BaselineStore(MonitorStore):
    def initialize_baseline(self):
        self.initialize()
        with StoreWriteLock(self.path):
            with self.read() as c:
                if check_extension(c):return False
            c=sqlite3.connect(self.path)
            try:
                c.executescript("BEGIN IMMEDIATE;"+DDL)
                c.execute("INSERT INTO baseline_metadata VALUES(1,?)",(CHECKSUM,));c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
        return True
    def create_run(self,plan):
        payload=canonical(plan);identity=sha256(payload.encode()).hexdigest()[:24]
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(identity,payload))
        return identity
    def plan(self,identity):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            r=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(identity,)).fetchone()
            if not r:raise ValueError("baseline_run_missing")
            return json.loads(r[0])
    def configure_recovery(self,run_id):
        # An immutable amendment preserves the original manifest and receipts.
        self.plan(run_id)
        key="recovery:"+run_id;payload=canonical(RETRY_POLICY)
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(key,payload))
        if self.plan(key)!=RETRY_POLICY:raise ValueError("baseline_recovery_policy_conflict")
        return RETRY_POLICY.copy()
    def recovery_policy(self,run_id):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            return effective_recovery(c,run_id)
    def configure_internal_recovery(self,run_id):
        """Opt this finite run into known-byte INTERNAL RPC recovery.

        Call under the backfill owner lock after explicit authorization. Existing
        attempts, delays, caps, deadline and unknown-transfer behavior still apply.
        The original manifest, policies and request receipts remain immutable.
        """
        self.plan(run_id)
        if self.recovery_policy(run_id) is None:raise ValueError("baseline_recovery_policy_missing")
        key="internal-recovery:"+run_id
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(key,canonical(INTERNAL_POLICY)))
        if self.plan(key)!=INTERNAL_POLICY:raise ValueError("baseline_internal_policy_conflict")
        return self.recovery_policy(run_id)
    def configure_resilient_recovery(self,run_id):
        """Explicit twenty-attempt amendment; caller holds the finite owner lock."""
        self.plan(run_id)
        if self.recovery_policy(run_id) is None:raise ValueError("baseline_recovery_policy_missing")
        key="resilient-recovery:"+run_id
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(key,canonical(RESILIENT_POLICY)))
        if self.plan(key)!=RESILIENT_POLICY:raise ValueError("baseline_resilient_policy_conflict")
        return self.recovery_policy(run_id)
    def configure_storage_retry(self,run_id,symbol,session,part,*,evidence_sha256):
        """Append one hash-pinned retry grant for a measured local save failure."""
        plan=self.plan(run_id)
        if symbol not in plan["symbols"] or session not in {d["session"] for d in plan["sessions"]}:
            raise ValueError("baseline_storage_retry_scope")
        if (not isinstance(evidence_sha256,str) or len(evidence_sha256)!=64
                or any(x not in "0123456789abcdef" for x in evidence_sha256)):
            raise ValueError("baseline_storage_retry_evidence")
        key="storage-retry:"+run_id+":"+symbol+":"+session+":"+part
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            existing=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(key,)).fetchone()
            policy=effective_recovery(c,run_id)
            state=attempt_state(c,symbol,session,part)
            if existing:
                value=json.loads(existing[0])
                if value["evidence_sha256"]!=evidence_sha256:raise ValueError("baseline_storage_retry_policy_conflict")
                return value
            if (not state or not policy or state["run_id"]!=run_id or state["original_run_id"]!=run_id
                    or state["attempt"]>=policy["max_attempts"] or not storage_failure(state)):
                raise ValueError("baseline_storage_retry_forbidden")
            row=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",
                (symbol,session,state["part"])).fetchone()
            value={"version":1,"attempt":state["attempt"]+1,"after_sha256":request_fingerprint(row),
                "evidence_sha256":evidence_sha256}
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(key,canonical(value)))
        if self.plan(key)!=value:raise ValueError("baseline_storage_retry_policy_conflict")
        return value

    def connection_state(self,run_id):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:return connection_state(c,run_id)
    def reserve_connection(self,run_id,when):
        """One central login admission, charged within the original global cap."""
        plan=self.plan(run_id)
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                policy=effective_recovery(c,run_id)
                if not policy or not policy.get("central_session_recovery"):raise ValueError("baseline_connection_policy_missing")
                state=connection_state(c,run_id)
                if state["blocked"]:raise ValueError("baseline_connection_recovery_exhausted")
                used=c.execute("SELECT COUNT(*) FROM baseline_requests WHERE run_id=?",(run_id,)).fetchone()[0]+state["admissions"]
                if used>=plan["max_requests"]:raise RuntimeError("request_cap")
                stamp=datetime.fromisoformat(when)
                if stamp.tzinfo is None or stamp>=datetime.fromisoformat(plan["deadline"]):raise RuntimeError("duration_cap")
                if state["pending"]:
                    delays=policy["connection_delays_seconds"]
                    delay=delays[min(state["consecutive_failures"]-1,len(delays)-1)]
                    from datetime import timedelta
                    if stamp<datetime.fromisoformat(state["last_at"])+timedelta(seconds=delay):
                        raise ValueError("baseline_connection_cooldown")
                sequence=state["admissions"]+1
                value=canonical({"version":1,"started_at":when})
                c.execute("INSERT INTO baseline_runs VALUES(?,?)",("connection-start:"+run_id+f":{sequence:06d}",value))
                c.commit();return sequence
            except BaseException:c.rollback();raise
            finally:c.close()
    def finish_connection(self,run_id,sequence,outcome,when):
        allowed=("success",*CONNECTION_TRANSIENT,"AUTHENTICATION_REJECTED","SESSION_SETUP_FAILED","duration_cap","baseline_stopped")
        if outcome not in allowed:outcome="SESSION_SETUP_FAILED"
        key="connection-start:"+run_id+f":{sequence:06d}"
        start=canonical(self.plan(key))
        value={"version":1,"started_sha256":sha256(start.encode()).hexdigest(),"finished_at":when,"outcome":outcome}
        end_key="connection-finish:"+run_id+f":{sequence:06d}"
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(end_key,canonical(value)))
        if self.plan(end_key)!=value:raise ValueError("baseline_connection_receipt_conflict")
        return self.connection_state(run_id)
    def configure_session_recovery(self,run_id,reconciliations,*,evidence_sha256):
        """Explicit, append-only correction for reviewed pre-dispatch failures.

        Call only under the backfill owner lock. Original request rows, attempt
        counts, manifest, schema and caps are preserved; this grants no new units.
        """
        if not isinstance(evidence_sha256,str) or len(evidence_sha256)!=64 or any(ch not in "0123456789abcdef" for ch in evidence_sha256):
            raise ValueError("baseline_reconciliation_evidence")
        if not isinstance(reconciliations,list) or len(reconciliations)>4:
            raise ValueError("baseline_reconciliation_scope")
        plan=self.plan(run_id)
        value={"policy":SESSION_POLICY,"reconciliations":reconciliations,"evidence_sha256":evidence_sha256}
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                if effective_recovery(c,run_id) is None:raise ValueError("baseline_recovery_policy_missing")
                previous=session_amendment(c,run_id)
                if previous is not None:
                    if previous!=value:raise ValueError("baseline_session_policy_conflict")
                    c.rollback();return value
                seen=set()
                for item in reconciliations:
                    if set(item)!={"symbol","session","part","request_sha256"}:raise ValueError("baseline_reconciliation_scope")
                    key=(item["symbol"],item["session"],item["part"])
                    if key in seen:raise ValueError("baseline_reconciliation_duplicate")
                    seen.add(key)
                    row=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",key).fetchone()
                    if row is None or row["run_id"]!=run_id or row["outcome"]!="RuntimeError" or row["payload"] is not None:
                        raise ValueError("baseline_reconciliation_ineligible")
                    receipt=json.loads(row["receipt"]) if row["receipt"] else {}
                    if (receipt.get("response_bytes")!=plan["max_bytes"] or
                        any(receipt.get(k) is not None for k in ("method","captured_at","elapsed_seconds","outcome","messages","rows","response_sha256")) or
                        not receipt.get("finished_at") or request_fingerprint(row)!=item["request_sha256"]):
                        raise ValueError("baseline_reconciliation_ineligible")
                    latest=attempt_state(c,*key[:2],logical_part(key[2]))
                    if latest["part"]!=key[2] or latest["attempt"]>=SESSION_POLICY["max_attempts"]:
                        raise ValueError("baseline_reconciliation_ineligible")
                c.execute("INSERT INTO baseline_runs VALUES(?,?)",("session-recovery:"+run_id,canonical(value)))
                c.commit();return value
            except BaseException:c.rollback();raise
            finally:c.close()
    def configure_interrupted_recovery(self,run_id,requests,*,evidence_sha256):
        """Append a user-approved exception under the finite-run owner lock.

        Original rows stay started/unknown and count as attempts. Only the named
        original unknown byte amounts are excluded; subsequent calls use normal accounting.
        """
        value={"version":1,"requests":requests,"evidence_sha256":evidence_sha256}
        key="interrupted-recovery:"+run_id
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                validate_interruption(c,run_id,value)
                previous=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(key,)).fetchone()
                if previous:
                    if json.loads(previous[0])!=value:raise ValueError("baseline_interruption_policy_conflict")
                    c.rollback();return value
                for item in requests:
                    state=attempt_state(c,item["symbol"],item["session"],item["part"])
                    if state["attempt"]!=1:raise ValueError("baseline_interruption_not_latest")
                c.execute("INSERT INTO baseline_runs VALUES(?,?)",(key,canonical(value)))
                c.commit();return value
            except BaseException:c.rollback();raise
            finally:c.close()

    def configure_auth_retry_once(self,run_id,*,symbol,session,part,after_sha256,evidence_sha256):
        """Append a single user-approved retry; caller holds the finite owner lock."""
        key="auth-retry-once:"+symbol+":"+session+":"+part
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                parent=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",(symbol,session,part)).fetchone()
                if parent is None:raise ValueError("baseline_auth_retry_parent_changed")
                value={"version":1,"run_id":run_id,"source_run_id":parent["run_id"],"symbol":symbol,
                       "session":session,"part":part,"after_sha256":after_sha256,
                       "evidence_sha256":evidence_sha256,"attempt":2}
                validate_auth_retry(c,value,symbol,session,part)
                previous=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(key,)).fetchone()
                if previous:
                    if json.loads(previous[0])!=value:raise ValueError("baseline_auth_retry_policy_conflict")
                    attempt_state(c,symbol,session,part)
                    c.rollback();return value
                state=attempt_state(c,symbol,session,part)
                if state["attempt"]!=1 or c.execute("SELECT 1 FROM baseline_days WHERE symbol=? AND session=?",(symbol,session)).fetchone():
                    raise ValueError("baseline_auth_retry_not_latest")
                c.execute("INSERT INTO baseline_runs VALUES(?,?)",(key,canonical(value)))
                c.commit();return value
            except BaseException:c.rollback();raise
            finally:c.close()

    def configure_setup_retry_once(self,run_id,*,symbol,session,part,after_sha256):
        """Record one explicit user-authorized attempt after known setup exhaustion.

        The owner lock is required by the caller. The immutable grant names one
        exact parent row. It never resets attempts or increases the run caps.
        """
        self.plan(run_id)
        value={"version":1,"symbol":symbol,"session":session,"part":part,
               "after_sha256":after_sha256,"attempt":4}
        key="setup-retry-once:"+run_id
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                previous=c.execute("SELECT payload FROM baseline_runs WHERE id=?",(key,)).fetchone()
                if previous:
                    if json.loads(previous[0])!=value:raise ValueError("baseline_setup_retry_policy_conflict")
                    # Replay validates the pinned parent but does not grant #5.
                    attempt_state(c,symbol,session,part)
                    c.rollback();return value
                policy=effective_recovery(c,run_id)
                state=attempt_state(c,symbol,session,part)
                if (not policy or not state or state["attempt"]!=3 or state["original_run_id"]!=run_id or
                    state["run_id"]!=run_id or state["outcome"]!="ProviderFailure"):
                    raise ValueError("baseline_setup_retry_ineligible")
                receipt=state["receipt"] or {}
                if (receipt.get("outcome") not in ("SESSION_TIMEOUT","SESSION_CONNECTION_ERROR") or
                    receipt.get("stage")!="session_setup" or receipt.get("data_dispatched") is not False or
                    type(receipt.get("response_bytes")) is not int or receipt["response_bytes"]!=0 or
                    not blocked_setup(state,run_id,policy)):
                    raise ValueError("baseline_setup_retry_ineligible")
                parent=c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",
                    (symbol,session,attempt_part(part,3))).fetchone()
                if request_fingerprint(parent)!=after_sha256:raise ValueError("baseline_setup_retry_parent_changed")
                c.execute("INSERT INTO baseline_runs VALUES(?,?)",(key,canonical(value)))
                c.commit();return value
            except BaseException:c.rollback();raise
            finally:c.close()
    def configure_daytime(self,run_id):
        self.plan(run_id)
        key="daytime:"+run_id;payload=canonical(DAYTIME_POLICY)
        self.write("INSERT OR IGNORE INTO baseline_runs VALUES(?,?)",(key,payload))
        if self.plan(key)!=DAYTIME_POLICY:raise ValueError("baseline_daytime_policy_conflict")
        return DAYTIME_POLICY.copy()
    def daytime_policy(self,run_id):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            row=c.execute("SELECT payload FROM baseline_runs WHERE id=?",("daytime:"+run_id,)).fetchone()
            if not row:return None
            policy=json.loads(row[0])
            if policy!=DAYTIME_POLICY:raise ValueError("baseline_daytime_policy_conflict")
            return policy
    def attempt_state(self,symbol,session,part):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            return attempt_state(c,symbol,session,part)
    def reserve_retry(self,symbol,session,part,run_id,when,attempt):
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
            try:
                c.execute("BEGIN IMMEDIATE")
                policy=effective_recovery(c,run_id)
                if policy is None:raise ValueError("baseline_recovery_policy_missing")
                state=attempt_state(c,symbol,session,part)
                if state is None or not retryable(state,run_id,policy) or attempt!=state["attempt"]+1:
                    raise ValueError("baseline_retry_forbidden")
                c.execute("INSERT INTO baseline_requests VALUES(?,?,?,?,?,'started',NULL,NULL)",
                    (symbol,session,attempt_part(part,attempt),run_id,when))
                c.commit();return True
            except BaseException:c.rollback();raise
            finally:c.close()
    def part(self,symbol,session,part):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            r=c.execute("SELECT outcome,payload FROM baseline_requests WHERE symbol=? AND session=? AND part=?",(symbol,session,part)).fetchone()
            return None if r is None else (r[0],json.loads(r[1]) if r[1] is not None else None)
    def reserve_part(self,symbol,session,part,run_id,when):
        return self.write("INSERT OR IGNORE INTO baseline_requests VALUES(?,?,?,?,?,'started',NULL,NULL)",(symbol,session,part,run_id,when))==1
    def finish_part(self,symbol,session,part,outcome,payload,receipt):
        safe={k:receipt.get(k) for k in ("method","captured_at","finished_at","outcome","rows","response_bytes","response_sha256","messages","elapsed_seconds","stage","data_dispatched")}
        statement="UPDATE baseline_requests SET outcome=?,payload=?,receipt=? WHERE symbol=? AND session=? AND part=? AND outcome='started'"
        params=(outcome,None if payload is None else canonical(payload),canonical(safe),symbol,session,part)
        # Retain the already reduced response while waiting for a busy reader.
        # These are local lock admissions, never additional provider requests.
        for attempt in range(3):
            try:
                self.write(statement,params)
                return
            except ConflictError as exc:
                if str(exc)!="Timed out acquiring the physical store lock" or attempt==2:raise
    def usage(self,run_id,max_bytes):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            rows=c.execute("SELECT * FROM baseline_requests WHERE run_id=?",(run_id,)).fetchall()
            amendment=session_amendment(c,run_id)
            interrupted=interruption_amendment(c,run_id)
            sizes=[0 if (approved_interruption(r,interrupted) or (amendment and reconciled_setup(c,r,amendment))) else
                   json.loads(r["receipt"]).get("response_bytes") if r["receipt"] else None for r in rows]
            connection_count=connection_state(c,run_id)["admissions"]
        # Only individually reviewed, hash-pinned setup failures are reconciled.
        # Every reservation still counts. The explicit interrupted exceptions
        # omit unknown bytes without asserting zero transfer; all others fail closed.
        return len(rows)+connection_count,sum(size if type(size) is int and size>=0 else max_bytes for size in sizes)
    def save_day(self,day):
        validate_day(day);payload=canonical(day);digest=sha256(payload.encode()).hexdigest()
        with StoreWriteLock(self.path):
            c=sqlite3.connect(self.path)
            try:
                c.execute("BEGIN IMMEDIATE")
                old=c.execute("SELECT digest FROM baseline_days WHERE symbol=? AND session=?",(day["symbol"],day["session"])).fetchone()
                if old:
                    if old[0]!=digest:raise ValueError("baseline_day_conflict")
                    c.rollback();return False
                c.execute("INSERT INTO baseline_days VALUES(?,?,?,?,0)",(day["symbol"],day["session"],payload,digest))
                # Keep request receipts / admission identities, discard completed temporary parts.
                c.execute("UPDATE baseline_requests SET payload=NULL WHERE symbol=? AND session=?",(day["symbol"],day["session"]))
                c.commit();return True
            except BaseException:c.rollback();raise
            finally:c.close()
    def has_day(self,symbol,session):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:return c.execute("SELECT 1 FROM baseline_days WHERE symbol=? AND session=?",(symbol,session)).fetchone() is not None
    def collection_status(self,run_id):
        plan=self.plan(run_id);policy=self.recovery_policy(run_id)
        targets={(symbol,day["session"]) for symbol in plan["symbols"] for day in plan["sessions"]}
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            saved={tuple(r) for r in c.execute("SELECT symbol,session FROM baseline_days")}&targets
            # Empty successful catalogs cannot acquire retries. Count their
            # terminal days directly instead of resolving every empty catalog
            # through all attempt/policy queries while holding the store lock.
            terminal=({tuple(r) for r in c.execute(
                "SELECT symbol,session FROM baseline_requests WHERE outcome='success' AND part='catalog' AND payload='[]'")}&targets)-saved
            candidates={(r[0],r[1],logical_part(r[2])) for r in c.execute(
                "SELECT symbol,session,part FROM baseline_requests WHERE outcome!='success' OR part LIKE 'catalog#retry%'")}
            terminal-={(symbol,session) for symbol,session,part in candidates if part=="catalog"}
            unresolved=set();recoverable=set();setup_blocked=set()
            for symbol,session,part in candidates:
                key=(symbol,session)
                if key not in targets:continue
                state=attempt_state(c,symbol,session,part)
                if state["outcome"]=="started" and not retryable(state,run_id,policy):unresolved.add(key)
                elif key in saved:continue
                elif retryable(state,run_id,policy):recoverable.add(key)
                elif blocked_setup(state,run_id,policy):setup_blocked.add(key)
                elif state["outcome"]!="success" or (part=="catalog" and state["payload"]==[]):terminal.add(key)
            connections=connection_state(c,run_id)
            interrupted=interruption_amendment(c,run_id)
        # A different terminal part in the same day cannot hide a fatal setup
        # failure or make a persistent session outage eligible for publication.
        terminal-=setup_blocked
        pending=targets-saved-terminal
        result={"target_days":len(targets),"saved_days":len(saved),"skipped_days":len(terminal),
            "pending_days":len(pending),"unresolved_days":len(unresolved),"retry_pending_days":len(recoverable-terminal),
            "setup_blocked_days":len(setup_blocked),
            "connection_pending":connections["pending"],"connection_blocked":connections["blocked"],
            "connection_attempts":connections["admissions"],
            "collection_complete":not pending and not unresolved and not setup_blocked and not connections["pending"]}
        if interrupted:result["excluded_unknown_requests"]=len(interrupted["requests"])
        return result
    def pending_days(self,limit=100,*,run_id=None):
        with StoreWriteLock(self.path,timeout_seconds=15),self.read() as c:
            scope="";params=[]
            if run_id is not None:
                scope=""" AND symbol IN (SELECT value FROM json_each((SELECT json_extract(payload,'$.symbols') FROM baseline_runs WHERE id=?)))
                    AND session IN (SELECT json_extract(value,'$.session') FROM json_each((SELECT json_extract(payload,'$.sessions') FROM baseline_runs WHERE id=?)))"""
                params=[run_id,run_id]
            return [json.loads(r[0]) for r in c.execute("SELECT payload FROM baseline_days WHERE published=0"+scope+" ORDER BY session DESC,symbol LIMIT ?",(*params,limit))]
    def publish_days(self,site,limit=100,*,timeout=15,deadline=None,clock=lambda:datetime.now(timezone.utc),run_id=None):
        days=self.pending_days(limit,run_id=run_id)
        if not days:return 0
        if deadline is not None:
            remaining=(deadline-clock()).total_seconds()
            if remaining<=0:raise RuntimeError("duration_cap")
            timeout=min(timeout,remaining)
        reply=site.request("/api/collector/baselines",{"version":1,"days":days},timeout=timeout)
        if reply.get("ok") is not True or reply.get("days")!=len(days):raise ValueError("baseline_publication_rejected")
        for day in days:self.write("UPDATE baseline_days SET published=1 WHERE symbol=? AND session=?",(day["symbol"],day["session"]))
        return len(days)
