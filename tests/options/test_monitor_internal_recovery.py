"""Offline coverage for the explicitly authorized INTERNAL RPC amendment."""
import json,unittest,tempfile
from pathlib import Path
from datetime import timedelta
from quant_data.options.monitor.baseline_store import BaselineStore,INTERNAL_POLICY,CHECKSUM,retryable
from quant_data.options.transport import ProviderFailure
from tests.options import test_monitor_recovery as recovery

class InternalRecoveryTests(unittest.TestCase):
    # Reuse the established isolated clock/provider fixtures without inheriting
    # and rerunning the original test cases.
    setUp=recovery.RecoveryTests.setUp
    plan=recovery.RecoveryTests.plan
    sleep=recovery.RecoveryTests.sleep
    provider=recovery.RecoveryTests.provider
    site=recovery.RecoveryTests.site
    job=recovery.RecoveryTests.job
    enable=recovery.RecoveryTests.enable

    def enabled(self,**kwargs):
        identity=self.plan(**kwargs);self.enable(identity)
        self.store.configure_internal_recovery(identity)
        return identity

    def failed(self,identity,*,symbol="SPY",part="catalog",code="INTERNAL",count=0):
        self.store.reserve_part(symbol,"2026-09-25",part,identity,self.now.isoformat())
        self.store.finish_part(symbol,"2026-09-25",part,"ProviderFailure",None,
            {"outcome":code,"response_bytes":count,"finished_at":self.now.isoformat()})

    def test_policy_is_opt_in_replay_safe_and_schema_unchanged(self):
        identity=self.plan();self.enable(identity)
        original=self.store.plan(identity);base=self.store.plan("recovery:"+identity)
        self.failed(identity)
        state=self.store.attempt_state("SPY","2026-09-25","catalog")
        self.assertFalse(retryable(state,identity,self.store.recovery_policy(identity)))
        policy=self.store.configure_internal_recovery(identity)
        self.assertEqual(self.store.configure_internal_recovery(identity),policy)
        self.assertTrue(retryable(state,identity,policy));self.assertEqual(policy["max_attempts"],3)
        self.assertEqual(policy["delays_seconds"],[5,30])
        self.assertEqual(self.store.plan(identity),original)
        self.assertEqual(self.store.plan("recovery:"+identity),base)
        self.assertEqual(self.store.plan("internal-recovery:"+identity),INTERNAL_POLICY)
        self.assertEqual(self.store.attempt_state("SPY","2026-09-25","catalog"),state)
        self.assertEqual(self.store.usage(identity,1000000),(1,0))
        other=self.plan(requests=999);self.enable(other)
        self.assertNotIn("INTERNAL",self.store.recovery_policy(other)["retry_codes"])
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT checksum FROM baseline_metadata").fetchone()[0],CHECKSUM)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_runs WHERE id=?",
                ("internal-recovery:"+identity,)).fetchone()[0],1)

    def test_amendment_requires_existing_recovery_policy(self):
        identity=self.plan()
        with self.assertRaisesRegex(ValueError,"recovery_policy_missing"):
            self.store.configure_internal_recovery(identity)
        with self.store.read() as c:self.assertIsNone(c.execute(
            "SELECT 1 FROM baseline_runs WHERE id=?",("internal-recovery:"+identity,)).fetchone())

    def test_policy_conflict_fails_closed(self):
        identity=self.enabled()
        self.store.write("UPDATE baseline_runs SET payload=? WHERE id=?",
            (json.dumps({"version":1,"retry_codes":["INTERNAL","DATA_LOSS"]}),"internal-recovery:"+identity))
        for operation in (self.store.recovery_policy,self.store.configure_internal_recovery):
            with self.assertRaisesRegex(ValueError,"internal_policy_conflict"):operation(identity)

    def test_session_amendment_is_preserved(self):
        identity=self.enabled()
        self.store.configure_session_recovery(identity,[],evidence_sha256="0"*64)
        policy=self.store.recovery_policy(identity)
        self.assertIn("INTERNAL",policy["retry_codes"])
        self.assertIn("SESSION_TIMEOUT",policy["retry_codes"])
        self.assertEqual(policy["session_delays_seconds"],[30,120])
        self.assertEqual(policy["max_attempts"],3)

    def test_rpc_recovers_after_known_byte_internal_failure(self):
        identity=self.enabled()
        provider,state=self.provider(lambda key,n:"INTERNAL" if key[2]!="catalog" and n==1 else None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertTrue(result["upload_complete"])
        self.assertEqual(len(state["calls"]),3);self.assertGreaterEqual(sum(self.waits),5)
        self.assertEqual(self.store.usage(identity,1000000),(3,30))
        self.assertTrue(any(e.get("error")=="INTERNAL" and e.get("phase")=="retrying" for e in self.events))

    def test_resume_prior_failure_reuses_catalog_and_preserves_receipt(self):
        identity=self.enabled();session="2026-09-25"
        self.store.reserve_part("SPY",session,"catalog",identity,self.now.isoformat())
        self.store.finish_part("SPY",session,"catalog","success",[session],{"response_bytes":10})
        self.failed(identity,part=session,count=12)
        with self.store.read() as c:original=tuple(c.execute(
            "SELECT * FROM baseline_requests WHERE part=?",(session,)).fetchone())
        self.assertEqual(self.store.collection_status(identity)["retry_pending_days"],1)
        provider,state=self.provider(lambda key,n:None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["calls"],[("SPY",session,session)])
        self.assertEqual(result["saved_days"],1);self.assertEqual(self.store.usage(identity,1000000),(3,32))
        with self.store.read() as c:self.assertEqual(tuple(c.execute(
            "SELECT * FROM baseline_requests WHERE part=?",(session,)).fetchone()),original)

    def test_exhaustion_continues_other_tickers_and_never_grants_fourth(self):
        identity=self.enabled(symbols=("SPY","QQQ"))
        provider,state=self.provider(lambda key,n:"INTERNAL" if key[0]=="SPY" else None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(result["skipped_days"],1)
        self.assertEqual(state["calls"].count(("SPY","2026-09-25","catalog")),3)
        self.assertTrue(self.store.has_day("QQQ","2026-09-25"))
        before=len(state["calls"])
        self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(len(state["calls"]),before)
        with self.assertRaisesRegex(ValueError,"forbidden"):
            self.store.reserve_retry("SPY","2026-09-25","catalog",identity,self.now.isoformat(),4)

    def test_request_cap_counts_internal_retries_across_restart(self):
        identity=self.enabled(requests=2)
        provider,state=self.provider(lambda key,n:"INTERNAL")
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"request_cap"):
                self.job(identity,provider).run(lambda:self.fail("unexpected upload"),report=self.events.append)
        self.assertEqual(len(state["calls"]),2);self.assertEqual(self.store.usage(identity,1000000),(2,20))

    def test_unknown_or_invalid_transfer_is_not_retryable(self):
        for count in (None,-1,True,"0"):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as directory:
                self.store=BaselineStore(Path(directory));self.store.initialize_baseline()
                identity=self.enabled();self.failed(identity,count=count)
                state=self.store.attempt_state("SPY","2026-09-25","catalog")
                self.assertFalse(retryable(state,identity,self.store.recovery_policy(identity)))
                with self.assertRaisesRegex(ValueError,"forbidden"):
                    self.store.reserve_retry("SPY","2026-09-25","catalog",identity,self.now.isoformat(),2)
                if count is None:
                    provider,observed=self.provider(lambda key,n:None)
                    with self.assertRaisesRegex(RuntimeError,"response_byte_cap"):
                        self.job(identity,provider).run(self.site,report=self.events.append)
                    self.assertEqual(observed["calls"],[])
                    self.assertEqual(self.store.usage(identity,1000000),(1,1000000))

    def test_other_run_failure_cannot_be_retried(self):
        previous=self.plan(requests=10);self.failed(previous)
        identity=self.enabled(requests=100)
        provider,state=self.provider(lambda key,n:None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["skipped_days"],1);self.assertEqual(state["calls"],[])

    def test_deadline_still_stops_during_internal_backoff(self):
        identity=self.enabled(deadline=(self.now+timedelta(seconds=1)).isoformat())
        provider,state=self.provider(lambda key,n:"INTERNAL")
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):
            self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(len(state["calls"]),1)

    def test_pending_internal_retry_blocks_publication(self):
        identity=self.enabled();self.failed(identity)
        provider,state=self.provider(lambda key,n:None)
        result=self.job(identity,provider).upload(lambda:self.fail("upload before local completion"))
        self.assertFalse(result["upload_complete"]);self.assertEqual(result["retry_pending_days"],1)
        self.assertEqual(state["calls"],[])

    def test_other_rpc_errors_remain_terminal(self):
        identity=self.enabled()
        provider,state=self.provider(lambda key,n:"DATA_LOSS")
        with self.assertRaises(ProviderFailure):
            self.job(identity,provider).run(lambda:self.fail("unexpected upload"),report=self.events.append)
        self.assertEqual(len(state["calls"]),1)
