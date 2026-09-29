from copy import deepcopy
import json
import unittest
from quant_data.company.forward_pe import make_windows
from quant_data.company.forward_pe_reviews import validate_catalog, CONTRACT, load_catalog
from tests.company.test_forward_pe import fixture, SESSIONS, CUTOFF


def review(inputs):
    event=inputs['earnings'][0]
    return dict(instrument_id=inputs['instrument_id'],symbol=inputs['symbol'],
        natural_identity=event['natural_identity'],source_event_date=event['source_event_date'],
        reviewed_version_id=event['observation_version_id'],announcement_date=event['source_event_date'],
        reported_period_end='2023-10-31',fiscal_year=2024,fiscal_quarter=1,
        confidence='most_likely',reporting_kind='quarter',reviewed_at='2026-09-19T00:00:00Z',
        source_urls=['https://example.test/issuer-release'],finding='Explicit offline fixture evidence')


class ReviewedPeriodsTests(unittest.TestCase):
    def inputs(self):
        data=fixture();data['statements']=[];data['transcripts']=[]
        data['reviewed_periods']=[review(data)]
        return data

    def test_explicit_review_selects_next_four_estimates_without_actual_statements(self):
        data=self.inputs();w=make_windows(data,SESSIONS)[-1]
        self.assertEqual(w['forward_eps'],'14')
        self.assertEqual(w['components'][0]['period_end'],'2024-01-31')
        self.assertIn('inferred_reporting_period',w['flags'])
        self.assertEqual(w['mapping_evidence']['review']['reviewed_version_id'],'a1')

    def test_identity_event_and_review_cutoff_are_required(self):
        for field,value in [('instrument_id','other'),('symbol','OTHER'),('natural_identity','other'),
                            ('source_event_date','2023-12-04'),('reviewed_at','2026-09-21T00:00:00Z')]:
            with self.subTest(field=field):
                data=self.inputs();data['reviewed_periods'][0][field]=value
                self.assertEqual(make_windows(data,SESSIONS)[-1]['status'],'unmapped_announcement')

    def test_no_ticker_fallback_on_new_report_and_null_actuals_never_advance(self):
        data=self.inputs();event=deepcopy(data['earnings'][0])
        event.update(natural_identity='next',observation_version_id='next',source_event_date='2023-12-07',
                     event_precision='date',payload_json=json.dumps({'epsActual':1}))
        data['earnings'].append(event)
        self.assertEqual(make_windows(data,SESSIONS)[-1]['status'],'unmapped_announcement')
        event['payload_json']='{}'
        self.assertEqual(make_windows(data,SESSIONS)[-1]['mapping'],'reviewed_reporting_period')

    def test_corrected_announcement_stays_date_only_and_keeps_original(self):
        data=self.inputs();data['reviewed_periods'][0]['announcement_date']='2023-12-07'
        w=make_windows(data,SESSIONS)[-1]
        self.assertEqual((w['announcement'],w['effective_date']),('2023-12-07','2023-12-08'))
        self.assertEqual(w['mapping_evidence']['source_announcement'],'2023-12-05')

    def test_half_year_boundary_uses_quarter_estimates_never_actual_eps(self):
        data=self.inputs();data['reviewed_periods'][0]['reporting_kind']='half_year'
        data['earnings'][0]['payload_json']=json.dumps({'epsActual':999})
        self.assertEqual(make_windows(data,SESSIONS)[-1]['forward_eps'],'14')

    def test_unreleased_result_and_old_fiscal_calendar_remain_blocked(self):
        for reason in ('financial_results_not_released','fiscal_calendar_transition'):
            data=self.inputs();data['reviewed_periods'][0]['blocking_reason']=reason
            w=make_windows(data,SESSIONS)[-1]
            self.assertFalse(w['ratio_allowed']);self.assertIsNone(w['forward_eps'])
            self.assertIn(reason,w['flags'])

    def test_nearby_statement_period_does_not_create_fifth_stub_quarter(self):
        data=fixture();data['reviewed_periods']=[review(data)]
        data['statements'][0]['period_end']='2023-10-29'
        self.assertEqual(make_windows(data,SESSIONS)[-1]['forward_eps'],'14')

    def test_duplicate_reviews_and_future_reporting_periods_rejected(self):
        r=review(fixture())
        with self.assertRaises(ValueError):validate_catalog(dict(contract=CONTRACT,rows=[r,r]))
        r['reported_period_end']='2027-01-01'
        with self.assertRaises(ValueError):validate_catalog(dict(contract=CONTRACT,rows=[r]))

    def test_production_catalog_is_complete_unique_and_bounded(self):
        rows=load_catalog()['rows']
        self.assertEqual(len(rows),208);self.assertEqual(len({r['symbol'] for r in rows}),207)
        self.assertEqual({r['symbol'] for r in rows if r.get('blocking_reason')},{'FDXF','VFS'})
