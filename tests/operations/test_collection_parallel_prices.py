import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from types import SimpleNamespace
from quant_data.operations.collection_parallel_prices import acquire_batch
from quant_data.operations.collection_provider_policy import FmpAllowance,FmpAccountGate
from quant_data.operations.collection_queue import atomic,QueueResponse
from quant_data.operations.collection_plan import AcquisitionUnit
from quant_data.operations.equibles_transcript_backfill import private_directory
from quant_data.errors import ConflictError

AT=datetime(2026,9,10,14,tzinfo=timezone.utc)
class ParallelPriceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.elapsed=0;self.starts=[];self.policy=FmpAllowance(100,10,120,AT.isoformat(),5,'fixture/rate.json')
        self.gate=FmpAccountGate(self.root/'account',self.policy,clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        private_directory(self.gate.root);private_directory(self.gate.root/'attempts')
        atomic(self.gate.root/'allowance.json',{'contract':'quant_data.fmp_account_allowance.v1','policy_sha256':self.policy.identity,
            'usage':{'2026-09-10':5},'last_request_at':None,'pending':None,'stopped':None})
    def clock(self):return AT+timedelta(seconds=self.elapsed)
    def sleep(self,n):self.elapsed+=n
    def units(self,n=20):
        return tuple(AcquisitionUnit('daily_prices','fmp','historical-price-eod/full',f'T{i:03}',
            (('from','2022-01-01'),('symbol',f'T{i:03}'),('to','2026-09-09')),'historical_backfill','fixture',(f'T{i:03}',),'1'*64,max_response_bytes=1024,timeout_seconds=45) for i in range(n))
    def factory(self,status=200,crash=False):
        test=self
        class Ticket:
            def __init__(self,u,credential,deadline):
                test.starts.append(test.elapsed);self.ready_at=test.elapsed+0.8;self.status=status
                if crash:raise RuntimeError('Synthetic constructor failure')
            def ready(self):return test.elapsed>=self.ready_at
            def result(self):return QueueResponse(self.status,b'[]',test.clock().isoformat())
            def close(self):pass
        return Ticket
    def run_batch(self,**kw):
        return acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=100000,ticket_factory=self.factory(),clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep,**kw)
    def state(self):return json.loads((self.gate.root/'allowance.json').read_bytes())
    def test_overlapping_requests_are_paced_and_share_legacy_quota(self):
        result=self.run_batch();self.assertEqual(result['outcome'],'complete');self.assertEqual(result['requests'],20)
        self.assertGreater(result['peak_inflight'],1);self.assertLessEqual(result['peak_inflight'],8)
        self.assertTrue(all(b-a>=0.12-1e-9 for a,b in zip(self.starts,self.starts[1:])))
        self.assertLess(self.elapsed,5);self.assertEqual(self.state()['usage']['2026-09-10'],25)
        self.assertIsNone(self.state()['pending'])
        self.gate.invoke(lambda _:SimpleNamespace(status=200),request_identity='f'*64,priority='maintenance',deadline=100)
        self.assertEqual(self.state()['usage']['2026-09-10'],26)
        with self.assertRaises(ConflictError):self.run_batch()
        self.assertEqual(len(self.starts),20)
    def test_systemic_failure_stops_new_dispatch_and_drains_inflight(self):
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=100000,ticket_factory=self.factory(status=429),clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertLess(result['requests'],20);self.assertEqual(result['retained_responses'],result['requests'])
        self.assertEqual(self.state()['stopped']['status'],429);self.assertIsNone(self.state()['pending'])
        with self.assertRaises(ConflictError):self.gate.invoke(lambda _:self.fail('No HTTP'),request_identity='f'*64,priority='maintenance',deadline=100)
    def test_unknown_request_keeps_charge_and_blocks_legacy_without_retry(self):
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=100000,ticket_factory=self.factory(crash=True),clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['requests'],1);self.assertEqual(self.state()['usage']['2026-09-10'],6)
        self.assertIsNotNone(self.state()['pending'])
        with self.assertRaises(ConflictError):self.gate.invoke(lambda _:self.fail('No HTTP'),request_identity='f'*64,priority='maintenance',deadline=100)
    def test_maintenance_reserve_and_byte_reservations_limit_parallel_starts(self):
        s=self.state();s['usage']['2026-09-10']=89;atomic(self.gate.root/'allowance.json',s,replace=True)
        result=self.run_batch();self.assertEqual(result['requests'],1);self.assertEqual(result['outcome'],'daily_budget')
        self.assertEqual(self.state()['usage']['2026-09-10'],90)
    def test_outstanding_worst_case_bytes_are_reserved(self):
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=1024,ticket_factory=self.factory(),clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['requests'],1);self.assertEqual(result['peak_inflight'],1);self.assertEqual(result['outcome'],'byte_budget')

    def test_backward_clock_delays_dispatch_while_completed_responses_are_drained(self):
        def backwards_clock():
            return self.clock()-timedelta(seconds=60) if self.elapsed>=0.25 else self.clock()
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=100000,ticket_factory=self.factory(),clock=backwards_clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['outcome'],'batch_boundary');self.assertGreater(result['requests'],1)
        self.assertEqual(result['requests'],result['retained_responses']);self.assertEqual(result['uncertain_requests'],0)
        self.assertIsNone(self.state()['pending']);self.assertEqual(len(self.starts),result['requests'])

    def test_admission_closes_then_late_requests_receive_full_drain_time(self):
        test=self
        class SlowTicket:
            def __init__(self,u,credential,deadline):
                test.starts.append(test.elapsed)
                self.ready_at=9 if len(test.starts)<=8 else test.elapsed+15
                test.assertGreaterEqual(deadline-test.elapsed,45)
            def ready(self):return test.elapsed>=self.ready_at
            def result(self):return QueueResponse(200,b'[]',test.clock().isoformat())
            def close(self):pass
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=100,
            max_total_bytes=100000,workers=8,ticket_factory=SlowTicket,clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['outcome'],'batch_boundary');self.assertEqual(result['requests'],16)
        self.assertEqual(result['retained_responses'],16);self.assertEqual(result['uncertain_requests'],0)
        self.assertTrue(all(at<10 for at in self.starts));self.assertGreater(self.elapsed,15)
        self.assertIsNone(self.state()['pending']);self.assertEqual(result['unattempted'],4)

    def test_insufficient_original_time_does_not_reserve_or_dispatch(self):
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(),credential='synthetic',deadline=40,
            max_total_bytes=100000,ticket_factory=self.factory(),clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['outcome'],'invocation_budget');self.assertEqual(result['requests'],0)
        self.assertEqual(self.starts,[]);self.assertEqual(self.state()['usage']['2026-09-10'],5)

    def test_larger_pool_hides_latency_without_exceeding_shared_rate(self):
        test=self
        class LatentTicket:
            def __init__(self,u,credential,deadline):test.starts.append(test.elapsed);self.ready_at=test.elapsed+2.5
            def ready(self):return test.elapsed>=self.ready_at
            def result(self):return QueueResponse(200,b'[]',test.clock().isoformat())
            def close(self):pass
        result=acquire_batch(root=self.root/'batch',gate=self.gate,units=self.units(40),credential='synthetic',deadline=100,
            max_total_bytes=100000,ticket_factory=LatentTicket,clock=self.clock,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['requests'],40)
        self.assertGreater(result['peak_inflight'],8);self.assertLessEqual(result['peak_inflight'],24)
        self.assertTrue(all(b-a>=0.12-1e-9 for a,b in zip(self.starts,self.starts[1:])))
        self.assertEqual(result['requests'],result['retained_responses']);self.assertIsNone(self.state()['pending'])

    def test_durable_reservation_delay_does_not_truncate_an_admitted_request(self):
        from unittest.mock import patch
        from quant_data.operations import collection_parallel_prices as implementation
        real_atomic=implementation.atomic
        def slow_reservation(path,*args,**kwargs):
            result=real_atomic(path,*args,**kwargs)
            if path.parent==self.root/'batch/attempts':self.elapsed+=10.25
            return result
        with patch.object(implementation,'atomic',side_effect=slow_reservation):
            result=self.run_batch()
        self.assertEqual(result['outcome'],'batch_boundary');self.assertEqual(result['requests'],1)
        self.assertEqual(result['retained_responses'],1);self.assertEqual(result['uncertain_requests'],0)
        self.assertEqual(len(self.starts),1);self.assertIsNone(self.state()['pending'])
