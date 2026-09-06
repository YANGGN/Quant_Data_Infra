"""Escaped retained-data status and local timer presentation for the Inspector."""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import QuantDataError
from ..json_codec import loads_strict
from .inspector_shell import render_inspector_shell


def _sequence(value: object) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return tuple(value)
    return ()


def _rows(result: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for record in _sequence(result.get("records")):
        if not isinstance(record, Mapping):
            continue
        row: dict[str, object] = {}
        for field in _sequence(record.get("fields")):
            if isinstance(field, Mapping) and isinstance(field.get("name"), str):
                row[str(field["name"])] = field.get("value")
        rows.append(row)
    return tuple(rows)


def _decoded(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return loads_strict(value, max_bytes=16_384)
    except QuantDataError:
        return value


def _timestamp(value: object) -> str:
    decoded = _decoded(value)
    if not isinstance(decoded, Mapping):
        return "—"
    captured = decoded.get("captured_at") or decoded.get("recorded_at")
    if isinstance(captured, Mapping):
        captured = captured.get("value")
    return str(captured) if captured else "—"


def _eastern_timestamp(timestamp: str) -> str:
    """Display an explicit instant in New York time without rounding its fraction."""
    match = re.fullmatch(
        r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[T ]"
        r"(?P<clock>[0-9]{2}:[0-9]{2}:[0-9]{2})"
        r"(?P<fraction>\.[0-9]+)?(?P<offset>Z|[+-][0-9]{2}:[0-9]{2})",
        timestamp,
    )
    if not match or match["offset"] == "-00:00":
        return timestamp
    offset = "+00:00" if match["offset"] == "Z" else match["offset"]
    if int(offset[1:3]) > 23 or int(offset[4:6]) > 59:
        return timestamp
    try:
        # Keep fractional digits outside datetime, whose precision is microseconds.
        instant = datetime.fromisoformat(match["date"] + "T" + match["clock"] + offset)
        eastern = instant.astimezone(ZoneInfo("America/New_York"))
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return timestamp
    fraction = match["fraction"] or ""
    return (
        f"{eastern.year:04d}-{eastern.month:02d}-{eastern.day:02d} "
        f"{eastern.hour:02d}:{eastern.minute:02d}:{eastern.second:02d}"
        + fraction + " " + str(eastern.tzname())
    )


def _instant_markup(original: str) -> str:
    displayed = _eastern_timestamp(original)
    if displayed == original:
        return _escape(original)
    return (
        '<time datetime="' + _escape(original) + '">'
        + _escape(displayed) + '</time>'
    )


def _capture_timestamp_markup(value: object) -> str:
    return _instant_markup(_timestamp(value))


def _outcome(value: object) -> str:
    decoded = _decoded(value)
    if not isinstance(decoded, Mapping):
        return "—"
    return str(decoded.get("outcome_kind") or decoded.get("status") or "—")


def _reference(value: object) -> str:
    decoded = _decoded(value)
    if not isinstance(decoded, Mapping):
        return "—"
    start = decoded.get("period_start")
    end = decoded.get("period_end")
    if start and end and start != end:
        return f"{start} – {end}"
    return str(end or start or "—")


def _threshold(value: object) -> str:
    decoded = _decoded(value)
    if not isinstance(decoded, Mapping):
        return "Not declared"
    return str(decoded.get("stale_after") or "Not declared")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _schedule_cells(
    row: Mapping[str, object],
    schedules: Mapping[str, Mapping[str, str | None]] | None,
) -> str:
    """Present the supplied timer snapshot without reading or inferring schedules."""
    identity = str(row.get("id", row.get("dataset_id", "")))
    schedule = schedules.get(identity) if schedules is not None else None
    if schedule is None:
        state = "unmapped" if schedules is not None else "unavailable"
        schedule = {}
    else:
        state = str(schedule.get("schedule_state") or "unavailable")
    if state not in {"scheduled", "inactive", "unavailable", "unmapped"}:
        state = "unavailable"
    fallback = "Not scheduled" if state in {"inactive", "unmapped"} else "Unavailable"
    cadence = schedule.get("refresh_cadence") or fallback
    next_fetch = schedule.get("next_scheduled_fetch")
    next_markup = (
        '<time datetime="' + _escape(next_fetch) + '">'
        + _escape(next_fetch) + '</time> <small>UTC</small>'
        if state == "scheduled" and next_fetch
        else _escape(fallback)
    )
    note = schedule.get("schedule_note")
    note_markup = (
        '<br><small class="inspector-schedule-note">' + _escape(note) + '</small>'
        if note else ""
    )
    return (
        '<td data-field="refresh_cadence" data-label="Refresh Cadence" data-schedule-state="' + state + '">'
        + _escape(cadence)
        + '</td><td data-field="next_scheduled_fetch" data-label="Next Scheduled Fetch" data-schedule-state="'
        + state + '">' + next_markup + note_markup + '</td>'
    )


def _metadata_text(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    return value if isinstance(value, str) else ""


def _metadata_for(
    row: Mapping[str, object],
    metadata: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
    identity = str(row.get("id", row.get("dataset_id", "")))
    value = metadata.get(identity) if metadata is not None else None
    return value if isinstance(value, Mapping) else {}


def _lifecycle(metadata: Mapping[str, Any]) -> str:
    value = _metadata_text(metadata, "lifecycle")
    return value if value in {"active", "legacy", "historical", "fixture", "planned"} else "active"


def _retention_state(row: Mapping[str, object], metadata: Mapping[str, Any]) -> str:
    value = _metadata_text(metadata, "retention_state")
    if value in {"retained", "missing", "unknown"}:
        return value
    # Existing public source statuses already describe their native retained evidence.
    return {"current": "retained", "stale": "retained", "no_data": "missing"}.get(
        str(row.get("status", "unknown")), "unknown"
    )


def _retention_freshness(row: Mapping[str, object], metadata: Mapping[str, Any]) -> str:
    value = _metadata_text(metadata, "retention_freshness")
    if value in {"current", "stale", "unknown"}:
        return value
    value = str(row.get("status", "unknown"))
    return value if value in {"current", "stale"} else "unknown"


def _capture_provenance(metadata: Mapping[str, Any]) -> str:
    basis = _metadata_text(metadata, "retention_basis")
    return {
        "direct": "Direct capture record",
        "shared": "Shared ingestion output",
        "native": "Native capture ledger",
    }.get(basis, "Public status evidence" if not basis else "Not established")


def _status_category(style: str) -> str:
    if style in {"legacy", "historical", "fixture"}:
        return "nonlive"
    if style == "planned":
        return "planned"
    if style in {"failed", "batch-issue", "source-old", "stale", "missing"}:
        return "attention"
    return "retained" if style == "current" else "unknown"


def _display_status(
    row: Mapping[str, object], metadata: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Use the same explicit evidence classification for rows and summary counts."""
    lifecycle = _lifecycle(metadata)
    lifecycle_note = _metadata_text(metadata, "lifecycle_note")
    if lifecycle != "active":
        label = {
            "legacy": "Legacy", "historical": "Historical snapshot",
            "fixture": "Fixture only", "planned": "Not yet live",
        }[lifecycle]
        return label, lifecycle, lifecycle_note
    retention = _retention_state(row, metadata)
    freshness = _retention_freshness(row, metadata)
    refresh_state = _metadata_text(metadata, "refresh_state") or "unknown"
    refresh_note = _metadata_text(metadata, "refresh_note")
    provenance = _capture_provenance(metadata)
    retained_note = (
        "Data retained · " + provenance + "." if retention == "retained" else ""
    )
    if refresh_state == "failed":
        note = " ".join(part for part in (refresh_note, retained_note) if part)
        return "Refresh failed", "failed", note
    if refresh_state == "batch_failed":
        note = " ".join(part for part in (
            "Per-source outcome unavailable.", refresh_note, retained_note
        ) if part)
        return "Batch issue", "batch-issue", note
    if metadata.get("source_overdue") is True:
        note = refresh_note or "Source date is older than the expected update window."
        return "Source date old", "source-old", " ".join(part for part in (note, retained_note) if part)
    if retention == "retained" and freshness == "stale":
        return "Capture old", "stale", " ".join(part for part in (
            "Retained capture is beyond its declared threshold.", retained_note
        ) if part)
    if retention == "missing":
        return "Capture not found", "missing", _metadata_text(metadata, "retention_note") or "No successful stored capture is recorded."
    if retention == "unknown":
        return "Status unavailable", "unknown", _metadata_text(metadata, "retention_note") or "Retention evidence is unavailable."
    return "Data retained", "current", provenance + "."


def _note_markup(text: str, *, class_name: str = "inspector-status-note") -> str:
    return '<br><small class="' + class_name + '">' + _escape(text) + '</small>' if text else ""


def _as_of_cell(metadata: Mapping[str, Any]) -> str:
    value = _metadata_text(metadata, "as_of_date")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        displayed = '<time datetime="' + _escape(value) + '">' + _escape(value) + '</time>'
    else:
        displayed = _escape(value) if value else "Not available"
    return (
        '<td data-field="as_of_date" data-label="As-of Date">' + displayed
        + _note_markup(_metadata_text(metadata, "source_frequency"), class_name="inspector-source-frequency")
        + _note_markup(_metadata_text(metadata, "as_of_note")) + '</td>'
    )


def _fetch_cell(metadata: Mapping[str, Any]) -> str:
    timestamp = _metadata_text(metadata, "latest_successful_fetch_at")
    displayed = _instant_markup(timestamp) if timestamp else "Not recorded"
    note = "" if timestamp else "Past unchanged checks may not have been recorded."
    return (
        '<td data-field="latest_successful_fetch_at" data-label="Latest Successful Fetch · Eastern (EST/EDT)">'
        + displayed + _note_markup(note) + '</td>'
    )


def _detail_cell(field: str, label: str, markup: str) -> str:
    return (
        '<td data-inspector-detail-only data-field="' + _escape(field)
        + '" data-label="' + _escape(label) + '">' + markup + '</td>'
    )


_SUPPLEMENTAL_HEADERS = (
    "Store",
    "Last stored capture · Eastern (EST/EDT)",
    "Public retained threshold status",
    "Latest retained outcome",
    "Reference period",
    "Stale after",
    "Latest refresh outcome",
    "Latest refresh attempt · Eastern (EST/EDT)",
    "Successor dataset",
    "Capture provenance",
    "Linked capture dataset",
    "Retention evidence",
    "Retention freshness",
    "Original public capture · Eastern (EST/EDT)",
    "Original public retained outcome",
)


def _status_row(
    row: Mapping[str, object],
    schedules: Mapping[str, Mapping[str, str | None]] | None,
    metadata: Mapping[str, Any],
) -> str:
    label, style, note = _display_status(row, metadata)
    identity = str(row.get("id", row.get("dataset_id", "—")))
    retained = _escape(row.get("status", "unknown")) + _note_markup(str(row.get("status_reason", "—")))
    refresh = _escape(_metadata_text(metadata, "refresh_state") or "unknown")
    refresh += _note_markup(_metadata_text(metadata, "refresh_note"))
    attempted = _metadata_text(metadata, "latest_attempt_at")
    attempted_markup = _instant_markup(attempted) if attempted else "Not recorded"
    capture = (
        _metadata_text(metadata, "retained_capture_at") or "—"
        if "retained_capture_at" in metadata else _timestamp(row.get("latest_successful_capture"))
    )
    outcome = (
        _metadata_text(metadata, "retained_outcome") or "—"
        if "retained_outcome" in metadata else _outcome(row.get("latest_retained_outcome"))
    )
    provenance = _escape(_capture_provenance(metadata))
    provenance += _note_markup(_metadata_text(metadata, "retention_note"))
    retention_label = {"retained": "Data retained", "missing": "Capture not found", "unknown": "Status unavailable"}[
        _retention_state(row, metadata)
    ]
    freshness_label = {"current": "Within threshold", "stale": "Beyond threshold", "unknown": "Not established"}[
        _retention_freshness(row, metadata)
    ]
    supplemental = "".join((
        _detail_cell("store", _SUPPLEMENTAL_HEADERS[0], _escape(row.get("store", "—"))),
        _detail_cell("last_successful_capture", _SUPPLEMENTAL_HEADERS[1], _instant_markup(capture)),
        _detail_cell("retained_status", _SUPPLEMENTAL_HEADERS[2], retained),
        _detail_cell("latest_retained_outcome", _SUPPLEMENTAL_HEADERS[3], _escape(outcome)),
        _detail_cell("latest_reference_period", _SUPPLEMENTAL_HEADERS[4], _escape(_reference(row.get("latest_reference_period")))),
        _detail_cell("freshness_threshold", _SUPPLEMENTAL_HEADERS[5], _escape(_threshold(row.get("freshness")))),
        _detail_cell("latest_refresh_outcome", _SUPPLEMENTAL_HEADERS[6], refresh),
        _detail_cell("latest_attempt_at", _SUPPLEMENTAL_HEADERS[7], attempted_markup),
        _detail_cell("successor_id", _SUPPLEMENTAL_HEADERS[8], _escape(_metadata_text(metadata, "successor_id") or "—")),
        _detail_cell("retention_basis", _SUPPLEMENTAL_HEADERS[9], provenance),
        _detail_cell("capture_anchor_id", _SUPPLEMENTAL_HEADERS[10], _escape(_metadata_text(metadata, "capture_anchor_id") or "—")),
        _detail_cell("retention_state", _SUPPLEMENTAL_HEADERS[11], _escape(retention_label)),
        _detail_cell("retention_freshness", _SUPPLEMENTAL_HEADERS[12], _escape(freshness_label)),
        _detail_cell("public_last_successful_capture", _SUPPLEMENTAL_HEADERS[13], _capture_timestamp_markup(row.get("latest_successful_capture"))),
        _detail_cell("public_latest_retained_outcome", _SUPPLEMENTAL_HEADERS[14], _escape(_outcome(row.get("latest_retained_outcome")))),
    ))
    return (
        '<tr data-lifecycle="' + _lifecycle(metadata) + '" data-status-category="' + _status_category(style)
        + '"><td data-field="display_status" data-label="Status">'
        + '<strong class="inspector-state inspector-state-' + style + '">' + _escape(label)
        + '</strong>' + _note_markup(note)
        + '</td><th scope="row" data-field="dataset_id" data-label="Dataset or source"><code>'
        + _escape(identity) + '</code></th>' + _as_of_cell(metadata) + _fetch_cell(metadata)
        + _schedule_cells(row, schedules) + supplemental + '</tr>'
    )


def _render_status_group(
    group: str,
    rows: tuple[dict[str, object], ...],
    *,
    schedules: Mapping[str, Mapping[str, str | None]] | None,
    metadata: Mapping[str, Mapping[str, Any]] | None,
) -> str:
    live = group == "live"
    title = "Live data" if live else "Fixtures, legacy & planned"
    description = (
        "Active datasets, including any with capture or refresh issues."
        if live else "Fixtures, historical snapshots, legacy evidence and planned coverage."
    )
    counts = Counter(
        _status_category(_display_status(row, _metadata_for(row, metadata))[1])
        for row in rows
    )
    summaries = (
        (
            ("retained", "Data retained", "Successful capture evidence"),
            ("attention", "Needs attention", "Active errors or missing / old captures"),
            ("unknown", "Status unknown", "Retention evidence unavailable"),
        )
        if live else (
            ("nonlive", "Legacy & fixtures", "Historical, superseded or fixture-only"),
            ("planned", "Not yet live", "Planned source coverage"),
        )
    )
    summary_markup = "".join(
        '<article class="metric" data-status-summary="' + category + '"><p>'
        + _escape(label) + '</p><strong>' + str(counts[category]) + '</strong><span>'
        + _escape(note) + '</span></article>'
        for category, label, note in summaries
    )
    table_rows = "".join(_status_row(row, schedules, _metadata_for(row, metadata)) for row in rows)
    supplemental_headers = "".join(
        '<th scope="col" data-inspector-detail-only>' + _escape(label) + '</th>'
        for label in _SUPPLEMENTAL_HEADERS
    ) if rows else ""
    if not table_rows:
        message = "No live data records are available." if live else "No other dataset records are available."
        table_rows = '<tr><td colspan="6">' + message + '</td></tr>'
    return f"""
<section id="data-status-{group}" data-status-group="{group}" aria-labelledby="data-status-{group}-heading">
<p class="inspector-summary-label">{_escape(title)} overview · {len(rows)} records, each counted once</p>
<div class="metric-grid inspector-summary inspector-status-summary" aria-label="{_escape(title)} summary">{summary_markup}</div>
<section class="panel"><div class="panel-header"><div><h2 id="data-status-{group}-heading">{_escape(title)} · {len(rows)} records</h2>
<p>{description}</p></div><a href="/api/data-status" title="Retained-status JSON for all datasets; source metadata and timer schedules are shown on this page">View JSON</a></div>
<div class="inspector-table-workspace" data-inspector-table-workspace><div class="table-scroll" tabindex="0" role="region" aria-label="{_escape(title)} status and refresh evidence"><table data-inspector-table data-inspector-record-note="Displayed fields · resolved capture evidence, original public status, source dates, refresh receipts and local timer snapshot"><caption>{_escape(title)} · all supporting fields preserved</caption>
<thead><tr><th scope="col">Status</th><th scope="col">Dataset or source</th><th scope="col">As-of Date</th><th scope="col">Latest Successful Fetch<br><span>Eastern (EST/EDT)</span></th><th scope="col">Refresh Cadence</th><th scope="col">Next Scheduled Fetch</th>{supplemental_headers}</tr></thead>
<tbody>{table_rows}</tbody></table></div></div></section></section>"""


def render_data_status_page(
    result: Mapping[str, Any],
    *,
    registry_revision: str,
    error: str | None = None,
    schedules: Mapping[str, Mapping[str, str | None]] | None = None,
    metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Partition reviewed lifecycle groups; render all evidence before enhancement."""
    rows = _rows(result)
    live_rows = tuple(row for row in rows if _lifecycle(_metadata_for(row, metadata)) == "active")
    other_rows = tuple(row for row in rows if _lifecycle(_metadata_for(row, metadata)) != "active")
    groups = "".join(
        _render_status_group(group, group_rows, schedules=schedules, metadata=metadata)
        for group, group_rows in (("live", live_rows), ("other", other_rows))
    )
    notice = (
        '<div class="state-notice state-error" role="alert">' + _escape(error) + '</div>'
        if error else ""
    )
    body = f"""
{notice}<div class="inspector-status-group-control" data-status-group-control hidden>
<div><label for="data-status-group-select">Dataset group</label>
<select id="data-status-group-select" data-status-group-select aria-controls="data-status-live data-status-other" aria-describedby="data-status-group-help">
<option value="live" selected>Live data · {len(live_rows)}</option>
<option value="other">Fixtures, legacy &amp; planned · {len(other_rows)}</option>
</select></div><p id="data-status-group-help">Live data includes active datasets even when they need attention. Counts below apply to the selected group.</p></div>
<p class="inspector-schedule-explanation">As-of Date is the source reference date; Latest Successful Fetch records a successful source request or collector completion; a later processing failure is shown separately. Retention uses successful capture evidence, including linked outputs and native ledgers; a successful fetch alone does not prove retention. Storage capture time and provenance remain in record details. Source update frequency and local timer cadence are separate.</p>
<noscript><p class="inspector-footnote">Both dataset groups and all supporting fields are shown while JavaScript is disabled.</p></noscript>
{groups}<p class="inspector-footnote">Timer schedules are read at page load. A planned trigger is not a successful fetch.</p>"""
    return render_inspector_shell(
        title="Data status", active="/data-status", revision=registry_revision,
        body=body,
        description="Source reference dates, recorded fetches, and retained evidence.",
        footer="Loopback only · read-only status, refresh evidence and local timer snapshot",
    )


__all__ = ("render_data_status_page",)
