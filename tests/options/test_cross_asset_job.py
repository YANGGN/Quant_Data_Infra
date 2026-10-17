"""Finite cross-asset backfill checks using only temporary stores and fake Theta."""
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import json
import socket
import unittest
from unittest.mock import patch

from quant_data.options.cross_asset import (
    CROSS_ASSET_ETFS, MAX_AUTHENTICATIONS, MAX_BYTES, MAX_REQUESTS,
    MAX_SECONDS, CrossAssetJob, WORK, plan_summary, reviewed_manifest,
)
from quant_data.options.transport import ProviderFailure
from quant_data.options.daily import sync_admission_directories
from quant_data.stores import StoreWriteLock
from tests.options.test_theta_compact import temporary_store
from tests.options.test_theta_daily import rows_for


ALLOWED = datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)
BLOCKED = datetime(2026, 10, 13, 11, 20, tzinfo=timezone.utc)


def batch(symbol, session, method, rows):
    params = {"symbol": symbol, "expiration": "*"}
    if method == "option_history_greeks_eod":
        params.update(start_date=session, end_date=session, version="1", underlyer_use_nbbo="True")
    else:
        params["date"] = session
    return {"receipt": {
        "method": method, "params": params, "response_bytes": 100,
        "response_sha256": "a" * 64, "outcome": "success",
        "captured_at": "2026-10-13T10:00:00+00:00",
    }, "rows": rows}


def assert_stage_sync_failure_restarts(case, *, durable=False):
    """A failed stage sync stops publication; restart reuses the retained response."""
    before = case.store.path.read_bytes()
    staged = []

    def sync(directory, root):
        if directory.name == "stage":
            staged.extend(directory.glob("*.json.gz"))
            raise OSError("stage directory sync failed")
        sync_admission_directories(directory, root)

    modules = ["cross_asset"]
    if durable:
        modules += ["cross_asset_durable", "cross_asset_recovery"]
    with ExitStack() as stack:
        for module in modules:
            stack.enter_context(patch(
                f"quant_data.options.{module}.sync_admission_directories", side_effect=sync))
        for _ in range(2):
            with case.assertRaisesRegex(OSError, "stage directory sync failed"):
                case.job().run(wait=True)
            case.assertEqual(case.store.path.read_bytes(), before)
    case.assertTrue(staged)
    case.assertTrue(all(path.exists() for path in staged))
    result = case.job().run(wait=True)
    case.assertEqual(result["state"], "completed_with_gaps" if durable else "completed")
    case.assertEqual(result["completed_sessions"], 20 if durable else 24)
    calls = Counter((method, tuple(sorted((k, str(v)) for k, v in params.items())))
                    for method, params in case.calls)
    case.assertTrue(calls)
    case.assertEqual(set(calls.values()), {1})


class CrossAssetJobTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.store = temporary_store()
        self.addCleanup(self.temp.cleanup)
        self.store.initialize()
        self.work = self.store.root / WORK
        self.work.mkdir(parents=True)
        self.calls = []
        self.authentications = 0
        self.fail_authentication = False
        self.fail_request = False
        self.addCleanup(patch.stopall)
        patch("socket.create_connection", side_effect=AssertionError("offline test")).start()
        patch.object(socket.socket, "connect", side_effect=AssertionError("offline test")).start()
        patch.object(CrossAssetJob, "_storage_check").start()
        evidence = []
        for year in (2025, 2026):
            path = self.store.root / f".local/theta-spy-20260924/calendar-{year}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"rows": [
                {"date": f"{year}-01-01", "type": "full_close"},
                {"date": f"{year}-12-25", "type": "full_close"},
            ]}))
        for symbol in CROSS_ASSET_ETFS:
            eod, oi = rows_for(symbol, "2026-10-09")
            for suffix, method, rows in (
                ("recent-greeks", "option_history_greeks_eod", eod),
                ("recent-oi", "option_history_open_interest", oi),
            ):
                path = self.work / f"{symbol}-{suffix}.json.gz"
                with gzip.open(path, "wt") as stream:
                    json.dump(batch(symbol, "2026-10-09", method, rows), stream)
                evidence.append(str(path.relative_to(self.store.root)))
        (self.work / "plan.json").write_text(json.dumps({
            "symbols": list(CROSS_ASSET_ETFS),
            "start_dates": {symbol: "2026-10-09" for symbol in CROSS_ASSET_ETFS},
            "end_date": "2026-10-12", "evidence": evidence,
        }))

    def factory(self, root, budget, *, receipt_callback, symbols):
        self.authentications += 1
        if self.fail_authentication:
            raise RuntimeError("fake_authentication_failure")
        self.assertEqual(tuple(symbols), CROSS_ASSET_ETFS)
        owner = self

        class Fake:
            subscription_code = 2

            def request(self, method, **params):
                # This worker must not hold the lock across provider work.
                # Allow the parallel coordinator's separate publication to drain.
                with StoreWriteLock(owner.store.path, timeout_seconds=1):
                    pass
                budget.reserve_request()
                budget.add_bytes(100)
                owner.calls.append((method, params))
                session = str(params.get("date", params.get("start_date")))
                eod, oi = rows_for(params["symbol"], session)
                rows = eod if method in ("option_history_greeks_eod", "option_history_eod") else oi
                result = batch(params["symbol"], session, method, rows)
                receipt = result["receipt"]
                receipt["params"] = {k: str(v) for k, v in params.items()}
                if owner.fail_request == "interrupt":
                    raise KeyboardInterrupt()
                if owner.fail_request:
                    receipt["outcome"] = "UNAVAILABLE"
                    receipt_callback(receipt, budget)
                    raise ProviderFailure("UNAVAILABLE", receipt)
                receipt_callback(receipt, budget)
                return rows, receipt

            def close(self):
                pass

        return Fake()

    def job(self, clock=ALLOWED):
        return CrossAssetJob(self.store.root, transport_factory=self.factory,
                             clock=lambda: clock, sleep=lambda seconds: self.fail("unexpected sleep"))

    def test_plan_is_exact_and_does_not_start_authorization(self):
        summary = plan_summary(reviewed_manifest(self.store.root))
        self.assertEqual(summary["planned_sessions"], 24)
        self.assertEqual(summary["caps"]["max_requests"], MAX_REQUESTS)
        self.assertEqual(summary["caps"]["max_bytes"], MAX_BYTES)
        self.assertEqual(summary["caps"]["max_seconds"], MAX_SECONDS)
        self.assertEqual(summary["caps"]["max_authentications"], MAX_AUTHENTICATIONS)
        self.assertFalse((self.work / "authorization.json").exists())
        self.assertEqual(self.authentications, 0)

    def test_adopts_october_9_evidence_and_only_requests_missing_october_12(self):
        result = self.job().run()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["completed_sessions"], 24)
        self.assertEqual(result["published"], 24)
        self.assertEqual(result["data_requests"], 24)
        self.assertEqual(self.authentications, 1)
        self.assertEqual({str(params.get("date", params.get("start_date")))
                          for _, params in self.calls}, {"2026-10-12"})
        self.assertTrue(all((self.work / f"{symbol}-recent-greeks.json.gz").exists()
                            for symbol in CROSS_ASSET_ETFS))
        self.assertEqual(len((self.work / "attempts.jsonl").read_text().splitlines()), 24)
        before = self.store.path.read_bytes()
        self.assertEqual(self.job().run()["state"], "completed")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 24)

    def test_window_pause_admits_no_request_and_keeps_original_deadline(self):
        first = self.job(BLOCKED).run()
        self.assertEqual(first["state"], "paused")
        self.assertEqual(first["completed_sessions"], 0)  # newest session is first
        self.assertEqual(first["data_requests"], 0)
        self.assertEqual(self.authentications, 0)
        self.assertEqual((self.work / "attempts.jsonl").read_text(), "")
        resumed = self.job().run()
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(resumed["completed_sessions"], 24)
        self.assertEqual(resumed["data_requests"], 24)
        started = json.loads((self.work / "authorization.json").read_text())
        self.assertEqual(started["started_at"], BLOCKED.isoformat())

    def test_stage_sync_failure_stops_publication_and_restart_reuses_response(self):
        assert_stage_sync_failure_restarts(self)

    def test_failed_attempt_cannot_refetch_on_restart(self):
        self.fail_request = True
        with self.assertRaises(ProviderFailure):
            self.job().run()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len((self.work / "attempts.jsonl").read_text().splitlines()), 1)
        self.fail_request = False
        with self.assertRaisesRegex(RuntimeError, "unreconciled_attempts"):
            self.job().run()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.authentications, 1)

    def test_scheduled_pause_renews_successful_login_without_repeating_requests(self):
        now = [datetime(2026, 10, 14, 11, 6, tzinfo=timezone.utc)]
        closed = []

        def factory(*args, **kwargs):
            transport = self.factory(*args, **kwargs)
            ordinal = self.authentications
            request = transport.request
            transport.close = lambda: closed.append(ordinal)

            def call(method, **params):
                result = request(method, **params)
                if len(self.calls) == 1:
                    now[0] = datetime(2026, 10, 14, 11, 20, tzinfo=timezone.utc)
                return result

            transport.request = call
            return transport

        def sleep(seconds):
            self.assertLessEqual(seconds, 30)
            self.assertEqual(closed, [1])
            now[0] = datetime(2026, 10, 14, 11, 41, tzinfo=timezone.utc)

        job = CrossAssetJob(self.store.root, transport_factory=factory,
                            clock=lambda: now[0], sleep=sleep)
        result = job.run(wait=True)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.authentications, 2)
        self.assertEqual(closed, [1, 2])
        self.assertEqual(len(self.calls), 24)
        identities = {(method, json.dumps({k: str(v) for k, v in params.items()}, sort_keys=True))
                      for method, params in self.calls}
        self.assertEqual(len(identities), 24)

    def test_failed_authentication_cannot_retry_on_restart(self):
        self.fail_authentication = True
        with self.assertRaisesRegex(RuntimeError, "fake_authentication_failure"):
            self.job().run()
        attempts = (self.work / "authentication-attempts.jsonl").read_text().splitlines()
        outcomes = (self.work / "authentication-outcomes.jsonl").read_text().splitlines()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(json.loads(attempts[0])["attempt"], 1)
        self.assertEqual(json.loads(outcomes[0])["outcome"], "failed")
        self.fail_authentication = False
        with self.assertRaisesRegex(RuntimeError, "authentication_failed_or_unknown"):
            self.job().run()
        self.assertEqual(self.authentications, 1)

    def test_unknown_authentication_cannot_retry_on_restart(self):
        job = self.job()
        append = job._append

        def interrupt_after_attempt(path, value):
            append(path, value)
            if path.name == "authentication-attempts.jsonl":
                raise KeyboardInterrupt()

        with patch.object(job, "_append", side_effect=interrupt_after_attempt):
            with self.assertRaises(KeyboardInterrupt):
                job.run()
        self.assertEqual(self.authentications, 0)
        self.assertEqual(len((self.work / "authentication-attempts.jsonl").read_text().splitlines()), 1)
        self.assertEqual((self.work / "authentication-outcomes.jsonl").read_text(), "")
        with self.assertRaisesRegex(RuntimeError, "authentication_failed_or_unknown"):
            self.job().run()
        self.assertEqual(self.authentications, 0)

    def test_first_session_greeks_survive_empty_open_interest(self):
        path = self.work / "IBIT-recent-oi.json.gz"
        with gzip.open(path, "wt") as stream:
            value = batch("IBIT", "2026-10-09", "option_history_open_interest", [])
            value["receipt"]["outcome"] = "no_data"
            json.dump(value, stream)
        result = self.job().run()
        self.assertEqual(result["state"], "completed")
        with StoreWriteLock(self.store.path, timeout_seconds=10):
            with self.store.read() as connection:
                capture = connection.execute(
                    "SELECT c.metadata,p.capture_id FROM option_current p "
                    "JOIN option_captures c USING(capture_id) "
                    "WHERE p.symbol='IBIT' AND p.session_date='2026-10-09'"
                ).fetchone()
                self.assertIsNotNone(capture)
                from quant_data.options.store import unpacked
                metadata = unpacked(capture[0])
                self.assertGreater(metadata["coverage"]["greeks_contracts"], 0)
                self.assertEqual(metadata["coverage"]["oi_matched"], 0)
                details = connection.execute(
                    "SELECT open_interest,implied_vol FROM option_details WHERE capture_id=?",
                    (capture[1],)).fetchall()
                self.assertTrue(details)
                self.assertTrue(all(row[0] is None for row in details))
                self.assertTrue(any(row[1] is not None for row in details))

    def test_reviewed_floor_response_is_adopted_without_refetch(self):
        symbol = "GLD"
        eod, _ = rows_for(symbol, "2026-10-12")
        path = self.work / "GLD-2026-10-12-option_history_greeks_eod.json.gz"
        with gzip.open(path, "wt") as stream:
            json.dump(batch(symbol, "2026-10-12", "option_history_greeks_eod", eod), stream)
        plan_path = self.work / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["evidence"].append(path.name)
        plan_path.write_text(json.dumps(plan))
        result = self.job().run()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["published"], 24)
        self.assertEqual(result["data_requests"], 23)
        self.assertFalse(any(method == "option_history_greeks_eod" and
            params["symbol"] == symbol and str(params["start_date"]) == "2026-10-12"
            for method, params in self.calls))
        self.assertTrue(path.exists())

    def test_persisted_budget_cannot_reset_on_restart(self):
        with patch("quant_data.options.cross_asset.MAX_REQUESTS", 1):
            with self.assertRaisesRegex(RuntimeError, "request_cap"):
                self.job().run()
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(len((self.work / "attempts.jsonl").read_text().splitlines()), 1)
            started = (self.work / "authorization.json").read_bytes()
            with self.assertRaisesRegex(RuntimeError, "request_cap"):
                self.job().run()
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.authentications, 1)
            self.assertEqual((self.work / "authorization.json").read_bytes(), started)

    def test_interrupted_unknown_request_stays_unrepeatable(self):
        self.fail_request = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.job().run()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len((self.work / "attempts.jsonl").read_text().splitlines()), 1)
        self.assertEqual((self.work / "request-receipts.jsonl").read_text(), "")
        self.fail_request = False
        with self.assertRaisesRegex(RuntimeError, "unreconciled_attempts"):
            self.job().run()
        self.assertEqual(len(self.calls), 1)

    def test_coverage_read_uses_the_physical_store_lock(self):
        job = self.job()
        manifest = reviewed_manifest(self.store.root)
        active = False
        original_read = job.store.read

        class TrackedLock:
            def __init__(self, *args, **kwargs):
                self.inner = StoreWriteLock(*args, **kwargs)

            def __enter__(self):
                nonlocal active
                self.inner.__enter__()
                active = True
                return self

            def __exit__(self, *args):
                nonlocal active
                active = False
                return self.inner.__exit__(*args)

        def guarded_read():
            self.assertTrue(active)
            return original_read()

        with patch("quant_data.options.cross_asset.StoreWriteLock", TrackedLock), \
             patch.object(job.store, "read", side_effect=guarded_read):
            self.assertEqual(job._coverage(manifest), set())

    def test_unreviewed_evidence_and_changed_plan_fail_before_authorization(self):
        plan_path = self.work / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["evidence"] = plan["evidence"][1:]
        plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "discovery_evidence_missing"):
            reviewed_manifest(self.store.root)
        self.assertFalse((self.work / "authorization.json").exists())
        self.assertEqual(self.authentications, 0)


if __name__ == "__main__":
    unittest.main()
