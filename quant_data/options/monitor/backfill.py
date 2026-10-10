"""Finite historical volume backfill with explicitly enabled bounded recovery."""
from __future__ import annotations
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import argparse,fcntl,json,time,threading,signal,os,math
from concurrent.futures import ThreadPoolExecutor,wait as wait_futures,FIRST_COMPLETED
from collections import deque
from contextlib import contextmanager
from .admission import ThetaAdmission,BackgroundBusy
from .baseline import VERSION,expirations,aggregate_expiration,completed_sessions
from .baseline_store import BaselineStore,attempt_part,retryable,blocked_setup
from .model import canonical
from .policy import NY,load_universe,validate_watchlist
from .site import SiteClient,connection
from .transport import MonitorThetaTransport,SessionSetupFailure
from quant_data.options.transport import Budget,ProviderFailure
from quant_data.stores import StoreWriteLock

class BaselineThetaTransport(MonitorThetaTransport):
    def request(self,method,**params):
        if params.get("symbol") not in self.symbols:raise ValueError("baseline_symbol_scope")
        day=params.get("date")
        if type(day) is not date or day>=datetime.now(NY).date():raise ValueError("baseline_completed_session")
        if method=="option_list_contracts":
            if set(params)!={"symbol","date","request_type"} or params["request_type"]!="trade":raise ValueError("baseline_catalog_scope")
        elif method=="option_history_ohlc":
            expected={"symbol","expiration","date","interval","strike","right","start_time","end_time"}
            if set(params)!=expected or type(params["expiration"]) is not date or params["expiration"]<day or params["interval"]!="5m" or params["strike"]!="*" or params["right"]!="both" or params["start_time"]!="09:30:00" or params["end_time"] not in ("13:00:00","16:00:00"):
                raise ValueError("baseline_full_chain_scope")
        else:raise ValueError("baseline_endpoint_not_allowed")
        return self._request(method,**params)

def outside_session(now):
    local=now.astimezone(NY)
    return local.weekday()>=5 or local.hour<9 or local.hour>=17

def backfill_window(now,*,allow_daytime=False):
    """Leave the known recurring Theta jobs their own login/request window."""
    local=now.astimezone(NY);minute=local.hour*60+local.minute
    if not allow_daytime:
        if not outside_session(now):return False
        if local.weekday()<5 and 8*60+55<=minute<9*60:return False
    # Daily Tue–Sat 07:15, max 20 minutes; Sunday 03:00, max 60 minutes.
    # Five minutes on either side exceeds the 60-second in-flight RPC limit.
    if local.weekday() in range(1,6) and 7*60+10<=minute<7*60+40:return False
    if local.weekday()==6 and 2*60+55<=minute<4*60+5:return False
    return True

class Backfill:
    def __init__(self,store,run_id,*,factory=BaselineThetaTransport,clock=lambda:datetime.now(timezone.utc),wait=False,sleep=time.sleep):
        self.store=store;self.run_id=run_id;self.plan=store.plan(run_id);self.factory=factory;self.clock=clock;self.wait=wait;self.sleep=sleep
        used,received=store.usage(run_id,self.plan["max_bytes"])
        seconds=(datetime.fromisoformat(self.plan["deadline"])-clock()).total_seconds()
        self.budget=Budget(self.plan["max_requests"],self.plan["max_bytes"],seconds,requests=used,received_bytes=received)
        self.transport=None
        self.recovery=store.recovery_policy(run_id)
    def close(self):
        if self.transport:self.transport.close()
        self.transport=None
    def request(self,scope_symbol,session,part,method,reduce,**params):
        symbol=scope_symbol
        previous=self.store.part(symbol,session,part)
        if previous is not None:
            if previous[0]!="success" or previous[1] is None:raise ValueError("baseline_previous_attempt_unusable")
            return previous[1]
        self.budget.check()
        while not outside_session(self.clock()) and not self.plan["pilot"]:
            self.close()
            if not self.wait:raise RuntimeError("baseline_market_open")
            self.budget.check();self.sleep(30)
        self.budget.check()
        if not self.store.reserve_part(symbol,session,part,self.run_id,self.clock().isoformat()):raise ValueError("baseline_concurrent_attempt")
        receipt={};dispatched=False
        try:
            if self.transport is None:self.transport=self.factory(self.store.root,self.budget,symbols=self.plan["symbols"])
            dispatched=True
            rows,receipt=self.transport.request(method,**params)
            result=reduce(rows)
            self.store.finish_part(symbol,session,part,"success",result,receipt)
            return result
        except BaseException as exc:
            if isinstance(exc,ProviderFailure):receipt=exc.receipt
            if not dispatched:
                receipt={**receipt,"response_bytes":0,"stage":"session_setup","data_dispatched":False}
                with self.budget._lock:self.budget.requests+=1
            # A dispatched request without a receipt has unknown transfer volume.
            if not receipt:receipt={"response_bytes":self.plan["max_bytes"]}
            self.store.finish_part(symbol,session,part,"ProviderFailure" if isinstance(exc,ProviderFailure) else type(exc).__name__,None,receipt)
            raise
    def publish(self,site,*,batch_size=25):
        remaining=(datetime.fromisoformat(self.plan["deadline"])-self.clock()).total_seconds()
        if remaining<=0:raise RuntimeError("duration_cap")
        return self.store.publish_days(site,limit=batch_size,timeout=min(15,remaining),deadline=datetime.fromisoformat(self.plan["deadline"]),clock=self.clock,run_id=self.run_id)
    def upload(self,site_factory,*,report=lambda value:print(canonical(value),flush=True)):
        """Final publication only; this method never constructs a Theta client."""
        status=self.store.collection_status(self.run_id);uploaded=0
        if not status["collection_complete"]:
            result={**status,"upload_complete":False,"uploaded_this_phase":0,"upload_error":"collection_incomplete"}
            report({"phase":"upload",**result});return result
        try:
            if not self.store.pending_days(limit=1,run_id=self.run_id):
                result={**status,"upload_complete":True,"uploaded_this_phase":0}
            else:
                if self.clock()>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
                if getattr(self,"stopping",None) is not None and self.stopping.is_set():raise RuntimeError("baseline_stopped")
                # Site configuration is intentionally lazy: collection and local
                # checkpoints never depend on credentials or an available Site.
                site=site_factory()
                while True:
                    if getattr(self,"stopping",None) is not None and self.stopping.is_set():raise RuntimeError("baseline_stopped")
                    count=self.publish(site)
                    if not count:break
                    uploaded+=count
                    report({"phase":"upload","uploaded_this_phase":uploaded})
                result={**status,"upload_complete":True,"uploaded_this_phase":uploaded}
        except Exception as exc:
            # A failed/uncertain POST remains pending. No immediate retry; the
            # explicit upload command resumes from acknowledged chunks only.
            result={**status,"upload_complete":False,"uploaded_this_phase":uploaded,"upload_error":type(exc).__name__}
        report({"phase":"upload",**result});return result

    def day(self,symbol,session,close_minute):
        if self.store.has_day(symbol,session):return False
        expiry=self.request(symbol,session,"catalog","option_list_contracts",lambda rows:expirations(rows,symbol,session),
            symbol=symbol,date=date.fromisoformat(session),request_type="trade")
        if not expiry:raise ValueError("baseline_empty_catalog")
        total=[[m,0,0] for m in range(570,close_minute,5)]
        for expiration in expiry:
            bins=self.request(symbol,session,expiration,"option_history_ohlc",
                lambda rows:aggregate_expiration(rows,symbol,session,expiration,close_minute),
                symbol=symbol,date=date.fromisoformat(session),expiration=date.fromisoformat(expiration),
                interval="5m",strike="*",right="both",start_time="09:30:00",end_time="13:00:00" if close_minute==780 else "16:00:00")
            for out,row in zip(total,bins):out[1]+=row[1];out[2]+=row[2]
        return self.store.save_day({"version":VERSION,"symbol":symbol,"session":session,"close_minute":close_minute,"bins":total})


class MarketPause(RuntimeError):
    """Drain current calls and renew the shared session after the daytime pause."""

class ParallelBackfill(Backfill):
    """Ten work slots, at most four active calls sharing one login.

    The coordinator publishes only after local collection. Each admission is durable before network;
    completed/failed admissions remain authoritative across all resumes.
    """
    def __init__(self,*args,workers=10,api_concurrency=4,factory_provider=None,**kwargs):
        if not 1<=workers<=10 or not 1<=api_concurrency<=min(4,workers):
            raise ValueError("baseline_concurrency")
        super().__init__(*args,**kwargs)
        self.workers=workers;self.api_concurrency=api_concurrency
        self._factory_provider=factory_provider or BaselineThetaTransport.session_factory
        self._factory=self._factory_provider()
        self._local=threading.local();self._transports=[]
        self._gate=threading.BoundedSemaphore(api_concurrency)
        self._admission=threading.Lock();self.stopping=threading.Event()
        self.paused=threading.Event()
        self._remaining=self.plan["max_requests"]-self.budget.requests
        self._cooldown_until=None;self._transient_streak=0;self._retry_after={}
        self._report=lambda value:None
        self.daytime_policy=self.store.daytime_policy(self.run_id)
        self._coordination=ThetaAdmission(self.store.root) if self.daytime_policy else None
        self._session_epoch=None
        self._central_session=bool(self.recovery and self.recovery.get("central_session_recovery"))
        self._session_ready=False;self._reset_session=False

    def close(self):
        # Called only by the coordinator after all worker futures have drained.
        for transport in self._transports:transport.close()
        self._transports.clear();self._local=threading.local()

    def request_capacity(self,now=None):
        if self._coordination is None:return self.api_concurrency
        return min(self.api_concurrency,self._coordination.backfill_capacity(now or self.clock(),self.run_id))

    def _window(self,now):
        return backfill_window(now,allow_daytime=bool(self.daytime_policy))

    @contextmanager
    def _shared_capacity(self):
        if self._coordination is None:
            yield None;return
        while True:
            if self.stopping.is_set():raise RuntimeError("baseline_stopped")
            self.budget.check()
            if self.clock()>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
            if self.paused.is_set() or not self._window(self.clock()):
                self.paused.set();raise MarketPause("baseline_window")
            try:
                with self._coordination.background(self.clock(),run_id=self.run_id) as epoch:
                    yield epoch
                return
            except BackgroundBusy as exc:
                if str(exc)=="live_collection":
                    self._reset_session=True
                    self.paused.set();raise MarketPause("baseline_live_priority") from None
                # Capacity contention precedes any durable request admission.
                # The cycle deadline and stop flag remain active while queued.
                self.sleep(.05)

    def _retry_delays(self,code):
        return self.recovery.get("session_delays_seconds",self.recovery["delays_seconds"]) if code.startswith("SESSION_") else self.recovery["delays_seconds"]

    def _backoff(self,code,attempt,symbol,session,part):
        with self._admission:
            self._transient_streak+=1
            delay=self._retry_delays(code)[min(attempt-1,1)]
            if self._transient_streak>=8:
                delay=max(delay,min(self.recovery["circuit_max_seconds"],60*2**min(3,(self._transient_streak-8)//4)))
            until=self.clock()+timedelta(seconds=delay)
            self._cooldown_until=max(self._cooldown_until or until,until)
            self.paused.set()
        self._report({"phase":"retrying","error":code,"attempt":attempt,"symbol":symbol,"session":session,"part":part,
            "cooldown_until":self._cooldown_until.isoformat()})

    def _retry_time(self,state,key,delay):
        receipt=state["receipt"] or {};ends=[]
        for field in ("finished_at","captured_at"):
            try:
                stamp=datetime.fromisoformat(receipt[field])
                if stamp.tzinfo is None:continue
                if field=="captured_at":
                    elapsed=receipt["elapsed_seconds"]
                    if isinstance(elapsed,bool) or not isinstance(elapsed,(int,float)) or not math.isfinite(elapsed) or elapsed<0:continue
                    stamp+=timedelta(seconds=elapsed)
                ends.append(stamp)
            except (KeyError,TypeError,ValueError,OverflowError):continue
        if ends:return max(ends)+timedelta(seconds=delay)
        # Old receipts without trustworthy completion timing get a full wait
        # from first inspection. Restarting can extend, never shorten, it.
        with self._admission:
            return self._retry_after.setdefault(key,self.clock()+timedelta(seconds=delay))

    def request(self,scope_symbol,session,part,method,reduce,**params):
        state=self.store.attempt_state(scope_symbol,session,part);attempt=1
        if state is not None:
            if state["outcome"]=="success" and state["payload"] is not None:return state["payload"]
            if not retryable(state,self.run_id,self.recovery):
                if blocked_setup(state,self.run_id,self.recovery):
                    raise ProviderFailure(state["receipt"].get("outcome") or "SESSION_SETUP_FAILED",state["receipt"])
                raise ValueError("baseline_previous_attempt_unusable")
            attempt=state["attempt"]+1
            # Restarting cannot bypass the persisted attempt count or its delay.
            retry_key=(scope_symbol,session,part,attempt)
            earliest=self._retry_time(state,retry_key,self._retry_delays((state["receipt"] or {}).get("outcome","INTERRUPTED_APPROVED"))[min(attempt-2,1)])
            if self.clock()<earliest:
                with self._admission:
                    self._cooldown_until=max(self._cooldown_until or earliest,earliest);self.paused.set()
                raise MarketPause("baseline_retry_backoff")
        physical_part=attempt_part(part,attempt)
        with self._gate,self._shared_capacity() as epoch:
            with self._admission:
                if self._coordination is not None:
                    if self._session_epoch is None:self._session_epoch=epoch
                    elif self._session_epoch!=epoch:
                        self._reset_session=True
                        self.paused.set();raise MarketPause("baseline_session_changed")
                if self.stopping.is_set():raise RuntimeError("baseline_stopped")
                if self.paused.is_set() or (not self.plan["pilot"] and not self._window(self.clock())):
                    self.paused.set();raise MarketPause("baseline_market_open")
                self.budget.check()
                if self._remaining<=0:raise RuntimeError("request_cap")
                when=self.clock().isoformat()
                admitted=(self.store.reserve_part(scope_symbol,session,part,self.run_id,when) if attempt==1 else
                    self.store.reserve_retry(scope_symbol,session,part,self.run_id,when,attempt))
                if not admitted:raise ValueError("baseline_concurrent_attempt")
                self._remaining-=1
                if attempt>1:self._retry_after.pop((scope_symbol,session,part,attempt),None)
            receipt={};dispatched=False
            try:
                transport=getattr(self._local,"transport",None)
                if transport is None:
                    transport=self._factory(self.store.root,self.budget,symbols=self.plan["symbols"])
                    self._local.transport=transport
                    with self._admission:self._transports.append(transport)
                dispatched=True
                rows,receipt=transport.request(method,**params)
                result=reduce(rows)
                self.store.finish_part(scope_symbol,session,physical_part,"success",result,receipt)
                if hasattr(transport,"receipts"):transport.receipts.clear()
                with self._admission:self._transient_streak=0
                return result
            except BaseException as exc:
                if isinstance(exc,ProviderFailure):receipt={**exc.receipt,"outcome":exc.code}
                if not dispatched:
                    # Setup failed before transport.request; no data could have
                    # arrived. Keep the admission charged against the call cap.
                    receipt={**receipt,"response_bytes":0,"stage":"session_setup","data_dispatched":False}
                    with self.budget._lock:self.budget.requests+=1
                if not receipt:receipt={"response_bytes":self.plan["max_bytes"]}
                receipt={**receipt,"finished_at":self.clock().isoformat()}
                self.store.finish_part(scope_symbol,session,physical_part,"ProviderFailure" if isinstance(exc,ProviderFailure) else type(exc).__name__,None,receipt)
                recoverable=(isinstance(exc,ProviderFailure) and self.recovery
                    and exc.code in self.recovery["retry_codes"]
                    and type(receipt.get("response_bytes")) is int and receipt["response_bytes"]>=0
                    and (not exc.code.startswith("SESSION_") or
                         (not dispatched and receipt.get("stage")=="session_setup" and receipt.get("data_dispatched") is False)))
                if recoverable:
                    self._backoff(exc.code,attempt,scope_symbol,session,part)
                    if attempt<self.recovery["max_attempts"]:raise MarketPause("baseline_transient_retry") from None
                    if exc.code.startswith("SESSION_"):
                        self.stopping.set();raise
                    raise ValueError("baseline_retry_exhausted:"+exc.code) from None
                if not isinstance(exc,ValueError):self.stopping.set()
                raise

    def _prepare_session(self):
        """Coordinator-only login; no contract admission exists before it succeeds."""
        with self._gate,self._shared_capacity() as epoch:
            if self._session_ready and self._session_epoch==epoch:return
            if self._session_ready:
                self._factory=self._factory_provider();self._session_ready=False
            self._session_epoch=epoch
            state=self.store.connection_state(self.run_id)
            if state["blocked"]:raise ProviderFailure("SESSION_RECOVERY_EXHAUSTED",{})
            if state["pending"]:
                delays=self.recovery["connection_delays_seconds"]
                delay=delays[min(state["consecutive_failures"]-1,len(delays)-1)]
                until=datetime.fromisoformat(state["last_at"])+timedelta(seconds=delay)
                if self.clock()<until:
                    self._cooldown_until=until;self.paused.set()
                    raise MarketPause("baseline_connection_backoff")
            if self.stopping.is_set():raise RuntimeError("baseline_stopped")
            self.budget.check()
            if self._remaining<=0:raise RuntimeError("request_cap")
            sequence=self.store.reserve_connection(self.run_id,self.clock().isoformat())
            self._remaining-=1
            with self.budget._lock:self.budget.requests+=1
            try:
                if self.clock()>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
                transport=self._factory(self.store.root,self.budget,symbols=self.plan["symbols"])
            except BaseException as exc:
                code=exc.code if isinstance(exc,SessionSetupFailure) else str(exc) if str(exc) in ("duration_cap","baseline_stopped") else "SESSION_SETUP_FAILED"
                state=self.store.finish_connection(self.run_id,sequence,code,self.clock().isoformat())
                self._reset_session=True
                if code in ("SESSION_TIMEOUT","SESSION_CONNECTION_ERROR") and not state["blocked"]:
                    delays=self.recovery["connection_delays_seconds"]
                    delay=delays[min(state["consecutive_failures"]-1,len(delays)-1)]
                    self._cooldown_until=self.clock()+timedelta(seconds=delay);self.paused.set()
                    self._report({"phase":"waiting","reason":"session_reconnect","error":code,
                        "session_attempt":state["consecutive_failures"],
                        "session_max_attempts":self.recovery["connection_max_attempts"],
                        "resume_not_before":self._cooldown_until.isoformat(),**self.budget.snapshot()})
                    raise MarketPause("baseline_connection_backoff") from None
                raise
            self._transports.append(transport)
            self.store.finish_connection(self.run_id,sequence,"success",self.clock().isoformat())
            self._session_ready=True
            if self.clock()>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
            self._report({"phase":"collection","connection_restored":True,
                "connection_admissions":sequence,"upload_deferred":True,**self.budget.snapshot()})

    def _wait_for_window(self):
        renew=self.paused.is_set() and not self._central_session
        while True:
            now=self.clock()
            if self.stopping.is_set():raise RuntimeError("baseline_stopped")
            self.budget.check()
            if now>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
            market_wait=not self.plan["pilot"] and not self._window(now)
            cooling=self._cooldown_until is not None and now<self._cooldown_until
            live_wait=self._coordination is not None and self._coordination.live_pending()
            if not market_wait and not cooling and not live_wait:break
            if not self._central_session or market_wait or live_wait:renew=True
            if market_wait and not self.wait:raise RuntimeError("baseline_market_open")
            until=max(now,self._cooldown_until or now)
            if not self.plan["pilot"] and not self._window(until):
                until=until.replace(second=0,microsecond=0)
                while not self._window(until):until+=timedelta(minutes=1)
            reason=("scheduled_theta_job" if self.daytime_policy else "market_hours") if market_wait else "provider_backoff" if cooling else "live_collection"
            event={"phase":"waiting","reason":reason,**self.budget.snapshot()}
            if not live_wait or market_wait or cooling:event["resume_not_before"]=until.isoformat()
            self._report(event)
            delay=max(.05,(until-now).total_seconds()) if market_wait or cooling else 5
            self.sleep(min(5 if live_wait else 30,delay))
        if renew or self._reset_session:
            self._factory=self._factory_provider()
            self._session_epoch=None;self._session_ready=False;self._reset_session=False
        self._cooldown_until=None;self.paused.clear()
        self._report({"phase":"collection","upload_deferred":True,
            "request_concurrency":self.request_capacity(now),
            **self.budget.snapshot()})

    def run(self,site_factory,report=lambda value:print(canonical(value),flush=True)):
        self._report=report
        # Check the entire run before spawning workers: a fatal setup part may
        # occur late in the manifest. Restart must not bypass it on other days.
        initial_status=self.store.collection_status(self.run_id)
        if initial_status["setup_blocked_days"] or initial_status["connection_blocked"]:
            raise ProviderFailure("SESSION_RECOVERY_BLOCKED",{})
        if initial_status["collection_complete"]:
            self.budget.check()
            if self.clock()>=datetime.fromisoformat(self.plan["deadline"]):raise RuntimeError("duration_cap")
            return {"added":0,"skipped":0,**self.upload(site_factory,report=report)}
        added=failed=0
        report({"phase":"collection","upload_deferred":True,"scheduling":"continuous"})
        # Only ten unfinished ticker-days are submitted at once. Reusing the
        # pool/channels keeps the next ticker moving while a slow day finishes.
        work=iter((symbol,day) for symbol in self.plan["symbols"] for day in self.plan["sessions"])
        remaining={symbol:len(self.plan["sessions"]) for symbol in self.plan["symbols"]}
        deferred=deque();exhausted=False
        while not exhausted or deferred:
            self._wait_for_window();fatal=None
            if self._central_session:
                try:self._prepare_session()
                except MarketPause:
                    self.close();continue
                except BaseException:
                    self.close();raise
            try:
                with ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix="baseline") as pool:
                    futures={}
                    def fill():
                        nonlocal exhausted
                        while len(futures)<self.workers and not self.paused.is_set() and not self.stopping.is_set():
                            if deferred:item=deferred.popleft()
                            elif not exhausted:
                                try:item=next(work)
                                except StopIteration:exhausted=True;break
                            else:break
                            symbol,day=item
                            futures[pool.submit(self.day,symbol,**day)]=item
                    fill();heartbeat=time.monotonic()+30
                    while futures:
                        done,_=wait_futures(futures,timeout=max(.01,heartbeat-time.monotonic()),return_when=FIRST_COMPLETED)
                        if time.monotonic()>=heartbeat:
                            report({"phase":"draining" if self.paused.is_set() else "collection",
                                "upload_deferred":True,**self.budget.snapshot()})
                            heartbeat=time.monotonic()+30
                        for future in done:
                            symbol,day=futures.pop(future);completed=True
                            try:added+=int(future.result())
                            except MarketPause:
                                deferred.append((symbol,day));completed=False
                            except ValueError as exc:
                                failed+=1;report({"phase":"collection","symbol":symbol,**day,"skipped":str(exc)})
                            except BaseException as exc:
                                completed=False;self.stopping.set()
                                # Prefer the original failure over other workers
                                # observing the coordinated stop flag.
                                if fatal is None or str(fatal)=="baseline_stopped":fatal=exc
                            if completed:
                                remaining[symbol]-=1
                                if not remaining[symbol]:
                                    report({"phase":"collection","symbol":symbol,"added":added,"skipped":failed,**self.budget.snapshot()})
                        if fatal is None:fill()
            finally:
                # Pause, stop and final completion all drain the pool before
                # closing channels. Durable receipts precede every resume.
                self.close()
            if fatal is not None:raise fatal
            if deferred:
                report({"paused":True,"unfinished_days":len(deferred)})
        report({"phase":"collection","pass_complete":True,"added":added,"skipped":failed,**self.budget.snapshot()})
        return {"added":added,"skipped":failed,**self.upload(site_factory,report=report)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    commands=parser.add_subparsers(dest="command",required=True)
    plan=commands.add_parser("plan")
    plan.add_argument("--as-of",required=True)
    plan.add_argument("--symbols",nargs="+")
    plan.add_argument("--sessions",type=int,default=30)
    plan.add_argument("--max-requests",type=int,required=True)
    plan.add_argument("--max-bytes",type=int,required=True)
    plan.add_argument("--max-seconds",type=int,required=True)
    plan.add_argument("--pilot",action="store_true",help="Permit a bounded pilot during market hours (at most 50 requests and two ticker-days).")
    run=commands.add_parser("run");run.add_argument("--run-id",required=True);run.add_argument("--wait",action="store_true");run.add_argument("--workers",type=int,default=10);run.add_argument("--api-concurrency",type=int,default=4)
    run.add_argument("--recover-transient",action="store_true",help="Persist the authorized three-attempt transient recovery policy for this run.")
    run.add_argument("--daytime-backfill",action="store_true",help="Persist one-request daytime collection with live-cycle priority for this run.")
    upload=commands.add_parser("upload",help="Publish saved aggregates after collection, without Theta requests.");upload.add_argument("--run-id",required=True)
    show=commands.add_parser("status");show.add_argument("--run-id",required=True)
    args=parser.parse_args();store=BaselineStore(args.root)
    if args.command=="status":
        value=store.plan(args.run_id)
        with StoreWriteLock(store.path,timeout_seconds=15),store.read() as c:
            counts=dict(c.execute("SELECT outcome,COUNT(*) FROM baseline_requests WHERE run_id=? GROUP BY outcome",(args.run_id,)).fetchall())
            days=c.execute("SELECT COUNT(*),COALESCE(SUM(published),0) FROM baseline_days").fetchone()
        print(canonical({"run_id":args.run_id,"requests":counts,"days":days[0],"published":days[1],"deadline":value["deadline"],**store.collection_status(args.run_id)}));return
    # One backfill owner. Never take the live collector's long-lived run lock.
    store.path.parent.mkdir(parents=True,exist_ok=True)
    with (store.path.parent/"baseline.lock").open("a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store.initialize_baseline()
        if args.command=="plan":
            if not 1<=args.sessions<=30 or not 1<=args.max_requests<=1000000 or not 1<=args.max_bytes<=256*1024**3 or not 1<=args.max_seconds<=14*86400:raise ValueError("baseline_plan_caps")
            now=datetime.now(timezone.utc)
            if date.fromisoformat(args.as_of)>now.astimezone(NY).date():raise ValueError("baseline_future_plan")
            universe=load_universe(store.root/"config/options_monitor_universe.json")
            if args.symbols:
                symbols=list(dict.fromkeys(args.symbols))
                if not set(symbols)<=set(universe):raise ValueError("baseline_universe_scope")
            else:
                site=SiteClient(connection(store.path.parent/"connection.json"))
                watch=validate_watchlist(site.config_get(),universe)
                symbols=sorted(universe,key=lambda s:(0 if universe[s]["group"]!="equity" else 1 if s in watch else 2,s))
            if args.pilot and (len(symbols)*args.sessions>2 or args.max_requests>50):raise ValueError("baseline_pilot_cap")
            asof=date.fromisoformat(args.as_of)
            with StoreWriteLock(store.path,timeout_seconds=15):
                years={y:store.calendar_year(y) for y in {asof.year,(asof-timedelta(days=70)).year}}
            sessions=completed_sessions(args.as_of,years,args.sessions)
            value={"version":1,"symbols":symbols,"sessions":sessions,"pilot":args.pilot,
                "max_requests":args.max_requests,"max_bytes":args.max_bytes,
                "created_at":now.isoformat(),"deadline":(now+timedelta(seconds=args.max_seconds)).isoformat()}
            identity=store.create_run(value)
            print(canonical({"run_id":identity,**value}),flush=True);return
        site_factory=lambda:SiteClient(connection(store.path.parent/"connection.json"))
        if args.command=="upload":
            job=Backfill(store,args.run_id)
            result=job.upload(site_factory)
            if not result["upload_complete"]:raise SystemExit(2)
            return
        if args.recover_transient:store.configure_recovery(args.run_id)
        if args.daytime_backfill:store.configure_daytime(args.run_id)
        job=ParallelBackfill(store,args.run_id,wait=args.wait,workers=args.workers,api_concurrency=args.api_concurrency)
        status_path=store.path.parent/"baseline-status.json";report_lock=threading.Lock()
        def report(value):
            with report_lock:
                value={"run_id":args.run_id,"pid":os.getpid(),"checked_at":datetime.now(timezone.utc).isoformat(),
                    "daytime_backfill":bool(job.daytime_policy),
                    "request_concurrency":job.request_capacity(),
                    **value}
                temporary=status_path.with_suffix(".tmp")
                temporary.write_text(canonical(value)+"\n");os.replace(temporary,status_path)
                print(canonical(value),flush=True)
        previous={sig:signal.signal(sig,lambda *_:job.stopping.set()) for sig in (signal.SIGTERM,signal.SIGINT)}
        print(canonical({"run_id":args.run_id,"workers":job.workers,"api_concurrency":job.api_concurrency,
            "deadline":job.plan["deadline"],**job.budget.snapshot()}),flush=True)
        try:
            result=job.run(site_factory,report=report)
            report({"phase":"complete" if result["upload_complete"] else "upload_pending",**result})
            if not result["upload_complete"]:raise SystemExit(2)
        except Exception as exc:
            report({"phase":"stopped","error":exc.code if isinstance(exc,ProviderFailure) else type(exc).__name__,
                **job.budget.snapshot()})
            raise
        finally:
            job.close()
            for sig,handler in previous.items():signal.signal(sig,handler)

if __name__=="__main__":main()
