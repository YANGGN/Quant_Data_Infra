from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quant_data.errors import ValidationError
from quant_data.inspector_fetch_status import read_fetch_status, _slots, _SERVICES, _UNITS

NOW = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)
TIMER = "quant-data-fmp-macro-calendar.timer"
SERVICE = TIMER.replace(".timer", ".service")


def timer(calendar=None, active="active"):
    rules = calendar or ["Mon..Fri *-*-* 08:15:00 America/New_York", "Mon..Fri *-*-* 08:45:00 America/New_York"]
    return "\n".join(["Id=" + TIMER, "LoadState=loaded", "ActiveState=" + active,
                      *["TimersCalendar={ OnCalendar=" + rule + " ; next_elapse=n/a }" for rule in rules]])


def service(**overrides):
    fields = {"Id": SERVICE, "LoadState": "loaded", "Type": "oneshot",
              "ActiveState": "inactive", "SubState": "dead", "Result": "success",
              "ExecMainCode": "1", "ExecMainStatus": "0", "InvocationID": "latest",
              "ExecMainStartTimestamp": "Fri 2026-09-04 12:45:12 UTC",
              "ExecMainExitTimestamp": "Fri 2026-09-04 12:45:24 UTC"}
    fields.update(overrides)
    return "\n".join(key + "=" + value for key, value in fields.items())


def entry(at, job="1", result=None, **overrides):
    instant = datetime.fromisoformat(at.replace("Z", "+00:00"))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    fields = {"__REALTIME_TIMESTAMP": str((instant - epoch) // timedelta(microseconds=1)),
              "_BOOT_ID": "boot", "_PID": "100", "_UID": str(os.getuid()),
              "_COMM": "systemd", "_EXE": "/usr/lib/systemd/systemd",
              "USER_UNIT": SERVICE, "JOB_TYPE": "start", "JOB_ID": job,
              "USER_INVOCATION_ID": "job-" + job}
    if result is not None:
        fields["JOB_RESULT"] = result
    fields.update(overrides)
    return json.dumps(fields)


def completed(at="2026-09-04T12:15:12Z", end="2026-09-04T12:15:24Z", job="1", result="done", **overrides):
    return "\n".join([entry(at, job, **overrides), entry(end, job, result, **overrides)])


class FetchStatusTests(unittest.TestCase):
    def snapshot(self, units=None, journal="", day="2026-09-04", now=NOW):
        with patch("quant_data.inspector_fetch_status.subprocess.run") as command:
            result = read_fetch_status(day, observed_at=now, probe=False,
                                       unit_output=units or timer() + "\n\n" + service(), journal_output=journal)
            command.assert_not_called()
            return result

    def test_recorded_history_and_latest_failure_belong_to_distinct_slots(self):
        units = timer() + "\n\n" + service(Result="exit-code", ExecMainStatus="1", ActiveState="failed")
        snapshot = self.snapshot(units, completed())
        first, second = snapshot["focus_events"]
        self.assertEqual((first["status"], second["status"]), ("succeeded", "failed"))
        self.assertEqual((first["color"], second["color"]), ("green", "red"))
        self.assertEqual(snapshot["summary"], {"total": 2, "partial": 0, "pending": 0, "green": 1, "amber": 0, "red": 1})
        self.assertEqual(first["scheduled_local"], "08:15 EDT")
        self.assertEqual(first["scheduled_at"], "2026-09-04T12:15:00Z")
        self.assertEqual(snapshot["month_start"], "2026-09-01")
        self.assertEqual(snapshot["month_end"], "2026-09-30")
        self.assertEqual(len(snapshot["days"]), 30)
        self.assertTrue(all(event["status"] == "unconfirmed" for event in snapshot["days"][0]["events"]))

    def test_missing_or_untrusted_history_never_invents_success(self):
        units = timer() + "\n\n" + service(ExecMainStartTimestamp="", ExecMainExitTimestamp="", ExecMainCode="0")
        cases = [
            "", entry("2026-09-04T12:15:24Z", result="done"),
            completed(JOB_TYPE="stop"), completed(USER_UNIT="unrelated.service"),
            completed(_EXE="/tmp/spoofed-systemd"), completed(_EXE=["/usr/lib/systemd/systemd"]),
            completed(_UID="not-the-current-user"),
            entry("2026-09-04T12:15:12Z") + "\n" + entry("2026-09-04T12:15:24Z", job="2", result="done"),
            entry("2026-09-04T12:15:12Z") + "\n" + entry("2026-09-04T12:15:24Z", result="done", _BOOT_ID="different"),
            completed(end="2026-09-04T16:00:00Z"),
            completed(at="2026-09-04T12:25:12Z", end="2026-09-04T12:25:24Z"),
        ]
        for journal in cases:
            with self.subTest(journal=journal[:70]):
                self.assertTrue(all(event["status"] == "unconfirmed"
                                    for event in self.snapshot(units, journal)["focus_events"]))
        snapshot = self.snapshot(units, completed(MESSAGE="private provider secret"))
        self.assertNotIn("private provider secret", json.dumps(snapshot))

    def test_ambiguous_repeats_and_conflicting_results_stay_unconfirmed(self):
        units = timer() + "\n\n" + service(ExecMainStartTimestamp="", ExecMainExitTimestamp="")
        history = completed() + "\n" + completed(at="2026-09-04T12:16:12Z", end="2026-09-04T12:16:24Z", job="2")
        self.assertEqual(self.snapshot(units, history)["focus_events"][0]["status"], "unconfirmed")
        history = completed(at="2026-09-04T12:45:12Z", end="2026-09-04T12:45:24Z", USER_INVOCATION_ID="latest")
        units = timer() + "\n\n" + service(Result="exit-code", ExecMainStatus="1")
        self.assertEqual(self.snapshot(units, history)["focus_events"][1]["status"], "unconfirmed")

    def test_journal_and_latest_snapshot_do_not_double_count_one_run(self):
        history = completed(at="2026-09-04T12:45:12Z", end="2026-09-04T12:45:24Z", USER_INVOCATION_ID="latest")
        result = self.snapshot(journal=history)
        self.assertEqual(result["focus_events"][1]["status"], "succeeded")
        self.assertEqual(result["summary"]["green"], 1)

    def test_running_future_and_incomplete_results_are_distinct(self):
        units = timer() + "\n\n" + service(ActiveState="activating", ExecMainExitTimestamp="")
        self.assertEqual(self.snapshot(units)["focus_events"][1]["status"], "running")
        # A finished ExecStart command does not finish the whole oneshot activation.
        starting = timer() + "\n\n" + service(ActiveState="activating", SubState="start-post")
        self.assertEqual(self.snapshot(starting)["focus_events"][1]["status"], "running")
        earlier = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
        self.assertTrue(all(event["status"] == "upcoming"
                            for event in self.snapshot(units, now=earlier)["focus_events"]))
        for changes in ({"ExecMainCode": "0"}, {"ExecMainStatus": "1"}, {"Type": "simple"},
                        {"ActiveState": "unknown"},
                        {"ExecMainExitTimestamp": "Fri 2026-09-04 16:00:00 UTC"}):
            with self.subTest(changes=changes):
                result = self.snapshot(timer() + "\n\n" + service(**changes))
                self.assertEqual(result["summary"]["green"], 0)

    def test_hourly_dst_first_friday_and_loaded_changes(self):
        hourly = ["{ OnCalendar=*-*-* *:10:00 UTC ; next_elapse=n/a }"]
        for start, day, expected in ((date(2026, 3, 2), "2026-03-08", 23),
                                     (date(2026, 10, 26), "2026-11-01", 25)):
            from quant_data.inspector_fetch_status import EASTERN
            slots, supported = _slots(hourly, start)
            self.assertTrue(supported)
            local = [slot.astimezone(EASTERN) for slot in slots if slot.astimezone(EASTERN).date().isoformat() == day]
            self.assertEqual(len(local), expected)
            self.assertEqual(len(slots), len(set(slots)))
            if expected == 25:
                self.assertEqual([v.strftime("%H:%M %Z") for v in local if v.hour == 1], ["01:10 EDT", "01:10 EST"])
        monthly = ["{ OnCalendar=Fri *-*-01..07 10:05:00 America/New_York ; next_elapse=n/a }"]
        self.assertEqual(len(_slots(monthly, date(2026, 8, 31))[0]), 1)
        self.assertEqual(len(_slots(monthly, date(2026, 9, 7))[0]), 0)
        changed = self.snapshot(timer(["Mon..Fri *-*-* 06:12:00 America/New_York"]) + "\n\n" + service())
        self.assertEqual([e["scheduled_local"] for e in changed["focus_events"]], ["06:12 EDT"])

    def test_inactive_unsupported_and_missing_metadata_are_explicit(self):
        inactive = self.snapshot(timer(active="inactive") + "\n\n" + service())
        self.assertEqual(inactive["summary"]["total"], 0)
        self.assertIn("inactive", inactive["unavailable_jobs"][5]["reason"])
        unsupported = self.snapshot(timer(["unsupported rule"]) + "\n\n" + service())
        self.assertEqual(unsupported["summary"]["total"], 0)
        self.assertTrue(any("cannot be displayed" in job["reason"] for job in unsupported["unavailable_jobs"]))
        result = read_fetch_status("2026-09-04", observed_at=NOW, probe=False)
        self.assertEqual(len(result["unavailable_jobs"]), 10)
        self.assertEqual(result["summary"]["green"], 0)
        self.assertTrue(any("unavailable" in note for note in result["notices"]))

    def test_probes_are_two_fixed_bounded_metadata_only_commands(self):
        responses = [SimpleNamespace(returncode=0, stdout=timer() + "\n\n" + service()),
                     SimpleNamespace(returncode=0, stdout=completed())]
        with patch("quant_data.inspector_fetch_status.subprocess.run", side_effect=responses) as call:
            read_fetch_status("2026-09-04", observed_at=NOW)
        self.assertEqual(call.call_count, 2)
        unit_args = call.call_args_list[0].args[0]
        journal_args = call.call_args_list[1].args[0]
        self.assertEqual(set(unit_args[6:]), _UNITS)
        self.assertEqual({arg.split("=", 1)[1] for arg in journal_args if arg.startswith("--user-unit=")}, _SERVICES)
        self.assertIn("--lines=2000", journal_args)
        fields = next(arg for arg in journal_args if arg.startswith("--output-fields=")).split("=", 1)[1].split(",")
        self.assertNotIn("MESSAGE", fields)
        self.assertNotIn("_CMDLINE", fields)
        for called in call.call_args_list:
            self.assertFalse(called.kwargs["shell"])
            self.assertEqual(called.kwargs["timeout"], 3)
            self.assertEqual(called.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(set(called.kwargs["env"]), {"PATH", "LC_ALL", "TZ", "XDG_RUNTIME_DIR"})

    def test_month_lengths_navigation_and_year_boundaries(self):
        for selected, expected in (("2024-02-29", 29), ("2025-02-28", 28),
                                   ("2026-09-04", 30), ("2026-05-31", 31)):
            with self.subTest(selected=selected):
                result = self.snapshot(day=selected)
                self.assertEqual(len(result["days"]), expected)
                self.assertEqual([day["date"] for day in result["days"]],
                                 [selected[:8] + str(day).zfill(2) for day in range(1, expected + 1)])
                self.assertEqual(sum(day["is_selected"] for day in result["days"]), 1)
                self.assertTrue(all(event["date"].startswith(selected[:7])
                                    for day in result["days"] for event in day["events"]))
        january = self.snapshot(day="2026-01-31")
        self.assertEqual(january["previous_month_date"], "2025-12-31")
        self.assertEqual(january["next_month_date"], "2026-02-28")
        leap = self.snapshot(day="2024-03-31")
        self.assertEqual(leap["previous_month_date"], "2024-02-29")
        self.assertEqual(leap["next_month_date"], "2024-04-30")
        self.assertIsNone(self.snapshot(day="1900-01-01")["previous_month_date"])
        self.assertIsNone(self.snapshot(day="9998-12-31")["next_month_date"])

    def test_daily_marker_keeps_any_failure_without_affecting_another_batch(self):
        from quant_data.inspector_schedules import TIMER_BINDINGS
        gdp = next(binding for binding in TIMER_BINDINGS if binding.label == "GDP / CPI")
        gdp_timer = timer(["Mon..Fri *-*-* 09:05:00 America/New_York"]).replace(TIMER, gdp.unit)
        gdp_service = service(Id=gdp.unit.replace(".timer", ".service"),
            ExecMainStartTimestamp="Fri 2026-09-04 13:05:12 UTC",
            ExecMainExitTimestamp="Fri 2026-09-04 13:05:24 UTC")
        units = "\n\n".join((timer(), service(), gdp_timer, gdp_service))
        result = self.snapshot(units, completed(result="failed"))
        day = next(day for day in result["days"] if day["is_selected"])
        markers = {marker["label"]: marker for marker in day["markers"]}
        calendar = markers["Economic calendar"]
        self.assertEqual(calendar["status"], "failed")
        self.assertEqual(calendar["run_count"], 2)
        self.assertEqual(calendar["counts"]["failed"], 1)
        self.assertEqual(calendar["counts"]["succeeded"], 1)
        self.assertEqual(markers["GDP / CPI"]["status"], "succeeded")
        self.assertEqual(len(day["events"]), 3)
        old_day = next(day for day in result["days"] if day["date"] == "2026-09-03")
        self.assertTrue(all(marker["status"] == "unconfirmed" for marker in old_day["markers"]))
        self.assertTrue(all(marker["counts"]["failed"] == 0 for marker in old_day["markers"]))
        future_day = next(day for day in result["days"] if day["date"] == "2026-09-07")
        self.assertTrue(all(marker["status"] == "upcoming" for marker in future_day["markers"]))
        complete = self.snapshot(journal=completed())
        self.assertEqual(next(day for day in complete["days"] if day["is_selected"])["markers"][0]["status"], "succeeded")

    def test_monthly_journal_window_is_bounded_and_future_month_skips_history(self):
        unit_response = SimpleNamespace(returncode=0, stdout=timer() + "\n\n" + service())
        with patch("quant_data.inspector_fetch_status.subprocess.run", side_effect=[unit_response, SimpleNamespace(returncode=0, stdout="")]) as call:
            read_fetch_status("2026-05-20", observed_at=NOW)
        journal = call.call_args_list[1].args[0]
        self.assertIn("--since=2026-05-01 04:00:00 UTC", journal)
        self.assertIn("--until=2026-06-01 04:00:00 UTC", journal)
        self.assertIn("--lines=2000", journal)
        with patch("quant_data.inspector_fetch_status.subprocess.run", return_value=unit_response) as call:
            future = read_fetch_status("2026-10-01", observed_at=NOW)
        call.assert_called_once()
        self.assertTrue(all(event["status"] == "upcoming" for day in future["days"] for event in day["events"]))

    def test_bad_dates_and_failed_probes_do_not_run_commands_or_leak_errors(self):
        for value in ("", "2026-02-30", "2026-09-04;id", "../../private", "2026-9-4"):
            with self.subTest(value=value), patch("quant_data.inspector_fetch_status.subprocess.run") as call:
                with self.assertRaises(ValidationError):
                    read_fetch_status(value, observed_at=NOW)
                call.assert_not_called()
        with patch("quant_data.inspector_fetch_status.subprocess.run", side_effect=OSError("private path secret")):
            result = read_fetch_status("2026-09-04", observed_at=NOW)
        self.assertNotIn("private path secret", json.dumps(result))
        self.assertEqual(result["summary"]["total"], 0)


class SavedFetchStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="inspector-run-history-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "history"

    def save(self, code=0, minute=15, invocation="a" * 32, unfinished=False):
        from quant_data.operations.fetch_run_history import run_recorded_cli
        run_recorded_cli(TIMER, lambda: code, argv=(), root=self.root,
            clock=Mock(side_effect=[NOW.replace(hour=12, minute=minute, second=12),
                                    NOW.replace(hour=12, minute=minute, second=24)]),
            environment={"INVOCATION_ID": invocation})
        if unfinished:
            path = next(path for path in self.root.glob("*/*/run.json")
                        if json.loads(path.read_text())["invocation_id"] == invocation)
            row = json.loads(path.read_text())
            row.update(outcome="running", finished_at=None, exit_code=None)
            path.write_text(json.dumps(row))

    def snapshot(self, journal="", units=None, now=NOW):
        with patch("quant_data.inspector_fetch_status.subprocess.run") as command:
            result = read_fetch_status("2026-09-04", observed_at=now, probe=False,
                unit_output=timer() if units is None else units, journal_output=journal, history_root=self.root)
            command.assert_not_called()
        return result

    def test_saved_outcomes_survive_empty_service_and_journal_history(self):
        self.save()
        self.save(75, minute=45, invocation="b" * 32)
        result = self.snapshot()
        first, second = result["focus_events"]
        self.assertEqual((first["status"], second["status"]), ("succeeded", "failed"))
        self.assertEqual((first["evidence"], second["exit_code"]), ("saved_run", 75))
        self.assertEqual(first["started_at"], "2026-09-04T12:15:12Z")
        self.assertEqual(first["finished_at"], "2026-09-04T12:15:24Z")
        self.assertIn("Saved run failed", second["note"])
        self.assertEqual(result["days"][3]["markers"][0]["status"], "failed")
        self.assertEqual(result["days"][0]["events"][0]["status"], "unconfirmed")

    def test_same_invocation_is_deduplicated_but_separate_attempts_are_ambiguous(self):
        self.save()
        units = timer() + "\n\n" + service(InvocationID="a" * 32,
            ExecMainStartTimestamp="Fri 2026-09-04 12:15:10 UTC",
            ExecMainExitTimestamp="Fri 2026-09-04 12:15:25 UTC")
        first = self.snapshot(completed(USER_INVOCATION_ID="a" * 32), units)["focus_events"][0]
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["evidence"], "saved_run")
        first = self.snapshot(completed(USER_INVOCATION_ID="b" * 32), units)["focus_events"][0]
        self.assertEqual(first["status"], "unconfirmed")
        self.assertIn("Multiple runs", first["note"])
        self.save(75, invocation="c" * 32)
        self.assertEqual(self.snapshot()["focus_events"][0]["status"], "unconfirmed")

    def test_completion_invocation_identifies_a_journal_start_without_one(self):
        self.save()
        units = timer() + "\n\n" + service(ExecMainStartTimestamp="", ExecMainExitTimestamp="")
        journal = entry("2026-09-04T12:15:10Z", USER_INVOCATION_ID="") + "\n" + entry(
            "2026-09-04T12:15:25Z", result="done", USER_INVOCATION_ID="a" * 32)
        first = self.snapshot(journal, units)["focus_events"][0]
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["evidence"], "saved_run")

    def test_disagreement_between_systemd_sources_is_not_erased_by_saved_receipt(self):
        self.save()
        units = timer() + "\n\n" + service(InvocationID="a" * 32, Result="exit-code", ExecMainStatus="75",
            ExecMainStartTimestamp="Fri 2026-09-04 12:15:10 UTC",
            ExecMainExitTimestamp="Fri 2026-09-04 12:15:25 UTC")
        first = self.snapshot(completed(USER_INVOCATION_ID="a" * 32), units)["focus_events"][0]
        self.assertEqual(first["status"], "unconfirmed")
        self.assertEqual(first["evidence"], "conflicting_results")

    def test_orphan_start_uses_only_exact_terminal_systemd_invocation(self):
        self.save(unfinished=True)
        units = timer() + "\n\n" + service(ExecMainStartTimestamp="", ExecMainExitTimestamp="")
        with patch("quant_data.operations.fetch_run_history._process_identity", return_value=None):
            first = self.snapshot()["focus_events"][0]
            self.assertEqual(first["status"], "unconfirmed")
            self.assertIn("original process cannot", first["note"])
            first = self.snapshot(completed(USER_INVOCATION_ID="a" * 32, result="failed"), units)["focus_events"][0]
            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["evidence"], "saved_start_systemd")
            self.assertEqual(first["finished_at"], "2026-09-04T12:15:24Z")
            self.assertIn("same invocation", first["note"])
            self.assertIsNone(first["exit_code"])
            first = self.snapshot(completed(USER_INVOCATION_ID="b" * 32, result="failed"), units)["focus_events"][0]
            self.assertEqual(first["status"], "unconfirmed")

    def test_saved_running_future_and_conflicting_outcomes_are_honest(self):
        self.save()
        before_finish = NOW.replace(hour=12, minute=15, second=20)
        self.assertEqual(self.snapshot(now=before_finish)["focus_events"][0]["status"], "unconfirmed")
        units = timer() + "\n\n" + service(ExecMainStartTimestamp="", ExecMainExitTimestamp="")
        first = self.snapshot(completed(USER_INVOCATION_ID="a" * 32, result="failed"), units)["focus_events"][0]
        self.assertEqual(first["status"], "unconfirmed")
        self.assertEqual(first["evidence"], "conflicting_results")
        self.save(minute=45, invocation="b" * 32, unfinished=True)
        self.assertEqual(self.snapshot()["focus_events"][1]["status"], "running")

    def test_missing_or_invalid_history_discloses_gap_without_creating_files(self):
        first = self.snapshot()
        self.assertFalse(self.root.exists())
        self.assertTrue(any("has not been created" in note for note in first["notices"]))
        self.save()
        path = next(self.root.glob("*/*/run.json"))
        path.write_text("not-json")
        result = self.snapshot()
        self.assertTrue(any("could not be read" in note for note in result["notices"]))
        self.assertEqual(result["focus_events"][0]["status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
