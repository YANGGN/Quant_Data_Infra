from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import tempfile
import unittest

from quant_data.operations import fetch_run_history as history
from quant_data.operations.fetch_run_summary import record_report, summarize_report
from quant_data.inspector_fetch_status import _Run, _event, _merge_saved_run, _day_markers
from quant_data.inspector_schedules import TIMER_BINDINGS
from quant_data.dashboard.fetch_status_page import _focus_event, _news_group

START = datetime(2026, 9, 8, 22, 30, 17, tzinfo=timezone.utc)
END = START + timedelta(minutes=2)
NOW = END + timedelta(minutes=1)
BATCH = "quant-data-macro-current-refresh.timer"
BINDING = next(binding for binding in TIMER_BINDINGS if binding.unit == BATCH)
REPORT = {"steps": [{"outcome": "published"}] * 7 + [{"outcome": "unchanged"}] * 20
          + [{"outcome": "failed", "error": "private secret https://provider.invalid"}] * 2}


class PartialFetchStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="partial-fetch-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "history"

    def invoke(self, *, report=REPORT, code=74, failure=None):
        def operation():
            record_report(BATCH, report)
            if failure:
                raise failure
            return code
        return history.run_recorded_cli(BATCH, operation, argv=(), root=self.root,
            clock=Mock(side_effect=[START, END]), environment={"INVOCATION_ID": "a" * 32})

    def read(self, now=NOW):
        return history.read_fetch_run_history(self.root, start=START - timedelta(hours=1),
            end=NOW + timedelta(hours=1), observed_at=now)

    def receipt(self):
        return next(self.root.glob("*/*/run.json"))

    def event(self, record):
        run = _Run(BATCH.replace(".timer", ".service"), START, END,
                   record["outcome"], "a" * 32, "saved_run", record["exit_code"],
                   record["run_id"], record.get("counts"))
        return _event(BINDING, START.replace(second=0), [run], NOW)

    def test_partial_run_keeps_failed_exit_and_displays_successful_work(self):
        self.assertEqual(self.invoke(), 74)
        result = self.read()
        self.assertEqual(result["invalid"], 0)
        row = result["records"][0]
        self.assertEqual((row["outcome"], row["exit_code"]), ("failed", 74))
        self.assertEqual((row["counts"]["successful"], row["counts"]["failed"]), (27, 2))
        event = self.event(row)
        self.assertEqual((event["status"], event["status_label"]), ("partial", "Partial Success"))
        markup = _focus_event(event)
        for text in ("Partial Success", "Sources · recorded run", "Successful", "Failed", ">27</dd>", ">2</dd>"):
            self.assertIn(text, markup)
        self.assertNotIn("private secret", json.dumps(result) + markup)
        self.assertEqual(json.loads(self.receipt().read_text())["version"], 1)
        self.assertNotIn("counts", json.loads(self.receipt().read_text()))
        self.assertEqual(self.receipt().with_name("summary.json").stat().st_mode & 0o777, 0o600)

    def test_total_failure_and_complete_run_keep_distinct_statuses(self):
        for report, code, expected in [
            ({"steps": [{"outcome": "failed"}] * 2}, 74, "failed"),
            ({"steps": [{"outcome": "unchanged"}] * 2}, 0, "succeeded"),
        ]:
            with self.subTest(expected=expected):
                self.invoke(report=report, code=code)
                rows = self.read()["records"]
                row = next(row for row in rows if row["counts"]["successful"] == (2 if code == 0 else 0))
                self.assertEqual(self.event(row)["status"], expected)

    def test_absent_or_invalid_summary_never_invents_success_or_zero_counts(self):
        self.invoke()
        path = self.receipt().with_name("summary.json")
        original = json.loads(path.read_text())
        for change in (
            {"run_id": "b" * 32}, {"batch_id": "quant-data-current-news-refresh.timer"},
            {"exit_code": 0}, {"finished_at": "2026-09-08T23:00:00Z"},
            {"counts": {**original["counts"], "successful": True}},
            {"counts": {**original["counts"], "failed": -1}},
            {"counts": {**original["counts"], "secret": "private"}},
            {"counts": {**original["counts"], "unit": "<script>"}},
            {"source_sha256": "bad"},
        ):
            with self.subTest(change=change):
                path.write_text(json.dumps({**original, **change}))
                result = self.read()
                self.assertEqual(result["invalid"], 1)
                row = result["records"][0]
                self.assertNotIn("counts", row)
                self.assertEqual(self.event(row)["status"], "failed")
        path.unlink()
        self.assertIn("counts were not recorded", _focus_event(self.event(self.read()["records"][0])))

    def test_summary_symlink_and_oversized_payload_leave_original_receipt_usable(self):
        self.invoke()
        path = self.receipt().with_name("summary.json")
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(self.read()["invalid"], 1)
        path.unlink()
        path.write_bytes(b"x" * 4097)
        result = self.read()
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(len(result["records"]), 1)
        self.assertNotIn("counts", result["records"][0])

    def test_historical_summary_preserves_original_bytes_and_respects_recorded_time(self):
        self.invoke()
        path = self.receipt()
        original = path.read_bytes()
        record = json.loads(original)
        history.save_run_summary(path.with_name("summary.json"), record,
            summarize_report(BATCH, REPORT), recorded_at=NOW.isoformat(),
            provenance="retained_evidence", source_sha256="f" * 64)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn("counts", self.read(now=END)["records"][0])
        self.assertEqual(self.read()["records"][0]["counts"]["successful"], 27)
        self.assertEqual(self.read(now=START)["records"], [])

    def test_conflicting_systemd_evidence_remains_unconfirmed(self):
        self.invoke()
        record = self.read()["records"][0]
        saved = _Run(BATCH.replace(".timer", ".service"), START, END, "failed", "a"*32,
                     "saved_run", 74, record["run_id"], record["counts"])
        for systemd_status, expected in [("failed", "partial"), ("succeeded", "unconfirmed")]:
            runs = [_Run(saved.service, START, END, systemd_status, saved.invocation)]
            _merge_saved_run(runs, saved)
            event = _event(BINDING, START.replace(second=0), runs, NOW)
            self.assertEqual(event["status"], expected)
            if expected == "unconfirmed":
                self.assertIsNone(event["counts"])

    def test_observer_preserves_exception_and_does_not_leak_context_to_manual_calls(self):
        failure = RuntimeError("private details")
        with self.assertRaises(RuntimeError) as caught:
            self.invoke(failure=failure)
        self.assertIs(caught.exception, failure)
        row = self.read()["records"][0]
        self.assertEqual(row["exit_code"], 1)
        before = self.receipt().with_name("summary.json").read_bytes()
        record_report(BATCH, {"steps": [{"outcome": "succeeded"}]})
        self.assertEqual(before, self.receipt().with_name("summary.json").read_bytes())

    def test_summary_write_failure_never_changes_success_or_failure(self):
        with patch.object(history, "save_run_summary", side_effect=OSError("private")):
            self.assertEqual(self.invoke(), 74)
        self.assertNotIn("counts", self.read()["records"][0])

    def test_adapter_counts_match_investigated_units_without_double_counting(self):
        examples = [
            ("quant-data-sec-company-fundamentals.timer",
             {"succeeded_cik_count": 510, "failed_cik_count": 3, "skipped_existing_cik_count": 0},
             ("issuers", 510, 3, 0, 0)),
            ("quant-data-alpaca-spy-options.timer",
             {"completed_underlyings": ["fixture"] * 13, "failed_underlyings": ["fixture"] * 2},
             ("etfs", 13, 2, 0, 0)),
            ("quant-data-company-market-refresh.timer",
             {"steps": [{"outcome": "unchanged"}] * 1027 + [{"outcome": "partial"}] * 499
                       + [{"outcome": "failed"}] * 22},
             ("steps", 1027, 22, 499, 0)),
            ("quant-data-market-close.timer",
             {"results": [{"outcome": "published"}] * 619 + [{"outcome": "failed_response"}] * 9
                         + [{"outcome": "terminal_noncoverage"}] * 2},
             ("symbols", 619, 9, 0, 2)),
            ("quant-data-fmp-macro-calendar.timer",
             {"published": 0, "unchanged": 1, "employment_outcome": "unchanged", "wholesale_outcome": "unchanged"},
             ("steps", 3, 0, 0, 0)),
        ]
        for batch, report, expected in examples:
            with self.subTest(batch=batch):
                counts = summarize_report(batch, report)
                self.assertEqual(tuple(counts[k] for k in ("unit", "successful", "failed", "partial", "skipped")), expected)

    def test_partial_company_counts_and_news_group_do_not_become_failed_slots(self):
        self.invoke()
        event = self.event(self.read()["records"][0])
        event["batch_id"] = "quant-data-current-news-refresh.timer"
        event["label"] = "Current news"
        pending = {**event, "status": "upcoming", "status_label": "Scheduled", "counts": None}
        complete = {**event, "status": "succeeded", "status_label": "Completed", "color": "green"}
        group = _news_group((event, complete, pending))
        self.assertIn("Partial Success", group)
        self.assertIn("<strong>1</strong> partial success", group)
        marker = _day_markers([event, complete, pending])[0]
        self.assertEqual(marker["status"], "partial")
        self.assertEqual(marker["counts"]["failed"], 0)
        failed = {**event, "status": "failed", "status_label": "Failed", "color": "red"}
        self.assertEqual(_day_markers([event, failed])[0]["status"], "failed")
