"""Parallel acquisition tests use private temporary roots and fake network only."""
import json
import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

from quant_data.errors import ConflictError
from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations import equibles_parallel_backfill as parallel
from tests.operations import test_equibles_paid_policy as fixtures
from tests.operations.test_equibles_paid_policy import AT, selection
from tests.company.test_equibles_transcripts import FakePublisher, body, catalog, event


def spawned_response(sender,path,key):
    sender.send((200,{'content-type':'application/json'},b'{}'))
    sender.close()


class SpawnTransportTests(unittest.TestCase):
    def test_spawned_http_children_are_safe_from_parallel_threads_without_network(self):
        from concurrent.futures import ThreadPoolExecutor
        transport=job.EquiblesTransport('offline-fixture',start_method='spawn')
        path='/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0'
        with patch.object(job,'_http_child',spawned_response):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(transport.request,path) for _ in range(2)]
                self.assertEqual([f.result() for f in futures],[(200,{'content-type':'application/json'},b'{}')]*2)


class Transport:
    def __init__(self, root, values=None, delay=0, remaining=10000):
        self.root,self.values,self.delay,self.remaining=root,values or {},delay,remaining
        self.paths=[];self.starts=[];self.lock=threading.Lock();self.active=self.peak=0
    def request(self,path):
        with self.lock:
            state=json.loads((self.root/'state.json').read_bytes())
            assert state['blocked']['reason']==parallel.GUARD
            self.paths.append(path);self.starts.append(time.monotonic())
            self.active+=1;self.peak=max(self.peak,self.active)
            self.remaining-=1;remaining=self.remaining
        try:
            time.sleep(self.delay)
            value=self.values.get(path)
            if isinstance(value,Exception):raise value
            if value is None:
                symbol=path.split('/')[3]
                value=catalog(symbol,(1,2,3,4)) if 'investor-events?' in path else body(symbol,int(path.split('/')[6]))
            if isinstance(value,tuple):status,raw=value
            else:status,raw=200,value
            return status,{'content-type':'application/json','x-ratelimit-limit':'10000',
                'x-ratelimit-remaining':str(remaining),
                'x-ratelimit-reset':str(int(AT.replace(hour=0,minute=0,second=0).timestamp()+86400))},raw
        finally:
            with self.lock:self.active-=1


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EquiblesPaidPolicyTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.convert(selection(('A',)));self.root=self.f.root;self.publisher=FakePublisher()
        spacing=patch.object(parallel,'SPACING_SECONDS',0.01);spacing.start();self.addCleanup(spacing.stop)
    def ready(self):
        state=self.f.state()
        rows=[event('A',q) for q in (1,2,3,4)]
        state['current']={'catalog':rows,'catalog_done':True,'event_index':0,
            'events':rows,'pages':[],'offset':0,'captured':0}
        from quant_data.operations.equibles_paid_policy import quota_bucket
        b=quota_bucket(state,AT.date().isoformat());b.update(paid_headers_verified=True,remaining=10000)
        self.f.save(state)
    def batch(self,t,requests=4,byte_limit=32*1024*1024,deadline=None):
        return parallel.acquire_batch(self.root,t,max_requests=requests,max_bytes=byte_limit,
            deadline=deadline or time.monotonic()+60,day=AT.date().isoformat(),clock=lambda:AT)
    def wave(self,t,**kw):
        return parallel.run_wave(self.root,self.publisher,t,clock=lambda:AT,
            max_run_seconds=120,**kw)
    def test_cache_only_miss_reserves_nothing(self):
        before=self.f.state()['usage']
        t=Transport(self.root)
        result=job.run(self.root,self.publisher,t,clock=lambda:AT,allow_paid=True,cache_only=True)
        self.assertEqual(result['outcome'],'cache_exhausted');self.assertEqual(t.paths,[])
        self.assertEqual(self.f.state()['usage'],before);self.assertIsNone(self.f.state()['pending'])
    def test_four_calls_overlap_but_one_owner_reserves_and_serially_publishes(self):
        self.ready();t=Transport(self.root,delay=0.08)
        result=self.batch(t)
        self.assertEqual(result['requests'],4);self.assertEqual(t.peak,4)
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],4)
        self.assertEqual(self.publisher.calls,[])
        report=self.wave(t)
        self.assertEqual(report['outcome'],'complete');self.assertEqual(report['requests_this_run'],0)
        self.assertEqual(self.publisher.calls,[('A',q) for q in (1,2,3,4)])
        self.assertEqual(len(t.paths),4)
    def test_first_quota_probe_is_single_request_then_wave_finishes(self):
        t=Transport(self.root)
        first=self.batch(t)
        self.assertEqual(first['requests'],1);self.assertEqual(len(t.paths),1)
        report=self.wave(t)
        self.assertEqual(report['outcome'],'complete');self.assertEqual(report['requests_this_run'],4)
        self.assertEqual(self.f.state()['transcripts'],4)
    def test_daily_cap_and_byte_reservation_bound_batch(self):
        self.ready();state=self.f.state()
        state['usage'][AT.date().isoformat()].update(attempted=9999,remaining=10000);self.f.save(state)
        t=Transport(self.root);result=self.batch(t)
        self.assertEqual(result['requests'],1)
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],10000)
        self.assertEqual(self.batch(t)['requests'],0);self.assertEqual(len(t.paths),1)
    def test_one_response_allocation_dispatches_one(self):
        self.ready();t=Transport(self.root)
        result=self.batch(t,byte_limit=job.MAX_BYTES)
        self.assertEqual(result['requests'],1)
    def test_failed_first_call_stops_waiting_callers_without_retry(self):
        self.ready();path=parallel.planned_paths(self.f.state())[0]
        t=Transport(self.root,{path:(500,b'{}')})
        result=self.batch(t)
        self.assertEqual(result['outcome'],'blocked');self.assertEqual(len(t.paths),1)
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],4)
        self.assertEqual(len(list((self.root/'attempts').glob('*.not-dispatched.json'))),3)
        self.assertFalse(parallel.recover_batch(self.root,clock=lambda:AT))
        self.assertEqual(len(t.paths),1)
    def test_received_batch_survives_crash_before_settlement_without_refetch(self):
        self.ready();t=Transport(self.root)
        with patch.object(parallel,'_settle',side_effect=RuntimeError('power loss')):
            with self.assertRaises(RuntimeError):self.batch(t)
        self.assertEqual(len(t.paths),4)
        self.assertTrue(parallel.recover_batch(self.root,clock=lambda:AT))
        result=self.wave(t)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(len(t.paths),4)
    def test_settled_journal_before_checkpoint_crash_is_reconciled(self):
        self.ready();t=Transport(self.root)
        original=job.atomic;cut=[False]
        def fail(path,value,**kw):
            if path==self.root/'state.json' and isinstance(value,dict) and value.get('blocked') is None:
                cut[0]=True;raise RuntimeError('power loss')
            return original(path,value,**kw)
        with patch.object(job,'atomic',fail):
            with self.assertRaises(RuntimeError):self.batch(t)
        self.assertTrue(cut[0]);self.assertTrue(parallel.recover_batch(self.root,clock=lambda:AT))
        self.assertIsNone(self.f.state()['blocked']);self.assertEqual(len(t.paths),4)
    def test_uncertain_attempt_keeps_guard_and_is_never_resubmitted(self):
        self.ready();t=Transport(self.root)
        with patch.object(parallel.ThreadPoolExecutor,'submit',side_effect=RuntimeError('crash before dispatch')):
            with self.assertRaises(RuntimeError):self.batch(t)
        self.assertFalse(parallel.recover_batch(self.root,clock=lambda:AT))
        self.assertEqual(t.paths,[]);self.assertIsNotNone(self.f.state()['blocked'])
    def test_reordered_headers_never_increase_saved_remaining(self):
        self.ready();t=Transport(self.root)
        self.batch(t)
        state=self.f.state();bucket=state['usage'][AT.date().isoformat()]
        self.assertEqual(bucket['remaining'],9996)
        self.assertTrue(parallel.recover_batch(self.root,clock=lambda:AT))
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['remaining'],9996)
    def test_pagination_waits_for_preceding_page_then_uses_exact_offset(self):
        self.ready();state=self.f.state();first=parallel.planned_paths(state)[0]
        second=first.rsplit('offset=',1)[0]+'offset=1'
        t=Transport(self.root,{first:body('A',1,total=2),second:body('A',1,offset=1,total=2)})
        result=self.wave(t)
        self.assertEqual(result['outcome'],'complete')
        self.assertIn(second,t.paths)
        self.assertLess(t.paths.index(first),t.paths.index(second))
        self.assertEqual(self.publisher.calls,[('A',q) for q in (1,2,3,4)])
    def test_expired_allocation_has_zero_network_and_zero_new_charge(self):
        self.ready();before=self.f.state();t=Transport(self.root)
        result=self.batch(t,deadline=time.monotonic()+20)
        self.assertEqual(result['requests'],0);self.assertEqual(t.paths,[])
        self.assertEqual(self.f.state(),before)
    def test_wrong_day_cannot_dispatch(self):
        self.ready();t=Transport(self.root)
        result=parallel.acquire_batch(self.root,t,max_requests=4,max_bytes=32*1024*1024,
            deadline=time.monotonic()+60,day='2026-09-08',clock=lambda:AT)
        self.assertEqual(result['requests'],0);self.assertEqual(t.paths,[])
    def test_malformed_success_stops_waiting_callers_and_preserves_receipt(self):
        self.ready();path=parallel.planned_paths(self.f.state())[0]
        t=Transport(self.root,{path:b'{}'})
        result=self.batch(t)
        self.assertEqual(result['outcome'],'blocked');self.assertEqual(len(t.paths),1)
        self.assertTrue((self.root/'responses'/'2026-09-09-001.json').exists())

    def test_invalid_bounds_never_reserve(self):
        from quant_data.errors import ValidationError
        self.ready();before=self.f.state();t=Transport(self.root)
        with self.assertRaises(ValidationError):
            self.batch(t,requests=1001)
        self.assertEqual(self.f.state(),before);self.assertEqual(t.paths,[])

    def test_bounded_wave_keeps_existing_progress_reader_compatible(self):
        from datetime import datetime,timezone
        from quant_data.inspector_equibles import read_equibles_progress
        t=Transport(self.root)
        report=self.wave(t,max_requests=2)
        self.assertEqual(report['outcome'],'run_resource_limit')
        status=read_equibles_progress(self.root,observed_at=datetime.now(timezone.utc)+timedelta(days=1))
        self.assertTrue(status['available']);self.assertEqual(status['requests_this_run'],2)
        self.assertEqual(status['transcripts'],1)

    def test_known_undispatched_deadline_is_resumable_and_charge_is_retained(self):
        self.ready();elapsed=[0.0];t=Transport(self.root);original=job.atomic
        def save(path,value,**kw):
            result=original(path,value,**kw)
            if path==self.root/'state.json' and value.get('blocked'):elapsed[0]=61
            return result
        with patch.object(job,'atomic',save):
            result=parallel.acquire_batch(self.root,t,max_requests=4,max_bytes=32*1024*1024,
                deadline=60,day=AT.date().isoformat(),clock=lambda:AT,
                monotonic=lambda:elapsed[0],sleeper=lambda x:None)
        self.assertEqual(t.paths,[]);self.assertEqual(result['outcome'],'acquired')
        self.assertIsNone(self.f.state()['blocked'])
        self.assertEqual(self.f.state()['undispatched_reservations'],4)
        self.assertTrue(parallel.recover_batch(self.root,clock=lambda:AT))
        self.assertEqual(self.f.state()['undispatched_reservations'],4)
        self.assertEqual(self.batch(t)['requests'],4)
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],8)

    def test_verified_429_defers_and_keeps_charges_without_permanent_block(self):
        self.ready();path=parallel.planned_paths(self.f.state())[0]
        t=Transport(self.root,{path:(429,b'{}')})
        result=self.batch(t)
        self.assertEqual(result['outcome'],'provider_quota');self.assertEqual(len(t.paths),1)
        state=self.f.state();self.assertIsNone(state['blocked']);self.assertIsNone(state['pending'])
        self.assertEqual(state['usage'][AT.date().isoformat()]['remaining'],0)
        self.assertEqual(state['usage'][AT.date().isoformat()]['attempted'],4)
        self.assertEqual(self.batch(t)['requests'],0);self.assertEqual(len(t.paths),1)

    def test_success_with_exhausted_quota_stops_queued_callers(self):
        self.ready();t=Transport(self.root,remaining=1)
        result=self.batch(t)
        self.assertEqual(result['outcome'],'acquired');self.assertEqual(len(t.paths),1)
        state=self.f.state();self.assertIsNone(state['blocked']);self.assertIsNone(state['pending'])
        self.assertEqual(state['usage'][AT.date().isoformat()]['attempted'],4)
        self.assertEqual(state['usage'][AT.date().isoformat()]['remaining'],0)
        self.assertEqual(len(list((self.root/'attempts').glob('*.not-dispatched.json'))),3)
        self.assertEqual(self.batch(t)['requests'],0);self.assertEqual(len(t.paths),1)

    def test_late_high_remaining_header_cannot_reopen_stopped_queue(self):
        self.ready();t=Transport(self.root);original=t.request
        first,second,*_=parallel.planned_paths(self.f.state())
        def out_of_order(path):
            status,headers,raw=original(path)
            if path==first:
                time.sleep(0.08)
                headers['x-ratelimit-remaining']='9999'
            elif path==second:headers['x-ratelimit-remaining']='0'
            return status,headers,raw
        t.request=out_of_order
        self.batch(t)
        self.assertEqual(set(t.paths),{first,second})
        state=self.f.state();self.assertIsNone(state['blocked'])
        self.assertEqual(state['usage'][AT.date().isoformat()]['remaining'],0)
        self.assertEqual(len(list((self.root/'attempts').glob('*.not-dispatched.json'))),2)

    def test_headerless_404_preserves_prior_verified_quota(self):
        self.ready();t=Transport(self.root)
        original=t.request
        def missing(path):
            status,headers,raw=original(path);return 404,{},b'{}'
        t.request=missing
        self.batch(t,requests=1)
        bucket=self.f.state()['usage'][AT.date().isoformat()]
        self.assertTrue(bucket['paid_headers_verified']);self.assertEqual(bucket['remaining'],9999)

    def test_first_headerless_404_does_not_verify_quota(self):
        t=Transport(self.root);original=t.request
        def missing(path):
            original(path);return 404,{},b'{}'
        t.request=missing
        self.batch(t,requests=1)
        self.assertFalse(self.f.state()['usage'][AT.date().isoformat()]['paid_headers_verified'])
        result=self.batch(t,requests=1)
        self.assertEqual(result['outcome'],'quota_verification_required');self.assertEqual(len(t.paths),1)

    def test_production_pacing_is_one_second_global(self):
        self.ready();elapsed=[0.0];starts=[]
        def sleep(seconds):elapsed[0]+=seconds
        t=Transport(self.root)
        request=t.request
        def tracked(path):starts.append(elapsed[0]);return request(path)
        t.request=tracked
        with patch.object(parallel,'SPACING_SECONDS',1):
            parallel.acquire_batch(self.root,t,max_requests=4,max_bytes=32*1024*1024,
                deadline=60,day=AT.date().isoformat(),clock=lambda:AT,
                monotonic=lambda:elapsed[0],sleeper=sleep)
        self.assertEqual(starts,[1,2,3,4])


if __name__=='__main__':
    unittest.main()
