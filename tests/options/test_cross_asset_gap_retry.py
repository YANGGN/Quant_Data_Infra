"""Gap-only retries use temporary stores, fake providers, and blocked network."""
from datetime import timedelta
import json
import threading
import unittest
from unittest.mock import patch

from quant_data.options.cross_asset import reviewed_manifest
from quant_data.options.cross_asset_durable import WINDOW
from quant_data.options.cross_asset_gap_retry import (
    AUTHORIZATION, PARENT_FILES, RETRY_WORK, GapRetryJob, retry_authorization,
)
from quant_data.options.cross_asset_recovery import digest, request_identity
from quant_data.options.model import build_capture
from tests.options import test_cross_asset_durable as durable_fixture
from tests.options import test_cross_asset_job as fixture


class GapRetryTests(unittest.TestCase):
    factory = durable_fixture.DurableTests.factory
    journal = durable_fixture.DurableTests.journal
    save_policy = durable_fixture.DurableTests.save_policy
    recovery_factory = durable_fixture.DurableTests.recovery_factory
    save_interruption = durable_fixture.DurableTests.save_interruption

    def setUp(self):
        durable_fixture.DurableTests.setUp(self)
        result = durable_fixture.DurableTests.job(self).run(wait=True)
        self.assertEqual((result['completed_sessions'],len(result['gaps'])),(20,4))
        self.parent_bytes = {name:(self.work/name).read_bytes() for name in PARENT_FILES}
        self.retry_work = self.store.root/RETRY_WORK
        self.retry_work.mkdir()
        self.retry_policy = retry_authorization(self.store.root,fixture.ALLOWED.isoformat(),
            clock=lambda:fixture.ALLOWED)
        self.save_retry_policy()
        self.calls.clear()
        self.auth_before = self.authentications

    def save_retry_policy(self):
        (self.retry_work/AUTHORIZATION).write_text(json.dumps(self.retry_policy))

    def job(self,factory=None,clock=None):
        return GapRetryJob(self.store.root,transport_factory=factory or self.recovery_factory,
            clock=clock or (lambda:fixture.ALLOWED))

    def attempts(self):
        return [json.loads(x) for x in (self.retry_work/'attempts.jsonl').read_text().splitlines()]

    def test_only_four_gaps_retried_once_parent_unchanged_and_replay_is_noop(self):
        barrier=threading.Barrier(4)
        def factory(*args,**kwargs):
            transport=self.recovery_factory(*args,**kwargs);original=transport.request
            def request(method,**params):
                if method=='option_history_greeks_eod':barrier.wait(timeout=5)
                return original(method,**params)
            transport.request=request;return transport
        result=self.job(factory).run(wait=True)
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['completed_sessions'],24)
        self.assertEqual(result['recovered_gap_sessions'],4)
        self.assertEqual(result['data_requests'],29)
        self.assertEqual(result['max_inflight_requests'],4)
        self.assertEqual(len(self.calls),8)
        self.assertEqual({p['symbol'] for m,p in self.calls},{'GLD','SLV','IEF','EFA'})
        self.assertEqual(len({a['unit'] for a in self.attempts()}),8)
        self.assertEqual(result['response_bytes_accounted'],2500+4*1024**3)
        self.assertEqual({name:(self.work/name).read_bytes() for name in PARENT_FILES},self.parent_bytes)
        before=self.store.path.read_bytes();auth=self.authentications
        self.assertEqual(self.job().run()['state'],'completed')
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual((self.authentications,len(self.calls)),(auth,8))

    def test_unavailable_remains_gap_without_blocking_other_gaps(self):
        self.errors[('SLV','option_history_greeks_eod')]='UNAVAILABLE'
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],23)
        self.assertEqual([(g['symbol'],g['reason']) for g in result['gaps']],[('SLV','provider_unavailable')])
        before=len(self.calls)
        self.job().run(wait=True)
        self.assertEqual(len(self.calls),before)

    def test_quality_failure_is_not_relaxed(self):
        def build(*args,**kwargs):
            if kwargs['symbol']=='SLV':raise ValueError('fallback_reference_not_crosschecked')
            return build_capture(*args,**kwargs)
        with patch('quant_data.options.cross_asset.build_capture',side_effect=build):
            result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],23)
        self.assertEqual(result['gaps'][0]['reason'],'fallback_reference_not_crosschecked')
        self.assertEqual(len(self.calls),8)

    def test_empty_greeks_and_eod_are_not_published_or_retried(self):
        def factory(*args,**kwargs):
            transport=self.recovery_factory(*args,**kwargs);original=transport.request
            def request(method,**params):
                rows,receipt=original(method,**params)
                return ([] if params['symbol']=='SLV' else rows),receipt
            transport.request=request;return transport
        result=self.job(factory).run(wait=True)
        self.assertEqual(result['completed_sessions'],23)
        self.assertEqual(result['gaps'][0]['reason'],'eod_no_data')
        self.assertEqual([m for m,p in self.calls if p['symbol']=='SLV'],
            ['option_history_greeks_eod','option_history_eod'])

    def test_crash_keeps_reservation_and_never_reissues_that_identity(self):
        interrupted=[False]
        def factory(root,budget,**kwargs):
            transport=self.recovery_factory(root,budget,**kwargs);original=transport.request
            def request(method,**params):
                if params['symbol']=='GLD' and not interrupted[0]:
                    interrupted[0]=True;budget.reserve_request();budget.add_bytes(100)
                    self.calls.append((method,params));raise KeyboardInterrupt()
                return original(method,**params)
            transport.request=request;return transport
        with self.assertRaises(KeyboardInterrupt):self.job(factory).run(wait=True)
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],23)
        self.assertEqual(result['gaps'][0]['reason'],'interrupted_no_receipt')
        self.assertEqual(result['response_bytes_unmeasured_allowance'],4*1024**3+WINDOW)
        self.assertEqual(sum(p['symbol']=='GLD' for m,p in self.calls),1)
        self.assertEqual(len(self.attempts()),len({a['unit'] for a in self.attempts()}))

    def test_receipted_response_lost_before_stage_is_gap_without_replay(self):
        import os
        replace=os.replace
        failed=[False]
        def interrupted(source,target):
            if str(target).endswith('.json.gz') and not failed[0]:
                failed[0]=True;raise OSError('stage rename failed')
            return replace(source,target)
        with patch('quant_data.options.cross_asset_gap_retry.os.replace',side_effect=interrupted):
            with self.assertRaises(OSError):self.job().run(wait=True)
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],23)
        self.assertEqual(result['gaps'][0]['reason'],'response_not_staged')
        self.assertEqual(len(self.attempts()),len({a['unit'] for a in self.attempts()}))

    def test_scope_or_parent_evidence_change_fails_before_authentication(self):
        self.retry_policy['max_new_requests']+=1;self.save_retry_policy()
        with self.assertRaisesRegex(ValueError,'authorization_drift'):self.job().run()
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))
        self.retry_policy['max_new_requests']-=1;self.save_retry_policy()
        status=json.loads((self.work/'status.json').read_text());status['tampered']=True
        (self.work/'status.json').write_text(json.dumps(status))
        with self.assertRaisesRegex(ValueError,'authorization_drift'):self.job().run()
        self.assertEqual(self.authentications,self.auth_before)

    def test_unknown_authentication_cannot_admit_another_login(self):
        (self.retry_work/'authentication-attempts.jsonl').write_text(json.dumps({
            'attempt':self.retry_policy['base_authentications']+1,'epoch':'unknown'})+'\n')
        with self.assertRaisesRegex(RuntimeError,'authentication_failed_or_unknown'):self.job().run()
        self.assertEqual(self.authentications,self.auth_before)

    def test_missing_session_outside_original_gaps_is_rejected(self):
        parent_completed=self.job()._coverage(reviewed_manifest(self.store.root))
        parent_completed.remove(('TLT','2026-10-12'))
        with patch.object(GapRetryJob,'_coverage',return_value=parent_completed):
            with self.assertRaisesRegex(RuntimeError,'missing_outside_scope'):self.job().run(wait=True)
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))

    def test_byte_cap_and_original_deadline_remain_global(self):
        with patch('quant_data.options.cross_asset_gap_retry.MAX_BYTES',4*1024**3):
            with self.assertRaisesRegex(RuntimeError,'budget_exhausted'):self.job().run()
        with self.assertRaisesRegex(RuntimeError,'authorization_duration_exhausted'):
            self.job(clock=lambda:fixture.BLOCKED+timedelta(hours=72,seconds=1)).run()
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))

    def test_original_request_cap_limits_parallel_admissions(self):
        cap=self.retry_policy['base_budget']['data_requests']+1
        with patch('quant_data.options.cross_asset_gap_retry.MAX_REQUESTS',cap):
            with self.assertRaisesRegex(RuntimeError,'request_cap'):self.job().run(wait=True)
        self.assertEqual(len(self.calls),1)

    def test_duplicate_attempt_evidence_is_rejected_before_login(self):
        identity=request_identity('option_history_greeks_eod',{
            **self.identity['params'],'symbol':'GLD'})
        event={**identity,'unit':digest(identity),
            'gap_retry_authorization_sha256':digest(self.retry_policy)}
        (self.retry_work/'attempts.jsonl').write_text((json.dumps(event)+'\n')*2)
        with self.assertRaisesRegex(RuntimeError,'retry_attempt_scope'):self.job().run()
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))

    def test_outside_request_rejected_even_with_direct_cache_entrypoint(self):
        job=self.job();job._authorization(reviewed_manifest(self.store.root))
        with self.assertRaisesRegex(RuntimeError,'outside_scope'):
            job.cached_get(None,'option_history_greeks_eod',**self.identity['params'])
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))

    def test_market_window_and_live_priority_preserved(self):
        # The next morning is within the original deadline and after authorization.
        blocked=fixture.ALLOWED.replace(day=14,hour=15)
        self.assertEqual(self.job(clock=lambda:blocked).run()['state'],'paused')
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))
        from quant_data.options.monitor.admission import BackgroundBusy
        with patch('quant_data.options.cross_asset.ThetaAdmission.background',
                   side_effect=BackgroundBusy('live')):
            self.assertEqual(self.job().run()['state'],'paused')
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))

    def test_directory_sync_failure_prevents_authentication(self):
        with patch('quant_data.options.cross_asset_gap_retry.sync_admission_directories',
                   side_effect=OSError('directory sync failed')):
            with self.assertRaisesRegex(OSError,'directory sync failed'):self.job().run()
        self.assertEqual((self.authentications,self.calls),(self.auth_before,[]))
        self.assertEqual(self.job().run(wait=True)['state'],'completed')


if __name__=='__main__':unittest.main()
