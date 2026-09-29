"""One normal-clock monitoring cycle with bounded network acquisition."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,date,time as daytime,timedelta
from pathlib import Path
import fcntl,os,threading,time
from quant_data.options.transport import Budget,ProviderFailure
from .policy import NY,load_policy,load_universe,validate_watchlist,market_window,previous_session,slot_for,radar_offset
from .model import aggregate,derive,alert_for
from .store import MonitorStore
from .site import SiteClient,connection
from .transport import MonitorThetaTransport
from .push import send_push,validate_subscription

def utc_now():return datetime.now(timezone.utc)

class MonitorJob:
    def __init__(self,root:Path,*,transport_factory=MonitorThetaTransport,site_factory=SiteClient,clock=utc_now,push_sender=send_push):
        self.root=Path(root).resolve(strict=True)
        self.policy=load_policy(self.root/"config/options_monitor.json")
        self.universe=load_universe(self.root/"config/options_monitor_universe.json")
        self.store=MonitorStore(self.root)
        self.transport_factory=transport_factory;self.site_factory=site_factory
        self.clock=clock;self.push_sender=push_sender
    def _usage(self,session):
        with self.store.read() as c:
            requests=c.execute("SELECT COUNT(*) FROM attempts WHERE session=?",(session,)).fetchone()[0]
            received=c.execute("SELECT COALESCE(SUM(CAST(json_extract(payload,'$.response_bytes') AS INTEGER)),0) FROM receipts WHERE session=?",(session,)).fetchone()[0]
            unresolved=c.execute("""
                SELECT COUNT(*) FROM attempts a LEFT JOIN receipts r
                ON a.session=r.session AND a.symbol=r.symbol AND a.slot=r.slot AND a.method=r.method
                WHERE a.session=? AND r.method IS NULL""",(session,)).fetchone()[0]
        # A crash can lose the terminal receipt after network bytes arrived.
        received+=unresolved*self.policy["max_response_bytes_per_cycle"]
        return requests,received
    def _request(self,transport,session,scope_symbol,slot,method,*,admission=None,**params):
        # Admission is separate from Budget's transport-side charge: it prevents
        # exhausted capacity from leaving a durable, never-called attempt.
        permit=None
        if admission is not None:
            permit,budget=admission
            if not permit.acquire(blocking=False):return None
            try:budget.check()
            except RuntimeError:
                permit.release();return None
        # This commit precedes transport initialization and every provider call.
        if not self.store.reserve(session,scope_symbol,slot,method,self.clock().isoformat()):
            if permit is not None:permit.release()
            return None
        try:
            rows,receipt=transport.request(method,**params)
            self.store.finish(session,scope_symbol,slot,method,receipt["outcome"],receipt)
            return rows
        except ProviderFailure as exc:
            self.store.finish(session,scope_symbol,slot,method,exc.code,exc.receipt)
            raise
        except BaseException as exc:
            self.store.finish(session,scope_symbol,slot,method,type(exc).__name__,
                {"method":method,"captured_at":self.clock().isoformat(),"outcome":type(exc).__name__})
            raise
    def _heartbeat(self,site,session,now,status,requests):
        site.publish({"schema_version":1,"observations":[],"alerts":[],
            "collector":{"last_seen":now.isoformat(),"status":status,"session":session,
                         "message":("Market closed" if status=="market_closed" else "Collector needs attention; latest cycle did not complete" if status=="failed" else "Monitoring current market session"),
                         "requests":requests},
            "universe":list(self.universe.values())})
    def _publish_outbox(self,site,session,deadline,status):
        batches=0
        while time.monotonic()<deadline-16:
            now=self.clock()
            observations=self.store.pending("observation",now.isoformat(),100)
            alerts=self.store.pending("alert",now.isoformat(),100)
            ids=[x[0] for x in observations+alerts]
            if not ids and batches:break
            self.store.mark_attempt(ids,now.isoformat())
            payload={"schema_version":1,"observations":[x[1] for x in observations],
                     "alerts":[x[1] for x in alerts],
                     "collector":{"last_seen":now.isoformat(),"status":status,"session":session,
                                  "message":"Monitoring current market session","requests":self._usage(session)[0]},
                     "universe":list(self.universe.values())}
            site.publish(payload)
            self.store.mark_published(ids)
            batches+=1
            if not ids:break
        return batches
    def _push(self,alerts,subscriptions,private_key,deadline):
        if not alerts or not subscriptions or not private_key:return 0
        if not isinstance(subscriptions,list) or len(subscriptions)>10:raise ValueError("push_subscription_cap")
        vetted=[(s["id"],s) for s in subscriptions if validate_subscription(s)]
        sent=0;attempted=0
        for alert in alerts:
            for identity,subscription in vetted:
                if alert["symbol"] in subscription.get("disabled_symbols",[]):continue
                if attempted>=10 or time.monotonic()>=deadline-10:return sent
                if not self.store.reserve_push(alert["id"],identity,self.clock().isoformat()):continue
                attempted+=1
                try:
                    self.push_sender(subscription,alert,private_key)
                    self.store.finish_push(alert["id"],identity,"sent");sent+=1
                except Exception:
                    self.store.finish_push(alert["id"],identity,"failed")
        return sent
    def run(self):
        os.umask(0o077)
        cycle_started=time.monotonic()
        self.store.initialize()
        now=self.clock()
        if now.tzinfo is None:raise ValueError("monitor_clock")
        local=now.astimezone(NY);day=local.date();session=day.isoformat()
        site=self.site_factory(connection(self.root/"data/.operations/options-monitor/connection.json"))
        if day.weekday()>=5 or local.time()<daytime(9,30) or local.time()>daytime(16,15):
            self._heartbeat(site,session,now,"market_closed",0)
            result={"status":"market_closed","session":session,"requests":0}
            if self.policy.get("daily_history",{}).get("enabled"):
                from .history import sync_history
                try:result["daily_history"]=sync_history(self.root,site,now,max_batches=1,deadline=cycle_started+90)
                except Exception as exc:result["daily_history"]={"status":"unavailable","reason":type(exc).__name__}
            return result
        work=self.store.path.parent
        with (work/"run.lock").open("a+b") as handle:
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError("monitor_cycle_running") from None
            try:
                preflight=getattr(self.transport_factory,"preflight",None)
                if preflight is not None:preflight()
                return self._run_locked(site,now,session,day,cycle_started)
            except Exception:
                # Report failure without exposing exception text or attempting a
                # provider retry. Preserve the original exception if Site is down.
                try:self._heartbeat(site,session,self.clock(),"failed",self._usage(session)[0])
                except Exception:pass
                raise
    def _run_locked(self,site,now,session,day,cycle_started):
        session_factory=getattr(self.transport_factory,"session_factory",None)
        transport_factory=session_factory() if session_factory is not None else self.transport_factory
        config=site.config_get()
        watch=validate_watchlist(config,self.universe)
        used,received=self._usage(session)
        remaining_requests=min(self.policy["max_requests_per_cycle"],self.policy["max_requests_per_session"]-used)
        remaining_bytes=min(self.policy["max_response_bytes_per_cycle"],self.policy["max_response_bytes_per_session"]-received)
        if remaining_requests<=0 or remaining_bytes<=0:
            self._heartbeat(site,session,now,"partial",0)
            return {"status":"session_cap","session":session,"requests":0}
        budget=Budget(max_requests=remaining_requests,max_bytes=remaining_bytes,
                      max_seconds=self.policy["max_seconds_per_cycle"],started=cycle_started)
        deadline=cycle_started+self.policy["max_seconds_per_cycle"]
        control=None
        try:
            calendar=self.store.calendar(session)
            if calendar is None:
                # Calendar is reserved before creating the Theta client, whose auth is network work.
                if not self.store.reserve(session,"","calendar","calendar_on_date",now.isoformat()):
                    self._heartbeat(site,session,now,"failed",0)
                    return {"status":"calendar_attempted","session":session,"requests":0}
                control=transport_factory(self.root,budget,symbols=self.universe)
                try:
                    calendar,receipt=control.request("calendar_on_date",date=day)
                    self.store.finish(session,"","calendar","calendar_on_date",receipt["outcome"],receipt)
                except ProviderFailure as exc:
                    self.store.finish(session,"","calendar","calendar_on_date",exc.code,exc.receipt)
                    raise
                if not calendar:raise ValueError("calendar_empty")
                self.store.save_calendar(session,calendar)
            regular=market_window(day,calendar,extended=False)
            if regular is None:
                self._heartbeat(site,session,now,"market_closed",budget.requests)
                return {"status":"market_closed","session":session,"requests":budget.requests}
            late=market_window(day,calendar,extended=True)
            years={}
            for year in (day.year,day.year-1 if day.month==1 else day.year):
                if year in years:continue
                year_rows=self.store.calendar_year(year)
                if year_rows is None and budget.requests<budget.max_requests and time.monotonic()<deadline-70:
                    if control is None:control=transport_factory(self.root,budget,symbols=self.universe)
                    try:
                        budget.check()
                        year_rows=self._request(control,session,"",str(year),"calendar_year",year=str(year))
                        if year_rows:self.store.save_calendar_year(year,year_rows)
                    except (ProviderFailure,RuntimeError,ValueError):
                        year_rows=None
                if year_rows:years[year]=year_rows
            effective=previous_session(day,years)
        finally:
            if control is not None:control.close()
        top100=self.store.top_volume_symbols(effective.isoformat() if effective else None)
        attempts=self.store.last_attempts(session)
        candidates=[]
        for symbol,member in self.universe.items():
            tier=watch.get(symbol)
            cadence=min(member["cadence_minutes"],tier["cadence_minutes"]) if tier else member["cadence_minutes"]
            if symbol in top100:cadence=min(cadence,15)
            last_alert=self.store.last_alert(symbol)
            if last_alert and 0<=(now-datetime.fromisoformat(last_alert)).total_seconds()<3600:
                cadence=5
            window=late if symbol in ("SPY","QQQ","IWM","DIA") else regular
            slot=slot_for(now,cadence,*window)
            if slot is None:continue
            observation_slot=slot
            if member["group"]=="equity" and cadence==30 and tier is None:
                offset=timedelta(minutes=radar_offset(symbol))
                observation_slot=slot_for(now,cadence,window[0]+offset,window[1])
                if observation_slot is None:continue
                # Keep the original admission identity. A pre-rollout attempt
                # (successful or failed) must not be replayed at the new phase.
                admission_slot=observation_slot-offset
                # A formerly promoted ticker may already have attempted this
                # observation phase under its faster cadence.
                if observation_slot!=slot and self.store.attempted(
                    session,symbol,observation_slot.isoformat(),"option_snapshot_ohlc"):continue
                if admission_slot!=slot and self.store.attempted(
                    session,symbol,slot.isoformat(),"option_snapshot_ohlc"):continue
                slot=admission_slot
            slot_text=slot.isoformat()
            if self.store.attempted(session,symbol,slot_text,"option_snapshot_ohlc"):continue
            candidates.append((attempts.get(symbol,""),symbol,cadence,slot_text,member,last_alert,observation_slot.isoformat()))
        candidates.sort(key=lambda x:(x[2],x[0],x[1]))
        # All mutable-state reads precede workers. Workers only reserve/finalize
        # request attempts and return compact in-memory aggregates.
        plans=[]
        available=budget.max_requests-budget.requests
        for _,symbol,cadence,slot,member,last_alert,observation_slot in candidates:
            if available<=0:break
            old=self.store.prior(symbol,session)
            first=self.store.first(symbol,session)
            local_slot=datetime.fromisoformat(observation_slot).astimezone(NY)
            baselines=self.store.baselines(symbol,cadence,local_slot.hour*60+local_slot.minute,session)
            flagged=bool(old and (old.get("relative_volume") or 0)>=2)
            iv_needed=member["group"]!="equity" or symbol in watch or flagged
            quota=min(available,1+int(old is None)+int(iv_needed))
            available-=quota
            plans.append((symbol,cadence,slot,old,first,baselines,last_alert,iv_needed,
                          bool(watch.get(symbol,{}).get("alerts_enabled",True)),quota,observation_slot))
        permit=threading.BoundedSemaphore(max(0,budget.max_requests-budget.requests))
        locals_by_thread=threading.local();transports=[];transport_lock=threading.Lock()
        job=self
        class Lazy:
            def request(self,method,**params):
                transport=getattr(locals_by_thread,"transport",None)
                if transport is None:
                    transport=transport_factory(job.root,budget,symbols=job.universe)
                    locals_by_thread.transport=transport
                    with transport_lock:transports.append(transport)
                return transport.request(method,**params)
        lazy=Lazy()
        def acquire(plan):
            symbol,cadence,slot,old,first,baselines,last_alert,iv_needed,enabled,quota,observation_slot=plan
            if time.monotonic()>=deadline-70:return plan,None,"deadline"
            args={"symbol":symbol,"expiration":"*","strike":"*","right":"both"}
            try:
                rows=self._request(lazy,session,symbol,slot,"option_snapshot_ohlc",admission=(permit,budget),**args)
                if rows is None:return plan,None,"attempted"
                oi=None;consumed=1
                if old is None and consumed<quota and time.monotonic()<deadline-70:
                    oi=self._request(lazy,session,symbol,"daily","option_snapshot_open_interest",admission=(permit,budget),**args)
                    consumed+=1
                iv=None
                if iv_needed and consumed<quota and time.monotonic()<deadline-70:
                    iv=self._request(lazy,session,symbol,slot,"option_snapshot_greeks_first_order",
                        admission=(permit,budget),**args,max_dte=120,strike_range=10)
                current=aggregate(symbol,session,self.clock().isoformat(),rows,oi_rows=oi,iv_rows=iv,
                                  oi_effective_session=effective.isoformat() if effective else None)
                return plan,current,None
            except Exception as exc:
                return plan,None,type(exc).__name__
        acquired=[]
        try:
            with ThreadPoolExecutor(max_workers=self.policy["max_workers"],thread_name_prefix="options-monitor") as pool:
                futures=[pool.submit(acquire,plan) for plan in plans]
                for future in as_completed(futures):acquired.append(future.result())
        finally:
            for transport in transports:transport.close()
        completed=0;errors=0;error_types={}
        for plan,current,error in acquired:
            if current is None:
                errors+=1;error_types[error]=error_types.get(error,0)+1;continue
            symbol,cadence,slot,old,first,baselines,last_alert,iv_needed,enabled,quota,observation_slot=plan
            if old and current["_fingerprint"]==old["_fingerprint"]:
                current["open_interest"]=old.get("open_interest")
                current["oi_effective_date"]=old.get("oi_effective_date")
                total=(current["call_volume"] or 0)+(current["put_volume"] or 0)
                current["volume_oi"]=total/current["open_interest"] if current["open_interest"] and current["oi_effective_date"] and current["call_volume"] is not None and current["put_volume"] is not None else None
            observation=derive(current,old,baselines,first,cadence=cadence)
            observation["_slot"]=observation_slot
            for leader in observation["leaders"]:
                leader["right"]="call" if leader["right"]=="C" else "put"
            alert=alert_for(observation,last_alert,enabled=enabled)
            self.store.publish(observation,alert);completed+=1
        cycle_status="complete" if not errors and completed==len(plans)==len(candidates) else "partial"
        batches=self._publish_outbox(site,session,deadline,cycle_status)
        if time.monotonic()<deadline-10:
            recent=self.store.recent_alerts((self.clock()-timedelta(minutes=60)).isoformat())
            key=connection(self.root/"data/.operations/options-monitor/connection.json").get("vapid_private_key")
            self._push(recent,config.get("push_subscriptions",[]),key,deadline)
        self.store.prune(session,self.policy["retain_sessions"])
        return {"status":cycle_status,
                "session":session,"requests":budget.requests,"observations":completed,
                "received_bytes":budget.received_bytes,"publish_batches":batches,"errors":error_types,
                "capacity_deferred":len(candidates)-len(plans)}
