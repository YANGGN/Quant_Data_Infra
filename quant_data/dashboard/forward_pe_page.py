"""Bounded, read-only dashboard over an explicitly selected research export."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Mapping

from ..json_codec import dumps_strict

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from .inspector_shell import render_inspector_shell

RANGES = {"1y": "1 year", "5y": "5 years", "all": "All history"}
MAX_DAYS = 30_000


def parse_selection(query: Mapping[str, str]) -> tuple[str, str]:
    if set(query) - {"symbol", "range", "window", "winsor"}:
        raise ValidationError("Forward P/E accepts ticker, history, rolling window and tail-cap selections")
    symbol = query.get("symbol", "AAPL").strip().upper()
    period = query.get("range", "5y")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}", symbol) or period not in RANGES:
        raise ValidationError("Choose a valid ticker and date range")
    parse_analysis_selection(query)
    return symbol, period


def parse_analysis_selection(query):
    window = query.get("window", "756")
    tail = query.get("winsor", "2.5")
    if window not in ("252", "504", "756", "1260") or tail not in ("0", "1", "2.5", "5"):
        raise ValidationError("Choose a supported rolling window and tail cap")
    return int(window), float(tail)


def analysis_tool_view(result):
    """Render exactly the registered tool's values; no browser-side statistics."""
    records = [(r["record_type"], {f["name"]: f["value"] for f in r["fields"]}) for r in result["records"]]
    summary = records[0][1]
    days, statistics, windows = [], [], {}
    for kind, row in records[1:]:
        if kind == "forward_pe_analysis_chunk":
            columns = json.loads(row["columns_json"])
            for values in json.loads(row["rows_json"]):
                day = dict(zip(columns, values, strict=True))
                days.append([day["trade_date"], day["close"], day["forward_eps"], day["raw_pe"], day["status"], day["window_id"], day["winsorized_pe"], day["z_score"], day["raw_z_score"]])
                statistics.append(day)
        elif kind == "forward_pe_window":
            windows[row["window_id"]] = json.loads(row["details_json"])
    return dict(days=days, statistics=statistics, windows=windows,
                symbols=json.loads(summary["symbols_json"]), cutoff=summary["snapshot_cutoff"],
                snapshot_id=summary["snapshot_id"], latest_price_date=summary["latest_price_date"],
                message=summary["message"], refresh=json.loads(summary["refresh_json"]),
                ticker_refresh=json.loads(summary["ticker_refresh_json"]),
                analysis={k: summary[k] for k in ("window_sessions", "min_observations", "winsor_tail_pct", "model_version")})


def _z_reason(row):
    return {"insufficient_history": "Building history", "missing_pe": "P/E unavailable", "zero_variance": "No variation in window"}.get(row.get("z_score_reason"), "Relative to trailing P/E")


def read_forward_pe(root: Path, symbol: str, period: str) -> dict:
    from ..company.forward_pe_reader import read_forward_pe as read
    result = read(root, symbol, period)
    if len(result["days"]) > MAX_DAYS:
        raise ResourceLimitError("Forward P/E date range exceeds the display limit")
    return result


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object, suffix: str = "") -> str:
    return "Unavailable" if value is None else f"{float(value):,.2f}{suffix}"


def _pe_value(day) -> str:
    if day[3] is not None:
        return _number(day[3], "×")
    return "Undefined — zero EPS" if day[4] == "zero_forward_eps" else "Data unavailable"


def _status(day) -> str:
    if day[3] is None:
        return ("Undefined — zero EPS" if day[4] == "zero_forward_eps"
                else "Data unavailable · " + day[4].replace("_", " "))
    return "Expected loss" if day[3] < 0 else day[4].replace("_", " ").capitalize()


def _refresh_note(ticker: Mapping) -> str:
    freshness = ticker.get("input_freshness", {})
    labels = {"current": "Current", "stale": "Stale", "unconfirmed": "Freshness unconfirmed",
              "input_warning": "Other input warning", "warning": "Freshness unconfirmed",
              "waiting_inputs": "Waiting for inputs", "catchup_pending": "Catch-up pending"}
    if isinstance(freshness, Mapping) and freshness.get("state") in labels:
        label = labels[freshness["state"]]
        if freshness.get("unchanged"):
            label += " · successful unchanged source check"
        if freshness.get("note"):
            label += " · " + str(freshness["note"])
        return label
    outcome = ticker.get("outcome")
    if outcome == "stale_inputs":
        return "Freshness unconfirmed · older capture; recent source-check evidence is unavailable"
    if outcome == "waiting_inputs":
        return "Calculation waiting for readable inputs"
    if outcome == "catchup_pending":
        return "Calculation catch-up pending"
    return str(ticker.get("issue") or "")


def render_forward_pe_page(result: Mapping, *, revision: str, symbol: str,
                           period: str, error: str | None = None,
                           window_sessions: int = 756, winsor_tail_pct: float = 2.5) -> str:
    options = "".join(f'<option value="{_esc(ticker)}"></option>' for ticker in result.get("symbols", ()))
    ranges = "".join(f'<option value="{key}"{" selected" if key == period else ""}>{label}</option>'
                     for key, label in RANGES.items())
    window_options = "".join(f'<option value="{n}"{" selected" if n == window_sessions else ""}>{label}</option>' for n, label in ((252, "1 year · 252 sessions"), (504, "2 years · 504 sessions"), (756, "3 years · 756 sessions"), (1260, "5 years · 1,260 sessions")))
    tail_options = "".join(f'<option value="{n:g}"{" selected" if n == winsor_tail_pct else ""}>{"Off" if n == 0 else str(n)+"% each tail"}</option>' for n in (0, 1, 2.5, 5))
    body = f"""
<link rel="stylesheet" href="/assets/forward-pe.css">
<script defer src="/assets/forward-pe.js"></script>
<div class="fp-notice"><strong>Reconstructed estimates · research proxy</strong>
<p>The historical baseline uses reconstructed estimates, not the consensus known on those dates. Daily additions retain the inputs captured for each calculation;
they do not claim consensus known at the market close.
Currency and EPS share/split comparability remain unverified. Gaps are kept as unavailable.</p></div>
<form class="fp-controls" method="get" action="/forward-pe">
<label>Ticker<input name="symbol" list="fp-tickers" value="{_esc(symbol)}" maxlength="20" required autocomplete="off"></label>
<datalist id="fp-tickers">{options}</datalist>
<label>History<select name="range">{ranges}</select></label>
<label>Rolling window<select name="window">{window_options}</select></label>
<label>Extreme values<select name="winsor">{tail_options}</select></label>
<button type="submit">Show series</button>
<span class="fp-universe">{len(result.get("symbols", ())):,} tickers in snapshot</span>
</form>"""
    if error or result.get("message"):
        body += f'<section class="fp-card"><h2>Series unavailable</h2><p role="status">{_esc(error or result["message"])}</p></section>'
    else:
        days = result.get("days", ())
        last = days[-1]
        count = sum(day[3] is not None for day in days)
        negative = sum(day[3] is not None and day[3] < 0 for day in days)
        undefined = sum(day[4] == "zero_forward_eps" for day in days)
        unavailable = len(days) - count - undefined
        coverage = f"{negative:,} negative (expected loss)"
        if undefined:
            coverage += f" · {undefined:,} undefined (zero EPS)"
        if unavailable:
            coverage += f" · {unavailable:,} data unavailable"
        latest_status = _status(last)
        statistics = result.get("statistics", [])
        latest_statistics = statistics[-1] if statistics else {}
        z_count = sum(row.get("z_score") is not None for row in statistics)
        minimum = result.get("analysis", {}).get("min_observations", min(252, window_sessions))
        treatment = f"{winsor_tail_pct:g}% each tail capped" if winsor_tail_pct else "No tail caps"
        settings_text = f"{window_sessions:,} sessions · {treatment} · at least {minimum:,} usable observations"
        z_value = _number(latest_statistics.get("z_score"), "σ")
        z_reason = _z_reason(latest_statistics)

        refresh=result.get("refresh",{})
        refresh_text=("Daily refresh configured · 23:30 weekdays and 06:30 daily · Toronto"
                      if refresh.get("schedule",{}).get("enabled") else "Manual snapshot · daily refresh not active")
        ticker=result.get("ticker_refresh",{})
        ticker_note = _refresh_note(ticker)
        detail = " · " + ticker_note if ticker_note else ""
        if refresh.get("completed_at"):
            refresh_text+=" · Last check "+str(refresh["completed_at"])
        if refresh.get("state") in ("failed","running","interrupted"):
            refresh_text+=" · "+str(refresh["state"]).capitalize()
        body += f"""
<div class="fp-freshness"><span>Snapshot cutoff <strong>{_esc(result["cutoff"])}</strong></span>
<span>Latest saved price <strong>{_esc(result.get("latest_price_date") or "Unavailable")}</strong></span>
<span class="fp-manual">{_esc(refresh_text+detail)}</span></div>
<section class="fp-metrics" aria-label="Latest session values">
<div class="fp-card"><p>Raw forward P/E</p><strong class="fp-pe-value{' fp-pe-message' if last[3] is None else ''}">{_pe_value(last)}</strong><span>{_esc(latest_status)}</span></div>
<div class="fp-card"><p>Rolling P/E z-score</p><strong class="fp-z-value">{z_value}</strong><span>{_esc(z_reason)} · {latest_statistics.get("valid_count", 0):,} observations</span></div>
<div class="fp-card"><p>Next four quarters EPS</p><strong>{_number(last[2])}</strong><span>Sum of quarterly mean estimates</span></div>
<div class="fp-card"><p>Daily close</p><strong>{_number(last[1])}</strong><span>Latest session · {_esc(last[0])}</span></div>
</section>
<section class="fp-card fp-chart-card" aria-labelledby="fp-chart-title">
<div class="fp-chart-heading"><div><h2 id="fp-chart-title">{_esc(symbol)} · daily history</h2>
<p>{_esc(days[0][0])} — {_esc(last[0])} · {count:,} of {len(days):,} ratios available</p>
<p class="fp-coverage">{_esc(coverage)}</p></div>
<label>Chart<select id="fp-metric"><option value="3">Raw forward P/E · ×</option><option value="6">Winsorized P/E · ×</option><option value="2">Forward EPS</option><option value="1">Daily close</option></select></label></div>
<div id="fp-chart" class="fp-chart" hidden></div>
<p id="fp-empty-chart" hidden>No values for this metric in the selected range.</p>
<div class="fp-chart-heading fp-z-heading"><div><h3>Rolling P/E z-score</h3><p>{_esc(settings_text)}</p><p class="fp-coverage">{z_count:,} of {len(days):,} scores available</p></div>
<label>Standardization<select id="fp-z-metric"><option value="7">Winsorized P/E</option><option value="8">Raw P/E</option></select></label></div>
<div id="fp-z-chart" class="fp-chart" hidden></div>
<p id="fp-empty-z-chart" hidden>No z-scores in this range. More usable history or variation is needed.</p>
<div id="fp-scrubber" hidden><label for="fp-day">Inspect a session <output id="fp-date">{_esc(last[0])}</output></label>
<input id="fp-day" type="range" min="0" max="{len(days)-1}" value="{len(days)-1}" step="1"></div>
<noscript><p>The interactive chart needs JavaScript. The saved values below remain available.</p></noscript>
<p class="fp-chart-note">Select a point or use the session slider. Missing values and changes in forward EPS sign break the P/E line; the chart uses the full value range. The lower chart shows standard deviations from the trailing mean. Both charts use the same selected session. Displayed numbers are rounded; calculations use stored precision.</p>
</section>
<section class="fp-card" aria-labelledby="fp-window-title">
<h2 id="fp-window-title">Behind the selected day</h2>
<div id="fp-selected" class="fp-selected" aria-live="polite">
<p>{_esc(last[0])} · P/E {_pe_value(last)} · EPS {_number(last[2])} · Close {_number(last[1])}</p>
<p>{_esc(latest_status)}</p></div>
<div id="fp-window">{_window_html(result.get("windows", {}).get(last[5], {}))}</div>
</section>
<details class="fp-card fp-table"><summary>Recent daily observations · latest 20 sessions</summary>
<div class="fp-table-scroll"><table><thead><tr><th>Date</th><th>Close</th><th>Forward EPS</th><th>Raw P/E</th><th>Winsorized P/E</th><th>Z-score</th><th>Status</th></tr></thead><tbody>
{"".join("<tr>" + "".join(f"<td>{_esc(value)}</td>" for value in (row[0], _number(row[1]), _number(row[2]), _pe_value(row), _number(row[6], "×") if len(row)>6 else "Unavailable", _number(row[7], "σ") if len(row)>7 else "Unavailable", _status(row))) + "</tr>" for row in reversed(days[-20:]))}
</tbody></table></div></details>"""
        statistic_columns = sorted(statistics[0]) if statistics else []
        payload = {"days": days, "windows": result.get("windows", {}),
                   "statistic_columns": statistic_columns,
                   "statistic_rows": [[row[key] for key in statistic_columns] for row in statistics]}
        body += f'<div id="fp-data" hidden data-series="{_esc(dumps_strict(payload))}"></div>'
    body += """<section class="fp-card fp-analysis-note"><h2>Reading the standardized view</h2>
<p>Winsorization caps extreme P/E values within each trailing window before calculating its mean and population standard deviation. The selected session is included. Earlier saved sessions warm up the calculation, so changing the chart history does not change overlapping scores.</p>
<p>Missing values occupy sessions but are excluded from the sample. A score needs the minimum usable history and a nonzero standard deviation. Negative P/E stays in the sample; sign changes and near-zero EPS can make valuation comparisons misleading. A low z-score is not by itself a sign that a stock is cheap.</p>
<p>Shared with agents through <a href="/agent-tools?tool=company.get_forward_pe_analysis">company.get_forward_pe_analysis</a>. Raw saved values are never overwritten.</p></section>"""
    body += """<details class="fp-card fp-method"><summary>How to read this series</summary>
<p>The denominator uses quarterly EPS estimates. An earnings announcement identifies the last reported fiscal quarter,
so the calculation can select the next four unreported quarters. Actual reported EPS is not the denominator.</p>
<p>If a reported announcement cannot be matched to its fiscal quarter, the next four quarters cannot be selected reliably
and P/E stays unavailable even when estimates exist. Actual period ends determine the order, including companies with non-calendar fiscal years.</p>
<p>Date-only announcements take effect on the next trading session. Daily P/E is that session’s saved close divided by
the sum of four quarterly EPS estimates. Negative ratios are shown as Expected loss; they are not comparable
with positive P/E valuations. Zero EPS is undefined. Missing inputs or conflicting period mappings remain unavailable.
The P/E line breaks when forward EPS changes sign, because the ratio is undefined at zero.</p>
<p>This is a reconstructed four-quarter proxy, not true point-in-time consensus or exactly twelve calendar months.
Price and EPS are shown in their retained source units; no currency or split-basis conversion is implied.</p></details>"""
    return render_inspector_shell(
        title="Forward P/E", active="/forward-pe", revision=revision, body=body,
        eyebrow="Research · announcement-based",
        description="Four forward quarters, stitched on earnings announcements and matched to daily closes.",
    )


def _window_html(window: Mapping) -> str:
    if not window:
        return "<p>No announcement window is available for this session.</p>"
    rows = "".join(f'<tr><td>{_esc(item.get("period_end", "Unavailable"))}</td>'
                   f'<td>{_esc(_number(item.get("eps")))}</td></tr>'
                   for item in window.get("components", ()))
    return f"""<p>Announcement <strong>{_esc(window.get("announcement", "Unavailable"))}</strong>
· Effective session <strong>{_esc(window.get("effective_date", "Unavailable"))}</strong></p>
<p>Reported fiscal period ended {_esc(window.get("reported_period_end") or "Unavailable")}</p>
<div class="fp-table-scroll"><table><thead><tr><th>Forward fiscal period end</th><th>Mean EPS estimate</th></tr></thead>
<tbody>{rows or '<tr><td colspan="2">No quarterly components available</td></tr>'}</tbody></table></div>
<p class="fp-flags">Flags: {_esc(", ".join(str(flag).replace("_", " ") for flag in window.get("flags", ())) or "None recorded")}</p>"""
