"""Finite Tier-1 expansion with bounded request retries and one final gap pass."""
from datetime import date,datetime,timezone
import fcntl,hashlib,json,os
from .job import SpyHistoryJob,atomic_json,sessions_from_calendars,DOWNLOAD_WORKERS
from .model import build_capture,canonical_json
from .store import unpacked
from .transport import Budget,ThetaTransport,ProviderFailure
from .etf_recovery import EtfRequests,RequestGap,POLICY
from .universe import EXPANSION_ETFS,EXPANSION_START,EXPANSION_END,LISTING_STARTS,MAX_REQUESTS,MAX_BYTES,MAX_SECONDS

QUALITY_GAPS={"eod_no_data","missing_contemporaneous_spot","fallback_reference_not_crosschecked",
              "fallback_reference_basis_mismatch"}

class EtfHistoryJob(SpyHistoryJob):
    WORK_DIRECTORY=".local/theta-etf-20260924"
    STORE_LIMIT_BYTES=25*1024**3

    def __init__(self,root,*,transport_factory=ThetaTransport):
        super().__init__(root,transport_factory=transport_factory)
        self.requests=None
        self.status.pop("symbol",None)
        self.status.update(contract="theta_tier1_etf_history_status",symbols=list(EXPANSION_ETFS),
            scope_start=EXPANSION_START,scope_end=EXPANSION_END,
            requests_cap=MAX_REQUESTS,bytes_cap=MAX_BYTES,seconds_cap=MAX_SECONDS)

    def receipt(self,receipt,budget):
        super().receipt(receipt,budget)
        if self.requests is not None:self.requests.record(receipt)

    def persist(self):
        with self._journal_lock:
            self.status["retry_requests"]=len(self.history)-len(self.attempted)
        super().persist()

    def acquire_session(self,transport,unit):
        symbol,session=unit.split("|")
        if symbol not in EXPANSION_ETFS:raise ValueError("symbol_outside_expansion")
        try:
            day=date.fromisoformat(session)
            greek,gpath=self.requests.get(transport,"option_history_greeks_eod",
                symbol=symbol,expiration="*",start_date=day,end_date=day,
                version="1",underlyer_use_nbbo=True)
            paths={gpath};receipts=[greek["receipt"]]
            rows=greek["rows"]
            if not rows:
                prices,epath=self.requests.get(transport,"option_history_eod",
                    symbol=symbol,expiration="*",start_date=day,end_date=day)
                paths.add(epath);receipts.append(prices["receipt"]);rows=prices["rows"]
            # A recovered Greek stream can make a prior EOD fallback redundant.
            # Include that same-day staged artifact in verified publication cleanup.
            fallback=self.requests.retained_path("option_history_eod",
                symbol=symbol,expiration="*",start_date=day,end_date=day)
            if fallback is not None:paths.add(fallback)
            if not rows:raise RequestGap("eod_no_data","option_history_eod")
            oi,opath=self.requests.get(transport,"option_history_open_interest",
                symbol=symbol,expiration="*",date=day)
            if not oi["rows"]:raise RequestGap("open_interest_no_data","option_history_open_interest")
            paths.add(opath);receipts.append(oi["receipt"])
            return {"greeks":rows,"oi":oi["rows"],"receipts":receipts,"paths":paths}
        except RequestGap as exc:
            return {"gap":{"reason":exc.reason,"method":exc.method}}
        except BaseException as exc:
            with self._journal_lock:
                if self._failure is None:
                    self._failure={"symbol":symbol,"session":session,"reason":exc.code if isinstance(exc,ProviderFailure)
                        else str(exc) if type(exc) in (ValueError,RuntimeError) else type(exc).__name__}
                self._stop.set()
            raise

    def load_plan(self):
        # Preserve the original frozen manifest; retry authority is an amendment.
        original=self.root/".local/theta-spy-20260924"
        calendars={}
        for year in range(2015,2027):
            target=self.work/f"calendar-{year}.json"
            if not target.exists():
                source=original/f"calendar-{year}.json"
                batch=json.loads(source.read_text())
                batch["reused_from"]=str(source.relative_to(self.root))
                batch["reused_source_sha256"]=hashlib.sha256(source.read_bytes()).hexdigest()
                atomic_json(target,batch)
            calendars[year]=json.loads(target.read_text())["rows"]
        sessions=sessions_from_calendars(calendars,date.fromisoformat(EXPANSION_START),date.fromisoformat(EXPANSION_END))
        units=[(symbol,session,previous) for session,previous in reversed(sessions)
               for symbol in EXPANSION_ETFS if session>=LISTING_STARTS.get(symbol,EXPANSION_START)]
        manifest={"contract":"theta_tier1_etf_expansion_v1","symbols":list(EXPANSION_ETFS),
            "start":EXPANSION_START,"end":EXPANSION_END,"listing_start_overrides":LISTING_STARTS,
            "ordering":"date_desc_then_declared_symbol_order","units":units,
            "max_requests":MAX_REQUESTS,"max_bytes":MAX_BYTES,"max_seconds":MAX_SECONDS,
            "download_workers":DOWNLOAD_WORKERS,"retries":0,"completed_spy_excluded":True}
        target=self.work/"manifest.json"
        if target.exists() and json.loads(target.read_text())!=json.loads(canonical_json(manifest)):
            raise RuntimeError("etf_manifest_drift")
        atomic_json(target,manifest)
        return manifest,units

    def freeze_retry_policy(self,manifest):
        value={**POLICY,"authorized_date":"2026-09-25",
            "original_manifest_sha256":hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
            "max_requests":MAX_REQUESTS,"max_bytes":MAX_BYTES,"max_seconds":MAX_SECONDS,
            "original_authorization_clock_preserved":True}
        path=self.work/"retry-policy.json"
        if path.exists():
            if json.loads(path.read_text())!=value:raise ValueError("retry_policy_drift")
        else:atomic_json(path,value)

    def gap_event(self,symbol,session,phase,*,reason=None,method=None,outcome="missing"):
        event={"symbol":symbol,"session":session,"phase":phase,"outcome":outcome,
            "reason":reason,"method":method,"at":datetime.now(timezone.utc).isoformat()}
        with (self.work/"gap-events.jsonl").open("a") as f:
            f.write(canonical_json(event)+"\n");f.flush();os.fsync(f.fileno())
        return event

    def gap_state(self,allowed):
        gaps={(g["symbol"],g["session"]):g for g in self.status["gaps"]}
        finished=set()
        path=self.work/"gap-events.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                event=json.loads(line);key=(event["symbol"],event["session"])
                if key not in allowed:raise ValueError("gap_outside_manifest")
                if event["outcome"]=="resolved":gaps.pop(key,None)
                elif event["outcome"]=="missing":gaps[key]=event
                else:raise ValueError("unknown_gap_outcome")
                if event["phase"]=="recovery":finished.add(key)
        if not set(gaps)<=allowed:raise ValueError("gap_outside_manifest")
        return gaps,finished

    def run(self):
        os.umask(0o077)
        lock=(self.work/"run.lock").open("a+b")
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close();raise RuntimeError("etf_history_job_already_running") from None
        transport=None
        try:
            self.reload_durable_state();self._stop.clear();self._failure=None
            self.store.initialize();self.reconcile_cleanup()
            if self.status.get("state") in ("completed","completed_with_gaps"):return self.status
            manifest,units=self.load_plan();self.freeze_retry_policy(manifest)
            self.requests=EtfRequests(self)
            references={symbol:json.loads((self.work/"references"/symbol/"spot-references.json").read_text())
                        for symbol in EXPANSION_ETFS}
            placeholders=",".join("?" for _ in EXPANSION_ETFS)
            completed=set()
            by_symbol={symbol:{"planned":sum(s==symbol for s,d,p in units),"completed":0,
                "greeks_sessions":0,"prices_only_sessions":0,"gaps":0} for symbol in EXPANSION_ETFS}
            with self.store.read() as c:
                for row in c.execute(f"SELECT p.symbol,p.session_date,c.metadata FROM option_current p JOIN option_captures c USING(capture_id) WHERE p.symbol IN ({placeholders})",EXPANSION_ETFS):
                    completed.add((row["symbol"],row["session_date"]))
                    mode="greeks_sessions" if unpacked(row["metadata"])["coverage"]["greeks_contracts"] else "prices_only_sessions"
                    by_symbol[row["symbol"]]["completed"]+=1;by_symbol[row["symbol"]][mode]+=1
            allowed={(s,d) for s,d,p in units}
            gaps,recovery_finished=self.gap_state(allowed)
            for key in completed:gaps.pop(key,None)
            def progress():
                self.status.update(completed_sessions=len(completed),
                    processed_sessions=len(completed|set(gaps)),gaps=list(gaps.values()))
                for symbol in by_symbol:
                    by_symbol[symbol]["gaps"]=sum(s==symbol for s,d in gaps)
                self.status["by_symbol"]=by_symbol
                self.persist()
            started=self.status.get("authorization_started_at")
            if not started:
                started=datetime.now(timezone.utc).isoformat()
                self.status["authorization_started_at"]=started;self.persist()
            elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(started)).total_seconds()
            if elapsed>=MAX_SECONDS:raise RuntimeError("authorization_duration_exhausted")
            receipts=[]
            path=self.work/"request-receipts.jsonl"
            if path.exists():receipts=[json.loads(line) for line in path.read_text().splitlines()]
            budget=Budget(max_requests=MAX_REQUESTS,max_bytes=MAX_BYTES,max_seconds=int(MAX_SECONDS-elapsed),
                requests=max(len(receipts),len(self.history)),
                received_bytes=sum(r["response_bytes"] for r in receipts))
            self._budget=budget;budget.check()
            transport=self.transport_factory(self.root,budget,receipt_callback=self.receipt,symbols=EXPANSION_ETFS)
            self.status.pop("error",None);self.status.pop("download_failure",None)
            self.status.update(state="running",planned_sessions=len(units),download_workers=DOWNLOAD_WORKERS,
                max_pending_sessions=DOWNLOAD_WORKERS,subscription_code=transport.subscription_code,
                automatic_retries=1,recovery_passes=1,retry_policy=POLICY["contract"],
                run_segment_started_at=datetime.now(timezone.utc).isoformat(),run_segment_start_sessions=len(completed),
                manifest_sha256=hashlib.sha256(canonical_json(manifest).encode()).hexdigest())
            progress()
            for phase in ("main","recovery"):
                tasks=[(s+"|"+d,p) for s,d,p in units if (s,d) not in completed and
                    ((s,d) not in gaps if phase=="main" else (s,d) in gaps and (s,d) not in recovery_finished)]
                self.status.update(phase=phase,phase_planned_sessions=len(tasks),phase_processed_sessions=0)
                self._stop.clear();self.persist()
                downloads=self.downloaded_sessions(transport,tasks)
                try:
                    for unit,previous,acquired in downloads:
                        symbol,session=unit.split("|");key=(symbol,session)
                        failure=acquired.get("gap")
                        if failure is None:
                            try:
                                capture=build_capture(session,acquired["greeks"],acquired["oi"],acquired["receipts"],
                                    previous_session=previous,spot_reference=references[symbol].get(session),symbol=symbol)
                            except ValueError as exc:
                                if str(exc) not in QUALITY_GAPS:raise
                                failure={"reason":str(exc),"method":None}
                        if failure is not None:
                            gaps[key]=self.gap_event(symbol,session,phase,**failure)
                        else:
                            self.prepare_cleanup(capture,acquired["paths"])
                            result=self.store.publish(capture)
                            if not self.reconcile_cleanup():raise RuntimeError("cleanup_unverified_capture")
                            completed.add(key)
                            mode="greeks_sessions" if capture["coverage"]["greeks_contracts"] else "prices_only_sessions"
                            by_symbol[symbol][mode]+=1;by_symbol[symbol]["completed"]+=1
                            if key in gaps:
                                self.gap_event(symbol,session,phase,outcome="resolved");gaps.pop(key)
                            self.status.update(last_published_symbol=symbol,last_published_session=session,
                                last_capture_id=result["capture_id"],last_coverage=capture["coverage"],
                                database_bytes=self.store.path.stat().st_size)
                            del capture
                        if phase=="recovery":recovery_finished.add(key)
                        self.status["phase_processed_sessions"]+=1
                        progress()
                        if self.status["phase_processed_sessions"]%50==0:
                            print(canonical_json({k:self.status[k] for k in
                                ("state","phase","completed_sessions","processed_sessions","planned_sessions",
                                 "data_requests","retry_requests")}),flush=True)
                        del acquired
                finally:downloads.close()
            self.status.update(state="finalizing",active_session=None,active_sessions=[],
                recovery_finished_sessions=len(recovery_finished))
            self.persist()
            self.status["database"]=self.store.status()
            self.status["backup"]=self.store.backup(self.work/"completed-options-backup.sqlite")
            self.status["state"]="completed" if not gaps else "completed_with_gaps"
            self.persist()
            return self.status
        except Exception as exc:
            self.status.update(state="stopped",error=exc.code if isinstance(exc,ProviderFailure) else
                str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__)
            if self._failure is not None:self.status["download_failure"]=self._failure
            self.persist();raise
        finally:
            if transport:transport.close()
            fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()
