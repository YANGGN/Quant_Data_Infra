"""Presentation-only shell and tables for the current Canonical Inspector."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode


INSPECTOR_NAVIGATION = (
    ("Market", (
        ("market-prices", "Market prices"),
        ("market-instruments", "Market symbols"),
        ("options-surfaces", "Options surfaces"),
        ("spy-options", "SPY options"),
    )),
    ("Macro · observations", (
        ("macro-current", "Macro current"),
        ("macro-vintages", "Macro vintages"),
        ("macro-surprises", "Release surprises"),
        ("price-wage-productivity", "Prices, wages & productivity"),
        ("personal-income-outlays", "Personal income & outlays"),
        ("industrial-production", "Industrial production"),
        ("national-activity", "National activity"),
        ("recession-chronology", "Recession chronology"),
        ("fmp-economic-calendar", "Raw FMP calendar"),
    )),
    ("Macro · rates & liquidity", (
        ("treasury-curve", "Treasury curve"),
        ("overnight-rates", "Overnight rates"),
        ("policy-rates", "Policy rates"),
        ("repo-facilities", "Repo facilities"),
        ("soma-summary", "SOMA summary"),
        ("h41-liquidity", "Fed H.4.1 liquidity"),
        ("treasury-cash", "Treasury cash balance"),
        ("treasury-debt", "Treasury debt"),
        ("fiscal-balance", "Federal fiscal balance"),
    )),
    ("Macro · credit & conditions", (
        ("financial-conditions", "Financial conditions"),
        ("bis-credit", "BIS credit conditions"),
        ("credit-market-distress", "Corporate bond distress"),
    )),
    ("Macro · energy", (
        ("natural-gas-storage", "Natural gas storage"),
        ("crude-oil-stocks", "Crude oil stocks"),
        ("petroleum-fundamentals", "Petroleum fundamentals"),
        ("electricity-retail", "Electricity retail"),
    )),
    ("Company & news", (
        ("company-fundamentals", "Company fundamentals"),
        ("/news", "Current news"),
    )),
    ("Workspace", (
        ("/data-status", "Data status"),
        ("/agent-tools", "Agent Tools"),
    )),
)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _navigation(active: str) -> tuple[str, str]:
    groups = []
    section = "Inspector"
    for label, destinations in INSPECTOR_NAVIGATION:
        selected = any(key == active for key, _ in destinations)
        if selected:
            section = label
        links = []
        for key, title in destinations:
            href = key if key.startswith("/") else "/?" + urlencode({"view": key})
            current = ' aria-current="page"' if key == active else ""
            links.append(f'<a href="{_escape(href)}"{current}>{_escape(title)}</a>')
        opened = " open" if selected or label in {"Market", "Workspace"} else ""
        groups.append(
            f'<details class="inspector-nav-group"{opened}>'
            f'<summary>{_escape(label)}</summary><div>{"".join(links)}</div></details>'
        )
    return "".join(groups), section


def render_inspector_shell(
    *, title: str, active: str, revision: str, body: str, description: str,
    eyebrow: str = "", footer: str = "Local · read-only · bounded queries",
    tools: bool = False,
) -> str:
    """Compose trusted renderer markup with escaped public shell labels."""
    navigation, section = _navigation(active)
    extra_assets = (
        '<link rel="stylesheet" href="/assets/inspector-tools.css">'
        '<script defer src="/assets/inspector-tools.js"></script>'
        if tools else ""
    )
    kicker = f'<p class="eyebrow">{_escape(eyebrow)}</p>' if eyebrow else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{_escape(title)} · Canonical Data Inspector</title>
<link rel="preload" href="/assets/inter-variable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/dashboard.css">{extra_assets}
<link rel="stylesheet" href="/assets/inspector.css"><script defer src="/assets/inspector.js"></script></head>
<body class="inspector"><a class="skip-link" href="#main-content">Skip to content</a>
<div class="inspector-shell"><aside class="inspector-sidebar" aria-label="Inspector navigation">
<a class="inspector-brand" href="/?view=market-prices"><span class="brand-mark" aria-hidden="true">QD</span><span>Canonical Data<strong>Inspector</strong></span></a>
<button class="inspector-nav-toggle" data-inspector-nav-toggle aria-controls="inspector-navigation" aria-expanded="true" type="button" hidden>Browse data</button>
<nav id="inspector-navigation" aria-label="Inspector views">{navigation}</nav>
<p class="inspector-sidebar-note">Local · read-only<br><span>Registry {_escape(revision)}</span></p></aside>
<div class="inspector-workspace"><header class="inspector-topbar"><span class="inspector-breadcrumb">{_escape(section)}<span aria-hidden="true">/</span><strong>{_escape(title)}</strong></span><span class="inspector-readonly">Read-only</span></header>
<main id="main-content"><header class="page-intro">{kicker}<h1>{_escape(title)}</h1><p>{_escape(description)}</p></header>{body}</main>
<footer class="site-footer"><p>{_escape(footer)}</p></footer></div></div></body></html>'''


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "number"
    if value == "":
        return "empty-string"
    return "text"


def _value_markup(value: Any) -> str:
    if value is None:
        return '<span class="inspector-null" aria-label="Missing value (null)">—</span>'
    if value == "":
        return '<span class="inspector-empty-string" aria-label="Empty string">""</span>'
    return _escape(value)


_NUMERIC_COLUMNS = frozenset({
    "open", "high", "low", "close", "volume", "value", "value_text", "bid", "ask",
    "mid", "strike", "delta", "gamma", "theta", "vega", "rho", "yield", "rate",
    "open_interest", "implied_volatility", "target_dte", "actual_dte", "amount",
    "quantity", "correction_sequence", "article_count",
})


def render_inspector_table(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], *, caption: str,
    empty_message: str = "No rows match this selection.",
) -> str:
    """Render every original field; enhancement reads escaped cell text only."""
    header = "".join(
        '<th scope="col"' + (' class="numeric"' if column in _NUMERIC_COLUMNS else '')
        + f'>{_escape(column.replace("_", " ").title())}</th>'
        for column in columns
    )
    rendered_rows = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        cells = []
        for column in columns:
            value = row.get(column)
            kind = _value_kind(value)
            classes = ' class="numeric"' if kind == "number" or column in _NUMERIC_COLUMNS else ""
            cells.append(
                f'<td data-field="{_escape(column)}" data-value-kind="{kind}"{classes}>'
                + _value_markup(value) + '</td>'
            )
        rendered_rows.append('<tr>' + ''.join(cells) + '</tr>')
    body = ''.join(rendered_rows) or (
        f'<tr><td colspan="{max(1, len(columns))}" class="inspector-empty">'
        f'{_escape(empty_message)}</td></tr>'
    )
    return (
        '<div class="inspector-table-workspace" data-inspector-table-workspace>'
        '<div class="table-scroll" tabindex="0" role="region" '
        f'aria-label="{_escape(caption)}"><table data-inspector-table>'
        f'<caption>{_escape(caption)}</caption><thead><tr>{header}</tr></thead>'
        f'<tbody>{body}</tbody></table></div></div>'
    )
