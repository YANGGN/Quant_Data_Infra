"""Finite concurrent FMP price acquisition under the existing account lock.

One parent owns quota reservations and original response retention. Bounded
child processes own HTTP only; no child receives a store or publisher. The
account lock spans a short batch, and legacy clients keep their existing guard.
"""
from datetime import datetime,timezone,date
from pathlib import Path
import hashlib,math,multiprocessing,select,time
from ..errors import ConflictError,ValidationError,ResourceLimitError,StoreUnavailableError
from ..json_codec import loads_strict,dumps_strict
from ..market.collection_universe import _utc
from .collection_plan import validate_unit
from .collection_provider_policy import MAX_LEDGER_BYTES
from .collection_queue import atomic,_retain,_read,QueueResponse,provider_stop,BoundedForwardClock
from .collection_transport import request_route,_http_once,_receive_until,ProviderRequestFailure
from .equibles_transcript_backfill import private_directory,job_lock,read_file

MAX_BATCH=100
MAX_WORKERS=24
ADMISSION_SECONDS=10

class HttpTicket:
    def __init__(self,unit,credential,deadline,*,diagnostics=False):
        self.diagnostics=diagnostics
        self.deadline=min(deadline,time.monotonic()+unit.timeout_seconds)
        remaining=math.floor(self.deadline-time.monotonic())
        if remaining<1:raise ResourceLimitError('Parallel request has no remaining deadline')
        context=multiprocessing.get_context('fork');self.receiver,sender=context.Pipe(duplex=False)
        self.child=context.Process(target=_http_once,args=(sender,request_route(unit),credential,remaining,unit.max_response_bytes,diagnostics))
        self.bound=unit.max_response_bytes;self.child.start();sender.close()
    def ready(self):return self.receiver.poll(0) or time.monotonic()>=self.deadline
    def result(self):
        try:
            try:value=_receive_until(self.receiver,self.deadline,self.bound)
            except (EOFError,OSError,ResourceLimitError) as error:
                if not self.diagnostics:raise
                raise ProviderRequestFailure('timeout' if isinstance(error,ResourceLimitError) else 'worker_lost',retryable=True) from None
            if self.diagnostics and isinstance(value,dict) and value.get('contract')=='quant_data.provider_failure.v1':
                if set(value)!={'contract','category','http_status','retryable'}:raise ValidationError('Invalid provider diagnostic')
                raise ProviderRequestFailure(value['category'],value['http_status'],retryable=value['retryable'])
            if not isinstance(value,tuple) or len(value)!=4:raise StoreUnavailableError('Parallel provider request failed')
            return QueueResponse(*value)
        finally:self.close()
    def close(self):
        self.receiver.close()
        if self.child.is_alive():self.child.terminate()
        self.child.join(timeout=0.2)
        if self.child.is_alive():self.child.kill();self.child.join()

def load_account(gate,*,observed_pause=None):
    state=loads_strict(read_file(gate.root/'allowance.json',MAX_LEDGER_BYTES),max_bytes=MAX_LEDGER_BYTES)
    if (not isinstance(state,dict) or set(state)!={'contract','policy_sha256','usage','last_request_at','pending','stopped'}
        or state['contract']!='quant_data.fmp_account_allowance.v1' or state['policy_sha256']!=gate.policy.identity
        or not isinstance(state['usage'],dict) or len(state['usage'])>3660):
        raise ConflictError('FMP shared allowance state differs')
    for day,used in state['usage'].items():
        try:valid=date.fromisoformat(day).isoformat()==day
        except (TypeError,ValueError):valid=False
        if not valid or type(used) is not int or not 0<=used<=gate.policy.daily_ceiling:raise ConflictError('FMP shared quota history is invalid')
    pause_matches=(observed_pause is not None and state['stopped']==observed_pause
        and type(observed_pause.get('status')) is int
        and (observed_pause['status']==429 or 500<=observed_pause['status']<=599))
    if state['pending'] is not None or (state['stopped'] is not None and not pause_matches):
        raise ConflictError('FMP shared allowance requires prior-attempt reconciliation')
    return state

def acquire_batch(*,root,gate,units,credential,deadline,max_total_bytes,workers=MAX_WORKERS,
                  ticket_factory=HttpTicket,monotonic=time.monotonic,sleeper=time.sleep,clock=lambda:datetime.now(timezone.utc)):
    for unit in units:
        validate_unit(unit)
        if unit.collection!='daily_prices' or unit.provider!='fmp' or unit.mode!='historical_backfill':
            raise ValidationError('Parallel acquisition is limited to historical FMP prices')
    return _acquire_batch(root=root,gate=gate,units=units,credential=credential,deadline=deadline,
        max_total_bytes=max_total_bytes,workers=workers,ticket_factory=ticket_factory,
        monotonic=monotonic,sleeper=sleeper,clock=clock,priority='backfill')

def acquire_company_batch(*,root,gate,units,credential,deadline,max_total_bytes,workers=MAX_WORKERS,
                          ticket_factory=HttpTicket,monotonic=time.monotonic,sleeper=time.sleep,
                          clock=lambda:datetime.now(timezone.utc),best_effort=False):
    """Reuse account pacing and HTTP-only children for selected company inputs."""
    if not isinstance(units,tuple) or not units:
        raise ValidationError('Company acquisition requires explicit units')
    allowed={'earnings_dates','fmp_statements','fmp_analyst_estimates','fmp_distinct_inputs'}
    modes={unit.mode for unit in units}
    for unit in units:
        validate_unit(unit)
        if unit.provider!='fmp' or unit.collection not in allowed:
            raise ValidationError('Company acquisition is outside its four bindings')
    if len(modes)!=1 or not modes<={'historical_backfill','incremental'}:
        raise ValidationError('Company batch requires one history or current mode')
    return _acquire_batch(root=root,gate=gate,units=units,credential=credential,deadline=deadline,
        max_total_bytes=max_total_bytes,workers=workers,ticket_factory=ticket_factory,
        monotonic=monotonic,sleeper=sleeper,clock=clock,
        priority='backfill' if modes=={'historical_backfill'} else 'maintenance',best_effort=best_effort)

def _acquire_batch(*,root,gate,units,credential,deadline,max_total_bytes,workers=MAX_WORKERS,
                  ticket_factory=HttpTicket,monotonic=time.monotonic,sleeper=time.sleep,clock=lambda:datetime.now(timezone.utc),priority='backfill',best_effort=False):
    if not isinstance(units,tuple) or not 1<=len(units)<=MAX_BATCH or len({u.unit_id for u in units})!=len(units):
        raise ValidationError('Parallel price batch must contain unique bounded units')
    if type(best_effort) is not bool:raise ValidationError('Company failure policy is invalid')
    if best_effort and any(u.provider!='fmp' or u.collection not in
            ('earnings_dates','fmp_statements','fmp_analyst_estimates','fmp_distinct_inputs') for u in units):
        raise ValidationError('Best-effort acquisition is limited to selected company inputs')
    if best_effort and ticket_factory is HttpTicket:
        ticket_factory=lambda u,c,d:HttpTicket(u,c,d,diagnostics=True)
    for u in units:
        validate_unit(u)
    if type(workers) is not int or not 1<=workers<=MAX_WORKERS or type(max_total_bytes) is not int or max_total_bytes<1:
        raise ValidationError('Parallel price batch allocation is invalid')
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:raise ValidationError('Parallel receipt root must be explicit and resolved')
    workload_deadline=deadline
    admission_deadline=min(workload_deadline,monotonic()+ADMISSION_SECONDS)
    deadline=min(workload_deadline,admission_deadline+max(u.timeout_seconds for u in units))
    with job_lock(root):
        if (root/'started.json').exists():raise ConflictError('Parallel batch already started; no automatic retry')
        for name in ('attempts','responses','blobs'):private_directory(root/name)
        atomic(root/'manifest.json',{'units':[{'unit_id':u.unit_id,'request_id':u.request_id,'request':u.request_material()} for u in units],
            'workers':workers,'maximum_bytes':max_total_bytes,'deadline_monotonic':workload_deadline,
            'planned_drain_deadline_monotonic':deadline,
            'admission_deadline_monotonic':admission_deadline,'workload_deadline_monotonic':workload_deadline,'retries':0})
        # Contention here has no reservation/GET and may settle within the same
        # finite invocation. No dispatcher is called twice.
        while True:
            try:
                context=job_lock(gate.root);context.__enter__();break
            except ConflictError as e:
                if str(e)!='Equibles backfill is already running' or monotonic()>=deadline:raise
                sleeper(min(0.05,max(0,deadline-monotonic())))
        try:
            state=load_account(gate)
            utcnow=BoundedForwardClock(clock,deadline=deadline,monotonic=monotonic,sleeper=sleeper,last=state['last_request_at'])
            atomic(root/'started.json',{'at':utcnow().isoformat(),'policy_sha256':gate.policy.identity})
            def save():atomic(gate.root/'allowance.json',dumps_strict(state,max_bytes=MAX_LEDGER_BYTES).encode(),replace=True)
            requests=received=unretained=0;next_index=0;pending={};uncertain={};failed={};last_dispatch=None;outcome='complete';peak=0
            try:
                while next_index<len(units) or pending:
                    # Settle completed work before considering another dispatch,
                    # so an observed systemic failure stops new requests.
                    for key,(u,ticket,reservation) in tuple(pending.items()):
                        if not ticket.ready():continue
                        try:
                            try:response=ticket.result()
                            except ProviderRequestFailure as error:
                                if not best_effort or error.category=='credential_exposure' or error.http_status in (401,403):raise
                                ticket.close()
                                temporary_http=(error.http_status==429 or
                                    (error.http_status is not None and error.http_status>=500))
                                failure={**error.diagnostic(),'unit_id':key,'request_id':u.request_id,
                                    'attempt':reservation,'source_response_available':False,
                                    'retryable':error.retryable or temporary_http,
                                    'unretained_byte_reserve':u.max_response_bytes}
                                atomic(root/(key+'.failure.json'),failure)
                                if temporary_http and (state['stopped'] is None or
                                        (type(state['stopped']['status']) is int and state['stopped']['status'] not in (401,403))):
                                    state['stopped']={'attempt':reservation['attempt'],'status':error.http_status,
                                        'observed_at':_utc(clock().isoformat())}
                                    outcome='provider_pause'
                                failed[key]=failure;unretained+=u.max_response_bytes
                                del pending[key]
                                state['pending']={'contract':'quant_data.fmp_parallel_pending.v1','attempts':{**uncertain,**{k:v[2] for k,v in pending.items()}}} if pending or uncertain else None
                                save()
                                continue
                            receipt,body=_retain(root,u,response,(credential,))
                            received+=len(body)
                            if provider_stop(response.status):
                                # A later transient error must not clear an authentication stop.
                                if not (best_effort and state['stopped'] and (type(state['stopped']['status']) is not int or state['stopped']['status'] in (401,403))):
                                    state['stopped']={'attempt':reservation['attempt'],'status':response.status,'observed_at':_utc(clock().isoformat())}
                                    outcome='provider_pause' if best_effort and (response.status==429 or response.status>=500) else 'provider_stop'
                            elif response.status!=200 and not best_effort:outcome='provider_failure'
                        except Exception as e:
                            uncertain[key]=reservation;outcome='uncertain_request'
                            state['stopped']={'attempt':reservation['attempt'],'status':'uncertain','observed_at':_utc(clock().isoformat())}
                            atomic(root/(key+'.failure.json'),{'error_type':type(e).__name__,'unit_id':key})
                        del pending[key]
                        state['pending']={'contract':'quant_data.fmp_parallel_pending.v1','attempts':{**uncertain,**{k:v[2] for k,v in pending.items()}}} if pending or uncertain else None
                        save()
                    if next_index>=len(units) and not pending:break
                    if monotonic()>=workload_deadline:
                        outcome='invocation_budget' if not uncertain else outcome
                        if not pending:break
                    if outcome=='complete' and next_index<len(units):
                        if monotonic()>=admission_deadline:outcome='batch_boundary'
                        elif monotonic()+units[next_index].timeout_seconds+1>=workload_deadline:outcome='invocation_budget'
                    if outcome=='complete' and next_index<len(units) and len(pending)<workers:
                        u=units[next_index]
                        reserved=sum(v[0].max_response_bytes for v in pending.values())
                        if received+unretained+reserved+u.max_response_bytes>max_total_bytes:
                            if not pending:outcome='byte_budget';break
                        else:
                            # A backward wall clock delays new dispatch but must not
                            # block draining already-started HTTP workers.
                            now=clock();at=_utc(now.isoformat());day=at[:10]
                            wall_delay=0 if state['last_request_at'] is None else max(0,gate.policy.min_interval_milliseconds/1000-(now-datetime.fromisoformat(state['last_request_at'].replace('Z','+00:00'))).total_seconds())
                            mono_delay=0 if last_dispatch is None else max(0,gate.policy.min_interval_milliseconds/1000-(monotonic()-last_dispatch))
                            if max(wall_delay,mono_delay)<=0:
                                if at<_utc(gate.policy.effective_at):raise ConflictError('FMP request predates its allowance activation')
                                used=state['usage'].get(day,0)
                                if used>=gate.policy.daily_ceiling-(gate.policy.maintenance_reserve if priority=='backfill' else 0):outcome='daily_budget';continue
                                reservation={'request_identity':hashlib.sha256(dumps_strict(u.request_material()).encode()).hexdigest(),
                                    'priority':priority,'charged_at':at,'attempt':day+'-'+str(used+1),'unit_id':u.unit_id}
                                state['usage'][day]=used+1;state['last_request_at']=at
                                outstanding={**uncertain,**{k:v[2] for k,v in pending.items()},u.unit_id:reservation}
                                state['pending']={'contract':'quant_data.fmp_parallel_pending.v1','attempts':outstanding};save()
                                atomic(gate.root/'attempts'/(reservation['attempt']+'.json'),reservation)
                                atomic(root/'attempts'/(u.unit_id+'.json'),reservation);requests+=1;next_index+=1
                                # Reservation precedes transport, including a
                                # constructor failure or deadline reached here.
                                if monotonic()+u.timeout_seconds>=workload_deadline:
                                    uncertain[u.unit_id]=reservation;outcome='invocation_budget';continue
                                last_dispatch=monotonic()
                                # Admission precedes durable reservation writes.
                                # Those writes cannot consume the admitted request's
                                # own timeout; its hard cap remains the original job.
                                try:ticket=ticket_factory(u,credential,workload_deadline)
                                except Exception:
                                    uncertain[u.unit_id]=reservation;outcome='uncertain_request';continue
                                pending[u.unit_id]=(u,ticket,reservation);peak=max(peak,len(pending))
                                continue
                    if outcome!='complete' and not pending:break
                    if outcome=='complete' and not pending and monotonic()+1>=deadline:outcome='invocation_budget';break
                    sleeper(0.005)
            finally:
                # If the parent fails unexpectedly, outstanding charges remain
                # ambiguous. Killing children never refunds or retries them.
                for u,ticket,reservation in pending.values():ticket.close()
            result={'outcome':outcome,'requests':requests,'received_bytes':received,'retained_responses':len(list((root/'responses').glob('*.json'))),
                'unattempted':len(units)-requests,'uncertain_requests':len(uncertain),'peak_inflight':peak}
            if best_effort:
                result.update(failed_requests=len(failed),unretained_byte_reserve=unretained,
                    pause_guard=state['stopped'] if outcome=='provider_pause' else None)
            atomic(root/'result.json',result);return result
        finally:context.__exit__(None,None,None)
