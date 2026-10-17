from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import unittest

from quant_data.dashboard.fetch_status_page import render_fetch_status_content


def snapshot():
    return {
        "today": "2026-10-08", "selected_date": "2026-10-08",
        "month_start": "2026-10-01",
        "days": [{"date": f"2026-10-{day:02d}", "is_selected": day == 8, "is_today": day == 8,
                  "markers": []} for day in range(1, 32)],
        "summary": {"total": 7, "green": 4, "partial": 1, "pending": 1, "red": 1},
        "intraday_options": {
            "available": True, "checked_at": "2026-10-08T15:31:00Z", "operating_date": "2026-10-08",
            "session": {"state": "open"},
            "calendar_days": [{"date": f"2026-10-{day:02d}",
                               "state": {7: "partial", 8: "current", 9: "scheduled", 10: "closed"}.get(day, "unknown"),
                               "successful_requests": 100 if day == 7 else None,
                               "failed_requests": 2 if day == 7 else None,
                               "unresolved_requests": 1 if day == 7 else None,
                               "message": "Recorded request activity for this date."}
                              for day in range(1, 32)],
            "collection": {"state": "current", "last_observed_at": "2026-10-08T15:30:00.123456Z",
                           "fresh_symbols": 20, "symbols_configured": 20,
                           "message": "Saved observations meet the configured cadence."},
            "delivery": {"state": "pending", "pending_count": 2,
                         "message": "Saved observations are waiting for upload."},
            "cadence": [{"minutes": minutes, "state": "current", "symbols_observed": 5,
                         "last_observed_at": "2026-10-08T15:30:00Z"} for minutes in (5, 10, 15, 30)],
        },
    }


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.lineage = []
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.elements.append((tag, attrs))
        self.lineage.append((tag, attrs, tuple(self.stack)))
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class IntradayOptionsPresentationTests(unittest.TestCase):
    def test_current_collection_and_delivery_are_independent_of_daily_totals(self):
        source = snapshot()
        before = deepcopy(source)
        page = render_fetch_status_content(source)
        panel = page[page.index('<section class="fetch-intraday"'):page.index('data-equibles-progress')]
        self.assertIn('data-intraday-state="current"', panel)
        self.assertIn('data-intraday-state="pending"', panel)
        self.assertIn('data-intraday-pending>2</dd>', panel)
        self.assertNotIn('data-fetch-event=', panel)
        self.assertIn('<strong>7</strong> slots', page)
        self.assertIn('Intraday observations are separate from daily batch slots', panel)
        self.assertEqual(source, before)

    def test_historical_selection_uses_date_specific_requests_without_current_health(self):
        source = snapshot()
        source["selected_date"] = "2026-10-07"
        source["intraday_options"]["selected_session"] = {
            "date": "2026-10-07", "observations": 1234, "symbols": 20,
            "last_observed_at": "2026-10-07T19:55:00Z",
        }
        page = render_fetch_status_content(source)
        self.assertIn('Selected day · intraday activity', page)
        self.assertIn('<dd>1234</dd>', page)
        self.assertIn('Wednesday, October 07, 2026', page)
        self.assertIn('<dt>Successful requests</dt><dd>100</dd>', page)
        self.assertIn('<dt>Failed requests</dt><dd>2</dd>', page)
        self.assertIn('Partial collection', page)
        self.assertNotIn('Current operating state', page)
        self.assertNotIn('Fresh symbols / configured', page)
        self.assertNotIn('Dashboard delivery', page)
        self.assertNotIn('data-intraday-pending', page)
        self.assertNotIn('data-intraday-cadence=', page)
        self.assertNotIn('2026-10-08T15:30:00.123456Z', page)
        self.assertNotIn('data-intraday-history', render_fetch_status_content(snapshot()))

    def test_unknown_counts_are_not_presented_as_zero(self):
        for invalid in (None, -1, False, "0"):
            source = snapshot()
            source["intraday_options"]["delivery"]["pending_count"] = invalid
            self.assertIn('data-intraday-pending>Unknown</dd>', render_fetch_status_content(source))
        source["intraday_options"]["delivery"]["pending_count"] = 0
        source["intraday_options"]["delivery"]["state"] = "current"
        page = render_fetch_status_content(source)
        self.assertIn('data-intraday-pending>0</dd>', page)
        self.assertIn('Queue clear', page)
        self.assertIn('A clear queue does not confirm the remote dashboard is current.', page)

    def test_closed_paused_and_unknown_states_are_neutral_not_failed_or_healthy(self):
        for state, label in (("closed", "Market closed"), ("paused", "Paused"), ("unexpected", "Unknown")):
            source = snapshot()
            source["intraday_options"]["collection"]["state"] = state
            page = render_fetch_status_content(source)
            section = page.split('id="fetch-intraday-collection-heading"')[1].split('</section>')[0]
            self.assertIn('fetch-neutral', section)
            self.assertIn(label, section)
            self.assertNotIn('fetch-red', section)
            self.assertNotIn('fetch-green', section)

    def test_missing_snapshot_and_cadences_remain_explicit(self):
        source = snapshot()
        source["intraday_options"] = {"available": False, "operating_date": "2026-10-08"}
        page = render_fetch_status_content(source)
        markup = Markup(page)
        self.assertIn('data-intraday-unavailable', page)
        self.assertIn('data-intraday-pending>Unknown</dd>', page)
        cadence = [attrs['data-intraday-cadence'] for _, attrs in markup.elements if 'data-intraday-cadence' in attrs]
        self.assertEqual(cadence, ['5', '10', '15', '30'])
        source["intraday_options"]["reason"] = "Monitor read unavailable."
        self.assertIn('Monitor read unavailable.', render_fetch_status_content(source))

    def test_calendar_markers_link_each_date_to_its_intraday_panel(self):
        page = render_fetch_status_content(snapshot())
        markers = [attrs for tag, attrs in Markup(page).elements if 'data-fetch-intraday-marker' in attrs]
        self.assertEqual(len(markers), 31)
        for attrs in markers:
            self.assertEqual(attrs['href'], '/status?date=' + attrs['data-fetch-intraday-marker'] + '#fetch-intraday-heading')
            self.assertIn('Intraday Options', attrs['aria-label'])
        by_date = {attrs['data-fetch-intraday-marker']: attrs['data-intraday-state'] for attrs in markers}
        self.assertEqual({day: by_date['2026-10-' + day] for day in ('07', '08', '09', '10', '11')},
                         {'07': 'partial', '08': 'current', '09': 'scheduled', '10': 'closed', '11': 'unknown'})
        self.assertIn('IO</span>Intraday Options · continuous monitor', page)
        self.assertIn('Intraday pulls are not counted as daily batch slots.', page)

    def test_intraday_card_is_inside_selected_focus_before_daily_timeline(self):
        source = snapshot()
        source['focus_events'] = [{'id': 'fixture-daily', 'status': 'upcoming', 'label': 'Daily batch'}]
        page = render_fetch_status_content(source)
        panel, = [parents for _, attrs, parents in Markup(page).lineage if 'data-intraday-options' in attrs]
        self.assertTrue(any('data-fetch-focus' in attrs for _, attrs in panel))
        self.assertLess(page.index('data-intraday-options'), page.index('data-fetch-event="fixture-daily"'))
        self.assertEqual(page.count('data-intraday-options'), 1)
        self.assertIn('<strong>7</strong> slots · daily batches', page)

    def test_future_weekend_and_unknown_dates_do_not_show_current_state_or_mismatched_coverage(self):
        for selected, state in (('2026-10-09', 'scheduled'), ('2026-10-10', 'closed'), ('2026-10-11', 'unknown')):
            with self.subTest(selected=selected):
                source = snapshot()
                source['selected_date'] = selected
                source['intraday_options']['selected_session'] = {
                    'date': '2026-10-07', 'observations': 456789, 'symbols': 654321}
                page = render_fetch_status_content(source)
                panel = page.split('data-intraday-options')[1].split('<ol class="fetch-timeline">')[0]
                self.assertIn('data-intraday-state="' + state + '"', panel)
                self.assertIn('<dt>Successful requests</dt><dd>Unknown</dd>', panel)
                self.assertNotIn('Dashboard delivery', panel)
                self.assertNotIn('Current operating state', panel)
                self.assertNotIn('456789', panel)
                self.assertNotIn('654321', panel)

    def test_absent_monitor_snapshot_retains_unknown_calendar_and_selected_day(self):
        source = snapshot()
        del source['intraday_options']
        page = render_fetch_status_content(source)
        markers = [attrs for _, attrs in Markup(page).elements if 'data-fetch-intraday-marker' in attrs]
        self.assertTrue(all(attrs['data-intraday-state'] == 'unknown' for attrs in markers))
        self.assertIn('Intraday request evidence is unavailable for this date.', page)
        self.assertNotIn('data-intraday-pending', page)

    def test_closed_and_paused_show_observed_coverage_without_claiming_freshness(self):
        for state in ("closed", "paused", "current", "stale", "failed", "waiting", "unknown"):
            with self.subTest(state=state):
                source = snapshot()
                source["intraday_options"]["collection"].update(
                    state=state, symbols_observed=19, fresh_symbols=None)
                page = render_fetch_status_content(source)
                section = page.split('id="fetch-intraday-collection-heading"')[1].split('</section>')[0]
                if state in {"closed", "paused"}:
                    self.assertIn('<dt>Symbols observed / configured</dt><dd>19 <span>/ 20</span>', section)
                    self.assertNotIn('Fresh symbols', section)
                else:
                    self.assertIn('<dt>Fresh symbols / configured</dt><dd>Unknown <span>/ 20</span>', section)
                    self.assertNotIn('Symbols observed / configured', section)

    def test_timestamps_keep_original_precision_and_eastern_display(self):
        page = render_fetch_status_content(snapshot())
        self.assertIn('<time datetime="2026-10-08T15:30:00.123456Z">2026-10-08 11:30:00.123456 EDT</time>', page)
        self.assertIn('<time datetime="2026-10-08T15:31:00Z">2026-10-08 11:31:00 EDT</time>', page)

    def test_error_state_is_visible_and_detailed_copy_is_escaped_without_javascript(self):
        source = snapshot()
        source["intraday_options"]["collection"]["state"] = "failed"
        source["intraday_options"]["collection"]["message"] = '<script>alert("private")</script>'
        page = render_fetch_status_content(source)
        panel = page.split('data-intraday-options>')[1].split('data-intraday-details')[0]
        self.assertIn('Collection issue', panel)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', panel)
        self.assertFalse(any(tag in {"script", "form", "button"} for tag, _ in Markup(panel).elements))

    def test_current_and_historical_metrics_are_in_one_closed_native_disclosure(self):
        for selected in ('2026-10-08', '2026-10-07'):
            with self.subTest(selected=selected):
                source = snapshot()
                source['selected_date'] = selected
                page = render_fetch_status_content(source)
                markup = Markup(page)
                disclosures = [attrs for tag, attrs in markup.elements
                               if tag == 'details' and 'data-intraday-details' in attrs]
                self.assertEqual(len(disclosures), 1)
                self.assertNotIn('open', disclosures[0])
                self.assertIn('data-intraday-details><summary>Run details</summary>', page)
                for tag, attrs, parents in markup.lineage:
                    if not any('data-intraday-options' in parent for _, parent in parents):
                        continue
                    if tag in {'dl', 'time'} or 'data-intraday-cadence' in attrs:
                        self.assertTrue(any(name == 'details' and 'data-intraday-details' in parent
                                            for name, parent in parents))
                collapsed = page.split('data-intraday-options')[1].split('data-intraday-details')[0]
                self.assertIn('Collection', collapsed)
                if selected == source['today']:
                    self.assertIn('Dashboard delivery', collapsed)
                    self.assertIn('Uploads pending', collapsed)
                else:
                    self.assertIn('Partial collection', collapsed)
                    self.assertNotIn('Dashboard delivery', collapsed)

    def test_request_issues_remain_visible_when_market_is_closed_or_collection_paused(self):
        for state in ('closed', 'paused'):
            source = snapshot()
            source['intraday_options']['collection'].update(state=state, failed_requests=2, unresolved_requests=None)
            page = render_fetch_status_content(source)
            collapsed = page.split('data-intraday-options>')[1].split('data-intraday-details')[0]
            self.assertIn('Request issues recorded', collapsed)
            self.assertIn('fetch-intraday-attention', collapsed)
            self.assertIn('<dt>Failed requests</dt><dd>2</dd>', page)
            self.assertIn('<dt>Unresolved requests</dt><dd>Unknown</dd>', page)


if __name__ == "__main__":
    unittest.main()
