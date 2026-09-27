"""Daily/weekly scheduling and publication invariants on explicit temporary stores."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import fcntl
import hashlib
import json
import os
import socket
import stat
import unittest
from unittest.mock import patch

from quant_data.options.daily import (
    NY, POLICY, POLICY_SHA256, STATE, ScheduledOptionsRun, due_window,
    read_coverage, run_scheduled, validate_calendars,
)
from quant_data.options.model import build_capture
from quant_data.options.store import OptionsStore
from quant_data.options.transport import ProviderFailure
from quant_data.options.universe import TIER1_ETFS
from quant_data.options.monitor.history import read_batch
from quant_data.stores import StoreWriteLock
from tests.options.test_theta_compact import temporary_store, fixtures, SESSION

DAILY = datetime(2026, 9, 23, 23, 16, tzinfo=timezone.utc)
WEEKLY = datetime(2026, 9, 27, 7, 1, tzinfo=timezone.utc)


def rows_for(symbol, session):
    eod, oi, _ = fixtures()
    # A small genuine-shaped chain keeps scheduler tests focused and inexpensive.
    eod, oi = eod[:8], oi[:8]
    shift = date.fromisoformat(session) - date.fromisoformat(SESSION)
    for row in eod + oi:
        row["symbol"] = symbol
        row["expiration"] = (date.fromisoformat(row["expiration"]) + shift).isoformat()
        row["timestamp"] = row["timestamp"].replace(SESSION, session)
        if "underlying_timestamp" in row:
            row["underlying_timestamp"] = row["underlying_timestamp"].replace(SESSION, session)
    return eod, oi


def day_capture(symbol, session, previous):
    eod, oi = rows_for(symbol, session)
    return build_capture(session, eod, oi, [{
        "method": "option_history_greeks_eod", "params": {"symbol": symbol},
        "response_sha256": "a" * 64, "response_bytes": 100, "outcome": "success",
        "captured_at": (date.fromisoformat(session) + timedelta(days=1)).isoformat() + "T10:00:00+00:00",
    }], previous_session=previous, symbol=symbol)


class ScheduledTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.store = temporary_store()
        self.addCleanup(self.temp.cleanup)
        self.store.initialize()
        self.base = self.store.root / STATE
        self.base.mkdir(parents=True)
        (self.base / "activation.json").write_text(json.dumps({
            "policy_sha256": POLICY_SHA256,
            "daily_first_slot": "2026-09-21T19:15:00-04:00",
            "weekly_first_slot": "2026-09-27T03:00:00-04:00",
        }))
        self.calls = []
        self.holidays = {}
        self.fail = None
        self.empty = None
        self.hook = None
        self.authorizations = 0
        self.addCleanup(patch.stopall)
        patch("socket.create_connection", side_effect=AssertionError("offline test")).start()
        patch.object(socket.socket, "connect", side_effect=AssertionError("offline test")).start()
        patch.object(ScheduledOptionsRun, "storage_check").start()
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)

    def factory(self, root, budget, *, receipt_callback, symbols):
        self.authorizations += 1
        self.assertEqual(tuple(symbols), TIER1_ETFS)
        self.assertTrue(list(self.base.glob("*/*/started.json")))
        owner = self

        class Fake:
            subscription_code = 2

            def request(self, method, **params):
                # Both calendar and option requests must be outside SQLite locks.
                with StoreWriteLock(owner.store.path, timeout_seconds=.05):
                    pass
                budget.reserve_request()
                budget.add_bytes(100)
                owner.calls.append((method, deepcopy(params)))
                if owner.hook:
                    owner.hook(method, params)
                if method == "calendar_year":
                    owner.assertIsInstance(params["year"], str)
                    year = int(params["year"])
                    rows = owner.holidays.get(year, [
                        {"date": f"{year}-01-01", "type": "full_close"},
                        {"date": f"{year}-12-25", "type": "full_close"},
                    ])
                else:
                    session = str(params.get("date", params.get("start_date")))
                    eod, oi = rows_for(params["symbol"], session)
                    rows = eod if method == "option_history_greeks_eod" else oi
                receipt = {
                    "method": method, "params": {k: str(v) for k, v in params.items()},
                    "response_bytes": 100, "response_sha256": "b" * 64,
                    "captured_at": "2027-01-05T10:00:00+00:00", "outcome": "success",
                }
                if owner.fail and owner.fail(method, params):
                    receipt["outcome"] = owner.fail(method, params)
                    receipt_callback(receipt, budget)
                    raise ProviderFailure(receipt["outcome"], receipt)
                if owner.empty and owner.empty(method, params):
                    rows = []
                receipt_callback(receipt, budget)
                return rows, receipt

            def close(self):
                pass

        return Fake()

    def run_job(self, mode="daily", now=DAILY):
        return run_scheduled(self.store.root, mode, now=now, transport_factory=self.factory)

    def seed(self, sessions, symbols=TIER1_ETFS):
        for session, previous in sessions:
            for symbol in symbols:
                self.store.publish(day_capture(symbol, session, previous))

    def test_daily_same_shape_reader_compatibility_and_verified_cleanup(self):
        result = self.run_job()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["published"], 15)
        self.assertEqual(result["data_requests"], 31)
        self.assertEqual(len(self.calls), 31)
        self.assertEqual(read_coverage(self.store, {"2026-09-23"}),
                         {(s, "2026-09-23") for s in TIER1_ETFS})
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM option_daily_summaries").fetchone()[0], 15 * 102)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(c.execute("SELECT DISTINCT predecessor_id FROM option_captures").fetchall()[0][0], None)
        self.assertEqual(len(read_batch(self.store.root, 0, WEEKLY)[0]), 15)
        stage = self.base / "daily/2026-09-23/stage"
        self.assertEqual(len(list(stage.glob("*.json.gz"))), 1)  # retained calendar evidence
        self.assertFalse((stage.parent / "pending-cleanup.json").exists())

    def test_completed_period_cannot_refetch_and_preserves_database_bytes(self):
        self.run_job()
        before = self.store.path.read_bytes()
        prior_calls = len(self.calls)
        result = self.run_job()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.calls), prior_calls)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_already_present_daily_never_authenticates_or_writes(self):
        self.seed([("2026-09-23", "2026-09-22")])
        before = self.store.path.read_bytes()
        result = self.run_job()
        self.assertEqual(result["data_requests"], 0)
        self.assertEqual(self.authorizations, 0)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_weekly_only_fills_missing_in_past_week_preserving_existing_capture(self):
        self.seed([("2026-09-21", "2026-09-18"), ("2026-09-22", "2026-09-21"),
                   ("2026-09-23", "2026-09-22")])
        with self.store.read() as c:
            before = [tuple(r) for r in c.execute("SELECT * FROM option_captures ORDER BY capture_id")]
        result = self.run_job("weekly", WEEKLY)
        self.assertEqual(result["published"], 30)
        self.assertEqual(result["data_requests"], 61)
        self.assertEqual(result["window_start"], "2026-09-21")
        self.assertEqual(result["window_end"], "2026-09-25")
        data_calls = [p for m, p in self.calls if m != "calendar_year"]
        self.assertEqual({str(p.get("date", p.get("start_date"))) for p in data_calls},
                         {"2026-09-24", "2026-09-25"})
        with self.store.read() as c:
            after = [tuple(r) for r in c.execute("SELECT * FROM option_captures WHERE capture_id<=45 ORDER BY capture_id")]
        self.assertEqual(before, after)

    def test_transient_failure_is_gap_no_retry_other_symbols_continue(self):
        self.fail = lambda m, p: "UNAVAILABLE" if p.get("symbol") == "SPY" else None
        result = self.run_job()
        self.assertEqual(result["state"], "complete_with_gaps")
        self.assertEqual(result["published"], 14)
        self.assertEqual(sum(p.get("symbol") == "SPY" for m, p in self.calls), 1)
        self.assertEqual(result["unresolved"], [{"symbol": "SPY", "session": "2026-09-23"}])

    def test_empty_oi_does_not_publish_and_retains_sources(self):
        self.empty = lambda m, p: m == "option_history_open_interest" and p["symbol"] == "SPY"
        result = self.run_job()
        self.assertEqual(result["published"], 14)
        self.assertEqual(result["gaps"][0]["reason"], "open_interest_no_data")
        self.assertNotIn(("SPY", "2026-09-23"), read_coverage(self.store, {"2026-09-23"}))
        self.assertEqual(len(list((self.base / "daily/2026-09-23/stage").glob("*.json.gz"))), 3)

    def test_weekend_is_the_only_automatic_recovery_of_failed_daily(self):
        self.empty = lambda m, p: m == "option_history_greeks_eod" and p["symbol"] == "SPY"
        self.run_job()
        self.seed([("2026-09-21", "2026-09-18"), ("2026-09-22", "2026-09-21"),
                   ("2026-09-24", "2026-09-23"), ("2026-09-25", "2026-09-24")])
        self.empty = None
        self.calls.clear()
        result = self.run_job("weekly", WEEKLY)
        self.assertEqual(result["published"], 1)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual({p["symbol"] for m, p in self.calls if m != "calendar_year"}, {"SPY"})

    def test_auth_or_permission_failure_stops_without_retry(self):
        self.fail = lambda m, p: "PERMISSION_DENIED" if p.get("symbol") else None
        result = self.run_job()
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["published"], 0)
        self.assertEqual(len(self.calls), 2)
        self.run_job()
        self.assertEqual(len(self.calls), 2)

    def test_interrupted_period_cannot_reset_allowance(self):
        work = self.base / "daily/2026-09-23"
        work.mkdir(parents=True)
        (work / "started.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.run_job()
        self.assertEqual(self.authorizations, 0)

    def test_durable_admission_precedes_authentication_failure(self):
        def broken(*a, **k):
            self.assertTrue((self.base / "daily/2026-09-23/started.json").exists())
            raise RuntimeError("authentication_failed")
        result = run_scheduled(self.store.root, "daily", now=DAILY, transport_factory=broken)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["data_requests"], 0)
        self.assertEqual(result["error"], "authentication_failed")

    def test_admission_directory_chain_is_durable_before_authentication(self):
        synced = []
        actual = os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
            actual(fd)
        work = self.base / "daily/2026-09-23"
        expected = []
        directory = work
        while True:
            expected.append(directory)
            if directory == self.store.root:
                break
            directory = directory.parent
        def factory(*a, **k):
            self.assertEqual(synced, expected)
            self.assertTrue((work / "started.json").exists())
            return self.factory(*a, **k)
        with patch("quant_data.options.daily.os.fsync", side_effect=fsync):
            result = run_scheduled(self.store.root, "daily", now=DAILY,
                                   transport_factory=factory)
        self.assertEqual(result["state"], "complete")

    def test_directory_sync_failure_makes_zero_provider_or_authentication_attempts(self):
        actual = os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory sync failure")
            actual(fd)
        with patch("quant_data.options.daily.os.fsync", side_effect=fsync):
            with self.assertRaises(OSError):
                self.run_job()
        self.assertEqual(self.authorizations, 0)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.base / "daily/2026-09-23/started.json").exists())
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.run_job()

    def test_overlap_lock_refuses_second_job_before_authentication(self):
        with (self.base / "scheduled.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "run_active"):
                self.run_job()
        self.assertEqual(self.authorizations, 0)

    def test_wrong_root_response_stops_before_any_publication(self):
        def fake_builder(*args, **kwargs):
            raise ValueError("unexpected_option_root")
        with patch("quant_data.options.daily.build_capture", side_effect=fake_builder):
            result = self.run_job()
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["published"], 0)
        self.assertEqual(len(self.calls), 3)

    def test_race_preserves_existing_capture_even_with_changed_source(self):
        competing = day_capture("SPY", "2026-09-23", "2026-09-22")
        competing["semantic_sha256"] = "c" * 64
        def race(method, params):
            if method == "option_history_open_interest" and params["symbol"] == "SPY":
                self.store.publish(competing)
        self.hook = race
        result = self.run_job()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["preserved"], 1)
        with self.store.read() as c:
            self.assertEqual(c.execute("SELECT semantic_sha256 FROM option_captures WHERE symbol='SPY'").fetchone()[0], "c" * 64)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM option_captures").fetchone()[0], 15)

    def test_calendar_holiday_skips_options_and_early_close_is_session(self):
        self.holidays[2026] = [{"date": "2026-09-23", "type": "full_close"}]
        result = self.run_job()
        self.assertEqual(result["expected_sessions"], 0)
        self.assertEqual(result["data_requests"], 1)
        self.assertEqual(result["state"], "complete")
        # An early-close date is still an eligible complete EOD session.
        self.holidays[2026] = [{"date": "2026-09-24", "type": "early_close"}]
        result = self.run_job(now=DAILY + timedelta(days=1))
        self.assertEqual(result["published"], 15)

    def test_year_boundary_uses_both_calendars_and_previous_session(self):
        result = self.run_job(now=datetime(2027, 1, 4, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(result["state"], "not_due")  # Sunday local evening
        result = self.run_job(now=datetime(2027, 1, 5, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(result["published"], 15)
        self.assertEqual(result["data_requests"], 32)
        self.assertEqual({p["year"] for m, p in self.calls if m == "calendar_year"}, {"2026", "2027"})
        plan = json.loads((self.base / "daily/2027-01-04/plan.json").read_text())
        self.assertEqual(plan["sessions"], [["2027-01-04", "2026-12-31"]])

    def test_calendar_bad_year_fails_closed(self):
        self.holidays[2026] = [{"date": "2025-12-25", "type": "full_close"}]
        result = self.run_job()
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["data_requests"], 1)
        self.assertEqual(result["published"], 0)

    def test_activation_and_policy_drift_block_requests(self):
        path = self.base / "activation.json"
        activation = json.loads(path.read_text())
        activation["daily_first_slot"] = "2026-09-24T19:15:00-04:00"
        path.write_text(json.dumps(activation))
        self.assertEqual(self.run_job()["state"], "not_due")
        activation["policy_sha256"] = "wrong"
        path.write_text(json.dumps(activation))
        with self.assertRaisesRegex(ValueError, "activation_policy"):
            self.run_job()
        self.assertEqual(self.authorizations, 0)

    def test_daily_and_weekly_hard_request_caps(self):
        for mode, window_end, count in (("daily", "2026-09-23", 32), ("weekly", "2026-09-25", 152)):
            self.assertEqual(POLICY[mode]["max_requests"], count)
        result = self.run_job("weekly", WEEKLY)
        self.assertEqual(result["published"], 75)
        self.assertEqual(result["data_requests"], 151)
        requests = [(m, json.dumps({k: str(v) for k, v in p.items()}, sort_keys=True)) for m, p in self.calls]
        self.assertEqual(len(requests), len(set(requests)))

    def test_budget_failure_preserves_partial_accounting_and_no_restart(self):
        from quant_data.options.transport import Budget
        def tiny(**kwargs):
            kwargs["max_requests"] = 2
            return Budget(**kwargs)
        with patch("quant_data.options.daily.Budget", side_effect=tiny):
            result = self.run_job()
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["data_requests"], 2)
        self.assertEqual(result["published"], 0)
        self.assertEqual(self.run_job(), result)


class ClockAndStoreTests(unittest.TestCase):
    def test_daily_cutoff_weekend_and_timezone(self):
        self.assertIsNone(due_window("daily", DAILY.replace(hour=23, minute=14)))
        self.assertIsNone(due_window("daily", WEEKLY))
        self.assertEqual(due_window("daily", DAILY)[1:], (date(2026, 9, 23),) * 2)
        with self.assertRaisesRegex(ValueError, "aware"):
            due_window("daily", datetime(2026, 9, 23))

    def test_weekly_latest_due_period_dst_and_before_slot(self):
        self.assertEqual(due_window("weekly", WEEKLY)[1:], (date(2026, 9, 21), date(2026, 9, 25)))
        self.assertEqual(due_window("weekly", WEEKLY - timedelta(minutes=2))[2], date(2026, 9, 18))
        delayed = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        slot, start, end = due_window("weekly", delayed)
        self.assertEqual(slot.utcoffset(), timedelta(hours=-5))
        self.assertEqual((start, end), (date(2026, 10, 26), date(2026, 10, 30)))

    def test_calendar_duplicates_and_empty_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            validate_calendars({2026: []})
        row = {"date": "2026-01-01", "type": "full_close"}
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_calendars({2026: [row, row]})

    def test_missing_only_preserves_bytes_and_default_correction_still_works(self):
        temp, store = temporary_store()
        with temp:
            store.initialize()
            capture = day_capture("SPY", "2026-09-23", "2026-09-22")
            store.publish(capture)
            before = store.path.read_bytes()
            changed = deepcopy(capture)
            changed["semantic_sha256"] = "f" * 64
            result = store.publish(changed, missing_only=True)
            self.assertEqual(result["outcome"], "existing")
            self.assertEqual(result["canonical_changes"], 0)
            self.assertEqual(before, store.path.read_bytes())
            self.assertEqual(store.publish(changed)["canonical_changes"], 1)

    def test_unit_bounds_runtime_and_policy_agree(self):
        root = Path(__file__).resolve().parents[2]
        for mode, timeout in (("daily", "21min"), ("weekly", "61min")):
            service = (root / f"deploy/systemd/quant-data-theta-options-{mode}.service").read_text()
            timer = (root / f"deploy/systemd/quant-data-theta-options-{mode}.timer").read_text()
            self.assertIn("--mode " + mode, service)
            self.assertIn("TimeoutStartSec=" + timeout, service)
            self.assertIn("Restart=no", service)
            self.assertIn("theta-discovery-20260924/venv/bin/python", service)
            self.assertIn("OnCalendar=" + POLICY[mode]["calendar"], timer)
            self.assertIn("Persistent=" + str(POLICY[mode]["persistent"]).lower(), timer)
