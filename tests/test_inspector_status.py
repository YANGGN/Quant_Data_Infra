from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from quant_data.inspector_status import read_inspector_status
from quant_data.dashboard.data_status_page import render_data_status_page
from quant_data.stores import StoreMap


class InspectorStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temp.name)
        self.macro = self.root / "macro.sqlite"
        self.stores = StoreMap.four_explicit(**{role: self.root / (role + ".sqlite") for role in ("market", "macro", "company", "news")})
        with sqlite3.connect(self.macro) as c:
            c.executescript("""
                CREATE TABLE store_metadata(singleton,store_role);
                INSERT INTO store_metadata VALUES(1,'macro');
                CREATE TABLE ingestion_runs(run_id,status);
                INSERT INTO ingestion_runs VALUES('ok','succeeded'),('bad','failed');
                CREATE TABLE soma_snapshots(as_of_date,run_id,completeness);
                INSERT INTO soma_snapshots VALUES('2026-09-02','ok','complete'),('2026-09-09','bad','complete');
                CREATE TABLE stage11_eia_weekly_observations(current_version_id);
                CREATE TABLE stage11_eia_weekly_observation_versions(version_id,canonical_series_id,period);
                INSERT INTO stage11_eia_weekly_observations VALUES('oil');
                INSERT INTO stage11_eia_weekly_observation_versions VALUES('oil','macro.eia.weekly.petroleum_stock','2026-08-28'),('orphan','macro.eia.weekly.petroleum_stock','2026-09-04');
                CREATE TABLE stage11_bea_nipa_observations(current_version_id);
                CREATE TABLE stage11_bea_nipa_observation_versions(version_id,canonical_series_id,period);
                INSERT INTO stage11_bea_nipa_observations VALUES('gdp');
                INSERT INTO stage11_bea_nipa_observation_versions VALUES('gdp','macro.gdp.real_qoq_saar_pct','2026Q2');
            """)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, day=5):
        return read_inspector_status(self.stores, operations_root=self.root / "missing-receipts",
            observed_at=datetime(2026, 9, day, tzinfo=timezone.utc))

    def test_dates_use_successful_current_source_facts_and_do_not_write(self):
        before = hashlib.sha256(self.macro.read_bytes()).hexdigest()
        files = sorted(p.name for p in self.root.iterdir())
        metadata = self.read()
        self.assertEqual(metadata['fixture.macro.soma_summary']['as_of_date'], '2026-09-02')
        self.assertEqual(metadata['macro.eia.petroleum_weekly_stock_history']['as_of_date'], '2026-08-28')
        self.assertEqual(metadata['macro.bea.nipa_history']['as_of_date'], '2026Q2')
        self.assertIsNone(metadata['fixture.macro.soma_summary']['latest_successful_fetch_at'])
        self.assertNotIn('as_of_date', metadata['fixture.macro.economic_calendar'])
        self.assertEqual(before, hashlib.sha256(self.macro.read_bytes()).hexdigest())
        self.assertEqual(files, sorted(p.name for p in self.root.iterdir()))

    def test_busy_store_never_returns_partial_source_dates(self):
        Path(str(self.macro) + '-wal').write_bytes(b'busy')
        metadata = self.read()
        self.assertIsNone(metadata['fixture.macro.soma_summary']['as_of_date'])
        self.assertEqual(metadata['macro.fmp.economic_calendar_evidence']['lifecycle'], 'legacy')
        self.assertEqual(metadata['macro.bea.nipa_history']['lifecycle'], 'historical')

    def test_weekly_age_does_not_mask_overdue_source(self):
        self.assertFalse(self.read()['fixture.macro.soma_summary']['source_overdue'])
        self.assertTrue(self.read(17)['fixture.macro.soma_summary']['source_overdue'])

    def test_failed_processing_preserves_fetch_and_does_not_mislabel_raw(self):
        def receipt(root, source):
            return {'last_attempt': {'outcome': 'failed' if source == 'fmp_macro_calendar' else 'published', 'completed_at': '2026-09-05T13:00:00Z'}, 'last_successful_fetch_at': '2026-09-05T12:59:00Z'}
        with patch('quant_data.inspector_status.read_refresh_status', side_effect=receipt):
            metadata = self.read()
        self.assertEqual(metadata['fixture.macro.economic_calendar']['refresh_state'], 'failed')
        self.assertEqual(metadata['macro.fmp.economic_calendar_incremental_evidence']['refresh_state'], 'published')
        self.assertEqual(metadata['fixture.macro.economic_calendar']['latest_successful_fetch_at'], '2026-09-05T12:59:00Z')

    def test_render_separates_dates_and_preserves_retained_evidence(self):
        metadata = self.read()
        ids = ['fixture.macro.soma_summary', 'macro.bea.nipa_history', 'macro.fmp.economic_calendar_evidence']
        result = {'records': [{'fields': [{'name': 'id', 'value': id}, {'name': 'status', 'value': 'current'}, {'name': 'latest_successful_capture', 'value': {'captured_at': '2026-09-03T22:30:00Z'}}]} for id in ids]}
        page = render_data_status_page(result, registry_revision='fixture', metadata=metadata)
        for text in ('2026-09-02', '2026Q2', 'Weekly source updates', 'Historical snapshot', 'Legacy', 'Not recorded', '2026-09-03 18:30:00 EDT', 'data-inspector-detail-only', 'Live data overview', 'Fixtures, legacy &amp; planned overview'):
            self.assertIn(text, page)
        hostile = '<img src=x onerror=bad>'
        metadata[ids[0]]['as_of_note'] = hostile
        page = render_data_status_page(result, registry_revision='fixture', metadata=metadata)
        self.assertNotIn(hostile, page)
        self.assertIn('&lt;img', page)


class StatusMarkup(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.summaries = {}
        self.group_summaries = {}
        self.group = None
        self.section_stack = []
        self.row = None
        self.field = None
        self.summary = None
        self.strong = False
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'section':
            self.section_stack.append(self.group)
            self.group = attrs.get('data-status-group', self.group)
        if tag == 'tr' and 'data-status-category' in attrs:
            self.row = {'category': attrs['data-status-category'], 'group': self.group, 'cells': {}}
            self.rows.append(self.row)
        if tag in ('td', 'th') and self.row is not None:
            self.field = attrs.get('data-field')
            if self.field:
                self.row['cells'][self.field] = ''
        if tag == 'article':
            self.summary = attrs.get('data-status-summary')
        if tag == 'strong':
            self.strong = True

    def handle_data(self, data):
        if self.row is not None and self.field:
            self.row['cells'][self.field] += data
        if self.summary and self.strong:
            self.summaries[self.summary] = int(data)
            self.group_summaries.setdefault(self.group, {})[self.summary] = int(data)

    def handle_endtag(self, tag):
        if tag == 'section':
            self.group = self.section_stack.pop()
        if tag in ('td', 'th'):
            self.field = None
        if tag == 'tr':
            self.row = None
        if tag == 'strong':
            self.strong = False
        if tag == 'article':
            self.summary = None


def status_result(ids, status='no_data'):
    return {'records': [{'fields': [
        {'name': 'id', 'value': id}, {'name': 'status', 'value': status},
        {'name': 'latest_successful_capture', 'value': {'captured_at': '2026-09-03T22:30:00Z'}},
        {'name': 'latest_retained_outcome', 'value': {'status': 'failed'}},
    ]} for id in ids]}


class InspectorStatusClassificationTests(unittest.TestCase):
    def test_summary_matches_lifecycle_and_evidence_without_fetch_implying_capture(self):
        retained = {'retention_state': 'retained', 'retention_freshness': 'current'}
        recent_fetch = {'latest_successful_fetch_at': '2026-09-05T12:00:00Z', 'refresh_state': 'unchanged'}
        metadata = {
            'shared': {**retained, 'retention_basis': 'shared', 'retained_capture_at': '2026-09-04T12:00:00Z'},
            'native': {**retained, 'retention_basis': 'native'},
            'failed': {**retained, **recent_fetch, 'refresh_state': 'failed'},
            'stale': {**retained, **recent_fetch, 'retention_freshness': 'stale'},
            'missing': {**recent_fetch, 'retention_state': 'missing'},
            'unknown': {**recent_fetch, 'retention_state': 'unknown'},
            'planned': {'lifecycle': 'planned'},
            'fixture': {'lifecycle': 'fixture'},
            'historical': {'lifecycle': 'historical'},
            'legacy': {'lifecycle': 'legacy', 'retained_outcome': 'response_rejected'},
        }
        page = StatusMarkup(render_data_status_page(status_result(metadata), registry_revision='fixture', metadata=metadata))
        expected = {'retained': 2, 'attention': 3, 'unknown': 1, 'planned': 1, 'nonlive': 3}
        self.assertEqual(page.summaries, expected)
        self.assertEqual(dict(Counter(row['category'] for row in page.rows)), expected)
        self.assertEqual(sum(page.summaries.values()), len(page.rows))
        rows = {row['cells']['dataset_id']: row['cells'] for row in page.rows}
        for id, label in {'shared': 'Data retained', 'native': 'Data retained', 'failed': 'Refresh failed',
                          'stale': 'Capture old', 'missing': 'Capture not found', 'unknown': 'Status unavailable',
                          'planned': 'Not yet live', 'fixture': 'Fixture only',
                          'historical': 'Historical snapshot', 'legacy': 'Legacy'}.items():
            self.assertTrue(rows[id]['display_status'].startswith(label), id)
        self.assertEqual(rows['shared']['latest_retained_outcome'], 'failed')
        self.assertIn('Shared ingestion output', rows['shared']['retention_basis'])
        self.assertIn('Native capture ledger', rows['native']['retention_basis'])
        self.assertIn('Not recorded', rows['shared']['latest_successful_fetch_at'])
        self.assertIn('2026-09-04 08:00:00 EDT', rows['shared']['last_successful_capture'])
        self.assertIn('2026-09-05 08:00:00 EDT', rows['missing']['latest_successful_fetch_at'])
        self.assertTrue(all(len(row['cells']) == 21 for row in page.rows))
        groups = {group: {row['cells']['dataset_id'] for row in page.rows if row['group'] == group}
                  for group in ('live', 'other')}
        self.assertEqual(groups['live'], {'shared', 'native', 'failed', 'stale', 'missing', 'unknown'})
        self.assertEqual(groups['other'], {'planned', 'fixture', 'historical', 'legacy'})
        self.assertFalse(groups['live'] & groups['other'])
        self.assertEqual(groups['live'] | groups['other'], set(metadata))
        self.assertEqual(len(page.rows), len(metadata))
        self.assertEqual(page.group_summaries['live'], {'retained': 2, 'attention': 3, 'unknown': 1})
        self.assertEqual(page.group_summaries['other'], {'nonlive': 3, 'planned': 1})

    def test_unverified_capture_is_not_restored_from_public_fallback_and_metadata_is_escaped(self):
        hostile = '<img src=x onerror=bad>'
        metadata = {'busy': {'retention_state': 'unknown', 'retained_capture_at': None,
                             'retention_note': hostile, 'capture_anchor_id': hostile,
                             'successor_id': hostile, 'retained_outcome': None}}
        source = render_data_status_page(status_result(['busy'], 'current'), registry_revision='fixture', metadata=metadata)
        page = StatusMarkup(source)
        cells = page.rows[0]['cells']
        self.assertEqual(page.summaries['unknown'], 1)
        self.assertEqual(cells['last_successful_capture'], '—')
        self.assertEqual(cells['latest_retained_outcome'], '—')
        self.assertEqual(cells['public_last_successful_capture'], '2026-09-03 18:30:00 EDT')
        self.assertEqual(cells['public_latest_retained_outcome'], 'failed')
        self.assertEqual(cells['capture_anchor_id'], hostile)
        self.assertNotIn(hostile, source)
        self.assertIn('&lt;img', source)

    def test_public_native_source_status_remains_usable_without_generic_overlay(self):
        page = StatusMarkup(render_data_status_page(status_result(['news-source'], 'current'), registry_revision='fixture'))
        self.assertEqual(page.summaries['retained'], 1)
        self.assertEqual(page.rows[0]['cells']['last_successful_capture'], '2026-09-03 18:30:00 EDT')


if __name__ == '__main__':
    unittest.main()
