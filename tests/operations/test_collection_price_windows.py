import unittest,json,hashlib
from dataclasses import replace
from quant_data.errors import ValidationError,ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.stores import quiet_immutable_read_connection
from quant_data.operations.collection_price_windows import HistoryPriceWindow,history_price_units,SelectedPriceHistoryPublisher
from quant_data.market.stage12_incremental import Stage12BFixtureRequest,Stage12BFixtureResponse
from tests.operations import test_collection_prices as fixtures
CUT=fixtures.CUT

class HistoricalPriceWindowsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SelectedPriceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.window=HistoryPriceWindow('AAPL','1990-01-01','1994-12-31')
        self.requests=history_price_units(self.f.f.stores,self.f.scope,windows=(self.window,),cutoff=CUT)
        self.publisher=SelectedPriceHistoryPublisher(stores=self.f.f.stores,selection=self.f.scope,cutoff=CUT,collector=self.f.collector,requests=self.requests)
        row=json.loads(self.f.body)[0]
        self.body=json.dumps([{**row,'date':'1994-12-30'},{**row,'date':'1990-01-02'}]).encode()
    def publish(self,body=None):
        body=self.body if body is None else body;u=self.requests[0][0]
        return self.publisher(unit=u,retained=None,body=body,receipt={'request_id':u.request_id,'unit_id':u.unit_id,'status':200,'content_sha256':hashlib.sha256(body).hexdigest(),'captured_at':CUT})
    def test_original_response_and_source_dates_preserved_and_replay_is_zero_write(self):
        result=self.publish();self.assertEqual(result['written_versions'],2)
        with quiet_immutable_read_connection(self.f.f.stores,'market') as c:
            capture=c.execute('SELECT response_bytes,request_scope_json FROM stage10_daily_price_captures WHERE capture_id=?',(result['capture_id'],)).fetchone()
            self.assertEqual(capture['response_bytes'],self.body)
            self.assertEqual(json.loads(capture['request_scope_json'])['coverage_basis'],'complete_provider_window')
            dates=[r[0] for r in c.execute('SELECT trade_date FROM stage10_daily_price_versions ORDER BY trade_date')]
            self.assertEqual(dates,['1990-01-02','1994-12-30'])
            source_rows=[tuple(r) for r in c.execute('SELECT trade_date,source_row FROM stage10_daily_price_versions ORDER BY trade_date')]
            self.assertEqual(source_rows,[('1990-01-02',2),('1994-12-30',1)])
        before=mutation_fingerprint(self.f.f.stores)
        self.assertEqual(self.publish()['outcome'],'unchanged')
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_duplicate_outside_window_wrong_symbol_and_invalid_ohlc_reject_without_writes(self):
        row=json.loads(self.body)[0];before=mutation_fingerprint(self.f.f.stores)
        for rows in ([row,row],[{**row,'date':'1995-01-03'}],[{**row,'symbol':'MSFT'}],[{**row,'low':999999}]):
            with self.assertRaises(ValidationError):self.publish(json.dumps(rows).encode())
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_empty_is_explicit_and_does_not_fabricate_history(self):
        before=mutation_fingerprint(self.f.f.stores)
        result=self.publish(b'[]');self.assertEqual(result['coverage'],'empty')
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
    def test_future_oversized_window_and_unselected_symbol_reject(self):
        for w in (replace(self.window,end='2000-01-01'),replace(self.window,symbol='UNKNOWN'),HistoryPriceWindow('AAPL','2026-09-09','2026-09-10')):
            with self.assertRaises(ValidationError):history_price_units(self.f.f.stores,self.f.scope,windows=(w,),cutoff=CUT)
    def test_incremental_rebind_invalidates_history_and_restores_short_window_bound(self):
        self.f.collector._bind_selected_prices(stores=self.f.f.stores,selection=self.f.scope,cutoff=CUT)
        with self.assertRaises(ConflictError):self.publish()
        with self.assertRaises(ValidationError):self.f.collector.prepare(Stage12BFixtureRequest('AAPL',self.window.start,self.window.end,(),CUT),Stage12BFixtureResponse(200,'application/json',self.body,0))

    def test_history_can_extend_before_1990_but_live_contract_stays_short(self):
        windows=(HistoryPriceWindow('AAPL','1980-01-01','1984-12-31'),)
        self.assertEqual(len(history_price_units(self.f.f.stores,self.f.scope,windows=windows,cutoff=CUT)),1)
        with self.assertRaises(ValidationError):history_price_units(self.f.f.stores,self.f.scope,windows=(HistoryPriceWindow('AAPL','1899-01-01','1900-01-01'),),cutoff=CUT)

    def test_null_ancillary_percent_preserves_original_bytes_source_rows_and_replay(self):
        from quant_data.json_codec import loads_strict
        from quant_data.operations.collection_price_windows import parse_history_response
        rows=json.loads(self.body);rows[0]['changePercent']=None;body=json.dumps(rows).encode()
        parsed=parse_history_response(self.f.collector,{'symbol':'AAPL','from':self.window.start,'to':self.window.end},Stage12BFixtureResponse(200,'application/json',body,0))
        self.assertIsNone(next(r for r in parsed if r.trade_date=='1994-12-30').semantic_mapping()['changePercent'])
        with self.assertRaises(ValidationError):self.f.collector._parse_row(loads_strict(body)[0],index=0,expected_symbol='AAPL')
        result=self.publish(body);self.assertEqual(result['written_versions'],2)
        with quiet_immutable_read_connection(self.f.f.stores,'market') as c:
            self.assertEqual(c.execute('SELECT response_bytes FROM stage10_daily_price_captures WHERE capture_id=?',(result['capture_id'],)).fetchone()[0],body)
            source_rows=[tuple(r) for r in c.execute('SELECT trade_date,source_row FROM stage10_daily_price_versions ORDER BY trade_date')]
            self.assertEqual(source_rows,[('1990-01-02',2),('1994-12-30',1)])
        before=mutation_fingerprint(self.f.f.stores)
        self.assertEqual(self.publish(body)['outcome'],'unchanged');self.assertEqual(before,mutation_fingerprint(self.f.f.stores))

    def test_null_percent_does_not_relax_ohlcv_shape_or_other_ancillary_values(self):
        row={**json.loads(self.body)[0],'changePercent':None};before=mutation_fingerprint(self.f.f.stores)
        for bad in ({**row,'high':0},{**row,'open':None},{**row,'volume':None},{**row,'change':None},{**row,'vwap':None},
                    {**row,'changePercent':'null'},{**row,'changePercent':True},{k:v for k,v in row.items() if k!='changePercent'}):
            with self.assertRaises(ValidationError):self.publish(json.dumps([bad]).encode())
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))

    def test_numeric_historical_semantics_remain_identical_to_legacy_parser(self):
        from quant_data.json_codec import loads_strict
        from quant_data.operations.collection_price_windows import parse_history_response
        expected=sorted((self.f.collector._parse_row(r,index=i,expected_symbol='AAPL') for i,r in enumerate(loads_strict(self.body))),key=lambda r:r.trade_date)
        actual=parse_history_response(self.f.collector,{'symbol':'AAPL','from':self.window.start,'to':self.window.end},Stage12BFixtureResponse(200,'application/json',self.body,0))
        self.assertEqual([r.semantic_mapping() for r in actual],[r.semantic_mapping() for r in expected])
