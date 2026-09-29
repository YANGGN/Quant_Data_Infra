import json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from tests.operations import test_collection_prices as fixtures
from quant_data.operations.selected_price_backfill import HistoryTarget,initial_state,next_window,advance,run_history
from quant_data.operations.collection_queue import QueueResponse
from quant_data.errors import ConflictError

class HistoricalProgressionTests(unittest.TestCase):
    def test_new_history_does_not_stop_at_empty_recent_window_or_1990(self):
        state=initial_state(HistoryTarget('AAPL','1980-12-12'),'2026-09-09')
        seen=[]
        while state['status']=='pending':
            w=next_window(state);seen.append(w);advance(state,w,empty=True,written=0)
        self.assertEqual(state['status'],'complete');self.assertLess(seen[-1].end,'1980-12-12')
        for a,b in zip(seen,seen[1:]):
            from datetime import date,timedelta
            self.assertEqual(date.fromisoformat(a.start)-timedelta(days=1),date.fromisoformat(b.end))
        self.assertEqual(seen[0].end,'2026-09-09')
    def test_existing_population_only_fills_missing_tail_and_older_prefix(self):
        state=initial_state(HistoryTarget('AAPL',None,'1990-01-01','2026-09-08'),'2026-09-09')
        tail=next_window(state);self.assertEqual((tail.start,tail.end),('2026-09-09','2026-09-09'))
        advance(state,tail,empty=False,written=1)
        older=next_window(state);self.assertEqual((older.start,older.end),('1985-01-01','1989-12-31'))
        advance(state,older,empty=False,written=100)
        self.assertEqual(next_window(state).end,'1984-12-31')
    def test_search_guard_is_incomplete_when_oldest_window_contains_data(self):
        state=initial_state(HistoryTarget('OLD','1900-01-01'),'1904-12-31')
        advance(state,next_window(state),empty=False,written=1)
        self.assertEqual(state['status'],'search_boundary_reached')

class DedicatedHistoryTests(unittest.TestCase):
    def test_progressively_publishes_and_never_restarts_completed_run(self):
        f=fixtures.SelectedPriceTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(temp.cleanup);root=Path(temp.name)/'history'
        calls=[];row=json.loads(f.body)[0]
        def fetch(*,unit,timeout_seconds,max_bytes):
            params=dict(unit.parameters);calls.append(params)
            body=json.dumps([{**row,'symbol':unit.subject}]).encode() if params['to']=='2026-09-08' else b'[]'
            return QueueResponse(200,body,fixtures.CUT)
        kw=dict(root=root,stores=f.f.stores,selection=f.scope,collector=f.collector,fetch=fetch,
            targets=tuple(HistoryTarget(s.provider_symbol,'2026-01-01') for s in f.scope.eligible),
            end='2026-09-08',cutoff=fixtures.CUT,utcnow=lambda:datetime.fromisoformat(fixtures.CUT.replace('Z','+00:00')))
        result=run_history(**kw)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['completed_symbols'],2)
        self.assertEqual(result['written_prices'],2);self.assertEqual(len(calls),4)
        self.assertEqual(set(p['symbol'] for p in calls),{'AAPL','BRK-B'})
        self.assertFalse(result['canonical_audit_verified'])
        with self.assertRaises(ConflictError):run_history(**kw)
        self.assertEqual(len(calls),4)

    def test_settled_continuation_keeps_original_deadline_and_does_not_repeat_gets(self):
        from unittest.mock import patch
        from dataclasses import replace
        from quant_data.operations.collection_queue import run_queue
        f=fixtures.SelectedPriceTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(temp.cleanup);root=Path(temp.name)/'history'
        calls=[];row=json.loads(f.body)[0]
        def fetch(*,unit,timeout_seconds,max_bytes):
            calls.append(unit.request_id);params=dict(unit.parameters)
            body=json.dumps([{**row,'symbol':unit.subject}]).encode() if params['to']=='2026-09-08' else b'[]'
            return QueueResponse(200,body,fixtures.CUT)
        kw=dict(root=root,stores=f.f.stores,selection=f.scope,collector=f.collector,fetch=fetch,
            targets=tuple(HistoryTarget(s.provider_symbol,'2026-01-01') for s in f.scope.eligible),
            end='2026-09-08',cutoff=fixtures.CUT,utcnow=lambda:datetime.fromisoformat(fixtures.CUT.replace('Z','+00:00')))
        def stop_between_requests(**kwargs):
            run_queue(**{**kwargs,'plan':replace(kwargs['plan'],units=kwargs['plan'].units[:1])})
            raise ConflictError('Controlled pre-dispatch interruption after settled publication')
        with patch('quant_data.operations.selected_price_backfill.run_queue',side_effect=stop_between_requests):
            first=run_history(**kw)
        self.assertEqual(first['requests'],1);self.assertEqual(first['outcome'],'stopped')
        start=(root/'started.json').read_bytes()
        result=run_history(**kw,continuation=True)
        self.assertEqual(result['outcome'],'complete');self.assertEqual(result['requests'],4)
        self.assertEqual(len(calls),len(set(calls)));self.assertEqual(len(calls),4)
        self.assertEqual((root/'started.json').read_bytes(),start)
        self.assertEqual(json.loads((root/'continuation.json').read_bytes())['remaining_requests'],13871)

class ForwardClockTests(unittest.TestCase):
    def test_wait_returns_real_forward_clock_without_fabricating_timestamp(self):
        from quant_data.operations.collection_queue import BoundedForwardClock
        values=iter(datetime.fromisoformat(s) for s in ['2026-09-10T14:00:00+00:00','2026-09-10T13:59:59.9+00:00','2026-09-10T14:00:00.1+00:00'])
        sleeps=[];clock=BoundedForwardClock(lambda:next(values),deadline=5,monotonic=lambda:0,sleeper=sleeps.append)
        self.assertEqual(clock().isoformat(),'2026-09-10T14:00:00+00:00')
        self.assertEqual(clock().isoformat(),'2026-09-10T14:00:00.100000+00:00');self.assertEqual(sleeps,[0.1])
    def test_backward_clock_cannot_extend_original_deadline(self):
        from quant_data.operations.collection_queue import BoundedForwardClock
        from quant_data.errors import ResourceLimitError
        clock=BoundedForwardClock(lambda:datetime.fromisoformat('2026-09-10T13:00:00+00:00'),deadline=5,
            monotonic=lambda:5,sleeper=lambda _:self.fail('No sleep after deadline'),last='2026-09-10T14:00:00Z')
        with self.assertRaises(ResourceLimitError):clock()
