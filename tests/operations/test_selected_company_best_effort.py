"""Offline bounded failure-policy checks; all stores and allowances are fixtures."""
import json,unittest,hashlib
from collections import Counter
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from quant_data.errors import ConflictError,StoreUnavailableError,ValidationError
from quant_data.operations import selected_company_best_effort as module
from quant_data.operations.collection_queue import atomic,_retain,QueueResponse
from quant_data.operations.collection_parallel_prices import acquire_company_batch
from quant_data.operations.collection_transport import ProviderRequestFailure,_http_once,request_route
from quant_data.operations.equibles_transcript_backfill import private_directory
from tests.operations import test_selected_company_collection as companyfixtures
from tests.operations import test_collection_parallel_prices as accountfixtures
from tests.operations.test_collection_transport import FakeConnection,FakeResponse,unit

class BestEffortTests(unittest.TestCase):
    setUp=companyfixtures.SelectedCompanyTests.setUp
    plan=companyfixtures.SelectedCompanyTests.plan
    def run_policy(self,acquire,*,seed=None,cap=1000,bytes_cap=8*1024**3,deadline=1000):
        elapsed=[0]
        def sleep(n):elapsed[0]+=n
        return module.run_best_effort(root=self.root/'run',stores=self.stores,registry=self.registry,
            manifest=self.plan(),gate=None,credential='synthetic',evidence_root=self.root/'evidence',
            require_active=False,acquire=acquire,clock=lambda:datetime(2026,9,9,6,tzinfo=timezone.utc),
            monotonic=lambda:elapsed[0],sleeper=sleep,hard_deadline=deadline,
            request_cap=cap,byte_cap=bytes_cap,seed=seed)
    def fake(self,behavior):
        calls=[];counts=Counter()
        def acquire(**kw):
            self.assertTrue(kw['best_effort']);root=kw['root'];received=reserve=0
            for name in ('attempts','responses','blobs'):private_directory(root/name)
            for u in kw['units']:
                calls.append(u.unit_id);counts[u.unit_id]+=1
                atomic(root/'attempts'/(u.unit_id+'.json'),{'unit_id':u.unit_id})
                value=behavior(u,counts[u.unit_id])
                if isinstance(value,ProviderRequestFailure):
                    atomic(root/(u.unit_id+'.failure.json'),value.diagnostic())
                    reserve+=u.max_response_bytes
                else:
                    status,body=value;received+=len(body)
                    _retain(root,u,QueueResponse(status,body,'2026-09-09T05:01:00Z'),())
            return {'requests':len(kw['units']),'received_bytes':received,'unretained_byte_reserve':reserve,
                'uncertain_requests':0,'outcome':'complete'}
        return calls,acquire
    def test_first_pass_finishes_before_two_bounded_retries_and_gap_keeps_diagnostics(self):
        initial=[module.unit_from_dict(v) for v in self.plan()['units']]
        failed=initial[0].unit_id;rejected=initial[1].unit_id
        def behavior(u,n):
            if u.unit_id==failed:return ProviderRequestFailure('timeout',retryable=True)
            return (200,b'{"error":"bad source"}' if u.unit_id==rejected else b'[]')
        calls,acquire=self.fake(behavior);result=self.run_policy(acquire)
        self.assertEqual(set(calls[:19]),{u.unit_id for u in initial})
        self.assertEqual(calls[19:],[failed,failed])
        self.assertEqual(result['retry_attempts'],2);self.assertEqual(result['pending_units'],0)
        self.assertEqual(result['source_failures'],2);self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['unretained_byte_reserve'],3*initial[0].max_response_bytes)
        gaps=json.loads((self.root/'run/gaps.json').read_text())
        row=next(r for r in gaps['failed_units'] if module.unit_from_dict(r['unit']).unit_id==failed)
        self.assertEqual(row['result']['category'],'timeout')
        self.assertFalse(row['result']['source_response_available'])
    def test_retry_success_replaces_gap_without_repeating_other_requests(self):
        first=module.unit_from_dict(self.plan()['units'][0]).unit_id
        calls,acquire=self.fake(lambda u,n:ProviderRequestFailure('network_error',retryable=True)
            if u.unit_id==first and n==1 else (200,b'[]'))
        result=self.run_policy(acquire)
        self.assertEqual(Counter(calls)[first],2);self.assertEqual(len(calls),20)
        self.assertEqual(result['source_failures'],0);self.assertEqual(result['counts']['successful'],19)
        self.assertEqual(len(list((self.root/'run/outcomes').glob(first+'-*.json'))),2)
    def test_mixed_unretained_rate_and_server_errors_keep_longer_pause(self):
        initial=[module.unit_from_dict(v).unit_id for v in self.plan()['units']]
        def behavior(u,n):
            if n==1 and u.unit_id in initial[:2]:
                return ProviderRequestFailure('invalid_response',429 if u.unit_id==initial[0] else 503,retryable=True)
            return 200,b'[]'
        calls,base=self.fake(behavior);batches=[]
        def acquire(**kw):
            report=base(**kw);batches.append(1)
            if len(batches)==1:report.update(outcome='provider_pause',pause_guard={'status':503})
            return report
        with patch.object(module,'wait_bounded',return_value=True) as pause,patch.object(module,'release_pause'):
            result=self.run_policy(acquire)
        self.assertEqual(pause.call_count,1);self.assertEqual(pause.call_args.args[0],60)
        self.assertEqual(result['retry_attempts'],2);self.assertEqual(result['source_failures'],0)
    def test_request_budget_covers_retry_attempts(self):
        first=module.unit_from_dict(self.plan()['units'][0]).unit_id
        calls,acquire=self.fake(lambda u,n:ProviderRequestFailure('timeout',retryable=True) if u.unit_id==first else (200,b'[]'))
        result=self.run_policy(acquire,cap=20)
        self.assertEqual(len(calls),20);self.assertEqual(result['stop_reason'],'aggregate_budget')
        self.assertEqual(result['retry_attempts'],1);self.assertEqual(result['pending_units'],1)
    def test_byte_reserve_can_end_run_without_retry(self):
        initial=tuple(module.unit_from_dict(v) for v in self.plan()['units'])
        first=initial[0]
        calls,acquire=self.fake(lambda u,n:ProviderRequestFailure('timeout',retryable=True) if u==first else (200,b'[]'))
        result=self.run_policy(acquire,bytes_cap=first.max_response_bytes+1000)
        self.assertEqual(len(calls),19);self.assertEqual(result['stop_reason'],'aggregate_budget')
        self.assertEqual(result['retry_attempts'],0)
    def test_global_retry_cap_is_enforced_before_dispatch(self):
        # Small fixture exercises the same cap branch without a 500-request test.
        calls,acquire=self.fake(lambda u,n:ProviderRequestFailure('timeout',retryable=True))
        with patch.object(module,'MAX_RETRY_ATTEMPTS',3):
            result=self.run_policy(acquire)
        self.assertEqual(len(calls),22);self.assertEqual(result['retry_attempts'],3)
        self.assertEqual(result['source_failures'],19);self.assertEqual(result['pending_units'],0)
    def test_completed_seed_and_cached_source_are_never_reacquired(self):
        manifest=self.plan();initial=tuple(module.unit_from_dict(v) for v in manifest['units'])
        seed=module.initial_state(manifest,initial);done=initial[0];cached=initial[1]
        seed['pending_first'].remove(done.unit_id);seed['completed'][done.unit_id]={'outcome':'succeeded'}
        seed['requests']=2;seed['received_bytes']=4
        source=self.root/'cached'
        for name in ('responses','blobs'):private_directory(source/name)
        _retain(source,cached,QueueResponse(200,b'[]','2026-09-09T05:01:00Z'),())
        seed['cached'][cached.unit_id]=str(source)
        calls,acquire=self.fake(lambda u,n:(200,b'[]'))
        result=self.run_policy(acquire,seed=seed)
        self.assertNotIn(done.unit_id,calls);self.assertNotIn(cached.unit_id,calls)
        self.assertEqual(len(calls),17);self.assertEqual(result['requests'],19)
    def test_checkpoint_cannot_retry_success_or_lose_request_or_exceed_retry_count(self):
        manifest=self.plan();units=tuple(module.unit_from_dict(v) for v in manifest['units'])
        for mode in ('lost','retry_success','too_many','undercharge'):
            seed=module.initial_state(manifest,units);key=units[0].unit_id
            if mode=='lost':seed['pending_first'].pop()
            else:
                seed['pending_first'].remove(key);seed['completed'][key]={'outcome':'succeeded','retryable':True}
                if mode=='retry_success':seed['pending_retry']=[key]
                elif mode=='undercharge':seed['requests']=0
                else:seed['retry_counts']={key:3};seed['retry_attempts']=3
            with self.assertRaises(ConflictError):module.validate_state(manifest,units,seed,1000,8*1024**3)
    def test_sidecar_churn_retries_only_pure_read_with_same_physical_stores(self):
        from quant_data.operations.collection_fmp import read_when_company_quiet
        elapsed=[0]
        def sleep(n):elapsed[0]+=n
        churn=StoreUnavailableError('company store path or sidecar changed during the read')
        read=Mock(side_effect=[churn,'fresh'])
        value=read_when_company_quiet(read,stores=self.stores,deadline=1,monotonic=lambda:elapsed[0],
            sleeper=sleep,retry_sidecar_changes=True)
        self.assertEqual(value,'fresh');self.assertEqual(read.call_count,2)
        with self.assertRaises(StoreUnavailableError):
            read_when_company_quiet(Mock(side_effect=churn),stores=self.stores,deadline=1,monotonic=lambda:elapsed[0])
        def replaced():
            path=self.stores.path('company');replacement=path.with_suffix('.fixture-replacement')
            replacement.write_bytes(b'replacement');replacement.replace(path);raise churn
        read=Mock(side_effect=replaced)
        with self.assertRaises(StoreUnavailableError):
            read_when_company_quiet(read,stores=self.stores,deadline=1,monotonic=lambda:elapsed[0],
                sleeper=sleep,retry_sidecar_changes=True)
        self.assertEqual(read.call_count,1)
    def test_weekday_entrypoint_uses_same_policy_and_fixed_caps(self):
        from quant_data.operations import selected_company_refresh as refresh
        now=Mock(wraps=datetime);now.now.return_value=datetime(2026,9,11,23,tzinfo=timezone.utc)
        manifest=self.plan(False)
        with patch.object(refresh,'datetime',now),patch.object(refresh,'ROOT',self.root),\
            patch.object(refresh,'prepare',return_value=manifest),patch.object(refresh,'require_activation'),\
            patch.object(refresh,'read_when_company_quiet',side_effect=lambda fn,**kw:fn()),\
            patch.object(refresh,'load_bindings'),patch.object(refresh,'load_registry'),\
            patch.object(refresh,'host_allowance',return_value=Mock()),patch.object(refresh,'read_project_credential',return_value='synthetic'),\
            patch('quant_data.operations.selected_company_collection.validate_manifest'),\
            patch.object(refresh,'run_best_effort',return_value={'done':True}) as run:
            self.assertEqual(refresh.run_live(),{'done':True})
        self.assertEqual(run.call_args.kwargs['request_cap'],50000)
        self.assertEqual(run.call_args.kwargs['byte_cap'],4*1024**3)

class AccountFailureTests(unittest.TestCase):
    setUp=accountfixtures.ParallelPriceTests.setUp
    clock=accountfixtures.ParallelPriceTests.clock
    sleep=accountfixtures.ParallelPriceTests.sleep
    state=accountfixtures.ParallelPriceTests.state
    def units(self,n=12):
        return tuple(replace(u,collection='earnings_dates',endpoint='earnings',parameters=(('limit','1000'),('symbol',u.subject)))
            for u in accountfixtures.ParallelPriceTests.units(self,n))
    def batch(self,behavior,*,size=12,bytes_cap=100000):
        test=self
        class Ticket:
            def __init__(self,u,c,d):self.u=u;self.ready_at=test.elapsed+0.8
            def ready(self):return test.elapsed>=self.ready_at
            def result(self):
                value=behavior(self.u)
                if isinstance(value,Exception):raise value
                return QueueResponse(value,b'[]',test.clock().isoformat())
            def close(self):pass
        return acquire_company_batch(root=self.root/'batch',gate=self.gate,units=self.units(size),
            credential='synthetic',deadline=100,max_total_bytes=bytes_cap,ticket_factory=Ticket,
            clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep,best_effort=True)
    def test_known_transport_failure_keeps_charge_continues_and_reserves_missing_bytes(self):
        result=self.batch(lambda u:ProviderRequestFailure('timeout',retryable=True) if u.subject=='T000' else 200)
        self.assertEqual(result['requests'],12);self.assertEqual(result['retained_responses'],11)
        self.assertEqual(result['failed_requests'],1);self.assertEqual(result['uncertain_requests'],0)
        self.assertEqual(result['unretained_byte_reserve'],1024)
        self.assertEqual(self.state()['usage']['2026-09-10'],17)
        self.assertIsNone(self.state()['pending']);self.assertIsNone(self.state()['stopped'])
    def test_authentication_stop_cannot_be_replaced_by_later_transient_response(self):
        result=self.batch(lambda u:401 if u.subject=='T000' else 503)
        self.assertEqual(result['outcome'],'provider_stop');self.assertEqual(self.state()['stopped']['status'],401)
        with self.assertRaises(ConflictError):module.release_pause(self.gate,self.state()['stopped'],self.root,self.clock)
    def test_failed_retention_still_halts_with_original_charges(self):
        with patch('quant_data.operations.collection_parallel_prices._retain',side_effect=OSError('fixture disk full')):
            result=self.batch(lambda u:200)
        self.assertGreater(result['uncertain_requests'],0)
        self.assertIsNotNone(self.state()['pending']);self.assertEqual(self.state()['stopped']['status'],'uncertain')
    def test_pause_release_preserves_usage_and_rejects_changed_guard(self):
        result=self.batch(lambda u:501)
        self.assertEqual(result['outcome'],'provider_pause');before=self.state()
        wrong={**result['pause_guard'],'attempt':'wrong'}
        with self.assertRaises(ConflictError):module.release_pause(self.gate,wrong,self.root,self.clock)
        module.release_pause(self.gate,result['pause_guard'],self.root,self.clock)
        after=self.state();self.assertIsNone(after['stopped'])
        self.assertEqual(before['usage'],after['usage']);self.assertEqual(before['last_request_at'],after['last_request_at'])
    def test_unretained_rate_limit_still_pauses_and_remains_retryable(self):
        result=self.batch(lambda u:ProviderRequestFailure('response_too_large',429))
        self.assertLess(result['requests'],12);self.assertEqual(result['outcome'],'provider_pause')
        self.assertEqual(result['pause_guard']['status'],429);self.assertIsNone(self.state()['pending'])
        self.assertEqual(result['unretained_byte_reserve'],1024*result['requests'])
        failures=[json.loads(p.read_text()) for p in (self.root/'batch').glob('*.failure.json')]
        self.assertTrue(all(f['retryable'] for f in failures))
    def test_unretained_server_failure_mixed_with_successes_still_pauses(self):
        result=self.batch(lambda u:ProviderRequestFailure('invalid_response',503,retryable=True) if u.subject=='T000' else 200)
        self.assertLess(result['requests'],12);self.assertEqual(result['outcome'],'provider_pause')
        self.assertEqual(result['pause_guard']['status'],503);self.assertIsNone(self.state()['pending'])
    def test_unretained_transient_error_preserves_an_earlier_fatal_guard(self):
        result=self.batch(lambda u:RuntimeError('fixture storage safety fault') if u.subject=='T000'
            else ProviderRequestFailure('timeout',503,retryable=True))
        self.assertGreater(result['uncertain_requests'],0)
        self.assertEqual(self.state()['stopped']['status'],'uncertain')
        self.assertIsNotNone(self.state()['pending'])
    def test_authentication_status_without_retained_body_is_fatal(self):
        result=self.batch(lambda u:ProviderRequestFailure('response_too_large',401))
        self.assertGreater(result['uncertain_requests'],0);self.assertIsNotNone(self.state()['pending'])
    def test_missing_response_reservations_limit_subsequent_starts(self):
        result=self.batch(lambda u:ProviderRequestFailure('timeout',retryable=True),bytes_cap=2048)
        self.assertEqual(result['requests'],2);self.assertEqual(result['unretained_byte_reserve'],2048)

class TransportDiagnosticTests(unittest.TestCase):
    def test_safe_diagnostic_retains_category_and_status_without_exception_text(self):
        sender=Mock()
        with patch('quant_data.operations.collection_transport.http.client.HTTPSConnection',side_effect=TimeoutError('secret-token https://secret-host')):
            _http_once(sender,request_route(unit()),'secret-token',1,1024,True)
        self.assertEqual(sender.send.call_args.args[0],{'contract':'quant_data.provider_failure.v1',
            'category':'timeout','http_status':None,'retryable':True})
    def test_default_transport_keeps_legacy_failure_shape(self):
        sender=Mock()
        with patch('quant_data.operations.collection_transport.http.client.HTTPSConnection',side_effect=TimeoutError('synthetic')):
            _http_once(sender,request_route(unit()),'synthetic',1,1024)
        sender.send.assert_called_once_with(None)
    def test_invalid_length_diagnostic_contains_observed_status(self):
        sender=Mock()
        with patch('quant_data.operations.collection_transport.http.client.HTTPSConnection',FakeConnection),\
             patch.object(FakeResponse,'getheader',return_value='invalid'):
            _http_once(sender,request_route(unit()),'synthetic',1,1024,True)
        self.assertEqual(sender.send.call_args.args[0]['category'],'invalid_response')
        self.assertEqual(sender.send.call_args.args[0]['http_status'],200)
    def test_wait_and_retry_after_respect_original_deadline(self):
        elapsed=[0]
        def sleep(n):self.assertLessEqual(n,30);elapsed[0]+=n
        self.assertFalse(module.wait_bounded(500,61,lambda:elapsed[0],sleep));self.assertEqual(elapsed[0],61)
        now=datetime(2026,9,12,tzinfo=timezone.utc)
        self.assertEqual(module.retry_delay([{'status':429,'headers':{'retry-after':'120'}}],lambda:now),120)
