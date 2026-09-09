"""Read-only calendar and recorded batch execution presentation for the Inspector."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlencode

from .data_status_page import _instant_markup
from .inspector_shell import render_inspector_shell


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _text(record: Mapping[str, Any], key: str, fallback: str = "") -> str:
    value = record.get(key)
    return value if isinstance(value, str) else fallback


def _items(value: object) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _records(value: object) -> tuple[Mapping[str, Any], ...]:
    return tuple(item for item in _items(value) if isinstance(item, Mapping))


def _count(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "—"


def _day_url(value: str) -> str:
    return "/status?" + urlencode({"date": value}) if value else "/status"


def _date_label(value: str, *, full: bool = False) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value or "Date unavailable"
    return parsed.strftime("%A, %B %d, %Y" if full else "%b %d, %Y")


def _instant(value: object) -> str:
    return _instant_markup(value) if isinstance(value, str) and value else "Not recorded"


def _state(event: Mapping[str, Any]) -> tuple[str, str, str]:
    status = _text(event, "status")
    labels = {"succeeded": "Completed", "partial": "Partial Success", "failed": "Failed", "running": "Running", "upcoming": "Scheduled", "unconfirmed": "Unconfirmed"}
    if status not in labels:
        return "unconfirmed", "Unconfirmed", "amber"
    color = _text(event, "color")
    return status, _text(event, "status_label", labels[status]), color if color in {"green", "amber", "red"} else "amber"


def _badge(event: Mapping[str, Any]) -> str:
    status, label, color = _state(event)
    icon = "◐" if status == "partial" else {"green": "✓", "red": "!", "amber": "◷"}[color]
    return (
        '<span class="fetch-badge fetch-' + color + '" data-fetch-status="' + status + '">'
        + '<span aria-hidden="true">' + icon + '</span> ' + _escape(label) + '</span>'
    )


def _slot_time(event: Mapping[str, Any]) -> str:
    label = _text(event, "scheduled_local", "Time unavailable")
    instant = _text(event, "scheduled_at")
    return '<time datetime="' + _escape(instant) + '">' + _escape(label) + '</time>' if instant else _escape(label)


_BATCH_CODES = {
    "Market close": "MC",
    "ETF options": "OP",
    "Macro current": "MA",
    "GDP / CPI": "GI",
    "Employment": "EM",
    "Economic calendar": "EC",
    "SEC fundamentals": "SF",
    "Company market": "CO",
    "Current news": "NW",
    "Equibles transcripts": "EQ",
}


def _batch_codes(days: tuple[Mapping[str, Any], ...]) -> dict[str, tuple[str, str]]:
    labels = {
        _text(marker, "batch_id"): _text(marker, "label", "Unnamed batch")
        for day in days for marker in _records(day.get("markers"))
    }
    result = {}
    unexpected = 0
    for identity, label in sorted(labels.items(), key=lambda item: (list(_BATCH_CODES).index(item[1]) if item[1] in _BATCH_CODES else len(_BATCH_CODES), item[0])):
        code = _BATCH_CODES.get(label)
        if code is None:
            unexpected += 1
            code = "X" + str(unexpected)
        result[identity] = (code, label)
    return result


def _month_label(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%B %Y")
    except ValueError:
        return value or "Month unavailable"


def _month_link(value: object, *, direction: str) -> str:
    label = "Previous month" if direction == "prev" else "Next month"
    icon = "←" if direction == "prev" else "→"
    if not isinstance(value, str) or not value:
        return '<span class="fetch-nav-disabled" aria-disabled="true" aria-label="' + label + ' unavailable">' + icon + '</span>'
    return '<a href="' + _escape(_day_url(value)) + '" rel="' + direction + '" aria-label="' + label + '">' + icon + '</a>'


def _calendar_marker(marker: Mapping[str, Any], day_date: str, codes: Mapping[str, tuple[str, str]]) -> str:
    status, status_label, color = _state(marker)
    identity = _text(marker, "batch_id")
    code, label = codes.get(identity, ("X", _text(marker, "label", "Unnamed batch")))
    counts = marker.get("counts") if isinstance(marker.get("counts"), Mapping) else {}
    count_description = ", ".join(
        _count(counts.get(key)) + " " + label
        for key, label in (("succeeded", "completed"), ("partial", "partial success"), ("failed", "failed"), ("running", "running"), ("upcoming", "scheduled"), ("unconfirmed", "unconfirmed"))
    )
    description = label + " · " + status_label + " · " + _count(marker.get("run_count")) + " slots; " + count_description
    style = "failed" if status == "failed" else color
    icon = "!" if status == "failed" else "✓" if status == "succeeded" else "◐" if status == "partial" else "◷"
    run_count = marker.get("run_count")
    count_markup = '<span class="fetch-marker-count">×' + _count(run_count) + '</span>' if isinstance(run_count, int) and not isinstance(run_count, bool) and run_count > 1 else ""
    return (
        '<a class="fetch-marker fetch-marker-' + style + '" data-fetch-marker="' + _escape(identity)
        + '" data-fetch-status="' + status + '" href="' + _escape(_day_url(day_date)) + '#fetch-focus-heading"'
        + ' title="' + _escape(description) + '" aria-label="' + _escape(description + ". View this day’s exact slots.") + '">'
        + '<span class="fetch-marker-icon" aria-hidden="true">' + icon + '</span>'
        + '<span class="fetch-marker-code" aria-hidden="true">' + _escape(code) + '</span>'
        + '<span class="fetch-marker-label" aria-hidden="true">' + _escape(label) + '</span>' + count_markup + '</a>'
    )


def _calendar_day(day: Mapping[str, Any], codes: Mapping[str, tuple[str, str]]) -> str:
    day_date = _text(day, "date")
    try:
        number = str(date.fromisoformat(day_date).day)
    except ValueError:
        number = _text(day, "label", "?")
    selected = day.get("is_selected") is True
    is_today = day.get("is_today") is True
    classes = "fetch-month-day" + (" is-selected" if selected else "") + (" is-today" if is_today else "")
    current = ' aria-current="date"' if selected else ""
    today = '<span class="fetch-month-today">Today</span>' if is_today else ""
    markers = "".join(_calendar_marker(marker, day_date, codes) for marker in _records(day.get("markers")))
    empty = '<span class="fetch-month-empty" aria-label="No scheduled batches">—</span>' if not markers else ""
    return (
        '<article class="' + classes + '" data-fetch-day="' + _escape(day_date) + '">'
        + '<a class="fetch-day-link" href="' + _escape(_day_url(day_date)) + '"' + current
        + ' aria-label="' + _escape(_date_label(day_date, full=True) + (". Today." if is_today else "") + " View daily focus.") + '">'
        + '<time datetime="' + _escape(day_date) + '">' + _escape(number) + '</time>' + today + '</a>'
        + '<div class="fetch-month-markers">' + markers + empty + '</div></article>'
    )


def _month_grid(days: tuple[Mapping[str, Any], ...], month_start: str, codes: Mapping[str, tuple[str, str]]) -> str:
    if not days:
        return '<p class="fetch-empty">Calendar slots are unavailable.</p>'
    try:
        offset = date.fromisoformat(month_start).weekday()
    except ValueError:
        offset = 0
    blank = '<span class="fetch-month-blank" data-fetch-blank aria-hidden="true"></span>'
    trailing = (-(offset + len(days))) % 7
    return blank * offset + "".join(_calendar_day(day, codes) for day in days) + blank * trailing


def _run_counts(event: Mapping[str, Any]) -> str:
    counts = event.get("counts")
    units = {"sources": "Sources", "issuers": "Issuers", "etfs": "ETFs", "symbols": "Symbols", "steps": "Source steps"}
    if not isinstance(counts, Mapping) or counts.get("unit") not in units:
        return '<p class="fetch-muted" data-fetch-counts-unavailable>Successful and failed counts were not recorded for this run.</p>'
    fields = (("successful", "Successful", "green"), ("failed", "Failed", "red"),
              ("partial", "Incomplete", "amber"), ("skipped", "Skipped / no coverage", "muted"),
              ("unattempted", "Not attempted", "muted"))
    metrics = "".join(
        '<div class="fetch-work-count fetch-count-' + color + '"><dt>' + label + '</dt><dd>'
        + _count(counts.get(key)) + '</dd></div>'
        for key, label, color in fields if key in {"successful", "failed"} or counts.get(key)
    )
    return ('<section class="fetch-work-summary" data-fetch-work-counts aria-label="Recorded work outcomes">'
            '<h4>' + units[counts["unit"]] + ' · recorded run</h4><dl class="fetch-work-counts">'
            + metrics + '</dl><p class="fetch-muted">Successful work includes unchanged data.</p></section>')


def _focus_event(event: Mapping[str, Any]) -> str:
    status, _, color = _state(event)
    datasets = tuple(value for value in _items(event.get("datasets")) if isinstance(value, str))
    dataset_markup = (
        '<ul class="fetch-datasets">' + "".join('<li><code>' + _escape(value) + '</code></li>' for value in datasets) + '</ul>'
        if datasets else '<p class="fetch-muted">No datasets are listed for this batch.</p>'
    )
    return (
        '<li class="fetch-focus-event" data-fetch-event="' + _escape(_text(event, "id")) + '">'
        + '<div class="fetch-focus-time"><span class="fetch-timeline-dot fetch-' + color + '" aria-hidden="true"></span>'
        + _slot_time(event) + '</div><article class="fetch-focus-card"><div class="fetch-focus-title"><h3>'
        + _escape(_text(event, "label", "Unnamed batch")) + '</h3>' + _badge(event) + '</div><p class="fetch-description">'
        + _escape(_text(event, "description")) + '</p><details data-fetch-event-details><summary>Run details'
        + (' · ' + str(len(datasets)) + ' datasets' if datasets else '') + '</summary><div class="fetch-run-details"><p>'
        + _escape(_text(event, "note", "No execution note is available."))
        + '</p>' + _run_counts(event) + '<dl><dt>Scheduled batch start</dt><dd>' + _instant(event.get("scheduled_at"))
        + '</dd><dt>Recorded start</dt><dd>' + _instant(event.get("started_at"))
        + '</dd><dt>Recorded finish</dt><dd>' + _instant(event.get("finished_at"))
        + '</dd></dl><h4>Datasets in this batch</h4>' + dataset_markup + '</div></details></article></li>'
    )


_NEWS_BATCH = "quant-data-current-news-refresh.timer"


def _news_group(events: tuple[Mapping[str, Any], ...]) -> str:
    counts = {key: 0 for key in ("succeeded", "partial", "failed", "running", "upcoming", "unconfirmed")}
    for event in events:
        counts[_state(event)[0]] += 1
    if counts["failed"]:
        status, label, color = "failed", "Recorded failure", "red"
    elif counts["partial"]:
        status, label, color = "partial", "Partial Success", "amber"
    elif counts["succeeded"] == len(events):
        status, label, color = "succeeded", "All completed", "green"
    else:
        status, label, color = "unconfirmed", "Pending / unconfirmed", "amber"
    count_markup = "".join(
        '<span class="fetch-count-' + shade + '"><strong>' + str(counts[key]) + '</strong> ' + name + '</span>'
        for key, name, shade in (
            ("failed", "failed", "red"), ("partial", "partial success", "amber"), ("succeeded", "completed", "green"),
            ("running", "running", "amber"), ("upcoming", "scheduled", "amber"),
            ("unconfirmed", "unconfirmed", "amber"),
        ) if counts[key]
    )
    return (
        '<li class="fetch-focus-event" data-fetch-group="current-news">'
        + '<div class="fetch-focus-time"><span class="fetch-timeline-dot fetch-' + color + '" aria-hidden="true"></span>Hourly</div>'
        + '<details class="fetch-focus-card fetch-news-group" data-fetch-news-group><summary>'
        + '<span class="fetch-group-summary"><span class="fetch-focus-title"><span class="fetch-group-title">Current news</span>'
        + _badge({"status": status, "status_label": label, "color": color}) + '</span>'
        + '<span class="fetch-description">' + str(len(events)) + ' hourly slots · expand for each run</span>'
        + '<span class="fetch-summary" aria-label="Hourly news outcomes">' + count_markup + '</span></span></summary>'
        + '<ol class="fetch-timeline fetch-group-runs" aria-label="Hourly news runs">'
        + "".join(_focus_event(event) for event in events) + '</ol></details></li>'
    )


def _focus_timeline(events: tuple[Mapping[str, Any], ...]) -> str:
    news = tuple(event for event in events if _text(event, "batch_id") == _NEWS_BATCH)
    rendered = []
    grouped = False
    for event in events:
        if _text(event, "batch_id") == _NEWS_BATCH:
            if not grouped:
                rendered.append(_news_group(news))
                grouped = True
        else:
            rendered.append(_focus_event(event))
    return "".join(rendered) or '<li class="fetch-empty">No scheduled batch starts are listed for this day.</li>'


def _equibles_progress(value: object) -> str:
    progress = value if isinstance(value, Mapping) else {}
    available = progress.get("available") is True
    outcome = _text(progress, "outcome") if available else "unavailable"
    states = {
        "quota_deferred": ("Quota paused", "amber", "Backfill is paused at the recorded quota limit."),
        "complete": ("Backfill complete", "green", "All companies in the recorded backfill are complete."),
        "bounded": ("In progress", "amber", "The bounded run finished; backfill remains in progress."),
        "blocked": ("Blocked", "red", "Backfill is blocked. Stored transcripts remain available to inspect."),
        "unavailable": ("Unavailable", "amber", "Backfill progress is unavailable."),
    }
    label, color, message = states.get(outcome, ("Unconfirmed", "amber", "The latest backfill outcome is unconfirmed."))
    # Only fixed presentation text describes outcomes. Private provider errors or
    # filesystem context in a reason must never be interpolated into this page.
    badge = '<span class="fetch-badge fetch-' + color + '">' + _escape(label) + '</span>'
    next_fetch = _instant(progress.get("next_scheduled_at")) if progress.get("next_scheduled_at") else "Unavailable"
    metrics = ""
    if available:
        ticker = _text(progress, "current_ticker")
        metrics = (
            '<dl class="fetch-backfill-metrics"><div><dt>Companies complete</dt><dd><strong>'
            + _count(progress.get("completed_tickers")) + '</strong> / ' + _count(progress.get("universe"))
            + '</dd></div><div><dt>Stored calls</dt><dd><strong>' + _count(progress.get("transcripts"))
            + '</strong></dd></div><div><dt>Current ticker</dt><dd>' + (_escape(ticker) if ticker else "None recorded")
            + '</dd></div><div><dt>Requests · recorded run</dt><dd>' + _count(progress.get("requests_this_run"))
            + '</dd></div><div><dt>Recorded quota</dt><dd>' + _count(progress.get("quota_used"))
            + ' used · ' + _count(progress.get("quota_remaining")) + ' remaining</dd></div>'
            + '<div><dt>Recorded quota reset</dt><dd>' + _instant(progress.get("quota_reset_at"))
            + '</dd></div><div class="fetch-backfill-next"><dt>Next scheduled fetch</dt><dd>' + next_fetch
            + '</dd></div></dl>'
        )
    else:
        metrics = ('<dl class="fetch-backfill-metrics"><div class="fetch-backfill-next">'
                   '<dt>Next scheduled fetch</dt><dd>' + next_fetch + '</dd></div></dl>')
    return (
        '<section class="fetch-backfill" data-equibles-progress aria-labelledby="fetch-equibles-heading">'
        + '<div class="fetch-section-header"><div><p class="fetch-kicker">Current backfill · latest recorded progress</p>'
        + '<h2 id="fetch-equibles-heading">Equibles transcripts</h2></div>' + badge + '</div>'
        + '<p class="fetch-description">' + message + '</p>' + metrics
        + '<div class="fetch-backfill-footer"><p>Last record · ' + _instant(progress.get("recorded_at"))
        + '<span>Current progress is independent of the selected calendar day. Quota is recorded, not live account usage.</span></p>'
        + '<a href="/?view=company-transcripts">View stored transcripts <span aria-hidden="true">→</span></a></div></section>'
    )


def render_fetch_status_page(snapshot: Mapping[str, Any], *, registry_revision: str) -> str:
    """Render supplied schedules and outcomes without executing or inferring jobs."""
    selected = _text(snapshot, "selected_date")
    today = _text(snapshot, "today")
    days = _records(snapshot.get("days"))
    focus = _records(snapshot.get("focus_events"))
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), Mapping) else {}
    focus_title = "Today's focus" if selected and selected == today else "Daily focus"
    month_start = _text(snapshot, "month_start")
    month = _escape(_month_label(month_start))
    codes = _batch_codes(days)
    calendar = _month_grid(days, month_start, codes)
    batch_key = "".join(
        '<li><span class="fetch-code-key">' + _escape(code) + '</span>' + _escape(label) + '</li>'
        for code, label in codes.values()
    )
    timeline = _focus_timeline(focus)
    notices = "".join('<li>' + _escape(note) + '</li>' for note in _items(snapshot.get("notices")) if isinstance(note, str))
    unavailable = "".join(
        '<li><strong>' + _escape(_text(job, "label", "Unnamed schedule")) + '</strong><span>'
        + _escape(_text(job, "reason", "Schedule information is unavailable.")) + '</span></li>'
        for job in _records(snapshot.get("unavailable_jobs"))
    )
    unavailable_markup = (
        '<section class="fetch-unavailable" aria-labelledby="fetch-unavailable-heading"><h2 id="fetch-unavailable-heading">Schedules not shown</h2><ul>'
        + unavailable + '</ul></section>' if unavailable else ""
    )
    body = f"""
<div class="fetch-page" data-fetch-status-page>
<section class="fetch-calendar" aria-labelledby="fetch-month-heading">
<div class="fetch-section-header"><div><p class="fetch-kicker">Monthly calendar · Eastern time</p><h2 id="fetch-month-heading">{month}</h2></div>
<nav class="fetch-week-navigation" aria-label="Calendar month">
{_month_link(snapshot.get('previous_month_date'), direction='prev')}
<a href="/status">Today</a>
{_month_link(snapshot.get('next_month_date'), direction='next')}
</nav></div>
<div class="fetch-month-legend" data-fetch-legend aria-label="Batch marker status legend">
<span><i class="fetch-legend-completed" aria-hidden="true">✓</i> All completed</span>
<span><i class="fetch-legend-partial" aria-hidden="true">◐</i> Partial Success</span>
<span><i class="fetch-legend-pending" aria-hidden="true">◷</i> Scheduled / running / unconfirmed</span>
<span><i class="fetch-legend-failed" aria-hidden="true">!</i> Recorded failure</span>
</div>
<p class="fetch-calendar-note">One marker per batch each day. Partial Success means some work completed; failed runs keep their failure marker. Select a marker for exact slots and recorded outcomes.</p>
<div class="fetch-weekdays" aria-hidden="true"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div>
<div class="fetch-month-grid" data-fetch-month aria-label="{month} calendar">{calendar}</div>
<ul class="fetch-batch-key" aria-label="Batch abbreviations">{batch_key}</ul>
</section>
<section class="fetch-focus" data-fetch-focus aria-labelledby="fetch-focus-heading">
<div class="fetch-section-header"><div><p class="fetch-kicker">{_escape(_date_label(selected, full=True))}</p><h2 id="fetch-focus-heading">{focus_title}</h2></div>
<div class="fetch-summary" aria-label="Selected day outcomes"><span><strong>{_count(summary.get('total'))}</strong> slots</span>
<span class="fetch-count-green"><strong>{_count(summary.get('green'))}</strong> completed</span>
<span class="fetch-count-amber"><strong>{_count(summary.get('partial', 0))}</strong> partial success</span>
<span class="fetch-count-amber"><strong>{_count(summary.get('pending', summary.get('amber')))}</strong> pending / unconfirmed</span>
<span class="fetch-count-red"><strong>{_count(summary.get('red'))}</strong> failed</span></div></div>
<ol class="fetch-timeline">{timeline}</ol></section>
{_equibles_progress(snapshot.get('equibles'))}
{unavailable_markup}
<section class="fetch-evidence-note" aria-label="Schedule evidence"><p><strong>Snapshot</strong> · {_instant(snapshot.get('observed_at'))}</p>
<ul>{notices}</ul><p>Unconfirmed means no matching outcome is recorded. It is not proof of success or failure.</p></section>
</div>"""
    return render_inspector_shell(
        title="Status", active="/status", revision=registry_revision, body=body,
        description="Live fetch schedules and recorded batch outcomes.",
        footer="Local · read-only schedule and execution snapshot · Eastern time (EST/EDT)",
    )


__all__ = ("render_fetch_status_page",)
