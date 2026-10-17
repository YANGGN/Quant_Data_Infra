"""Offline session recovery and immutable correction of proven setup-only admissions."""
import json,threading,unittest
from datetime import timedelta
from quant_data.options.monitor.baseline_store import request_fingerprint,SESSION_POLICY
from quant_data.options.monitor.transport import SessionSetupFailure
from quant_data.options.transport import ProviderFailure
from tests.options import test_monitor_recovery as recovery_fixtures

class SessionRecoveryTests(unittest.TestCase):
    setUp=recovery_fixtures.RecoveryTests.setUp
    plan=recovery_fixtures.RecoveryTests.plan
    sleep=recovery_fixtures.RecoveryTests.sleep
    provider=recovery_fixtures.RecoveryTests.provider
    site=recovery_fixtures.RecoveryTests.site
    job=recovery_fixtures.RecoveryTests.job
    enable=recovery_fixtures.RecoveryTests.enable

    def setup_policy(self,identity,rows=()):
        self.store.configure_session_recovery(identity,[
            {"symbol":r["symbol"],"session":r["session"],"part":r["part"],"request_sha256":request_fingerprint(r)}
            for r in rows],evidence_sha256="a"*64)

    def setup_provider(self,codes):
        normal,state=self.provider(lambda key,n:None)
        state["logins"]=0
        def provider():
            factory=normal()
            index=state["factories"]-1
            code=codes[min(index,len(codes)-1)]
            logged=False
            def create(*args,**kwargs):
                nonlocal logged
                if not logged:state["logins"]+=1;logged=True
                if code:raise SessionSetupFailure(code)
                return factory(*args,**kwargs)
            return create
        return provider,state

    def legacy_failure(self,identity,part="catalog"):
        self.store.reserve_part("SPY","2026-09-25",part,identity,self.now.isoformat())
        self.store.finish_part("SPY","2026-09-25",part,"RuntimeError",None,
            {"response_bytes":1000000,"finished_at":self.now.isoformat()})
        with self.store.read() as c:
            return dict(c.execute("SELECT * FROM baseline_requests WHERE part=?",(part,)).fetchone())

    def test_two_setup_failures_recover_without_false_transfer_or_duplicate_data(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        provider,state=self.setup_provider(["SESSION_TIMEOUT","SESSION_CONNECTION_ERROR",None])
        job=self.job(identity,provider,workers=1)
        result=job.run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(state["logins"],3)
        self.assertEqual(len(state["calls"]),2)
        self.assertEqual(sum(self.waits),150)
        self.assertEqual(self.store.usage(identity,1000000),(4,20))
        self.assertEqual(job.budget.snapshot(),{"data_requests":4,"received_bytes":20})

    def test_rejected_credentials_and_unknown_setup_stop_without_retry(self):
        for code in ("AUTHENTICATION_REJECTED","SESSION_SETUP_FAILED"):
            with self.subTest(code=code):
                # A separate fixture per code avoids reusing an admitted request.
                self.setUp()
                identity=self.plan();self.enable(identity);self.setup_policy(identity)
                provider,state=self.setup_provider([code])
                job=self.job(identity,provider,workers=1)
                with self.assertRaisesRegex(SessionSetupFailure,code):
                    job.run(lambda:self.fail("upload on setup failure"),report=self.events.append)
                self.assertEqual(state["logins"],1)
                self.assertEqual(state["calls"],[])
                self.assertEqual(self.store.usage(identity,1000000),(1,0))
                self.assertEqual(job.budget.requests,1)

    def test_session_retry_requires_opt_in(self):
        identity=self.plan();self.enable(identity)
        provider,state=self.setup_provider(["SESSION_TIMEOUT"])
        with self.assertRaises(SessionSetupFailure):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],1)
        self.setup_policy(identity)
        provider,state=self.setup_provider([None])
        result=self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(sum(self.waits),30)
        self.assertEqual(self.store.usage(identity,1000000),(3,20))

    def test_setup_exhaustion_stops_after_three_durable_attempts(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        provider,state=self.setup_provider(["SESSION_TIMEOUT"])
        job=self.job(identity,provider,workers=1)
        with self.assertRaisesRegex(SessionSetupFailure,"SESSION_TIMEOUT"):
            job.run(lambda:self.fail("upload on outage"),report=self.events.append)
        self.assertEqual(state["logins"],3)
        self.assertEqual(state["calls"],[])
        self.assertEqual(self.store.usage(identity,1000000),(3,0))
        self.assertEqual(job.budget.requests,3)
        self.assertEqual(self.store.collection_status(identity)["unresolved_days"],0)

    def test_setup_retry_cannot_bypass_request_or_wall_deadline(self):
        for which in ("requests","deadline"):
            with self.subTest(which=which):
                self.setUp()
                identity=self.plan(requests=1 if which=="requests" else 100,
                    deadline=(self.now+timedelta(seconds=10)).isoformat())
                self.enable(identity);self.setup_policy(identity)
                provider,state=self.setup_provider(["SESSION_TIMEOUT",None])
                with self.assertRaisesRegex(RuntimeError,"request_cap|duration_cap"):
                    self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
                self.assertEqual(state["logins"],1)
                self.assertEqual(state["calls"],[])
                self.assertEqual(self.store.usage(identity,1000000),(1,0))

    def test_reconciliation_is_hash_pinned_append_only_and_resumes_missing_part(self):
        identity=self.plan();self.enable(identity);plan=self.store.plan(identity)
        original=self.legacy_failure(identity)
        self.assertEqual(self.store.usage(identity,1000000),(1,1000000))
        self.setup_policy(identity,[original]);self.setup_policy(identity,[original])
        self.assertEqual(self.store.usage(identity,1000000),(1,0))
        self.assertEqual(self.store.collection_status(identity)["retry_pending_days"],1)
        provider,state=self.setup_provider([None])
        result=self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(len(state["calls"]),2)
        self.assertEqual(self.store.usage(identity,1000000),(3,20))
        with self.store.read() as c:
            self.assertEqual(dict(c.execute("SELECT * FROM baseline_requests WHERE part='catalog'").fetchone()),original)
        self.assertEqual(self.store.plan(identity),plan)

    def test_reconciliation_rejects_wrong_hash_other_run_and_provider_unknown(self):
        identity=self.plan();self.enable(identity);row=self.legacy_failure(identity)
        altered={**row,"started_at":"different"}
        with self.assertRaisesRegex(ValueError,"ineligible"):self.setup_policy(identity,[altered])
        other=self.plan(requests=999);self.enable(other)
        with self.assertRaisesRegex(ValueError,"ineligible"):self.setup_policy(other,[row])
        self.store.write("UPDATE baseline_requests SET outcome='ProviderFailure' WHERE part='catalog'")
        with self.store.read() as c:row=dict(c.execute("SELECT * FROM baseline_requests WHERE part='catalog'").fetchone())
        with self.assertRaisesRegex(ValueError,"ineligible"):self.setup_policy(identity,[row])
        self.assertEqual(self.store.usage(identity,1000000),(1,1000000))

    def test_reconciliation_tamper_and_policy_changes_fail_closed(self):
        identity=self.plan();self.enable(identity);row=self.legacy_failure(identity);self.setup_policy(identity,[row])
        with self.assertRaisesRegex(ValueError,"conflict"):self.setup_policy(identity)
        self.store.write("UPDATE baseline_requests SET started_at='changed' WHERE part='catalog'")
        with self.assertRaisesRegex(ValueError,"reconciliation_changed"):self.store.usage(identity,1000000)
        with self.assertRaisesRegex(ValueError,"reconciliation_changed"):self.store.attempt_state("SPY","2026-09-25","catalog")

    def test_unknown_dispatched_failure_still_charges_full_allowance(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        def provider():
            class Transport:
                def __init__(inner,root,budget,**kw):inner.budget=budget
                def request(inner,*a,**kw):
                    inner.budget.reserve_request()
                    raise RuntimeError("unknown data transfer")
                def close(inner):pass
            return Transport
        with self.assertRaisesRegex(RuntimeError,"unknown data transfer"):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(self.store.usage(identity,1000000),(1,1000000))

    def test_four_concurrent_setup_failures_are_counted_once_each(self):
        identity=self.plan(("SPY","QQQ","IWM","DIA"));self.enable(identity);self.setup_policy(identity)
        normal,state=self.provider(lambda key,n:None);barrier=threading.Barrier(4)
        generations=[0]
        def provider():
            base=normal();generations[0]+=1;first=generations[0]==1
            def create(*a,**kw):
                if first:
                    barrier.wait(timeout=10)
                    raise SessionSetupFailure("SESSION_TIMEOUT")
                return base(*a,**kw)
            return create
        job=self.job(identity,provider,workers=4)
        result=job.run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],4)
        self.assertEqual(self.store.usage(identity,1000000),(12,80))
        self.assertEqual(job.budget.snapshot(),{"data_requests":12,"received_bytes":80})
        self.assertEqual(self.store.collection_status(identity)["unresolved_days"],0)


    def test_setup_exhaustion_persists_across_restart_and_blocks_upload(self):
        identity=self.plan(("SPY","QQQ"));self.enable(identity);self.setup_policy(identity)
        # A fatal part late in the manifest must prevent all earlier dispatch.
        self.store.reserve_part("QQQ","2026-09-25","catalog",identity,self.now.isoformat())
        self.store.finish_part("QQQ","2026-09-25","catalog","ProviderFailure",None,
            {**SessionSetupFailure("SESSION_TIMEOUT").receipt,"finished_at":self.now.isoformat()})
        for attempt in (2,3):
            self.store.reserve_retry("QQQ","2026-09-25","catalog",identity,self.now.isoformat(),attempt)
            self.store.finish_part("QQQ","2026-09-25",f"catalog#retry{attempt}","ProviderFailure",None,
                {**SessionSetupFailure("SESSION_TIMEOUT").receipt,"finished_at":self.now.isoformat()})
        provider,state=self.setup_provider([None])
        for _ in range(2):
            job=self.job(identity,provider,workers=2)
            with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):
                job.run(lambda:self.fail("upload after exhausted setup"),report=self.events.append)
        status=self.store.collection_status(identity)
        self.assertEqual((status["setup_blocked_days"],status["skipped_days"],status["pending_days"]),(1,0,2))
        self.assertFalse(status["collection_complete"])
        result=job.upload(lambda:self.fail("site accessed after exhausted setup"),report=self.events.append)
        self.assertFalse(result["upload_complete"])
        self.assertEqual(state["logins"],0)
        self.assertEqual(state["calls"],[])
        self.assertEqual(self.store.usage(identity,1000000),(3,0))

    def test_persisted_rejected_and_unknown_setup_also_block_restart_and_upload(self):
        for code in ("AUTHENTICATION_REJECTED","SESSION_SETUP_FAILED"):
            with self.subTest(code=code):
                self.setUp()
                identity=self.plan();self.enable(identity);self.setup_policy(identity)
                provider,state=self.setup_provider([code])
                with self.assertRaises(SessionSetupFailure):
                    self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
                safe_provider,safe_state=self.setup_provider([None])
                job=self.job(identity,safe_provider,workers=1)
                with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):
                    job.run(self.site,report=self.events.append)
                self.assertFalse(job.upload(lambda:self.fail("site accessed after fatal setup"),report=self.events.append)["upload_complete"])
                self.assertEqual(safe_state["logins"],0)
                self.assertEqual(self.store.collection_status(identity)["setup_blocked_days"],1)


    def exhausted_setup(self,identity,code="SESSION_TIMEOUT"):
        receipt={**SessionSetupFailure(code).receipt,"finished_at":self.now.isoformat()}
        for attempt in (1,2,3):
            part="catalog" if attempt==1 else f"catalog#retry{attempt}"
            # Explicit fixture setup, never a live admission shortcut.
            self.store.reserve_part("SPY","2026-09-25",part,identity,self.now.isoformat())
            self.store.finish_part("SPY","2026-09-25",part,"ProviderFailure",None,receipt)
        with self.store.read() as c:
            return dict(c.execute("SELECT * FROM baseline_requests WHERE part='catalog#retry3'").fetchone())

    def grant(self,identity,parent):
        return self.store.configure_setup_retry_once(identity,symbol="SPY",session="2026-09-25",
            part="catalog",after_sha256=request_fingerprint(parent))

    def test_user_grant_allows_exactly_one_extra_attempt_without_reset_or_repeat(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity)
        self.grant(identity,parent);self.grant(identity,parent)
        self.assertEqual(self.store.collection_status(identity)["setup_blocked_days"],0)
        provider,state=self.setup_provider([None])
        result=self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(self.store.usage(identity,1000000),(5,20))
        self.assertEqual(sum(self.waits),120)
        with self.store.read() as c:
            self.assertEqual(dict(c.execute("SELECT * FROM baseline_requests WHERE part='catalog#retry3'").fetchone()),parent)
            self.assertEqual(c.execute("SELECT outcome FROM baseline_requests WHERE part='catalog#retry4'").fetchone()[0],"success")
        self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(len(state["calls"]),2)

    def test_failed_extra_setup_attempt_remains_blocked_and_grant_cannot_reset(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity);self.grant(identity,parent)
        provider,state=self.setup_provider(["SESSION_TIMEOUT"])
        with self.assertRaises(SessionSetupFailure):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.grant(identity,parent)
        with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],1)
        self.assertEqual(self.store.usage(identity,1000000),(4,0))
        self.assertEqual(self.store.collection_status(identity)["setup_blocked_days"],1)

    def test_extra_grant_requires_exact_parent_run_and_known_transient_setup(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity)
        with self.assertRaisesRegex(ValueError,"parent_changed"):
            self.grant(identity,{**parent,"started_at":"changed"})
        other=self.plan(requests=999);self.enable(other);self.setup_policy(other)
        with self.assertRaisesRegex(ValueError,"ineligible"):self.grant(other,parent)
        self.store.write("UPDATE baseline_requests SET receipt=? WHERE part='catalog#retry3'",
            (json.dumps(SessionSetupFailure("AUTHENTICATION_REJECTED").receipt),))
        with self.store.read() as c:rejected=dict(c.execute("SELECT * FROM baseline_requests WHERE part='catalog#retry3'").fetchone())
        with self.assertRaisesRegex(ValueError,"ineligible"):self.grant(identity,rejected)
        self.assertEqual(self.store.collection_status(identity)["setup_blocked_days"],1)


    def test_extra_grant_cannot_extend_original_request_cap(self):
        identity=self.plan(requests=3);self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity);original=self.store.plan(identity)
        self.grant(identity,parent)
        provider,state=self.setup_provider([None])
        with self.assertRaisesRegex(RuntimeError,"request_cap"):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],0)
        self.assertEqual(self.store.plan(identity),original)
        self.assertEqual(self.store.usage(identity,1000000),(3,0))

    def test_extra_grant_does_not_cover_other_part_and_detects_parent_tamper(self):
        identity=self.plan();self.enable(identity);self.setup_policy(identity)
        parent=self.exhausted_setup(identity);self.grant(identity,parent)
        receipt={**SessionSetupFailure("SESSION_TIMEOUT").receipt,"finished_at":self.now.isoformat()}
        for n in (1,2,3):
            part="2026-09-25" if n==1 else f"2026-09-25#retry{n}"
            self.store.reserve_part("SPY","2026-09-25",part,identity,self.now.isoformat())
            self.store.finish_part("SPY","2026-09-25",part,"ProviderFailure",None,receipt)
        provider,state=self.setup_provider([None])
        with self.assertRaisesRegex(ProviderFailure,"SESSION_RECOVERY_BLOCKED"):
            self.job(identity,provider,workers=1).run(self.site,report=self.events.append)
        self.assertEqual(state["logins"],0)
        self.store.write("UPDATE baseline_requests SET started_at='changed' WHERE part='catalog#retry3'")
        with self.assertRaisesRegex(ValueError,"parent_changed"):
            self.store.attempt_state("SPY","2026-09-25","catalog")
