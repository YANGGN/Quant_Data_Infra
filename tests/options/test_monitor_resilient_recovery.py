"""Finite twenty-attempt policy and central connection recovery, entirely offline."""
import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from quant_data.options.monitor.baseline_store import BaselineStore,RESILIENT_POLICY,CHECKSUM,attempt_part,retryable
from quant_data.options.monitor.transport import SessionSetupFailure
from quant_data.options.transport import ProviderFailure
from tests.options import test_monitor_recovery as fixtures
from tests.options import test_monitor_session_recovery as session_fixtures

class ResilientRecoveryTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    plan=fixtures.RecoveryTests.plan
    sleep=fixtures.RecoveryTests.sleep
    provider=fixtures.RecoveryTests.provider
    site=fixtures.RecoveryTests.site
    job=fixtures.RecoveryTests.job
    enable=fixtures.RecoveryTests.enable
    setup_policy=session_fixtures.SessionRecoveryTests.setup_policy
    exhausted_setup=session_fixtures.SessionRecoveryTests.exhausted_setup

    def enabled(self,**kwargs):
        identity=self.plan(**kwargs);self.enable(identity);self.setup_policy(identity)
        self.store.configure_internal_recovery(identity)
        self.store.configure_resilient_recovery(identity)
        return identity

    def factory(self,codes=(None,),choose=lambda key,n:None):
        normal,state=self.provider(choose);state["logins"]=0
        def provider():
            base=normal();index=state["factories"]-1;code=codes[min(index,len(codes)-1)];logged=False
            def create(*a,**kw):
                nonlocal logged
                if not logged:state["logins"]+=1;logged=True
                if code:raise SessionSetupFailure(code)
                return base(*a,**kw)
            return create
        return provider,state

    def test_policy_opt_in_replay_preserves_manifest_schema_and_prior_amendments(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        before=self.store.plan(identity);prior=self.store.plan("recovery:"+identity)
        self.assertEqual(self.store.recovery_policy(identity)["max_attempts"],3)
        policy=self.store.configure_resilient_recovery(identity)
        self.assertEqual(policy["max_attempts"],20)
        self.assertEqual(self.store.configure_resilient_recovery(identity),policy)
        self.assertEqual(self.store.plan(identity),before)
        self.assertEqual(self.store.plan("recovery:"+identity),prior)
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT checksum FROM baseline_metadata").fetchone()[0],CHECKSUM)
        other=self.plan(requests=500);self.enable(other)
        self.assertEqual(self.store.recovery_policy(other)["max_attempts"],3)

    def test_policy_tamper_and_absent_base_recovery_fail_closed(self):
        identity=self.plan()
        with self.assertRaisesRegex(ValueError,"policy_missing"):self.store.configure_resilient_recovery(identity)
        self.enable(identity);self.store.configure_resilient_recovery(identity)
        self.store.write("UPDATE baseline_runs SET payload=? WHERE id=?",
            (json.dumps({**RESILIENT_POLICY,"max_attempts":21}),"resilient-recovery:"+identity))
        with self.assertRaisesRegex(ValueError,"policy_conflict"):self.store.recovery_policy(identity)

    def test_shared_login_failures_do_not_reserve_any_contracts(self):
        identity=self.enabled(symbols=("SPY","QQQ","IWM","DIA"))
        provider,state=self.factory(("SESSION_TIMEOUT","SESSION_CONNECTION_ERROR",None))
        job=self.job(identity,provider,workers=4)
        result=job.run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],4);self.assertEqual(state["logins"],3)
        self.assertEqual(len(state["calls"]),8);self.assertEqual(sum(self.waits),90)
        self.assertEqual(self.store.usage(identity,1000000),(11,80))
        self.assertEqual(job.budget.requests,11)
        with self.store.read() as c:
            rows=list(c.execute("SELECT part,outcome FROM baseline_requests"))
            self.assertEqual(len(rows),8)
            self.assertTrue(all("#retry" not in r[0] and r[1]=="success" for r in rows))
        self.assertEqual(self.store.connection_state(identity)["consecutive_failures"],0)
        self.assertTrue(any(e.get("reason")=="session_reconnect" for e in self.events))

    def test_twentieth_connection_attempt_can_succeed(self):
        identity=self.enabled()
        provider,state=self.factory(("SESSION_TIMEOUT",)*19+(None,))
        result=self.job(identity,provider,workers=4).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],20)
        self.assertEqual(self.store.usage(identity,1000000),(22,20))
        self.assertEqual(self.store.connection_state(identity)["consecutive_failures"],0)
        self.assertGreaterEqual(sum(self.waits),30+60+120+16*300)

    def test_twenty_connection_failures_persist_and_block_restart_and_upload(self):
        identity=self.enabled()
        provider,state=self.factory(("SESSION_TIMEOUT",))
        with self.assertRaises(SessionSetupFailure):
            self.job(identity,provider,workers=4).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],20);self.assertEqual(state["calls"],[])
        self.assertEqual(self.store.usage(identity,1000000),(20,0))
        safe,observed=self.factory()
        job=self.job(identity,safe)
        with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):job.run(self.site,report=self.events.append)
        self.assertEqual(observed["logins"],0)
        self.assertFalse(job.upload(lambda:self.fail("upload on connection outage"),report=self.events.append)["upload_complete"])
        self.assertTrue(self.store.collection_status(identity)["connection_blocked"])
        self.now+=timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,"exhausted"):self.store.reserve_connection(identity,self.now.isoformat())

    def test_data_retry_reuses_authorized_session_and_preserves_attempts(self):
        identity=self.enabled()
        provider,state=self.factory(choose=lambda key,n:"INTERNAL" if key[2]!="catalog" and n<4 else None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],1)
        self.assertEqual(state["factories"],1)
        self.assertEqual(self.store.usage(identity,1000000),(6,50))
        self.assertEqual(self.store.attempt_state("SPY","2026-09-25","2026-09-25")["attempt"],4)

    def test_twentieth_data_attempt_succeeds_without_reset_or_twenty_first(self):
        identity=self.enabled()
        provider,state=self.factory(choose=lambda key,n:"UNAVAILABLE" if key[2]=="catalog" and n<20 else None)
        result=self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],1)
        latest=self.store.attempt_state("SPY","2026-09-25","catalog")
        self.assertEqual((latest["attempt"],latest["outcome"]),(20,"success"))
        self.assertEqual(self.store.usage(identity,1000000),(22,210))
        with self.assertRaisesRegex(ValueError,"forbidden"):
            self.store.reserve_retry("SPY","2026-09-25","catalog",identity,self.now.isoformat(),21)
        before=len(state["calls"]);logins=state["logins"]
        self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual((len(state["calls"]),state["logins"]),(before,logins))

    def test_data_exhaustion_is_twenty_total_and_other_tickers_continue(self):
        identity=self.enabled(symbols=("SPY","QQQ"))
        provider,state=self.factory(choose=lambda key,n:"UNAVAILABLE" if key[0]=="SPY" else None)
        result=self.job(identity,provider,workers=2).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(result["skipped_days"],1)
        self.assertEqual(state["calls"].count(("SPY","2026-09-25","catalog")),20)
        self.assertEqual(state["logins"],1)

    def test_prior_three_setup_failures_resume_without_modifying_original_receipts(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity)
        self.assertEqual(self.store.collection_status(identity)["setup_blocked_days"],1)
        self.store.configure_resilient_recovery(identity)
        provider,state=self.factory()
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(self.store.attempt_state("SPY","2026-09-25","catalog")["attempt"],4)
        with self.store.read() as c:self.assertEqual(dict(c.execute(
            "SELECT * FROM baseline_requests WHERE part='catalog#retry3'").fetchone()),parent)
        self.assertEqual(self.store.usage(identity,1000000),(6,20))

    def test_interrupted_connection_counts_and_restart_honors_backoff(self):
        identity=self.enabled()
        self.store.reserve_connection(identity,self.now.isoformat())
        self.assertEqual(self.store.usage(identity,1000000),(1,0))
        self.assertTrue(self.store.collection_status(identity)["connection_pending"])
        with self.assertRaisesRegex(ValueError,"cooldown"):self.store.reserve_connection(identity,self.now.isoformat())
        provider,state=self.factory()
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(sum(self.waits),30)
        self.assertEqual(self.store.usage(identity,1000000),(4,20))
        self.assertEqual(self.store.connection_state(identity)["admissions"],2)

    def test_successful_connection_breaks_the_consecutive_failure_streak(self):
        identity=self.enabled()
        for code in ("SESSION_TIMEOUT",)*3+("success",):
            seq=self.store.reserve_connection(identity,self.now.isoformat())
            self.store.finish_connection(identity,seq,code,self.now.isoformat())
            self.now+=timedelta(minutes=5)
        self.assertEqual(self.store.connection_state(identity)["consecutive_failures"],0)
        seq=self.store.reserve_connection(identity,self.now.isoformat())
        self.store.finish_connection(identity,seq,"SESSION_TIMEOUT",self.now.isoformat())
        self.assertEqual(self.store.connection_state(identity)["consecutive_failures"],1)
        self.assertEqual(self.store.usage(identity,1000000),(5,0))

    def test_rejected_credentials_and_unknown_setup_remain_terminal(self):
        for code in ("AUTHENTICATION_REJECTED","SESSION_SETUP_FAILED"):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                self.store=BaselineStore(Path(directory));self.store.initialize_baseline()
                identity=self.enabled();provider,state=self.factory((code,))
                with self.assertRaises(SessionSetupFailure):
                    self.job(identity,provider).run(self.site,report=self.events.append)
                self.assertEqual(state["logins"],1);self.assertEqual(state["calls"],[])
                self.assertTrue(self.store.connection_state(identity)["blocked"])
                safe,observed=self.factory()
                with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):
                    self.job(identity,safe).run(self.site,report=self.events.append)
                self.assertEqual(observed["logins"],0)

    def test_connection_receipts_are_immutable_and_hash_pinned(self):
        identity=self.enabled();stamp=self.now.isoformat()
        seq=self.store.reserve_connection(identity,stamp)
        self.store.finish_connection(identity,seq,"success",stamp)
        self.store.finish_connection(identity,seq,"success",stamp)
        with self.assertRaisesRegex(ValueError,"receipt_conflict"):
            self.store.finish_connection(identity,seq,"SESSION_TIMEOUT",stamp)
        self.store.write("UPDATE baseline_runs SET payload=? WHERE id=?",
            (json.dumps({"version":1,"started_at":"2026-09-30T20:00:00+00:00"}),"connection-start:"+identity+":000001"))
        with self.assertRaisesRegex(ValueError,"connection_record"):self.store.connection_state(identity)

    def test_original_global_request_cap_includes_logins_and_data(self):
        identity=self.enabled(requests=3)
        provider,state=self.factory(("SESSION_TIMEOUT",None))
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"request_cap"):
                self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],2);self.assertEqual(len(state["calls"]),1)
        self.assertEqual(self.store.usage(identity,1000000),(3,10))

    def test_deadline_stops_connection_backoff_without_another_login(self):
        identity=self.enabled(deadline=(self.now+timedelta(seconds=10)).isoformat())
        provider,state=self.factory(("SESSION_TIMEOUT",None))
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):
            self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],1);self.assertEqual(state["calls"],[])

    def test_unknown_data_bytes_block_login_and_are_not_refunded(self):
        identity=self.enabled()
        self.store.reserve_part("SPY","2026-09-25","catalog",identity,self.now.isoformat())
        self.store.finish_part("SPY","2026-09-25","catalog","ProviderFailure",None,{"outcome":"INTERNAL","response_bytes":None})
        provider,state=self.factory()
        with self.assertRaisesRegex(RuntimeError,"response_byte_cap"):
            self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],0);self.assertEqual(self.store.usage(identity,1000000),(1,1000000))

    def test_live_epoch_change_requires_new_login_and_reuses_completed_part(self):
        identity=self.enabled();self.store.configure_daytime(identity)
        marker=self.store.path.parent/"theta-session-generation"
        marker.write_text("a"*32)
        def choose(key,n):
            if key[2]=="catalog":marker.write_text("b"*32)
        provider,state=self.factory(choose=choose)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],2)
        self.assertEqual(len(state["calls"]),2);self.assertEqual(self.store.usage(identity,1000000),(4,20))

    def test_scheduled_guard_drains_and_renews_connection_without_refetch(self):
        identity=self.enabled(pilot=False,deadline="2026-10-01T23:00:00+00:00")
        self.store.configure_daytime(identity)
        def choose(key,n):
            if key[2]=="catalog":self.now=datetime(2026,10,1,11,10,tzinfo=timezone.utc)
        provider,state=self.factory(choose=choose)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],2)
        self.assertEqual(len(state["calls"]),2)
        self.assertGreaterEqual(sum(self.waits),30*60)

    def test_live_priority_defers_authentication_without_spending_admission(self):
        identity=self.enabled();self.store.configure_daytime(identity)
        provider,state=self.factory();job=self.job(identity,provider)
        from quant_data.options.monitor.admission import BackgroundBusy
        from contextlib import contextmanager
        original=job._coordination.background;attempts=[0]
        @contextmanager
        def background(*args,**kwargs):
            attempts[0]+=1
            if attempts[0]==1:
                self.assertEqual(self.store.usage(identity,1000000),(0,0))
                raise BackgroundBusy("live_collection")
            with original(*args,**kwargs) as epoch:yield epoch
        job._coordination.background=background
        result=job.run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1);self.assertEqual(state["logins"],1)
        self.assertEqual(self.store.usage(identity,1000000),(3,20))

    def test_stop_during_connection_wait_never_logs_in_again(self):
        identity=self.enabled();provider,state=self.factory(("SESSION_TIMEOUT",None))
        job=self.job(identity,provider)
        def sleep(seconds):
            self.sleep(seconds);job.stopping.set()
        job.sleep=sleep
        with self.assertRaisesRegex(RuntimeError,"baseline_stopped"):job.run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],1);self.assertEqual(state["calls"],[])

    def test_other_run_does_not_gain_contract_retries_or_connection_history(self):
        old=self.plan(requests=50);self.enable(old);self.setup_policy(old)
        self.exhausted_setup(old)
        identity=self.enabled(requests=51)
        latest=self.store.attempt_state("SPY","2026-09-25","catalog")
        self.assertFalse(retryable(latest,identity,self.store.recovery_policy(identity)))
        self.assertEqual(self.store.connection_state(identity)["admissions"],0)

    def test_login_that_finishes_after_deadline_cannot_dispatch_data(self):
        identity=self.enabled(deadline=(self.now+timedelta(seconds=5)).isoformat())
        normal,state=self.factory()
        def provider():
            base=normal()
            def create(*a,**kw):
                value=base(*a,**kw);self.now+=timedelta(seconds=6);return value
            return create
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):
            self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],1);self.assertEqual(state["calls"],[])
        self.assertEqual(self.store.usage(identity,1000000),(1,0))
        self.assertEqual(state["closed"],1)
