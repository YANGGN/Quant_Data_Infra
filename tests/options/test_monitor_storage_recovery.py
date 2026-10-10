"""Offline regressions for watchdog lock contention and exact failed-save recovery."""
import json, unittest
from unittest.mock import patch
from quant_data.errors import ConflictError
from quant_data.options.monitor.baseline_store import BaselineStore, retryable
from tests.options import test_monitor_recovery as fixtures

class StorageRecoveryTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    plan=fixtures.RecoveryTests.plan
    sleep=fixtures.RecoveryTests.sleep
    provider=fixtures.RecoveryTests.provider
    site=fixtures.RecoveryTests.site
    job=fixtures.RecoveryTests.job
    enable=fixtures.RecoveryTests.enable

    def failed_save(self,identity,**changes):
        receipt={"method":"option_history_ohlc","outcome":"success","response_bytes":12,
                 "rows":1,"response_sha256":"a"*64,"finished_at":self.now.isoformat()}
        receipt.update(changes)
        self.store.reserve_part("SPY","2026-09-25","2026-09-25",identity,self.now.isoformat())
        self.store.finish_part("SPY","2026-09-25","2026-09-25","ConflictError",None,receipt)

    def grant(self,identity,**kwargs):
        return self.store.configure_storage_retry(identity,"SPY","2026-09-25","2026-09-25",
                    evidence_sha256=kwargs.get("evidence_sha256","b"*64))

    def test_save_lock_retries_keep_result_without_repeating_provider(self):
        identity=self.plan();self.enable(identity)
        provider,state=self.provider(lambda key,n:None)
        original=self.store.write;timeouts=[]
        def write(statement,params=()):
            if statement.startswith("UPDATE baseline_requests SET outcome=") and params[0]=="success" and len(timeouts)<2:
                timeouts.append(True)
                raise ConflictError("Timed out acquiring the physical store lock")
            return original(statement,params)
        with patch.object(self.store,"write",side_effect=write):
            result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(len(state["calls"]),2)
        self.assertEqual(len(timeouts),2)
        self.assertEqual(self.store.usage(identity,1000000),(2,20))
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM baseline_requests WHERE outcome!='success'").fetchone()[0],0)

    def test_persistent_lock_timeout_stops_after_three_local_attempts(self):
        identity=self.plan()
        self.store.reserve_part("SPY","2026-09-25","catalog",identity,self.now.isoformat())
        with patch.object(self.store,"write",side_effect=ConflictError("Timed out acquiring the physical store lock")) as write:
            with self.assertRaises(ConflictError):
                self.store.finish_part("SPY","2026-09-25","catalog","success",[],{"response_bytes":0})
        self.assertEqual(write.call_count,3)
        self.assertEqual(self.store.part("SPY","2026-09-25","catalog"),("started",None))

    def test_other_conflicts_are_not_retried(self):
        with patch.object(self.store,"write",side_effect=ConflictError("different conflict")) as write:
            with self.assertRaisesRegex(ConflictError,"different conflict"):
                self.store.finish_part("SPY","2026-09-25","catalog","success",[],{})
        self.assertEqual(write.call_count,1)

    def test_exact_grant_resumes_only_failed_part_and_preserves_receipt(self):
        identity=self.plan();self.enable(identity);session="2026-09-25"
        self.store.reserve_part("SPY",session,"catalog",identity,self.now.isoformat())
        self.store.finish_part("SPY",session,"catalog","success",[session],{"response_bytes":10})
        self.failed_save(identity)
        with self.store.read() as c:original=tuple(c.execute("SELECT * FROM baseline_requests WHERE part=?",(session,)).fetchone())
        state=self.store.attempt_state("SPY",session,session)
        self.assertFalse(retryable(state,identity,self.store.recovery_policy(identity)))
        grant=self.grant(identity)
        self.assertEqual(grant,self.grant(identity))
        self.assertEqual(grant["attempt"],2)
        self.assertEqual(self.store.collection_status(identity)["retry_pending_days"],1)
        provider,calls=self.provider(lambda key,n:None)
        result=self.job(identity,provider).run(self.site,report=self.events.append)
        self.assertEqual(calls["calls"],[("SPY",session,session)])
        self.assertEqual(result["saved_days"],1)
        self.assertEqual(self.store.usage(identity,1000000),(3,32))
        with self.store.read() as c:
            self.assertEqual(tuple(c.execute("SELECT * FROM baseline_requests WHERE part=?",(session,)).fetchone()),original)
        self.assertEqual(self.grant(identity),grant)
        self.assertEqual(self.store.plan(identity)["max_requests"],1000)

    def test_unknown_bytes_and_no_data_are_not_eligible(self):
        for changes in ({"response_bytes":None},{"response_bytes":True},{"response_bytes":-1},
                        {"response_sha256":None},{"outcome":"no_data"},{"rows":0}):
            with self.subTest(changes=changes):
                self.setUp()
                identity=self.plan();self.enable(identity);self.failed_save(identity,**changes)
                with self.assertRaisesRegex(ValueError,"storage_retry_forbidden"):self.grant(identity)

    def test_original_receipt_tamper_fails_closed(self):
        identity=self.plan();self.enable(identity);self.failed_save(identity);self.grant(identity)
        self.store.write("UPDATE baseline_requests SET receipt=? WHERE part='2026-09-25'",
            (json.dumps({"outcome":"success","response_bytes":13}),))
        with self.assertRaisesRegex(ValueError,"storage_retry_parent_changed"):
            self.store.attempt_state("SPY","2026-09-25","2026-09-25")

    def test_grant_replay_cannot_change_evidence_or_other_run(self):
        identity=self.plan();self.enable(identity);self.failed_save(identity);self.grant(identity)
        with self.assertRaisesRegex(ValueError,"storage_retry_policy_conflict"):self.grant(identity,evidence_sha256="c"*64)
        other=self.plan(requests=999);self.enable(other)
        with self.assertRaisesRegex(ValueError,"storage_retry_forbidden"):self.grant(other)

    def test_grant_cannot_exceed_existing_attempt_limit(self):
        identity=self.plan();self.enable(identity);self.failed_save(identity)
        self.store.write("INSERT INTO baseline_requests SELECT symbol,session,part||'#retry3',run_id,started_at,outcome,payload,receipt FROM baseline_requests WHERE part='2026-09-25'")
        with self.assertRaisesRegex(ValueError,"storage_retry_forbidden"):self.grant(identity)

    def test_empty_catalogs_do_not_resolve_thousands_of_attempts(self):
        symbols=tuple("A"+str(n) for n in range(100))
        identity=self.plan(symbols=symbols);self.enable(identity)
        for symbol in symbols:
            self.store.reserve_part(symbol,"2026-09-25","catalog",identity,self.now.isoformat())
            self.store.finish_part(symbol,"2026-09-25","catalog","success",[],{"response_bytes":0})
        from quant_data.options.monitor import baseline_store
        with patch.object(baseline_store,"attempt_state",wraps=baseline_store.attempt_state) as resolve:
            status=self.store.collection_status(identity)
        self.assertEqual(status["skipped_days"],100)
        self.assertEqual(status["pending_days"],0)
        self.assertEqual(resolve.call_count,0)

    def test_empty_catalog_with_invalid_extra_attempt_still_fails_closed(self):
        identity=self.plan();self.enable(identity)
        self.store.reserve_part("SPY","2026-09-25","catalog",identity,self.now.isoformat())
        self.store.finish_part("SPY","2026-09-25","catalog","success",[],{"response_bytes":0})
        self.store.write("INSERT INTO baseline_requests SELECT symbol,session,part||'#retry4',run_id,started_at,outcome,payload,receipt FROM baseline_requests WHERE part='catalog'")
        with self.assertRaisesRegex(ValueError,"baseline_setup_retry_unauthorized"):self.store.collection_status(identity)

    def test_successful_catalog_retry_does_not_hide_other_terminal_part(self):
        symbols=tuple("B"+str(n) for n in range(20))
        identity=self.plan(symbols=symbols);self.enable(identity)
        for symbol in symbols:
            self.store.reserve_part(symbol,"2026-09-25","catalog",identity,self.now.isoformat())
            self.store.finish_part(symbol,"2026-09-25","catalog","ProviderFailure",None,{"outcome":"UNAVAILABLE","response_bytes":0})
            self.store.reserve_retry(symbol,"2026-09-25","catalog",identity,self.now.isoformat(),2)
            self.store.finish_part(symbol,"2026-09-25","catalog#retry2","success",["2026-09-25"],{"response_bytes":10})
            self.store.reserve_part(symbol,"2026-09-25","2026-09-25",identity,self.now.isoformat())
            self.store.finish_part(symbol,"2026-09-25","2026-09-25","ValueError",None,{"outcome":"no_data","response_bytes":0})
        status=self.store.collection_status(identity)
        self.assertEqual(status["skipped_days"],20)
        self.assertEqual(status["pending_days"],0)
        self.assertTrue(status["collection_complete"])
