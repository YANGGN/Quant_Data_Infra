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
    labels = {"succeeded": "Completed", "partial": "Partial Success", "warning": "Completed · input warnings", "failed": "Failed", "running": "Running", "upcoming": "Scheduled", "unconfirmed": "Unconfirmed"}
    if status not in labels:
        return "unconfirmed", "Unconfirmed", "amber"
    color = _text(event, "color")
    return status, _text(event, "status_label", labels[status]), color if color in {"green", "amber", "red"} else "amber"


def _badge(event: Mapping[str, Any]) -> str:
    status, label, color = _state(event)
    icon = "✓" if status == "warning" else "◐" if status == "partial" else {"green": "✓", "red": "!", "amber": "◷"}[color]
    return (
        '<span class="fetch-badge fetch-' + color + '" data-fetch-status="' + status + '">'
        + '<span aria-hidden="true">' + icon + '</span> ' + _escape(label) + '</span>'
    )


def _slot_time(event: Mapping[str, Any]) -> str:
    label = _text(event, "scheduled_local", "Time unavailable")
    instant = _text(event, "scheduled_at")
    return '<time datetime="' + _escape(instant) + '">' + _escape(label) + '</time>' if instant else _escape(label)


_BATCH_CODES = {
    "Derived calculations": "DC",
    "Market close": "MC",
    "Theta options daily": "OP",
    "Theta options weekly repair": "OR",
    "Macro current": "MA",
    "GDP / CPI": "GI",
    "Employment": "EM",
    "Economic calendar": "EC",
    "SEC fundamentals": "SF",
    "Sharadar fundamentals": "SH",
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
    if counts.get("warning"):
        count_description += ", " + _count(counts["warning"]) + " completed with input warnings"
    description = label + " · " + status_label + " · " + _count(marker.get("run_count")) + " slots; " + count_description
    style = "failed" if status == "failed" else color
    icon = "!" if status == "failed" else "✓" if status in {"succeeded", "warning"} else "◐" if status == "partial" else "◷"
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


def _calendar_day(day: Mapping[str, Any], codes: Mapping[str, tuple[str, str]],
                  intraday: Mapping[str, Any]) -> str:
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
    markers += _intraday_calendar_marker(intraday, day_date)
    empty = '<span class="fetch-month-empty" aria-label="No scheduled batches">—</span>' if not markers else ""
    return (
        '<article class="' + classes + '" data-fetch-day="' + _escape(day_date) + '">'
        + '<a class="fetch-day-link" href="' + _escape(_day_url(day_date)) + '"' + current
        + ' aria-label="' + _escape(_date_label(day_date, full=True) + (". Today." if is_today else "") + " View daily focus.") + '">'
        + '<time datetime="' + _escape(day_date) + '">' + _escape(number) + '</time>' + today + '</a>'
        + '<div class="fetch-month-markers">' + markers + empty + '</div></article>'
    )


def _month_grid(days: tuple[Mapping[str, Any], ...], month_start: str, codes: Mapping[str, tuple[str, str]],
                intraday_days: Mapping[str, Mapping[str, Any]]) -> str:
    if not days:
        return '<p class="fetch-empty">Calendar slots are unavailable.</p>'
    try:
        offset = date.fromisoformat(month_start).weekday()
    except ValueError:
        offset = 0
    blank = '<span class="fetch-month-blank" data-fetch-blank aria-hidden="true"></span>'
    trailing = (-(offset + len(days))) % 7
    return blank * offset + "".join(
        _calendar_day(day, codes, intraday_days.get(_text(day, "date"), {})) for day in days
    ) + blank * trailing



def _sharadar_details(event: Mapping[str, Any]) -> str:
    detail = event.get("sharadar")
    if not isinstance(detail, Mapping):
        return ""
    status = detail.get("http_status")
    name = {"ARQ":"as-reported quarterly", "ART":"as-reported trailing",
            "ARY":"as-reported annual", "MRQ":"restated quarterly",
            "MRT":"restated trailing", "MRY":"restated annual"}.get(detail.get("dimension"), "field definitions")
    failure = ("Sharadar returned HTTP " + _count(status) + " while fetching " + name
               + (" fundamentals for " + _count(detail.get("symbol_count")) + " stocks." if detail.get("dimension") else ".")
               if status else "No HTTP failure was recorded in this pass.")
    progress = (_count(detail.get("completed_partitions")) + " of " + _count(detail.get("total_partitions"))
                + " fundamentals partitions completed." if detail.get("total_partitions") else "Fundamentals partition total was not established.")
    recovery = {"next_day":"Recovery is deferred to the next scheduled day.",
                "budget":"The run limit was reached. Unfinished work is retained for the next scheduled day.",
                "complete":"Completed work and original response evidence are retained.",
                "blocked":"The request requires attention before recovery can continue."}.get(detail.get("recovery"), "")
    return ('<section class="fetch-work-summary" data-sharadar-details aria-label="Sharadar recovery details">'
            '<h4>Sharadar request and recovery</h4><p>' + _escape(failure) + '</p><p>'
            + _escape(progress) + '</p><p>' + _escape(recovery) + '</p><p class="fetch-muted">'
            + _count(detail.get("requests")) + ' requests this run; '
            + _count(detail.get("retries_today")) + ' of 2 timed retries used today.</p></section>')

def _run_counts(event: Mapping[str, Any]) -> str:
    derived = event.get("derived")
    if isinstance(derived, Mapping) and derived:
        fields = (("calculated", "Calculated", "green"),
                  ("current_inputs", "Current", "green"),
                  ("stale_inputs", "Stale", "amber"),
                  ("unconfirmed_inputs", "Freshness unconfirmed", "amber"),
                  ("input_warnings", "Other input warnings", "amber"),
                  ("waiting_inputs", "Waiting for inputs", "red"),
                  ("catchup_pending", "Catch-up pending", "amber"))
        metrics = "".join(
            '<div class="fetch-work-count fetch-count-' + color + '"><dt>' + label + '</dt><dd>'
            + _count(derived.get(key)) + '</dd></div>'
            for key, label, color in fields if key in {"calculated", "current_inputs", "stale_inputs", "unconfirmed_inputs"} or derived.get(key)
        )
        note = ("Current includes older captures confirmed unchanged by recent source checks. "
                "Stale means a newer changed input is confirmed; missing check evidence is shown as freshness unconfirmed. "
                "These counts describe inputs at this publication, not unavailable P/E values.")
        if derived.get("unchanged_checks"):
            note += " " + _count(derived["unchanged_checks"]) + " symbols have successful unchanged source checks."
        recorded = derived.get("recorded_freshness_warnings")
        if isinstance(recorded, int) and recorded != derived.get("freshness_warnings"):
            note += " The original run recorded " + _count(recorded) + " older-capture warnings; source-check evidence updates the labels above."
        return ('<section class="fetch-work-summary" data-fetch-work-counts aria-label="Derived calculation and input outcomes">'
                '<h4>Symbols · recorded publication</h4><dl class="fetch-work-counts">' + metrics
                + '</dl><p class="fetch-muted">' + _escape(note) + '</p></section>')
    counts = event.get("counts")
    units = {"sources": "Sources", "issuers": "Issuers", "etfs": "ETFs", "symbols": "Symbols", "steps": "Source steps", "symbol_sessions": "ETF sessions"}
    if not isinstance(counts, Mapping) or counts.get("unit") not in units:
        return '<p class="fetch-muted" data-fetch-counts-unavailable>Successful and failed counts were not recorded for this run.</p>'
    fields = (("successful", "Successful", "green"), ("failed", "Failed", "red"),
              ("partial", "Incomplete", "amber"), ("skipped", "Skipped / no coverage", "muted"),
              ("unattempted", "Not attempted", "muted"))
    theta = _text(event, "batch_id") in {
        "quant-data-theta-options-daily.timer", "quant-data-theta-options-weekly.timer"}
    if theta:
        fields = (("successful", "Published", "green"), ("failed", "Unresolved gaps", "red"),
                  ("skipped", "Already present", "muted"),
                  ("unattempted", "Unfinished", "amber"))
    derived_legacy = _text(event, "batch_id") == "quant-data-derived-refresh.timer"
    if derived_legacy:
        fields = (("successful", "Current · recorded", "green"), ("failed", "Waiting for inputs", "red"),
                  ("partial", "Unclassified · detail unavailable", "amber"),
                  ("skipped", "Skipped / no coverage", "muted"), ("unattempted", "Not attempted", "muted"))
    metrics = "".join(
        '<div class="fetch-work-count fetch-count-' + color + '"><dt>' + label + '</dt><dd>'
        + _count(counts.get(key)) + '</dd></div>'
        for key, label, color in fields if key in {"successful", "failed"} or counts.get(key)
    )
    return ('<section class="fetch-work-summary" data-fetch-work-counts aria-label="Recorded work outcomes">'
            '<h4>' + units[counts["unit"]] + ' · recorded run</h4><dl class="fetch-work-counts">'
            + metrics + '</dl><p class="fetch-muted">'
            + ("Older stored captures are not proof that a source was not checked. Publication detail is unavailable for this run."
               if derived_legacy else
               "Each ETF session is one symbol on one trading date. Already present includes preserved captures; unfinished work may have been attempted. Source: data/options.sqlite."
               if theta else "Successful work includes unchanged data.") + '</p></section>')


def _focus_event(event: Mapping[str, Any], *, linked_data: bool = False) -> str:
    status, _, color = _state(event)
    datasets = tuple(value for value in _items(event.get("datasets")) if isinstance(value, str))
    dataset_markup = (
        '<ul class="fetch-datasets">' + "".join('<li><code>' + _escape(value) + '</code></li>' for value in datasets) + '</ul>'
        if datasets else '<p class="fetch-muted">No datasets are listed for this batch.</p>'
    )
    dataset_link = (
        '<a class="status-batch-link" href="#status-datasets" data-status-batch-link="'
        + _escape(_text(event, "batch_id")) + '">View stored data for this batch <span aria-hidden="true">→</span></a>'
        if linked_data else ""
    )
    dataset_heading = "Datasets in this batch"
    if _text(event, "batch_id") in {
            "quant-data-theta-options-daily.timer", "quant-data-theta-options-weekly.timer"}:
        # The optional options store is not in the legacy four-store data table.
        dataset_link = ('<a class="status-batch-link" href="/agent-tools">'
                        'Inspect saved options with agent tools <span aria-hidden="true">→</span></a>')
    if _text(event, "batch_id") == "quant-data-derived-refresh.timer":
        dataset_heading = "Research snapshot"
        dataset_link = '<a class="status-batch-link" href="/forward-pe">View forward P/E <span aria-hidden="true">→</span></a>'
        dataset_markup = '<p class="fetch-muted">Derived results are saved in the forward P/E research snapshot.</p>'
    return (
        '<li class="fetch-focus-event" data-fetch-event="' + _escape(_text(event, "id")) + '">'
        + '<div class="fetch-focus-time"><span class="fetch-timeline-dot fetch-' + color + '" aria-hidden="true"></span>'
        + _slot_time(event) + '</div><article class="fetch-focus-card"><div class="fetch-focus-title"><h3>'
        + _escape(_text(event, "label", "Unnamed batch")) + '</h3>' + _badge(event) + '</div><p class="fetch-description">'
        + _escape(_text(event, "description")) + '</p><details data-fetch-event-details><summary>Run details'
        + (' · ' + str(len(datasets)) + ' datasets' if datasets else '') + '</summary><div class="fetch-run-details"><p>'
        + _escape(_text(event, "note", "No execution note is available."))
        + '</p>' + _sharadar_details(event) + _run_counts(event) + '<dl><dt>Scheduled batch start</dt><dd>' + _instant(event.get("scheduled_at"))
        + '</dd><dt>Recorded start</dt><dd>' + _instant(event.get("started_at"))
        + '</dd><dt>Recorded finish</dt><dd>' + _instant(event.get("finished_at"))
        + '</dd></dl>' + dataset_link + '<h4>' + dataset_heading + '</h4>' + dataset_markup + '</div></details></article></li>'
    )


_NEWS_BATCH = "quant-data-current-news-refresh.timer"


def _news_group(events: tuple[Mapping[str, Any], ...], *, linked_data: bool = False) -> str:
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
        + "".join(_focus_event(event, linked_data=linked_data) for event in events) + '</ol></details></li>'
    )


def _focus_timeline(events: tuple[Mapping[str, Any], ...], *, linked_data: bool = False) -> str:
    news = tuple(event for event in events if _text(event, "batch_id") == _NEWS_BATCH)
    rendered = []
    grouped = False
    for event in events:
        if _text(event, "batch_id") == _NEWS_BATCH:
            if not grouped:
                rendered.append(_news_group(news, linked_data=linked_data))
                grouped = True
        else:
            rendered.append(_focus_event(event, linked_data=linked_data))
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


_INTRADAY_STATES = {
    "current": ("Current", "green"),
    "stale": ("Behind cadence", "amber"),
    "failed": ("Collection issue", "red"),
    "waiting": ("Awaiting observations", "amber"),
    "closed": ("Market closed", "neutral"),
    "paused": ("Paused", "neutral"),
    "unknown": ("Unknown", "neutral"),
    "recorded": ("Requests recorded", "neutral"),
    "partial": ("Partial collection", "amber"),
    "scheduled": ("Scheduled", "neutral"),
}


def _intraday_badge(state: str, *, delivery: bool = False) -> str:
    states = ({"current": ("Queue clear", "green"),
               "pending": ("Uploads pending", "amber"),
               "unknown": ("Unknown", "neutral")}
              if delivery else _INTRADAY_STATES)
    state = state if state in states else "unknown"
    label, color = states[state]
    icon = {"green": "✓", "red": "!", "amber": "◷", "neutral": "—"}[color]
    return ('<span class="fetch-badge fetch-' + color + '" data-intraday-state="' + state
            + '"><span aria-hidden="true">' + icon + '</span> ' + label + '</span>')


def _intraday_count(value: object) -> str:
    return "Unknown" if _count(value) == "—" else _count(value)


def _intraday_has_issues(record: Mapping[str, Any]) -> bool:
    return any(type(record.get(key)) is int and record[key] > 0
               for key in ("failed_requests", "unresolved_requests"))


def _intraday_calendar_marker(record: Mapping[str, Any], day_date: str) -> str:
    state = _text(record, "state", "unknown")
    state = state if state in _INTRADAY_STATES else "unknown"
    label, color = _INTRADAY_STATES[state]
    icon = {"green": "✓", "red": "!", "amber": "◐", "neutral": "·"}[color]
    description = "Intraday Options · " + _date_label(day_date) + " · " + label
    return (
        '<a class="fetch-marker fetch-intraday-marker fetch-marker-' + color
        + '" data-fetch-intraday-marker="' + _escape(day_date) + '" data-intraday-state="' + state
        + '" href="' + _escape(_day_url(day_date)) + '#fetch-intraday-heading" title="'
        + _escape(description) + '" aria-label="' + _escape(description + '. View this day’s intraday evidence.') + '">'
        '<span class="fetch-marker-icon" aria-hidden="true">' + icon + '</span>'
        '<span class="fetch-marker-code" aria-hidden="true">IO</span>'
        '<span class="fetch-marker-label" aria-hidden="true">Intraday Options</span></a>'
    )


def _intraday_selected_day(status: Mapping[str, Any], selected_date: str) -> str:
    day = next((row for row in _records(status.get("calendar_days"))
                if _text(row, "date") == selected_date), {})
    historical = status.get("selected_session") if isinstance(status.get("selected_session"), Mapping) else {}
    if _text(historical, "date") != selected_date:
        historical = {}
    request_counts = "".join(
        '<div><dt>' + label + '</dt><dd>' + _intraday_count(day.get(key)) + '</dd></div>'
        for key, label in (("successful_requests", "Successful requests"),
                           ("failed_requests", "Failed requests"), ("unresolved_requests", "Unresolved requests"))
    )
    state = _text(day, "state", "unknown")
    has_issues = _intraday_has_issues(day) or state in {"failed", "partial", "stale"}
    brief = ("Request issues recorded" if has_issues else
             "Scheduled session" if state == "scheduled" else
             "Evidence unavailable" if state == "unknown" else "Selected-day intraday activity")
    return (
        '<section class="fetch-intraday" aria-labelledby="fetch-intraday-heading" data-intraday-options data-intraday-history>'
        '<div class="fetch-focus-title fetch-intraday-compact-title">'
        '<h3 id="fetch-intraday-heading" tabindex="-1">Intraday Options</h3></div>'
        '<div class="fetch-intraday-statuses" data-intraday-compact-states><div><span>Collection</span>'
        + _intraday_badge(state) + '</div></div>'
        '<p class="fetch-description' + (' fetch-intraday-attention' if has_issues else '')
        + '" data-intraday-brief>' + brief + ' · ' + _escape(_date_label(selected_date)) + '</p>'
        '<details class="fetch-intraday-disclosure" data-intraday-details><summary>Run details</summary>'
        '<div class="fetch-intraday-expanded"><p class="fetch-kicker">Selected day · intraday activity</p>'
        '<p class="fetch-intraday-context">' + _escape(_date_label(selected_date, full=True)) + '</p>'
        '<p class="fetch-intraday-message">' + _escape(_text(day, "message", "Intraday request evidence is unavailable for this date."))
        + '</p><dl class="fetch-intraday-request-counts">' + request_counts + '</dl>'
        '<dl class="fetch-intraday-history-metrics"><div><dt>Observations</dt><dd>'
        + _intraday_count(historical.get("observations")) + '</dd></div><div><dt>Symbols observed</dt><dd>'
        + _intraday_count(historical.get("symbols")) + '</dd></div><div><dt>Last observation</dt><dd>'
        + _instant(historical.get("last_observed_at")) + '</dd></div><div><dt>Last request attempt</dt><dd>'
        + _instant(day.get("last_attempt_at")) + '</dd></div></dl>'
        '<p class="fetch-intraday-queue-note">' + _escape(_text(historical, "note", "Coverage detail is unavailable for this selected date."))
        + '</p><p class="fetch-intraday-footnote">Request totals describe this date and do not prove a complete session. '
        'Intraday requests are separate from daily batch slots.</p></div></details></section>'
    )


def _intraday_options(value: object, *, selected_date: str) -> str:
    """Present supplied monitor evidence; health and freshness belong to the reader."""
    status = value if isinstance(value, Mapping) else {}
    if selected_date != _text(status, "operating_date"):
        return _intraday_selected_day(status, selected_date)
    collection = status.get("collection") if isinstance(status.get("collection"), Mapping) else {}
    delivery = status.get("delivery") if isinstance(status.get("delivery"), Mapping) else {}
    scheduler = status.get("scheduler") if isinstance(status.get("scheduler"), Mapping) else {}
    session = status.get("session") if isinstance(status.get("session"), Mapping) else {}
    operating_date = _text(status, "operating_date")
    current_date = _date_label(operating_date) if operating_date else "Date unavailable"
    collection_state = _text(collection, "state", "unknown")
    coverage_label, coverage_key = (("Symbols observed / configured", "symbols_observed")
                                    if collection_state in {"closed", "paused"}
                                    else ("Fresh symbols / configured", "fresh_symbols"))
    delivery_state = _text(delivery, "state", "unknown")
    collection_message = _text(collection, "message", "Collection evidence is unavailable.")
    delivery_message = _text(delivery, "message", "Upload queue evidence is unavailable.")
    session_label = {"open": "Market open", "closed": "Market closed", "unknown": "Market session unknown"}.get(
        _text(session, "state"), "Market session unknown")
    scheduler_label = {"enabled": "Enabled", "disabled": "Disabled", "unknown": "Unknown"}.get(
        _text(scheduler, "state"), "Unknown")
    cadence = {record.get("minutes"): record for record in _records(status.get("cadence"))
               if type(record.get("minutes")) is int and record["minutes"] in {5, 10, 15, 30}}
    cadence_rows = []
    for minutes in (5, 10, 15, 30):
        row = cadence.get(minutes, {})
        cadence_rows.append(
            '<div class="fetch-intraday-cadence" data-intraday-cadence="' + str(minutes) + '">'
            '<div><h5>Every ' + str(minutes) + ' min</h5>'
            + _intraday_badge(_text(row, "state", "unknown")) + '</div>'
            '<dl><div><dt>Symbols observed</dt><dd>' + _intraday_count(row.get("symbols_observed"))
            + '</dd></div><div><dt>Last observation</dt><dd>' + _instant(row.get("last_observed_at"))
            + '</dd></div></dl></div>'
        )
    current_context = ('Current operating state · ' + _escape(current_date) + ' · ' + session_label)
    has_issues = _intraday_has_issues(collection) or collection_state in {"failed", "partial", "stale"}
    brief = ("Request issues recorded · review run details." if _intraday_has_issues(collection) else {
        "current": "Continuous collection · 5–30 min cadence.",
        "closed": "Outside the trading session · 5–30 min cadence.",
        "paused": "Collection paused · saved observations retained.",
        "waiting": "Awaiting observations for the current session.",
        "stale": "Saved observations are behind the configured cadence.",
        "failed": "Collection needs attention · review run details.",
        "partial": "Partial collection · review run details.",
    }.get(collection_state, "Collection evidence is unavailable."))
    if status.get("available") is not True:
        brief = "Monitor evidence is unavailable."
    unavailable = ('<p class="fetch-intraday-unavailable" data-intraday-unavailable>'
                   + _escape(_text(status, "reason", "Monitor evidence is unavailable. Unknown values are not zero."))
                   + '</p>') if status.get("available") is not True else ""
    return (
        '<section class="fetch-intraday" aria-labelledby="fetch-intraday-heading" data-intraday-options>'
        '<div class="fetch-focus-title fetch-intraday-compact-title">'
        '<h3 id="fetch-intraday-heading" tabindex="-1">Intraday Options</h3></div>'
        '<div class="fetch-intraday-statuses" data-intraday-compact-states><div><span>Collection</span>'
        + _intraday_badge(collection_state) + '</div><div><span>Dashboard delivery</span>'
        + _intraday_badge(delivery_state, delivery=True) + '</div></div>'
        '<p class="fetch-description' + (' fetch-intraday-attention' if has_issues else '')
        + '" data-intraday-brief>' + brief + '</p>'
        '<details class="fetch-intraday-disclosure" data-intraday-details><summary>Run details</summary>'
        '<div class="fetch-intraday-expanded"><div class="fetch-section-header"><p class="fetch-kicker">Current monitor snapshot</p>'
        '<p class="fetch-intraday-checked">Checked <span>' + _instant(status.get("checked_at")) + '</span></p></div>'
        '<p class="fetch-intraday-context">' + current_context + '</p>' + unavailable
        + '<div class="fetch-intraday-summary"><section aria-labelledby="fetch-intraday-collection-heading">'
        '<div class="fetch-intraday-title"><h4 id="fetch-intraday-collection-heading">Collection</h4>'
        + _intraday_badge(collection_state) + '</div><p class="fetch-intraday-message">'
        + _escape(collection_message) + '</p><dl class="fetch-intraday-metrics">'
        '<div><dt>Last observation</dt><dd>' + _instant(collection.get("last_observed_at")) + '</dd></div>'
        '<div><dt>' + coverage_label + '</dt><dd>' + _intraday_count(collection.get(coverage_key))
        + ' <span>/ ' + _intraday_count(collection.get("symbols_configured")) + '</span></dd></div></dl></section>'
        '<section aria-labelledby="fetch-intraday-delivery-heading"><div class="fetch-intraday-title">'
        '<h4 id="fetch-intraday-delivery-heading">Dashboard delivery</h4>' + _intraday_badge(delivery_state, delivery=True)
        + '</div><p class="fetch-intraday-message">' + _escape(delivery_message)
        + '</p><dl class="fetch-intraday-metrics"><div><dt>Pending uploads</dt><dd data-intraday-pending>'
        + _intraday_count(delivery.get("pending_count")) + '</dd></div></dl>'
        '<p class="fetch-intraday-queue-note">Local upload queue only. A clear queue does not confirm the remote dashboard is current.</p>'
        '</section></div><div class="fetch-intraday-cadence-heading"><h4>Collection cadence</h4>'
        '<p>Saved observations by configured interval · Eastern time</p></div>'
        '<div class="fetch-intraday-cadences">' + ''.join(cadence_rows) + '</div>'
        '<div class="fetch-intraday-details">'
        '<dl><div><dt>Last collection attempt</dt><dd>' + _instant(collection.get("last_attempt_at"))
        + '</dd></div><div><dt>Last completed cycle</dt><dd>' + _instant(collection.get("last_completed_at"))
        + '</dd></div><div><dt>Symbols observed</dt><dd>' + _intraday_count(collection.get("symbols_observed"))
        + '</dd></div><div><dt>Symbols pending</dt><dd>' + _intraday_count(collection.get("pending_symbols"))
        + '</dd></div><div><dt>Failed requests</dt><dd>' + _intraday_count(collection.get("failed_requests"))
        + '</dd></div><div><dt>Unresolved requests</dt><dd>' + _intraday_count(collection.get("unresolved_requests"))
        + '</dd></div><div><dt>Scheduler</dt><dd>' + scheduler_label
        + '</dd></div><div><dt>Next cycle</dt><dd>' + _instant(scheduler.get("next_run_at"))
        + '</dd></div><div><dt>Last upload attempt</dt><dd>' + _instant(delivery.get("last_upload_attempt_at"))
        + '</dd></div><div><dt>Pending alerts</dt><dd>' + _intraday_count(delivery.get("pending_alerts"))
        + '</dd></div></dl></div>'
        + '<p class="fetch-intraday-footnote">Intraday observations are separate from daily batch slots and their totals. '
        'Refresh the page to update this snapshot.</p></div></details></section>'
    )


def render_fetch_status_content(snapshot: Mapping[str, Any], *, linked_data: bool = False) -> str:
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
    intraday = snapshot.get("intraday_options") if isinstance(snapshot.get("intraday_options"), Mapping) else {}
    intraday_days = {_text(row, "date"): row for row in _records(intraday.get("calendar_days"))}
    calendar = _month_grid(days, month_start, codes, intraday_days)
    batch_key = "".join(
        '<li><span class="fetch-code-key">' + _escape(code) + '</span>' + _escape(label) + '</li>'
        for code, label in codes.values()
    )
    batch_key += '<li><span class="fetch-code-key">IO</span>Intraday Options · continuous monitor</li>'
    timeline = _focus_timeline(focus, linked_data=linked_data)
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
    calendar_start = (
        '<details class="fetch-calendar status-calendar" open><summary><span>Run calendar</span>'
        '<span>' + month + '</span></summary><section aria-labelledby="fetch-month-heading">'
        if linked_data else '<section class="fetch-calendar" aria-labelledby="fetch-month-heading">'
    )
    calendar_end = "</section></details>" if linked_data else "</section>"
    warning_summary = ('<span class="fetch-count-amber"><strong>' + _count(summary.get("warning"))
                       + '</strong> completed with input warnings</span>') if summary.get("warning") else ""
    body = f"""
<div class="fetch-page" data-fetch-status-page>
{calendar_start}
<div class="fetch-section-header"><div><p class="fetch-kicker">Monthly calendar · Eastern time</p><h2 id="fetch-month-heading">{month}</h2></div>
<nav class="fetch-week-navigation" aria-label="Calendar month">
{_month_link(snapshot.get('previous_month_date'), direction='prev')}
<a href="/status">Today</a>
{_month_link(snapshot.get('next_month_date'), direction='next')}
</nav></div>
<div class="fetch-month-legend" data-fetch-legend aria-label="Batch marker status legend">
<span><i class="fetch-legend-completed" aria-hidden="true">✓</i> All completed</span>
<span><i class="fetch-legend-partial" aria-hidden="true">◐</i> Partial Success</span>
<span><i class="fetch-legend-partial" aria-hidden="true">✓</i> Completed · input warnings</span>
<span><i class="fetch-legend-pending" aria-hidden="true">◷</i> Scheduled / running / unconfirmed</span>
<span><i class="fetch-legend-failed" aria-hidden="true">!</i> Recorded failure</span>
</div>
<p class="fetch-calendar-note">One marker per batch each day. Partial Success means some work completed; failed runs keep their failure marker. Select a marker for exact slots and recorded outcomes.</p>
<p class="fetch-calendar-note fetch-intraday-calendar-note">IO shows intraday activity for each date. Recorded requests do not prove a complete session; closed, scheduled and unknown states remain separate. Intraday pulls are not counted as daily batch slots.</p>
<div class="fetch-weekdays" aria-hidden="true"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div>
<div class="fetch-month-grid" data-fetch-month aria-label="{month} calendar">{calendar}</div>
<ul class="fetch-batch-key" aria-label="Batch abbreviations">{batch_key}</ul>
{calendar_end}
<section class="fetch-focus" data-fetch-focus tabindex="0" aria-labelledby="fetch-focus-heading">
<div class="fetch-section-header"><div><p class="fetch-kicker">{_escape(_date_label(selected, full=True))}</p><h2 id="fetch-focus-heading">{focus_title}</h2></div>
<div class="fetch-summary" aria-label="Selected day batch outcomes"><span><strong>{_count(summary.get('total'))}</strong> slots · daily batches</span>
<span class="fetch-count-green"><strong>{_count(summary.get('green'))}</strong> completed</span>
<span class="fetch-count-amber"><strong>{_count(summary.get('partial', 0))}</strong> partial success</span>
{warning_summary}
<span class="fetch-count-amber"><strong>{_count(summary.get('pending', summary.get('amber')))}</strong> pending / unconfirmed</span>
<span class="fetch-count-red"><strong>{_count(summary.get('red'))}</strong> failed</span></div></div>
{_intraday_options(snapshot.get('intraday_options'), selected_date=selected)}
<ol class="fetch-timeline">{timeline}</ol></section>
{_equibles_progress(snapshot.get('equibles'))}
{unavailable_markup}
<section class="fetch-evidence-note" aria-label="Schedule evidence"><p><strong>Snapshot</strong> · {_instant(snapshot.get('observed_at'))}</p>
<ul>{notices}</ul><p>Unconfirmed means no matching outcome is recorded. It is not proof of success or failure.</p></section>
</div>"""
    return body


def render_fetch_status_page(snapshot: Mapping[str, Any], *, registry_revision: str) -> str:
    """Compatibility renderer for the run-only fragment."""
    return render_inspector_shell(
        title="Status", active="/status", revision=registry_revision, body=render_fetch_status_content(snapshot),
        description="Live fetch schedules and recorded batch outcomes.",
        footer="Local · read-only schedule and execution snapshot · Eastern time (EST/EDT)",
    )


__all__ = ("render_fetch_status_page", "render_fetch_status_content")
