"""Presentation regressions for completed derivations versus source freshness."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quant_data.dashboard.fetch_status_page import _focus_event, render_fetch_status_page
from quant_data.dashboard.forward_pe_page import render_forward_pe_page
from quant_data.dashboard.status_page import render_status_page
from quant_data.inspector_fetch_status import _Run, _day_markers, _event, read_fetch_status
from quant_data.inspector_schedules import TIMER_BINDINGS
from tests.dashboard.test_equibles_status_presentation import snapshot
from tests.test_inspector_fetch_status import timer, service, TIMER, SERVICE

BATCH = "quant-data-derived-refresh.timer"
BINDING = next(binding for binding in TIMER_BINDINGS if binding.unit == BATCH)
SLOT = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)
COUNTS = dict(unit="symbols", successful=234, failed=0, partial=1948, skipped=0, unattempted=0)


def derived(**overrides):
    result = dict(calculated=2182, current_inputs=2180, freshness_warnings=2,
                  waiting_inputs=0, catchup_pending=0, unchanged_checks=1946,
                  stale_inputs=1, unconfirmed_inputs=1, input_warnings=0,
                  recorded_freshness_warnings=1948, evidence="publication_and_source_checks")
    result.update(overrides)
    return result


def run_event(details=None, *, state="failed", root=True):
    run = _Run(BATCH.replace(".timer", ".service"), SLOT + timedelta(seconds=42),
               None if state == "running" else SLOT + timedelta(minutes=14), state,
               "fixture-invocation", "saved_run", 2, "fixture-run", deepcopy(COUNTS))
    helper = Mock(return_value=details or {})
    with patch.dict("sys.modules", {"quant_data.derived_refresh_status": SimpleNamespace(presentation=helper)}):
        event = _event(BINDING, SLOT, [run], SLOT + timedelta(hours=1),
                       Path("/explicit/fixture/export") if root else None)
    return event, helper


def pe_result(ticker=None):
    return dict(symbols=["TEST"], cutoff="2026-09-22T10:30:42Z",
                latest_price_date="2026-09-21", days=[["2026-09-21", 30, 2, 15, "unverified_basis", None]],
                windows={}, ticker_refresh=ticker or {}, refresh={})


class DerivedPresentationTests(unittest.TestCase):
    def test_publication_separates_warning_from_failed_process_receipt(self):
        event, helper = run_event(derived())
        self.assertEqual((event["status"], event["color"]), ("warning", "amber"))
        self.assertEqual(event["counts"], COUNTS)
        self.assertEqual(event["exit_code"], 2)
        self.assertIn("Calculation work completed", event["note"])
        helper.assert_called_once_with(Path("/explicit/fixture/export"),
                                       started_at="2026-09-22T10:30:42Z", finished_at="2026-09-22T10:44:00Z")
        page = _focus_event(event, linked_data=True)
        self.assertIn("Completed · input warnings", page)
        self.assertIn("Calculated</dt><dd>2182", page)
        self.assertIn("Current</dt><dd>2180", page)
        self.assertIn("Stale</dt><dd>1", page)
        self.assertIn("Freshness unconfirmed</dt><dd>1", page)
        self.assertIn("1946 symbols have successful unchanged source checks", page)
        self.assertIn("original run recorded 1948", page)
        self.assertNotIn("Incomplete", page)
        self.assertIn('href="/forward-pe"', page)
        self.assertNotIn("No datasets are listed", page)

    def test_resolved_freshness_is_complete_and_real_pending_work_stays_partial(self):
        clean, _ = run_event(derived(current_inputs=2182, freshness_warnings=0, stale_inputs=0, unconfirmed_inputs=0))
        self.assertEqual(clean["status"], "succeeded")
        pending, _ = run_event(derived(calculated=2180, waiting_inputs=1, catchup_pending=1))
        self.assertEqual(pending["status"], "partial")
        page = _focus_event(pending)
        self.assertIn("Waiting for inputs</dt><dd>1", page)
        self.assertIn("Catch-up pending</dt><dd>1", page)
        self.assertNotIn("Calculation work completed", pending["note"])

    def test_legacy_counts_do_not_prove_finished_publication(self):
        event, _ = run_event()
        self.assertEqual(event["status"], "partial")
        self.assertNotIn("derived", event)
        self.assertIn("matching publication receipt is unavailable", event["note"])
        page = _focus_event(event)
        self.assertIn("Unclassified · detail unavailable", page)
        self.assertNotIn("Input / catch-up warnings", page)
        self.assertNotIn("Incomplete", page)
        self.assertNotIn("Calculated</dt>", page)

    def test_running_and_unconfigured_readers_do_not_consult_publication(self):
        event, helper = run_event(derived(), state="running")
        self.assertEqual(event["status"], "running")
        helper.assert_not_called()
        _, helper = run_event(derived(), root=False)
        helper.assert_not_called()

    def test_calendar_and_overview_preserve_completed_warning(self):
        event, _ = run_event(derived())
        marker = _day_markers([event])[0]
        self.assertEqual(marker["status"], "warning")
        self.assertEqual(marker["counts"]["warning"], 1)
        day = dict(date="2026-09-22", is_selected=True, markers=[marker])
        value = snapshot([event], days=[day], selected_date="2026-09-22", today="2026-09-22",
                         summary=dict(total=1, green=0, amber=1, red=0, partial=0, warning=1, pending=0))
        page = render_status_page(value, {}, registry_revision="fixture")
        self.assertIn("Completed runs</span><strong>1</strong>", page)
        self.assertIn("1 input warnings · 0 partial · 0 failed", page)
        self.assertIn("1 completed with input warnings", page)
        self.assertIn('<strong>1</strong> completed with input warnings', page)

    def test_snapshot_summary_counts_warning_separately_from_pending(self):
        units = timer(["*-*-* 06:30:00 America/Toronto"]).replace(TIMER, BATCH)
        units += "\n\n" + service(Id=BATCH.replace(".timer", ".service"), Result="exit-code",
            ExecMainStatus="2", ExecMainStartTimestamp="Tue 2026-09-22 10:30:42 UTC",
            ExecMainExitTimestamp="Tue 2026-09-22 10:44:00 UTC")
        with patch.dict("sys.modules", {"quant_data.derived_refresh_status": SimpleNamespace(presentation=lambda *a, **k: derived())}):
            value = read_fetch_status("2026-09-22", observed_at=SLOT+timedelta(hours=1),
                unit_output=units, journal_output="", probe=False, derived_root=Path("/fixture"))
        self.assertEqual(value["summary"], dict(total=1, partial=0, warning=1, pending=0, green=0, amber=1, red=0))

    def test_current_status_distinguishes_waiting_and_older_captures(self):
        status = dict(state="complete_with_gaps", outcomes=dict(stale_inputs=1948, waiting_inputs=0, catchup_pending=0),
                      input_freshness=derived())
        page = render_status_page(snapshot(), {}, registry_revision="fixture", derived_status=status)
        self.assertIn("Calculations complete · input warnings", page)
        self.assertIn("Current: 2,180 · Stale: 1 · Freshness unconfirmed: 1", page)
        self.assertIn("1,946 tickers were successfully checked", page)
        self.assertNotIn("1,948 tickers have stale inputs or need attention", page)
        status.pop("input_freshness")
        page = render_status_page(snapshot(), {}, registry_revision="fixture", derived_status=status)
        self.assertIn("1,948 tickers were flagged for older stored input captures", page)
        self.assertIn("Input freshness warnings do not count unavailable P/E values", page)

    def test_ticker_labels_use_confirmed_checks_and_escape_notes(self):
        ticker = dict(outcome="stale_inputs", issue="old source captures",
                      input_freshness=dict(state="current", unchanged=True, note="<source evidence>"))
        page = render_forward_pe_page(pe_result(ticker), revision="fixture", symbol="TEST", period="5y")
        self.assertIn("Current · successful unchanged source check", page)
        self.assertIn("&lt;source evidence&gt;", page)
        self.assertNotIn("<source evidence>", page)
        ticker.pop("input_freshness")
        page = render_forward_pe_page(pe_result(ticker), revision="fixture", symbol="TEST", period="5y")
        self.assertIn("Freshness unconfirmed · older capture; recent source-check evidence is unavailable", page)
        self.assertIn("Actual reported EPS is not the denominator", page)
        self.assertIn("P/E stays unavailable even when estimates exist", page)

    def test_stale_unknown_and_pending_tickers_have_distinct_labels(self):
        for state,label in (("stale","Stale"),("unconfirmed","Freshness unconfirmed"),
                            ("input_warning","Other input warning"),("waiting_inputs","Waiting for inputs"),
                            ("catchup_pending","Catch-up pending")):
            with self.subTest(state=state):
                ticker=dict(input_freshness=dict(state=state,note="fixture"))
                page=render_forward_pe_page(pe_result(ticker),revision="fixture",symbol="TEST",period="5y")
                self.assertIn(label+" · fixture",page)

    def test_missing_counts_and_empty_derived_status_remain_explicit(self):
        event, _ = run_event()
        event["counts"] = None
        self.assertIn("counts were not recorded", _focus_event(event))
        page = render_status_page(snapshot(), {}, registry_revision="fixture")
        self.assertIn("No derived refresh receipt is available yet", page)


if __name__ == "__main__":
    unittest.main()
