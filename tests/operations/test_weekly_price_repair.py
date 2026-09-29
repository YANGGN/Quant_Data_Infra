import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ValidationError, ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.stores import quiet_immutable_read_connection, writer_connection
from quant_data.operations import weekly_price_repair as weekly
from quant_data.operations.collection_price_windows import HistoryPriceWindow, history_price_units, SelectedPriceHistoryPublisher
from quant_data.operations.collection_queue import QueueResponse, atomic
from quant_data.operations.equibles_transcript_backfill import private_directory
from tests.operations import test_collection_prices as fixtures

CUT = "2026-09-12T06:00:00Z"
START, END = "2026-09-07", "2026-09-11"
DAYS = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]

class WeeklyClockTests(unittest.TestCase):
    def test_due_week_boundaries_catchup_and_dst(self):
        cases = {
            "2026-09-12T05:59:59Z": ("2026-08-31", "2026-09-04"),
            CUT: (START, END),
            "2026-09-14T15:00:00Z": (START, END),
            "2026-11-07T07:00:00Z": ("2026-11-02", "2026-11-06"),
        }
        for value, expected in cases.items():
            _, start, end = weekly.due_week(datetime.fromisoformat(value))
            self.assertEqual((str(start), str(end)), expected)
        with self.assertRaises(ValidationError):
            weekly.due_week(datetime(2026, 9, 12))

    def test_exchange_holidays_are_distinct(self):
        self.assertEqual(weekly.exchange_sessions("XNYS", START, END), tuple(DAYS))
        self.assertIn(START, weekly.exchange_sessions("XLON", START, END))
        self.assertNotIn("2026-12-25", weekly.exchange_sessions("XNYS", "2026-12-21", "2026-12-25"))

    def test_status_slot_and_summary(self):
        from quant_data.inspector_fetch_status import _slots
        slots, supported = _slots(["{ OnCalendar=Sat *-*-* 02:00:00 America/Toronto ; next_elapse=n/a }"], date(2026,9,7))
        self.assertTrue(supported)
        self.assertEqual([s.isoformat() for s in slots], ["2026-09-12T06:00:00+00:00"])

    def test_initial_activation_and_reentry_do_not_fetch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(weekly, "ROOT", Path(tmp)), patch.object(weekly, "host_fetch") as fetch:
            base = Path(tmp)/weekly.STATE
            private_directory(base)
            atomic(base/"activation.json", {"first_slot":"2099-01-03T02:00:00-05:00"})
            self.assertEqual(weekly.run_live()["outcome"], "not_due")
            fetch.assert_not_called()

    def test_completed_or_interrupted_period_never_restarts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(weekly, "ROOT", Path(tmp)), patch.object(weekly, "host_fetch") as fetch:
            base = Path(tmp)/weekly.STATE
            _, _, end = weekly.due_week(datetime.now(weekly.timezone.utc))
            root = base/str(end)
            private_directory(base)
            private_directory(root)
            atomic(base/"activation.json", {"first_slot":"2026-01-03T02:00:00-05:00"})
            atomic(root/"started.json", {"at":CUT})
            with self.assertRaises(ConflictError):
                weekly.run_live()
            atomic(root/"result.json", {"outcome":"complete_with_gaps","requests":1})
            self.assertEqual(weekly.run_live()["requests"],1)
            fetch.assert_not_called()


class WeeklyStartupTests(unittest.TestCase):
    def setUp(self):
        from tests.operations.test_collection_price_quarantine import QuarantineActivationTests
        self.q = QuarantineActivationTests("runTest")
        self.q.setUp()
        self.addCleanup(self.q.doCleanups)
        self.q.save()
        self.activation = {"price_population_sha256": self.q.population}
        private_directory(self.q.root / weekly.STATE)
        atomic(self.q.root / weekly.STATE / "activation.json", self.activation)
        self.cutoff = "2026-09-09T05:00:00Z"

    def context(self, activation=None):
        with patch.object(weekly, "ROOT", self.q.root), patch.object(
                weekly.StoreMap, "four_explicit", return_value=self.q.f.f.stores), patch.object(
                weekly, "load_bindings", return_value={"daily_prices": self.q.f.scope.binding}), patch.object(
                weekly, "load_stage12b_incremental_market_v1_scope"), patch.object(
                weekly, "load_stage12_market_v1_scope"), patch.object(
                weekly.Stage12BIncrementalCollector, "_for_selected_market_prices") as build, patch.object(
                weekly, "host_fetch") as fetch:
            try:
                result = weekly.live_context(
                    self.activation if activation is None else activation, self.cutoff)
            except Exception:
                build.assert_not_called()
                raise
            finally:
                fetch.assert_not_called()
            build.assert_called_once()
            self.assertEqual(build.call_args.kwargs["selection"], result[1])
            return result

    def test_unchanged_weekly_population_still_starts(self):
        self.assertEqual(self.context()[1], self.q.f.scope)

    def test_issuer_only_revision_starts_with_current_pin_and_preserves_evidence(self):
        updated = self.q.revised_mapping()
        before = mutation_fingerprint(self.q.f.f.stores)
        receipts = {p: p.read_bytes() for p in self.q.root.rglob("*.json")}
        self.assertNotEqual(weekly.price_population_sha256(updated), self.q.population)
        self.assertEqual(self.context()[1], updated)
        self.assertEqual(before, mutation_fingerprint(self.q.f.f.stores))
        self.assertEqual(receipts, {p: p.read_bytes() for p in receipts})

    def test_changed_price_identity_is_rejected_before_collector_creation(self):
        self.q.revised_mapping(price_change=True)
        before = mutation_fingerprint(self.q.f.f.stores)
        with self.assertRaises(ConflictError):
            self.context()
        self.assertEqual(before, mutation_fingerprint(self.q.f.f.stores))

    def test_daily_approval_cannot_replace_a_different_weekly_calendar_pin(self):
        self.q.revised_mapping()
        with self.assertRaisesRegex(ConflictError, "calendar scope needs review"):
            self.context({"price_population_sha256": "f" * 64})

    def test_original_history_and_quarantine_hashes_remain_required(self):
        from quant_data.operations.collection_price_quarantine import QUALITY_RECEIPT, AUDIT_RECEIPT
        from quant_data.operations.selected_price_refresh import HISTORY_COMPLETION
        self.q.revised_mapping()
        for receipt in (HISTORY_COMPLETION, QUALITY_RECEIPT, AUDIT_RECEIPT):
            with self.subTest(receipt=receipt):
                self.q.save()
                atomic(self.q.root / receipt, b"{}", replace=True)
                with self.assertRaises(ConflictError):
                    self.context()

    def test_missing_or_future_original_approval_cutoff_cannot_authorize_revision(self):
        self.q.revised_mapping()
        for timestamp in (None, "2026-09-09T06:00:00Z"):
            with self.subTest(timestamp=timestamp):
                self.q.save()
                path = self.q.root / weekly.LIVE_ACTIVATION
                activation = json.loads(path.read_text())
                activation["at"] = timestamp
                atomic(path, activation, replace=True)
                with self.assertRaises(ConflictError):
                    self.context()

class WeeklyPublicationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SelectedPriceTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        state = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(state.cleanup)
        self.root = Path(state.name)/"weekly"
        self.rows = weekly.selected_market_rows(self.f.f.stores,self.f.scope,cutoff=CUT)
        self.row = json.loads(self.f.body)[0]
        # Seed observed earlier history through the real publisher; identities stay immutable.
        requests=history_price_units(self.f.f.stores,self.f.scope,
            windows=tuple(HistoryPriceWindow(r["provider_symbol"],"2026-09-04","2026-09-04") for r in self.rows),cutoff=CUT)
        publisher=SelectedPriceHistoryPublisher(stores=self.f.f.stores,selection=self.f.scope,
            cutoff=CUT,collector=self.f.collector,requests=requests)
        for unit, _ in requests:
            self.publish(publisher,unit,self.body(unit.subject,["2026-09-04"]))

    def body(self, symbol="AAPL", dates=DAYS):
        return json.dumps([{**self.row,"symbol":symbol,"date":d} for d in reversed(dates)]).encode()

    def publisher(self):
        audit = weekly.audit_week(self.f.f.stores,self.rows,START,END)
        requests = history_price_units(self.f.f.stores,self.f.scope,
            windows=(HistoryPriceWindow("AAPL",START,END),),cutoff=CUT)
        pub = weekly.WeeklyPricePublisher(stores=self.f.f.stores,selection=self.f.scope,
            cutoff=CUT,collector=copy.copy(self.f.collector),requests=requests,audit=audit)
        return pub, requests[0][0]

    def publish(self, pub, unit, body):
        return pub(unit=unit,retained=None,body=body,receipt={
            "request_id":unit.request_id,"unit_id":unit.unit_id,"status":200,
            "content_sha256":hashlib.sha256(body).hexdigest(),"captured_at":CUT})


    def test_retired_symbols_are_excluded_before_weekly_audit_and_fetch(self):
        rows = self.rows + tuple({"instrument_id": "retired-" + s,
            "provider_symbol": s, "asset_type": a}
            for s, a in (("ATAI", "equity"), ("IRBO", "etf")))
        observed = []
        def audit(stores, targets, start, end):
            observed.append([r["provider_symbol"] for r in targets])
            return [dict(r, missing=[], expected=[], issue=None) for r in targets]
        with patch.object(weekly, "selected_market_rows", return_value=rows), patch.object(
                weekly, "host_fetch") as fetch:
            report = weekly.repair_week(root=self.root, stores=self.f.f.stores,
                selection=self.f.scope, collector=self.f.collector, fetch_factory=fetch,
                cutoff="2026-09-22T23:00:00Z", start="2026-09-14", end="2026-09-18",
                audit_fn=audit, utcnow=lambda:datetime.fromisoformat("2026-09-22T23:00:00Z"))
        fetch.assert_not_called()
        self.assertEqual(report["requests"], 0)
        self.assertTrue(all("ATAI" not in batch and "IRBO" not in batch for batch in observed))
        self.assertEqual({r["symbol"] for r in report["excluded_symbols"]}, {"ATAI", "IRBO"})

    def test_insert_missing_preserve_racing_existing_raw_order_and_replay(self):
        pub, unit = self.publisher()
        # Simulate an overlapping writer after the gap plan was frozen.
        self.f.publish()
        old = mutation_fingerprint(self.f.f.stores)
        with quiet_immutable_read_connection(self.f.f.stores,"market") as c:
            original = tuple(c.execute("SELECT * FROM stage10_daily_price_versions WHERE trade_date=?", (DAYS[0],)).fetchone())
        values = json.loads(self.body())
        for row in values:
            if row["date"] == DAYS[0]:
                row["volume"] += 100
        body = json.dumps(values).encode()
        result = self.publish(pub,unit,body)
        self.assertEqual(result["written_versions"],3)
        with quiet_immutable_read_connection(self.f.f.stores,"market") as c:
            self.assertEqual(tuple(c.execute("SELECT * FROM stage10_daily_price_versions WHERE trade_date=?",(DAYS[0],)).fetchone()),original)
            capture = c.execute("SELECT response_bytes,request_scope_json FROM stage10_daily_price_captures WHERE capture_id=?",(result["capture_id"],)).fetchone()
            self.assertEqual(capture[0],body)
            self.assertEqual(json.loads(capture[1])["missing_price_repair_contract"],"v1")
            sources = [tuple(r) for r in c.execute("SELECT trade_date,source_row FROM stage10_daily_price_versions WHERE trade_date>? ORDER BY trade_date",(DAYS[0],))]
            self.assertEqual(sources,[(DAYS[1],3),(DAYS[2],2),(DAYS[3],1)])
        before = mutation_fingerprint(self.f.f.stores)
        self.assertEqual(self.publish(pub,unit,body)["outcome"],"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))
        self.assertNotEqual(old,before)

    def test_partial_holiday_wrong_symbol_bad_ohlc_fail_without_writes(self):
        pub, unit = self.publisher()
        before = mutation_fingerprint(self.f.f.stores)
        bad = [b"[]",self.body(dates=DAYS[:-1]),self.body(dates=[START]+DAYS),self.body(symbol="MSFT")]
        values = json.loads(self.body());values[0]["low"]=99999
        bad.append(json.dumps(values).encode())
        for body in bad:
            with self.assertRaises(ValidationError):
                self.publish(pub,unit,body)
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))

    def run_week(self, status=200, partial=False, explode=False, empty_first=False):
        self.calls = []
        def fetch(*,unit,timeout_seconds,max_bytes):
            self.calls.append(unit)
            self.assertEqual(max_bytes,65536)
            if explode:
                raise RuntimeError("offline simulated timeout")
            body=b"[]" if empty_first and len(self.calls)==1 else self.body(unit.subject,DAYS[:-1] if partial else DAYS)
            return QueueResponse(status,body,CUT)
        return weekly.repair_week(root=self.root,stores=self.f.f.stores,selection=self.f.scope,
            collector=self.f.collector,fetch_factory=lambda:fetch,cutoff=CUT,start=START,end=END,
            utcnow=lambda:datetime.fromisoformat(CUT))

    def test_complete_queue_then_zero_gaps_zero_credentials(self):
        result=self.run_week()
        self.assertEqual(result["outcome"],"complete")
        self.assertEqual(result["requests"],4)
        self.assertEqual(result["missing_before"],16)
        self.assertEqual(result["missing_after"],0)
        self.assertTrue(all(dict(u.parameters)=={"from":START,"to":END,"symbol":u.subject} for u in self.calls))
        before=mutation_fingerprint(self.f.f.stores)
        def forbidden():self.fail("credentials accessed for complete week")
        result=weekly.repair_week(root=self.root.parent/"second",stores=self.f.f.stores,
            selection=self.f.scope,collector=self.f.collector,fetch_factory=forbidden,
            cutoff=CUT,start=START,end=END)
        self.assertEqual(result["requests"],0)
        self.assertEqual(before,mutation_fingerprint(self.f.f.stores))

    def test_http_failure_one_attempt_no_retry_and_missing_remains(self):
        result=self.run_week(status=403)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result["requests"],1)
        self.assertEqual(result["missing_after"],16)
        self.assertEqual(result["outcome"],"complete_with_gaps")

    def test_uncertain_acquisition_is_charged_and_not_retried(self):
        result=self.run_week(explode=True)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result["requests"],1)
        self.assertEqual(result["failure"],"ConflictError")
        self.assertIsNotNone(weekly._read(self.root/"queue/ledger.json")["pending"])

    def test_incomplete_response_is_retained_and_reported(self):
        result=self.run_week(partial=True)
        self.assertEqual(result["requests"],1)
        self.assertGreater(result["received_bytes"],0)
        self.assertEqual(result["missing_after"],16)
        self.assertEqual(result["failure"],"ValidationError")
        self.assertEqual(len(list((self.root/"queue/responses").glob("*.json"))),1)

    def test_listing_boundary_and_unknown_calendar_are_explicit(self):
        audit=weekly.audit_week(self.f.f.stores,self.rows,"2026-08-31","2026-09-04")
        self.assertTrue(all(r["issue"]=="listing_boundary_unverified" and not r["missing"] for r in audit))
        altered=[{**self.rows[0],"asset_type":"index","provider_symbol":"^UNKNOWN"}]
        self.assertEqual(weekly.audit_week(self.f.f.stores,altered,START,END)[0]["issue"],"unreviewed_exchange_calendar")


    def test_empty_first_symbol_is_retained_without_blocking_other_symbols(self):
        result = self.run_week(empty_first=True)
        first = self.calls[0]
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["published"], 3)
        self.assertEqual(result["missing_after"], 4)
        self.assertIsNone(result["failure"])
        self.assertEqual(result["outcome"], "complete_with_gaps")
        self.assertEqual(result["symbol_outcomes"][0]["symbol"], first.subject)
        self.assertEqual(result["symbol_outcomes"][0]["outcome"], "empty_response")
        self.assertFalse((self.root/"queue/publications"/(first.unit_id+".json")).exists())
        self.assertEqual(weekly._read(self.root/"queue/ledger.json")["units"][first.unit_id]["status"], "captured")
        self.assertTrue((self.root/"symbol-outcomes"/(first.unit_id+".json")).is_file())

    def legacy_empty_failure(self):
        original = weekly.WeeklyPricePublisher.__call__
        def legacy(publisher, **kwargs):
            if json.loads(kwargs["body"]) == []:
                raise ValidationError("Legacy empty response failure")
            return original(publisher, **kwargs)
        with patch.object(weekly.WeeklyPricePublisher, "__call__", legacy):
            return self.run_week(empty_first=True)

    def test_manual_recovery_reuses_original_empty_response_and_never_restarts(self):
        project = self.root.parent
        self.root = project / weekly.STATE / END
        private_directory(self.root.parent)
        prior = self.legacy_empty_failure()
        first = self.calls[0]
        self.assertEqual(prior["requests"], 1)
        self.assertEqual(prior["published"], 0)
        atomic(self.root/"started.json", {"at":CUT, "scope":self.f.scope.scope_sha256})
        atomic(self.root/"result.json", prior)
        originals = {name:(self.root/name).read_bytes() for name in ("before.json","after.json","started.json","result.json")}
        calls = []
        def fetch(*, unit, **kwargs):
            calls.append(unit.subject)
            self.assertNotEqual(unit.subject, first.subject)
            return QueueResponse(200, self.body(unit.subject), CUT)
        with patch.object(weekly, "ROOT", project), patch.object(
                weekly, "live_context", return_value=(self.f.f.stores,self.f.scope,self.f.collector)), patch.object(
                weekly, "host_fetch", return_value=fetch):
            result = weekly.recover_week(END)
            self.assertEqual(result["maximum_requests"], 3)
            self.assertEqual(result["requests"], 3)
            self.assertEqual(result["published"], 3)
            self.assertEqual(result["missing_after"], 4)
            self.assertEqual(len(calls), 3)
            before = mutation_fingerprint(self.f.f.stores)
            self.assertEqual(weekly.recover_week(END), result)
            self.assertEqual(len(calls), 3)
            self.assertEqual(mutation_fingerprint(self.f.f.stores), before)
        self.assertEqual(originals, {name:(self.root/name).read_bytes() for name in originals})
        self.assertEqual(weekly.queue_usage(self.root/"queue")[0], 4)

    def test_recovery_rejects_uncertain_original_attempt_and_invalid_week(self):
        project = self.root.parent
        self.root = project / weekly.STATE / END
        private_directory(self.root.parent)
        prior = self.run_week(explode=True)
        atomic(self.root/"started.json", {"at":CUT, "scope":self.f.scope.scope_sha256})
        atomic(self.root/"result.json", prior)
        with patch.object(weekly, "ROOT", project), patch.object(weekly, "host_fetch") as fetch:
            with self.assertRaisesRegex(ConflictError, "uncertain original request"):
                weekly.recover_week(END)
            for value in ("2026-09-12", "../2026-09-11", "20260911"):
                with self.assertRaises(ValidationError):
                    weekly.recover_week(value)
            fetch.assert_not_called()

    def test_recovery_new_request_cap_and_original_symbol_scope(self):
        self.legacy_empty_failure()
        first = self.calls[0]
        with patch.object(weekly, "host_fetch") as fetch:
            result = weekly.repair_week(
                root=self.root.parent/"recovery", queue_root=self.root/"queue",
                stores=self.f.f.stores, selection=self.f.scope, collector=self.f.collector,
                fetch_factory=fetch, cutoff=CUT, start=START, end=END,
                authorized_symbols=(first.subject,), max_new_requests=0,
                utcnow=lambda:datetime.fromisoformat(CUT))
            self.assertEqual(result["requests"], 0)
            self.assertEqual(result["planned_requests"], 1)
            self.assertEqual(result["published"], 0)
            self.assertEqual(result["symbol_outcomes"][0]["outcome"], "empty_response")
            fetch.assert_not_called()
            blocked = weekly.repair_week(
                root=self.root.parent/"blocked", queue_root=self.root/"queue",
                stores=self.f.f.stores, selection=self.f.scope, collector=self.f.collector,
                fetch_factory=fetch, cutoff=CUT, start=START, end=END,
                max_new_requests=0, utcnow=lambda:datetime.fromisoformat(CUT))
            self.assertEqual(blocked["requests"], 0)
            self.assertEqual(blocked["failure"], "invocation_budget")
            fetch.assert_not_called()

    def test_interrupted_recovery_and_cli_dispatch_do_not_repeat_requests(self):
        from contextlib import redirect_stdout
        import io
        project = self.root.parent
        self.root = project / weekly.STATE / END
        private_directory(self.root.parent)
        prior = self.legacy_empty_failure()
        atomic(self.root/"started.json", {"at":CUT, "scope":self.f.scope.scope_sha256})
        atomic(self.root/"result.json", prior)
        private_directory(self.root/"manual-recovery")
        atomic(self.root/"manual-recovery/started.json", {"at":CUT})
        with patch.object(weekly, "ROOT", project), patch.object(weekly, "host_fetch") as fetch:
            with self.assertRaisesRegex(ConflictError, "recovery already attempted"):
                weekly.recover_week(END)
            fetch.assert_not_called()
        with patch.object(weekly, "recover_week", return_value={"outcome":"complete"}) as recover, patch.object(weekly, "run_live") as live, redirect_stdout(io.StringIO()):
            self.assertEqual(weekly.main(["--recover-week", END]), 0)
            recover.assert_called_once_with(END)
            live.assert_not_called()


    def test_recovery_refuses_lost_or_changed_original_accounting(self):
        project = self.root.parent
        self.root = project / weekly.STATE / END
        private_directory(self.root.parent)
        prior = self.legacy_empty_failure()
        atomic(self.root/"started.json", {"at":CUT, "scope":self.f.scope.scope_sha256})
        atomic(self.root/"result.json", prior)
        path = self.root/"queue/ledger.json"
        retained = path.read_bytes()
        with patch.object(weekly, "ROOT", project), patch.object(weekly, "live_context") as context, patch.object(weekly, "host_fetch") as fetch:
            path.unlink()
            with self.assertRaisesRegex(ConflictError, "accounting is missing"):
                weekly.recover_week(END)
            atomic(path, retained, replace=True)
            state = json.loads(retained)
            next(iter(state["usage"].values()))["charged_attempts"] += 1
            atomic(path, state, replace=True)
            with self.assertRaisesRegex(ConflictError, "accounting differs"):
                weekly.recover_week(END)
            atomic(path, retained, replace=True)
            state = json.loads(retained)
            next(iter(state["usage"].values()))["received_bytes"] += 1
            atomic(path, state, replace=True)
            with self.assertRaisesRegex(ConflictError, "accounting differs"):
                weekly.recover_week(END)
            context.assert_not_called()
            fetch.assert_not_called()
            self.assertFalse((self.root/"manual-recovery/started.json").exists())

    def test_recovery_preserves_original_provider_stop_before_new_units(self):
        project = self.root.parent
        self.root = project / weekly.STATE / END
        private_directory(self.root.parent)
        prior = self.run_week(status=403)
        atomic(self.root/"started.json", {"at":CUT, "scope":self.f.scope.scope_sha256})
        atomic(self.root/"result.json", prior)
        with patch.object(weekly, "ROOT", project), patch.object(weekly, "live_context") as context, patch.object(weekly, "host_fetch") as fetch:
            with self.assertRaisesRegex(ConflictError, "provider stop requires reconciliation"):
                weekly.recover_week(END)
            context.assert_not_called()
            fetch.assert_not_called()
