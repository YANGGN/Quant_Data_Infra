"""Bounded company-universe plans and one serial canonical publisher.

The existing FMP transport owns request reservation, pacing, and raw retention.
This controller owns explicit endpoint selection, no-repeat inventory, estimate
pagination and finite completion. No issuer, schema, or source value is invented.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import hashlib, time, math
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..json_codec import loads_strict, dumps_strict
from ..market.collection_universe import _utc
from ..market.collection_bindings import (pin_binding, PinnedCollection, CollectionBinding,
    BoundSubject, RetainedInstrument)
from ..operations.collection_targets import selected_fmp_subjects, validate_selection
from ..stores import quiet_immutable_read_connection
from ..company.fmp_research import parse_research_response
from ..company.fmp_analyst_history import parse_analyst_response
from .collection_fmp import fmp_units, SelectedFmpPublisher, STATEMENTS, DISTINCT_REVIEW, estimate_continuation, read_when_company_quiet
from .collection_plan import AcquisitionUnit, validate_unit, digest
from .collection_queue import atomic, _read, _response, BoundedForwardClock
from .collection_parallel_prices import acquire_company_batch
from .equibles_transcript_backfill import private_directory, job_lock

BINDINGS = ('earnings_dates','fmp_statements','fmp_analyst_estimates','fmp_distinct_inputs')
DISTINCT = tuple(sorted(row[0] for row in DISTINCT_REVIEW))
MAX_REQUESTS = 80000
MAX_BYTES = 8 * 1024**3
MAX_SECONDS = 72 * 3600
MAX_CURRENT_REQUESTS = 50000
MAX_CURRENT_SECONDS = 12 * 3600
MAX_CURRENT_BYTES = 4 * 1024**3


def unit_from_dict(value):
    return validate_unit(AcquisitionUnit(**{**value,
        'parameters':tuple(tuple(x) for x in value['parameters']),
        'selected_symbols':tuple(value['selected_symbols'])}))


def selection_from_dict(value):
    binding = CollectionBinding(**{**value['binding'],
        'retain_universes':tuple(value['binding']['retain_universes']),
        'required_identity_fields':tuple(value['binding']['required_identity_fields'])})
    return PinnedCollection(binding,value['membership_snapshot_id'],value['mapping_id'],
        tuple(BoundSubject(**v) for v in value['subjects']),
        tuple(RetainedInstrument(**v) for v in value['retained']),value['scope_sha256'])


def scope_key(unit):
    return (unit.subject,unit.endpoint,dict(unit.parameters).get('period','all'))


def retained_keys(stores):
    """Presence means retained population, not complete source history."""
    result={}
    with quiet_immutable_read_connection(stores,'company') as c:
        deadline=time.monotonic()+60
        c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        for table,identity in (('company_fmp_research_snapshots','snapshot_id'),
                               ('company_fmp_analyst_captures','capture_id')):
            for row in c.execute('SELECT symbol,endpoint,request_scope_json,'+identity+' FROM '+table):
                scope=loads_strict(row[2],max_bytes=1024*1024)
                period=scope.get('parameters',{}).get('period','all')
                result.setdefault((row[0],row[1],period),row[3])
    return result


def collection_work(stores,selection,*,cutoff,historical):
    work=fmp_units(stores,selection,mode='historical_backfill' if historical else 'incremental',
        observation_window=cutoff,cutoff=cutoff,
        distinct_endpoints=DISTINCT if selection.binding.id=='fmp_distinct_inputs' else ())
    if historical:return work
    # Revisit five fiscal years of statements for new reports and restatements.
    # Full available statement history belongs to the one-time backfill.
    units=[]
    for unit in work.units:
        if unit.endpoint in STATEMENTS:
            parameters=dict(unit.parameters)
            parameters['limit']='5' if parameters['period']=='annual' else '20'
            unit=replace(unit,parameters=tuple(sorted(parameters.items())))
        units.append(unit)
    return replace(work,units=tuple(units))


def prepare(stores,bindings,*,cutoff,historical,extra_completed=()):
    existing=retained_keys(stores) if historical else {}
    for key,reference in extra_completed:
        existing.setdefault(tuple(key),reference)
    selections=[];units=[];skipped=[];gaps={}
    mode='historical_backfill' if historical else 'incremental'
    for key in BINDINGS:
        selection=pin_binding(stores,bindings[key],cutoff=cutoff)
        work=collection_work(stores,selection,cutoff=cutoff,historical=historical)
        selections.append(selection)
        ready={u.subject for u in work.units}
        gaps[key]=[{'symbol':s.source_symbol,'reason':s.reason if s.status!='resolved' else 'canonical_issuer_missing'}
                   for s in selection.subjects if s.status!='resolved' or s.provider_symbol not in ready]
        for unit in work.units:
            old=existing.get(scope_key(unit))
            if old:
                skipped.append({'key':scope_key(unit),'evidence':old})
            else:units.append(unit)
    # Check each endpoint/period on one missing symbol before its broad population.
    ordered=sorted(units,key=lambda u:(u.endpoint,dict(u.parameters).get('period',''),u.subject))
    first={}
    for u in ordered:first.setdefault((u.endpoint,dict(u.parameters).get('period','')),u)
    pilot=list(first.values());pilot_ids={u.unit_id for u in pilot}
    ordered=pilot+[u for u in ordered if u.unit_id not in pilot_ids]
    potential=len(ordered)+sum(9 for u in ordered if historical and u.endpoint=='analyst-estimates')
    if potential>MAX_REQUESTS or len(ordered)>MAX_CURRENT_REQUESTS:
        raise ResourceLimitError('Selected company workload exceeds its finite ceiling')
    return {'contract':'quant_data.selected_company_plan.v1','created_at':cutoff,
        'historical':historical,'selections':[asdict(s) for s in selections],
        'units':[asdict(u) for u in ordered],'retained_scopes':skipped,'identity_gaps':gaps,
        'pilot_units':len(pilot),'maximum_requests':potential,
        'maximum_bytes':MAX_BYTES if historical else MAX_CURRENT_BYTES,
        'maximum_seconds':MAX_SECONDS if historical else MAX_CURRENT_SECONDS,
        'endpoint_requests':dict(Counter(u.endpoint for u in ordered)),
        'retries':0,'estimate_pages_per_period':10 if historical else 1}


def validate_manifest(manifest,stores,*,require_active):
    if (manifest.get('contract')!='quant_data.selected_company_plan.v1'
        or type(manifest.get('historical')) is not bool or manifest.get('retries')!=0):
        raise ValidationError('Selected company manifest is invalid')
    historical=manifest['historical']
    ceilings=(MAX_REQUESTS,MAX_BYTES,MAX_SECONDS) if historical else (MAX_CURRENT_REQUESTS,MAX_CURRENT_BYTES,MAX_CURRENT_SECONDS)
    for key,ceiling in zip(('maximum_requests','maximum_bytes','maximum_seconds'),ceilings):
        v=manifest.get(key)
        if type(v) is not int or not 0<=v<=ceiling:
            raise ResourceLimitError('Selected company allocation exceeds its bound')
    selections=tuple(selection_from_dict(v) for v in manifest['selections'])
    if tuple(s.binding.id for s in selections)!=BINDINGS:
        raise ValidationError('Selected company manifest requires its four exact bindings')
    expected={}
    for selection in selections:
        validate_selection(stores,selection,cutoff=manifest['created_at'],collection=selection.binding.id,
            require_active=require_active)
        work=collection_work(stores,selection,cutoff=manifest['created_at'],historical=historical)
        expected.update({u.unit_id:u for u in work.units})
    units=tuple(unit_from_dict(v) for v in manifest['units'])
    if len({u.unit_id for u in units})!=len(units) or any(expected.get(u.unit_id)!=u for u in units):
        raise ConflictError('Company request differs from the pinned selection')
    pilot_keys={(u.endpoint,dict(u.parameters).get('period','')) for u in units}
    pilot_count=manifest.get('pilot_units')
    if (type(pilot_count) is not int or pilot_count!=len(pilot_keys)
        or {(u.endpoint,dict(u.parameters).get('period','')) for u in units[:pilot_count]}!=pilot_keys):
        raise ConflictError('Company pilot differs from its endpoint and period scope')
    potential=len(units)+sum(9 for u in units if historical and u.endpoint=='analyst-estimates')
    if manifest['maximum_requests']!=potential:
        raise ConflictError('Company request ceiling differs from the finite manifest')
    return selections,units


def parse_unit(unit,subject,receipt,body):
    reference='collection/fmp/blobs/'+receipt['content_sha256']+'.json'
    kw=dict(endpoint=unit.endpoint,parameters=dict(unit.parameters),subject=subject,
        captured_at=receipt['captured_at'],source_reference=reference)
    if unit.endpoint in STATEMENTS or unit.endpoint=='revenue-product-segmentation':
        return parse_research_response(body,**kw,history_profile=unit.endpoint in STATEMENTS)
    return parse_analyst_response(body,**kw)


def process_response(*,unit,receipt,body,subject,publisher,evidence_root,history,walk):
    """Reject invalid source bytes before invoking a canonical publisher."""
    if (hashlib.sha256(body).hexdigest()!=receipt['content_sha256']
        or receipt['unit_id']!=unit.unit_id or receipt['request_id']!=unit.request_id):
        raise ConflictError('Company original response identity differs')
    if receipt['status']!=200:
        return {'outcome':'provider_unavailable','http_status':receipt['status']},None,walk
    try:
        parsed=parse_unit(unit,subject,receipt,body)
    except (ValidationError,ResourceLimitError) as error:
        return {'outcome':'source_rejected','error_type':type(error).__name__,'reason':str(error)},None,walk
    child=None;updated=walk
    pagination=None
    if history and unit.endpoint=='analyst-estimates':
        if walk.get('captured_at') and receipt['captured_at']<walk['captured_at']:
            raise ConflictError('Estimate page capture time moved backwards')
        try:
            child,pagination=estimate_continuation(unit,parsed,
                seen_page_hashes=tuple(walk.get('hashes',())),seen_target_dates=tuple(walk.get('dates',())))
        except (ConflictError,ValidationError,ResourceLimitError) as error:
            return {'outcome':'source_rejected','error_type':type(error).__name__,'reason':str(error)},None,walk
        updated={'hashes':[*walk.get('hashes',()),parsed.content_sha256],
            'dates':sorted(set(walk.get('dates',()))|{r.target_period_end for r in parsed.rows}),
            'captured_at':receipt['captured_at']}
    private_directory(evidence_root)
    atomic(evidence_root/(receipt['content_sha256']+'.json'),body)
    try:
        result=publisher(unit=unit,retained=None,receipt=receipt,body=body)
    except ConflictError as error:
        # A newer issuer observation can already exist for another share class.
        # The publisher rolls back its rows and records the failed run; retain
        # the original source as an explicit rejection without changing its time.
        if str(error)!='FMP research revisions require increasing capture time':
            raise
        return {'outcome':'source_rejected','error_type':type(error).__name__,
            'reason':str(error),'rejection_stage':'publication'},None,walk
    return {**result,'pagination':pagination,'historical_vintages_reconstructed':False},child,updated


def publication_order(root, units):
    """Order receipt metadata only; load and validate one response body at a time."""
    captured=[]
    for unit in units:
        receipt=_read(root/'responses'/(unit.unit_id+'.json'))
        if receipt is not None:
            captured.append((_utc(receipt['captured_at']),unit.unit_id,unit))
    return tuple(item[2] for item in sorted(captured))



def _continuation(manifest,initial,state):
    """Validate an explicitly reconciled, fully settled private checkpoint."""
    if set(state)!={'manifest_sha256','pending','completed_units','completed','walks','requests','received_bytes'}:
        raise ConflictError('Company continuation fields differ')
    if state['manifest_sha256']!=hashlib.sha256(dumps_strict(manifest,max_bytes=64*1024**2).encode()).hexdigest():
        raise ConflictError('Company continuation manifest differs')
    pending=[unit_from_dict(v) for v in state['pending']]
    settled=[unit_from_dict(v) for v in state['completed_units']]
    expected={u.unit_id:u for u in initial};completed=state['completed']
    if (len({u.unit_id for u in pending+settled})!=len(pending)+len(settled)
        or {u.unit_id for u in settled}!=set(completed)
        or not set(expected)<={u.unit_id for u in pending+settled}):
        raise ConflictError('Company continuation lost or repeated a unit')
    for unit in pending+settled:
        parameters=dict(unit.parameters);page=parameters.get('page','0')
        if not page.isdigit() or not 0<=int(page)<=9:
            raise ConflictError('Company continuation page is outside its bound')
        if int(page):
            if not manifest['historical'] or unit.endpoint!='analyst-estimates':
                raise ConflictError('Company continuation page is outside estimates')
            parameters['page']='0'
            base=replace(unit,parameters=tuple(sorted(parameters.items())))
            for parent_page in range(int(page)):
                parent_parameters={**parameters,'page':str(parent_page)}
                parent=replace(unit,parameters=tuple(sorted(parent_parameters.items())))
                if completed.get(parent.unit_id,{}).get('pagination')!='continuation_required':
                    raise ConflictError('Company continuation has no completed page predecessor')
        else:base=unit
        if expected.get(base.unit_id)!=base:
            raise ConflictError('Company continuation changed a pinned request')
    for key,ceiling in (('requests',manifest['maximum_requests']),('received_bytes',manifest['maximum_bytes'])):
        if type(state[key]) is not int or not 0<=state[key]<=ceiling:
            raise ConflictError('Company continuation accounting exceeds its bound')
    if state['requests']!=len(settled):
        raise ConflictError('Company continuation has unresolved attempts')
    pilots={u.unit_id for u in initial[:manifest['pilot_units']]}
    if any(completed[k].get('outcome') not in ('succeeded','unchanged','reused') for k in pilots & set(completed)):
        raise ConflictError('Company continuation has an unreconciled pilot')
    if not isinstance(state['walks'],dict):raise ConflictError('Company continuation estimate walks differ')
    for unit in pending:
        page=int(dict(unit.parameters).get('page','0'))
        if page and len(state['walks'].get('|'.join(scope_key(unit)),{}).get('hashes',()))!=page:
            raise ConflictError('Company continuation lost its estimate walk')
    return pending,dict(completed),state['walks'],state['requests'],state['received_bytes']


def run(*,root,stores,registry,manifest,gate,credential,evidence_root,
        require_active=True,acquire=acquire_company_batch,hard_deadline=None,continuation_state=None,clock=lambda:datetime.now(timezone.utc),
        monotonic=time.monotonic,sleeper=time.sleep):
    read_deadline=monotonic()+manifest['maximum_seconds']
    if hard_deadline is not None:
        if type(hard_deadline) not in (int,float) or not math.isfinite(hard_deadline):
            raise ValidationError('Company continuation deadline is invalid')
        read_deadline=min(read_deadline,hard_deadline)
    selections,initial=read_when_company_quiet(lambda:validate_manifest(manifest,stores,require_active=require_active),
        stores=stores,deadline=read_deadline,monotonic=monotonic,sleeper=sleeper)
    resumed=_continuation(manifest,initial,continuation_state) if continuation_state is not None else None
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:raise ValidationError('Company job root must be resolved')
    private_directory(root)
    with job_lock(root):
        if (root/'started.json').exists():raise ConflictError('Company invocation already started; explicit reconciliation required')
        atomic(root/'manifest.json',manifest)
        started=monotonic();deadline=min(started+manifest['maximum_seconds'],read_deadline)
        if hard_deadline is not None:
            if type(hard_deadline) not in (int,float) or not math.isfinite(hard_deadline):
                raise ValidationError('Company continuation deadline is invalid')
            deadline=min(deadline,hard_deadline)
        utcnow=BoundedForwardClock(clock,deadline=deadline,monotonic=monotonic,sleeper=sleeper)
        atomic(root/'started.json',{'at':utcnow().isoformat(),'manifest_sha256':hashlib.sha256(dumps_strict(manifest,max_bytes=64*1024**2).encode()).hexdigest(),
            'deadline_monotonic':deadline,'retries':0})
        pending=list(initial);completed={};walks={};batches=0;requests=received=0;reason=None
        if resumed is not None:
            pending,completed,walks,requests,received=resumed
            atomic(root/'continuation-input.json',continuation_state)
        attempted_ids=set(completed);pilot_ids={u.unit_id for u in initial[:manifest['pilot_units']]}
        private_directory(root/'outcomes');private_directory(root/'batches')
        by_binding={s.binding.id:s for s in selections}
        while pending:
            if monotonic()+31>=deadline:reason='invocation_deadline';break
            remaining=manifest['maximum_requests']-requests
            byte_remaining=manifest['maximum_bytes']-received
            if remaining<1 or byte_remaining<max(u.max_response_bytes for u in pending[:1]):
                reason='aggregate_budget';break
            # Admission may split the pilot across batches. Every pilot must
            # settle successfully before any broad or continuation request.
            pilot_pending=pilot_ids-set(completed)
            candidates=[u for u in pending if u.unit_id in pilot_pending] if pilot_pending else pending
            batch=tuple(candidates[:min(64,remaining)])
            def check_selections():
                for selection in selections:
                    validate_selection(stores,selection,cutoff=utcnow().isoformat(),
                        collection=selection.binding.id,require_active=require_active)
            read_when_company_quiet(check_selections,stores=stores,deadline=deadline,
                monotonic=monotonic,sleeper=sleeper)
            path=root/'batches'/str(batches).zfill(5)
            report=acquire(root=path,gate=gate,units=batch,credential=credential,deadline=deadline,
                max_total_bytes=min(byte_remaining,1024**3),monotonic=monotonic,sleeper=sleeper,clock=utcnow)
            requests+=report['requests'];received+=report['received_bytes'];batches+=1
            attempted={p.stem for p in (path/'attempts').glob('*.json')}
            attempted_ids.update(attempted)
            if len(attempted)!=report['requests']:
                raise ConflictError('Company attempt accounting differs')
            publishers={};subjects={}
            for key in {u.collection for u in batch}:
                selection=by_binding[key]
                own=tuple(u for u in batch if u.collection==key)
                publishers[key]=SelectedFmpPublisher(stores=stores,registry=registry,selection=selection,
                    units=own,cutoff=utcnow().isoformat(),deadline=deadline,monotonic=monotonic,sleeper=sleeper)
                subjects[key]=publishers[key].subjects
            children=[]
            for unit in publication_order(path,batch):
                cached=_response(path,unit.unit_id,unit)
                if cached is None:continue
                receipt,body=cached;key='|'.join(scope_key(unit))
                def publish(**kw):
                    return publishers[unit.collection](**kw,deadline=deadline,monotonic=monotonic,sleeper=sleeper)
                result,child,walk=process_response(unit=unit,receipt=receipt,body=body,
                    subject=subjects[unit.collection][unit.subject],publisher=publish,evidence_root=evidence_root,
                    history=manifest['historical'],walk=walks.get(key,{}))
                atomic(root/'outcomes'/(unit.unit_id+'.json'),{'unit':asdict(unit),'response_sha256':receipt['content_sha256'],
                    'captured_at':receipt['captured_at'],'result':result})
                completed[unit.unit_id]=result;walks[key]=walk
                if child is not None:children.append(child)
            pending=[u for u in pending if u.unit_id not in completed]+children
            snapshot={'contract':'quant_data.selected_company_progress.v1','at':utcnow().isoformat(),
                'requests':requests,'received_bytes':received,'processed_units':len(completed),'pending_units':len(pending),
                'outcomes':dict(Counter(r['outcome'] for r in completed.values())),'batches':batches}
            atomic(root/'progress.json',snapshot,replace=True)
            atomic(root/'checkpoint.json',{'pending':[asdict(u) for u in pending],
                'walks':walks,'completed':list(completed),'requests':requests,'received_bytes':received},replace=True)
            if any(completed[key]['outcome'] in ('source_rejected','provider_unavailable')
                   for key in pilot_ids & set(completed)):
                reason='endpoint_preflight_failed';break
            if report['uncertain_requests'] or attempted-set(completed):
                reason='uncertain_requests';break
            if report['outcome'] not in ('complete','batch_boundary','provider_failure'):
                reason=report['outcome'];break
            if report['requests']==0:reason='no_acquisition_progress';break
        failures=sum(r['outcome'] in ('source_rejected','provider_unavailable') for r in completed.values())
        available=sum(r['outcome'] in ('succeeded','unchanged','reused') for r in completed.values())
        result={'contract':'quant_data.selected_company_result.v1','at':utcnow().isoformat(),
            'outcome':'stopped' if reason else 'processed_with_gaps' if failures or any(manifest['identity_gaps'].values()) else 'complete',
            'stop_reason':reason,'requests':requests,'received_bytes':received,'processed_units':len(completed),
            'pending_units':len(pending),'retained_scopes':len(manifest['retained_scopes']),
            'identity_gaps':manifest['identity_gaps'],'source_failures':failures,
            'counts':{'unit':'steps','successful':available,'failed':failures,
                'partial':len(attempted_ids-set(completed)),
                'skipped':len(manifest['retained_scopes']),
                'unattempted':sum(u.unit_id not in attempted_ids for u in pending)},
            'whole_history_complete':False,'retries':0,
            'exit_code':75 if reason or failures or any(manifest['identity_gaps'].values()) else 0}
        atomic(root/'result.json',result)
        return result
