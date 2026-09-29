import unittest,json,tempfile
from pathlib import Path
from datetime import datetime
from quant_data.operations.selected_price_refresh import run_current_prices
from quant_data.operations.collection_queue import QueueResponse
from tests.operations import test_collection_prices as fixtures

class SelectedDailyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SelectedPriceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        state=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(state.cleanup);self.state=Path(state.name)/'current'
        self.calls=[];self.parameters=[];self.row=json.loads(self.f.body)[0]
    def run_refresh(self,empty=False,status=200,monotonic=None,dates=None):
        def fetch(*,unit,timeout_seconds,max_bytes):
            self.calls.append(unit.subject);self.parameters.append(dict(unit.parameters))
            body=b'[]' if empty else json.dumps([{**self.row,'symbol':unit.subject,'date':d} for d in (dates or ['2026-09-08'])]).encode()
            return QueueResponse(status,body,fixtures.CUT)
        kw={} if monotonic is None else {'monotonic':monotonic}
        return run_current_prices(root=self.state,stores=self.f.f.stores,selection=self.f.scope,
            collector=self.f.collector,fetch=fetch,cutoff=fixtures.CUT,session='2026-09-08',
            utcnow=lambda:datetime.fromisoformat(fixtures.CUT.replace('Z','+00:00')),**kw)
    def test_all_selected_and_retained_prices_follow_one_sentinel(self):
        result=self.run_refresh()
        self.assertEqual(self.calls[0],'AAPL');self.assertEqual(set(self.calls),{'AAPL','BRK-B','SPY','^GSPC'})
        self.assertEqual(len(self.calls),4);self.assertEqual(result['published'],4)
        self.assertEqual(result['outcome'],'complete')
        from quant_data.operations.fetch_run_summary import summarize_report
        self.assertEqual(summarize_report('quant-data-market-close.timer',result)['successful'],4)
    def test_empty_sentinel_stops_all_remaining_requests(self):
        result=self.run_refresh(empty=True)
        self.assertEqual(self.calls,['AAPL']);self.assertEqual(result['outcome'],'no_market_session')
        self.assertEqual(result['requests'],1)
        self.assertEqual(result['symbol_counts']['skipped'],4)
    def test_provider_failure_stops_without_retry(self):
        result=self.run_refresh(status=403)
        self.assertEqual(self.calls,['AAPL']);self.assertEqual(result['outcome'],'provider_http_failure')
    def test_invocation_deadline_defers_without_any_provider_request(self):
        clock=iter([0,10000]);result=self.run_refresh(monotonic=lambda:next(clock))
        self.assertEqual(self.calls,[]);self.assertEqual(result['outcome'],'invocation_budget')

    def test_live_requests_exactly_seven_days_and_preserves_actual_returned_dates(self):
        dates=['2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08']
        result=self.run_refresh(dates=dates)
        self.assertTrue(all(p['from']=='2026-09-02' and p['to']=='2026-09-08' for p in self.parameters))
        self.assertEqual(result['maximum_calendar_days'],7)
        from quant_data.stores import quiet_immutable_read_connection
        with quiet_immutable_read_connection(self.f.f.stores,'market') as c:
            self.assertEqual([r[0] for r in c.execute('SELECT DISTINCT trade_date FROM stage10_daily_prices ORDER BY trade_date')],dates)
    def test_out_of_window_response_stops_before_canonical_write(self):
        from quant_data.errors import ValidationError
        from quant_data.stores import quiet_immutable_read_connection
        with self.assertRaises(ValidationError):self.run_refresh(dates=['2026-09-01'])
        self.assertEqual(self.calls,['AAPL'])
        with quiet_immutable_read_connection(self.f.f.stores,'market') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM stage10_daily_prices').fetchone()[0],0)
    def test_live_gate_rejects_incomplete_history_and_wrong_selection(self):
        import hashlib
        from quant_data.operations.selected_price_refresh import require_history_completion,LIVE_ACTIVATION,HISTORY_COMPLETION,price_population_sha256
        from quant_data.operations.collection_queue import atomic
        from quant_data.operations.equibles_transcript_backfill import private_directory
        from quant_data.errors import ConflictError
        root=self.state;private_directory((root/LIVE_ACTIVATION).parent)
        result={'price_population_sha256':price_population_sha256(self.f.scope),'scope_sha256':self.f.scope.scope_sha256,'outcome':'complete','completed_symbols':2,
            'unattempted_symbols':0,'failed_symbols':0,'canonical_audit_verified':True}
        def write(result,scope=None):
            body=json.dumps(result).encode();atomic(root/HISTORY_COMPLETION,body,replace=True)
            atomic(root/LIVE_ACTIVATION,{'contract':'quant_data.selected_price_live_activation.v1',
                'history_sha256':hashlib.sha256(body).hexdigest(),'price_population_sha256':scope or price_population_sha256(self.f.scope)},replace=True)
        write(result);require_history_completion(root,self.f.scope)
        from dataclasses import replace
        changed=replace(self.f.scope,binding=replace(self.f.scope.binding,config_sha256='e'*64),scope_sha256='d'*64)
        require_history_completion(root,changed)
        for bad in ({**result,'outcome':'invocation_budget'},{**result,'failed_symbols':1},{**result,'canonical_audit_verified':False}):
            write(bad)
            with self.assertRaises(ConflictError):require_history_completion(root,self.f.scope)
        write(result,scope='f'*64)
        with self.assertRaises(ConflictError):require_history_completion(root,self.f.scope)

    def test_retired_symbols_are_removed_before_request_planning(self):
        from unittest.mock import patch
        from quant_data.operations import selected_price_refresh as daily
        rows = daily.selected_market_rows(self.f.f.stores, self.f.scope, cutoff=fixtures.CUT)
        extended = rows + tuple({"instrument_id": "retired-" + s,
            "provider_symbol": s, "asset_type": a}
            for s, a in (("ATAI", "equity"), ("IRBO", "etf")))
        at = "2026-09-22T23:00:00Z"
        def fetch(*, unit, timeout_seconds, max_bytes):
            self.calls.append(unit.subject)
            return QueueResponse(200, json.dumps([{**self.row, "symbol": unit.subject,
                "date": "2026-09-22"}]).encode(), at)
        with patch.object(daily, "selected_market_rows", return_value=extended):
            report = daily.run_current_prices(root=self.state, stores=self.f.f.stores,
                selection=self.f.scope, collector=self.f.collector, fetch=fetch,
                cutoff=at, session="2026-09-22",
                utcnow=lambda: datetime.fromisoformat(at))
        self.assertEqual(set(self.calls), {"AAPL", "BRK-B", "SPY", "^GSPC"})
        self.assertEqual(report["requests"], 4)
        self.assertEqual(report["planned_requests"], 4)
        self.assertEqual({r["symbol"] for r in report["excluded_symbols"]}, {"ATAI", "IRBO"})
