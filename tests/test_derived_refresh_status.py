"""Status must recognize unchanged checks without rewriting facts or run receipts."""
from collections import Counter
from dataclasses import asdict
import hashlib,json,sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from quant_data import derived_refresh_status as status
from quant_data.operations.collection_plan import AcquisitionUnit
from quant_data.json_codec import dumps_strict

AT='2026-09-22T10:30:42.119554Z'
FINISH='2026-09-22T10:43:57.118194Z'
NAME='20260922T103042119554Z.sqlite'
OLD='2026-09-11T12:00:00Z'
RECENT='2026-09-22T01:00:00Z'

class DerivedStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)
        self.root=self.project/'exports/forward-pe';self.root.mkdir(parents=True)
        status._members.cache_clear();status._collection_index.cache_clear()
        self.rows=[('i','AAA','stale_inputs','Older or unchecked estimates, earnings inputs; source cutoffs retained.',OLD,OLD)]

    def build(self):
        receipt={'artifact':NAME,'state':'complete_with_gaps','outcomes':dict(Counter(r[2] for r in self.rows)),
                 'symbols':len(self.rows),'target_session':'2026-09-21','completed_at':FINISH,'elapsed_seconds':826.775}
        with sqlite3.connect(self.root/NAME) as db:
            db.executescript('CREATE TABLE metadata(key TEXT,value_json TEXT); CREATE TABLE source_inputs(instrument_id TEXT,symbol TEXT); CREATE TABLE refresh_members(instrument_id TEXT,outcome TEXT,issue TEXT,estimate_capture TEXT,earnings_capture TEXT);')
            for r in self.rows:
                db.execute('INSERT INTO source_inputs VALUES (?,?)',r[:2])
                db.execute('INSERT INTO refresh_members VALUES (?,?,?,?,?)',(r[0],*r[2:]))
            values={'contract':'announcement_forward_pe.v1','is_point_in_time':False,'cutoff':AT,'refresh':receipt}
            db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in values.items()])
        (self.root/(NAME+'.receipt.json')).write_text(json.dumps(receipt))
        (self.root/'current.json').write_text(json.dumps({'artifact':NAME}))
        return receipt

    def checks(self):
        return {('i','AAA',e):{'at':RECENT,'outcome':'unchanged'} for e in ('earnings','analyst-estimates')}

    def test_unchanged_checks_clear_only_freshness_warnings_and_preserve_every_file(self):
        self.build();before={p:p.read_bytes() for p in self.root.iterdir()}
        with patch.object(status,'source_checks',return_value=self.checks()):
            view=status.presentation(self.root,started_at='2026-09-22T06:30:42.103233-04:00',finished_at=FINISH)
            self.assertEqual((view['calculated'],view['current_inputs'],view['freshness_warnings'],view['unchanged_checks']),(1,1,0,1))
            self.assertEqual(view['recorded_freshness_warnings'],1)
            self.assertTrue(status.ticker_presentation(self.root,'AAA')['unchanged'])
            self.assertEqual(status.ticker_presentation(self.root,'AAA')['state'],'current')
            self.assertEqual((view['stale_inputs'],view['unconfirmed_inputs']), (0,0))
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.iterdir()})

    def test_missing_failed_future_or_wrong_identity_checks_do_not_clear_warning(self):
        self.build()
        for checks in ({},{k:{'at':RECENT,'outcome':'failed'} for k in self.checks()},{('other','AAA','earnings'):{'at':RECENT,'outcome':'unchanged'}},
                       {k:{'at':'2026-09-23T01:00:00Z','outcome':'unchanged'} for k in self.checks()}):
            with self.subTest(checks=checks),patch.object(status,'source_checks',return_value=checks):
                view=status.presentation(self.root)
                self.assertEqual((view['freshness_warnings'],view['stale_inputs'],view['unconfirmed_inputs']),(1,0,1))
                self.assertEqual(status.ticker_presentation(self.root,'AAA')['state'],'unconfirmed')

    def test_basis_warning_and_unfinished_work_are_not_reclassified_as_fresh(self):
        self.rows=[('i','AAA','stale_inputs','Saved price basis could not be verified; historical prices were preserved.',OLD,OLD),
                   ('ii','BBB','waiting_inputs','Unavailable',OLD,OLD),('iii','CCC','catchup_pending','Pending',OLD,OLD)]
        self.build()
        with patch.object(status,'source_checks',return_value=self.checks()):
            view=status.presentation(self.root)
        self.assertEqual((view['calculated'],view['freshness_warnings'],view['waiting_inputs'],view['catchup_pending']),(1,1,1,1))
        self.assertEqual((view['stale_inputs'],view['unconfirmed_inputs'],view['input_warnings']),(0,0,1))

    def test_identical_fractional_receipts_match_but_changed_receipts_fail_closed(self):
        receipt=self.build()
        with patch.object(status,'source_checks',return_value=self.checks()):
            self.assertEqual(status.presentation(self.root)['current_inputs'],1)
            receipt['elapsed_seconds']=826.776
            (self.root/(NAME+'.receipt.json')).write_text(json.dumps(receipt))
            self.assertEqual(status.presentation(self.root),{})

    def test_confirmed_newer_change_is_stale_even_after_a_later_unchanged_check(self):
        self.build()
        checks=self.checks()
        checks[('i','AAA','earnings')]['changed_at']='2026-09-21T23:00:00Z'
        with patch.object(status,'source_checks',return_value=checks):
            view=status.presentation(self.root)
            self.assertEqual((view['current_inputs'],view['stale_inputs'],view['unconfirmed_inputs']),(0,1,0))
            self.assertEqual(status.ticker_presentation(self.root,'AAA')['state'],'stale')

    def test_recent_capture_does_not_hide_a_newer_confirmed_change(self):
        self.rows=[('i','AAA','current','','2026-09-21T10:00:00Z','2026-09-21T10:00:00Z')]
        self.build()
        checks=self.checks()
        checks[('i','AAA','earnings')]['changed_at']='2026-09-21T23:00:00Z'
        with patch.object(status,'source_checks',return_value=checks):
            self.assertEqual(status.presentation(self.root)['stale_inputs'],1)

    def test_missing_capture_or_unproven_reuse_cannot_become_current(self):
        self.rows=[('i','AAA','stale_inputs',next(iter(status.FRESHNESS_ISSUES)),None,OLD)]
        self.build()
        with patch.object(status,'source_checks',return_value=self.checks()):
            self.assertEqual(status.presentation(self.root)['unconfirmed_inputs'],1)
        row=dict(zip(('instrument_id','symbol','outcome','issue','estimate_capture','earnings_capture'),self.rows[0]))
        row['estimate_capture']=OLD
        for outcome in ('reused','succeeded'):
            checks={k:dict(v,outcome=outcome) for k,v in self.checks().items()}
            self.assertEqual(status._input_state(row,checks,'2026-09-21',status._instant(AT)),('unconfirmed',False))

    def test_future_change_does_not_relabel_historical_inputs(self):
        self.build()
        checks={k:dict(v,at='2026-09-23T01:00:00Z',changed_at='2026-09-23T01:00:00Z') for k,v in self.checks().items()}
        with patch.object(status,'source_checks',return_value=checks):
            self.assertEqual((status.presentation(self.root)['stale_inputs'],status.presentation(self.root)['unconfirmed_inputs']),(0,1))

    def test_receipt_matching_and_publication_cutoff_fail_closed(self):
        self.build()
        self.assertEqual(status.presentation(self.root,started_at='2026-09-22T10:31:42Z'),{})
        self.assertEqual(status.presentation(self.root,finished_at=AT),{})
        self.assertEqual(status.presentation(self.root,cutoff=AT),{})

    def test_pruned_artifact_retains_publication_evidence_without_inventing_checks(self):
        self.build();(self.root/NAME).unlink()
        view=status.presentation(self.root)
        self.assertEqual((view['calculated'],view['freshness_warnings'],view['evidence']),(1,1,'publication_receipt'))
        self.assertEqual(status.ticker_presentation(self.root,'AAA'),{})

    def test_ticker_projection_stays_with_its_selected_artifact_when_pointer_moves(self):
        self.build()
        (self.root/'current.json').write_text(json.dumps({'artifact':'20260923T000000000000Z.sqlite'}))
        with patch.object(status,'source_checks',return_value=self.checks()):
            self.assertTrue(status.ticker_presentation(self.root,'AAA',artifact=NAME)['unchanged'])
            self.assertEqual(status.ticker_presentation(self.root,'AAA',artifact='../outside'),{})

    def test_calendar_summary_does_not_open_members_or_reconcile_source_checks(self):
        self.build()
        with patch.object(status, '_members', side_effect=AssertionError('No full calendar scan')), patch.object(status, 'source_checks', side_effect=AssertionError('No source scan')):
            view = status.presentation(self.root, detailed=False)
        self.assertEqual((view['calculated'], view['freshness_warnings'], view['evidence']), (1, 1, 'publication_receipt'))

    def test_ui_snapshot_hash_pins_projection_after_current_pointer_moves(self):
        self.build()
        (self.root/'current.json').write_text(json.dumps({'artifact':'20260923T000000000000Z.sqlite'}))
        snapshot = hashlib.sha256(NAME.encode()).hexdigest()
        with patch.object(status, 'source_checks', return_value=self.checks()):
            self.assertTrue(status.ticker_snapshot_presentation(self.root, 'AAA', snapshot)['unchanged'])
            self.assertEqual(status.ticker_snapshot_presentation(self.root, 'AAA', '0'*64), {})
            self.assertEqual(status.ticker_snapshot_presentation(self.root, 'AAA', '../outside'), {})

    def test_malformed_or_symlinked_evidence_fails_closed(self):
        self.build();receipt=self.root/(NAME+'.receipt.json')
        target=self.project/'outside.json';target.write_bytes(receipt.read_bytes());receipt.unlink();receipt.symlink_to(target)
        self.assertEqual(status.presentation(self.root),{})

    def collection(self, *, outcome='unchanged', captured=RECENT, instrument='i', complete=True):
        directory=self.project/'data/.operations/collection/company-selected/current/2026-09-21'
        directory.mkdir(parents=True);(directory/'outcomes').mkdir()
        unit=AcquisitionUnit('earnings_dates','fmp','earnings','AAA',(('limit','1000'),('symbol','AAA')),
                             'incremental','2026-09-22T00:00:00Z',('AAA',),'a'*64)
        raw=json.loads(json.dumps(asdict(unit)))
        manifest={'created_at':'2026-09-22T00:00:00Z','selections':[{'binding':{'id':'earnings_dates'},
                  'scope_sha256':'a'*64,'subjects':[{'provider_symbol':'AAA','status':'resolved','instrument_id':instrument,'cik':'0000000001'}]}]}
        result={'outcome':outcome,'raw_rows':1}
        checkpoint={'manifest_sha256':hashlib.sha256(dumps_strict(manifest,max_bytes=64*1024*1024).encode()).hexdigest(),
                    'pending_first':[],'pending_retry':[],'units':{unit.unit_id:raw},'completed':{unit.unit_id:result}}
        files={'started.json':{'at':manifest['created_at']},'manifest.json':manifest,'checkpoint.json':checkpoint}
        if complete:files['result.json']={'pending_units':0,'outcome':'processed_with_gaps','at':'2026-09-22T03:00:00Z'}
        for name,value in files.items():(directory/name).write_text(json.dumps(value))
        proof={'unit':raw,'result':result,'captured_at':captured,'response_sha256':'b'*64}
        path=directory/'outcomes'/(unit.unit_id+'-0.json');path.write_text(json.dumps(proof))
        return directory,path

    def test_completed_unchanged_source_check_is_identity_and_time_bound(self):
        self.collection()
        self.assertEqual(status.source_checks(self.project,AT),{('i','AAA','earnings'):{'at':RECENT,'outcome':'unchanged'}})
        self.assertEqual(status.source_checks(self.project,'2026-09-21T22:00:00Z'),{})
        self.assertEqual(status.source_checks(self.project,'2026-09-22T02:00:00Z'),{})

    def test_failed_source_and_unfinished_batch_cannot_establish_freshness(self):
        self.collection(outcome='source_rejected',complete=False)
        self.assertEqual(status.source_checks(self.project,AT),{})

    def test_forged_response_or_future_receipt_is_rejected(self):
        directory,path=self.collection(captured='2026-09-23T01:00:00Z')
        self.assertEqual(status.source_checks(self.project,AT),{})
        status._collection_index.cache_clear()
        proof=json.loads(path.read_text());proof['captured_at']=RECENT;proof['unit']['subject']='OTHER';path.write_text(json.dumps(proof))
        self.assertEqual(status.source_checks(self.project,AT),{})

    def test_complete_collection_is_accepted_and_empty_check_is_not_current_evidence(self):
        directory,path=self.collection()
        result_path=directory/'result.json'
        result=json.loads(result_path.read_text());result['outcome']='complete';result_path.write_text(json.dumps(result))
        self.assertEqual(status.source_checks(self.project,AT),{('i','AAA','earnings'):{'at':RECENT,'outcome':'unchanged'}})
        proof=json.loads(path.read_text());proof['result']['raw_rows']=0
        path.write_text(json.dumps(proof))
        checkpoint_path=directory/'checkpoint.json'
        checkpoint=json.loads(checkpoint_path.read_text())
        checkpoint['completed'][next(iter(checkpoint['completed']))]=proof['result']
        checkpoint_path.write_text(json.dumps(checkpoint))
        # Completed production evidence is immutable; this fixture rewrites it.
        status._collection_index.cache_clear()
        self.assertEqual(status.source_checks(self.project,AT),{})

    def test_changed_publication_evidence_survives_a_later_unchanged_check(self):
        directory,path=self.collection()
        proof=json.loads(path.read_text())
        checkpoint_path=directory/'checkpoint.json'
        checkpoint=json.loads(checkpoint_path.read_text())
        changed_raw=dict(proof['unit'],parameters=[['limit','999'],['symbol','AAA']])
        from quant_data.operations.selected_company_collection import unit_from_dict
        changed=unit_from_dict(changed_raw)
        result=dict(outcome='succeeded',raw_rows=1,written_count=1)
        checkpoint['units'][changed.unit_id]=changed_raw
        checkpoint['completed'][changed.unit_id]=result
        checkpoint_path.write_text(json.dumps(checkpoint))
        earlier='2026-09-22T00:30:00Z'
        (directory/'outcomes'/(changed.unit_id+'-0.json')).write_text(json.dumps(
            dict(unit=changed_raw,result=result,captured_at=earlier,response_sha256='c'*64)))
        evidence=status.source_checks(self.project,AT)[('i','AAA','earnings')]
        self.assertEqual(evidence,dict(at=RECENT,outcome='unchanged',changed_at=earlier))

    def test_manifest_checkpoint_mismatch_is_not_accepted(self):
        directory,_=self.collection();path=directory/'manifest.json'
        manifest=json.loads(path.read_text());manifest['created_at']='2026-09-21T23:59:59Z';path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):status.source_checks(self.project,AT)

if __name__=='__main__':unittest.main()
