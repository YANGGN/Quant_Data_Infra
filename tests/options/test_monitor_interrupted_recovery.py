"""Offline invariants for explicitly approved original interrupted-request exceptions."""
import json, unittest
from quant_data.options.monitor.baseline_store import request_fingerprint, retryable
from tests.options import test_monitor_recovery as fixtures

class InterruptedRecoveryTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    plan=fixtures.RecoveryTests.plan
    sleep=fixtures.RecoveryTests.sleep
    provider=fixtures.RecoveryTests.provider
    site=fixtures.RecoveryTests.site
    job=fixtures.RecoveryTests.job
    enable=fixtures.RecoveryTests.enable
    session="2026-09-25"

    def pending(self,identity,part=None,symbol="SPY",catalog=True):
        part=part or self.session
        if catalog:
            self.store.reserve_part(symbol,self.session,"catalog",identity,self.now.isoformat())
            self.store.finish_part(symbol,self.session,"catalog","success",[part],{"response_bytes":10})
        self.store.reserve_part(symbol,self.session,part,identity,self.now.isoformat())
        with self.store.read() as c:
            row=dict(c.execute("SELECT * FROM baseline_requests WHERE symbol=? AND session=? AND part=?",
                 (symbol,self.session,part)).fetchone())
        return {"symbol":symbol,"session":self.session,"part":part,"request_sha256":request_fingerprint(row)},row

    def grant(self,identity,items,**kwargs):
        return self.store.configure_interrupted_recovery(identity,items,evidence_sha256=kwargs.get("evidence","b"*64))

    def test_unamended_unknown_stays_blocked(self):
        identity=self.plan();self.enable(identity);item,original=self.pending(identity)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertFalse(retryable(state,identity,self.store.recovery_policy(identity)))
        self.assertEqual(self.store.usage(identity,1000000),(2,1000010))
        provider,calls=self.provider(lambda key,n:None)
        with self.assertRaisesRegex(RuntimeError,"byte"):
            self.job(identity,provider).run(self.site)
        self.assertFalse(calls["calls"])

    def test_exact_exception_resumes_and_preserves_original_unknown(self):
        identity=self.plan();self.enable(identity);plan=self.store.plan(identity)
        item,original=self.pending(identity);grant=self.grant(identity,[item])
        self.assertEqual(self.grant(identity,[item]),grant)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertIsNone(state["receipt"]);self.assertEqual(state["outcome"],"started")
        self.assertTrue(retryable(state,identity,self.store.recovery_policy(identity)))
        status=self.store.collection_status(identity)
        self.assertEqual(status["retry_pending_days"],1);self.assertEqual(status["unresolved_days"],0)
        self.assertEqual(status["excluded_unknown_requests"],1)
        self.assertEqual(self.store.usage(identity,1000000),(2,10))
        provider,calls=self.provider(lambda key,n:None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(calls["calls"],[("SPY",self.session,self.session)])
        self.assertTrue(result["upload_complete"]);self.assertEqual(result["saved_days"],1)
        self.assertGreaterEqual(sum(self.waits),5)
        self.assertEqual(self.store.usage(identity,1000000),(3,20))
        with self.store.read() as c:
            current=dict(c.execute("SELECT * FROM baseline_requests WHERE part=?",(self.session,)).fetchone())
        self.assertEqual(current,original)
        self.assertEqual(self.store.plan(identity),plan)
        self.assertEqual(self.grant(identity,[item]),grant)
        again=self.job(identity,provider).run(self.site)
        self.assertEqual(again["saved_days"],1);self.assertEqual(len(calls["calls"]),1)

    def test_new_interrupted_retry_is_not_exempt_or_retryable(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity);self.grant(identity,[item])
        self.store.reserve_retry("SPY",self.session,self.session,identity,self.now.isoformat(),2)
        state=self.store.attempt_state("SPY",self.session,self.session)
        self.assertFalse(state["interrupted_retry"])
        self.assertFalse(retryable(state,identity,self.store.recovery_policy(identity)))
        self.assertEqual(self.store.usage(identity,1000000),(3,1000010))
        self.assertEqual(self.store.collection_status(identity)["unresolved_days"],1)
        self.grant(identity,[item])
        with self.assertRaisesRegex(ValueError,"baseline_retry_forbidden"):
            self.store.reserve_retry("SPY",self.session,self.session,identity,self.now.isoformat(),3)

    def test_unselected_request_keeps_full_unknown_charge(self):
        identity=self.plan(("SPY","QQQ"));self.enable(identity)
        item,_=self.pending(identity);self.pending(identity,symbol="QQQ")
        self.grant(identity,[item])
        self.assertEqual(self.store.usage(identity,1000000),(4,1000020))
        self.assertEqual(self.store.collection_status(identity)["unresolved_days"],1)

    def test_four_only_and_no_duplicate_requests(self):
        identity=self.plan();self.enable(identity)
        items=[self.pending(identity,part=p,catalog=False)[0] for p in
               ("2026-09-25","2026-10-02","2026-10-09","2026-10-16","2026-10-23")]
        for invalid in ([],items,[items[0],items[0]]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):self.grant(identity,invalid)
        self.grant(identity,items[:4])
        self.assertEqual(self.store.usage(identity,1000000),(5,1000000))

    def test_wrong_owner_evidence_or_scope_rejected(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity)
        other=self.plan(requests=999);self.enable(other)
        with self.assertRaises(ValueError):self.grant(other,[item])
        with self.assertRaises(ValueError):self.grant(identity,[item],evidence="invalid")
        for field,value in (("symbol","QQQ"),("session","2026-09-24"),("part","catalog"),("request_sha256","c"*64)):
            bad={**item,field:value}
            with self.subTest(field=field):
                with self.assertRaises(ValueError):self.grant(identity,[bad])

    def test_measured_outcomes_cannot_use_exception(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity)
        self.store.finish_part("SPY",self.session,self.session,"ProviderFailure",None,
                               {"outcome":"UNAVAILABLE","response_bytes":0})
        with self.store.read() as c:
            row=dict(c.execute("SELECT * FROM baseline_requests WHERE part=?",(self.session,)).fetchone())
        item["request_sha256"]=request_fingerprint(row)
        with self.assertRaises(ValueError):self.grant(identity,[item])

    def test_parent_tampering_stops_usage_resolution_and_status(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity);self.grant(identity,[item])
        self.store.write("UPDATE baseline_requests SET started_at=? WHERE part=?",
                         ("2026-09-30T21:00:01+00:00",self.session))
        for check in (lambda:self.store.usage(identity,1000000),
                      lambda:self.store.attempt_state("SPY",self.session,self.session),
                      lambda:self.store.collection_status(identity)):
            with self.assertRaisesRegex(ValueError,"interruption_parent_changed"):check()

    def test_grant_replay_cannot_expand_or_change_evidence(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity);self.grant(identity,[item])
        with self.assertRaisesRegex(ValueError,"interruption_policy_conflict"):
            self.grant(identity,[item],evidence="c"*64)
        second,_=self.pending(identity,part="2026-10-02",catalog=False)
        with self.assertRaisesRegex(ValueError,"interruption_policy_conflict"):
            self.grant(identity,[item,second])

    def test_twenty_attempt_limit_and_original_admission_remain_charged(self):
        identity=self.plan();self.enable(identity);self.store.configure_resilient_recovery(identity)
        item,_=self.pending(identity);self.grant(identity,[item])
        for attempt in range(2,21):
            self.store.reserve_retry("SPY",self.session,self.session,identity,self.now.isoformat(),attempt)
            self.store.finish_part("SPY",self.session,f"{self.session}#retry{attempt}","ProviderFailure",None,
                                   {"outcome":"UNAVAILABLE","response_bytes":1})
        with self.assertRaisesRegex(ValueError,"baseline_retry_forbidden"):
            self.store.reserve_retry("SPY",self.session,self.session,identity,self.now.isoformat(),21)
        self.assertEqual(self.store.usage(identity,1000000),(21,29))
        self.assertEqual(self.store.recovery_policy(identity)["max_attempts"],20)

    def test_request_and_deadline_caps_still_prevent_dispatch(self):
        for overrides,error in (({"requests":2},"request_cap"),({"deadline":self.now.isoformat()},"duration_cap")):
            with self.subTest(overrides=overrides):
                self.setUp()
                identity=self.plan(**overrides);self.enable(identity)
                item,_=self.pending(identity);self.grant(identity,[item])
                provider,calls=self.provider(lambda key,n:None)
                with self.assertRaisesRegex(RuntimeError,error):self.job(identity,provider).run(self.site)
                self.assertEqual(calls["calls"],[])

    def test_later_attempt_cannot_be_exempted_as_original(self):
        identity=self.plan();self.enable(identity);item,_=self.pending(identity)
        self.store.write("INSERT INTO baseline_requests SELECT symbol,session,part||'#retry2',run_id,started_at,outcome,payload,receipt FROM baseline_requests WHERE part=?",(self.session,))
        with self.assertRaisesRegex(ValueError,"interruption_not_latest"):self.grant(identity,[item])
