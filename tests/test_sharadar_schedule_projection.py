from datetime import date, datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from quant_data.inspector_fetch_status import _slots, read_fetch_status
from quant_data.inspector_schedules import TIMER_BINDINGS, _timer_properties, _unit_schedule
from quant_data.dashboard.fetch_status_page import render_fetch_status_page

UNIT = "quant-data-sharadar-selected-refresh.timer"
CALENDAR = "*-*-* 05:00:00 America/New_York"


class SharadarScheduleProjectionTests(unittest.TestCase):
    def test_daily_slot_preserves_eastern_daylight_saving_transition(self):
        rows, supported = _slots(["{ OnCalendar=" + CALENDAR + " ; next_elapse=n/a }"],
            date(2026, 10, 31), 3)
        self.assertTrue(supported)
        self.assertEqual([row.isoformat() for row in rows], [
            "2026-10-31T09:00:00+00:00", "2026-11-01T10:00:00+00:00", "2026-11-02T10:00:00+00:00"])

    def test_sharadar_outputs_have_only_their_fixed_timer_binding(self):
        binding = next(row for row in TIMER_BINDINGS if row.unit == UNIT)
        self.assertEqual(set(binding.datasets), {"company.sharadar.evidence", "company.sharadar.sf1",
            "company.sharadar.definition_evidence", "company.sharadar.definitions"})
        self.assertEqual(binding.label, "Sharadar fundamentals")

    def test_loaded_timer_is_visible_without_claiming_execution(self):
        output = "\n".join(["Id=" + UNIT, "LoadState=loaded", "ActiveState=active",
            "TimersCalendar={ OnCalendar=" + CALENDAR + " ; next_elapse=n/a }",
            "NextElapseUSecRealtime=Thu 2026-09-10 09:00:00 UTC"])
        now = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)
        schedule = _unit_schedule(_timer_properties(output)[UNIT], now)
        self.assertEqual(schedule["refresh_cadence"], "Daily at 05:00 New York")
        self.assertEqual(schedule["next_scheduled_fetch"], "2026-09-10T09:00:00Z")
        with patch("quant_data.inspector_fetch_status.subprocess.run") as command:
            snapshot = read_fetch_status("2026-09-10", observed_at=now, probe=False,
                unit_output=output, journal_output="")
            command.assert_not_called()
        event = snapshot["focus_events"][0]
        self.assertEqual((event["label"], event["status"], event["scheduled_local"]),
            ("Sharadar fundamentals", "upcoming", "05:00 EDT"))
        rendered = render_fetch_status_page(snapshot, registry_revision="fixture")
        self.assertIn("Sharadar fundamentals", rendered)
