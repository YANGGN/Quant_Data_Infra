import unittest,json,hashlib,tempfile,copy
from pathlib import Path
from dataclasses import replace
from quant_data.errors import ValidationError,ConflictError
from quant_data.operations.collection_price_quarantine import inspect_window,POLICY,QUALITY_RECEIPT,AUDIT_RECEIPT
from quant_data.operations.collection_price_windows import HistoryPriceWindow
from quant_data.market.stage12_incremental import Stage12BFixtureResponse
from quant_data.operations.selected_price_refresh import require_history_completion,price_population_sha256,LIVE_ACTIVATION,HISTORY_COMPLETION
from quant_data.operations.collection_queue import atomic
from quant_data.operations.equibles_transcript_backfill import private_directory
from tests.operations import test_collection_prices as fixtures

class QuarantinePlanningTests(unittest.TestCase):
    def setUp(self):
        self.window=HistoryPriceWindow('AAPL','1990-01-01','1990-01-10')
        self.row={'symbol':'AAPL','date':'1990-01-03','open':10,'high':12,'low':9,'close':11,'volume':100,'change':1,'changePercent':10,'vwap':10}
    def inspect(self,rows):return inspect_window(self.window,Stage12BFixtureResponse(200,'application/json',json.dumps(rows).encode(),0))
    def test_clean_original_window_and_empty_boundary_are_distinct(self):
        clean=self.inspect([self.row]);self.assertEqual(clean.clean_windows,(self.window,));self.assertFalse(clean.excluded)
        empty=self.inspect([]);self.assertEqual(empty.raw_rows,0);self.assertEqual(empty.empty_ranges,(self.window,))
    def test_invalid_ohlc_splits_clean_ranges_and_preserves_source_row_indices(self):
        bad={**self.row,'date':'1990-01-05','open':20}
        result=self.inspect([bad,self.row,{**self.row,'date':'1990-01-08'}])
        self.assertEqual(result.excluded[0]['source_rows'],[1]);self.assertEqual(result.raw_rows,3)
        self.assertEqual([(w.start,w.end) for w in result.clean_windows],[('1990-01-01','1990-01-04'),('1990-01-06','1990-01-10')])
        self.assertEqual(result.valid_rows,2)
    def test_all_duplicate_competitors_are_excluded_without_selecting_one(self):
        r=self.inspect([self.row,{**self.row,'close':12},{**self.row,'date':'1990-01-08'}])
        self.assertEqual(r.excluded[0]['source_rows'],[1,2]);self.assertEqual(r.valid_rows,1)
        self.assertEqual(r.clean_windows,(HistoryPriceWindow('AAPL','1990-01-04','1990-01-10'),))
    def test_all_bad_is_not_an_empty_provider_window(self):
        r=self.inspect([{**self.row,'open':20}]);self.assertEqual(r.raw_rows,1);self.assertFalse(r.clean_windows);self.assertTrue(r.excluded)
    def test_consecutive_bad_dates_and_edges_never_create_invalid_or_overlapping_windows(self):
        r=self.inspect([{**self.row,'date':day,'open':20} for day in ('1990-01-01','1990-01-02','1990-01-10')]+[self.row])
        self.assertEqual(r.clean_windows,(HistoryPriceWindow('AAPL','1990-01-03','1990-01-09'),))
    def test_shape_symbol_and_date_corruption_stays_hard_failure(self):
        for row in ({**self.row,'symbol':'WRONG'},{**self.row,'date':'bad'},{**self.row,'date':'1991-01-01'},{k:v for k,v in self.row.items() if k!='volume'}):
            with self.assertRaises(ValidationError):self.inspect([row])
    def test_optional_null_percent_retains_valid_prices(self):
        r=self.inspect([{**self.row,'changePercent':None}]);self.assertEqual(r.valid_rows,1);self.assertFalse(r.excluded)
    def test_response_and_elapsed_bounds_apply_even_with_quarantined_rows(self):
        response=Stage12BFixtureResponse(200,'application/json',json.dumps([{**self.row,'open':20}]).encode(),0)
        for bad in (replace(response,status=503),replace(response,media_type='text/html'),replace(response,elapsed_seconds=True)):
            with self.assertRaises(ValidationError):inspect_window(self.window,bad)

class QuarantineActivationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SelectedPriceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        private_directory((self.root/LIVE_ACTIVATION).parent)
        self.population=price_population_sha256(self.f.scope)
        self.quality={'contract':'quant_data.price_history_quarantine.v1','policy':POLICY,'price_population_sha256':self.population,'unresolved_windows':0,'quarantined_dates':1,
            'windows':[{'symbol':'AAPL','from':'1990-01-01','to':'1990-01-10','response_sha256':'a'*64,'resolution':'clean_subranges_published','excluded':[{'date':'1990-01-05','source_rows':[2],'reasons':['invalid OHLC']}]}]}
        self.audit={'price_population_sha256':self.population,'coverage':[{'source_symbol':s.source_symbol,'status':'resolved','price_rows':100} for s in self.f.scope.subjects]}
        self.result={'contract':'quant_data.selected_price_history_completion.v2','outcome':'complete_with_quarantine','price_population_sha256':self.population,'finished_symbols':2,'completed_symbols':1,'quarantined_symbols':1,'quarantined_dates':1,'unattempted_symbols':0,'failed_symbols':0,'canonical_audit_verified':True,'symbol_states':{s.provider_symbol:{'status':'complete_with_quarantine' if s.provider_symbol=='AAPL' else 'complete','boundary':{'evidence':'empty_provider_window_before_listing_or_retained_history'}} for s in self.f.scope.eligible}}
    def save(self,result=None,quality=None,audit=None,window=7):
        quality=self.quality if quality is None else quality;audit=self.audit if audit is None else audit;result=copy.deepcopy(self.result if result is None else result)
        q=json.dumps(quality).encode();a=json.dumps(audit).encode();result.update(quarantine_sha256=hashlib.sha256(q).hexdigest(),canonical_audit_sha256=hashlib.sha256(a).hexdigest());body=json.dumps(result).encode()
        for name,value in [(QUALITY_RECEIPT,q),(AUDIT_RECEIPT,a),(HISTORY_COMPLETION,body)]:atomic(self.root/name,value,replace=True)
        atomic(self.root/LIVE_ACTIVATION,{'contract':'quant_data.selected_price_live_activation.v2','at':fixtures.CUT,'price_population_sha256':self.population,'history_sha256':hashlib.sha256(body).hexdigest(),'window_calendar_days':window},replace=True)
    def revised_mapping(self,*,price_change=False):
        from quant_data.market.collection_mappings import IdentityEvidence
        from quant_data.market.collection_bindings import pin_binding
        f=self.f.f
        rows=copy.deepcopy(f.rows)
        rows[0]['cik']='0000789019'
        rows[0]['reason']='Reviewed issuer correction'
        if price_change:
            rows[0].update(provider_symbol='MSFT',provider_subject='MSFT',instrument_id=f.instrument_ids['MSFT'])
        proof=IdentityEvidence(json.dumps([{'symbol':r['provider_symbol'],'cik':r['cik']} for r in rows]).encode(),
            'fixture/revised-identities.json','2026-09-09T04:00:00Z')
        for i,row in enumerate(rows):
            row.update(evidence_sha256=proof.sha256,evidence_reference=proof.source_reference,evidence_pointer='/'+str(i))
        f.publisher.publish(f.batch(rows,at='2026-09-09T04:00:00Z',evidence=(proof,)))
        return pin_binding(f.stores,self.f.scope.binding,cutoff='2026-09-09T05:00:00Z')

    def check_revised(self,scope):
        return require_history_completion(self.root,scope,stores=self.f.f.stores,cutoff='2026-09-09T05:00:00Z')

    def test_issuer_only_correction_reuses_exact_original_approval_without_writes(self):
        from quant_data.fingerprint import mutation_fingerprint
        self.save();updated=self.revised_mapping()
        self.assertNotEqual(updated.mapping_id,self.f.scope.mapping_id)
        self.assertNotEqual(price_population_sha256(updated),self.population)
        before=mutation_fingerprint(self.f.f.stores)
        receipts={p:p.read_bytes() for p in (self.root/LIVE_ACTIVATION).parent.iterdir()}
        self.check_revised(updated)
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
        self.assertEqual(receipts,{p:p.read_bytes() for p in receipts})
        with self.assertRaises(ConflictError):require_history_completion(self.root,updated)

    def test_real_changed_price_identity_cannot_borrow_issuer_correction_approval(self):
        self.save();updated=self.revised_mapping(price_change=True)
        with self.assertRaises(ConflictError):self.check_revised(updated)

    def test_membership_retained_identity_missingness_and_forged_pin_still_reject(self):
        self.save();updated=self.revised_mapping()
        for bad in (replace(updated,membership_snapshot_id='different'),
                    replace(updated,retained=updated.retained[:-1]),
                    replace(updated,scope_sha256='f'*64),
                    replace(updated,subjects=(replace(updated.subjects[0],status='identity_incomplete'),*updated.subjects[1:]))):
            with self.subTest(bad=bad),self.assertRaises(ConflictError):self.check_revised(bad)

    def test_issuer_correction_still_validates_original_hashes_and_activation_cutoff(self):
        self.save();updated=self.revised_mapping()
        for receipt in (HISTORY_COMPLETION,QUALITY_RECEIPT,AUDIT_RECEIPT):
            self.save();atomic(self.root/receipt,b'{}',replace=True)
            with self.subTest(receipt=receipt),self.assertRaises(ConflictError):self.check_revised(updated)
        self.save()
        activation=json.loads((self.root/LIVE_ACTIVATION).read_text())
        activation['at']='2026-09-09T06:00:00Z'
        atomic(self.root/LIVE_ACTIVATION,activation,replace=True)
        with self.assertRaises(ConflictError):self.check_revised(updated)

    def test_explicit_gap_receipts_allow_only_completed_selected_population(self):
        self.save();require_history_completion(self.root,self.f.scope)
        for change in ({'finished_symbols':1},{'failed_symbols':1},{'canonical_audit_verified':False},{'completed_symbols':2}):
            self.save(result={**self.result,**change})
            with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
        pending=copy.deepcopy(self.result);pending['symbol_states']['AAPL']['status']='pending';self.save(result=pending)
        with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
    def test_receipt_tamper_unresolved_windows_missing_prices_and_longer_live_window_fail(self):
        self.save();atomic(self.root/QUALITY_RECEIPT,b'{}',replace=True)
        with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
        self.save(quality={**self.quality,'unresolved_windows':1})
        with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
        audit=copy.deepcopy(self.audit);audit['coverage'][0]['price_rows']=0;self.save(audit=audit)
        with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
        self.save(window=8)
        with self.assertRaises(ConflictError):require_history_completion(self.root,self.f.scope)
