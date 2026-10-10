import importlib.util,json,tempfile,unittest
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from quant_data.options.monitor.baseline import aggregate_expiration
from quant_data.options.monitor.baseline_probe import (aggregate_vectorized,split_sessions,compare_engines,
    Probe,ProbeTransport,SYMBOL,SESSIONS)


def row(session=SESSIONS[0],expiry="2029-01-19",**changes):
    return {"symbol":SYMBOL,"expiration":expiry,"strike":100,"right":"CALL",
        "timestamp":session+"T09:30:00-04:00","volume":10,**changes}

@unittest.skipUnless(importlib.util.find_spec("numpy"),"Optional experimental NumPy dependency unavailable")
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.output=self.root/"probe";self.output.mkdir()

    def compare(self,rows,close=960):
        results=[]
        for fn in (aggregate_expiration,aggregate_vectorized):
            try:results.append(("ok",fn(rows,SYMBOL,SESSIONS[0],"2029-01-19",close)))
            except (ValueError,KeyError,TypeError) as exc:results.append((type(exc).__name__,str(exc)))
        self.assertEqual(results[0],results[1]);return results[0]

    def test_valid_vector_parity_and_large_integer_totals(self):
        self.compare([row(),row(right="PUT"),row(timestamp=SESSIONS[0]+"T09:35:00-04:00")])
        result=self.compare([row(strike=100+i,volume=2**62) for i in range(6)])
        self.assertEqual(result[1][0][1],6*2**62)
        self.compare([row(volume=0)])
        self.compare([row(timestamp=SESSIONS[0]+"T13:30:00+00:00")])
        self.compare([row(),row(timestamp=SESSIONS[0]+"T13:00:00-04:00",volume=None)],close=780)

    def test_validation_parity(self):
        invalid=[[],[row(),row()], [row(strike=100.000000001),row(strike=100.000000002)],
            [row(symbol="SPY")],[row(expiry="2029-02-16")],[row(right="BAD")],[row(strike=-1)],
            [row(volume=None)],[row(volume=True)],[row(volume=float("nan"))],[row(volume=float("inf"))],
            [row(volume=-1)],[row(volume=1.5)],[row(timestamp="bad")],
            [row(timestamp=SESSIONS[0]+"T09:30:00")],[row(timestamp=SESSIONS[0]+"T09:31:00-04:00")],
            [row(timestamp=SESSIONS[0]+"T09:30:00.001-04:00")],[row(timestamp=SESSIONS[0]+"T09:25:00-04:00")],
            [row(timestamp=SESSIONS[0]+"T16:05:00-04:00")],[row(session=SESSIONS[1])]]
        for rows in invalid:
            with self.subTest(rows=rows):self.assertNotEqual(self.compare(rows)[0],"ok")

    def test_multiday_split_requires_every_day_and_rejects_weekend(self):
        rows=[row(),row(session=SESSIONS[1],volume=20)]
        result,timing=compare_engines(rows,SYMBOL,SESSIONS,"2029-01-19",repeats=1)
        self.assertEqual(result[SESSIONS[0]][0],[570,10,0]);self.assertEqual(result[SESSIONS[1]][0],[570,20,0])
        self.assertTrue(timing["exact_match"])
        with self.assertRaisesRegex(ValueError,"missing_session"):split_sessions(rows[:1],SESSIONS)
        with self.assertRaisesRegex(ValueError,"unrequested_session"):split_sessions(rows+[row(session="2026-08-15")],SESSIONS)

    def factory(self,*,missing=False,mismatch=False):
        calls=[]
        class Transport:
            def __init__(self,root,budget,**kwargs):self.budget=budget
            def close(self):pass
            def request(self,method,**params):
                self.budget.reserve_request();self.budget.add_bytes(10);calls.append((method,params))
                if method=="option_list_contracts":
                    session=params["date"].isoformat();rows=[row(session=session)]
                    if session==SESSIONS[0]:rows.append(row(session=session,expiry=SESSIONS[0]))
                    if missing:rows.append(row(session=session,strike=1234.56))
                    return rows,{"response_bytes":10}
                sessions=[params["date"].isoformat()] if "date" in params else SESSIONS
                return [row(session=session,expiry=params["expiration"].isoformat(),volume=20 if mismatch and "date" in params and params["expiration"].year==2029 else 10) for session in sessions],{"response_bytes":10}
        return Transport,calls

    def test_all_expiries_including_expired_and_leaps_one_range_per_common_expiry(self):
        factory,calls=self.factory()
        p=Probe(self.root,self.output,factory=factory,clock=lambda:datetime(2026,9,30,22,tzinfo=timezone.utc))
        try:result=p.run()
        finally:p.close()
        self.assertEqual(len(calls),6);self.assertEqual(result["data_requests"],6)
        history=[params for method,params in calls if method=="option_history_ohlc"]
        self.assertEqual(history[0]["expiration"],date(2026,8,14));self.assertIn("date",history[0])
        self.assertEqual(history[1]["expiration"],date(2029,1,19));self.assertNotIn("date",history[1])
        self.assertEqual(history[1]["start_date"],date(2026,8,14));self.assertEqual(history[1]["end_date"],date(2026,8,17))
        for params in history:
            self.assertEqual(params["strike"],"*");self.assertEqual(params["right"],"both")
            self.assertNotIn("max_dte",params);self.assertNotIn("strike_range",params)
        self.assertTrue(result["complete_catalog_coverage"])
        with self.assertRaises(FileExistsError):Probe(self.root,self.output,factory=lambda *a,**k:self.fail("repeat"))
        self.assertFalse((self.root/"data").exists())

    def test_missing_contracts_are_reported_not_claimed_complete(self):
        factory,_=self.factory(missing=True)
        p=Probe(self.root,self.output,factory=factory,clock=lambda:datetime(2026,9,30,22,tzinfo=timezone.utc))
        try:result=p.run()
        finally:p.close()
        self.assertFalse(result["complete_catalog_coverage"])
        self.assertEqual(result["coverage"][SESSIONS[0]]["missing_contracts"],1)

    def test_request_cap_and_repetition_checked_before_admission(self):
        factory,calls=self.factory()
        p=Probe(self.root,self.output,factory=factory,clock=lambda:datetime(2026,9,30,22,tzinfo=timezone.utc))
        params={"symbol":SYMBOL,"date":date.fromisoformat(SESSIONS[0]),"request_type":"trade"}
        try:
            p.request("option_list_contracts",**params)
            with self.assertRaisesRegex(ValueError,"repeat_forbidden"):p.request("option_list_contracts",**params)
            p.budget.max_requests=1
            with self.assertRaisesRegex(RuntimeError,"request_cap"):p.request("option_list_contracts",symbol=SYMBOL,date=date.fromisoformat(SESSIONS[1]),request_type="trade")
        finally:p.close()
        self.assertEqual(len(calls),1)
        records=[json.loads(line) for line in (self.output/"live-admissions.jsonl").read_text().splitlines()]
        self.assertEqual(sum(r["kind"]=="started" for r in records),1)

    def test_collection_window_prevents_login(self):
        p=Probe(self.root,self.output,factory=lambda *a,**k:self.fail("login"),clock=lambda:datetime(2026,9,30,14,tzinfo=timezone.utc))
        try:
            with self.assertRaisesRegex(RuntimeError,"reserved_collection_window"):p.run()
        finally:p.close()

    def test_transport_scope_and_omitted_single_date_in_range(self):
        transport=ProbeTransport.__new__(ProbeTransport);transport._request=Mock(return_value=([],{}))
        params={"symbol":SYMBOL,"expiration":date(2029,1,19),"interval":"5m","strike":"*","right":"both",
            "start_time":"09:30:00","end_time":"16:00:00","start_date":date(2026,8,14),"end_date":date(2026,8,17)}
        transport.request("option_history_ohlc",**params);self.assertEqual(transport._request.call_count,1)
        for change in ({"expiration":"*"},{"symbol":"SPY"},{"date":date(2026,8,14)},{"strike":100},{"max_dte":120},{"start_date":date(2026,8,13)}):
            with self.assertRaises(ValueError):transport.request("option_history_ohlc",**{**params,**change})
        self.assertEqual(transport._request.call_count,1)

    @unittest.skipUnless(importlib.util.find_spec("thetadata"),"Installed SDK unavailable")
    def test_installed_sdk_expiry_asterisk_fails_before_rpc(self):
        from thetadata import ThetaClient
        from thetadata._proto.endpoints_pb2 import AuthToken
        client=ThetaClient.__new__(ThetaClient);client.auth_token=AuthToken();client.email="offline"
        client.stub=Mock()
        with self.assertRaises(AttributeError):
            client.option_history_ohlc(symbol=SYMBOL,expiration="*",date=date(2026,8,14),interval="5m")
        client.stub.GetOptionHistoryOhlc.assert_not_called()

    def test_durable_ledger_names_before_login(self):
        import os,stat
        events=[];real_sync=os.fsync
        def sync(fd):
            events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            real_sync(fd)
        factory,calls=self.factory()
        def checked_factory(*args,**kwargs):
            self.assertEqual(events[:3],["file","directory","directory"])
            return factory(*args,**kwargs)
        with patch("quant_data.options.monitor.baseline_probe.os.fsync",side_effect=sync):
            p=Probe(self.root,self.output,factory=checked_factory,clock=lambda:datetime(2026,9,30,22,tzinfo=timezone.utc))
            try:p.request("option_list_contracts",symbol=SYMBOL,date=date.fromisoformat(SESSIONS[0]),request_type="trade")
            finally:p.close()
        self.assertEqual(len(calls),1)

    def test_directory_sync_failure_prevents_login(self):
        factory=Mock(side_effect=AssertionError("network"))
        with patch("quant_data.options.monitor.baseline_probe.os.fsync",side_effect=[None,OSError("sync failed")]):
            with self.assertRaises(OSError):Probe(self.root,self.output,factory=factory)
        factory.assert_not_called();self.assertTrue((self.output/"live-admissions.jsonl").exists())

    def test_daily_control_mismatch_fails_probe(self):
        factory,_=self.factory(mismatch=True)
        p=Probe(self.root,self.output,factory=factory,clock=lambda:datetime(2026,9,30,22,tzinfo=timezone.utc))
        try:
            with self.assertRaisesRegex(ValueError,"multiday_daily_mismatch"):p.run()
        finally:p.close()
        self.assertFalse((self.output/"live-result.json").exists())
        rows=[json.loads(x) for x in (self.output/"live-admissions.jsonl").read_text().splitlines()]
        self.assertEqual([x["exact_match"] for x in rows if x["kind"]=="daily_control"],[False])
