"""One finite manual SPY history job, restart-safe without provider retries."""
from __future__ import annotations
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import fcntl,gzip,hashlib,json,os,shutil,time,threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from .model import build_capture,canonical_json
from .store import OptionsStore
from .transport import Budget,ThetaTransport,ProviderFailure

START=date(2016,1,4)
END=date(2026,9,23)
DOWNLOAD_WORKERS=4

def atomic_json(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w") as f:
        f.write(canonical_json(value)+"\n");f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def sessions_from_calendars(calendars,start=START,end=END):
    closed=set()
    for year,rows in calendars.items():
        for row in rows:
            d=date.fromisoformat(row["date"])
            if d.year!=int(year):raise ValueError("calendar_year_mismatch")
            if row["type"]=="full_close":closed.add(d)
            elif row["type"]!="early_close":raise ValueError("calendar_status_unknown")
    sessions=[];d=start-timedelta(days=14)
    while d<=end:
        if d.year not in {int(y) for y in calendars}:raise ValueError("calendar_missing_year")
        if d.weekday()<5 and d not in closed:sessions.append(d)
        d+=timedelta(days=1)
    return [(d.isoformat(),sessions[i-1].isoformat()) for i,d in enumerate(sessions) if start<=d<=end and i>0]

class SpyHistoryJob:
    WORK_DIRECTORY=".local/theta-spy-20260924"
    STORE_LIMIT_BYTES=15*1024**3
    def __init__(self,root,*,transport_factory=ThetaTransport):
        self.root=Path(root).resolve()
        self.work=self.root/self.WORK_DIRECTORY
        self.work.mkdir(parents=True,exist_ok=True)
        self.store=OptionsStore(self.root)
        self.transport_factory=transport_factory
        self._journal_lock=threading.RLock()
        self._stop=threading.Event()
        self._budget=None
        self._failure=None
        self.status_path=self.work/"status.json"
        self.attempt_path=self.work/"attempts.jsonl"
        self.stage=self.work/"stage";self.stage.mkdir(exist_ok=True)
        self.attempted=set()
        self.history=[]
        self.status={"contract":"theta_spy_history_status","symbol":"SPY",
            "scope_start":str(START),"scope_end":str(END),"state":"starting",
            "requests_cap":20000,"bytes_cap":128*1024**3,"seconds_cap":86400,
            "automatic_retries":0,"completed_sessions":0,"gaps":[]}
        self.reload_durable_state()

    def reload_durable_state(self):
        # run() refreshes this under its exclusive lock; a preconstructed instance
        # must not reuse stale request authorization or completion state.
        self.attempted=set();self.history=[]
        if self.attempt_path.exists():
            for line in self.attempt_path.read_text().splitlines():
                item=json.loads(line);self.history.append(item);self.attempted.add(item["unit"])
        if self.status_path.exists():
            self.status.update(json.loads(self.status_path.read_text()))
        self.status["pid"]=os.getpid()

    def prepare_cleanup(self,capture,paths):
        entries=[]
        for path in sorted(paths):
            resolved=path.resolve(strict=True)
            if not resolved.is_relative_to(self.work.resolve()) or path.is_symlink():
                raise ValueError("cleanup_scope")
            entries.append({"path":str(resolved.relative_to(self.work.resolve())),
                            "sha256":hashlib.sha256(resolved.read_bytes()).hexdigest()})
        atomic_json(self.work/"pending-cleanup.json",{
            "symbol":capture["symbol"],"session":capture["session"],"semantic_sha256":capture["semantic_sha256"],"files":entries})

    def reconcile_cleanup(self):
        journal=self.work/"pending-cleanup.json"
        if not journal.exists():return False
        manifest=json.loads(journal.read_text())
        with self.store.read() as c:
            committed=c.execute(
                "SELECT 1 FROM option_captures WHERE symbol=? AND session_date=? AND semantic_sha256=?",
                (manifest.get("symbol","SPY"),manifest["session"],manifest["semantic_sha256"])).fetchone()
        if not committed:return False
        paths=[]
        for entry in manifest["files"]:
            relative=Path(entry["path"])
            path=self.work/relative
            if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(self.work.resolve()):
                raise ValueError("cleanup_scope")
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=entry["sha256"]:
                raise ValueError("cleanup_source_changed")
            paths.append(path)
        for path in paths:path.unlink(missing_ok=True)
        journal.unlink()
        return True

    def persist(self):
        if self._budget is not None:self.status.update(self._budget.snapshot())
        self.status["updated_at"]=datetime.now(timezone.utc).isoformat()
        atomic_json(self.status_path,self.status)

    def receipt(self,receipt,budget):
        # Worker callbacks append evidence only; the coordinator owns status.json.
        with self._journal_lock:
            with (self.work/"request-receipts.jsonl").open("a") as f:
                f.write(canonical_json(receipt)+"\n");f.flush();os.fsync(f.fileno())

    def cached_get(self,transport,method,**params):
        identity={"method":method,"params":{k:str(v) for k,v in params.items()}}
        unit=hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        target=self.stage/(unit+".json.gz")
        if target.exists():
            with gzip.open(target,"rt") as f:batch=json.load(f)
            return batch,target
        # Adopt matching preflight artifacts rather than refetching their scope.
        day=params.get("date",params.get("start_date"))
        legacy=self.work/(method+"-"+str(day)+".json.gz")
        if legacy.exists():
            with gzip.open(legacy,"rt") as f:batch=json.load(f)
            if batch["receipt"]["params"]==identity["params"]:return batch,legacy
        with self._journal_lock:
            if self._stop.is_set():raise RuntimeError("parallel_download_stopped")
            if unit in self.attempted:raise RuntimeError("unit_previously_attempted_no_retry")
            event={"unit":unit,**identity,"started_at":datetime.now(timezone.utc).isoformat()}
            with self.attempt_path.open("a") as f:
                f.write(canonical_json(event)+"\n");f.flush();os.fsync(f.fileno())
            self.attempted.add(unit)
        rows,receipt=transport.request(method,**params)
        batch={"rows":rows,"receipt":receipt}
        tmp=target.with_suffix(".tmp")
        with gzip.open(tmp,"wt",compresslevel=1) as f:json.dump(batch,f,sort_keys=True,allow_nan=False)
        with tmp.open("rb") as f:os.fsync(f.fileno())
        os.replace(tmp,target)
        return batch,target

    def acquire_session(self,transport,session,*,symbol="SPY"):
        """Workers acquire and stage only; they never open the options database."""
        try:
            day=date.fromisoformat(session)
            greek,gpath=self.cached_get(transport,"option_history_greeks_eod",
                symbol=symbol,expiration="*",start_date=day,end_date=day,
                version="1",underlyer_use_nbbo=True)
            paths={gpath};source_receipts=[greek["receipt"]]
            if not greek["rows"]:
                prices,epath=self.cached_get(transport,"option_history_eod",
                    symbol=symbol,expiration="*",start_date=day,end_date=day)
                paths.add(epath);source_receipts.append(prices["receipt"])
                greek_rows=prices["rows"]
            else:greek_rows=greek["rows"]
            if not greek_rows:return {"empty":True,"paths":paths}
            oi,opath=self.cached_get(transport,"option_history_open_interest",
                symbol=symbol,expiration="*",date=day)
            paths.add(opath);source_receipts.append(oi["receipt"])
            return {"empty":False,"greeks":greek_rows,"oi":oi["rows"],
                    "receipts":source_receipts,"paths":paths}
        except BaseException as exc:
            # No new provider unit is admitted after a worker failure.
            with self._journal_lock:
                if self._failure is None:
                    self._failure={"symbol":symbol,"session":session,"reason":exc.code if isinstance(exc,ProviderFailure) else
                        str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__}
                self._stop.set()
            raise

    def downloaded_sessions(self,transport,remaining):
        """At most four pending days; publication and cleanup stay in this thread."""
        pending=deque();iterator=iter(remaining)
        pool=ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS,thread_name_prefix="theta-download")
        def submit():
            if self._stop.is_set():return False
            try:session,previous=next(iterator)
            except StopIteration:return False
            if shutil.disk_usage(self.work).free<20*1024**3:raise RuntimeError("disk_reserve")
            if self.store.path.stat().st_size>self.STORE_LIMIT_BYTES:raise RuntimeError("spy_database_cap")
            pending.append((session,previous,pool.submit(self.acquire_session,transport,session)))
            return True
        try:
            for _ in range(DOWNLOAD_WORKERS):
                if not submit():break
            while pending:
                self.status["active_sessions"]=[item[0] for item in pending]
                self.status["active_session"]=pending[0][0]
                self.persist()
                session,previous,future=pending.popleft()
                yield session,previous,future.result()
                future=None
                # Consumer finishes publication before another day enters the queue.
                submit()
        finally:
            self._stop.set()
            pool.shutdown(wait=True,cancel_futures=True)

    def run(self):
        os.umask(0o077)
        lock=(self.work/"run.lock").open("a+b")
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("spy_history_job_already_running") from None
        transport=None
        try:
            self.reload_durable_state()
            self._stop.clear()
            self._failure=None
            self.store.initialize()
            self.reconcile_cleanup()
            completed=self.store.completed_sessions()
            existing=self.store.status()
            self.status.update(greeks_sessions=existing["greeks_sessions"],
                               prices_only_sessions=existing["prices_only_sessions"])
            if self.status.get("state")=="completed":return self.status
            preflight=json.loads((self.work/"preflight.json").read_text())
            # Account for all preflight successes/denials plus the separate calendar probe.
            seeded=preflight["data_requests"]+1
            seeded_bytes=preflight["response_bytes"]
            seeded_bytes+=json.loads((self.work/"calendar-2026.json").read_text())["receipt"]["response_bytes"]
            original_start=self.status.get("authorization_started_at")
            if not original_start:
                original_start=json.loads((self.work/"preflight-receipts.jsonl").read_text().splitlines()[0])["captured_at"]
                self.status["authorization_started_at"]=original_start
            elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(original_start)).total_seconds()
            if elapsed>=86400:raise RuntimeError("authorization_duration_exhausted")
            previous_receipts=[]
            if (self.work/"request-receipts.jsonl").exists():
                previous_receipts=[json.loads(s) for s in (self.work/"request-receipts.jsonl").read_text().splitlines()]
            budget=Budget(max_seconds=int(86400-elapsed),
                requests=seeded+max(len(previous_receipts),len(self.attempted)),
                received_bytes=seeded_bytes+sum(r["response_bytes"] for r in previous_receipts))
            self._budget=budget
            budget.check()
            transport=self.transport_factory(self.root,budget,receipt_callback=self.receipt)
            self.status.update(state="planning",subscription_code=transport.subscription_code,
                               completed_sessions=len(completed))
            self.persist()
            calendars={}
            for year in range(2015,2027):
                path=self.work/f"calendar-{year}.json"
                if path.exists():batch=json.loads(path.read_text())
                else:
                    batch,stage_path=self.cached_get(transport,"calendar_year",year=str(year))
                    atomic_json(path,batch)
                    stage_path.unlink()
                if not batch["rows"]:raise RuntimeError("calendar_empty")
                calendars[year]=batch["rows"]
            planned=sessions_from_calendars(calendars)
            references=json.loads((self.work/"spot-references.json").read_text())
            manifest={"symbol":"SPY","start":str(START),"end":str(END),"sessions":planned,
                "ordering":"newest_first","request_cap":20000,"response_bytes_cap":128*1024**3,
                "no_retries":True,"retention":"discard_full_source_after_verified_publication"}
            manifest_path=self.work/"manifest.json"
            if manifest_path.exists() and json.loads(manifest_path.read_text())!=json.loads(canonical_json(manifest)):
                raise RuntimeError("manifest_drift")
            atomic_json(manifest_path,manifest)
            self.status.update(state="running",planned_sessions=len(planned),
                download_workers=DOWNLOAD_WORKERS,max_pending_sessions=DOWNLOAD_WORKERS,
                run_segment_started_at=datetime.now(timezone.utc).isoformat(),
                run_segment_start_sessions=len(completed),
                manifest_sha256=hashlib.sha256(canonical_json(manifest).encode()).hexdigest())
            self.persist()
            failures=0
            remaining=[(session,previous) for session,previous in reversed(planned) if session not in completed]
            downloads=self.downloaded_sessions(transport,remaining)
            try:
                for session,previous,acquired in downloads:
                    if acquired["empty"]:
                        self.status["gaps"].append({"session":session,"reason":"eod_no_data"})
                        self.persist();continue
                    try:
                        capture=build_capture(session,acquired["greeks"],acquired["oi"],acquired["receipts"],
                            previous_session=previous,spot_reference=references.get(session))
                    except ValueError as exc:
                        self.status["gaps"].append({"session":session,"reason":str(exc)})
                        failures+=1;self.persist()
                        if failures>=3:raise
                        continue
                    # Publication and cleanup failures are terminal. Never overwrite
                    # their recovery journal by proceeding to another session.
                    self.prepare_cleanup(capture,acquired["paths"])
                    result=self.store.publish(capture)
                    if not self.reconcile_cleanup():raise RuntimeError("cleanup_unverified_capture")
                    completed.add(session)
                    counter="greeks_sessions" if capture["coverage"]["greeks_contracts"] else "prices_only_sessions"
                    self.status[counter]=self.status.get(counter,0)+1
                    self.status.update(completed_sessions=len(completed),last_published_session=session,
                        last_capture_id=result["capture_id"],last_coverage=capture["coverage"],
                        database_bytes=self.store.path.stat().st_size)
                    self.persist()
                    if len(completed)%25==0:
                        print(canonical_json({k:self.status[k] for k in
                            ("state","completed_sessions","planned_sessions","last_published_session","database_bytes","data_requests")}),flush=True)
                    del acquired,capture
                    failures=0
            finally:
                downloads.close()
            self.status["state"]="completed" if len(completed)==len(planned) else "completed_with_gaps"
            self.status["active_session"]=None
            self.status["active_sessions"]=[]
            self.status["database"]=self.store.status()
            self.status["backup"]=self.store.backup(self.work/"completed-options-backup.sqlite")
            self.persist()
            return self.status
        except Exception as exc:
            if self._failure is not None:
                if self._failure not in self.status["gaps"]:self.status["gaps"].append(self._failure)
                self.status["download_failure"]=self._failure
            self.status.update(state="stopped",error=exc.code if isinstance(exc,ProviderFailure) else str(exc))
            # Sanitize unexpected errors rather than include provider/auth content.
            if type(exc) not in (RuntimeError,ValueError,ProviderFailure):
                self.status["error"]=type(exc).__name__
            self.persist()
            raise
        finally:
            if transport:transport.close()
            fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()
