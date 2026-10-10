"""Offline boundaries for one explicitly approved historical authentication retry."""
import json,unittest
from datetime import timedelta
from quant_data.options.monitor.baseline_store import request_fingerprint,retryable
from tests.options import test_monitor_recovery as fixtures

class AuthRetryOnceTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    plan=fixtures.RecoveryTests.plan
    sleep=fixtures.RecoveryTests.sleep
    provider=fixtures.RecoveryTests.provider
    site=fixtures.RecoveryTests.site
    job=fixtures.RecoveryTests.job
    enable=fixtures.RecoveryTests.enable
    session="2026-09-25"
    def pending(self,code="UNAUTHENTICATED",size=0):
        source=self.plan(requests=50)
        target=self.plan(requests=999)
        self.enable(target);self.store.configure_resilient_recovery(target)
        self.store.reserve_part("SPY",self.session,"catalog",source,self.now.isoformat())
        self.store.finish_part("SPY",self.session,"catalog","success",[self.session],{"response_bytes":7})
        self.store.reserve_part("SPY",self.session,self.session,source,self.now.isoformat())
        receipt={"method":"option_history_ohlc","outcome":code,"response_bytes":size,"captured_at":(self.now-timedelta(seconds=60)).isoformat()}
        self.store.finish_part("SPY",self.session,self.session,"ProviderFailure",None,receipt)
        with self.store.read() as c:
            parent=dict(c.execute("SELECT * FROM baseline_requests WHERE part=?",(self.session,)).fetchone())
        return source,target,parent
    def grant(self,target,parent,**overrides):
        args={"symbol":"SPY","session":self.session,"part":self.session,
              "after_sha256":request_fingerprint(parent),"evidence_sha256":"a"*64}
        return self.store.configure_auth_retry_once(target,**{**args,**overrides})
    def test_unamended_auth_failure_stays_blocked(self):
        source,target,parent=self.pending()
        self.assertFalse(retryable(self.store.attempt_state("SPY",self.session,self.session),target,self.store.recovery_policy(target)))
        with self.assertRaisesRegex(ValueError,"retry_forbidden"):
            self.store.reserve_retry("SPY",self.session,self.session,target,self.now.isoformat(),2)
    def test_cross_run_success_reuses_catalog_preserves_parent_and_uploads(self):
        source,target,parent=self.pending();grant=self.grant(target,parent)
        source_plan=self.store.plan(source);target_plan=self.store.plan(target)
        self.assertEqual(self.store.collection_status(target)["retry_pending_days"],1)
        provider,calls=self.provider(lambda key,n:None)
        result=self.job(target,provider,workers=1).run(self.site,report=self.events.append)
        self.assertTrue(result["upload_complete"]);self.assertEqual(result["saved_days"],1)
        self.assertEqual(calls["calls"],[("SPY",self.session,self.session)])
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertEqual((state["attempt"],state["run_id"],state["original_run_id"]),(2,target,source))
        with self.store.read() as c:
            self.assertEqual(dict(c.execute("SELECT * FROM baseline_requests WHERE part=?",(self.session,)).fetchone()),parent)
        self.assertEqual(self.store.plan(source),source_plan);self.assertEqual(self.store.plan(target),target_plan)
        self.assertEqual(self.store.usage(source,1000000),(2,7))
        self.assertEqual(self.store.usage(target,1000000),(2,10))
        self.assertEqual(self.grant(target,parent),grant)
        before=len(calls["calls"]);self.job(target,provider).run(self.site)
        self.assertEqual(len(calls["calls"]),before)
    def test_failed_retry_has_no_third_attempt_even_when_transient(self):
        source,target,parent=self.pending();self.grant(target,parent)
        provider,calls=self.provider(lambda key,n:"UNAVAILABLE")
        result=self.job(target,provider,workers=1).run(self.site)
        self.assertEqual(len(calls["calls"]),1);self.assertEqual(result["skipped_days"],1)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertEqual(state["attempt"],2)
        self.assertFalse(retryable(state,target,self.store.recovery_policy(target)))
        with self.assertRaisesRegex(ValueError,"retry_forbidden"):
            self.store.reserve_retry("SPY",self.session,self.session,target,self.now.isoformat(),3)
    def test_interrupted_retry_is_unknown_and_never_gets_another_attempt(self):
        source,target,parent=self.pending();self.grant(target,parent)
        self.store.reserve_retry("SPY",self.session,self.session,target,self.now.isoformat(),2)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertFalse(retryable(state,target,self.store.recovery_policy(target)))
        self.assertEqual(self.store.usage(target,1000000),(1,1000000))
        self.assertEqual(self.store.collection_status(target)["unresolved_days"],1)
        self.grant(target,parent)
        with self.assertRaisesRegex(ValueError,"retry_forbidden"):
            self.store.reserve_retry("SPY",self.session,self.session,target,self.now.isoformat(),3)
    def test_unknown_or_other_provider_failure_cannot_receive_grant(self):
        for code,size in (("UNAVAILABLE",0),("UNAUTHENTICATED",None)):
            with self.subTest(code=code,size=size):
                source,target,parent=self.pending(code,size)
                with self.assertRaisesRegex(ValueError,"auth_retry_ineligible"):self.grant(target,parent)
                # Independent fixtures for the next variant.
                self.setUp()
    def test_wrong_fingerprint_target_or_evidence_is_rejected(self):
        source,target,parent=self.pending()
        for override in ({"after_sha256":"b"*64},{"evidence_sha256":"invalid"},{"symbol":"QQQ"},{"part":"catalog"}):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):self.grant(target,parent,**override)
        outside=self.plan(symbols=("QQQ",));self.enable(outside)
        with self.assertRaisesRegex(ValueError,"auth_retry_scope"):self.grant(outside,parent)
    def test_grant_is_idempotent_and_cannot_change_recipient_or_evidence(self):
        source,target,parent=self.pending();first=self.grant(target,parent)
        self.assertEqual(self.grant(target,parent),first)
        other=self.plan(requests=998);self.enable(other)
        for new_target,override in ((target,{"evidence_sha256":"b"*64}),(other,{})):
            with self.assertRaisesRegex(ValueError,"auth_retry_policy_conflict"):self.grant(new_target,parent,**override)
    def test_parent_tamper_and_unapproved_attempt_three_fail_closed(self):
        source,target,parent=self.pending();self.grant(target,parent)
        self.store.reserve_part("SPY",self.session,self.session+"#retry3",target,self.now.isoformat())
        with self.assertRaisesRegex(ValueError,"auth_retry_attempt_limit"):self.store.attempt_state("SPY",self.session,self.session)
        self.store.write("UPDATE baseline_requests SET started_at=? WHERE part=?",("changed",self.session))
        with self.assertRaisesRegex(ValueError,"auth_retry_parent_changed"):self.store.attempt_state("SPY",self.session,self.session)
    def test_request_and_deadline_caps_prevent_extra_provider_work(self):
        source,target,parent=self.pending();self.grant(target,parent)
        provider,calls=self.provider(lambda key,n:None)
        job=self.job(target,provider,workers=1);job.budget.max_requests=1
        with self.assertRaisesRegex(RuntimeError,"request_cap"):job.run(self.site)
        self.assertFalse(calls["calls"])
        job=self.job(target,provider,workers=1);self.now+=timedelta(days=1)
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):job.run(self.site)
        self.assertFalse(calls["calls"])
    def test_other_run_and_other_auth_failure_are_not_covered(self):
        source,target,parent=self.pending();self.grant(target,parent)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertFalse(retryable(state,source,self.store.recovery_policy(target)))
        other_part="2026-10-02"
        self.store.reserve_part("SPY",self.session,other_part,source,self.now.isoformat())
        self.store.finish_part("SPY",self.session,other_part,"ProviderFailure",None,
                               {"outcome":"UNAUTHENTICATED","method":"option_history_ohlc","response_bytes":0})
        self.assertFalse(retryable(self.store.attempt_state("SPY",self.session,other_part),target,self.store.recovery_policy(target)))

if __name__=="__main__":unittest.main()
