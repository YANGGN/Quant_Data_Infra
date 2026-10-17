"""Offline retry, gap continuation and restart-budget invariants for ETFs."""
from collections import Counter
from datetime import datetime,timezone
import json,threading,unittest
from unittest.mock import patch
from quant_data.options.etf_job import EtfHistoryJob
from quant_data.options.etf_recovery import EtfRequests,RequestGap
from quant_data.options.transport import ProviderFailure,Budget
from quant_data.options.universe import EXPANSION_ETFS
from tests.options.test_theta_compact import temporary_store,SESSION,PREVIOUS
from tests.options.test_theta_parallel import day_rows

GREEKS="option_history_greeks_eod"
OI="option_history_open_interest"
EOD="option_history_eod"

class Scripted:
    subscription_code=2
    def __init__(self,job,budget,callback,script,calls):
        self.job,self.budget,self.callback=job,budget,callback
        self.script,self.calls=script,calls
        self.counts=Counter();self.lock=threading.Lock()
    def request(self,method,**params):
        key=(params["symbol"],method)
        self.budget.reserve_request()
        with self.lock:
            self.counts[key]+=1;number=self.counts[key]
            self.calls.append((self.job.status.get("phase","main"),*key))
            outcomes=self.script.get(key,["success"])
            outcome=outcomes[min(number-1,len(outcomes)-1)]
        day=str(params.get("date",params.get("start_date")))
        e,o=day_rows(day)
        for row in e+o:row["symbol"]=params["symbol"]
        rows=(o if method==OI else e) if outcome=="success" else []
        receipt={"method":method,"params":{k:str(v) for k,v in params.items()},
            "captured_at":datetime.now(timezone.utc).isoformat(),"outcome":outcome,
            "response_bytes":100,"response_sha256":"c"*64,"rows":len(rows)}
        self.budget.add_bytes(100);self.callback(receipt,self.budget)
        if outcome not in ("success","empty","no_data"):raise ProviderFailure(outcome,receipt)
        return rows,receipt
    def close(self):pass

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp,self.store=temporary_store();self.addCleanup(self.temp.cleanup)
        self.store.initialize()
        self.job=EtfHistoryJob(self.store.root)
        for symbol in EXPANSION_ETFS:
            p=self.job.work/"references"/symbol/"spot-references.json"
            p.parent.mkdir(parents=True);p.write_text("{}")
        self.calls=[]
        self.delay=patch.object(EtfRequests,"delay",return_value=None)
        self.delay.start();self.addCleanup(self.delay.stop)

    def run_job(self,script,symbols=("QQQ",),job=None):
        job=job or self.job
        units=[(symbol,SESSION,PREVIOUS) for symbol in symbols]
        job.transport_factory=lambda root,budget,receipt_callback,symbols:Scripted(
            job,budget,receipt_callback,script,self.calls)
        with patch.object(job,"load_plan",return_value=({"units":units},units)):return job.run()

    def events(self):
        p=self.job.work/"gap-events.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def test_retry_once_then_publish_without_repeating_other_inputs(self):
        result=self.run_job({("QQQ",GREEKS):["UNAVAILABLE","success"]})
        self.assertEqual(result["state"],"completed")
        self.assertEqual(result["data_requests"],3)
        self.assertEqual(result["retry_requests"],1)
        self.assertEqual([c[2] for c in self.calls],[GREEKS,GREEKS,OI])
        attempts=[json.loads(line) for line in self.job.attempt_path.read_text().splitlines()]
        self.assertEqual([a["attempt_number"] for a in attempts],[1,2,1])

    def test_persistent_failure_logs_and_other_symbol_finishes_before_final_pass(self):
        result=self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]},("QQQ","IWM"))
        self.assertEqual(result["state"],"completed_with_gaps")
        self.assertEqual(result["completed_sessions"],1)
        self.assertEqual(result["processed_sessions"],2)
        self.assertEqual(result["gaps"][0]["symbol"],"QQQ")
        qqq=[c for c in self.calls if c[1]=="QQQ"]
        self.assertEqual([c[0] for c in qqq],["main","main","recovery"])
        final_index=self.calls.index(qqq[-1])
        self.assertTrue(all(i<final_index for i,c in enumerate(self.calls) if c[1]=="IWM"))
        self.assertEqual([e["phase"] for e in self.events()],["main","recovery"])

    def test_empty_responses_retry_and_final_pass_can_recover(self):
        result=self.run_job({("QQQ",GREEKS):["no_data","no_data","success"],
                             ("QQQ",EOD):["empty"]})
        self.assertEqual(result["state"],"completed")
        self.assertEqual(result["gaps"],[])
        counts=Counter(c[2] for c in self.calls)
        self.assertEqual(counts,{GREEKS:3,EOD:2,OI:1})
        self.assertEqual([e["outcome"] for e in self.events()],["missing","resolved"])
        self.assertFalse(list(self.job.stage.glob("*.json.gz")))

    def test_missing_oi_recovery_reuses_successful_greeks(self):
        result=self.run_job({("QQQ",OI):["empty","empty","success"]})
        self.assertEqual(result["state"],"completed")
        self.assertEqual(Counter(c[2] for c in self.calls),{GREEKS:1,OI:3})
        self.assertEqual(result["retry_requests"],2)

    def test_final_gap_does_not_start_another_pass_on_restart(self):
        result=self.run_job({("QQQ",OI):["no_data"]})
        self.assertEqual(result["state"],"completed_with_gaps")
        count=len(self.calls)
        next_job=EtfHistoryJob(self.store.root,transport_factory=lambda *a,**k:self.fail("extra recovery"))
        self.assertEqual(next_job.run()["state"],"completed_with_gaps")
        self.assertEqual(len(self.calls),count)

    def test_access_failure_is_fatal_and_never_retried(self):
        with self.assertRaises(ProviderFailure):
            self.run_job({("QQQ",GREEKS):["PERMISSION_DENIED"]})
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.job.status["state"],"stopped")
        self.assertEqual(self.events(),[])

    def test_request_cap_stops_retry_and_never_becomes_a_data_gap(self):
        with patch("quant_data.options.etf_job.MAX_REQUESTS",1):
            with self.assertRaisesRegex(RuntimeError,"request_cap"):
                self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]})
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.job.status["error"],"request_cap")
        self.assertEqual(self.events(),[])

    def test_original_legacy_failed_attempt_counts_toward_main_limit(self):
        self.job.requests=EtfRequests(self.job);self.job._budget=Budget()
        fake=Scripted(self.job,self.job._budget,self.job.receipt,{("QQQ",GREEKS):["UNAVAILABLE"]},self.calls)
        args=dict(symbol="QQQ",expiration="*",start_date=SESSION,end_date=SESSION,
                  version="1",underlyer_use_nbbo=True)
        with patch.object(EtfRequests,"delay",side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError,"interrupted"):
                self.job.requests.get(fake,GREEKS,**args)
        event=json.loads(self.job.attempt_path.read_text())
        for key in ("phase","attempt_number","attempt_id"):event.pop(key)
        self.job.attempt_path.write_text(json.dumps(event)+"\n")
        new=EtfHistoryJob(self.store.root)
        result=self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]},job=new)
        self.assertEqual(result["data_requests"],3)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(result["retry_requests"],2)
        self.assertEqual(result["state"],"completed_with_gaps")

    def test_restart_reconstructs_total_attempts_not_unique_units(self):
        with patch.object(self.job.store,"publish",side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError,"interrupted"):
                self.run_job({("QQQ",GREEKS):["UNAVAILABLE","success"]})
        self.assertEqual(len(self.calls),3)
        new=EtfHistoryJob(self.store.root)
        result=self.run_job({},job=new)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(result["data_requests"],3)
        self.assertEqual(result["state"],"completed")

    def test_recovery_attempt_not_reset_if_crash_precedes_gap_checkpoint(self):
        original=self.job.gap_event
        def gap(*args,**kwargs):
            if args[2]=="recovery":raise RuntimeError("crash_before_gap_checkpoint")
            return original(*args,**kwargs)
        with patch.object(self.job,"gap_event",side_effect=gap):
            with self.assertRaisesRegex(RuntimeError,"crash_before_gap_checkpoint"):
                self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]})
        self.assertEqual(len(self.calls),3)
        new=EtfHistoryJob(self.store.root)
        result=self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]},job=new)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(result["state"],"completed_with_gaps")

    def test_gap_log_survives_crash_before_status_checkpoint(self):
        original=self.job.persist
        def persist():
            if self.events():raise RuntimeError("crash_after_gap_log")
            return original()
        with patch.object(self.job,"persist",side_effect=persist):
            with self.assertRaisesRegex(RuntimeError,"crash_after_gap_log"):
                self.run_job({("QQQ",GREEKS):["UNAVAILABLE"]})
        self.assertEqual(len(self.calls),2)
        new=EtfHistoryJob(self.store.root)
        result=self.run_job({},job=new)
        self.assertEqual(result["state"],"completed")
        self.assertEqual([c[0] for c in self.calls if c[2]==GREEKS],["main","main","recovery"])

    def test_unreceipted_midstream_bytes_block_resume_before_authentication(self):
        class Crash:
            subscription_code=2
            def __init__(self,root,budget,receipt_callback,symbols):self.budget=budget
            def request(self,*args,**kwargs):
                self.budget.reserve_request();self.budget.add_bytes(400)
                raise RuntimeError("midstream_crash")
            def close(self):pass
        units=[("QQQ",SESSION,PREVIOUS)]
        self.job.transport_factory=Crash
        with patch("quant_data.options.etf_job.MAX_BYTES",500),patch.object(
                self.job,"load_plan",return_value=({"units":units},units)):
            with self.assertRaisesRegex(RuntimeError,"midstream_crash"):self.job.run()
        self.assertEqual(self.job.status["received_bytes"],400)
        self.assertEqual(len(self.job.attempt_path.read_text().splitlines()),1)
        new=EtfHistoryJob(self.store.root,
            transport_factory=lambda *a,**k:self.fail("unaccounted bytes authenticated"))
        with patch("quant_data.options.etf_job.MAX_BYTES",500),patch.object(
                new,"load_plan",return_value=({"units":units},units)):
            with self.assertRaisesRegex(RuntimeError,"request_receipt_accounting_mismatch"):new.run()
        self.assertEqual(new.status["received_bytes"],400)
        self.assertEqual(self.store.status()["sessions"],0)
        self.assertEqual(len(new.attempt_path.read_text().splitlines()),1)

    def test_quality_gap_final_pass_reuses_all_valid_provider_inputs(self):
        with patch("quant_data.options.etf_job.build_capture",side_effect=ValueError("missing_contemporaneous_spot")):
            result=self.run_job({})
        self.assertEqual(result["state"],"completed_with_gaps")
        self.assertEqual(len(self.calls),2)
        self.assertEqual([e["phase"] for e in self.events()],["main","recovery"])
        self.assertEqual(result["gaps"][0]["reason"],"missing_contemporaneous_spot")

if __name__=="__main__":unittest.main()
