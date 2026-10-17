"""One read-only workspace connecting run history to current retained data."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..inspector_schedules import TIMER_BINDINGS
from .data_status_page import (
    _SUPPLEMENTAL_HEADERS, _display_status, _escape, _lifecycle,
    _metadata_for, _rows, _status_category, _status_row,
)
from .fetch_status_page import (
    _date_label, _instant, _records, _text, render_fetch_status_content,
)
from .inspector_shell import render_inspector_shell


def _data_counts(result, metadata):
    active = tuple(row for row in _rows(result) if _lifecycle(_metadata_for(row, metadata)) == "active")
    counts = Counter(_status_category(_display_status(row, _metadata_for(row, metadata))[1]) for row in active)
    return len(active), counts


def _overview(snapshot, result, metadata, error):
    events = _records(snapshot.get("focus_events"))
    outcomes = Counter(_text(event, "status") for event in events)
    active, counts = _data_counts(result, metadata) if error is None else (0, {})
    day = _date_label(_text(snapshot, "selected_date"))
    cards = (
        ("Completed runs", str(outcomes["succeeded"] + outcomes["warning"]), str(len(events)) + " slots · " + day,
         "#fetch-focus-heading", "", "green"),
        ("Runs with issues", str(outcomes["failed"] + outcomes["partial"] + outcomes["warning"]),
         str(outcomes["warning"]) + " input warnings · " + str(outcomes["partial"]) + " partial · " + str(outcomes["failed"]) + " failed · " + day,
         "#fetch-focus-heading", "", "amber"),
        ("Data retained", str(counts.get("retained", 0)) if error is None else "—",
         str(active) + " active datasets · now" if error is None else "Current data snapshot unavailable",
         "#status-datasets", ' data-status-category-link="retained"', "green"),
        ("Data needs attention", str(counts.get("attention", 0)) if error is None else "—",
         str(counts.get("unknown", 0)) + " unknown · current snapshot" if error is None else "Current data snapshot unavailable",
         "#status-datasets", ' data-status-category-link="attention"', "amber"),
    )
    return '<div class="status-overview" aria-label="Run and data overview">' + "".join(
        '<a class="status-metric status-metric-' + color + '" href="' + target + '"' + action + '>'
        '<span>' + label + '</span><strong>' + value + '</strong><small>' + _escape(note) + '</small></a>'
        for label, value, note, target, action, color in cards
    ) + '</div>'


def _dataset_section(result, metadata, error, observed_at, reload_url):
    intro = (
        '<section class="status-datasets" id="status-datasets" aria-labelledby="status-datasets-heading">'
        '<div class="status-section-heading"><div><p class="status-eyebrow">Current snapshot</p>'
        '<h2 id="status-datasets-heading" tabindex="-1">Stored data</h2>'
        '<p>What is available now, with capture history and source dates in each record.</p></div>'
        '<a class="status-text-link" href="/api/data-status">View JSON <span aria-hidden="true">↗</span></a></div>'
    )
    if error is not None:
        return intro + (
            '<div class="status-data-unavailable" role="alert"><h3>Stored data status is unavailable</h3>'
            '<p>The data snapshot could not be loaded. Run history above is still available.</p>'
            '<details><summary>Error details</summary><p>' + _escape(error) + '</p></details>'
            '<a href="' + _escape(reload_url + "#status-datasets") + '">Reload this page</a></div></section>'
        )
    rows = _rows(result)
    active = sum(_lifecycle(_metadata_for(row, metadata)) == "active" for row in rows)
    options = "".join('<option value="' + _escape(binding.unit) + '">' + _escape(binding.label) + '</option>' for binding in TIMER_BINDINGS)
    body = []
    for row in rows:
        identities = {value for value in (row.get("id"), row.get("dataset_id")) if isinstance(value, str)}
        batches = tuple(binding.unit for binding in TIMER_BINDINGS if identities.intersection(binding.datasets))
        body.append(_status_row(row, None, _metadata_for(row, metadata), integrated=True, batches=batches))
    supplemental = "".join(
        '<th scope="col"' + ('' if index == 1 else ' data-inspector-detail-only') + '>'
        + _escape(label) + '</th>' for index, label in enumerate(_SUPPLEMENTAL_HEADERS)
    )
    if not body:
        body = ['<tr><td colspan="19">No dataset records are available in this snapshot.</td></tr>']
    return intro + f"""
<div class="status-data-panel">
<div class="status-data-controls" data-status-data-controls hidden>
<label class="status-search">Find a dataset<input type="search" data-status-search placeholder="Search by dataset or source" autocomplete="off"></label>
<label>Collection<select data-status-lifecycle><option value="active">Active data · {active}</option><option value="other">Other data · {len(rows) - active}</option><option value="all">All datasets · {len(rows)}</option></select></label>
<label>Batch<select data-status-batch><option value="">All batches</option>{options}<option value="unmapped">No recurring batch</option></select></label>
<label>Data state<select data-status-data-state><option value="">All states</option><option value="attention">Needs attention</option><option value="retained">Data retained</option><option value="unknown">Unknown</option><option value="planned">Not yet live</option><option value="nonlive">Historical / fixtures</option></select></label>
</div>
<div class="status-data-context"><p data-status-match-count aria-live="polite">{len(rows)} datasets · all collections</p>
<button type="button" data-status-clear hidden>Clear filters</button><span>Snapshot · {_instant(observed_at)}</span></div>
<noscript><p class="inspector-footnote">All collections and supporting fields are shown. Use your browser’s Find command to locate a dataset.</p></noscript>
<div class="inspector-table-workspace" data-inspector-table-workspace>
<div class="table-scroll" tabindex="0" role="region" aria-label="Stored datasets and their current capture evidence">
<table class="status-dataset-table" data-inspector-table data-inspector-record-note="Current retained evidence · run outcomes remain in the activity timeline">
<caption>Stored data · current snapshot</caption><thead><tr>
<th scope="col">Data status</th><th scope="col">Dataset or source</th><th scope="col">Source date</th>
<th scope="col" data-inspector-detail-only>Latest successful fetch · Eastern (EST/EDT)</th>{supplemental}
</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>
<p class="status-no-matches" data-status-no-matches hidden>No datasets match these filters. Clear filters to see all active data.</p>
</div><p class="status-data-footnote">A source date describes the data’s reference period. A capture date records when it was stored. Historical, fixture and planned datasets are available under Collection.</p></section>
"""


def render_status_page(
    snapshot: Mapping[str, Any], result: Mapping[str, Any], *,
    registry_revision: str, metadata: Mapping[str, Mapping[str, Any]] | None = None,
    data_error: str | None = None,
    derived_status: Mapping[str, Any] | None = None,
) -> str:
    """Compose independent run and retained-data evidence without provider work."""
    selected = _text(snapshot, "selected_date")
    history_note = (
        'Run history is for ' + _date_label(selected) + '. Stored data and backfill progress are current snapshots.'
        if selected != _text(snapshot, "today") else
        'Run outcomes describe this day. Stored data and backfill progress show the latest available evidence.'
    )
    reload_url = "/status?date=" + selected if selected else "/status"
    body = (
        '<div class="status-workspace" data-status-workspace>'
        + _overview(snapshot, result, metadata, data_error)
        + '<nav class="status-section-nav" aria-label="Status sections">'
        '<a href="#status-runs">Run activity</a><a href="#status-datasets">Stored data</a>'
        '<a href="#status-derived">Derived calculations</a><a href="#fetch-equibles-heading">Backfill progress</a>'
        '<a class="status-refresh" href="' + _escape(reload_url) + '">Refresh snapshot ↻</a></nav>'
        '<p class="status-time-context">' + _escape(history_note) + '</p>'
        '<section id="status-runs" aria-label="Run activity">'
        + render_fetch_status_content(snapshot, linked_data=True) + '</section>'
        + _derived_section(derived_status or {})
        + _dataset_section(result, metadata, data_error, snapshot.get("observed_at"), reload_url)
        + '</div>'
    )
    return render_inspector_shell(
        title="Status", active="/status", revision=registry_revision, body=body,
        description="Run activity and the data available for research.",
        footer="Local · read-only · run history and current retained data · Eastern time (EST/EDT)",
    )


def _derived_section(status):
    raw_state = status.get("state", "not_started")
    state = {"complete": "Calculations complete", "complete_with_warnings": "Calculations complete · input warnings",
             "complete_with_gaps": "Published · review input and calculation status"}.get(
                 raw_state, str(raw_state).replace("_", " ").capitalize())
    schedule="23:30 weekdays · 06:30 daily · Toronto" if status.get("schedule",{}).get("enabled") else "Not activated"
    counts=status.get("outcomes",{})
    coverage=(f'{status.get("numeric_pe",0):,} ratios / {status.get("daily_rows",0):,} saved sessions'
              if "daily_rows" in status else "Awaiting first refresh")
    waiting = counts.get("waiting_inputs", 0)
    catchup = counts.get("catchup_pending", 0)
    freshness = status.get("input_freshness", {})
    warnings = freshness.get("freshness_warnings", counts.get("stale_inputs", 0))
    if raw_state in {"complete", "complete_with_warnings", "complete_with_gaps"} and freshness and not waiting and not catchup:
        state = "Calculations complete · input warnings" if warnings else "Calculations complete"
    notes = []
    if freshness:
        notes.append(f'Current: {freshness.get("current_inputs", 0):,} · Stale: {freshness.get("stale_inputs", 0):,} · '
                     f'Freshness unconfirmed: {freshness.get("unconfirmed_inputs", warnings):,}.')
        if freshness.get("input_warnings"):
            notes.append(f'{freshness["input_warnings"]:,} tickers have other input warnings.')
        notes.append("Current includes older captures confirmed unchanged. Stale requires evidence of a newer changed input.")
        if freshness.get("unchanged_checks"):
            notes.append(f'{freshness["unchanged_checks"]:,} tickers were successfully checked with unchanged source data.')
    elif warnings:
        notes.append(f'Freshness unconfirmed: {warnings:,} tickers were flagged for older stored input captures; capture age alone does not establish stale information.')
    if waiting:
        notes.append(f'{waiting:,} tickers are waiting for readable inputs.')
    if catchup:
        notes.append(f'{catchup:,} tickers have calculation catch-up pending.')
    notes.append("Input freshness warnings do not count unavailable P/E values.")
    if status.get("issue"):
        notes.append(str(status["issue"]))
    note = " ".join(notes) if status else "No derived refresh receipt is available yet."
    return (
        '<section class="status-datasets" id="status-derived" aria-labelledby="status-derived-heading">'
        '<div class="status-section-heading"><div><p class="status-eyebrow">Daily research</p>'
        '<h2 id="status-derived-heading">Derived calculations</h2>'
        '<p>Forward EPS, fiscal-quarter matching and daily P/E.</p></div>'
        '<a class="status-text-link" href="/forward-pe">Open forward P/E →</a></div>'
        '<div class="status-data-panel"><p><strong>'+_escape(state)+'</strong> · '+_escape(schedule)+'</p>'
        '<p>Latest completed market session: '+_escape(status.get("target_session") or "Not recorded")
        +' · Last published: '+_escape(status.get("last_successful_publication") or "Not recorded")+'</p>'
        '<p>'+_escape(coverage)+'</p><p>'+_escape(note)+'</p></div></section>'
    )
