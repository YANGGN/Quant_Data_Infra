"""Offline concurrency, restart and live-priority checks for the finite history."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from quant_data.options.cross_asset import CrossAssetJob, _request_window
from quant_data.options.cross_asset_parallel import AMENDMENT, ParallelCrossAssetJob
from quant_data.options.monitor.admission import BackgroundBusy
from quant_data.options.transport import ProviderFailure
from tests.options import test_cross_asset_job as fixture


class ParallelTests(unittest.TestCase):
    factory = fixture.CrossAssetJobTests.factory

    def setUp(self):
        fixture.CrossAssetJobTests.setUp(self)
        original = CrossAssetJob(self.store.root, transport_factory=self.factory,
                                 clock=lambda: fixture.BLOCKED)
        self.assertEqual(original.run()['state'], 'paused')
        raw = (self.work / 'authorization.json').read_bytes()
        self.original_authorization = raw
        self.original_manifest = (self.work / 'manifest.json').read_bytes()
        base = json.loads(raw)
        self.amendment = {
            'contract': 'theta_cross_asset_parallel_amendment_v1',
            'authorization_sha256': hashlib.sha256(raw).hexdigest(),
            'manifest_sha256': base['manifest_sha256'], 'unchanged_caps': base['caps'],
            'workers': 4, 'after_hours_only': True,
            'authorized_at': fixture.BLOCKED.isoformat(),
        }
        (self.work / AMENDMENT).write_text(json.dumps(self.amendment))

    def job(self, factory=None, clock=None):
        return ParallelCrossAssetJob(self.store.root,
            transport_factory=factory or self.factory, clock=clock or (lambda: fixture.ALLOWED))

    def journal(self):
        return [json.loads(line) for line in (self.work / 'attempts.jsonl').read_text().splitlines()]

    def test_stage_sync_failure_stops_publication_and_restart_reuses_response(self):
        fixture.assert_stage_sync_failure_restarts(self)

    def test_four_requests_share_one_login_and_one_publisher_without_duplicates(self):
        barrier = threading.Barrier(4)
        guard = threading.Lock()
        active = peak = 0
        def factory(*args, **kwargs):
            transport = self.factory(*args, **kwargs)
            request = transport.request
            def call(method, **params):
                nonlocal active, peak
                with guard:
                    active += 1
                    peak = max(peak, active)
                try:
                    if method == 'option_history_greeks_eod':
                        barrier.wait(timeout=5)
                    return request(method, **params)
                finally:
                    with guard:
                        active -= 1
            transport.request = call
            return transport
        job = self.job(factory)
        publisher = threading.get_ident()
        publish = job.store.publish
        def guarded_publish(*args, **kwargs):
            self.assertEqual(threading.get_ident(), publisher)
            return publish(*args, **kwargs)
        with patch.object(job.store, 'publish', side_effect=guarded_publish):
            result = job.run(wait=True)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['completed_sessions'], 24)
        self.assertEqual(peak, 4)
        self.assertEqual(result['max_inflight_requests'], 4)
        self.assertEqual(result['download_workers'], 4)
        self.assertEqual(self.authentications, 1)
        self.assertEqual(len(self.calls), 24)
        self.assertEqual(len({e['unit'] for e in self.journal()}), 24)
        self.assertEqual((self.work / 'authorization.json').read_bytes(), self.original_authorization)
        self.assertEqual((self.work / 'manifest.json').read_bytes(), self.original_manifest)
        before = self.store.path.read_bytes()
        self.assertEqual(self.job().run()['state'], 'completed')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.authentications, 1)

    def test_graceful_stop_drains_and_resume_adopts_staged_responses(self):
        barrier = threading.Barrier(4)
        holder = {}
        active = 0
        guard = threading.Lock()
        def factory(*args, **kwargs):
            transport = self.factory(*args, **kwargs)
            request = transport.request
            def call(method, **params):
                nonlocal active
                with guard:
                    active += 1
                try:
                    barrier.wait(timeout=5)
                    result = request(method, **params)
                    holder['job']._stop.set()
                    return result
                finally:
                    with guard:
                        active -= 1
            transport.request = call
            transport.close = lambda: self.assertEqual(active, 0)
            return transport
        job = holder['job'] = self.job(factory)
        self.assertEqual(job.run(wait=True)['state'], 'paused')
        prior = {e['unit'] for e in self.journal()}
        self.assertEqual(len(prior), 4)
        self.assertEqual(active, 0)
        result = self.job().run(wait=True)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(self.calls), 24)
        self.assertEqual(len(self.journal()), 24)
        self.assertEqual(len({e['unit'] for e in self.journal()}), 24)
        self.assertEqual(result['data_requests'], 24)
        self.assertEqual((self.work / 'authorization.json').read_bytes(), self.original_authorization)

    def test_failed_request_drains_and_cannot_retry_after_restart(self):
        self.fail_request = True
        with self.assertRaises(ProviderFailure):
            self.job().run(wait=True)
        calls = len(self.calls)
        self.assertLessEqual(calls, 4)
        self.fail_request = False
        with self.assertRaisesRegex(RuntimeError, 'unreconciled_attempts'):
            self.job().run(wait=True)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.authentications, 1)

    def test_failed_shared_login_does_not_fan_out_or_retry(self):
        self.fail_authentication = True
        with self.assertRaisesRegex(RuntimeError, 'fake_authentication_failure'):
            self.job().run(wait=True)
        self.assertEqual(self.authentications, 1)
        self.assertEqual(self.calls, [])
        self.fail_authentication = False
        with self.assertRaisesRegex(RuntimeError, 'authentication_failed_or_unknown'):
            self.job().run()
        self.assertEqual(self.authentications, 1)

    def test_stop_racing_cached_request_admission_is_a_pause(self):
        job = self.job()
        def stopped(*args, **kwargs):
            job._stop.set()
            raise RuntimeError('parallel_download_stopped')
        with patch.object(job, 'cached_get', side_effect=stopped):
            self.assertEqual(job.run(wait=True)['state'], 'paused')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.journal(), [])

    def test_request_budget_is_global_across_four_workers(self):
        job = self.job()
        original = job._budget_from_journal
        def limited(*args):
            budget = original(*args)
            budget.max_requests = 3
            return budget
        with patch.object(job, '_budget_from_journal', side_effect=limited):
            with self.assertRaisesRegex(RuntimeError, 'request_cap'):
                job.run(wait=True)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(job._budget.requests, 3)
        self.assertEqual(len({(m, json.dumps({k:str(v) for k,v in p.items()},sort_keys=True))
                              for m,p in self.calls}), 3)

    def test_market_and_scheduled_windows_admit_nothing(self):
        for now in (fixture.BLOCKED, fixture.ALLOWED.replace(hour=18)):
            with self.subTest(now=now):
                result = self.job(clock=lambda: now).run()
                self.assertEqual(result['state'], 'paused')
                self.assertEqual(self.calls, [])
                self.assertEqual(self.authentications, 0)

    def test_current_schedule_guards_include_three_minute_rpc_headroom(self):
        for stamp in ('2026-10-14T07:07:00-04:00', '2026-10-14T07:39:59-04:00',
                      '2026-10-14T08:52:00-04:00', '2026-10-14T16:59:59-04:00',
                      '2026-10-18T02:52:00-04:00', '2026-10-18T04:04:59-04:00'):
            self.assertFalse(_request_window(datetime.fromisoformat(stamp)), stamp)
        for stamp in ('2026-10-14T07:06:59-04:00', '2026-10-14T07:40:00-04:00',
                      '2026-10-14T17:00:00-04:00', '2026-10-14T19:15:00-04:00',
                      '2026-10-18T04:05:00-04:00'):
            self.assertTrue(_request_window(datetime.fromisoformat(stamp)), stamp)

    def test_live_intent_blocks_admission_before_authentication(self):
        job = self.job()
        @contextmanager
        def busy(now):
            raise BackgroundBusy('live_collection')
            yield
        with patch.object(job.admission, 'background', side_effect=busy):
            self.assertEqual(job.run()['state'], 'paused')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authentications, 0)
        self.assertEqual(self.journal(), [])

    def test_live_generation_change_renews_only_after_old_calls_drain(self):
        now = [fixture.ALLOWED]
        barrier = threading.Barrier(4)
        epoch = ['a' * 32]
        active = 0
        guard = threading.Lock()
        closed = []
        def factory(*args, **kwargs):
            transport = self.factory(*args, **kwargs)
            ordinal = self.authentications
            request = transport.request
            def call(method, **params):
                nonlocal active
                with guard:
                    active += 1
                try:
                    if ordinal == 1:
                        barrier.wait(timeout=5)
                    return request(method, **params)
                finally:
                    with guard:
                        active -= 1
                        if ordinal == 1:
                            epoch[0] = 'b' * 32
            def close():
                self.assertEqual(active, 0)
                closed.append(ordinal)
            transport.request, transport.close = call, close
            return transport
        job = self.job(factory, clock=lambda: now[0])
        @contextmanager
        def admitted(at):
            yield epoch[0]
        with patch.object(job.admission, 'background', side_effect=admitted):
            result = job.run(wait=True)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.authentications, 2)
        self.assertEqual(closed, [1, 2])
        self.assertEqual(len(self.calls), 24)

    def test_caps_and_original_deadline_cannot_be_amended(self):
        changed = dict(self.amendment)
        changed['unchanged_caps'] = {**changed['unchanged_caps'], 'max_requests': 100001}
        (self.work / AMENDMENT).write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'parallel_amendment'):
            self.job().run()
        (self.work / AMENDMENT).write_text(json.dumps(self.amendment))
        with self.assertRaisesRegex(RuntimeError, 'authorization_duration_exhausted'):
            self.job(clock=lambda: fixture.BLOCKED + timedelta(hours=72, seconds=1)).run()
        self.assertEqual(self.authentications, 0)
        self.assertEqual(self.calls, [])

    def test_coordinator_rechecks_store_cap_before_publishing_downloaded_capture(self):
        job = self.job()
        main = threading.get_ident()
        def storage_check():
            if threading.get_ident() == main:
                raise RuntimeError('options_database_cap')
        with patch.object(job, '_storage_check', side_effect=storage_check), \
             patch.object(job.store, 'publish', side_effect=AssertionError('must not publish')):
            with self.assertRaisesRegex(RuntimeError, 'options_database_cap'):
                job.run(wait=True)
        self.assertEqual(job.status['completed_sessions'], 0)
        self.assertTrue(any(job.stage.glob('*.json.gz')))


if __name__ == '__main__':
    unittest.main()
