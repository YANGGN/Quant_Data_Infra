from __future__ import annotations

import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quant_data.dashboard.data_status_page import render_data_status_page
from quant_data.inspector_schedules import (
    TIMER_BINDINGS, _timer_properties, _unit_schedule, read_local_refresh_schedules,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from tests.test_inspector_presentation import Markup

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def block(unit, *, active="active", load="loaded", upcoming="Mon 2099-01-05 22:30:00 UTC",
          calendars=("Mon..Fri *-*-* 18:30:00 America/New_York",)):
    return "\n".join([
        f"Id={unit}", f"LoadState={load}", f"ActiveState={active}", "SubState=waiting",
        *(f"TimersCalendar={{ OnCalendar={calendar} ; next_elapse={upcoming} }}"
          for calendar in calendars),
        f"NextElapseUSecRealtime={upcoming}",
    ])


def record(identity):
    return {"fields": [{"name": "id", "value": identity},
                       {"name": "status", "value": "current"},
                       {"name": "freshness", "value": '{"cadence":"monthly","stale_after":"P7D"}'}]}


class InspectorScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(CANONICAL_REGISTRY_PATH, project_root=ROOT, environment={})

    def snapshot(self, output):
        response = SimpleNamespace(returncode=0, stdout=output)
        with patch("quant_data.inspector_schedules.subprocess.run", return_value=response) as run:
            result = read_local_refresh_schedules(self.registry)
        return result, run

    def test_read_is_one_fixed_bounded_command_without_shell_or_credentials(self):
        _, run = self.snapshot("\n\n".join(block(b.unit) for b in TIMER_BINDINGS))
        run.assert_called_once()
        args, options = run.call_args
        command = args[0]
        self.assertEqual(command[:3], ["/usr/bin/systemctl", "--user", "show"])
        self.assertEqual(set(command[6:]), {b.unit for b in TIMER_BINDINGS})
        self.assertFalse(options["shell"])
        self.assertEqual(options["timeout"], 3)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertEqual(set(options["env"]), {"PATH", "LC_ALL", "TZ", "XDG_RUNTIME_DIR"})
        self.assertEqual(options["env"]["TZ"], "UTC")

    def test_bindings_cover_reviewed_units_and_only_registered_outputs(self):
        self.assertEqual({b.unit for b in TIMER_BINDINGS},
                         {p.name for p in (ROOT / "deploy/systemd").glob("*.timer")})
        dataset_ids = {d.id for d in self.registry.datasets}
        self.assertTrue(all(set(b.datasets) <= dataset_ids for b in TIMER_BINDINGS))

    def test_shared_timers_map_sources_and_leave_historical_datasets_unscheduled(self):
        result, _ = self.snapshot("\n\n".join(block(b.unit) for b in TIMER_BINDINGS))
        for identity in ("market.stage10.daily_prices", "fixture.company.fundamentals",
                         "fixture.company.corporate_actions", "fixture.market.options",
                         "fixture.macro.rtdsm_employ", "news.source.fmp_stock_latest",
                         "news.source.alpaca_benzinga", "news.source.fed_press"):
            self.assertEqual(result[identity]["schedule_state"], "scheduled", identity)
        for identity in ("market.fmp.daily_prices", "macro.bea.nipa_history",
                         "macro.fmp.economic_calendar_evidence", "news.fmp.stock_latest_articles",
                         "fixture.news.items", "market.stage10.instruments"):
            self.assertEqual(result[identity]["schedule_state"], "unmapped", identity)
            self.assertIsNone(result[identity]["next_scheduled_fetch"])

    def test_multiple_schedules_choose_earliest_known_trigger_and_label_both(self):
        output = block("quant-data-macro-vintages.timer", upcoming="Mon 2099-01-05 13:05:00 UTC",
                       calendars=("Mon..Fri *-*-* 09:05:00 America/New_York",))
        output += "\n\n" + block("quant-data-employment-vintages.timer",
                               upcoming="Fri 2099-02-06 15:05:00 UTC",
                               calendars=("Fri *-*-01..07 10:05:00 America/New_York",))
        result, _ = self.snapshot(output)
        schedule = result["macro.official_vintages"]
        self.assertEqual(schedule["next_scheduled_fetch"], "2099-01-05T13:05:00Z")
        self.assertIn("GDP / CPI: Weekdays at 09:05 New York", schedule["refresh_cadence"])
        self.assertIn("Employment: First Friday", schedule["refresh_cadence"])
        self.assertIn("Earliest known", schedule["schedule_note"])

    def test_loaded_calendar_overrides_disk_and_retains_multiple_daily_times(self):
        unit = "quant-data-fmp-macro-calendar.timer"
        output = block(unit, upcoming="Mon 2026-11-02 13:15:00 UTC", calendars=(
            "Mon..Fri *-*-* 08:45:00 America/New_York", "Mon..Fri *-*-* 08:15:00 America/New_York"))
        schedule = _unit_schedule(_timer_properties(output)[unit], NOW)
        self.assertIn("08:15", schedule["refresh_cadence"])
        self.assertIn("08:45", schedule["refresh_cadence"])
        self.assertEqual(schedule["next_scheduled_fetch"], "2026-11-02T13:15:00Z")
        changed = _timer_properties(block(unit, calendars=("Mon..Fri *-*-* 06:12:00 America/New_York",)))[unit]
        self.assertEqual(_unit_schedule(changed, NOW)["refresh_cadence"], "Weekdays at 06:12 New York")

    def test_inactive_missing_and_past_triggers_never_claim_future_fetch(self):
        unit = TIMER_BINDINGS[0].unit
        for options, expected in (({"active": "inactive"}, "inactive"),
                                  ({"load": "not-found"}, "inactive"),
                                  ({"active": "failed"}, "inactive"),
                                  ({"upcoming": "n/a"}, "unavailable"),
                                  ({"upcoming": "Fri 2026-09-04 22:00:00 UTC"}, "unavailable")):
            with self.subTest(options=options):
                schedule = _unit_schedule(_timer_properties(block(unit, **options))[unit], NOW)
                self.assertEqual(schedule["schedule_state"], expected)
                self.assertIsNone(schedule["next_scheduled_fetch"])

    def test_missing_bus_timeout_and_bad_output_fail_to_safe_unavailable_labels(self):
        for response in (FileNotFoundError("/private/path secret"),
                         subprocess.TimeoutExpired("secret-command", 3),
                         SimpleNamespace(returncode=1, stdout="secret"),
                         SimpleNamespace(returncode=0, stdout="x" * 65_537)):
            options = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with self.subTest(response=type(response).__name__), patch("quant_data.inspector_schedules.subprocess.run", **options):
                result = read_local_refresh_schedules(self.registry)
                self.assertEqual(result["market.stage10.daily_prices"]["schedule_state"], "unavailable")
                self.assertNotIn("secret", str(result))
                self.assertNotIn("/private", str(result))

    def test_unrequested_units_cannot_supply_schedule_properties(self):
        self.assertEqual(_timer_properties(block("unrelated.timer")), {})
        unit = TIMER_BINDINGS[0].unit
        self.assertEqual(_timer_properties(block(unit) + "\nId=unrelated.timer"), {})

    def test_presentation_distinguishes_schedule_from_freshness_and_escapes_values(self):
        hostile = '<img src=x onerror="bad">'
        schedules = {"active": dict(refresh_cadence="Hourly at :10 UTC " + hostile,
                                    next_scheduled_fetch="2099-01-05T13:10:00Z",
                                    schedule_state="scheduled", schedule_note=hostile),
                     "off": dict(refresh_cadence="Weekdays", next_scheduled_fetch="2099-01-05T13:10:00Z",
                                 schedule_state="inactive", schedule_note="Timer inactive"),
                     "unknown": dict(schedule_state="unavailable")}
        document = render_data_status_page({"records": [record(i) for i in ("active", "off", "unknown", "manual")]},
                                           registry_revision="fixture", schedules=schedules)
        markup = Markup(document)
        self.assertIn("Refresh Cadence", document)
        self.assertIn("Next Scheduled Fetch", document)
        self.assertIn('data-label="Refresh Cadence"', document)
        self.assertIn('data-label="Next Scheduled Fetch"', document)
        self.assertIn('data-inspector-record-note="Displayed fields', document)
        self.assertIn("Hourly at :10 UTC", document)
        self.assertNotIn("monthly", document)
        self.assertIn("Not scheduled", document)
        self.assertIn("Unavailable", document)
        times = [attrs for tag, attrs in markup.elements if tag == "time"]
        self.assertEqual(times, [{"datetime": "2099-01-05T13:10:00Z"}])
        self.assertFalse(any(tag == "img" for tag, _ in markup.elements))
        self.assertIn("P7D", document)

    def test_empty_presentation_and_unavailable_snapshot_preserve_table_shape(self):
        empty = render_data_status_page({}, registry_revision="fixture", schedules={})
        self.assertIn('colspan="6"', empty)
        document = render_data_status_page({"records": [record("active")]}, registry_revision="fixture")
        self.assertIn('data-schedule-state="unavailable"', document)


if __name__ == "__main__":
    unittest.main()
