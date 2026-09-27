"""Durable ETF-only retries: two main attempts and one final gap attempt."""
from collections import Counter
from datetime import datetime,timezone
import gzip,hashlib,json,os,random,threading
from .model import canonical_json
from .transport import ProviderFailure

TRANSIENT={"UNAVAILABLE","DEADLINE_EXCEEDED","RESOURCE_EXHAUSTED","INTERNAL","ABORTED","CANCELLED"}
EMPTY={"empty","no_data"}
POLICY={"contract":"theta_etf_retry_policy_v1","main_attempts_per_request":2,
        "recovery_attempts_per_request":1,"recovery_passes":1,
        "retryable_provider_codes":sorted(TRANSIENT),"retry_empty_responses":True,
        "completed_captures_excluded":True,"successful_inputs_reused":True}

class RequestGap(ValueError):
    def __init__(self,reason,method):
        super().__init__(reason);self.reason=reason;self.method=method

def identity(method,params):
    value={"method":method,"params":{k:str(v) for k,v in params.items()}}
    return value,hashlib.sha256(canonical_json(value).encode()).hexdigest()

class EtfRequests:
    def __init__(self,job):
        self.job=job
        self.counts=Counter()
        self.total=Counter()
        self.latest={}
        self.locks={}
        for item in job.history:
            unit=item["unit"];phase=item.get("phase","main")
            if phase not in ("main","recovery"):raise ValueError("unknown_retry_phase")
            self.counts[unit,phase]+=1;self.total[unit]+=1
            if item.get("attempt_number",self.total[unit])!=self.total[unit]:
                raise ValueError("retry_attempt_sequence")
            if self.counts[unit,phase]>(2 if phase=="main" else 1):
                raise ValueError("retry_attempt_limit")
        path=job.work/"request-receipts.jsonl"
        terminal=Counter()
        if path.exists():
            for line in path.read_text().splitlines():
                receipt=json.loads(line)
                _,unit=identity(receipt["method"],receipt["params"])
                terminal[unit]+=1;self.record(receipt)
        # A crash mid-stream can consume bytes without writing a final receipt.
        # Never reset those unknown bytes to zero or retry that attempt blindly.
        if terminal!=self.total:raise RuntimeError("request_receipt_accounting_mismatch")

    def record(self,receipt):
        _,unit=identity(receipt["method"],receipt["params"])
        with self.job._journal_lock:self.latest[unit]=receipt

    def retained_path(self,method,**params):
        value,unit=identity(method,params)
        path=self.job.stage/(unit+".json.gz")
        if path.exists():
            with gzip.open(path,"rt") as source:batch=json.load(source)
            receipt=batch["receipt"]
            if identity(receipt["method"],receipt["params"])[0]!=value:
                raise ValueError("retry_cache_identity")
            return path
        return None

    def delay(self):
        # One retry has a short jittered delay, outside all store locks.
        if self.job._stop.wait(random.uniform(1,2)):
            raise RuntimeError("parallel_download_stopped")

    def get(self,transport,method,**params):
        value,unit=identity(method,params)
        with self.job._journal_lock:
            lock=self.locks.setdefault(unit,threading.Lock())
        with lock:return self._get(transport,method,params,value,unit)

    def _get(self,transport,method,params,value,unit):
        job=self.job
        phase=job.status.get("phase","main")
        if phase not in ("main","recovery"):raise ValueError("unknown_retry_phase")
        limit=2 if phase=="main" else 1
        target=job.stage/(unit+".json.gz")
        batch=None
        if target.exists():
            with gzip.open(target,"rt") as source:batch=json.load(source)
            receipt=batch["receipt"]
            if identity(receipt["method"],receipt["params"])[0]!=value:
                raise ValueError("retry_cache_identity")
            if receipt["outcome"] not in EMPTY|{"success"}:
                raise ValueError("retry_cache_outcome")
            if batch["rows"]:return batch,target
        latest=self.latest.get(unit)
        if batch is None and latest and latest["outcome"]=="success":
            raise RuntimeError("successful_source_missing")
        if latest and latest["outcome"] not in TRANSIENT|EMPTY|{"success"}:
            raise ProviderFailure(latest["outcome"],latest)
        while self.counts[unit,phase]<limit:
            if self.counts[unit,phase]:self.delay()
            if job._budget is not None:job._budget.check()
            with job._journal_lock:
                if job._stop.is_set():raise RuntimeError("parallel_download_stopped")
                number=self.total[unit]+1
                event={"unit":unit,**value,"phase":phase,"attempt_number":number,
                    "attempt_id":unit+":"+str(number),"started_at":datetime.now(timezone.utc).isoformat()}
                with job.attempt_path.open("a") as f:
                    f.write(canonical_json(event)+"\n");f.flush();os.fsync(f.fileno())
                job.attempted.add(unit);job.history.append(event)
                self.counts[unit,phase]+=1;self.total[unit]+=1
            try:
                rows,receipt=transport.request(method,**params)
            except ProviderFailure as exc:
                if exc.code not in TRANSIENT:raise
                latest=exc.receipt
                if self.counts[unit,phase]>=limit:raise RequestGap(exc.code,method) from None
                continue
            batch={"rows":rows,"receipt":receipt}
            tmp=target.with_suffix(".tmp")
            with gzip.open(tmp,"wt",compresslevel=1) as f:
                json.dump(batch,f,sort_keys=True,allow_nan=False)
            with tmp.open("rb") as f:os.fsync(f.fileno())
            os.replace(tmp,target)
            latest=receipt
            if rows:return batch,target
        if latest and latest["outcome"] in EMPTY|{"success"} and batch is not None:
            return batch,target
        reason=latest["outcome"] if latest else "attempt_without_response"
        raise RequestGap(reason,method)
