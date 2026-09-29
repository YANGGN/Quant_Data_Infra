"""Parallel historical downloads with one independent canonical price writer."""
from dataclasses import asdict
from datetime import datetime,timezone
import copy,time
from ..errors import ConflictError,ValidationError
from ..market.stage12_incremental import Stage12BIncrementalCollector,Stage12BFixtureResponse
from .collection_price_windows import HistoryPriceWindow,history_price_units,SelectedPriceHistoryPublisher,parse_history_response,PinnedHistoryPlanner
from .collection_plan import AcquisitionUnit
from .collection_queue import atomic,_read,_response
from .collection_parallel_prices import acquire_batch
from .selected_price_backfill import next_window,advance,MAX_BYTES,MAX_REQUESTS
from .equibles_transcript_backfill import private_directory,job_lock

class RowsOnly:
    # This existing parser is pure and has no store dependency.
    _parse_row=Stage12BIncrementalCollector._parse_row

def stats(states):
    return {'total_symbols':len(states),'completed_symbols':sum(s['status']=='complete' for s in states.values()),
        'started_symbols':sum(s['completed_windows']>0 for s in states.values()),
        'completed_windows':sum(s['completed_windows'] for s in states.values()),
        'written_prices':sum(s['written_prices'] for s in states.values())}

def requests_from_manifest(raw):
    result=[]
    for pair in raw:
        u=dict(pair['unit']);u['parameters']=tuple(tuple(x) for x in u['parameters']);u['selected_symbols']=tuple(u['selected_symbols'])
        result.append((AcquisitionUnit(**u),HistoryPriceWindow(**pair['window'])))
    return tuple(result)

def acquire_history(*,root,stores,selection,cutoff,baseline,gate,credential,deadline,planner,acquire=acquire_batch):
    if not isinstance(planner,PinnedHistoryPlanner) or planner.selection!=selection or planner.cutoff!=cutoff:
        raise ValidationError('Price acquisition requires its previously validated population planner')
    private_directory(root/'batches')
    with job_lock(root/'producer'):
        if (root/'acquisition-started.json').exists():raise ConflictError('Parallel history acquisition already started; no retry')
        atomic(root/'acquisition-started.json',{'at':datetime.now(timezone.utc).isoformat(),'deadline_monotonic':deadline})
        states=copy.deepcopy(baseline['states']);requests=baseline['requests'];received=baseline['received_bytes'];byte_reserve=baseline.get('unretained_byte_reserve',0);index=0;outcome='complete';gaps=[s['gap'] for s in states.values() if 'gap' in s];downloaded_rows=0
        if type(byte_reserve) is not int or not 0<=byte_reserve<=MAX_BYTES:raise ValidationError('Unretained price byte reserve is invalid')
        while any(s['status']=='pending' for s in states.values()):
            writer_failure=_read(root/'publication-result.json')
            if writer_failure is not None and writer_failure['outcome']!='complete':outcome='publisher_stop';break
            pending=sorted((s for s in states.values() if s['status']=='pending'),key=lambda s:(s['completed_windows'],s['target']['symbol']))
            if time.monotonic()+2>=deadline or requests>=MAX_REQUESTS or received+byte_reserve+16*1024**2>MAX_BYTES:outcome='invocation_budget';break
            windows=tuple(next_window(s) for s in pending[:min(100,MAX_REQUESTS-requests)])
            work=planner.units(windows)
            batch=root/'batches'/f'{index:06d}';private_directory(batch)
            atomic(batch/'work.json',[{'unit':asdict(u),'window':asdict(w)} for u,w in work])
            error=None
            try:report=acquire(root=batch/'queue',gate=gate,units=tuple(u for u,w in work),credential=credential,
                deadline=deadline,max_total_bytes=MAX_BYTES-received-byte_reserve)
            except Exception as e:report=None;error=type(e).__name__;outcome='acquisition_stop'
            # The attempt files precede every HTTP start. Accounting remains
            # conservative if the parent fails before a batch result is written.
            charged=len(list((batch/'queue/attempts').glob('*.json')));bytes_here=0;valid=[];held={}
            for u,w in work:
                retained=_response(batch/'queue',u.unit_id,u)
                if retained is None:continue
                receipt,body=retained;bytes_here+=len(body)
                if receipt['status']!=200:
                    gap={'unit_id':u.unit_id,'symbol':u.subject,'status':receipt['status']}
                    gaps.append(gap);held[u.subject]=gap;states[u.subject].update(status='provider_gap',gap=gap);continue
                try:rows=parse_history_response(RowsOnly(),{'symbol':u.subject,'from':w.start,'to':w.end},Stage12BFixtureResponse(200,'application/json',body,0))
                except Exception as e:
                    gap={'unit_id':u.unit_id,'symbol':u.subject,'error_type':type(e).__name__}
                    gaps.append(gap);held[u.subject]=gap;states[u.subject].update(status='validation_gap',gap=gap);continue
                valid.append(u.unit_id);downloaded_rows+=len(rows);advance(states[u.subject],w,empty=not rows,written=0)
            requests+=charged;received+=bytes_here
            atomic(batch/'ready.json',{'valid_units':valid,'held_symbols':held,'requests':charged,'bytes':bytes_here,'outcome':report['outcome'] if report else outcome})
            progress={'at':datetime.now(timezone.utc).isoformat(),'outcome':'running',**stats(states),'requests':requests,'received_bytes':received,'unretained_byte_reserve':byte_reserve,'downloaded_rows':downloaded_rows,'batch':index,'peak_inflight':report.get('peak_inflight') if report else None}
            atomic(root/'acquisition-progress.json',progress,replace=True);print(progress,flush=True);index+=1
            if error or report['outcome'] not in ('complete','batch_boundary','provider_failure'):
                if not error:outcome=report['outcome']
                break
            # Give waiting routine FMP requests an opportunity between batches.
            time.sleep(0.2)
        if any(s['status']!='complete' for s in states.values()) and outcome=='complete':
            outcome='incomplete' if any(s['status']=='pending' for s in states.values()) else 'complete_with_gaps'
        result={'at':datetime.now(timezone.utc).isoformat(),'outcome':outcome,**stats(states),'requests':requests,'received_bytes':received,
            'unretained_byte_reserve':byte_reserve,'downloaded_rows':downloaded_rows,'batches':index,'gaps':gaps,'states':states}
        atomic(root/'acquisition-result.json',result);return result

def publish_history(*,root,stores,selection,cutoff,baseline,collector,deadline,sleeper=time.sleep):
    with job_lock(root/'publisher'):
        if (root/'publication-started.json').exists():raise ConflictError('Parallel history publisher already started; reconcile retained work')
        atomic(root/'publication-started.json',{'at':datetime.now(timezone.utc).isoformat(),'deadline_monotonic':deadline})
        states=copy.deepcopy(baseline['states']);index=0;outcome='complete';failure=None
        try:
            while time.monotonic()<deadline:
                batch=root/'batches'/f'{index:06d}';ready=_read(batch/'ready.json')
                if ready is None:
                    acquisition=_read(root/'acquisition-result.json')
                    if acquisition is not None:break
                    sleeper(0.1);continue
                work=requests_from_manifest(_read(batch/'work.json'))
                publisher=SelectedPriceHistoryPublisher(stores=stores,selection=selection,cutoff=cutoff,collector=collector,requests=work)
                private_directory(batch/'publications')
                valid=set(ready['valid_units'])
                if not valid<={u.unit_id for u,w in work}:raise ConflictError('Price publication unit is outside its batch')
                for u,w in work:
                    if u.unit_id not in valid:continue
                    receipt,body=_response(batch/'queue',u.unit_id,u)
                    result=publisher(unit=u,retained=None,receipt=receipt,body=body,deadline=deadline)
                    atomic(batch/'publications'/(u.unit_id+'.json'),{'unit_id':u.unit_id,'request_id':u.request_id,'result':result})
                    advance(states[u.subject],w,empty=result['coverage']=='empty',written=result.get('written_versions',0))
                held=ready.get('held_symbols',{})
                if not set(held)<={u.subject for u,w in work}:raise ConflictError('Held price symbol is outside its batch')
                for symbol,gap in held.items():states[symbol].update(status='validation_gap',gap=gap)
                index+=1
                atomic(root/'publication-progress.json',{'at':datetime.now(timezone.utc).isoformat(),'outcome':'running',**stats(states),'batches':index},replace=True)
            acquisition=_read(root/'acquisition-result.json')
            if acquisition is None or acquisition['outcome'] not in ('complete','complete_with_gaps') or any(s['status']=='pending' for s in states.values()):outcome='incomplete'
            elif any(s['status']!='complete' for s in states.values()):outcome='complete_with_gaps'
        except Exception as e:outcome='publication_stop';failure={'error_type':type(e).__name__,'batch':index}
        result={'at':datetime.now(timezone.utc).isoformat(),'outcome':outcome,**stats(states),'batches':index,'failure':failure,'states':states}
        atomic(root/'publication-result.json',result);return result
