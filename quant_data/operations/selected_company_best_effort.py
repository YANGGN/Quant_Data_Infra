"""Best-effort selected-company first pass, bounded retry pass, and durable gaps.

The existing publishers, physical locks, account charges and immutable source
receipts remain authoritative. Only selected company collection opts into this
failure policy; no shared allowance reset or automatic repeat of successes.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import hashlib,math,time
from ..errors import ConflictError,ValidationError
from ..json_codec import dumps_strict
from .collection_queue import atomic,_read,_response,BoundedForwardClock
from .collection_parallel_prices import acquire_company_batch,load_account
from .collection_fmp import SelectedFmpPublisher,read_when_company_quiet
from .equibles_transcript_backfill import job_lock,private_directory
from .selected_company_collection import (unit_from_dict,validate_manifest,_continuation,
    process_response,scope_key,publication_order,validate_selection)
MAX_RETRIES_PER_REQUEST=2
MAX_RETRY_ATTEMPTS=500
SUCCESS=('succeeded','unchanged','reused')

def manifest_digest(manifest):
    return hashlib.sha256(dumps_strict(manifest,max_bytes=64*1024**2).encode()).hexdigest()

def initial_state(manifest,units):
    return {'contract':'quant_data.company_best_effort_state.v1','manifest_sha256':manifest_digest(manifest),
        'pending_first':[u.unit_id for u in units],'pending_retry':[],
        'units':{u.unit_id:asdict(u) for u in units},'completed':{},'walks':{},
        'requests':0,'received_bytes':0,'unretained_byte_reserve':0,
        'retry_counts':{},'retry_attempts':0,'batches':0,'cached':{},
        'external_successes':0,'retained_scopes':len(manifest['retained_scopes'])}

def validate_state(manifest,initial,state,request_cap,byte_cap):
    if state.get('contract')!='quant_data.company_best_effort_state.v1' or state['manifest_sha256']!=manifest_digest(manifest):
        raise ConflictError('Best-effort checkpoint manifest differs')
    units={k:unit_from_dict(v) for k,v in state['units'].items()}
    if any(k!=u.unit_id for k,u in units.items()):raise ConflictError('Checkpoint unit identity differs')
    first=state['pending_first'];retry=state['pending_retry'];completed=state['completed']
    if (len(first)!=len(set(first)) or len(retry)!=len(set(retry)) or set(first)&set(completed)
            or set(first)&set(retry) or not set(retry)<=set(completed)
            or set(units)!=set(first)|set(completed) or not set(state['cached'])<=set(first)):
        raise ConflictError('Best-effort checkpoint lost or repeated units')
    if any(not completed[k].get('retryable') or completed[k]['outcome'] in SUCCESS for k in retry):
        raise ConflictError('Only failed temporary requests can be retried')
    for name,cap in (('requests',request_cap),('received_bytes',byte_cap),
                     ('unretained_byte_reserve',byte_cap),('retry_attempts',MAX_RETRY_ATTEMPTS)):
        if type(state[name]) is not int or not 0<=state[name]<=cap:
            raise ValidationError('Best-effort checkpoint exceeds its limits')
    if state['received_bytes']+state['unretained_byte_reserve']>byte_cap:
        raise ValidationError('Unretained responses exceed the byte allocation')
    if (any(k not in units or type(v) is not int or not 1<=v<=MAX_RETRIES_PER_REQUEST for k,v in state['retry_counts'].items())
            or sum(state['retry_counts'].values())!=state['retry_attempts']):
        raise ConflictError('Retry charges do not reconcile')
    for name in ('external_successes','retained_scopes','batches'):
        if type(state[name]) is not int or state[name]<0:
            raise ConflictError('Checkpoint counters are invalid')
    if state['requests']!=len(completed)+len(state['cached'])+state['external_successes']+state['retry_attempts']:
        raise ConflictError('Checkpoint request charges do not reconcile')
    # Reuse established request/page/membership validation with logical outcomes.
    logical={'manifest_sha256':state['manifest_sha256'],'pending':[state['units'][k] for k in first],
        'completed_units':[state['units'][k] for k in completed],'completed':completed,
        'walks':state['walks'],'requests':len(completed),
        'received_bytes':state['received_bytes']}
    # Pilot failures are legitimate best-effort gaps; their request identities
    # must still be validated by the unchanged continuation validator.
    validation=deepcopy(logical)
    pilots={u.unit_id for u in initial[:manifest['pilot_units']]}
    for key in pilots & set(validation['completed']):
        if validation['completed'][key]['outcome'] not in SUCCESS:
            validation['completed'][key]={**validation['completed'][key],'outcome':'succeeded'}
    _continuation(manifest,initial,validation)
    return units

def retry_delay(receipts,clock):
    seconds=30.0
    for receipt in receipts:
        if receipt['status']==429:seconds=max(seconds,60.0)
        raw=receipt.get('headers',{}).get('retry-after')
        if raw:
            try:value=float(int(raw))
            except (ValueError,TypeError):
                try:value=(parsedate_to_datetime(raw)-clock()).total_seconds()
                except (ValueError,TypeError,OverflowError):continue
            seconds=max(seconds,value)
    return max(0.0,seconds)

def wait_bounded(seconds,deadline,monotonic,sleeper):
    end=min(deadline,monotonic()+seconds)
    while monotonic()<end:sleeper(min(30,end-monotonic()))
    return monotonic()<deadline

def release_pause(gate,guard,root,clock):
    """Clear only our fully drained, observed HTTP pause after its delay."""
    with job_lock(gate.root):
        state=load_account(gate,observed_pause=guard)
        if state['pending'] is not None or state['stopped']!=guard or not (type(guard['status']) is int and (guard['status']==429 or 500<=guard['status']<=599)):
            raise ConflictError('Provider pause no longer belongs to this invocation')
        private_directory(gate.root/'reconciliations')
        atomic(gate.root/'reconciliations'/(guard['attempt']+'-company-pause.json'),
            {'at':clock().isoformat(),'outcome':'known_http_pause_elapsed','guard':guard,
             'usage_preserved':state['usage'],'last_request_at_preserved':state['last_request_at'],
             'provider_requests':0,'owner':str(root)})
        state['stopped']=None;atomic(gate.root/'allowance.json',state,replace=True)

def run_best_effort(*,root,stores,registry,manifest,gate,credential,evidence_root,
        hard_deadline,request_cap,byte_cap,seed=None,require_active=True,
        acquire=acquire_company_batch,clock=lambda:datetime.now(timezone.utc),
        monotonic=time.monotonic,sleeper=time.sleep):
    if (type(request_cap) is not int or not 1<=request_cap<=80000
            or type(byte_cap) is not int or not 1<=byte_cap<=8*1024**3):
        raise ValidationError('Best-effort allocation is invalid')
    if type(hard_deadline) not in (int,float) or not math.isfinite(hard_deadline):
        raise ValidationError('Best-effort deadline is invalid')
    hard_deadline=min(hard_deadline,monotonic()+manifest['maximum_seconds'])
    read=lambda fn:read_when_company_quiet(fn,stores=stores,deadline=hard_deadline,
        monotonic=monotonic,sleeper=sleeper,retry_sidecar_changes=True)
    selections,initial=read(lambda:validate_manifest(manifest,stores,require_active=require_active))
    state=deepcopy(seed) if seed is not None else initial_state(manifest,initial)
    units=validate_state(manifest,initial,state,request_cap,byte_cap)
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:raise ValidationError('Best-effort root must be resolved')
    private_directory(root);private_directory(root/'outcomes');private_directory(root/'batches')
    by_binding={s.binding.id:s for s in selections}
    reason=None;transient_streak=0
    utcnow=BoundedForwardClock(clock,deadline=hard_deadline,monotonic=monotonic,sleeper=sleeper)
    def progress(status='running',**extra):
        counts=Counter(r['outcome'] for r in state['completed'].values())
        value={'contract':'quant_data.selected_company_progress.v2','at':utcnow().isoformat(),
            'status':status,'requests':state['requests'],'processed_units':len(state['completed'])+state['external_successes'],
            'pending_units':len(state['pending_first'])+len(state['pending_retry']),
            'first_pass_pending':len(state['pending_first']),'retry_pending':len(state['pending_retry']),
            'retry_attempts':state['retry_attempts'],'received_bytes':state['received_bytes'],
            'unretained_byte_reserve':state['unretained_byte_reserve'],'outcomes':dict(counts),
            'batches':state['batches'],**extra}
        atomic(root/'checkpoint.json',state,replace=True);atomic(root/'progress.json',value,replace=True)
    def accept(unit,result,receipt,source_root,ordinal,walk=None,child=None):
        key=unit.unit_id
        if key in state['pending_first']:state['pending_first'].remove(key)
        if key in state['pending_retry']:state['pending_retry'].remove(key)
        state['cached'].pop(key,None);state['completed'][key]=result
        if walk is not None:state['walks']['|'.join(scope_key(unit))]=walk
        if child is not None and child.unit_id not in units:
            units[child.unit_id]=child;state['units'][child.unit_id]=asdict(child)
            state['pending_first'].append(child.unit_id)
        if result.get('retryable') and result['outcome'] not in SUCCESS and state['retry_counts'].get(key,0)<MAX_RETRIES_PER_REQUEST:
            state['pending_retry'].append(key)
        record={'unit':asdict(unit),'result':result,'source_batch':str(source_root),
            'response_sha256':None if receipt is None else receipt['content_sha256'],
            'captured_at':None if receipt is None else receipt['captured_at'],
            'attempt_ordinal':ordinal}
        atomic(root/'outcomes'/(key+'-'+str(ordinal)+'.json'),record)
    with job_lock(root):
        if (root/'started.json').exists():raise ConflictError('Best-effort run already started; use its checkpoint')
        atomic(root/'manifest.json',manifest);atomic(root/'initial-state.json',state)
        atomic(root/'started.json',{'at':utcnow().isoformat(),'deadline_monotonic':hard_deadline,
            'request_cap':request_cap,'byte_cap':byte_cap,'retry_cap':MAX_RETRY_ATTEMPTS,
            'retries_per_request':MAX_RETRIES_PER_REQUEST})
        progress()
        try:
            while state['pending_first'] or state['pending_retry']:
                if monotonic()+31>=hard_deadline:reason='invocation_deadline';break
                if not state['pending_first'] and state['retry_attempts']>=MAX_RETRY_ATTEMPTS:
                    state['pending_retry']=[];break
                retrying=not state['pending_first']
                queue=state['pending_retry'] if retrying else state['pending_first']
                available=request_cap-state['requests']
                byte_available=byte_cap-state['received_bytes']-state['unretained_byte_reserve']
                cached=not retrying and queue[0] in state['cached']
                if not cached and (available<=0 or byte_available<units[queue[0]].max_response_bytes):
                    reason='aggregate_budget';break
                limit=min(64,available,MAX_RETRY_ATTEMPTS-state['retry_attempts'] if retrying else 64)
                if cached:
                    source_root=Path(state['cached'][queue[0]])
                    chosen=[k for k in queue[:64] if state['cached'].get(k)==str(source_root)]
                    report={'requests':0,'received_bytes':0,'outcome':'complete','uncertain_requests':0}
                else:
                    chosen=[k for k in queue[:limit] if k not in state['cached']]
                    source_root=root/'batches'/str(state['batches']).zfill(5)
                    batch=tuple(units[k] for k in chosen)
                    def check_selections():
                        for selection in selections:
                            validate_selection(stores,selection,cutoff=utcnow().isoformat(),
                                collection=selection.binding.id,require_active=require_active)
                    read(check_selections)
                    report=acquire(root=source_root,gate=gate,units=batch,credential=credential,
                        deadline=hard_deadline,max_total_bytes=min(byte_available,1024**3),
                        best_effort=True,monotonic=monotonic,sleeper=sleeper,clock=utcnow)
                    state['requests']+=report['requests'];state['received_bytes']+=report['received_bytes']
                    state['unretained_byte_reserve']+=report.get('unretained_byte_reserve',0)
                    attempts={p.stem for p in (source_root/'attempts').glob('*.json')}
                    if len(attempts)!=report['requests']:raise ConflictError('Best-effort attempt accounting differs')
                    if retrying:
                        for key in attempts:state['retry_counts'][key]=state['retry_counts'].get(key,0)+1
                        state['retry_attempts']+=len(attempts)
                    state['batches']+=1
                batch=tuple(units[k] for k in chosen)
                publishers={}
                for binding in {u.collection for u in batch}:
                    publishers[binding]=SelectedFmpPublisher(stores=stores,registry=registry,
                        selection=by_binding[binding],units=tuple(u for u in batch if u.collection==binding),
                        cutoff=utcnow().isoformat(),deadline=hard_deadline,monotonic=monotonic,sleeper=sleeper,
                        retry_sidecar_changes=True)
                receipts=[];succeeded=0;transport_failures=0
                for unit in publication_order(source_root,batch):
                    receipt,body=_response(source_root,unit.unit_id,unit);receipts.append(receipt)
                    publisher=publishers[unit.collection];key='|'.join(scope_key(unit))
                    result,child,walk=process_response(unit=unit,receipt=receipt,body=body,
                        subject=publisher.subjects[unit.subject],
                        publisher=lambda **kw:publisher(**kw,deadline=hard_deadline,monotonic=monotonic,sleeper=sleeper),
                        evidence_root=evidence_root,history=manifest['historical'],walk=state['walks'].get(key,{}))
                    if result['outcome']=='provider_unavailable':
                        result['retryable']=receipt['status'] in (408,425,429) or receipt['status']>=500
                    accept(unit,result,receipt,source_root,state['retry_counts'].get(unit.unit_id,0),walk,child)
                    succeeded+=result['outcome'] in SUCCESS
                for unit in batch:
                    failure=_read(source_root/(unit.unit_id+'.failure.json'))
                    if failure and failure.get('contract')=='quant_data.provider_failure.v1':
                        if failure['http_status'] is not None:
                            receipts.append({'status':failure['http_status'],'headers':{}})
                        result={'outcome':'transport_failed','category':failure['category'],
                            'http_status':failure['http_status'],'retryable':failure['retryable'],
                            'source_response_available':False}
                        accept(unit,result,None,source_root,state['retry_counts'].get(unit.unit_id,0))
                        transport_failures+=1
                transient_streak=0 if succeeded else transient_streak+transport_failures
                progress()
                if report['uncertain_requests']:
                    reason='accounting_or_storage_failure';break
                if report['outcome']=='provider_stop':
                    reason='provider_authorization_or_safety_stop';break
                if report['outcome']=='provider_pause':
                    delay=retry_delay([*receipts,{'status':report['pause_guard']['status'],'headers':{}}],utcnow)
                    progress('paused_provider',pause_seconds=delay)
                    if not wait_bounded(delay,hard_deadline,monotonic,sleeper):reason='invocation_deadline';break
                    release_pause(gate,report['pause_guard'],root,utcnow)
                elif report['outcome']=='daily_budget':
                    now=utcnow();tomorrow=datetime.combine(now.date()+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
                    delay=(tomorrow-now).total_seconds()+1;progress('paused_daily_allowance',pause_seconds=delay)
                    if not wait_bounded(delay,hard_deadline,monotonic,sleeper):reason='invocation_deadline';break
                elif transient_streak>=3:
                    progress('paused_temporary_outage',pause_seconds=60)
                    if not wait_bounded(60,hard_deadline,monotonic,sleeper):reason='invocation_deadline';break
                    transient_streak=0
                elif not cached and report['requests']==0:
                    reason=report['outcome'];break
            progress('stopped' if reason else 'finished')
            counts=Counter(r['outcome'] for r in state['completed'].values())
            successful=sum(counts[k] for k in SUCCESS)+state['external_successes']
            failed=sum(v for k,v in counts.items() if k not in SUCCESS)
            result={'contract':'quant_data.selected_company_result.v2','at':utcnow().isoformat(),
                'outcome':'stopped' if reason else 'processed_with_gaps' if failed or any(manifest['identity_gaps'].values()) else 'complete',
                'stop_reason':reason,'requests':state['requests'],'processed_units':len(state['completed'])+state['external_successes'],
                'pending_units':len(state['pending_first'])+len(state['pending_retry']),
                'received_bytes':state['received_bytes'],'unretained_byte_reserve':state['unretained_byte_reserve'],
                'retry_attempts':state['retry_attempts'],'source_failures':failed,'identity_gaps':manifest['identity_gaps'],
                'counts':{'unit':'steps','successful':successful,'failed':failed,
                    'partial':1 if reason=='accounting_or_storage_failure' else 0,
                    'skipped':state['retained_scopes'],'unattempted':len(state['pending_first'])},
                'whole_history_complete':False,'exit_code':75 if reason else 0}
            atomic(root/'result.json',result)
            atomic(root/'gaps.json',{'at':result['at'],'identity_gaps':manifest['identity_gaps'],
                'failed_units':[{ 'unit':state['units'][k], 'result':v } for k,v in state['completed'].items() if v['outcome'] not in SUCCESS],
                'unattempted_units':[state['units'][k] for k in state['pending_first']]})
            return result
        except Exception as error:
            progress('failed')
            atomic(root/'failure.json',{'at':utcnow().isoformat(),'error_type':type(error).__name__,
                'pending_units':len(state['pending_first'])+len(state['pending_retry']),
                'requests':state['requests'],'retry_attempts':state['retry_attempts']})
            raise
