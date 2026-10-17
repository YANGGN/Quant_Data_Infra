"""Health semantics and immutable reads use temporary monitor stores only."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from quant_data.inspector_intraday_status import read_intraday_options, attach_intraday_options
from quant_data.options.monitor.store import MonitorStore
from quant_data.stores import StoreWriteLock
from quant_data.errors import ConflictError

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def units(*, enabled=True, failed=False, completed="Thu 2026-10-08 14:59:00 UTC"):
    return (
        "Id=quant-data-options-monitor.timer\nLoadState=loaded\nActiveState=" + ("active" if enabled else "inactive")
        + "\nNextElapseUSecRealtime=Thu 2026-10-08 15:05:00 UTC\n\n"
        + "Id=quant-data-options-monitor.service\nLoadState=loaded\nActiveState=inactive\n"
        + "ExecMainStartTimestamp=Thu 2026-10-08 14:58:00 UTC\nExecMainExitTimestamp=" + completed
        + "\nResult=" + ("exit-code" if failed else "success") + "\nExecMainCode=1\nExecMainStatus=" + ("1" if failed else "0")
    )


class IntradayOptionsStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "config").mkdir()
        (self.root / "config/options_monitor_universe.json").write_text(json.dumps({
            "schema_version": 1, "members": [
                {"symbol": "SPY", "name": "S&P 500", "group": "market", "cadence_minutes": 5},
                {"symbol": "AAPL", "name": "Apple", "group": "equity", "cadence_minutes": 30},
            ]}))
        self.store = MonitorStore(self.root)
        self.store.initialize()
        self.store.save_calendar("2026-10-08", [{"date": "2026-10-08", "type": "open", "open": "09:30:00", "close": "16:00:00"}])
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        guard.start()
        self.addCleanup(guard.stop)

    def observation(self, symbol="SPY", at="2026-10-08T14:59:00+00:00", minutes=5, session="2026-10-08"):
        self.store.publish({"session": session, "symbol": symbol, "captured_at": at,
                            "cadence_minutes": minutes, "_slot": at, "_fingerprint": "test"})

    def snapshot(self, **kwargs):
        return read_intraday_options(self.root, kwargs.pop("selected", "2026-10-08"),
            observed_at=kwargs.pop("now", NOW), unit_output=kwargs.pop("unit_output", units()), **kwargs)

    def both(self):
        self.observation()
        self.observation("AAPL", minutes=30)

    def test_fresh_collection_separate_from_pending_delivery(self):
        self.both()
        result = self.snapshot()
        self.assertEqual(result["collection"]["state"], "current")
        self.assertEqual(result["collection"]["fresh_symbols"], 2)
        self.assertEqual(result["delivery"]["state"], "pending")
        self.assertEqual(result["delivery"]["pending_count"], 2)
        self.assertEqual(result["scheduler"]["state"], "enabled")
        self.assertEqual(result["scheduler"]["next_run_at"], "2026-10-08T15:05:00Z")

    def test_old_and_missing_symbols_prevent_false_green(self):
        self.observation(at="2026-10-08T14:00:00+00:00")
        result = self.snapshot()
        self.assertEqual(result["collection"]["state"], "stale")
        self.assertEqual(result["collection"]["pending_symbols"], 2)
        self.assertEqual(result["collection"]["fresh_symbols"], 0)

    def test_closed_market_not_stale_but_queue_backlog_still_visible(self):
        self.observation(at="2026-10-08T14:00:00+00:00")
        result = self.snapshot(now=datetime(2026, 10, 8, 22, tzinfo=timezone.utc))
        self.assertEqual(result["collection"]["state"], "closed")
        self.assertEqual(result["delivery"]["state"], "pending")

    def test_market_holiday_and_early_close_use_saved_calendar(self):
        self.store.write("UPDATE calendar SET payload=? WHERE session=?", (json.dumps([{"date": "2026-10-08", "type": "full_close"}]), "2026-10-08"))
        self.assertEqual(self.snapshot()["session"]["state"], "closed")
        self.store.write("UPDATE calendar SET payload=? WHERE session=?", (json.dumps([{"date": "2026-10-08", "type": "early_close", "open": "09:30:00", "close": "13:00:00"}]), "2026-10-08"))
        self.assertEqual(self.snapshot(now=datetime(2026, 10, 8, 17, 16, tzinfo=timezone.utc))["collection"]["state"], "closed")

    def test_missing_market_calendar_never_claims_open(self):
        self.store.write("DELETE FROM calendar")
        self.both()
        self.assertEqual(self.snapshot()["session"]["state"], "unknown")

    def test_paused_is_explicit(self):
        (self.store.path.parent / "live-pause-2026-10-08.json").write_text(json.dumps({
            "version": 1, "session": "2026-10-08", "until": "2026-10-09T00:00:00-04:00",
            "backfill_run_id": "a" * 24, "reason": "backfill_priority"}))
        self.assertEqual(self.snapshot()["collection"]["state"], "paused")

    def test_current_startup_does_not_present_yesterdays_coverage_as_today(self):
        self.observation(at="2026-10-07T19:55:00+00:00", session="2026-10-07")
        result = self.snapshot(now=datetime(2026, 10, 8, 13, 31, tzinfo=timezone.utc))
        self.assertEqual(result["collection"]["state"], "waiting")
        self.assertEqual(result["collection"]["symbols_observed"], 0)
        self.assertEqual(result["collection"]["observation_session"], "2026-10-07")
        self.assertEqual(sum(row["symbols_observed"] for row in result["cadence"]), 0)

    def test_historical_selection_independent_of_current_health(self):
        self.both()
        self.observation(at="2026-10-07T19:55:00+00:00", session="2026-10-07")
        result = self.snapshot(selected="2026-10-07")
        self.assertEqual(result["collection"]["state"], "current")
        self.assertEqual(result["operating_date"], "2026-10-08")
        self.assertEqual(result["selected_session"]["symbols"], 1)
        self.assertIsNone(result["selected_session"]["failed_requests"])

    def test_future_evidence_does_not_enter_freshness_counts(self):
        self.observation(at="2026-10-08T15:10:00+00:00")
        result = self.snapshot()
        self.assertEqual(result["collection"]["symbols_observed"], 0)
        self.assertEqual(result["selected_session"]["observations"], 0)

    def test_delivery_queue_clear_is_not_remote_verification(self):
        self.both()
        self.store.write("DELETE FROM outbox")
        result = self.snapshot()
        self.assertEqual(result["delivery"]["pending_count"], 0)
        self.assertEqual(result["delivery"]["state"], "current")
        self.assertIn("not a remote dashboard check", result["delivery"]["message"])
        self.assertNotIn("last_uploaded_at", result["delivery"])

    def test_failed_cycle_and_disabled_timer_need_attention(self):
        self.observation(at="2026-10-08T14:50:00+00:00")
        self.assertEqual(self.snapshot(unit_output=units(failed=True))["collection"]["state"], "failed")
        self.assertEqual(self.snapshot(unit_output=units(enabled=False))["collection"]["state"], "failed")

    def test_unfinished_requests_are_only_overdue_after_cycle_allowance(self):
        self.both()
        self.store.reserve("2026-10-08", "SPY", "a", "option_snapshot_ohlc", "2026-10-08T14:59:00+00:00")
        self.assertEqual(self.snapshot()["collection"]["unresolved_requests"], 0)
        self.store.reserve("2026-10-08", "SPY", "b", "option_snapshot_ohlc", "2026-10-08T14:50:00+00:00")
        self.assertEqual(self.snapshot()["collection"]["unresolved_requests"], 1)
        self.assertEqual(self.snapshot()["collection"]["state"], "stale")

    def test_null_root_and_missing_store_do_not_initialize(self):
        with patch.object(MonitorStore, "initialize", side_effect=AssertionError("No initialization")):
            result = read_intraday_options(None, "2026-10-08", observed_at=NOW)
            self.assertIsNone(result["delivery"]["pending_count"])
            self.store.path.unlink()
            result = self.snapshot()
            self.assertFalse(result["available"])
            self.assertFalse(self.store.path.exists())

    def test_physical_lock_busy_is_unknown_and_never_force_unlocked(self):
        with StoreWriteLock(self.store.path):
            result = self.snapshot()
            self.assertFalse(result["available"])
            self.assertEqual(result["collection"]["state"], "unknown")
            with self.assertRaises(ConflictError):
                with StoreWriteLock(self.store.path, timeout_seconds=.01):
                    pass

    def test_reader_changes_no_database_bytes_or_store_identity(self):
        self.both()
        before = self.store._stamp()
        digest = sha256(self.store.path.read_bytes()).hexdigest()
        self.snapshot()
        self.assertEqual(before, self.store._stamp())
        self.assertEqual(digest, sha256(self.store.path.read_bytes()).hexdigest())

    def test_unsafe_store_and_roster_paths_are_unavailable(self):
        roster = self.root / "config/options_monitor_universe.json"
        original = roster.with_suffix(".saved")
        roster.rename(original)
        roster.symlink_to(original)
        self.assertFalse(self.snapshot()["available"])

    def test_query_budget_exhaustion_returns_unknown(self):
        self.both()
        with patch("quant_data.inspector_intraday_status.MAX_OPERATIONS", -1), patch("quant_data.inspector_intraday_status.READ_SECONDS", -1):
            # Force callback on every SQLite operation for a deterministic bound.
            import sqlite3
            original = sqlite3.Connection.set_progress_handler
            class Bounded(sqlite3.Connection):
                def set_progress_handler(self, callback, n):
                    return original(self, callback, 1)
            connect = sqlite3.connect
            with patch("sqlite3.connect", side_effect=lambda *a, **k: connect(*a, **k, factory=Bounded)):
                result = self.snapshot()
        self.assertFalse(result["available"])

    def test_slow_historical_detail_does_not_erase_current_health(self):
        import sqlite3
        from quant_data.inspector_intraday_status import _session_rows
        self.both()
        def rows(connection, session, cutoff):
            if session == "2026-10-07":
                exc = sqlite3.OperationalError("interrupted")
                exc.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
                raise exc
            return _session_rows(connection, session, cutoff)
        with patch("quant_data.inspector_intraday_status._session_rows", side_effect=rows):
            result = self.snapshot(selected="2026-10-07")
        self.assertTrue(result["available"])
        self.assertEqual(result["collection"]["state"], "current")
        self.assertEqual(result["delivery"]["pending_count"], 2)
        self.assertIsNone(result["selected_session"]["observations"])


    def calendar(self, days, status=None):
        from quant_data.inspector_intraday_status import read_intraday_calendar
        return read_intraday_calendar(self.root, days, observed_at=NOW, status=status or {"available": False})

    def attempt(self, session, slot, outcome, at):
        self.store.reserve(session, "SPY", slot, "option_snapshot_ohlc", at)
        self.store.finish(session, "SPY", slot, "option_snapshot_ohlc", outcome, {})

    def test_calendar_outcomes_belong_to_each_day(self):
        self.attempt("2026-10-06", "a", "success", "2026-10-06T15:00:00+00:00")
        self.attempt("2026-10-07", "a", "success", "2026-10-07T15:00:00+00:00")
        self.attempt("2026-10-07", "b", "provider_error", "2026-10-07T15:05:00+00:00")
        days = self.calendar(["2026-10-06", "2026-10-07"])
        self.assertEqual([d["state"] for d in days], ["recorded", "partial"])
        self.assertEqual(days[1]["successful_requests"], 1)
        self.assertEqual(days[1]["failed_requests"], 1)
        self.assertIn("does not prove complete", days[0]["message"])

    def test_calendar_missing_future_weekend_and_holiday(self):
        days = self.calendar(["2026-10-01", "2026-10-09", "2026-10-10"])
        self.assertEqual([d["state"] for d in days], ["unknown", "scheduled", "closed"])
        self.assertIsNone(days[0]["successful_requests"])
        self.store.save_calendar("2026-10-09", [{"date": "2026-10-09", "type": "full_close"}])
        self.assertEqual(self.calendar(["2026-10-09"])[0]["state"], "closed")

    def test_calendar_future_evidence_excluded_and_offsets_preserved(self):
        self.attempt("2026-10-07", "a", "success", "2026-10-07T11:00:00-04:00")
        self.attempt("2026-10-07", "b", "provider_error", "2026-10-08T15:01:00+00:00")
        day = self.calendar(["2026-10-07"])[0]
        self.assertEqual(day["successful_requests"], 1)
        self.assertEqual(day["failed_requests"], 0)
        self.assertEqual(day["last_attempt_at"], "2026-10-07T15:00:00Z")

    def test_calendar_market_close_does_not_erase_failures(self):
        self.attempt("2026-10-08", "a", "success", "2026-10-08T14:00:00+00:00")
        self.attempt("2026-10-08", "b", "provider_error", "2026-10-08T14:01:00+00:00")
        day = self.calendar(["2026-10-08"], {"available": True, "collection": {"state": "closed"}})[0]
        self.assertEqual(day["state"], "partial")

    def test_calendar_past_unfinished_requests_distinct_from_current_pause(self):
        self.store.reserve("2026-10-07", "SPY", "a", "option_snapshot_ohlc", "2026-10-07T14:00:00+00:00")
        days = self.calendar(["2026-10-07", "2026-10-08"], {"available": True, "collection": {"state": "paused", "message": "Paused"}})
        self.assertEqual(days[0]["unresolved_requests"], 1)
        self.assertEqual(days[0]["state"], "stale")
        self.assertEqual(days[1]["state"], "paused")

    def test_calendar_busy_is_unknown_without_writes(self):
        before = self.store._stamp()
        with StoreWriteLock(self.store.path), patch.object(MonitorStore, "initialize", side_effect=AssertionError("No initialization")):
            self.assertEqual(self.calendar(["2026-10-07"])[0]["state"], "unknown")
        self.assertEqual(before, self.store._stamp())

    def test_calendar_month_scope_bounded(self):
        with self.assertRaises(ValueError):
            self.calendar(["2026-09-30", "2026-10-01"])

    def test_host_adapter_only_adds_intraday_field(self):
        base = {"selected_date": "2026-10-08", "observed_at": "2026-10-08T15:00:00Z", "summary": {"total": 12}}
        with patch("quant_data.inspector_intraday_status.read_intraday_options", return_value={"available": False}) as read:
            result = attach_intraday_options(base, self.root)
        self.assertEqual(result["summary"], base["summary"])
        self.assertNotIn("intraday_options", base)
        self.assertTrue(read.call_args.kwargs["probe"])


if __name__ == "__main__":
    unittest.main()
