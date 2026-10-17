from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import unittest
from zoneinfo import ZoneInfo

from quant_data.dashboard.fetch_status_page import render_fetch_status_page


NEWS_BATCH = "quant-data-current-news-refresh.timer"


class Markup(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs, tuple(self.stack)))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.text.append((data, tuple(self.stack)))

    def inside(self, attribute):
        return "".join(text for text, ancestors in self.text
                       if any(attribute in attrs for _, attrs in ancestors))


def event(index, status="succeeded", *, batch=NEWS_BATCH, label="Current news"):
    instant = datetime(2026, 9, 7, 4, 10, tzinfo=timezone.utc) + timedelta(hours=index)
    utc = instant.isoformat().replace("+00:00", "Z")
    return {
        "id": f"slot-{index}", "batch_id": batch, "label": label,
        "status": status,
        "color": {"succeeded": "green", "failed": "red"}.get(status, "amber"),
        "scheduled_at": utc, "scheduled_local": instant.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M %Z"),
        "started_at": utc if status in {"succeeded", "failed", "running"} else None,
        "finished_at": utc if status in {"succeeded", "failed"} else None,
        "description": f"Description {index}", "note": f"Recorded evidence {index}",
        "datasets": [f"news.fixture.{index}"],
    }


def snapshot(events=(), **overrides):
    result = {
        "selected_date": "2026-09-07", "today": "2026-09-07",
        "month_start": "2026-09-01", "days": [], "focus_events": list(events),
        "observed_at": "2026-09-07T20:00:00Z",
        "summary": {
            "total": len(events),
            "green": sum(e["status"] == "succeeded" for e in events),
            "red": sum(e["status"] == "failed" for e in events),
            "amber": sum(e["status"] not in {"succeeded", "failed"} for e in events),
        },
    }
    result.update(overrides)
    return result


def progress(**overrides):
    result = {
        "available": True, "outcome": "quota_deferred", "universe": 25,
        "completed_tickers": 7, "transcripts": 318, "current_ticker": "AMZN",
        "requests_this_run": 100, "quota_used": 100, "quota_remaining": 0,
        "quota_reset_at": "2026-09-08T00:00:00Z",
        "recorded_at": "2026-09-07T19:20:00Z",
        "next_scheduled_at": "2026-09-08T00:10:00Z",
    }
    result.update(overrides)
    return result


class EquiblesStatusPresentationTests(unittest.TestCase):
    def render(self, value):
        return render_fetch_status_page(value, registry_revision="fixture")

    def test_hourly_group_is_closed_native_disclosure_at_first_news_position(self):
        events = [
            event(0, batch="other.timer", label="Before news"),
            event(1),
            event(2, batch="not-news.timer", label="Current news"),
            event(3),
            event(4, batch="last.timer", label="After news"),
        ]
        value = snapshot(events)
        original = deepcopy(value)
        document = self.render(value)
        markup = Markup(document)
        groups = [(attrs, ancestors) for tag, attrs, ancestors in markup.elements
                  if tag == "details" and "data-fetch-news-group" in attrs]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("open", groups[0][0])
        self.assertTrue(any(tag == "summary" and ancestors[-1][1] == groups[0][0]
                            for tag, _, ancestors in markup.elements))
        top_events = [attrs.get("data-fetch-event", attrs.get("data-fetch-group"))
                      for tag, attrs, ancestors in markup.elements
                      if tag == "li" and ("data-fetch-event" in attrs or "data-fetch-group" in attrs)
                      and not any("data-fetch-news-group" in a for _, a in ancestors)]
        self.assertEqual(top_events, ["slot-0", "current-news", "slot-2", "slot-4"])
        grouped_events = [attrs["data-fetch-event"] for tag, attrs, ancestors in markup.elements
                          if tag == "li" and "data-fetch-event" in attrs
                          and any("data-fetch-news-group" in a for _, a in ancestors)]
        self.assertEqual(grouped_events, ["slot-1", "slot-3"])
        self.assertEqual(value, original)
        self.assertIn('<strong>5</strong> slots', document)
        for item in events:
            self.assertEqual(document.count('data-fetch-event="' + item["id"] + '"'), 1)
            self.assertIn(item["note"], document)
            self.assertIn(item["datasets"][0], document)

    def test_collapsed_summary_keeps_failure_even_after_later_success(self):
        document = self.render(snapshot([event(0, "failed"), event(1), event(2, "upcoming")]))
        markup = Markup(document)
        summary = "".join(text for text, ancestors in markup.text
                          if any("data-fetch-news-group" in attrs for _, attrs in ancestors)
                          and ancestors and any(tag == "summary" for tag, _ in ancestors))
        self.assertIn("Recorded failure", summary)
        self.assertIn("1 failed", summary)
        self.assertIn("1 completed", summary)
        self.assertIn("1 scheduled", summary)
        badge = [(attrs, ancestors) for _, attrs, ancestors in markup.elements
                 if attrs.get("data-fetch-status") == "failed"]
        self.assertTrue(any(any(tag == "summary" for tag, _ in ancestors) for _, ancestors in badge))

    def test_mixed_outcomes_stay_pending_and_only_all_complete_is_green(self):
        for statuses, label, shade in (
            (["succeeded", "upcoming"], "Pending / unconfirmed", "amber"),
            (["succeeded", "running", "unconfirmed"], "Pending / unconfirmed", "amber"),
            (["succeeded", "unknown"], "Pending / unconfirmed", "amber"),
            (["succeeded", "succeeded"], "All completed", "green"),
        ):
            with self.subTest(statuses=statuses):
                markup = Markup(self.render(snapshot([event(i, s) for i, s in enumerate(statuses)])))
                badges = [attrs for _, attrs, ancestors in markup.elements
                          if "data-fetch-status" in attrs
                          and any(tag == "summary" for tag, _ in ancestors)
                          and any("data-fetch-news-group" in a for _, a in ancestors)]
                self.assertEqual(len(badges), 1)
                self.assertIn("fetch-" + shade, badges[0]["class"])
                self.assertIn(label, markup.inside("data-fetch-news-group"))

    def test_dst_groups_preserve_all_23_or_25_distinct_instants(self):
        for start, count in ((datetime(2026, 3, 8, 5, 10, tzinfo=timezone.utc), 23),
                             (datetime(2026, 11, 1, 4, 10, tzinfo=timezone.utc), 25)):
            with self.subTest(count=count):
                events = []
                for i in range(count):
                    instant = start + timedelta(hours=i)
                    item = event(i)
                    item["scheduled_at"] = instant.isoformat().replace("+00:00", "Z")
                    item["scheduled_local"] = instant.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M %Z")
                    events.append(item)
                document = self.render(snapshot(events))
                self.assertIn(f"{count} hourly slots", document)
                self.assertIn(f"<strong>{count}</strong> slots", document)
                self.assertEqual(document.count("data-fetch-event="), count)
                for item in events:
                    self.assertIn('datetime="' + item["scheduled_at"] + '"', document)
                if count == 25:
                    self.assertIn("01:10 EDT", document)
                    self.assertIn("01:10 EST", document)

    def test_equibles_current_progress_has_recorded_quota_and_retained_data_link(self):
        document = self.render(snapshot(selected_date="2025-01-12", equibles=progress()))
        markup = Markup(document)
        panel = markup.inside("data-equibles-progress")
        self.assertIn("Current backfill", panel)
        self.assertIn("independent of the selected calendar day", panel)
        self.assertIn("Companies complete7 / 25", panel)
        self.assertIn("Stored calls318", panel)
        self.assertIn("Current tickerAMZN", panel)
        self.assertIn("Recorded quota100 used · 0 remaining", panel)
        self.assertIn("Quota paused", panel)
        self.assertIn("Next scheduled fetch2026-09-07 20:10:00 EDT", panel)
        self.assertIn('datetime="2026-09-08T00:10:00Z"', document)
        links = [a["href"] for tag, a, ancestors in markup.elements
                 if tag == "a" and any("data-equibles-progress" in attrs for _, attrs in ancestors)]
        self.assertEqual(links, ["/?view=company-transcripts"])
        self.assertFalse(any(tag in {"form", "button", "input"} and any("data-equibles-progress" in a for _, a in ancestors)
                             for tag, _, ancestors in markup.elements))

    def test_equibles_complete_bounded_blocked_unknown_and_unavailable_are_explicit(self):
        for value, label in (
            (progress(outcome="complete"), "Backfill complete"),
            (progress(outcome="bounded"), "In progress"),
            (progress(outcome="blocked"), "Blocked"),
            (progress(outcome="arbitrary-private-outcome"), "Unconfirmed"),
            (progress(available=False), "Unavailable"),
            (None, "Unavailable"),
        ):
            with self.subTest(value=value):
                document = self.render(snapshot(equibles=value))
                panel = Markup(document).inside("data-equibles-progress")
                self.assertIn(label, panel)
                self.assertNotIn("arbitrary-private-outcome", document)
                if not value or not value["available"]:
                    self.assertNotIn("Stored calls318", panel)
                    self.assertIn("View stored transcripts", panel)
                    self.assertIn("Next scheduled fetch", panel)
                    if value:
                        self.assertIn("2026-09-07 20:10:00 EDT", panel)

    def test_escaping_and_private_reason_never_create_markup_or_leak_provider_context(self):
        hostile = '<img src=x onerror="alert(1)">'
        item = event(0)
        item.update(label=hostile, description=hostile, note=hostile, datasets=[hostile])
        value = progress(current_ticker=hostile, reason="/home/private/provider: " + hostile)
        document = self.render(snapshot([item], equibles=value))
        markup = Markup(document)
        self.assertFalse(any(tag == "img" for tag, _, _ in markup.elements))
        self.assertIn("&lt;img", document)
        self.assertNotIn("/home/private/provider", document)

    def test_unknown_counts_are_distinct_from_zero_and_empty_day_has_no_group(self):
        document = self.render(snapshot(equibles=progress(
            completed_tickers=None, transcripts=0, quota_used=None,
            quota_remaining=None, current_ticker=None, next_scheduled_at=None,
        )))
        panel = Markup(document).inside("data-equibles-progress")
        self.assertIn("Companies complete— / 25", panel)
        self.assertIn("Stored calls0", panel)
        self.assertIn("Recorded quota— used · — remaining", panel)
        self.assertIn("Next scheduled fetchUnavailable", panel)
        self.assertNotIn("data-fetch-news-group", document)
        self.assertIn("No scheduled batch starts", document)

    def test_equibles_calendar_uses_fixed_eq_key(self):
        value = snapshot(days=[{
            "date": "2026-09-07", "is_selected": True,
            "markers": [{"batch_id": "fixture-equibles.timer",
                         "label": "Equibles transcripts", "status": "upcoming",
                         "color": "amber", "run_count": 1, "counts": {"upcoming": 1}}],
        }])
        document = self.render(value)
        self.assertIn('<span class="fetch-marker-code" aria-hidden="true">EQ</span>', document)
        self.assertIn('<span class="fetch-code-key">EQ</span>Equibles transcripts', document)


if __name__ == "__main__":
    unittest.main()
