"""Reviewed, event-specific reporting boundaries for the derived research proxy.

The catalog is research evidence, never a replacement for canonical actuals.
No ticker-wide fallback is allowed: every decision names an exact source event.
"""
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

CONTRACT = 'forward_pe.reviewed_reporting_periods.v1'
CATALOG = Path(__file__).resolve().parents[2] / 'config' / 'forward_pe_reviewed_periods.json'


def stamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Review time requires a timezone')
    return value


def validate_catalog(catalog):
    if catalog.get('contract') != CONTRACT or not 1 <= len(catalog['rows']) <= 2500:
        raise ValueError('Invalid bounded review catalog')
    seen = set()
    for row in catalog['rows']:
        key = tuple(row[k] for k in ('instrument_id', 'symbol', 'natural_identity', 'source_event_date'))
        if key in seen or not all(isinstance(x, str) and x for x in key):
            raise ValueError('Duplicate or missing reviewed event identity')
        seen.add(key)
        for key in ('source_event_date', 'reported_period_end', 'announcement_date'):
            date.fromisoformat(row[key])
        reviewed = stamp(row['reviewed_at'])
        if not row['reported_period_end'] <= row['announcement_date'] <= reviewed.date().isoformat():
            raise ValueError('Review cannot assert future results')
        if row['confidence'] not in ('confirmed', 'most_likely'):
            raise ValueError('Review confidence must be explicit')
        if row['reporting_kind'] not in ('quarter', 'half_year', 'annual', 'preliminary_half_year', 'intended_quarter'):
            raise ValueError('Unknown reporting kind')
        if not row.get('source_urls') or any(not url.startswith('https://') for url in row['source_urls']):
            raise ValueError('Primary evidence links are required')
        if not row.get('finding') or not row.get('reviewed_version_id'):
            raise ValueError('Review provenance is required')
        if row.get('blocking_reason') not in (None, 'financial_results_not_released', 'fiscal_calendar_transition'):
            raise ValueError('Unknown review restriction')
    return catalog


def load_catalog():
    return validate_catalog(json.loads(CATALOG.read_text()))


def for_subject(catalog, subject, cutoff):
    return [row for row in catalog['rows'] if row['instrument_id'] == subject['instrument_id']
            and row['symbol'] == subject['symbol'] and stamp(row['reviewed_at']) <= stamp(cutoff)]


def event_reviews(inputs, earnings):
    """Only applicable evidence enters a calculation, including its period aliases."""
    rows = inputs.get('reviewed_periods', [])
    if not rows:
        return {}
    validate_catalog(dict(contract=CONTRACT, rows=rows))
    candidates = for_subject(dict(rows=rows), inputs, inputs['cutoff'])
    by_key = {(r['natural_identity'], r['source_event_date']): r for r in candidates}
    return {e['natural_identity']: by_key[(e['natural_identity'], e['source_event_date'])]
            for e in earnings if (e['natural_identity'], e['source_event_date']) in by_key}


def review_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
