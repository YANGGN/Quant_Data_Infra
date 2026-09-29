from copy import deepcopy
from datetime import timedelta
import json
import unittest
from quant_data.company.forward_pe_store import open_current, day_row, window_detail
from quant_data.operations.derived_refresh import refresh
from tests.operations import test_derived_refresh as base_tests
from tests.company.test_forward_pe import SESSIONS
from tests.company.test_forward_pe_reviews import review


class ReviewRepairTests(base_tests.DailyRefreshTests):
    # Reuse isolated setup and artifact helpers, not the inherited test matrix.
    def prepare(self):
        other=deepcopy(self.inputs);other.update(symbol='TWO',instrument_id='instrument2')
        self.items.append(other)
        self.inputs['statements']=[]
        self.build();self.run_refresh()
        self.before=self.row('2023-12-11')
        self.old=self.row('2023-12-08')
        with open_current(self.root) as (_,db,base,meta):self.other=day_row(db,base,'instrument2','2023-12-11')
        self.inputs['reviewed_periods']=[review(self.inputs)]
        self.inputs['prices'][-1].update(close_value='99999',version_id='new-price')

    def repair(self, **kwargs):
        return refresh(self.root,now=kwargs.pop('now',self.now+timedelta(hours=1)),
            selected=[self.inputs],sessions=SESSIONS,read=kwargs.pop('read',self.read),repair_symbols={'ODD'},**kwargs)

    def test_review_repairs_only_latest_selected_day_preserves_price_and_replays(self):
        self.prepare();r=self.repair()
        self.assertEqual(r['repaired_periods'],1);self.assertEqual(r['appended_rows'],0)
        self.assertEqual(r['repaired_prices'],0);self.assertEqual(r['symbols'],1)
        fixed=self.row('2023-12-11')
        self.assertEqual(fixed[4],14);self.assertIsNotNone(fixed[5])
        self.assertEqual((fixed[3],fixed[8]),(self.before[3],self.before[8]))
        self.assertEqual(self.row('2023-12-08'),self.old)
        with open_current(self.root) as (_,db,base,meta):
            self.assertEqual(day_row(db,base,'instrument2','2023-12-11'),self.other)
            self.assertEqual(window_detail(db,base,fixed[7])['observation_kind'],'reviewed_period_correction')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.inputs['estimates'][1]['payload_json']=json.dumps({'epsAvg':999})
        again=self.repair(now=self.now+timedelta(hours=2))
        self.assertEqual(again['changed_rows'],0);self.assertEqual(self.row('2023-12-11'),fixed)

    def test_missing_applicable_review_preserves_pointer(self):
        self.prepare();self.inputs['reviewed_periods']=[]
        before=(self.root/'current.json').read_text()
        with self.assertRaises(ValueError):self.repair()
        self.assertEqual((self.root/'current.json').read_text(),before)

    def test_fills_only_missing_latest_session_without_backfilling_history(self):
        other=deepcopy(self.inputs);other.update(symbol='TWO',instrument_id='instrument2')
        self.items.append(other);self.inputs['statements']=[];self.build()
        # The target completed Dec 8 but missed Dec 11; another symbol advanced the artifact.
        self.run_refresh(sessions=SESSIONS[:-1])
        def except_target(subject,*args):
            if subject['symbol']=='ODD':raise OSError('fixture unavailable')
            return self.read(subject,*args)
        self.run_refresh(now=self.now+timedelta(minutes=30),read=except_target)
        self.assertIsNone(self.row('2023-12-11'))
        prior=self.row('2023-12-08');self.inputs['reviewed_periods']=[review(self.inputs)]
        result=self.repair()
        self.assertEqual(result['appended_rows'],1);self.assertEqual(result['repaired_periods'],1)
        self.assertEqual(self.row('2023-12-11')[4],14);self.assertEqual(self.row('2023-12-08'),prior)

    def test_mismatched_cohort_is_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            refresh(self.root,now=self.now,selected=[self.inputs],sessions=SESSIONS,read=self.read,repair_symbols={'TWO'})

# Inherited cases belong to their existing module and need not run a second time.
for name in list(vars(base_tests.DailyRefreshTests)):
    if name.startswith('test_') and name not in vars(ReviewRepairTests):
        setattr(ReviewRepairTests,name,None)
