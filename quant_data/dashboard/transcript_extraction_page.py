"""Read-only, server-rendered presentation of retained transcript model drafts."""
from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import loads_strict
from .inspector_shell import render_inspector_shell

_MISSING = object()
_SCHEMA = "transcript.structured_call.v1"
_SECTIONS = (
    ("reported_results", "Reported results"), ("guidance", "Guidance"),
    ("business_drivers", "Business drivers"), ("analyst_focus", "Analyst focus"),
    ("management_tone", "Management tone"), ("watch_items", "Watch items"),
)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fields(items: Any) -> dict:
    if not isinstance(items, (list, tuple)):
        return {}
    return {item["name"]: item.get("value") for item in items
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)}


def _records(result: Mapping, kind: str) -> list[dict]:
    records = result.get("records", ())
    if not isinstance(records, (list, tuple)):
        return []
    return [_fields(row.get("fields")) for row in records
            if isinstance(row, Mapping) and row.get("record_type") == kind]


def _scalar(value: Any = _MISSING) -> str:
    if value is _MISSING:
        return '<span class="tx-missing">Unavailable</span>'
    if value is None:
        return '<span class="tx-missing">Not provided</span>'
    if value == "":
        return '<span class="tx-missing">Empty string ("")</span>'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple)):
        return _escape(json.dumps(value, ensure_ascii=False, default=str))
    return _escape(value)


def _get(row: Mapping, key: str) -> Any:
    return row.get(key, _MISSING)


def _link(params: Mapping, *, source: bool = False) -> str:
    values = {"view": "company-transcripts", **params} if source else dict(params)
    return ("/?" if source else "/transcript-extractions?") + urlencode(values)


def _status(value: Any) -> str:
    tone = "neutral"
    if isinstance(value, str):
        if any(word in value for word in ("blocked", "failed", "rejected", "error")):
            tone = "negative"
        elif any(word in value for word in ("flag", "unreviewed", "unassessed", "partial")):
            tone = "caution"
    label = value.replace("_", " ").capitalize() if isinstance(value, str) else value
    return f'<span class="tx-badge tx-{tone}">{_scalar(label)}</span>'


def _sources(value: Any, capture_id: str, label: str = "Source turns") -> str:
    if not isinstance(value, list):
        return f'<p class="tx-sources">{label}: {_scalar(value)}</p>'
    if not value:
        return f'<p class="tx-sources">{label}: <span class="tx-missing">None recorded</span></p>'
    links = []
    for turn in value:
        if isinstance(turn, str) and re.fullmatch(r"t[1-9][0-9]{0,8}", turn):
            page = (int(turn[1:]) - 1) // 100 + 1
            href = _link({"capture_id": capture_id, "limit": 100, "page": page}, source=True)
            links.append(f'<a href="{_escape(href)}" aria-label="{label}: {_escape(turn)}; original transcript page {page}">{_escape(turn)}</a>')
        else:
            links.append(f'<span class="tx-source-unlinked">{_scalar(turn)}</span>')
    return f'<div class="tx-sources"><span>{label}</span> ' + " ".join(links) + '</div>'


def _pair(label: str, value: Any) -> str:
    return f'<div><dt>{label}</dt><dd>{_scalar(value)}</dd></div>'


def _value(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _scalar(value)
    kind = value.get("kind")
    if kind == "range":
        main = _scalar(_get(value, "low")) + ' <span aria-label="to">–</span> ' + _scalar(_get(value, "high"))
    elif kind == "point":
        main = _scalar(_get(value, "amount"))
    elif kind == "qualitative":
        main = _scalar(_get(value, "description"))
    elif kind == "not_provided":
        main = '<span class="tx-missing">Not provided</span>'
    else:
        return '<p class="tx-missing">Unsupported value format. See original JSON.</p>'
    qualifier = value.get("qualifier")
    if isinstance(qualifier, str) and qualifier != "none":
        main = '<span class="tx-qualifier">' + _escape(qualifier.replace("_", " ").capitalize()) + '</span> ' + main
    unit = value.get("unit")
    if unit is not None:
        main += ' <span class="tx-unit">' + _scalar(unit) + '</span>'
    description = value.get("description")
    secondary = ('<p class="tx-value-description">' + _scalar(description) + '</p>'
                 if description is not None and kind != "qualitative" else '')
    return '<div class="tx-value">' + main + '</div>' + secondary


def _item(section: str, item: Mapping, capture: str) -> str:
    if section == "reported_results":
        return ('<div class="tx-result"><div><h3>' + _scalar(_get(item, "metric")) +
                '</h3><p class="tx-item-period">' + _scalar(_get(item, "period")) +
                '</p></div><div>' + _value(_get(item, "value")) + '</div><div><p>' +
                _scalar(_get(item, "comparison")) + '</p>' +
                _sources(_get(item, "source_turn_ids"), capture) + '</div></div>')
    if section == "guidance":
        return ('<article class="tx-entry"><div class="tx-entry-heading"><div><h3>' +
                _scalar(_get(item, "metric")) + '</h3><p class="tx-item-period">' +
                _scalar(_get(item, "period")) + '</p></div>' + _status(_get(item, "change")) +
                '</div><div class="tx-guidance-values"><div><h4>Current</h4>' +
                _value(_get(item, "current")) + '</div><div><h4>Previous</h4>' +
                _value(_get(item, "previous")) + '</div></div><dl class="tx-inline-details">' +
                _pair("Condition", _get(item, "condition")) + '</dl>' +
                _sources(_get(item, "source_turn_ids"), capture) + '</article>')
    if section == "business_drivers":
        return ('<article class="tx-entry"><div class="tx-entry-heading"><h3>' +
                _scalar(_get(item, "business")) + '</h3>' + _status(_get(item, "direction")) +
                '</div><p>' + _scalar(_get(item, "driver")) + '</p>' +
                _sources(_get(item, "source_turn_ids"), capture) + '</article>')
    if section == "analyst_focus":
        return ('<article class="tx-entry"><div class="tx-entry-heading"><h3>' +
                _scalar(_get(item, "topic")) + '</h3>' + _status(_get(item, "answer_status")) +
                '</div><dl class="tx-question"><div><dt>Analyst question</dt><dd>' +
                _scalar(_get(item, "question")) + _sources(_get(item, "question_turn_ids"), capture, "Question turns") +
                '</dd></div><div><dt>Management answer</dt><dd>' + _scalar(_get(item, "management_answer")) +
                _sources(_get(item, "answer_turn_ids"), capture, "Answer turns") + '</dd></div></dl></article>')
    if section == "management_tone":
        return ('<div class="tx-entry"><dl class="tx-tone">' + _pair("Stance", _get(item, "stance")) +
                _pair("Expressed confidence", _get(item, "expressed_confidence")) + '</dl><p>' +
                _scalar(_get(item, "qualification")) + '</p>' + _sources(_get(item, "source_turn_ids"), capture) + '</div>')
    return ('<article class="tx-entry"><div class="tx-entry-heading"><h3>' +
            _scalar(_get(item, "item")) + '</h3>' + _status(_get(item, "type")) +
            '</div><p>' + _scalar(_get(item, "what_to_monitor")) +
            '</p><dl class="tx-inline-details">' + _pair("Horizon", _get(item, "horizon")) +
            '</dl>' + _sources(_get(item, "source_turn_ids"), capture) + '</article>')


def _section(key: str, title: str, value: Any, capture: str, number: int) -> str:
    if isinstance(value, list):
        content = ''.join(_item(key, item, capture) if isinstance(item, Mapping) else
                          '<p class="tx-section-empty">Malformed item. See original JSON.</p>' for item in value)
        content = content or '<p class="tx-section-empty">No items recorded in this extraction.</p>'
        count = f'<span class="tx-section-count">{len(value)}</span>'
    elif key == "management_tone" and isinstance(value, Mapping):
        content, count = _item(key, value, capture), ""
    else:
        content = '<p class="tx-section-empty">' + (_scalar(value) if value is _MISSING or value is None else
                  'This section has an unsupported format. See original JSON.') + '</p>'
        count = ""
    return (f'<section class="tx-section" id="{key}" aria-labelledby="{key}-title">'
            f'<header class="tx-section-heading"><span aria-hidden="true">{number:02d}</span>'
            f'<h2 id="{key}-title" tabindex="-1">{title}</h2>{count}</header>{content}</section>')


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return _MISSING
    try:
        return loads_strict(value)
    except (ValidationError, ResourceLimitError, RecursionError, UnicodeError):
        return _MISSING


def _json_tree(value: Any, depth: int = 0) -> str:
    """Render assessment keys and exact scalar values without schema assumptions."""
    if depth >= 12 and isinstance(value, (Mapping, list)):
        return '<pre class="tx-nested-json">' + _escape(json.dumps(value, ensure_ascii=False, default=str)) + '</pre>'
    if isinstance(value, Mapping):
        if not value:
            return '<span class="tx-missing">Empty object ({})</span>'
        return '<dl class="tx-json-tree">' + ''.join(
            '<div><dt>' + _escape(key) + '</dt><dd>' + _json_tree(child, depth + 1) + '</dd></div>'
            for key, child in value.items()) + '</dl>'
    if isinstance(value, list):
        if not value:
            return '<span class="tx-missing">None recorded</span>'
        return '<ol class="tx-json-list">' + ''.join('<li>' + _json_tree(child, depth + 1) + '</li>' for child in value) + '</ol>'
    return _scalar(value)


def _disclosure(title: str, text: str) -> str:
    return ('<details class="tx-disclosure"><summary>' + title + '</summary><pre tabindex="0">' +
            _escape(text) + '</pre></details>')


def _metadata(result: Mapping) -> str:
    return _disclosure("Query metadata and complete returned records", json.dumps(result, indent=2, ensure_ascii=False, default=str))


def _selection(result: Mapping) -> dict:
    diagnostics = result.get("diagnostics", ())
    if isinstance(diagnostics, (list, tuple)):
        for entry in diagnostics:
            if isinstance(entry, Mapping) and entry.get("code") == "transcript_selection":
                return _fields(entry.get("metrics"))
    return {}


def _context(result: Mapping) -> str:
    selection = _selection(result)
    cutoff = '<span>Availability cutoff · ' + _scalar(_get(selection, "cutoff")) + '</span>'
    return ('<div class="tx-query-context">' + cutoff +
            '<span>Fiscal period labels are provider supplied.</span></div>')


def _pagination(result: Mapping, query: Mapping, *, assessments: bool = False) -> str:
    truncation = result.get("truncation")
    truncation = truncation if isinstance(truncation, Mapping) else {}
    cursor = truncation.get("next_cursor")
    allowed = ("capture_id",) if assessments else ("ticker", "fiscal_year", "fiscal_quarter")
    params = {key: query[key] for key in allowed if query.get(key)}
    links = []
    if query.get("cursor"):
        href = _link(params) if params else "/transcript-extractions"
        links.append(f'<a href="{_escape(href)}">First {"assessments" if assessments else "results"}</a>')
    if isinstance(cursor, str) and cursor and truncation.get("has_more"):
        href = _link({**params, "cursor": cursor})
        if assessments:
            href += "#assessments"
        links.append(f'<a class="tx-next" href="{_escape(href)}">Next {"assessments" if assessments else "results"} <span aria-hidden="true">→</span></a>')
    label = "Assessment pagination" if assessments else "Capture pagination"
    return f'<nav class="tx-pagination" aria-label="{label}">' + ''.join(links) + '</nav>' if links else ""


def _assessments(result: Mapping, query: Mapping) -> str:
    rows = _records(result, "transcript_assessment")
    content = []
    for row in rows:
        parsed = _parse_json(row.get("assessment_json"))
        detail = (_json_tree(parsed) if parsed is not _MISSING else
                  '<p class="tx-missing">Assessment JSON could not be formatted. The original is retained below.</p>')
        kind = "Automatic quality assessment" if row.get("kind") == "automatic" else "Independent review assessment"
        content.append('<article class="tx-assessment"><header class="tx-entry-heading"><h3>' + kind +
                       '</h3>' + _status(_get(row, "outcome")) + '</header><dl class="tx-assessment-meta">' +
                       _pair("Kind", _get(row, "kind")) + _pair("Evaluator", _get(row, "evaluator")) +
                       _pair("Reasoning effort", _get(row, "reasoning_effort")) +
                       _pair("Available at", _get(row, "available_at")) + '</dl>' + detail +
                       _disclosure("Original assessment JSON", str(row.get("assessment_json", ""))) + '</article>')
    if not content:
        content.append('<p class="tx-section-empty">No assessment records on this page. Review status is shown above.</p>')
    return ('<section class="tx-section" id="assessments" aria-labelledby="assessments-title">'
            '<header class="tx-section-heading"><h2 id="assessments-title" tabindex="-1">Quality &amp; review</h2>'
            f'<span class="tx-section-count">{len(rows)} on this page</span></header>'
            '<p class="tx-assessment-note">Automatic checks and independent reviews are separate records. '
            'A stored draft or assessment does not imply approval.</p>' + ''.join(content) +
            _pagination(result, query, assessments=True) + '</section>')


def _notice(title: str, detail: str, *, error: bool = False) -> str:
    return ('<section class="tx-notice"' + (' role="alert"' if error else '') +
            '><h2>' + title + '</h2><p>' + detail + '</p></section>')


def _detail(result: Mapping, query: Mapping) -> tuple[str, str, str]:
    capture = query["capture_id"]
    rows = _records(result, "transcript_extraction")
    back = {key: query[key] for key in ("ticker", "fiscal_year", "fiscal_quarter") if query.get(key)}
    back_url = _link(back) if back else "/transcript-extractions"
    toolbar = ('<div class="tx-toolbar"><a href="' + _escape(back_url) + '">← Browse extractions</a>'
               '<a class="tx-source-action" href="' + _escape(_link({"capture_id": capture}, source=True)) +
               '">Read original transcript <span aria-hidden="true">↗</span></a></div>')
    if not rows:
        missing_extraction = bool(_records(result, "transcript_extraction_status"))
        title = "Extraction not available" if missing_extraction else "Capture not available"
        message = ("This capture has no extraction available at the query cutoff." if missing_extraction else
                   "No retained capture was returned for this selection at the query cutoff.")
        return title, "Retained transcript extraction", toolbar + _notice(title, message) + _context(result) + _metadata(result)
    row = rows[0]
    parsed = _parse_json(row.get("structured_json"))
    call = parsed.get("call", {}) if isinstance(parsed, Mapping) else {}
    call = call if isinstance(call, Mapping) else {}
    symbol = row.get("symbol") or call.get("symbol") or "Transcript"
    title = f"{symbol} · FY{row.get('fiscal_year', '—')} Q{row.get('fiscal_quarter', '—')}"
    description = str(call.get("period_label") or "Retained transcript extraction")
    status = ('<dl class="tx-status-strip"><div><dt>Extraction model</dt><dd>' + _scalar(_get(row, "model")) +
              '</dd><dd class="tx-effort">Reasoning effort · ' + _scalar(_get(row, "reasoning_effort")) +
              '</dd></div><div><dt>Automatic quality</dt><dd>' + _status(_get(row, "automatic_quality_status")) +
              '</dd></div><div><dt>Independent review</dt><dd>' + _status(_get(row, "review_status")) +
              '</dd><dd class="tx-review-outcome">Latest outcome · ' + _scalar(_get(row, "latest_review_outcome")) + '</dd></div></dl>')
    provenance = ('<div class="tx-query-context"><span>Extraction available · ' + _scalar(_get(row, "available_at")) +
                  '</span><span>Original model draft · wording preserved</span></div>')
    if not isinstance(parsed, Mapping):
        reading = _notice("Extraction could not be formatted", "The stored structured JSON is missing, malformed, or not an object. The original data is available below.", error=True)
    elif parsed.get("schema_version") != _SCHEMA:
        reading = _notice("Unsupported extraction format", "This reader supports " + _escape(_SCHEMA) +
                          ". Stored schema: " + _scalar(_get(parsed, "schema_version")) + ". The original data is available below.")
    else:
        headline = parsed.get("headline")
        headline_body = ('<p class="tx-headline-text">' + _scalar(_get(headline, "message")) + '</p>' +
                         _sources(_get(headline, "source_turn_ids"), capture) if isinstance(headline, Mapping) else
                         '<p class="tx-section-empty">' + _scalar(_get(parsed, "headline")) + '</p>')
        reading = ('<section class="tx-headline" id="headline" aria-labelledby="headline-title">'
                   '<h2 id="headline-title" tabindex="-1">Headline</h2>' + headline_body + '</section>' +
                   ''.join(_section(key, label, _get(parsed, key), capture, index) for index, (key, label) in enumerate(_SECTIONS, 1)))
    index = ('<aside class="tx-index"><nav aria-label="Extraction sections"><p>On this page</p>' +
             ('<a href="#headline">Headline</a>' + ''.join(f'<a href="#{key}">{label}</a>' for key, label in _SECTIONS)
              if isinstance(parsed, Mapping) and parsed.get("schema_version") == _SCHEMA else '') +
             '<a href="#assessments">Quality &amp; review</a><a href="#original-data">Original data</a></nav>'
             '<p class="tx-index-note">Source links open the original transcript page containing the cited turn.</p></aside>')
    original = ('<section class="tx-original" id="original-data" aria-labelledby="original-title"><h2 id="original-title" tabindex="-1">Original data</h2>' +
                _disclosure("Original extraction JSON · unchanged", str(row.get("structured_json", ""))) + _metadata(result) + '</section>')
    body = toolbar + status + provenance + '<div class="tx-reading-layout"><div class="tx-document">' + reading + _assessments(result, query) + original + '</div>' + index + '</div>' + _context(result)
    return title, description, body


def _browse(result: Mapping, query: Mapping, error: str | None) -> str:
    def selected(value: str) -> str:
        return ' selected' if query.get("fiscal_quarter", "") == value else ''
    quarters = '<option value="">All quarters</option>' + ''.join(f'<option value="{number}"{selected(str(number))}>Q{number}</option>' for number in range(1, 5))
    form = ('<form class="tx-filters" action="/transcript-extractions" method="get" aria-label="Find transcript extractions">'
            '<label>Ticker<input name="ticker" value="' + _escape(query.get("ticker", "")) + '" placeholder="e.g. MSFT" autocomplete="off"></label>'
            '<label>Fiscal year<input name="fiscal_year" type="number" min="1900" max="9999" step="1" value="' + _escape(query.get("fiscal_year", "")) + '" placeholder="All years"></label>'
            '<label>Fiscal quarter<select name="fiscal_quarter">' + quarters + '</select></label>'
            '<button type="submit">Find transcripts</button><a href="/transcript-extractions">Clear filters</a></form>')
    if error is not None:
        return form + _notice("Extractions could not be loaded", _escape(error), error=True)
    rows = _records(result, "transcript_capture")
    if not rows:
        return form + _notice("No matching transcripts", "No retained captures match these filters at the query cutoff. Try another ticker or fiscal period.") + _context(result) + _metadata(result)
    items = []
    for row in rows:
        identity = str(row.get("symbol", "Transcript")) + f" · FY{row.get('fiscal_year', '—')} Q{row.get('fiscal_quarter', '—')}"
        capture = row.get("capture_id")
        params = {"capture_id": capture}
        available = row.get("extraction_available") is True
        action = "Read extraction" if available else "View availability"
        link = ('<a class="tx-read-link" href="' + _escape(_link(params)) + '" aria-label="' +
                _escape(action + ': ' + identity) + '">' + action + ' <span aria-hidden="true">→</span></a>' if isinstance(capture, str) and capture else _scalar())
        source = ('<a href="' + _escape(_link({"capture_id": capture}, source=True)) + '">Original transcript</a>' if isinstance(capture, str) and capture else '')
        items.append('<tr><th scope="row"><strong>' + _scalar(_get(row, "symbol")) + '</strong><span>FY' +
                     _scalar(_get(row, "fiscal_year")) + ' · Q' + _scalar(_get(row, "fiscal_quarter")) + '</span></th>'
                     '<td>' + ('Available' if available else 'Not available' if row.get("extraction_available") is False else 'Availability unknown') +
                     '<small>' + _scalar(_get(row, "extraction_available_at")) + '</small></td><td>' +
                     _status(_get(row, "automatic_quality_status")) + '</td><td>' + _status(_get(row, "review_status")) +
                     '<small>Latest outcome · ' + _scalar(_get(row, "latest_review_outcome")) + '</small></td><td class="tx-row-links">' + link + source + '</td></tr>')
    table = ('<section class="tx-browse-results" aria-labelledby="tx-results-title"><div class="tx-results-heading"><h2 id="tx-results-title">Retained transcripts</h2>'
             f'<span>{len(rows)} on this page</span></div><div class="tx-table-scroll" role="region" tabindex="0" aria-label="Transcript extraction availability">'
             '<table class="tx-browse-table"><caption class="inspector-sr-only">Retained transcripts and their separate extraction, automatic quality, and independent review states</caption>'
             '<thead><tr><th scope="col">Company / fiscal period</th><th scope="col">Extraction</th><th scope="col">Automatic quality</th><th scope="col">Independent review</th><th scope="col">Read</th></tr></thead><tbody>' +
             ''.join(items) + '</tbody></table></div></section>')
    return form + table + _pagination(result, query) + _context(result) + _metadata(result)


def render_transcript_extraction_page(
    result: Mapping, *, query: Mapping[str, str], registry_revision: str,
    error: str | None = None,
) -> str:
    """Display a QueryResultV1 from the fixed search or extraction reader."""
    title = "Transcript extractions"
    description = "Read retained model drafts alongside their source turns, quality checks, and independent reviews."
    if query.get("capture_id") and error is None:
        title, description, body = _detail(result, query)
    elif query.get("capture_id"):
        body = '<div class="tx-toolbar"><a href="/transcript-extractions">← Browse extractions</a></div>' + _notice("Extraction could not be loaded", _escape(error), error=True)
    else:
        body = _browse(result, query, error)
    page = render_inspector_shell(title=title, active="/transcript-extractions", revision=registry_revision,
                                  body='<div class="tx-page">' + body + '</div>', description=description,
                                  eyebrow="Transcript extractions" if query.get("capture_id") else "Company research")
    return page.replace('</head>', '<link rel="stylesheet" href="/assets/transcript-extraction.css"></head>', 1)
