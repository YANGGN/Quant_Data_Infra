"""Theta status integration using temporary receipts and fake collection only."""
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.dashboard.fetch_status_page import render_fetch_status_page, render_fetch_status_content
from quant_data.inspector_fetch_status import read_fetch_status, _slots
from quant_data.inspector_schedules import TIMER_BINDINGS, _cadence
from quant_data.operations import theta_options_refresh as cli
from quant_data.operations.fetch_run_history import run_recorded_cli, read_fetch_run_history
from quant_data.operations.fetch_run_summary import summarize_report

DAILY = "quant-data-theta-options-daily.timer"
WEEKLY = "quant-data-theta-options-weekly.timer"
START = datetime(2026, 9, 27, 7, 0, 59, tzinfo=timezone.utc)
FINISH = datetime(2026, 9, 27, 7, 2, 10, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return START


def report(**changes):
    return dict(contract="theta_daily_and_weekly_v1", mode="weekly",
                state="complete", expected_sessions=75, initially_missing=30,
                published=30, preserved=0, gaps=[], unresolved=[],
                finished_at=FINISH.isoformat(), **changes)


def units():
    return "\n\n".join(
        "\n".join(("Id=" + batch, "LoadState=loaded", "ActiveState=active",
                    "TimersCalendar={ OnCalendar=" + calendar + " ; next_elapse=n/a }"))
        for batch, calendar in (
            (DAILY, "Mon..Fri *-*-* 19:15:00 America/New_York"),
            (WEEKLY, "Sun *-*-* 03:00:00 America/New_York")))


class ThetaFetchStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "history"
        # No test is permitted to contact a provider or open any SQLite store.
        for target in ("socket.socket.connect", "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Unexpected external access"))
            guard.start()
            self.addCleanup(guard.stop)

    def record(self, result):
        with patch.object(cli, "run_scheduled", return_value=result) as collect, \
                patch.object(cli, "datetime", Clock), redirect_stdout(StringIO()) as output:
            code = run_recorded_cli(WEEKLY, lambda: cli.main(["--mode", "weekly"]),
                argv=["--mode", "weekly"], root=self.root,
                clock=iter((START, FINISH)).__next__, environment={})
        collect.assert_called_once_with(cli.ROOT, "weekly")
        self.assertEqual(json.loads(output.getvalue()), result)
        return code

    def snapshot(self, day="2026-09-27", **changes):
        return read_fetch_status(day, observed_at=NOW, unit_output=units(),
                                 journal_output="", probe=False,
                                 history_root=self.root, **changes)

    def test_active_options_bindings_use_only_optional_theta_datasets(self):
        bindings = {b.unit: b for b in TIMER_BINDINGS}
        self.assertNotIn("quant-data-alpaca-spy-options.timer", bindings)
        expected = {"options.theta.receipts", "options.theta.selected_contracts",
                    "options.theta.daily_research"}
        for batch in (DAILY, WEEKLY):
            self.assertEqual(set(bindings[batch].datasets), expected)

    def test_daily_and_sunday_slots_preserve_dst(self):
        rules = ["{ OnCalendar=Sun *-*-* 03:00:00 America/New_York ; next_elapse=n/a }"]
        slots, supported = _slots(rules, date(2026, 11, 1), 8)
        self.assertTrue(supported)
        self.assertEqual([t.isoformat() for t in slots],
                         ["2026-11-01T08:00:00+00:00", "2026-11-08T08:00:00+00:00"])
        self.assertEqual(_cadence({"TimersCalendar": rules}), "Sundays at 03:00 New York")
        rules = ["{ OnCalendar=Mon..Fri *-*-* 19:15:00 America/New_York ; next_elapse=n/a }"]
        slots, supported = _slots(rules, date(2026, 10, 30), 4)
        self.assertTrue(supported)
        self.assertEqual([t.isoformat() for t in slots],
                         ["2026-10-30T23:15:00+00:00", "2026-11-03T00:15:00+00:00"])

    def test_completed_receipt_flows_through_calendar_and_existing_markup(self):
        self.assertEqual(self.record(report()), 0)
        snapshot = self.snapshot()
        event, = snapshot["focus_events"]
        self.assertEqual(event["batch_id"], WEEKLY)
        self.assertEqual((event["status"], event["evidence"]), ("succeeded", "saved_run"))
        self.assertEqual(event["counts"], dict(unit="symbol_sessions", successful=30,
            failed=0, partial=0, skipped=45, unattempted=0))
        self.assertIn("data/options.sqlite", event["description"])
        html = render_fetch_status_page(snapshot, registry_revision="test")
        for text in ("Theta options daily", "Theta options weekly repair",
                     "ETF sessions", "Published", "Already present", "data/options.sqlite"):
            self.assertIn(text, html)
        self.assertNotIn("Alpaca", html)
        linked = render_fetch_status_content(snapshot, linked_data=True)
        self.assertIn('href="/agent-tools"', linked)
        self.assertNotIn('data-status-batch-link="' + WEEKLY, linked)
        next_day = self.snapshot("2026-09-28")["focus_events"]
        self.assertEqual(next_day[0]["scheduled_local"], "19:15 EDT")
        self.assertEqual(next_day[0]["status"], "upcoming")

    def test_gaps_are_partial_only_when_publication_is_recorded(self):
        for published, expected_status in ((29, "partial"), (0, "failed")):
            with self.subTest(published=published):
                self.root = Path(self.temp.name) / str(published)
                result = report()
                result.update(state="complete_with_gaps", published=published,
                              unresolved=[{}] * (30 - published))
                self.assertEqual(self.record(result), 2)
                event, = self.snapshot()["focus_events"]
                self.assertEqual(event["status"], expected_status)
                self.assertEqual(event["exit_code"], 2)
                self.assertEqual(event["counts"]["failed"], 30 - published)

    def test_stopped_work_is_not_mislabeled_as_never_attempted(self):
        result = report()
        result.update(state="stopped", published=8, preserved=2, gaps=[{}] * 3)
        counts = summarize_report(WEEKLY, result)
        self.assertEqual(counts, dict(unit="symbol_sessions", successful=8,
            failed=3, partial=0, skipped=47, unattempted=17))
        self.record(result)
        html = render_fetch_status_page(self.snapshot(), registry_revision="test")
        self.assertIn("Unfinished", html)
        self.assertIn("may have been attempted", html)

    def test_replayed_period_does_not_recount_prior_publications(self):
        result = report()
        result["finished_at"] = "2026-09-27T06:00:00+00:00"
        self.assertEqual(self.record(result), 0)
        event, = self.snapshot()["focus_events"]
        self.assertIsNone(event["counts"])

    def test_unplanned_noop_counts_are_unavailable(self):
        result = report()
        result.update(expected_sessions=None, published=0,
                      reason="all_weekdays_already_present")
        self.assertEqual(self.record(result), 0)
        self.assertIsNone(self.snapshot()["focus_events"][0]["counts"])

    def test_invalid_reports_never_create_invented_counts(self):
        for changes in ({"mode": "daily"}, {"published": True},
                        {"expected_sessions": 76}, {"initially_missing": 76},
                        {"published": 31}, {"preserved": -1},
                        {"unresolved": [{}]}, {"gaps": "secret", "state": "stopped"}):
            with self.subTest(changes=changes):
                value = report()
                value.update(changes)
                with self.assertRaises(ValueError):
                    summarize_report(WEEKLY, value)

    def test_receipt_errors_do_not_change_collector_result(self):
        with patch("quant_data.operations.fetch_run_history._publish", side_effect=OSError), \
                patch("quant_data.operations.fetch_run_history._warn"):
            self.assertEqual(self.record(report()), 0)

    def test_missing_or_future_history_is_not_success(self):
        self.assertEqual(self.snapshot()["focus_events"][0]["status"], "unconfirmed")
        self.record(report())
        records = read_fetch_run_history(self.root, start=START, end=NOW,
                                         observed_at=START)["records"]
        self.assertEqual(records, [])

    def test_existing_systemd_completion_remains_visible_without_new_receipts(self):
        service = "\n".join(("Id=" + WEEKLY.replace(".timer", ".service"),
            "LoadState=loaded", "Type=oneshot", "ActiveState=inactive", "SubState=dead",
            "Result=success", "ExecMainCode=1", "ExecMainStatus=0",
            "ExecMainStartTimestamp=Sun 2026-09-27 07:00:59 UTC",
            "ExecMainExitTimestamp=Sun 2026-09-27 07:02:10 UTC"))
        snapshot = read_fetch_status("2026-09-27", observed_at=NOW, probe=False,
                                    unit_output=units() + "\n\n" + service, journal_output="")
        event, = snapshot["focus_events"]
        self.assertEqual((event["status"], event["evidence"]), ("succeeded", "systemd"))
        self.assertIsNone(event["counts"])
