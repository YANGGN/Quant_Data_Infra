"""Dedicated, finite historical price worker; never invoked by the daily timer.

Walk non-overlapping five-year windows backwards beyond listing evidence until
an empty older window establishes a provider boundary. An explicit 1900 guard,
request/byte/deadline exhaustion, or failed request is unfinished coverage.
"""
from dataclasses import dataclass,asdict
from datetime import date,timedelta,datetime,timezone
import time
from ..errors import ValidationError,ConflictError
from ..json_codec import loads_strict
from .collection_price_windows import HistoryPriceWindow,history_price_units,SelectedPriceHistoryPublisher
from .collection_plan import ProviderBudget,build_plan
from .collection_queue import atomic,run_queue,_read,_response,BoundedForwardClock
from .equibles_transcript_backfill import private_directory,job_lock

MAX_REQUESTS=13872
MAX_BYTES=8*1024**3
MAX_SECONDS=14400
SEARCH_FLOOR=date(1900,1,1)

@dataclass(frozen=True)
class HistoryTarget:
    symbol:str
    ipo_date:str|None
    retained_history_from:str|None=None
    retained_history_to:str|None=None

def previous_window(symbol,end):
    end=date.fromisoformat(end)
    if end<SEARCH_FLOOR:raise ValidationError('Historical search boundary reached')
    return HistoryPriceWindow(symbol,max(SEARCH_FLOOR,date(end.year-4,1,1)).isoformat(),end.isoformat())

def initial_state(target,end):
    end=date.fromisoformat(end)
    ipo=date.fromisoformat(target.ipo_date) if target.ipo_date else None
    if ipo is not None and (ipo<SEARCH_FLOOR or ipo>end):raise ValidationError('Listing evidence is outside the historical plan')
    if bool(target.retained_history_from)!=bool(target.retained_history_to):raise ValidationError('Retained history needs both boundaries')
    if target.retained_history_from:
        first=date.fromisoformat(target.retained_history_from);last=date.fromisoformat(target.retained_history_to)
        if first>last or first<SEARCH_FLOOR or last>end:raise ValidationError('Retained history boundaries are invalid')
        older=(first-timedelta(days=1)).isoformat()
        tail=HistoryPriceWindow(target.symbol,(last+timedelta(days=1)).isoformat(),end.isoformat()) if last<end else None
    else:
        if ipo is None:raise ValidationError('New price history requires retained listing evidence')
        older=end.isoformat();tail=None
    return {'target':asdict(target),'cursor':older,'tail':asdict(tail) if tail else None,
        'status':'pending','completed_windows':0,'written_prices':0,'empty_windows':0}

def next_window(state):
    if state['status']!='pending':return None
    if state['tail']:return HistoryPriceWindow(**state['tail'])
    return previous_window(state['target']['symbol'],state['cursor'])

def advance(state,window,*,empty,written):
    if window!=next_window(state):raise ConflictError('Historical continuation differs from its retained cursor')
    state['completed_windows']+=1;state['written_prices']+=written;state['empty_windows']+=int(empty)
    if state['tail']:
        state['tail']=None;return
    ipo=state['target']['ipo_date']
    past_listing=state['target']['retained_history_from'] is not None or window.end<ipo
    if empty and past_listing:
        state.update(status='complete',boundary={'from':window.start,'to':window.end,'evidence':'empty_provider_window_before_listing_or_retained_history'})
    elif window.start==SEARCH_FLOOR.isoformat():state['status']='search_boundary_reached'
    else:state['cursor']=(date.fromisoformat(window.start)-timedelta(days=1)).isoformat()

def run_history(*,root,stores,selection,collector,fetch,targets,end,cutoff,
                monotonic=time.monotonic,utcnow=lambda:datetime.now(timezone.utc),continuation=False):
    """One manual run, immutable attempts, no GET retries, progressive publication."""
    private_directory(root)
    with job_lock(root):
        already_started=(root/'started.json').exists()
        if already_started and not continuation:raise ConflictError('Historical run already started; reconcile original checkpoints before continuation')
        if continuation and not already_started:raise ConflictError('Continuation requires an original historical run')
        initial={t.symbol:initial_state(t,end) for t in targets}
        expected={s.provider_symbol for s in selection.eligible}
        if len(initial)!=len(targets) or set(initial)!=expected:raise ValidationError('History must cover exactly the supported selected securities')
        if continuation:
            manifest=_read(root/'manifest.json');previous=_read(root/'result.json');ledger=_read(root/'queue/ledger.json')
            if (manifest['scope_sha256']!=selection.scope_sha256 or manifest['end']!=end
                or manifest['targets']!=[asdict(t) for t in targets]
                or manifest['maximum_requests']!=MAX_REQUESTS or manifest['maximum_bytes']!=MAX_BYTES
                or manifest['maximum_seconds']!=MAX_SECONDS or ledger['pending'] is not None
                or any(u['status']!='published' for u in ledger['units'].values())
                or len(ledger['units'])!=previous['completed_windows']
                or previous['outcome']=='complete'):
                raise ConflictError('Historical continuation requires fully settled original checkpoints')
            deadline=float(_read(root/'started.json')['deadline_monotonic'])
            if monotonic()>=deadline:raise ConflictError('Original historical deadline has expired')
            states=previous['states'];cutoff=manifest['cutoff']
            if set(states)!=set(initial) or any(states[k]['target']!=initial[k]['target'] for k in initial):
                raise ConflictError('Historical continuation changed its population')
            for name in ('result.json','canonical-coverage.json'):
                old=root/name
                if old.exists():atomic(root/(name+'.before-continuation'),old.read_bytes())
            atomic(root/'continuation.json',{'at':utcnow().isoformat(),'original_deadline_monotonic':deadline,
                'retained_requests':previous['requests'],'remaining_requests':MAX_REQUESTS-previous['requests'],
                'retries':0,'basis':'All earlier requests have retained successful publications; continue only next unrequested windows.'})
        else:
            states=initial;deadline=monotonic()+MAX_SECONDS;ledger=None
            atomic(root/'manifest.json',{'contract':'quant_data.selected_price_history.v1','scope_sha256':selection.scope_sha256,
                'cutoff':cutoff,'end':end,'search_floor':SEARCH_FLOOR.isoformat(),'targets':[asdict(t) for t in targets],
                'maximum_requests':MAX_REQUESTS,'maximum_bytes':MAX_BYTES,'maximum_seconds':MAX_SECONDS,'retries':0})
            atomic(root/'started.json',{'at':utcnow().isoformat(),'deadline_monotonic':deadline})
        utcnow=BoundedForwardClock(utcnow,deadline=deadline,monotonic=monotonic,last=ledger['last_request_at'] if ledger else None)
        requests=previous['requests'] if continuation else 0
        received=previous['received_bytes'] if continuation else 0
        reports=[];outcome='complete';failure=None;batch=0
        attempted={k for k,s in states.items() if s['completed_windows']}
        def tracked_fetch(**kwargs):
            attempted.add(kwargs['unit'].subject)
            return fetch(**kwargs)
        while any(s['status']=='pending' for s in states.values()):
            pending=[s for s in states.values() if s['status']=='pending']
            # Progress newest history across the universe before older rounds.
            pending.sort(key=lambda s:(s['completed_windows'],s['target']['symbol']))
            remaining=int(deadline-monotonic());available=MAX_REQUESTS-requests;bytes_left=MAX_BYTES-received
            if remaining<45 or available<1 or bytes_left<16*1024*1024:outcome='invocation_budget';break
            windows=tuple(next_window(s) for s in pending[:min(50,available)])
            work=history_price_units(stores,selection,windows=windows,cutoff=cutoff)
            publisher=SelectedPriceHistoryPublisher(stores=stores,selection=selection,cutoff=cutoff,collector=collector,requests=work)
            plan=build_plan(selections=(selection,),units=tuple(u for u,w in work),
                budgets=(ProviderBudget('fmp',len(work),min(bytes_left,1024**3),min(remaining,7200),250),),created_at=cutoff)
            def publish(**kwargs):return publisher(**kwargs,deadline=deadline,monotonic=monotonic)
            try:
                report=run_queue(root=root/'queue',plan=plan,provider='fmp',fetch=tracked_fetch,publish=publish,
                    secret_values=getattr(fetch,'secret_values',()),deadline=deadline,monotonic=monotonic,utcnow=utcnow)
                reports.append(report)
            except Exception as error:
                failure={'error_type':type(error).__name__,'batch':batch,'clock_at_stop':utcnow.last.isoformat() if utcnow.last else None};outcome='stopped';report=None
            # Ledger charges survive exceptions; never infer usage from only
            # completed batch reports or repeat a response that was retained.
            ledger=_read(root/'queue/ledger.json') or {'usage':{}}
            requests=sum(b['charged_attempts'] for b in ledger['usage'].values())
            received=sum(b['received_bytes'] for b in ledger['usage'].values())
            for u,w in work:
                publication=_read(root/'queue/publications'/(u.unit_id+'.json'))
                if publication is not None:
                    if publication['request_id']!=u.request_id:raise ConflictError('Historical checkpoint differs from its exact request')
                    result=publication['result'];advance(states[u.subject],w,empty=result.get('coverage')=='empty',written=result.get('written_versions',0))
            progress={'at':utcnow().isoformat(),'outcome':'running' if outcome=='complete' else outcome,'requests':requests,'received_bytes':received,
                'completed_symbols':sum(s['status']=='complete' for s in states.values()),
                'total_symbols':len(states),'completed_windows':sum(s['completed_windows'] for s in states.values()),
                'written_prices':sum(s['written_prices'] for s in states.values()),'states':states}
            atomic(root/'progress.json',progress,replace=True)
            print({k:v for k,v in progress.items() if k!='states'},flush=True)
            batch+=1
            if failure or report['outcome']!='complete':
                if not failure:outcome=report['outcome']
                break
        incomplete=[s for s in states.values() if s['status']!='complete']
        if incomplete and outcome=='complete':outcome='incomplete'
        result={'contract':'quant_data.selected_price_history_result.v1','scope_sha256':selection.scope_sha256,
            'at':utcnow().isoformat(),'outcome':outcome,'completed_symbols':len(states)-len(incomplete),
            'unattempted_symbols':sum(s['target']['symbol'] not in attempted for s in incomplete),
            'failed_symbols':sum(s['target']['symbol'] in attempted for s in incomplete),
            'requests':requests,'received_bytes':received,'completed_windows':sum(s['completed_windows'] for s in states.values()),
            'written_prices':sum(s['written_prices'] for s in states.values()),'failure':failure,'states':states,
            'canonical_audit_verified':False,'whole_exchange_session_completeness':'not_asserted'}
        atomic(root/'result.json',result,replace=continuation)
        return result
