"""Offline checks for the authorized single retry and nonblocking unavailability."""
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
import json
import threading
import unittest
from unittest.mock import patch

from quant_data.options.cross_asset_recovery import (
    RECOVERY_AMENDMENT, RecoveringCrossAssetJob, digest, request_identity,
)
from quant_data.options.monitor.admission import BackgroundBusy
from quant_data.options.transport import ProviderFailure
from tests.options import test_cross_asset_job as fixture
from tests.options.test_cross_asset_parallel import ParallelTests
from tests.options.test_theta_daily import rows_for


class RecoveryTests(unittest.TestCase):
    factory = fixture.CrossAssetJobTests.factory
    journal = ParallelTests.journal

    def setUp(self):
        ParallelTests.setUp(self)
        self.errors = {}
        self.request_guard = threading.Lock()
        self.identity = request_identity('option_history_greeks_eod', {
            'symbol': 'TLT', 'expiration': '*', 'start_date': '2026-10-12',
            'end_date': '2026-10-12', 'version': '1', 'underlyer_use_nbbo': True})
        self.unit = digest(self.identity)
        self.failed = {**self.identity, 'outcome': 'UNAVAILABLE', 'response_bytes': 100,
                       'captured_at': fixture.BLOCKED.isoformat()}
        self.first_attempt = {**self.identity, 'unit': self.unit,
                              'started_at': fixture.BLOCKED.isoformat()}
        (self.work / 'attempts.jsonl').write_text(json.dumps(self.first_attempt)+'\n')
        (self.work / 'request-receipts.jsonl').write_text(json.dumps(self.failed)+'\n')
        self.policy = {
            'contract': 'theta_cross_asset_unavailable_amendment_v1',
            'authorization_sha256': self.amendment['authorization_sha256'],
            'manifest_sha256': self.amendment['manifest_sha256'],
            'unchanged_caps': self.amendment['unchanged_caps'],
            'continue_codes': ['UNAVAILABLE'], 'authorized_at': fixture.BLOCKED.isoformat(),
            'retry': {**self.identity, 'unit': self.unit,
                      'previous_receipt_sha256': digest(self.failed), 'additional_attempts': 1}}
        self.save_policy()

    def save_policy(self):
        (self.work / RECOVERY_AMENDMENT).write_text(json.dumps(self.policy))

    def recovery_factory(self, root, budget, *, receipt_callback, symbols):
        self.authentications += 1
        owner = self
        class Fake:
            subscription_code = 2
            def request(self, method, **params):
                budget.reserve_request()
                budget.add_bytes(100)
                with owner.request_guard:
                    owner.calls.append((method, params))
                session = str(params.get('date', params.get('start_date')))
                eod, oi = rows_for(params['symbol'], session)
                rows = oi if method == 'option_history_open_interest' else eod
                receipt = fixture.batch(params['symbol'], session, method, rows)['receipt']
                receipt['params'] = request_identity(method, params)['params']
                code = owner.errors.get((params['symbol'], method))
                if code:
                    receipt['outcome'] = code
                receipt_callback(receipt, budget)
                if code:
                    raise ProviderFailure(code, receipt)
                return rows, receipt
            def close(self):
                pass
        return Fake()

    def job(self, clock=None):
        return RecoveringCrossAssetJob(self.store.root,
            transport_factory=self.recovery_factory, clock=clock or (lambda: fixture.ALLOWED))

    def assert_retry_limit(self):
        counts = Counter(e['unit'] for e in self.journal())
        self.assertEqual(counts[self.unit], 2)
        self.assertTrue(all(n == 1 for unit, n in counts.items() if unit != self.unit))
        self.assertEqual((self.work/'authorization.json').read_bytes(), self.original_authorization)
        self.assertEqual((self.work/'manifest.json').read_bytes(), self.original_manifest)

    def test_exact_retry_succeeds_and_counts_both_attempts(self):
        result = self.job().run(wait=True)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['completed_sessions'], 24)
        self.assertEqual(result['data_requests'], 25)
        self.assertEqual(result['received_bytes'], 2500)
        self.assertTrue(result['explicit_retry_consumed'])
        self.assertEqual(len(self.calls), 24)
        self.assert_retry_limit()
        before = self.store.path.read_bytes()
        self.job().run(wait=True)
        self.assertEqual(len(self.calls), 24)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_retry_unavailable_is_gap_and_other_symbols_continue(self):
        self.errors[('TLT', 'option_history_greeks_eod')] = 'UNAVAILABLE'
        result = self.job().run(wait=True)
        self.assertEqual(result['state'], 'completed_with_gaps')
        self.assertEqual(result['completed_sessions'], 23)
        self.assertEqual([(e['symbol'],e['session'],e['reason']) for e in result['gaps']],
                         [('TLT','2026-10-12','provider_unavailable')])
        self.assertEqual(self.authentications, 1)
        self.assert_retry_limit()
        count = len(self.calls)
        self.job().run(wait=True)
        self.assertEqual(len(self.calls), count)

    def test_new_oi_unavailable_retains_partial_data_and_continues(self):
        self.errors[('SLV', 'option_history_open_interest')] = 'UNAVAILABLE'
        result = self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'], 23)
        self.assertEqual(result['gaps'][0]['symbol'], 'SLV')
        self.assertTrue(any((self.work/'stage').glob('*.json.gz')))
        self.assert_retry_limit()

    def test_crash_before_gap_record_recovers_without_refetching_failure(self):
        self.errors[('SLV', 'option_history_open_interest')] = 'UNAVAILABLE'
        job = self.job()
        with patch.object(job, '_record_gap', side_effect=RuntimeError('coordinator_crash')):
            with self.assertRaisesRegex(RuntimeError, 'coordinator_crash'):
                job.run(wait=True)
        self.assertEqual(self.job().run(wait=True)['state'], 'completed_with_gaps')
        self.assert_retry_limit()
        self.assertEqual(sum(m == 'option_history_open_interest' and p['symbol'] == 'SLV'
                             for m,p in self.calls), 1)

    def test_successful_retry_staged_before_stop_is_reused(self):
        job = self.job()
        main = threading.get_ident()
        def storage():
            if threading.get_ident() == main:
                raise RuntimeError('options_database_cap')
        with patch.object(job, '_storage_check', side_effect=storage):
            with self.assertRaisesRegex(RuntimeError, 'options_database_cap'):
                job.run(wait=True)
        self.assertEqual(self.job().run(wait=True)['state'], 'completed')
        self.assert_retry_limit()
        self.assertEqual(len(self.calls), 24)

    def test_other_provider_error_still_stops_and_cannot_replay(self):
        self.errors[('GLD', 'option_history_greeks_eod')] = 'PERMISSION_DENIED'
        with self.assertRaises(ProviderFailure):
            self.job().run(wait=True)
        count, auth = len(self.calls), self.authentications
        with self.assertRaisesRegex(RuntimeError, 'unreconciled_attempts'):
            self.job().run(wait=True)
        self.assertEqual((len(self.calls),self.authentications), (count,auth))

    def test_unknown_attempt_stops_before_login(self):
        with (self.work/'attempts.jsonl').open('a') as stream:
            stream.write(json.dumps({**self.first_attempt, 'retry_of':digest(self.failed)})+'\n')
        with self.assertRaisesRegex(RuntimeError, 'unreconciled_attempts'):
            self.job().run(wait=True)
        self.assertEqual((self.authentications,self.calls), (0,[]))

    def test_third_attempt_is_rejected(self):
        with (self.work/'attempts.jsonl').open('a') as stream:
            for _ in range(2):
                stream.write(json.dumps({**self.first_attempt, 'retry_of':digest(self.failed)})+'\n')
        with (self.work/'request-receipts.jsonl').open('a') as stream:
            stream.write((json.dumps(self.failed)+'\n')*2)
        with self.assertRaisesRegex(RuntimeError, 'unreconciled_attempts'):
            self.job().run(wait=True)
        self.assertEqual(self.authentications, 0)

    def test_retry_is_not_an_extra_budget_allocation(self):
        job = self.job()
        original = job._budget_from_journal
        def limited(*args):
            budget = original(*args)
            budget.max_requests = 2
            return budget
        with patch.object(job, '_budget_from_journal', side_effect=limited):
            with self.assertRaisesRegex(RuntimeError, 'request_cap'):
                job.run(wait=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(job._budget.requests, 2)

    def test_wrong_receipt_or_expanded_exception_fails_before_login(self):
        self.policy['retry']['previous_receipt_sha256'] = '0'*64
        self.save_policy()
        with self.assertRaisesRegex(RuntimeError, 'retry_evidence'):
            self.job().run(wait=True)
        self.policy['retry']['additional_attempts'] = 2
        self.save_policy()
        with self.assertRaisesRegex(ValueError, 'retry_scope'):
            self.job().run(wait=True)
        self.assertEqual(self.authentications, 0)

    def test_live_priority_and_market_window_still_admit_nothing(self):
        job = self.job()
        @contextmanager
        def busy(now):
            raise BackgroundBusy('live_collection')
            yield
        with patch.object(job.admission, 'background', side_effect=busy):
            self.assertEqual(job.run()['state'], 'paused')
        self.assertEqual(self.job(clock=lambda: fixture.BLOCKED).run()['state'], 'paused')
        self.assertEqual((self.authentications,self.calls), (0,[]))

    def test_original_deadline_is_not_restarted(self):
        with self.assertRaisesRegex(RuntimeError, 'authorization_duration_exhausted'):
            self.job(clock=lambda:fixture.BLOCKED+timedelta(hours=72,seconds=1)).run()
        self.assertEqual((self.authentications,self.calls), (0,[]))


if __name__ == '__main__':
    unittest.main()
