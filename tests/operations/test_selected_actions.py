import hashlib, json, tempfile, unittest
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations import selected_actions as actions
from quant_data.operations.collection_queue import atomic, _retain, QueueResponse
from quant_data.operations.equibles_transcript_backfill import private_directory
from quant_data.operations.fetch_run_summary import summarize_report
from tests.operations import test_collection_fmp as fixtures

class SelectedActionsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SelectedFmpTests('runTest'); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.stores = self.fixture.f.stores; self.registry = self.fixture.f.registry
        self.binding = self.fixture.f.bindings['dividends_splits']
        temporary = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.evidence = self.root / 'collection/fmp/blobs'
    def plan(self, historical=True):
        return actions.prepare(self.stores, self.binding, cutoff=fixtures.CUT, historical=historical, source_root=self.root)
    def fake(self, body=b'[]', status=200, fail_publish=False, partial_pilot=False):
        calls=[]
        def acquire(**kw):
            path=kw['root']
            for part in ('attempts','responses','blobs'):private_directory(path/part)
            units=kw['units'][:1] if partial_pilot else kw['units']
            for u in units:
                calls.append(u)
                atomic(path/'attempts'/(u.unit_id+'.json'), {'unit_id':u.unit_id})
                _retain(path,u,QueueResponse(status,body,'2026-09-09T05:01:00Z'),())
            return {'requests':len(units),'received_bytes':len(body)*len(units),'uncertain_requests':0,'outcome':'batch_boundary'}
        return calls,acquire
    def run_plan(self, manifest, acquire, name='run'):
        return actions.run(root=self.root/name,stores=self.stores,registry=self.registry,manifest=manifest,
            gate=None,credential='synthetic',evidence_root=self.evidence,require_active=False,acquire=acquire,
            clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
    def test_exact_two_endpoints_and_issuer_gaps(self):
        manifest=self.plan();selection,units,subjects=actions.validate_manifest(manifest,self.stores,require_active=False)
        self.assertEqual({u.endpoint for u in units},{'dividends','splits'})
        self.assertEqual(manifest['ready_symbols'],['AAPL']);self.assertTrue(manifest['identity_gaps'])
        self.assertEqual(manifest['maximum_requests'],2)
        for u in units:self.assertEqual(dict(u.parameters),{'symbol':'AAPL','limit':'1000'})
    def test_retained_population_excluded_and_original_bytes_required(self):
        selection,work=self.fixture.work('dividends_splits');unit=work.units[0]
        self.fixture.publish(selection,work.units,unit,[])
        private_directory(self.evidence);atomic(self.evidence/(hashlib.sha256(b'[]').hexdigest()+'.json'),b'[]')
        historical=self.plan();current=self.plan(False)
        self.assertEqual(len(historical['units']),1);self.assertEqual(len(historical['retained']),1)
        self.assertEqual(len(current['units']),2);self.assertFalse(current['retained'])
        actions.validate_manifest(historical,self.stores,require_active=False)
        bad=deepcopy(historical);bad['retained'][0]['capture_id']='invented'
        with self.assertRaises(ConflictError):actions.validate_manifest(bad,self.stores,require_active=False)
        (self.evidence/(hashlib.sha256(b'[]').hexdigest()+'.json')).unlink()
        with self.assertRaisesRegex(ConflictError,'bytes are missing'):self.plan()
    def test_manifest_cannot_drop_or_change_requests_or_add_endpoints(self):
        for change in ('drop','endpoint','budget','pilot'):
            manifest=self.plan()
            if change=='drop':manifest['units'].pop();manifest['maximum_requests']-=1
            elif change=='endpoint':manifest['units'][0]['endpoint']='earnings'
            elif change=='budget':manifest['maximum_requests']+=1
            else:manifest['pilot_units']=0
            with self.assertRaises((ConflictError,ValidationError)):actions.validate_manifest(manifest,self.stores,require_active=False)
    def test_empty_results_retained_once_and_repeat_start_rejected(self):
        calls,acquire=self.fake();manifest=self.plan();result=self.run_plan(manifest,acquire)
        self.assertEqual(len(calls),2);self.assertEqual(result['pending_scopes'],0)
        self.assertEqual(result['source_failures'],0);self.assertEqual(result['counts']['successful'],2)
        self.assertEqual(result['requests'],2);self.assertEqual(result['received_bytes'],4)
        from quant_data.stores import quiet_immutable_read_connection
        with quiet_immutable_read_connection(self.stores,'company') as c:
            for path in (self.root/'run/outcomes').glob('*.json'):
                item=json.loads(path.read_text())['result']
                domain=c.execute('SELECT semantic_identity FROM company_action_snapshots WHERE snapshot_id=?',(item['capture_id'],)).fetchone()
                control=c.execute('SELECT semantic_identity FROM ingestion_snapshots WHERE snapshot_id=?',(item['publication_snapshot_id'],)).fetchone()
                self.assertIsNotNone(domain);self.assertIsNotNone(control);self.assertEqual(domain[0],control[0])
                self.assertNotEqual(item['capture_id'],item['publication_snapshot_id'])
        self.assertEqual((self.evidence/(hashlib.sha256(b'[]').hexdigest()+'.json')).read_bytes(),b'[]')
        with self.assertRaises(ConflictError):self.run_plan(manifest,acquire)
        self.assertEqual(len(calls),2)
    def test_exact_replay_keeps_resolvable_receipt_ids_and_zero_canonical_change(self):
        calls,acquire=self.fake();manifest=self.plan();self.run_plan(manifest,acquire)
        before=mutation_fingerprint(self.stores)
        self.run_plan(manifest,acquire,name='explicit_fixture_replay')
        self.assertEqual(before,mutation_fingerprint(self.stores))
        first=sorted((self.root/'run/outcomes').glob('*.json'))
        for path in first:
            old=json.loads(path.read_text())['result']
            repeated=json.loads((self.root/'explicit_fixture_replay/outcomes'/path.name).read_text())['result']
            self.assertEqual(repeated['outcome'],'unchanged')
            self.assertEqual(repeated['capture_id'],old['capture_id'])
            self.assertEqual(repeated['publication_snapshot_id'],old['publication_snapshot_id'])

    def test_entitlement_and_invalid_source_do_not_reach_canonical_publisher(self):
        for status,body in [(402,b'{"error":"plan"}'),(200,b'{"error":"invalid"}')]:
            calls,acquire=self.fake(body,status);before=mutation_fingerprint(self.stores)
            result=self.run_plan(self.plan(),acquire,str(status))
            self.assertEqual(result['stop_reason'],'endpoint_preflight_failed')
            self.assertEqual(before,mutation_fingerprint(self.stores));self.assertEqual(len(calls),2)
    def test_split_pilot_finishes_both_endpoints_without_repeating_calls(self):
        calls,acquire=self.fake(partial_pilot=True);result=self.run_plan(self.plan(),acquire)
        self.assertEqual(result['pending_scopes'],0);self.assertEqual(len(calls),2)
        self.assertEqual(len({u.unit_id for u in calls}),2)
    def test_publication_failure_retains_charges_bytes_and_pending_scope(self):
        calls,acquire=self.fake()
        with patch.object(actions,'publish_response',side_effect=ConflictError('explicit fixture failure')):
            result=self.run_plan(self.plan(),acquire)
        self.assertEqual(result['requests'],2);self.assertEqual(result['received_bytes'],4)
        self.assertEqual(result['pending_scopes'],2);self.assertEqual(result['counts']['partial'],2)
        self.assertEqual(result['counts']['unattempted'],0);self.assertEqual(len(calls),2)
    def test_transport_wrapper_rejects_non_action_units_without_dispatch(self):
        manifest=self.plan();u=actions.unit_from_dict(manifest['units'][0])
        with patch.object(actions,'_acquire_batch') as acquire:
            actions.acquire_actions_batch(units=(u,));self.assertEqual(acquire.call_args.kwargs['priority'],'backfill')
            bad=deepcopy(manifest['units'][0]);bad['collection']='earnings_dates';bad['endpoint']='earnings'
            with self.assertRaises(ValidationError):actions.acquire_actions_batch(units=(actions.unit_from_dict(bad),))
            self.assertEqual(acquire.call_count,1)
    def test_capped_action_response_remains_partial(self):
        from datetime import timedelta,date
        rows=[{'symbol':'AAPL','date':(date(2020,1,1)+timedelta(days=i)).isoformat(),
            'dividend':0.1,'numerator':2,'denominator':1} for i in range(1000)]
        calls,acquire=self.fake(json.dumps(rows).encode())
        result=self.run_plan(self.plan(),acquire)
        self.assertEqual(len(calls),2);self.assertEqual(result['pending_scopes'],0)
        self.assertEqual(result['counts']['partial'],2);self.assertEqual(result['counts']['successful'],0)
        self.assertEqual(result['exit_code'],75)

    def activate_fixture(self, manifest):
        private_directory(self.root/actions.ACTIVATION.parent)
        selection=actions.selection_from_dict(manifest['selection'])
        audit={'population':actions.population(selection),'ready_symbols':manifest['ready_symbols'],
            'verified_scopes':2,'foreign_key_violations':0,'original_bytes_verified':True}
        completion={'contract':'quant_data.selected_actions_result.v1','stop_reason':None,'pending_scopes':0,
            'source_failures':0,'counts':{'partial':0}}
        atomic(self.root/actions.AUDIT,audit,replace=True);atomic(self.root/actions.COMPLETION,completion,replace=True)
        activation={'contract':'quant_data.selected_actions_activation.v1','population':actions.population(selection),
            'ready_symbols':manifest['ready_symbols'],'endpoints':['dividends','splits'],'cadence':'Mon..Fri 19:00 America/New_York',
            'backfill_audit_verified':True,'audit_sha256':hashlib.sha256((self.root/actions.AUDIT).read_bytes()).hexdigest(),
            'completion_sha256':hashlib.sha256((self.root/actions.COMPLETION).read_bytes()).hexdigest()}
        atomic(self.root/actions.ACTIVATION,activation,replace=True)

    def test_activation_requires_complete_hash_linked_evidence(self):
        manifest=self.plan(False);self.activate_fixture(manifest)
        actions.require_activation(self.root,manifest)
        completion=json.loads((self.root/actions.COMPLETION).read_text())
        changed=deepcopy(manifest);changed['ready_symbols'].append('MSFT')
        with self.assertRaises(ConflictError):actions.require_activation(self.root,changed)
        atomic(self.root/actions.COMPLETION,{**completion,'pending_scopes':1},replace=True)
        with self.assertRaises(ConflictError):actions.require_activation(self.root,manifest)
    def test_local_gate_accepts_exact_prepared_binding_without_shared_cutover(self):
        from quant_data.market.collection_bindings import load_bindings,pin_binding
        from quant_data.operations.collection_fmp import fmp_units
        host=self.root/'config/collection_bindings.json';host.parent.mkdir()
        source=Path(actions.__file__).resolve().parents[2]/'config/collection_bindings.json'
        original=source.read_bytes();host.write_bytes(original)
        before=pin_binding(self.stores,load_bindings(host)['fmp_statements'],cutoff=fixtures.CUT)
        work=fmp_units(self.stores,before,mode='historical_backfill',observation_window=fixtures.CUT,cutoff=fixtures.CUT)
        manifest=self.plan(False);self.assertEqual(self.binding.mode,'prepared')
        self.activate_fixture(manifest)
        with patch.object(actions,'LIVE_ROOT',self.root):
            actions.validate_live_authority(self.stores,manifest,cutoff=fixtures.CUT)
            for field,value in [('mode','active'),('config_sha256','f'*64)]:
                changed=deepcopy(manifest);changed['selection']['binding'][field]=value
                with self.assertRaises(ConflictError):actions.validate_live_authority(self.stores,changed,cutoff=fixtures.CUT)
        self.assertEqual(host.read_bytes(),original)
        after=pin_binding(self.stores,load_bindings(host)['fmp_statements'],cutoff=fixtures.CUT)
        self.assertEqual(before,after)
        self.assertEqual(work,fmp_units(self.stores,after,mode='historical_backfill',observation_window=fixtures.CUT,cutoff=fixtures.CUT))

    def test_incomplete_live_gate_stops_before_credentials_or_provider_dispatch(self):
        manifest=self.plan(False);self.activate_fixture(manifest)
        for mutation in ('missing','hash','pending','partial'):
            self.activate_fixture(manifest)
            if mutation=='missing':(self.root/actions.ACTIVATION).unlink()
            elif mutation=='hash':
                marker=json.loads((self.root/actions.ACTIVATION).read_text());marker['audit_sha256']='0'*64
                atomic(self.root/actions.ACTIVATION,marker,replace=True)
            else:
                result=json.loads((self.root/actions.COMPLETION).read_text())
                if mutation=='pending':result['pending_scopes']=1
                else:result['counts']['partial']=1
                atomic(self.root/actions.COMPLETION,result,replace=True)
                marker=json.loads((self.root/actions.ACTIVATION).read_text())
                marker['completion_sha256']=hashlib.sha256((self.root/actions.COMPLETION).read_bytes()).hexdigest()
                atomic(self.root/actions.ACTIVATION,marker,replace=True)
            with patch.object(actions,'LIVE_ROOT',self.root), \
                 patch('quant_data.market.collection_bindings.load_bindings',return_value={'dividends_splits':self.binding}), \
                 patch.object(actions,'prepare',return_value=manifest), \
                 patch('quant_data.credentials.read_project_credential') as credential, \
                 patch('quant_data.operations.collection_provider_policy.host_allowance') as allowance, \
                 patch.object(actions,'run') as run:
                with self.assertRaises(ConflictError):actions.run_live(project_root=self.root,stores=self.stores,registry=self.registry)
                credential.assert_not_called();allowance.assert_not_called();run.assert_not_called()

    def gap_fixture(self):
        manifest=self.plan(False);self.activate_fixture(manifest)
        audit=json.loads((self.root/actions.AUDIT).read_text())
        audit.update(contract='quant_data.selected_actions_audit.v2',verified_scopes=1,assessed_scopes=2,
            source_rejected_scopes=1,source_gap_evidence_verified=True,scopes=[{'symbol':'AAPL','endpoint':'splits'}])
        completion={'contract':'quant_data.selected_actions_result.v1','exit_code':75,'stop_reason':None,
            'pending_scopes':0,'source_failures':1,'requests':2,'processed_scopes':2,'retained_scopes':0,
            'counts':{'failed':1,'partial':0,'unattempted':0,'successful':1}}
        gaps={'contract':'quant_data.selected_action_source_gaps.v1','gaps':[{'symbol':'AAPL','endpoint':'dividends',
            'unit_id':'one','request_id':'request','captured_at':fixtures.CUT,'content_sha256':'a'*64,
            'status':200,'error_type':'ValidationError','classification':'ambiguous_duplicate_dividend_event',
            'canonical_unpublished':True,'raw_rows':2,'byte_count':300,
            'reason':'FMP company market data /dividends/2 duplicates an FMP dividend event'}]}
        atomic(self.root/actions.AUDIT,audit,replace=True);atomic(self.root/actions.COMPLETION,completion,replace=True)
        atomic(self.root/actions.SOURCE_GAPS,gaps,replace=True)
        marker=json.loads((self.root/actions.ACTIVATION).read_text());marker.update(
            contract='quant_data.selected_actions_activation.v2',history_status='assessed_with_source_gaps',monitoring_enabled=True)
        for field,path in [('audit_sha256',actions.AUDIT),('completion_sha256',actions.COMPLETION),('source_gaps_sha256',actions.SOURCE_GAPS)]:
            marker[field]=hashlib.sha256((self.root/path).read_bytes()).hexdigest()
        atomic(self.root/actions.ACTIVATION,marker,replace=True)
        return manifest

    def test_gap_gate_admits_exact_partition_and_preserves_nonzero_completion(self):
        manifest=self.gap_fixture();before=(self.root/actions.COMPLETION).read_bytes()
        actions.require_activation(self.root,manifest)
        self.assertEqual((self.root/actions.COMPLETION).read_bytes(),before)
        self.assertEqual(len(manifest['units']),2)

    def test_gap_gate_rejects_resource_caps_uncertainty_and_partition_forgery(self):
        for change in ('cap','resource','http','wrong_reason','missing_scope','duplicate_scope','unattempted','pending','counts','hash'):
            manifest=self.gap_fixture()
            audit=json.loads((self.root/actions.AUDIT).read_text());gaps=json.loads((self.root/actions.SOURCE_GAPS).read_text())
            completion=json.loads((self.root/actions.COMPLETION).read_text());v=gaps['gaps'][0]
            if change=='cap':v['raw_rows']=1000
            elif change=='resource':v['error_type']='ResourceLimitError'
            elif change=='http':v['status']=402
            elif change=='wrong_reason':v['reason']='An invalid event date'
            elif change=='missing_scope':audit['scopes']=[]
            elif change=='duplicate_scope':audit['scopes'].append(audit['scopes'][0])
            elif change=='unattempted':completion['counts']['unattempted']=1
            elif change=='pending':completion['pending_scopes']=1
            elif change=='counts':completion['source_failures']=0
            else:v['byte_count']=301
            atomic(self.root/actions.AUDIT,audit,replace=True);atomic(self.root/actions.SOURCE_GAPS,gaps,replace=True)
            atomic(self.root/actions.COMPLETION,completion,replace=True)
            marker=json.loads((self.root/actions.ACTIVATION).read_text())
            if change!='hash':
                for field,path in [('audit_sha256',actions.AUDIT),('completion_sha256',actions.COMPLETION),('source_gaps_sha256',actions.SOURCE_GAPS)]:
                    marker[field]=hashlib.sha256((self.root/path).read_bytes()).hexdigest()
            atomic(self.root/actions.ACTIVATION,marker,replace=True)
            with self.subTest(change=change),self.assertRaises(ConflictError):actions.require_activation(self.root,manifest)

    def test_expanded_numeric_report_exceeds_legacy_step_list_bound(self):
        report={'contract':'quant_data.company_market_refresh.v2','counts':{'unit':'steps','successful':4402,
            'failed':0,'partial':0,'skipped':0,'unattempted':0}}
        self.assertEqual(summarize_report('quant-data-company-market-refresh.timer',report)['successful'],4402)

if __name__=='__main__':unittest.main()
