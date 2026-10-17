"""Offline crash accounting and missing-only continuation for history."""
from datetime import timedelta
import hashlib
import json
import unittest
from unittest.mock import patch

from quant_data.options.cross_asset_durable import (
    DurableCrossAssetJob, DurableResponseBudget, INTERRUPTION_AMENDMENT,
    LEGACY_ALLOWANCE, MESSAGE_LIMIT, RESERVATIONS, WINDOW,
)
from quant_data.options.cross_asset_recovery import digest, request_identity
from quant_data.options.daily import sync_admission_directories
from tests.options import test_cross_asset_recovery as recovery_fixture
from tests.options import test_cross_asset_job as fixture


class ReservationTests(unittest.TestCase):
    def budget(self, **kwargs):
        self.events = []
        return DurableResponseBudget(journal=self.events.append, known_bytes=100,
            uncertain_bytes=LEGACY_ALLOWANCE, max_requests=10, max_bytes=8*LEGACY_ALLOWANCE,
            max_seconds=60, **kwargs)

    def test_reservation_precedes_bytes_and_receipt_releases_unused_capacity(self):
        budget = self.budget()
        with budget.request_context('request'):
            budget.reserve_request()
            self.assertEqual(self.events,[{'unit':'request','reserved_bytes':WINDOW}])
            budget.add_bytes(1000)
            self.assertEqual(budget.snapshot()['response_bytes_accounted'],100+LEGACY_ALLOWANCE+WINDOW)
            budget.settle({'response_bytes':1000})
        snapshot = budget.snapshot()
        self.assertEqual(snapshot['received_bytes'],1100)
        self.assertEqual(snapshot['response_bytes_accounted'],1100+LEGACY_ALLOWANCE)
        self.assertEqual(snapshot['response_bytes_inflight_reservation'],0)

    def test_refill_is_durable_before_next_maximum_message(self):
        budget=self.budget()
        with budget.request_context('request'):
            budget.reserve_request()
            budget.add_bytes(MESSAGE_LIMIT)
            budget.add_bytes(1)
            self.assertEqual(self.events[-1]['reserved_bytes'],2*WINDOW)
            budget.add_bytes(MESSAGE_LIMIT)
            self.assertGreaterEqual(budget.active['request'][0]-budget.active['request'][1],MESSAGE_LIMIT)

    def test_failed_reservation_does_not_admit_request(self):
        budget=self.budget()
        with budget.request_context('request'), patch.object(budget,'journal',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):budget.reserve_request()
        self.assertEqual(budget.requests,0)
        self.assertEqual(budget.active,{})

    def test_reserved_and_uncertain_bytes_share_original_cap(self):
        budget=self.budget()
        budget.max_bytes=LEGACY_ALLOWANCE+100+WINDOW-1
        with budget.request_context('request'):
            with self.assertRaisesRegex(RuntimeError,'response_byte_cap'):budget.reserve_request()
            budget.settle({'response_bytes':0})
        self.assertEqual(self.events,[])


class DurableTests(unittest.TestCase):
    factory = recovery_fixture.RecoveryTests.factory
    journal = recovery_fixture.RecoveryTests.journal
    save_policy = recovery_fixture.RecoveryTests.save_policy
    recovery_factory = recovery_fixture.RecoveryTests.recovery_factory

    def setUp(self):
        recovery_fixture.RecoveryTests.setUp(self)
        self.unknown=[]
        for symbol in ('GLD','SLV','IEF','EFA'):
            identity=request_identity('option_history_greeks_eod',{
                **self.identity['params'],'symbol':symbol})
            event={**identity,'unit':digest(identity),'started_at':fixture.BLOCKED.isoformat()}
            with (self.work/'attempts.jsonl').open('a') as stream:
                stream.write(json.dumps(event)+'\n')
            self.unknown.append({**identity,'unit':event['unit'],'attempt_sha256':digest(event)})
        self.interruption={
            'contract':'theta_cross_asset_interruption_amendment_v1',
            'authorization_sha256':self.policy['authorization_sha256'],
            'manifest_sha256':self.policy['manifest_sha256'],
            'unchanged_caps':self.policy['unchanged_caps'],
            'legacy_allowance_bytes_per_request':LEGACY_ALLOWANCE,
            'additional_retries':0,'interrupted_policy':'record_gap_and_continue',
            'authorized_at':fixture.BLOCKED.isoformat(),'requests':self.unknown,
            'prefixes':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                              'lines':len(raw.splitlines())}
                for name in ('attempts.jsonl','request-receipts.jsonl')
                for raw in [(self.work/name).read_bytes()]}}
        self.save_interruption()

    def save_interruption(self):
        (self.work/INTERRUPTION_AMENDMENT).write_text(json.dumps(self.interruption))

    def job(self, factory=None, clock=None):
        return DurableCrossAssetJob(self.store.root,
            transport_factory=factory or self.recovery_factory,
            clock=clock or (lambda:fixture.ALLOWED))

    def test_stage_sync_failure_stops_publication_and_restart_reuses_response(self):
        fixture.assert_stage_sync_failure_restarts(self, durable=True)

    def test_legacy_unknowns_are_gaps_without_replay_and_allowance_is_separate(self):
        result=self.job().run(wait=True)
        self.assertEqual(result['state'],'completed_with_gaps')
        self.assertEqual(result['completed_sessions'],20)
        self.assertEqual(len(result['gaps']),4)
        self.assertTrue(all(x['reason']=='interrupted_no_receipt' for x in result['gaps']))
        self.assertEqual(result['data_requests'],21)
        self.assertEqual(result['received_bytes'],1700)
        self.assertEqual(result['response_bytes_accounted'],1700+4*LEGACY_ALLOWANCE)
        self.assertEqual(result['response_bytes_unmeasured_allowance'],4*LEGACY_ALLOWANCE)
        self.assertFalse(any(p['symbol'] in {'GLD','SLV','IEF','EFA'} for m,p in self.calls))
        before=self.store.path.read_bytes(); count=len(self.calls)
        self.job().run(wait=True)
        self.assertEqual(len(self.calls),count)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_new_interruption_charges_reservation_and_never_repeats_request(self):
        interrupted=[False]
        def factory(root,budget,**kwargs):
            transport=self.recovery_factory(root,budget,**kwargs)
            original=transport.request
            def request(method,**params):
                if params['symbol']=='USO' and method=='option_history_greeks_eod' and not interrupted[0]:
                    interrupted[0]=True
                    budget.reserve_request()
                    budget.add_bytes(MESSAGE_LIMIT)
                    budget.add_bytes(1)
                    self.calls.append((method,params))
                    raise KeyboardInterrupt()
                return original(method,**params)
            transport.request=request
            return transport
        with self.assertRaises(KeyboardInterrupt):self.job(factory).run(wait=True)
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],19)
        self.assertEqual(result['response_bytes_unmeasured_allowance'],4*LEGACY_ALLOWANCE+2*WINDOW)
        self.assertEqual(sum(m=='option_history_greeks_eod' and p['symbol']=='USO' for m,p in self.calls),1)

    def test_attempt_before_reservation_has_zero_new_byte_charge(self):
        identity=request_identity('option_history_greeks_eod',{**self.identity['params'],'symbol':'USO'})
        with (self.work/'attempts.jsonl').open('a') as stream:
            stream.write(json.dumps({**identity,'unit':digest(identity),'started_at':fixture.ALLOWED.isoformat(),
                'response_reservation_contract':'v1','interruption_amendment_sha256':digest(self.interruption)})+'\n')
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],19)
        self.assertEqual(result['response_bytes_unmeasured_allowance'],4*LEGACY_ALLOWANCE)
        self.assertFalse(any(p['symbol']=='USO' for m,p in self.calls))

    def test_unknown_attempt_from_unmarked_runner_cannot_borrow_zero_charge(self):
        identity=request_identity('option_history_greeks_eod',{**self.identity['params'],'symbol':'USO'})
        with (self.work/'attempts.jsonl').open('a') as stream:
            stream.write(json.dumps({**identity,'unit':digest(identity),'started_at':fixture.ALLOWED.isoformat()})+'\n')
        with self.assertRaisesRegex(RuntimeError,'unreconciled_attempts'):self.job().run()
        self.assertEqual(self.authentications,0)

    def test_reservation_directory_sync_failure_prevents_authentication_and_rpc(self):
        target='quant_data.options.cross_asset_durable.sync_admission_directories'
        with patch(target,side_effect=OSError('directory sync failed')):
            with self.assertRaisesRegex(OSError,'directory sync failed'):
                self.job().run(wait=True)
        self.assertTrue((self.work/RESERVATIONS).exists())
        self.assertEqual((self.authentications,self.calls),(0,[]))
        synced=[]
        def sync(work,root):
            if work == self.work:
                self.assertTrue((work/RESERVATIONS).exists())
                self.assertEqual((self.authentications,self.calls),(0,[]))
                synced.append(True)
            sync_admission_directories(work,root)
        def factory(*args,**kwargs):
            self.assertEqual(synced,[True])
            return self.recovery_factory(*args,**kwargs)
        with patch(target,side_effect=sync):
            result=self.job(factory).run(wait=True)
        self.assertEqual(result['state'],'completed_with_gaps')
        self.assertGreater(len(self.calls),0)

    def test_known_unavailable_still_continues_with_other_units(self):
        self.errors[('USO','option_history_open_interest')]='UNAVAILABLE'
        result=self.job().run(wait=True)
        self.assertEqual(result['completed_sessions'],19)
        self.assertIn('provider_unavailable',{x['reason'] for x in result['gaps']})
        self.assertEqual(result['response_bytes_unmeasured_allowance'],4*LEGACY_ALLOWANCE)

    def test_prefix_change_and_extra_legacy_unknown_are_rejected(self):
        self.interruption['requests'][0]['attempt_sha256']='0'*64
        self.save_interruption()
        with self.assertRaisesRegex(RuntimeError,'interruption_evidence'):self.job().run()
        self.assertEqual(self.authentications,0)
        with (self.work/'attempts.jsonl').open('r+') as stream:
            original=stream.read();stream.seek(0);stream.write(original.replace('TLT','LQD',1))
        with self.assertRaisesRegex(ValueError,'interruption_prefix'):self.job().run()
        self.assertEqual(self.authentications,0)

    def test_invalid_reservation_cannot_hide_byte_use(self):
        identity=request_identity('option_history_greeks_eod',{**self.identity['params'],'symbol':'USO'})
        unit=digest(identity)
        with (self.work/'attempts.jsonl').open('a') as stream:
            stream.write(json.dumps({**identity,'unit':unit,'started_at':fixture.ALLOWED.isoformat()})+'\n')
        (self.work/RESERVATIONS).write_text(json.dumps({'unit':unit,'reserved_bytes':1})+'\n')
        with self.assertRaisesRegex(RuntimeError,'reservation_evidence'):self.job().run()
        self.assertEqual(self.authentications,0)

    def test_deadline_and_protected_window_still_apply(self):
        self.assertEqual(self.job(clock=lambda:fixture.BLOCKED).run()['state'],'paused')
        with self.assertRaisesRegex(RuntimeError,'authorization_duration_exhausted'):
            self.job(clock=lambda:fixture.BLOCKED+timedelta(hours=72,seconds=1)).run()
        self.assertEqual((self.authentications,self.calls),(0,[]))


if __name__=='__main__':unittest.main()
