"""Local, escaped HTML presentation primitives for the Stage 6 dashboard.

The module deliberately has no knowledge of store paths, SQL, collectors, or
writer services.  It accepts already bounded read-service mappings and renders
them with a shared, local-only application shell.  ``render_shell`` accepts
ordinary strings for text values; only the module's private ``_TrustedHtml``
fragments are allowed to carry markup into the document body.
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from quant_data.json_codec import dumps_strict


_NAVIGATION: tuple[tuple[str, str], ...] = (
    ("/", "Overview"),
    ("/gdp-vintages", "GDP Vintages"),
    ("/table-inspector", "Tables"),
    ("/agent-tools", "Agent Tools"),
)
_GDP_SERIES = (("real-growth", "Real GDP growth"), ("nominal-gdp", "Nominal GDP"))
_GDP_MODES = (("latest", "Latest"), ("as_of", "As of"), ("first_release", "First release"))
_DATE_POLICIES = (
    ("completed_date", "Completed date"),
    ("calendar_date_inclusive", "Calendar date inclusive"),
)
_TABLE_VIEWS = (
    ("market-prices", "Market prices"),
    ("macro-observations", "Macro observations"),
    ("company-filings", "Company filings"),
    ("news-items", "News items"),
)
_RUNNER_STATES = frozenset({"ok", "succeeded", "not_established", "capability_unavailable"})


class _TrustedHtml(str):
    """A private marker for fragments assembled only from escaped helpers."""


def _trusted(value: str) -> _TrustedHtml:
    return _TrustedHtml(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = value.get("result")
    return nested if isinstance(nested, Mapping) else value


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return ()


def _display(value: Any) -> str:
    """Create bounded display text without relying on unsafe ``repr`` values."""

    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and not math.isfinite(value):
        return "Unavailable"
    if isinstance(value, Decimal) and not value.is_finite():
        return "Unavailable"
    if isinstance(value, (str, int, float, Decimal)):
        return str(value)
    if isinstance(value, Mapping) or isinstance(value, (list, tuple)):
        try:
            return dumps_strict(value)
        except Exception:
            return "Unavailable"
    return str(value)


def _text(value: Any) -> str:
    return html.escape(_display(value), quote=True)


def _attribute(value: Any) -> str:
    return html.escape(_display(value), quote=True)


def _safe_json(value: Any) -> str:
    """Render strict JSON only after the established encoder accepts it."""

    try:
        return _text(dumps_strict(value))
    except Exception:
        return _text({"state": "unavailable", "reason": "strict_json_rejected"})


def _selected(value: Any, allowed: tuple[tuple[str, str], ...], default: str) -> str:
    candidate = value if isinstance(value, str) else default
    return candidate if candidate in {item[0] for item in allowed} else default


def _options(
    choices: Sequence[tuple[str, str]], selected: str, *, disabled_placeholder: str | None = None
) -> str:
    if not choices and disabled_placeholder is not None:
        return f'<option value="" selected disabled>{_text(disabled_placeholder)}</option>'
    return "".join(
        "<option value=\""
        + _attribute(value)
        + "\""
        + (" selected" if value == selected else "")
        + ">"
        + _text(label)
        + "</option>"
        for value, label in choices
    )


def _summary_count(value: Any) -> int:
    return len(_sequence(value))


def _warnings(value: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    entries: list[str] = []
    for source in (value.get("warnings"), payload.get("warnings"), value.get("warning_codes")):
        for warning in _sequence(source):
            if isinstance(warning, Mapping):
                code = warning.get("code")
                message = warning.get("message", warning.get("reason", "Warning"))
                entries.append(
                    "<li><strong>"
                    + _text(code if code is not None else "Warning")
                    + "</strong> — "
                    + _text(message)
                    + "</li>"
                )
            else:
                entries.append("<li>" + _text(warning) + "</li>")
    truncation = payload.get("truncation", value.get("truncation"))
    if isinstance(truncation, Mapping) and bool(truncation.get("truncated")):
        entries.append("<li><strong>Truncated</strong> — The service returned its bounded result.</li>")
    elif bool(payload.get("truncated", value.get("truncated", False))):
        entries.append("<li><strong>Truncated</strong> — The service returned its bounded result.</li>")
    if not entries:
        return ""
    return (
        '<section class="state-notice state-warning" data-state="warning" aria-labelledby="warnings-title">'
        '<h2 id="warnings-title">Warnings and result limits</h2><ul>'
        + "".join(entries)
        + "</ul></section>"
    )


def _state_notice(value: Mapping[str, Any], payload: Mapping[str, Any], *, has_rows: bool) -> str:
    error = payload.get("error", value.get("error"))
    explicit_state = payload.get("state", value.get("state"))
    if isinstance(error, Mapping) or isinstance(error, str):
        if isinstance(error, Mapping):
            detail = error.get("message", error.get("code", "The read service was unavailable."))
        else:
            detail = error
        return (
            '<section class="state-notice state-error" data-state="error" role="alert">'
            "<h2>Read service unavailable</h2><p>"
            + _text(detail)
            + "</p></section>"
        )
    if explicit_state == "loading":
        return (
            '<section class="state-notice state-loading" data-state="loading" aria-live="polite">'
            "<h2>Loading bounded read result</h2><p>Waiting for the local read service.</p></section>"
        )
    if explicit_state in {"partial", "warning"}:
        return (
            '<section class="state-notice state-warning" data-state="partial" aria-live="polite">'
            "<h2>Partial result</h2><p>Review warnings, provenance, and any truncation before use.</p></section>"
        )
    if explicit_state in {"unavailable", "capability_unavailable"}:
        return (
            '<section class="state-notice state-unavailable" data-state="unavailable" aria-live="polite">'
            "<h2>Capability unavailable</h2><p>The requested read surface is not established in this fixture scope.</p></section>"
        )
    if not has_rows:
        return (
            '<section class="state-notice state-empty" data-state="empty" aria-live="polite">'
            "<h2>No matching rows</h2><p>No values are substituted for an empty result.</p></section>"
        )
    return (
        '<section class="state-notice state-ready" data-state="ready" aria-live="polite">'
        "<h2>Bounded read result</h2><p>Rows retain the service’s explicit availability and provenance fields.</p></section>"
    )


def _row_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for item in _sequence(value):
        if isinstance(item, Mapping):
            rows.append(item)
        else:
            rows.append({"value": item})
    return tuple(rows)


def _first_rows(value: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    for source in (payload, value):
        for key in ("rows", "observations", "records", "items", "stores"):
            rows = _row_mappings(source.get(key))
            if rows:
                return rows
    return ()


def _columns(value: Mapping[str, Any], payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    raw_columns = payload.get("columns", value.get("columns"))
    columns: list[tuple[str, str]] = []
    for item in _sequence(raw_columns):
        if isinstance(item, Mapping):
            name = item.get("name", item.get("field"))
            label = item.get("label", item.get("title", name))
        else:
            name = item
            label = item
        if isinstance(name, str) and name and not any(existing[0] == name for existing in columns):
            columns.append((name, label if isinstance(label, str) and label else name))
    if columns:
        return tuple(columns)
    for row in rows:
        for key in row:
            if isinstance(key, str) and key and not any(existing[0] == key for existing in columns):
                columns.append((key, key.replace("_", " ").title()))
    return tuple(columns)


def _is_numeric_column(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("value", "count", "price", "rate", "volume", "amount", "page"))


def _data_table(
    *,
    table_id: str,
    value: Mapping[str, Any],
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    caption: str,
) -> str:
    columns = _columns(value, payload, rows)
    if not columns:
        return ""
    header = "".join(
        '<th scope="col"' + (' class="numeric"' if _is_numeric_column(name) else "") + ">"
        + _text(label)
        + "</th>"
        for name, label in columns
    )
    body_rows = "".join(
        "<tr>"
        + "".join(
            "<td" + (' class="numeric"' if _is_numeric_column(name) else "") + ">"
            + _text(row.get(name))
            + "</td>"
            for name, _label in columns
        )
        + "</tr>"
        for row in rows
    )
    return (
        '<div class="table-scroll" tabindex="0" aria-label="Scrollable result table">'
        f'<table id="{_attribute(table_id)}"><caption>{_text(caption)}</caption><thead><tr>{header}</tr></thead>'
        f"<tbody>{body_rows}</tbody></table></div>"
    )


def _provenance(value: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    candidates: list[Mapping[str, Any]] = []
    for source in (payload.get("receipt"), value.get("receipt"), payload.get("audit"), value.get("audit")):
        if isinstance(source, Mapping):
            candidates.append(source)
    fields = (
        ("registry_revision", "Registry revision"),
        ("route", "Read route"),
        ("mode", "Selection mode"),
        ("as_of", "Availability cutoff"),
        ("cutoff", "Availability cutoff"),
        ("date_only_policy", "Date-only policy"),
        ("available_at", "Available at"),
        ("captured_at", "Captured at"),
        ("source", "Source"),
    )
    entries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for key, label in fields:
            if key in candidate and key not in seen:
                seen.add(key)
                entries.append("<dt>" + _text(label) + "</dt><dd>" + _text(candidate[key]) + "</dd>")
    if not entries:
        return ""
    return (
        '<section class="provenance" aria-labelledby="provenance-title">'
        '<h2 id="provenance-title">Provenance and query context</h2><dl>'
        + "".join(entries)
        + "</dl></section>"
    )


def _panel(*, panel_id: str, kicker: str, title: str, description: str, content: str) -> str:
    return (
        f'<section id="{_attribute(panel_id)}" class="panel" aria-labelledby="{_attribute(panel_id)}-title">'
        '<header class="panel-header"><p class="panel-kicker">'
        + _text(kicker)
        + f'</p><h2 id="{_attribute(panel_id)}-title">'
        + _text(title)
        + "</h2><p>"
        + _text(description)
        + "</p></header>"
        + content
        + "</section>"
    )


def render_shell(*, title: str, active_route: str, eyebrow: str, body_html: str) -> str:
    """Render the shared local-only application shell.

    Public callers may pass plain text for ``body_html`` and it will be
    escaped.  The page renderers below pass the internal marker class after
    composing semantic markup from escaped values.
    """

    navigation = "".join(
        '<a href="'
        + route
        + '"'
        + (' aria-current="page"' if route == active_route else "")
        + ">"
        + label
        + "</a>"
        for route, label in _NAVIGATION
    )
    safe_body = body_html if isinstance(body_html, _TrustedHtml) else _trusted("<p>" + _text(body_html) + "</p>")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        "<title>"
        + _text(title)
        + " · Quant Data Infrastructure</title>"
        '<link rel="preload" href="/assets/inter-variable.woff2" as="font" type="font/woff2" crossorigin>'
        '<link rel="stylesheet" href="/assets/dashboard.css">'
        '<script defer src="/assets/dashboard.js"></script></head><body>'
        '<a class="skip-link" href="#main-content">Skip to main content</a>'
        '<div class="app-shell"><header class="site-header"><a class="brand" href="/" aria-label="Quant Data Infrastructure home">'
        '<span class="brand-mark" aria-hidden="true">QD</span><span>Quant Data <strong>Infrastructure</strong></span></a>'
        '<p class="shell-status"><span aria-hidden="true">●</span> Local read-only workspace</p></header>'
        '<nav class="primary-nav" aria-label="Primary navigation">'
        + navigation
        + '</nav><main id="main-content" tabindex="-1" data-route="'
        + _attribute(active_route)
        + '"><header class="page-intro"><p class="eyebrow">'
        + _text(eyebrow)
        + "</p><h1>"
        + _text(title)
        + "</h1></header>"
        + safe_body
        + '</main><footer class="site-footer"><p>Local inspection surface · bounded read services only</p></footer></div></body></html>'
    )


def render_overview_page(context: Mapping[str, Any]) -> str:
    """Render an operational overview without manufacturing missing values."""

    payload = _payload(context)
    health = _mapping(context.get("health")) or _mapping(payload.get("health")) or payload
    stores = _row_mappings(health.get("stores", context.get("stores")))
    healthy = sum(1 for store in stores if store.get("status") == "ok")
    unavailable = sum(1 for store in stores if store.get("status") in {"unavailable", "unhealthy"})
    datasets = sum(_summary_count(store.get("datasets")) for store in stores)
    metrics = (
        ("Operational stores", len(stores), "Registered local store boundaries"),
        ("Healthy stores", healthy, "Integrity result reported by the read service"),
        ("Unavailable or unhealthy", unavailable, "Requires an explicit service recovery"),
        ("Registered datasets", datasets, "Dataset registrations visible to this surface"),
    )
    metric_markup = "".join(
        '<article class="metric"><p>' + _text(label) + "</p><strong>" + _text(value) + "</strong><span>" + _text(note) + "</span></article>"
        for label, value, note in metrics
    )
    store_rows = tuple(
        {
            "role": store.get("role"),
            "status": store.get("status"),
            "integrity": store.get("integrity"),
            "datasets": _summary_count(store.get("datasets")),
            "applied migrations": ", ".join(_display(item) for item in _sequence(store.get("migration_ids"))) or "—",
        }
        for store in stores
    )
    store_payload: Mapping[str, Any] = {
        "columns": (
            {"name": "role", "label": "Store"},
            {"name": "status", "label": "Status"},
            {"name": "integrity", "label": "Integrity"},
            {"name": "datasets", "label": "Datasets"},
            {"name": "applied migrations", "label": "Applied migrations"},
        )
    }
    body = (
        '<section class="workspace-note" aria-label="Read boundary"><p><strong>Read-only boundary.</strong> '
        "This portal has no ingestion, raw query, arbitrary path, or write control.</p></section>"
        '<section class="metric-grid" aria-label="Operational summary">'
        + metric_markup
        + "</section>"
        + _state_notice(context, payload, has_rows=bool(stores))
        + _panel(
            panel_id="store-health",
            kicker="Operational inspection",
            title="Store health",
            description="Health is reported by fixed, query-only local read services.",
            content=_data_table(
                table_id="store-health-table",
                value=store_payload,
                payload=store_payload,
                rows=store_rows,
                caption="Registered operational stores",
            )
            or '<p class="empty-copy">No store health rows were returned.</p>',
        )
        + _warnings(context, payload)
        + _provenance(context, payload)
    )
    return render_shell(
        title="Overview",
        active_route="/",
        eyebrow="Local research workspace",
        body_html=_trusted(body),
    )


def render_gdp_vintages_page(result: Mapping[str, Any]) -> str:
    """Render the bounded GDP vintage query form and its read-only result."""

    payload = _payload(result)
    query = _mapping(payload.get("query")) or _mapping(result.get("query"))
    series = _selected(query.get("series"), _GDP_SERIES, "real-growth")
    mode = _selected(query.get("mode"), _GDP_MODES, "latest")
    policy = _selected(query.get("date_only_policy"), _DATE_POLICIES, "completed_date")
    as_of = query.get("as_of") if isinstance(query.get("as_of"), str) else ""
    limit = query.get("limit", 25)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        limit = 25
    rows = _first_rows(result, payload)
    form = (
        '<form id="gdp-vintages-form" class="query-form" action="/api/gdp-vintages" method="get" data-dashboard-form="gdp-vintages">'
        '<fieldset><legend>GDP vintage read query</legend><div class="form-grid">'
        '<label for="gdp-series">Series<select id="gdp-series" name="series">'
        + _options(_GDP_SERIES, series)
        + "</select></label>"
        '<label for="gdp-mode">Vintage mode<select id="gdp-mode" name="mode" data-as-of-mode>'
        + _options(_GDP_MODES, mode)
        + "</select></label>"
        '<label for="gdp-as-of">Availability cutoff <span class="field-note">ISO date or aware timestamp for as-of mode</span>'
        '<input id="gdp-as-of" name="as_of" type="text" inputmode="text" maxlength="64" placeholder="2026-07-09"'
        + (' disabled' if mode != "as_of" else "")
        + ' data-as-of-input value="'
        + _attribute(as_of)
        + '"></label>'
        '<label for="gdp-policy">Date-only policy<select id="gdp-policy" name="date_only_policy">'
        + _options(_DATE_POLICIES, policy)
        + "</select></label>"
        '<label for="gdp-limit">Row limit<input id="gdp-limit" name="limit" type="number" min="1" max="100" step="1" required value="'
        + _attribute(limit)
        + '"></label></div><div class="form-actions"><button type="submit">Run bounded read</button>'
        '<p class="form-bound">Maximum 100 rows. The cutoff is active only in as-of mode.</p></div></fieldset></form>'
    )
    table = _data_table(
        table_id="gdp-vintages-table",
        value=result,
        payload=payload,
        rows=rows,
        caption="GDP vintage observations returned by the local read service",
    )
    result_content = (
        _state_notice(result, payload, has_rows=bool(rows))
        + (table or '<p class="empty-copy">Run a bounded GDP vintage query to inspect returned observations.</p>')
        + '<output id="gdp-vintages-live-result" class="runner-output" data-dashboard-result aria-live="polite">'
        "A progressive-enhancement result will appear here after a local read request."
        "</output>"
    )
    body = (
        _panel(
            panel_id="gdp-query",
            kicker="Macro · bounded query",
            title="GDP vintages",
            description="Choose a declared series and vintage policy; missing observations remain missing.",
            content=form,
        )
        + _panel(
            panel_id="gdp-result",
            kicker="Read receipt",
            title="Returned observations",
            description="Availability cutoff, warnings, and provenance remain visible alongside the data.",
            content=result_content,
        )
        + _warnings(result, payload)
        + _provenance(result, payload)
    )
    return render_shell(
        title="GDP Vintages",
        active_route="/gdp-vintages",
        eyebrow="Macro observation history",
        body_html=_trusted(body),
    )


def _sort_choices(value: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    raw = payload.get("available_sorts", value.get("available_sorts"))
    choices: list[tuple[str, str]] = []
    for item in _sequence(raw):
        if isinstance(item, Mapping):
            name = item.get("value", item.get("name", item.get("field")))
            label = item.get("label", item.get("title", name))
        else:
            name = item
            label = item
        if isinstance(name, str) and name and not any(existing[0] == name for existing in choices):
            choices.append((name, label if isinstance(label, str) and label else name))
    return tuple(choices)


def render_table_inspector_page(result: Mapping[str, Any]) -> str:
    """Render only server-declared table views, filters, and sort choices."""

    payload = _payload(result)
    query = _mapping(payload.get("query")) or _mapping(result.get("query"))
    view = _selected(query.get("view"), _TABLE_VIEWS, "market-prices")
    search = query.get("search") if isinstance(query.get("search"), str) else ""
    sort_choices = _sort_choices(result, payload)
    sort_default = sort_choices[0][0] if sort_choices else ""
    sort = _selected(query.get("sort"), sort_choices, sort_default) if sort_choices else ""
    direction = "desc" if query.get("direction") == "desc" else "asc"
    page = query.get("page", 1)
    limit = query.get("limit", 25)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 100:
        page = 1
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        limit = 25
    rows = _first_rows(result, payload)
    view_label = dict(_TABLE_VIEWS)[view]
    sort_control = (
        '<label for="table-sort">Sort<select id="table-sort" name="sort">'
        + _options(sort_choices, sort, disabled_placeholder="Sort choices load with the selected view")
        + "</select></label>"
        if sort_choices
        else '<label for="table-sort">Sort<select id="table-sort" name="sort" disabled>'
        + _options((), "", disabled_placeholder="Sort choices load with the selected view")
        + "</select></label>"
    )
    form = (
        '<form id="table-inspector-form" class="query-form" action="/api/table-inspector" method="get" data-dashboard-form="table-inspector">'
        '<fieldset><legend>Declared table view query</legend><div class="form-grid">'
        '<label for="table-view">View<select id="table-view" name="view">'
        + _options(_TABLE_VIEWS, view)
        + "</select></label>"
        '<label for="table-search">Search <span class="field-note">Maximum 128 characters</span>'
        '<input id="table-search" name="search" type="search" maxlength="128" value="'
        + _attribute(search[:128])
        + '"></label>'
        + sort_control
        + '<label for="table-direction">Direction<select id="table-direction" name="direction">'
        + _options((("asc", "Ascending"), ("desc", "Descending")), direction)
        + "</select></label>"
        '<label for="table-page">Page<input id="table-page" name="page" type="number" min="1" max="100" step="1" required value="'
        + _attribute(page)
        + '"></label>'
        '<label for="table-limit">Row limit<input id="table-limit" name="limit" type="number" min="1" max="100" step="1" required value="'
        + _attribute(limit)
        + '"></label></div><div class="form-actions"><button type="submit">Inspect bounded rows</button>'
        '<p class="form-bound">Only registry-declared views, fields, filters, and sort choices are available.</p></div></fieldset></form>'
    )
    table = _data_table(
        table_id="table-inspector-result",
        value=result,
        payload=payload,
        rows=rows,
        caption=f"{view_label} rows returned by the bounded inspector",
    )
    result_content = (
        _state_notice(result, payload, has_rows=bool(rows))
        + (table or '<p class="empty-copy">No declared rows matched this bounded query.</p>')
        + '<output id="table-inspector-live-result" class="runner-output" data-dashboard-result aria-live="polite">'
        "A progressive-enhancement result will appear here after a local read request."
        "</output>"
    )
    body = (
        _panel(
            panel_id="table-query",
            kicker="Operational inspection",
            title="Tables",
            description="Inspect a finite, server-owned relation view without raw query or path input.",
            content=form,
        )
        + _panel(
            panel_id="table-result",
            kicker="Declared result",
            title=view_label,
            description="Columns and sort choices come from the selected view’s read contract.",
            content=result_content,
        )
        + _warnings(result, payload)
        + _provenance(result, payload)
    )
    return render_shell(
        title="Tables",
        active_route="/table-inspector",
        eyebrow="Bounded operational inspection",
        body_html=_trusted(body),
    )


def _tool_availability(tool: Mapping[str, Any]) -> str:
    live_capability = _mapping(tool.get("live_capability"))
    if (
        live_capability.get("possible") is True
        and live_capability.get("offline_status") == "disabled"
    ):
        return "capability_unavailable"
    policy = _mapping(tool.get("availability_policy"))
    return _display(policy.get("point_in_time_default", tool.get("status")))


def _runner_status(manifest: Mapping[str, Any]) -> tuple[str, str, str]:
    candidate = manifest.get("runner_result", manifest.get("last_result"))
    if not isinstance(candidate, Mapping):
        return ("idle", "No tool request has been run in this view.", "")
    payload = _payload(candidate)
    receipt = _mapping(candidate.get("receipt"))
    status = payload.get("status", candidate.get("status", receipt.get("outcome")))
    if isinstance(status, str) and status in _RUNNER_STATES:
        return (status, f"Result status: {status}", _safe_json(candidate))
    error = candidate.get("error", payload.get("error"))
    if isinstance(error, Mapping):
        return ("error", _display(error.get("message", error.get("code", "Read request failed."))), _safe_json(candidate))
    return ("idle", "No established result status was returned.", _safe_json(candidate))


def render_agent_tools_page(manifest: Mapping[str, Any]) -> str:
    """Render all manifest-declared public tools and a bounded runner form."""

    payload = _payload(manifest)
    tools = _row_mappings(payload.get("tools", manifest.get("tools")))
    api_version = payload.get("api_version", manifest.get("api_version", "1.0"))
    api_version = api_version if isinstance(api_version, str) and api_version else "1.0"
    manifest_rows = "".join(
        "<tr><th scope=\"row\"><code>"
        + _text(tool.get("name"))
        + "</code></th><td>"
        + _text(tool.get("version"))
        + "</td><td>"
        + _text(tool.get("family"))
        + "</td><td>"
        + _text(_tool_availability(tool))
        + "</td><td>"
        + _text(tool.get("read_only"))
        + "</td></tr>"
        for tool in tools
    )
    tool_options = "".join(
        '<option value="'
        + _attribute(tool.get("name"))
        + '" data-example="'
        + _attribute(
            dumps_strict(_sequence(tool.get("examples"))[0])
            if _sequence(tool.get("examples"))
            else "{}"
        )
        + '">'
        + _text(tool.get("name"))
        + "</option>"
        for tool in tools
        if isinstance(tool.get("name"), str) and tool.get("name")
    )
    runner_state, runner_message, runner_json = _runner_status(manifest)
    runner_disabled = " disabled" if not tool_options else ""
    runner = (
        '<form id="agent-tools-runner" class="query-form tool-runner" action="/api/agent-tools/call" method="post" data-dashboard-runner>'
        '<fieldset><legend>Bounded read-only tool runner</legend><div class="form-grid">'
        '<label for="tool-name">Public tool<select id="tool-name" name="tool" required'
        + runner_disabled
        + ">"
        + (tool_options or '<option value="" selected>No public tools available</option>')
        + "</select></label>"
        '<label for="tool-arguments">Arguments <span class="field-note">Strict JSON object</span>'
        '<textarea id="tool-arguments" name="arguments" rows="6" maxlength="65536" required'
        + runner_disabled
        + ">{}</textarea></label>"
        '<input type="hidden" name="api_version" value="'
        + _attribute(api_version)
        + '"></div><div class="form-actions"><button type="submit"'
        + runner_disabled
        + ">Run read-only tool</button>"
        '<p class="form-bound">The browser can call only the manifest endpoint and this bounded public runner.</p>'
        "</div></fieldset></form>"
        '<output id="agent-tools-live-result" class="runner-output" data-dashboard-runner-result data-state="'
        + _attribute(runner_state)
        + '" aria-live="polite"><strong>'
        + _text(runner_message)
        + "</strong>"
        + ("<pre>" + runner_json + "</pre>" if runner_json else "")
        + "</output>"
    )
    manifest_table = (
        '<div class="table-scroll" tabindex="0" aria-label="Scrollable public tool manifest"><table id="agent-tools-manifest">'
        '<caption>Public read-only tool manifest</caption><thead><tr><th>Tool</th><th>Version</th><th>Family</th>'
        "<th>Availability policy</th><th>Read only</th></tr></thead><tbody>"
        + manifest_rows
        + "</tbody></table></div>"
        if tools
        else '<p class="empty-copy">No public tool declarations were returned.</p>'
    )
    body = (
        _panel(
            panel_id="tool-runner",
            kicker="Manifest-generated read request",
            title="Agent Tools",
            description="Tools are declared by the local manifest; unavailable semantics remain explicitly unavailable.",
            content=runner,
        )
        + _panel(
            panel_id="tool-manifest",
            kicker="Public contract inventory",
            title=f"{len(tools)} declared tools",
            description="The list is generated from the local read-only manifest, not a hand-maintained UI copy.",
            content=manifest_table,
        )
        + _warnings(manifest, payload)
        + _provenance(manifest, payload)
    )
    return render_shell(
        title="Agent Tools",
        active_route="/agent-tools",
        eyebrow="Composable public read contracts",
        body_html=_trusted(body),
    )
