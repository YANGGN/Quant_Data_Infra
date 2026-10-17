"""Raw reference identity, basis, missing IV and replay boundaries; no network."""
import copy, hashlib, json, unittest
from quant_data.options.model import build_capture, canonical_json
from quant_data.options.raw_reference import CONTRACT, ENDPOINT, prepare_raw_references, validate_raw_reference
from quant_data.options.monitor.history import read_batch
from tests.options.test_theta_compact import temporary_store
from datetime import datetime, timezone

DAY='2016-01-04'

def evidence():
    row={'symbol':'USO','date':DAY,'adjOpen':11.26,'adjHigh':11.42,
         'adjLow':10.82,'adjClose':10.98,'volume':36325824}
    body=json.dumps([row]).encode()
    receipt={'endpoint':ENDPOINT,'status':200,'attempts':1,
        'params':{'symbol':'USO','from':DAY,'to':DAY},'captured_at':'2026-10-15T02:46:00+00:00',
        'response_bytes':len(body),'response_sha256':hashlib.sha256(body).hexdigest()}
    return body,receipt

def reference():
    body,receipt=evidence()
    return prepare_raw_references(body,receipt,artifact='USO.json')[DAY]

def rows():
    # Monthly-only chain: no short-dated pairs to validate an adjusted proxy.
    return [{'symbol':'USO','expiration':'2016-02-19','strike':str(strike),'right':right,
             'created':DAY+'T17:20:00-05:00','last_trade':DAY+'T15:59:00-05:00',
             'bid':'1','ask':'1.1','volume':10,'count':2}
            for strike in (10,11,12) for right in ('CALL','PUT')]

def capture(ref=None, source=None):
    receipt={'method':'option_history_eod','captured_at':'2026-10-15T02:24:00+00:00',
        'response_sha256':'a'*64,'response_bytes':50,'outcome':'success','params':{}}
    return build_capture(DAY,source if source is not None else rows(),[],[receipt],
        previous_session='2015-12-31',spot_reference=reference() if ref is None else ref,symbol='USO')

class RawReferenceTests(unittest.TestCase):
    def test_raw_price_is_retained_without_rescaling_and_missing_iv_stays_missing(self):
        c=capture()
        self.assertEqual(c['spot'],'10.98')
        self.assertEqual(c['policy']['spot_basis'],'retained_fmp_non_split_adjusted_close')
        self.assertEqual(c['policy']['greeks_state'],'unavailable')
        self.assertTrue(all(d['implied_vol'] is None and d['iv_quality']=='excluded' for d in c['details']))
        self.assertEqual(c['underlying_reference']['observation']['precision'],'date')
        self.assertEqual(c['captured_at'],'2026-10-15T02:46:00+00:00')

    def test_full_eod_cannot_be_relabelled(self):
        body,r=evidence();r['endpoint']='/stable/historical-price-eod/full'
        with self.assertRaisesRegex(ValueError,'raw_reference_receipt'):
            prepare_raw_references(body,r,artifact='test')

    def test_changed_body_or_receipt_hash_fails(self):
        body,r=evidence()
        with self.assertRaisesRegex(ValueError,'body_hash'):
            prepare_raw_references(body+b' ',r,artifact='test')
        r['response_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'body_hash'):
            prepare_raw_references(body,r,artifact='test')

    def test_wrong_root_date_and_future_capture_binding_fail(self):
        for symbol,session in [('UNG',DAY),('USO','2016-01-05')]:
            with self.subTest(symbol=symbol,session=session),self.assertRaises(ValueError):
                validate_raw_reference(reference(),symbol=symbol,session=session)
        body,r=evidence();r['captured_at']='2016-01-04T08:00:00+00:00'
        with self.assertRaisesRegex(ValueError,'raw_reference_date'):
            prepare_raw_references(body,r,artifact='test')

    def test_tampered_value_basis_row_and_provenance_fail(self):
        mutations=[lambda r:r.update(value='87.84'),
            lambda r:r['metadata'].update(adjustment_status='split_adjusted_excluding_distributions'),
            lambda r:r['row'].update(adjClose=11),lambda r:r.update(row_sha256='0'*64),
            lambda r:r.update(artifact_sha256='0'*64),lambda r:r.update(contract='unknown'),
            lambda r:r['observation'].update(precision='datetime')]
        for change in mutations:
            r=reference();change(r)
            with self.subTest(change=change),self.assertRaises(ValueError):capture(r)

    def test_invalid_bar_and_zero_trading_fail(self):
        for patch in ({'adjClose':100},{'adjLow':0},{'adjOpen':float('nan')},{'volume':0}):
            body,r=evidence();row=json.loads(body)[0];row.update(patch)
            body=json.dumps([row]).encode();r.update(response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest())
            with self.subTest(patch=patch),self.assertRaisesRegex(ValueError,'ohlc'):
                prepare_raw_references(body,r,artifact='test')

    def test_duplicate_or_out_of_window_response_fails(self):
        for change in ('duplicate','outside'):
            body,r=evidence();data=json.loads(body)
            if change=='duplicate':data*=2
            else:data[0]['date']='2015-12-31'
            body=json.dumps(data).encode();r.update(response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest())
            with self.subTest(change=change),self.assertRaises(ValueError):prepare_raw_references(body,r,artifact='test')

    def test_adjusted_proxy_still_requires_original_parity_check(self):
        old={'value':'10.98','metadata':{'provider_symbol':'USO'},'observation':{'period_start':DAY}}
        with self.assertRaisesRegex(ValueError,'fallback_reference_not_crosschecked'):capture(old)

    def test_raw_reference_does_not_rescue_invalid_greek_time(self):
        source=rows();source[0].update(implied_vol='.2',underlying_price='10.98',underlying_timestamp='2016-01-05T16:00:00-05:00')
        with self.assertRaisesRegex(ValueError,'invalid_greek_reference_time'):capture(source=source)

    def test_raw_reference_does_not_rescue_future_option_time(self):
        source=rows();source[0]['last_trade']='2016-01-05T16:00:00-05:00'
        with self.assertRaisesRegex(ValueError,'future_eod_time'):capture(source=source)

    def test_replay_and_dashboard_keep_price_only_missingness(self):
        c=capture();temp,store=temporary_store()
        with temp:
            store.initialize();first=store.publish(c,missing_only=True)
            self.assertEqual(first['outcome'],'published')
            self.assertEqual(store.publish(c)['canonical_changes'],0)
            self.assertEqual(store.publish(c,missing_only=True)['canonical_changes'],0)
            points,_=read_batch(store.root,0,datetime(2026,10,15,tzinfo=timezone.utc))
            self.assertEqual(len(points),1)
            self.assertEqual(points[0]['symbol'],'USO')
            self.assertIsNone(points[0]['atm_iv_30'])
            self.assertEqual(points[0]['iv_contracts'],0)
            self.assertIn('missing_iv_coverage',points[0]['quality_flags'])

    def test_order_replay_is_stable_and_reference_change_is_visible(self):
        c=capture();self.assertEqual(c['semantic_sha256'],capture(source=list(reversed(rows())))['semantic_sha256'])
        body,r=evidence();data=json.loads(body);data[0]['adjClose']=11
        body=json.dumps(data).encode();r.update(response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest())
        ref=prepare_raw_references(body,r,artifact='other')[DAY]
        self.assertNotEqual(c['semantic_sha256'],capture(ref)['semantic_sha256'])
