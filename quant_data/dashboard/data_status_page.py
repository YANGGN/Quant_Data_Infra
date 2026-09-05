"""Escaped retained-data status page for the local Canonical Inspector."""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import QuantDataError
from ..json_codec import loads_strict


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


def render_data_status_page(
    result: Mapping[str, Any],
    *,
    registry_revision: str,
    error: str | None = None,
) -> str:
    """Render the public status result without exposing paths or raw errors."""

    rows = _rows(result)
    counts = Counter(str(row.get("status", "unknown")) for row in rows)
    table_rows = "".join(
        "<tr><td><strong>"
        + _escape(row.get("status", "unknown"))
        + "</strong><br><small>"
        + _escape(row.get("status_reason", "—"))
        + "</small></td><th scope=\"row\"><code>"
        + _escape(row.get("id", row.get("dataset_id", "—")))
        + "</code></th><td>"
        + _escape(row.get("store", "—"))
        + "</td><td>"
        + _escape(_timestamp(row.get("latest_successful_capture")))
        + "</td><td>"
        + _escape(_outcome(row.get("latest_retained_outcome")))
        + "</td><td>"
        + _escape(_reference(row.get("latest_reference_period")))
        + "</td><td>"
        + _escape(_threshold(row.get("freshness")))
        + "</td></tr>"
        for row in rows
    )
    if not table_rows:
        table_rows = '<tr><td colspan="7">No retained dataset status is available.</td></tr>'
    notice = (
        '<div class="state-notice warning" role="alert">'
        + _escape(error)
        + "</div>"
        if error
        else ""
    )
    navigation = (
        '<a href="/?view=market-prices">Data views</a>'
        '<a href="/data-status" aria-current="page">Data status</a>'
        '<a href="/news">Current news</a>'
        '<a href="/agent-tools">Agent Tools</a>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data status · Canonical Data Inspector</title><link rel="stylesheet" href="/assets/dashboard.css"></head>
<body><a class="skip-link" href="#main-content">Skip to content</a><div class="app-shell">
<header class="site-header"><a class="brand" href="/"><span class="brand-mark">QD</span><strong>Canonical Data Inspector</strong></a>
<p class="shell-status"><span aria-hidden="true">●</span> Local read-only · registry {_escape(registry_revision)}</p></header>
<nav class="primary-nav" aria-label="Inspector sections">{navigation}</nav>
<main id="main-content"><section class="page-intro"><p class="eyebrow">Retained control-plane evidence</p>
<h1>Data status</h1><p>Retained dataset/control-plane status only. This does not test providers, credentials, scheduler processes, or perform a fetch. “Current” means the latest retained successful-capture anchor is within the dataset’s declared threshold.</p></section>
{notice}<section class="metric-grid" aria-label="Dataset status summary">
<article class="metric"><p>Current</p><strong>{counts['current']}</strong><span>Within retained threshold</span></article>
<article class="metric"><p>Stale</p><strong>{counts['stale']}</strong><span>Retained anchor exceeded threshold</span></article>
<article class="metric"><p>No data</p><strong>{counts['no_data']}</strong><span>No retained successful capture</span></article>
<article class="metric"><p>Unknown</p><strong>{counts['unknown']}</strong><span>Status cannot be established</span></article></section>
<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Bounded read-only projection</p><h2>{len(rows)} status records</h2>
<p>Reference periods appear only where a fixed reviewed projection exists; none is inferred from generic ingestion metadata.</p></div><a href="/api/data-status">View JSON</a></div>
<div class="table-scroll" tabindex="0"><table><caption>Latest retained status by registered dataset or fixed news source</caption>
<thead><tr><th>Status</th><th>Dataset or source</th><th>Store</th><th>Last successful capture</th><th>Latest retained outcome</th><th>Reference period</th><th>Stale after</th></tr></thead>
<tbody>{table_rows}</tbody></table></div></section></main>
<footer class="site-footer"><p>Loopback only · retained status · no provider or scheduler probe</p></footer></div></body></html>"""


__all__ = ("render_data_status_page",)
