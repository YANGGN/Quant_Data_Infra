"""Offline finite-controller checks: explicit temporary roots and fake transport."""
import importlib.util,json,unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from quant_data.errors import ConflictError
from quant_data.operations import equibles_transcript_backfill as job
from tests.operations import test_equibles_paid_policy as fixtures
from tests.operations.test_equibles_paid_policy import PaidTransport,AT
from tests.company.test_equibles_transcripts import FakePublisher,catalog,body
from quant_data.operations import equibles_daily_backfill as daily

class DailyTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EquiblesPaidPolicyTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.convert();self.root=self.f.root;self.clock=AT;self.elapsed=0
        self.publisher=FakePublisher()
    def sleep(self,seconds):
        self.clock+=timedelta(seconds=seconds);self.elapsed+=seconds
    def run_day(self,values,remaining=99900):
        transport=PaidTransport(values,remaining=remaining,clock=lambda:self.clock)
        result=daily.run_day(self.root,self.publisher,transport,clock=lambda:self.clock,
            monotonic=lambda:self.elapsed,sleeper=self.sleep)
        return result,transport
    def test_account_charges_allow_only_one_more_request_even_if_provider_reports_100k(self):
        state=self.f.state();state['usage'][AT.date().isoformat()]={'attempted':9999,'remaining':90001}
        self.f.save(state)
        result,t=self.run_day([catalog('A'),body('A')])
        self.assertEqual(result['requests'],1);self.assertEqual(len(t.paths),1)
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],10000)
        self.assertEqual(result['outcome'],'daily_cap')
    def test_no_request_at_cap(self):
        state=self.f.state();state['usage'][AT.date().isoformat()]={'attempted':10000,'remaining':90000};self.f.save(state)
        result,t=self.run_day([])
        self.assertEqual(result['outcome'],'daily_cap');self.assertEqual(t.paths,[])
    def test_failure_is_not_retried(self):
        result,t=self.run_day([RuntimeError('interrupted')])
        self.assertEqual(result['outcome'],'blocked');self.assertEqual(len(t.paths),1)
        result,t=self.run_day([])
        self.assertEqual(result['outcome'],'blocked');self.assertEqual(t.paths,[])
    def test_provider_quota_wins(self):
        result,t=self.run_day([catalog('A')],remaining=1)
        self.assertEqual(result['outcome'],'daily_quota');self.assertEqual(len(t.paths),1)
    def test_complete_preserves_zero_request_replay(self):
        result,t=self.run_day([catalog('A'),body('A'),catalog('B'),body('B')])
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['requests'],4)
        result,t=self.run_day([])
        self.assertEqual(result['outcome'],'complete');self.assertEqual(t.paths,[])
        self.assertEqual(len(self.publisher.calls),2)
    def test_midnight_does_not_open_second_quota_day(self):
        self.clock=AT.replace(hour=23,minute=59,second=50)
        result,t=self.run_day([])
        self.assertEqual(t.paths,[]);self.assertEqual(result['outcome'],'allocation_exhausted')
    def test_dispatch_rechecks_day_and_charge(self):
        state=self.f.state();state['usage'][AT.date().isoformat()]={'attempted':10001,'remaining':89999};self.f.save(state)
        t=PaidTransport([]);wrapped=daily.DailyCappedTransport(self.root,t,AT.date().isoformat(),lambda:self.clock,21600,lambda:self.elapsed)
        with self.assertRaises(ConflictError):wrapped.request('unused')
        state['usage'][AT.date().isoformat()]['attempted']=1;self.f.save(state);self.clock+=timedelta(days=1)
        with self.assertRaises(ConflictError):wrapped.request('unused')
        self.assertEqual(t.paths,[])
    def test_slow_checkpoint_read_prevents_late_dispatch_and_retains_charge(self):
        state=self.f.state();state['usage'][AT.date().isoformat()]={'attempted':1,'remaining':99999};self.f.save(state)
        t=PaidTransport([])
        wrapped=daily.DailyCappedTransport(self.root,t,AT.date().isoformat(),lambda:self.clock,21600,lambda:self.elapsed)
        original=job.read_file
        def slow(*args):
            result=original(*args);self.elapsed=21601;return result
        with patch.object(job,'read_file',slow):
            with self.assertRaises(ConflictError):wrapped.request('unused')
        self.assertEqual(t.paths,[])
        self.assertEqual(self.f.state()['usage'][AT.date().isoformat()]['attempted'],1)
    def test_dated_allocation_rejects_setup_crossing_midnight(self):
        transport=PaidTransport([])
        prior=(self.root/'state.json').read_bytes()
        with self.assertRaises(ConflictError):
            daily.run_day(self.root,self.publisher,transport,clock=lambda:AT+timedelta(days=1),
                monotonic=lambda:0,sleeper=lambda _:None,expected_day=AT.date().isoformat())
        self.assertEqual(transport.paths,[])
        self.assertEqual(prior,(self.root/'state.json').read_bytes())
    def test_bounded_invocation_continues_only_new_work(self):
        actual=job.run
        def small(*args,**kwargs):
            kwargs['max_requests']=min(kwargs['max_requests'],2)
            return actual(*args,**kwargs)
        with patch.object(job,'run',small):
            result,t=self.run_day([catalog('A'),body('A'),catalog('B'),body('B')])
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['invocations'],2)
        self.assertEqual(result['requests'],4)
class EntrypointTests(unittest.TestCase):
    def check_mode(self,mode):
        from contextlib import ExitStack,redirect_stdout
        from types import SimpleNamespace
        from io import StringIO
        with ExitStack() as stack:
            stack.enter_context(patch.object(job,'load_registry',return_value=object()))
            stack.enter_context(patch.object(job,'stores',return_value=object()))
            stack.enter_context(patch.object(job,'EquiblesTranscriptPublisher',return_value=object()))
            stack.enter_context(patch.object(job,'read_project_credential',return_value='offline-fixture'))
            stack.enter_context(patch('quant_data.market.collection_bindings.load_bindings',
                return_value={'equibles_transcripts':SimpleNamespace(mode=mode)}))
            current=stack.enter_context(patch.object(daily,'run_day',return_value={'outcome':'complete'}))
            legacy=stack.enter_context(patch.object(job,'run',return_value={'outcome':'complete'}))
            with redirect_stdout(StringIO()):self.assertEqual(job.main(()),0)
            self.assertEqual(current.call_count,1 if mode=='active' else 0)
            self.assertEqual(legacy.call_count,0 if mode=='active' else 1)
    def test_active_entrypoint_uses_daily_controller_without_live_access(self):
        self.check_mode('active')
    def test_prepared_entrypoint_preserves_legacy_path_without_live_access(self):
        self.check_mode('prepared')

if __name__=='__main__':
    unittest.main(verbosity=2)
