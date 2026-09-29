import hashlib,json,tempfile,unittest
from copy import deepcopy
from dataclasses import replace,asdict
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from quant_data.errors import ConflictError,ValidationError,StoreUnavailableError
from quant_data.ingestion import PublicationDeferred
from quant_data.fingerprint import mutation_fingerprint
from quant_data.operations.selected_company_collection import (prepare,validate_manifest,run,process_response,
    BINDINGS,scope_key,selection_from_dict,unit_from_dict)
from quant_data.operations.selected_company_refresh import require_activation,population,ACTIVATION
from quant_data.operations.collection_queue import _retain,QueueResponse,atomic
from quant_data.operations.equibles_transcript_backfill import private_directory
from quant_data.operations.collection_parallel_prices import acquire_company_batch,acquire_batch
from quant_data.operations.fetch_run_summary import summarize_report
from tests.operations import test_collection_fmp as fmpfixtures
CUT=fmpfixtures.CUT
from tests.operations import test_collection_parallel_prices as parallelfixtures

class SelectedCompanyTests(unittest.TestCase):
    def setUp(self):
        self.f=fmpfixtures.SelectedFmpTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.stores=self.f.f.stores;self.registry=self.f.f.registry;self.bindings=self.f.f.bindings
        t=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(t.cleanup);self.root=Path(t.name)
    def plan(self,historical=True):
        return prepare(self.stores,self.bindings,cutoff=CUT,historical=historical)
    def test_existing_population_is_excluded_but_current_observation_is_fresh(self):
        selection,work=self.f.work('fmp_analyst_estimates');unit=work.units[0]
        self.f.publish(selection,work.units,unit,[{'symbol':'AAPL','date':'2027-09-30','estimatedRevenueAvg':100}])
        historical=self.plan();current=self.plan(False)
        self.assertIn(scope_key(unit),[tuple(r['key']) for r in historical['retained_scopes']])
        self.assertEqual(len(current['units']),19)
        self.assertEqual(len(historical['units']),18)
        self.assertTrue(historical['identity_gaps']['fmp_statements'])
        self.assertEqual({r['endpoint'] for r in current['units']},
            {'income-statement','balance-sheet-statement','cash-flow-statement','financial-statement-full-as-reported',
             'analyst-estimates','earnings','grades','grades-historical','grades-consensus','price-target',
             'price-target-consensus','price-target-summary','revenue-product-segmentation'})
    def test_busy_identity_reads_wait_but_publication_is_called_once(self):
        from quant_data.operations import collection_fmp as module
        selection,work=self.f.work('fmp_analyst_estimates');unit=work.units[0]
        original=module.selected_fmp_subjects;reads=[];elapsed=[0]
        def read(*args,**kwargs):
            reads.append(1)
            if len(reads) in (1,3):raise StoreUnavailableError('company store is not quiet')
            return original(*args,**kwargs)
        def sleep(value):elapsed[0]+=value
        with patch.object(module,'selected_fmp_subjects',side_effect=read):
            publisher=module.SelectedFmpPublisher(stores=self.stores,registry=self.registry,selection=selection,
                units=work.units,cutoff=CUT,deadline=5,monotonic=lambda:elapsed[0],sleeper=sleep)
            body=b'[{"symbol":"AAPL","date":"2027-09-30","estimatedRevenueAvg":100}]'
            receipt={'unit_id':unit.unit_id,'request_id':unit.request_id,'captured_at':CUT,'status':200,'content_sha256':hashlib.sha256(body).hexdigest()}
            with patch.object(publisher.analyst,'publish',wraps=publisher.analyst.publish) as canonical:
                result=publisher(unit=unit,retained=None,receipt=receipt,body=body,deadline=5,monotonic=lambda:elapsed[0],sleeper=sleep)
                self.assertEqual(result['outcome'],'succeeded');self.assertEqual(canonical.call_count,1)
            with patch.object(publisher.analyst,'publish',side_effect=StoreUnavailableError('company store is not quiet')) as canonical:
                with self.assertRaises(StoreUnavailableError):
                    publisher(unit=unit,retained=None,receipt=receipt,body=body,deadline=5,monotonic=lambda:elapsed[0],sleeper=sleep)
                self.assertEqual(canonical.call_count,1)
        self.assertEqual(elapsed[0],0.5)
    def test_identity_read_coordinates_both_stores_and_releases_before_publication(self):
        from quant_data.operations import collection_fmp as module
        from quant_data.stores import StoreWriteLock
        selection,work=self.f.work('fmp_analyst_estimates');unit=work.units[0]
        original=module.selected_fmp_subjects;read_calls=[]
        def coordinated(*args,**kwargs):
            for role in ('market','company'):
                with self.assertRaises(ConflictError):
                    with StoreWriteLock(self.stores.path(role),timeout_seconds=0):pass
            read_calls.append(1)
            return original(*args,**kwargs)
        with patch.object(module,'selected_fmp_subjects',side_effect=coordinated):
            publisher=module.SelectedFmpPublisher(stores=self.stores,registry=self.registry,selection=selection,
                units=work.units,cutoff=CUT)
            original_publish=publisher.analyst.publish
            def canonical(*args,**kwargs):
                # Both advisory locks have been released before publication;
                # the existing publisher acquires its own company lock.
                for role in ('market','company'):
                    with StoreWriteLock(self.stores.path(role),timeout_seconds=0):pass
                return original_publish(*args,**kwargs)
            body=b'[{"symbol":"AAPL","date":"2027-09-30","estimatedRevenueAvg":100}]'
            receipt={'unit_id':unit.unit_id,'request_id':unit.request_id,'captured_at':CUT,'status':200,'content_sha256':hashlib.sha256(body).hexdigest()}
            with patch.object(publisher.analyst,'publish',side_effect=canonical) as publish:
                result=publisher(unit=unit,retained=None,receipt=receipt,body=body)
            self.assertEqual(result['outcome'],'succeeded');self.assertEqual(publish.call_count,1)
            self.assertEqual(len(read_calls),2)
        elapsed=[0]
        from contextlib import contextmanager
        @contextmanager
        def delayed(*args,**kwargs):
            self.assertEqual(kwargs['timeout_seconds'],1);elapsed[0]=2;yield None
        read=Mock()
        with patch.object(module,'acquire_write_session',side_effect=delayed):
            with self.assertRaises(PublicationDeferred):
                module.read_when_company_quiet(read,stores=self.stores,deadline=1,monotonic=lambda:elapsed[0])
        read.assert_not_called()

    def test_busy_read_wait_keeps_deadline_and_other_store_errors_fail_closed(self):
        from quant_data.operations.collection_fmp import read_when_company_quiet
        elapsed=[0]
        def sleep(value):elapsed[0]+=value
        read=Mock(side_effect=StoreUnavailableError('company store is not quiet'))
        with self.assertRaises(PublicationDeferred):
            read_when_company_quiet(read,deadline=0.1,monotonic=lambda:elapsed[0],sleeper=sleep)
        self.assertEqual(read.call_count,1);self.assertEqual(elapsed[0],0.1)
        read=Mock(side_effect=StoreUnavailableError('company store identity changed'))
        with self.assertRaises(StoreUnavailableError):
            read_when_company_quiet(read,deadline=1,monotonic=lambda:elapsed[0],sleeper=sleep)
        self.assertEqual(read.call_count,1);self.assertEqual(elapsed[0],0.1)
        def slow_read():elapsed[0]=2;return 'too late'
        with self.assertRaises(PublicationDeferred):
            read_when_company_quiet(slow_read,deadline=1,monotonic=lambda:elapsed[0],sleeper=sleep)

    def test_weekday_statement_window_is_bounded_and_history_remains_full(self):
        from quant_data.operations.collection_fmp import STATEMENTS
        current=self.plan(False);history=self.plan(True)
        for unit in current['units']:
            if unit['endpoint'] in STATEMENTS:
                params=dict(unit['parameters'])
                self.assertEqual(params['limit'],'5' if params['period']=='annual' else '20')
        for unit in history['units']:
            if unit['endpoint'] in STATEMENTS:self.assertEqual(dict(unit['parameters'])['limit'],'1000')
        validate_manifest(current,self.stores,require_active=False)
        changed=deepcopy(current)
        unit=next(u for u in changed['units'] if u['endpoint']=='income-statement')
        unit['parameters']=tuple((k,'1000' if k=='limit' else v) for k,v in unit['parameters'])
        with self.assertRaises(ConflictError):validate_manifest(changed,self.stores,require_active=False)

    def test_manifest_roundtrip_and_endpoint_or_cap_tampering(self):
        manifest=json.loads(json.dumps(self.plan()))
        selections,units=validate_manifest(manifest,self.stores,require_active=False)
        self.assertEqual(tuple(s.binding.id for s in selections),BINDINGS)
        for field,value in [('maximum_requests',80001),('maximum_seconds',999999)]:
            wrong=deepcopy(manifest);wrong[field]=value
            with self.assertRaises(Exception):validate_manifest(wrong,self.stores,require_active=False)
        wrong=deepcopy(manifest);wrong['units'][0]['parameters'].append(['page','1'])
        with self.assertRaises(Exception):validate_manifest(wrong,self.stores,require_active=False)
    def fake_acquire(self,body=b'[]',status=200):
        calls=[]
        def acquire(**kw):
            root=kw['root']
            for n in ('attempts','responses','blobs'):private_directory(root/n)
            for u in kw['units']:
                calls.append(u.unit_id)
                atomic(root/'attempts'/(u.unit_id+'.json'),{'unit_id':u.unit_id})
                _retain(root,u,QueueResponse(status,body,'2026-09-09T05:01:00Z'),())
            return {'requests':len(kw['units']),'received_bytes':len(body)*len(kw['units']),
                'uncertain_requests':0,'outcome':'complete' if status==200 else 'provider_failure'}
        return calls,acquire
    def test_finite_empty_results_retain_bytes_and_never_restart(self):
        calls,acquire=self.fake_acquire();manifest=self.plan()
        result=run(root=self.root/'run',stores=self.stores,registry=self.registry,manifest=manifest,
            gate=None,credential='synthetic',evidence_root=self.root/'evidence',require_active=False,
            acquire=acquire,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertEqual(len(calls),19);self.assertEqual(result['pending_units'],0)
        self.assertEqual(result['source_failures'],0)
        self.assertEqual((self.root/'evidence'/ (hashlib.sha256(b'[]').hexdigest()+'.json')).read_bytes(),b'[]')
        with self.assertRaises(ConflictError):
            run(root=self.root/'run',stores=self.stores,registry=self.registry,manifest=manifest,
                gate=None,credential='synthetic',evidence_root=self.root/'evidence',require_active=False,
                acquire=acquire)
        self.assertEqual(len(calls),19)
    def test_invalid_source_cannot_reach_publisher_or_mutate_store(self):
        selection,work=self.f.work('fmp_statements');u=work.units[0]
        from quant_data.operations.collection_fmp import SelectedFmpPublisher
        pub=SelectedFmpPublisher(stores=self.stores,registry=self.registry,selection=selection,units=work.units,cutoff=CUT)
        body=b'{"error":"bad source"}';receipt={'unit_id':u.unit_id,'request_id':u.request_id,
            'content_sha256':hashlib.sha256(body).hexdigest(),'captured_at':CUT,'status':200}
        fake=Mock();before=mutation_fingerprint(self.stores)
        result,child,walk=process_response(unit=u,receipt=receipt,body=body,subject=pub.subjects[u.subject],
            publisher=fake,evidence_root=self.root/'evidence',history=True,walk={})
        self.assertEqual(result['outcome'],'source_rejected');fake.assert_not_called()
        self.assertEqual(before,mutation_fingerprint(self.stores));self.assertIsNone(child)
    def test_parallel_responses_publish_by_capture_time_without_rewriting_it(self):
        from quant_data.operations import selected_company_collection as module
        seen=[];captures={}
        real=module.process_response
        def acquire(**kw):
            root=kw['root'];units=kw['units']
            for name in ('attempts','responses','blobs'):private_directory(root/name)
            for index,unit in enumerate(units):
                capture=f'2026-09-09T05:01:{len(units)-index:02d}.000000Z'
                captures[unit.unit_id]=capture
                atomic(root/'attempts'/(unit.unit_id+'.json'),{'unit_id':unit.unit_id})
                _retain(root,unit,QueueResponse(200,b'[]',capture),())
            return {'requests':len(units),'received_bytes':2*len(units),
                'uncertain_requests':0,'outcome':'complete'}
        def publish(**kw):
            seen.append((kw['unit'].unit_id,kw['receipt']['captured_at']))
            return real(**kw)
        with patch.object(module,'process_response',side_effect=publish):
            result=run(root=self.root/'ordered',stores=self.stores,registry=self.registry,
                manifest=self.plan(),gate=None,credential='synthetic',evidence_root=self.root/'evidence',
                require_active=False,acquire=acquire,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertIsNone(result['stop_reason'])
        self.assertEqual([capture for _,capture in seen],sorted(captures.values()))
        self.assertEqual(dict(seen),captures)

    def test_stale_research_capture_is_explicit_rejection_and_preserves_newer_rows(self):
        from quant_data.operations.collection_fmp import SelectedFmpPublisher
        from quant_data.stores import quiet_immutable_read_connection
        selection,work=self.f.work('fmp_statements')
        unit=next(u for u in work.units if u.endpoint=='income-statement' and dict(u.parameters)['period']=='annual')
        self.f.publish(selection,work.units,unit,[{'symbol':'AAPL','date':'2025-09-30','revenue':100}])
        publisher=SelectedFmpPublisher(stores=self.stores,registry=self.registry,selection=selection,units=work.units,cutoff=CUT)
        body=b'[{"symbol":"AAPL","date":"2025-09-30","revenue":90}]'
        receipt={'unit_id':unit.unit_id,'request_id':unit.request_id,'status':200,
            'captured_at':'2026-09-09T04:29:59Z','content_sha256':hashlib.sha256(body).hexdigest()}
        def rows():
            with quiet_immutable_read_connection(self.stores,'company') as c:
                return {table:[tuple(r) for r in c.execute('SELECT * FROM '+table)]
                    for table in ('company_fmp_research_rows','company_fmp_research_snapshots')}
        before=rows()
        result,child,walk=process_response(unit=unit,receipt=receipt,body=body,subject=publisher.subjects['AAPL'],
            publisher=publisher,evidence_root=self.root/'evidence',history=True,walk={})
        self.assertEqual(result['outcome'],'source_rejected')
        self.assertEqual(result['rejection_stage'],'publication')
        self.assertEqual(result['reason'],'FMP research revisions require increasing capture time')
        self.assertEqual(rows(),before);self.assertIsNone(child);self.assertEqual(walk,{})
        self.assertEqual((self.root/'evidence'/(receipt['content_sha256']+'.json')).read_bytes(),body)
        with self.assertRaisesRegex(ConflictError,'fixture unrelated failure'):
            process_response(unit=unit,receipt=receipt,body=body,subject=publisher.subjects['AAPL'],
                publisher=Mock(side_effect=ConflictError('fixture unrelated failure')),
                evidence_root=self.root/'evidence',history=True,walk={})

    def test_entitlement_preflight_stops_without_broad_population(self):
        calls,acquire=self.fake_acquire(b'{"error":"entitlement"}',402)
        result=run(root=self.root/'run',stores=self.stores,registry=self.registry,manifest=self.plan(),
            gate=None,credential='synthetic',evidence_root=self.root/'evidence',require_active=False,
            acquire=acquire,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertEqual(result['stop_reason'],'endpoint_preflight_failed')
        self.assertEqual(len(calls),19)
    def test_partial_pilot_withholds_continuations_and_checks_late_rejection(self):
        manifest=self.plan();pilot={unit_from_dict(v).unit_id for v in manifest['units']}
        for reject in (False,True):
            with self.subTest(reject=reject):
                calls=[];batches=[]
                def acquire(**kw):
                    root=kw['root'];units=kw['units'];batches.append([u.unit_id for u in units])
                    for n in ('attempts','responses','blobs'):private_directory(root/n)
                    admitted=units[:1] if len(batches)==1 else units
                    for index,u in enumerate(admitted):
                        calls.append(u.unit_id)
                        atomic(root/'attempts'/(u.unit_id+'.json'),{'unit_id':u.unit_id})
                        rows=[{'symbol':'AAPL','date':str(2027-i)+'-09-30','estimatedRevenueAvg':100} for i in range(100)]
                        body=json.dumps(rows).encode() if len(batches)==1 else b'[]'
                        if reject and len(batches)==2 and index==0:body=b'{"error":"invalid source"}'
                        _retain(root,u,QueueResponse(200,body,'2026-09-09T05:01:00Z'),())
                    return {'requests':len(admitted),'received_bytes':10000,
                        'uncertain_requests':0,'outcome':'batch_boundary' if len(batches)==1 else 'complete'}
                result=run(root=self.root/str(reject),stores=self.stores,registry=self.registry,
                    manifest=manifest,gate=None,credential='synthetic',evidence_root=self.root/'evidence',
                    require_active=False,acquire=acquire,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
                self.assertEqual(set(calls[:19]),pilot)
                self.assertTrue(set(batches[1])<=pilot)
                self.assertEqual(result['stop_reason'],'endpoint_preflight_failed' if reject else None)
                self.assertEqual(len(calls),19 if reject else 20)
    def test_reconciled_continuation_keeps_page_progress_and_never_repeats_requests(self):
        manifest=self.plan();initial=tuple(unit_from_dict(v) for v in manifest['units']);first=initial[0]
        batches=[]
        def acquire(**kw):
            root=kw['root'];batches.append(kw['units'])
            for n in ('attempts','responses','blobs'):private_directory(root/n)
            if len(batches)>1:return {'requests':0,'received_bytes':0,'uncertain_requests':0,'outcome':'daily_budget'}
            received=0
            for u in kw['units']:
                rows=[{'symbol':'AAPL','date':str(2027-i)+'-09-30','estimatedRevenueAvg':100} for i in range(100)] if u==first else []
                body=json.dumps(rows).encode();received+=len(body)
                atomic(root/'attempts'/(u.unit_id+'.json'),{'unit_id':u.unit_id})
                _retain(root,u,QueueResponse(200,body,'2026-09-09T05:01:00Z'),())
            return {'requests':len(kw['units']),'received_bytes':received,'uncertain_requests':0,'outcome':'complete'}
        original=self.root/'original'
        result=run(root=original,stores=self.stores,registry=self.registry,manifest=manifest,gate=None,
            credential='synthetic',evidence_root=self.root/'evidence',require_active=False,acquire=acquire,
            clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertEqual(result['requests'],19);self.assertEqual(result['pending_units'],1)
        from quant_data.json_codec import dumps_strict
        checkpoint=json.loads((original/'checkpoint.json').read_text())
        outcomes={p.stem:json.loads(p.read_text()) for p in (original/'outcomes').glob('*.json')}
        state={'manifest_sha256':hashlib.sha256(dumps_strict(manifest,max_bytes=64*1024**2).encode()).hexdigest(),
            'pending':checkpoint['pending'],'walks':checkpoint['walks'],'requests':checkpoint['requests'],
            'received_bytes':checkpoint['received_bytes'],'completed_units':[v['unit'] for v in outcomes.values()],
            'completed':{k:v['result'] for k,v in outcomes.items()}}
        calls,finish=self.fake_acquire()
        def resume(value,path):
            return run(root=self.root/path,stores=self.stores,registry=self.registry,manifest=manifest,gate=None,
                credential='synthetic',evidence_root=self.root/'evidence',require_active=False,acquire=finish,
                continuation_state=value,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        for kind in ('duplicate','unresolved','lost_walk'):
            bad=deepcopy(state)
            if kind=='duplicate':bad['pending'].append(bad['completed_units'][0])
            elif kind=='unresolved':bad['requests']+=1
            else:bad['walks']={}
            with self.subTest(kind=kind),self.assertRaises(ConflictError):resume(bad,kind)
        self.assertFalse(calls)
        finished=resume(state,'continued')
        self.assertEqual(len(calls),1);self.assertNotIn(calls[0],{u.unit_id for u in initial})
        self.assertEqual(finished['requests'],20);self.assertEqual(finished['pending_units'],0)

    def test_continuation_cannot_extend_original_deadline(self):
        calls,acquire=self.fake_acquire()
        result=run(root=self.root/'capped',stores=self.stores,registry=self.registry,manifest=self.plan(),
            gate=None,credential='synthetic',evidence_root=self.root/'evidence',require_active=False,
            acquire=acquire,hard_deadline=20,monotonic=lambda:0,
            clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertFalse(calls);self.assertEqual(result['stop_reason'],'invocation_deadline')

    def test_uncertain_attempt_is_partial_and_never_unattempted_or_retried(self):
        def acquire(**kw):
            root=kw['root'];private_directory(root/'attempts');u=kw['units'][0]
            atomic(root/'attempts'/(u.unit_id+'.json'),{'unit_id':u.unit_id})
            return {'requests':1,'received_bytes':0,'uncertain_requests':1,'outcome':'uncertain_request'}
        result=run(root=self.root/'uncertain',stores=self.stores,registry=self.registry,manifest=self.plan(),
            gate=None,credential='synthetic',evidence_root=self.root/'evidence',require_active=False,
            acquire=acquire,clock=lambda:datetime(2026,9,9,5,2,tzinfo=timezone.utc))
        self.assertEqual(result['requests'],1);self.assertEqual(result['stop_reason'],'uncertain_requests')
        self.assertEqual(result['counts']['partial'],1);self.assertEqual(result['counts']['unattempted'],18)
        self.assertEqual(result['retries'],0)
    def test_activation_requires_exact_population_and_weekday_scope(self):
        manifest=self.plan(False)
        with self.assertRaises(ConflictError):require_activation(self.root,manifest)
        path=self.root/ACTIVATION;private_directory(path.parent)
        value={'contract':'quant_data.selected_company_live_activation.v1','population':population(manifest),
            'cadence':'Mon..Fri 20:00 America/New_York','backfill_audit_verified':True,'bindings':list(BINDINGS)}
        atomic(path,value);require_activation(self.root,manifest)
        changed=deepcopy(manifest);changed['selections'][0]['mapping_id']='different'
        with self.assertRaises(ConflictError):require_activation(self.root,changed)
    def test_estimate_pagination_retains_original_capture_and_stops_on_short_page(self):
        selection,work=self.f.work('fmp_analyst_estimates');unit=work.units[0]
        from quant_data.operations.collection_fmp import SelectedFmpPublisher
        pub=SelectedFmpPublisher(stores=self.stores,registry=self.registry,selection=selection,units=work.units,cutoff=CUT)
        calls=[]
        def publisher(**kw):calls.append(kw['receipt']['captured_at']);return {'outcome':'succeeded'}
        def process(u,rows,walk):
            body=json.dumps(rows).encode();receipt={'unit_id':u.unit_id,'request_id':u.request_id,
                'content_sha256':hashlib.sha256(body).hexdigest(),'captured_at':CUT,'status':200}
            return process_response(unit=u,receipt=receipt,body=body,subject=pub.subjects[u.subject],
                publisher=publisher,evidence_root=self.root/'evidence',history=True,walk=walk)
        rows=[{'symbol':'AAPL','date':str(2027-i)+'-09-30','estimatedRevenueAvg':100} for i in range(100)]
        first,child,walk=process(unit,rows,{})
        self.assertEqual(dict(child.parameters)['page'],'1');self.assertEqual(first['pagination'],'continuation_required')
        second,child,walk=process(child,[],walk)
        self.assertIsNone(child);self.assertEqual(second['pagination'],'short_page_history_unverified')
        self.assertEqual(calls,[CUT,CUT])

    def test_fetch_summary_exposes_numeric_counts_only(self):
        counts={'unit':'steps','successful':10,'failed':1,'partial':0,'skipped':2,'unattempted':3}
        self.assertEqual(summarize_report('quant-data-selected-company-refresh.timer',{'counts':counts,'secret':'not emitted'}),counts)

class CompanyTransportTests(unittest.TestCase):
    def setUp(self):
        self.f=parallelfixtures.ParallelPriceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
    def units(self,current=False):
        return tuple(replace(u,collection='earnings_dates',endpoint='earnings',
            parameters=(('limit','1000'),('symbol',u.subject)),
            mode='incremental' if current else 'historical_backfill',
            observation_window='2026-09-10T14:00:00Z') for u in self.f.units(3))
    def call(self,units):
        return acquire_company_batch(root=self.f.root/'company',gate=self.f.gate,units=units,credential='synthetic',
            deadline=100,max_total_bytes=100000,ticket_factory=self.f.factory(),clock=self.f.clock,
            monotonic=lambda:self.f.elapsed,sleeper=self.f.sleep)
    def test_current_uses_maintenance_allowance_and_original_spacing(self):
        state=self.f.state();state['usage']['2026-09-10']=90;atomic(self.f.gate.root/'allowance.json',state,replace=True)
        result=self.call(self.units(True));self.assertEqual(result['requests'],3)
        self.assertEqual(self.f.state()['usage']['2026-09-10'],93)
        self.assertTrue(all(b-a>=0.12-1e-9 for a,b in zip(self.f.starts,self.f.starts[1:])))
    def test_history_preserves_maintenance_reserve_and_rejects_unrelated_scope(self):
        state=self.f.state();state['usage']['2026-09-10']=90;atomic(self.f.gate.root/'allowance.json',state,replace=True)
        self.assertEqual(self.call(self.units())['requests'],0)
        with self.assertRaises(ValidationError):self.call(self.f.units(1))
        with self.assertRaises(ValidationError):
            acquire_batch(root=self.f.root/'wrong',gate=self.f.gate,units=self.units(),credential='synthetic',deadline=100,max_total_bytes=10000)
