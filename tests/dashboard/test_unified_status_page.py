from __future__ import annotations

from html.parser import HTMLParser
import unittest

from quant_data.dashboard.status_page import render_status_page
from tests.dashboard.test_equibles_status_presentation import event, snapshot


class Rows(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.rows = []
        self.row = None
        self.cells = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-status-dataset" in attrs:
            self.row = attrs
            self.cells = []
        if tag in ("th", "td") and self.row is not None:
            self.cells.append(attrs)

    def handle_endtag(self, tag):
        if tag == "tr" and self.row is not None:
            self.rows.append((self.row, self.cells))
            self.row = None


def result(*ids):
    return {"records": [{"fields": [
        {"name": "id", "value": identity},
        {"name": "dataset_id", "value": owner},
        {"name": "store", "value": "company"},
        {"name": "status", "value": "current"},
        {"name": "latest_successful_capture", "value": {"captured_at": "2026-09-07T12:00:00Z"}},
    ]} for identity, owner in ids]}


class UnifiedStatusTests(unittest.TestCase):
    def render(self, records=None, **overrides):
        return render_status_page(
            snapshot((event(0, "partial", batch="quant-data-company-market-refresh.timer", label="Company market"),)),
            records or {}, registry_revision="fixture", **overrides,
        )

    def test_one_navigation_destination_connects_runs_to_current_data(self):
        page = self.render(result(("fixture.company.expectations", "fixture.company.expectations")))
        self.assertEqual(page.count('href="/status" aria-current="page"'), 1)
        self.assertNotIn('href="/data-status"', page)
        self.assertIn('data-status-batch-link="quant-data-company-market-refresh.timer"', page)
        self.assertIn('data-status-batches="quant-data-company-market-refresh.timer"', page)
        self.assertIn("Partial Success", page)
        self.assertIn('id="status-datasets"', page)
        self.assertIn("Stored data · current snapshot", page)

    def test_historical_run_selection_never_dates_the_current_inventory(self):
        value = snapshot((), selected_date="2026-08-01", today="2026-09-08")
        page = render_status_page(value, {}, registry_revision="fixture")
        self.assertIn("Run history is for Aug 01, 2026", page)
        self.assertIn("Stored data and backfill progress are current snapshots.", page)

    def test_data_failure_keeps_run_history_and_does_not_claim_missing_datasets(self):
        page = self.render(data_error="<timeout>")
        self.assertIn('data-fetch-month', page)
        self.assertIn("Partial Success", page)
        self.assertIn("Stored data status is unavailable", page)
        self.assertIn("&lt;timeout&gt;", page)
        self.assertNotIn("<timeout>", page)
        self.assertNotIn('data-status-dataset=', page)
        self.assertEqual(page.count("<strong>—</strong>"), 2)
        self.assertIn('href="/status?date=2026-09-07#status-datasets"', page)

    def test_inventory_preserves_evidence_while_schedules_stay_with_runs(self):
        page = self.render(result(("fixture.company.expectations", "fixture.company.expectations")))
        rows = Rows(page).rows
        self.assertEqual(len(rows), 1)
        cells = rows[0][1]
        self.assertEqual(len(cells), 19)
        visible = [cell["data-field"] for cell in cells if "data-inspector-detail-only" not in cell]
        self.assertEqual(visible, ["display_status", "dataset_id", "as_of_date", "last_successful_capture"])
        self.assertIn("public_last_successful_capture", [cell["data-field"] for cell in cells])
        self.assertNotIn("next_scheduled_fetch", [cell["data-field"] for cell in cells])

    def test_source_rows_use_the_registered_output_owner_for_batch_links(self):
        page = self.render(result(("news.source.fed_press", "news.current_multi_source_evidence")))
        self.assertEqual(Rows(page).rows[0][0]["data-status-batches"], "quant-data-current-news-refresh.timer")

    def test_busy_retention_never_falls_back_to_a_public_capture(self):
        identity = "fixture.company.expectations"
        page = self.render(
            result((identity, identity)),
            metadata={identity: {"retention_state": "unknown", "retained_capture_at": None}},
        )
        row = Rows(page).rows[0][0]
        self.assertEqual(row["data-status-category"], "unknown")
        self.assertIn('data-field="last_successful_capture" data-label="Last stored capture · Eastern (EST/EDT)">—</td>', page)

    def test_all_lifecycle_rows_exist_without_javascript_and_text_is_escaped(self):
        hostile = '<script>alert("bad")</script>'
        records = result(("live", "live"), ("fixture", "fixture"), (hostile, hostile))
        page = self.render(records, metadata={"fixture": {"lifecycle": "fixture"}})
        self.assertEqual(len(Rows(page).rows), 3)
        self.assertIn('data-status-data-controls hidden', page)
        self.assertNotIn(hostile, page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Other data · 1", page)


if __name__ == "__main__":
    unittest.main()
