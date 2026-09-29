import unittest,tempfile,json,copy,time
from pathlib import Path
from datetime import datetime,timedelta
from tests.operations import test_collection_prices as fixtures
from tests.operations import test_collection_parallel_prices as parallel
from quant_data.operations.selected_price_pipeline import acquire_history,publish_history
from quant_data.operations.collection_price_windows import PinnedHistoryPlanner
from quant_data.operations.selected_price_backfill import HistoryTarget,initial_state
from quant_data.operations.collection_parallel_prices import acquire_batch
from quant_data.operations.collection_queue import QueueResponse
from quant_data.fingerprint import mutation_fingerprint

class PricePipelineTests(unittest.TestCase):
    def run_case(self,invalid=None,clean_boundary=False,byte_reserve=0):
        f=fixtures.SelectedPriceTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        a=parallel.ParallelPriceTests('runTest');a.setUp();self.addCleanup(a.doCleanups)
        root=a.root/'pipeline';row=json.loads(f.body)[0];calls=[]
        baseline={'states':{s.provider_symbol:initial_state(HistoryTarget(s.provider_symbol,'2026-01-01'),'2026-09-08') for s in f.scope.eligible},'requests':0,'received_bytes':0,'unretained_byte_reserve':byte_reserve}
        class Ticket:
            def __init__(self,u,credential,deadline):self.unit=u;calls.append(u.request_id)
            def ready(self):return True
            def result(self):
                body=json.dumps([{**row,'symbol':self.unit.subject}]).encode() if dict(self.unit.parameters)['to']=='2026-09-08' else b'[]'
                if invalid=='payload' and self.unit.subject=='AAPL':body=json.dumps([{**row,'high':0}]).encode()
                if invalid=='null_percent' and self.unit.subject=='AAPL' and body!=b'[]':body=json.dumps([{**row,'changePercent':None}]).encode()
                return QueueResponse(404 if invalid=='http' and self.unit.subject=='AAPL' else 200,body,a.clock().isoformat())
            def close(self):pass
        batch_calls=0
        def acquire(**kw):
            nonlocal batch_calls
            batch_calls+=1
            split=clean_boundary and batch_calls==1
            if split:kw={**kw,'units':kw['units'][:1]}
            report=acquire_batch(**kw,ticket_factory=Ticket,clock=a.clock,monotonic=lambda:a.elapsed,sleeper=a.sleep)
            if split:report.update(outcome='batch_boundary',unattempted=1)
            return report
        before=mutation_fingerprint(f.f.stores)
        planner=PinnedHistoryPlanner(f.f.stores,f.scope,cutoff=fixtures.CUT)
        from unittest.mock import patch
        # Simulate a canonical writer holding a nonempty WAL: producer-side
        # identity readers must never be called after the population was pinned.
        with patch('quant_data.operations.collection_targets.quiet_immutable_read_connection',side_effect=AssertionError('Concurrent writer rejects quiet reads')):
            acquired=acquire_history(root=root,stores=f.f.stores,selection=f.scope,cutoff=fixtures.CUT,baseline=baseline,
                gate=a.gate,credential='synthetic',deadline=time.monotonic()+120,planner=planner,acquire=acquire)
        self.assertEqual(before,mutation_fingerprint(f.f.stores))
        result=publish_history(root=root,stores=f.f.stores,selection=f.scope,cutoff=fixtures.CUT,
            baseline=baseline,collector=f.collector,deadline=time.monotonic()+120)
        self.assertEqual(len(calls),len(set(calls)))
        return acquired,result,calls

    def test_all_downloads_can_finish_before_one_writer_publishes_without_duplicate_gets(self):
        acquired,result,calls=self.run_case()
        self.assertEqual(acquired['outcome'],'complete');self.assertEqual(acquired['requests'],4)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['written_prices'],2)
        self.assertEqual(result['completed_symbols'],2)
    def test_invalid_payload_is_held_while_independent_history_completes(self):
        acquired,result,calls=self.run_case('payload')
        self.assertEqual(acquired['outcome'],'complete_with_gaps');self.assertEqual(acquired['requests'],3)
        self.assertEqual(result['outcome'],'complete_with_gaps');self.assertEqual(result['written_prices'],1)
        self.assertEqual(result['completed_symbols'],1);self.assertEqual(result['states']['AAPL']['status'],'validation_gap')
    def test_non_systemic_http_gap_is_held_without_retrying_the_symbol(self):
        acquired,result,calls=self.run_case('http')
        self.assertEqual(acquired['outcome'],'complete_with_gaps');self.assertEqual(acquired['requests'],3)
        self.assertEqual(result['written_prices'],1);self.assertEqual(result['completed_symbols'],1)

    def test_clean_admission_boundary_continues_only_uncharged_windows(self):
        acquired,result,calls=self.run_case(clean_boundary=True)
        self.assertEqual(acquired['outcome'],'complete');self.assertEqual(acquired['requests'],4)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['completed_symbols'],2)
        self.assertEqual(len(calls),len(set(calls)))

    def test_unretained_response_bound_remains_charged_to_original_byte_cap(self):
        acquired,result,calls=self.run_case(byte_reserve=8*1024**3)
        self.assertEqual(acquired['outcome'],'invocation_budget');self.assertEqual(acquired['requests'],0)
        self.assertEqual(calls,[]);self.assertEqual(acquired['unretained_byte_reserve'],8*1024**3)
        self.assertEqual(acquired['received_bytes'],0);self.assertEqual(result['written_prices'],0)

    def test_historical_null_percent_can_be_downloaded_and_published(self):
        acquired,result,calls=self.run_case('null_percent')
        self.assertEqual(acquired['outcome'],'complete');self.assertEqual(acquired['requests'],4)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['written_prices'],2)
        self.assertEqual(result['completed_symbols'],2)
