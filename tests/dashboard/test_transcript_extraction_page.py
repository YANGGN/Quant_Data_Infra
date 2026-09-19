from __future__ import annotations

import copy
import html
from html.parser import HTMLParser
import json
import unittest
from urllib.parse import parse_qs, urlparse

from quant_data.dashboard.transcript_extraction_page import render_transcript_extraction_page


def record(record_type, **fields):
    return {"record_type": record_type, "fields": [{"name": key, "value": value} for key, value in fields.items()]}


def scalar_value(**changes):
    return {"kind": "point", "amount": "3.30", "low": None, "high": None,
            "unit": "$ per share", "description": None, "qualifier": "approximately", **changes}


def extraction():
    return {
        "schema_version": "transcript.structured_call.v1",
        "call": {"symbol": "TEST", "period_label": "Q1 FY2025 (source label)", "source_capture_id": "capture_fixture"},
        "headline": {"message": "Growth with constraints <&>", "source_turn_ids": ["t1", "t101"]},
        "reported_results": [{"metric": "Diluted EPS", "period": "Q1 FY25", "value": scalar_value(),
                              "comparison": "Up 10% year-over-year", "source_turn_ids": ["t100"]}],
        "guidance": [{"metric": "Revenue growth", "period": "Q2 FY25", "previous": scalar_value(kind="range", amount=None, low="8.0", high="9.00", unit="%", qualifier="none"),
                      "current": scalar_value(kind="range", amount=None, low="10.0", high="11.00", unit="%", qualifier="at_least"),
                      "change": "raised", "condition": "If capacity arrives", "source_turn_ids": ["t102"]}],
        "business_drivers": [{"business": "Infrastructure", "direction": "mixed", "driver": "Demand exceeds supply.", "source_turn_ids": ["t2"]}],
        "analyst_focus": [{"topic": "Capacity timing", "question": "When will capacity arrive?", "management_answer": "Timing remains uncertain.",
                           "answer_status": "partial", "question_turn_ids": ["t201"], "answer_turn_ids": ["t202"]}],
        "management_tone": {"stance": "balanced", "expressed_confidence": "moderate", "qualification": "Subject to delivery timing.", "source_turn_ids": ["t203"]},
        "watch_items": [{"item": "Capacity expansion", "type": "risk", "what_to_monitor": "Equipment deliveries", "horizon": "FY25 H2", "source_turn_ids": ["t204"]}],
    }


def result(structured=None, **changes):
    data = extraction() if structured is None else structured
    fields = {"capture_id": "capture_fixture", "analysis_id": "analysis_fixture", "model": "fixture-model", "reasoning_effort": "high",
              "symbol": "TEST", "fiscal_year": 2025, "fiscal_quarter": 1, "automatic_quality_status": "accepted_with_flags",
              "review_status": "unreviewed", "latest_review_outcome": None, "available_at": "2026-09-01T12:00:00Z",
              "structured_json": json.dumps(data), **changes}
    return {"records": [record("transcript_extraction", **fields)],
            "truncation": {"has_more": False, "next_cursor": None},
            "diagnostics": [{"code": "transcript_selection", "metrics": [{"name": "cutoff", "value": "2026-09-19T12:00:00Z"}]}],
            "warnings": [{"code": "fixture_warning", "message": "Fixture provenance retained."}]}


class Markup(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.links = []
        self.ids = []
        self.pre = []
        self.in_pre = False
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "a":
            self.links.append(attributes)
        if tag == "pre":
            self.pre.append("")
            self.in_pre = True

    def handle_data(self, data):
        if self.in_pre:
            self.pre[-1] += data

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = False


class TranscriptExtractionPageTests(unittest.TestCase):
    def render(self, value=None, query=None, **kwargs):
        return render_transcript_extraction_page(result() if value is None else value,
            query={"capture_id": "capture_fixture"} if query is None else query,
            registry_revision="fixture", **kwargs)

    def test_all_structured_sections_exact_values_and_qualifiers_are_visible(self):
        page = self.render()
        document = page.split('id="original-data"')[0]
        for text in ("Growth with constraints &lt;&amp;&gt;", "3.30", "$ per share", "Approximately", "10.0", "11.00", "8.0", "9.00",
                     "At least", "If capacity arrives", "Demand exceeds supply.", "When will capacity arrive?",
                     "Timing remains uncertain.", "Subject to delivery timing.", "Equipment deliveries", "FY25 H2"):
            self.assertIn(text, document)
        for section in ("headline", "reported_results", "guidance", "business_drivers", "analyst_focus", "management_tone", "watch_items", "assessments", "original-data"):
            self.assertIn(section, Markup(page).ids)
        self.assertIn("fixture-model", page)
        self.assertIn("Q1 FY2025 (source label)", page)
        self.assertIn('href="/assets/transcript-extraction.css"', page)
        self.assertEqual(len(Markup(page).ids), len(set(Markup(page).ids)))

    def test_source_turn_links_use_original_pages_without_invented_anchors(self):
        links = Markup(self.render()).links
        source_links = {a.get("aria-label", ""): a["href"] for a in links if a.get("aria-label", "").startswith(("Source turns:", "Question turns:", "Answer turns:"))}
        for turn, expected in (("t1", "1"), ("t100", "1"), ("t101", "2"), ("t201", "3")):
            href = next(href for label, href in source_links.items() if f": {turn};" in label)
            self.assertEqual(parse_qs(urlparse(href).query), {"view": ["company-transcripts"], "capture_id": ["capture_fixture"], "limit": ["100"], "page": [expected]})
            self.assertEqual(urlparse(href).fragment, "")

    def test_untrusted_text_is_escaped_in_content_attributes_and_json(self):
        bad = '</p><script>alert("x")</script><img src=x onerror=alert(1)>'
        data = extraction()
        data["headline"]["message"] = bad
        data["headline"]["source_turn_ids"] = [bad, "t1"]
        value = result(data, model=bad)
        page = self.render(value, query={"capture_id": '\" onclick=evil&other=<script>'})
        self.assertNotIn('<script>alert', page)
        self.assertNotIn('<img src=x', page)
        self.assertIn(html.escape(bad, quote=True), page)
        links = Markup(page).links
        self.assertFalse(any("onclick" in a for a in links))
        for a in links:
            self.assertTrue(a["href"].startswith(("/", "#")))
        self.assertEqual(json.loads(Markup(page).pre[0])["headline"]["message"], bad)

    def test_original_extraction_and_complete_metadata_are_lossless_disclosures(self):
        value = result()
        before = copy.deepcopy(value)
        page = self.render(value)
        raw = next(f["value"] for f in value["records"][0]["fields"] if f["name"] == "structured_json")
        blocks = Markup(page).pre
        self.assertEqual(blocks[0], raw)
        self.assertEqual(json.loads(blocks[-1]), value)
        self.assertEqual(value, before)

    def test_empty_null_missing_and_not_provided_are_distinct(self):
        data = extraction()
        data["reported_results"] = []
        data["management_tone"] = None
        del data["analyst_focus"]
        data["guidance"][0]["previous"] = None
        data["guidance"][0]["condition"] = ""
        data["guidance"][0]["current"] = scalar_value(kind="not_provided", amount=None, unit=None)
        page = self.render(result(data))
        self.assertIn("No items recorded in this extraction.", page)
        self.assertIn("Not provided", page)
        self.assertIn("Unavailable", page)
        self.assertIn('Empty string ("")', page)
        self.assertNotIn('Qualifier: none', page)

    def test_missing_capture_extraction_and_load_error_have_distinct_states(self):
        self.assertIn("Capture not available", self.render({"records": []}))
        missing = {"records": [record("transcript_extraction_status", capture_id="capture_fixture", extraction_available=False)]}
        self.assertIn("Extraction not available", self.render(missing))
        page = self.render({}, error="<busy>")
        self.assertIn('role="alert"', page)
        self.assertIn("Extraction could not be loaded", page)
        self.assertIn("&lt;busy&gt;", page)
        self.assertNotIn("Capture not available", page)

    def test_malformed_unsupported_and_partly_malformed_structures_do_not_raise(self):
        for raw in ("{broken", "[]", "null", '{"key":NaN}', '{"key":1,"key":2}'):
            with self.subTest(raw=raw):
                page = self.render(result(structured_json=raw))
                self.assertIn("Extraction could not be formatted", page)
                self.assertIn(raw, Markup(page).pre[0])
        data = extraction()
        data["schema_version"] = "transcript.future.v2"
        self.assertIn("Unsupported extraction format", self.render(result(data)))
        data = extraction()
        data["guidance"] = ["bad item", {"current": {"kind": "unknown"}}]
        data["business_drivers"] = {"unexpected": "object"}
        page = self.render(result(data))
        self.assertIn("Malformed item", page)
        self.assertIn("unsupported format", page)
        self.assertIn("Unsupported value format", page)

    def test_automatic_and_independent_outcomes_remain_separate(self):
        value = result(automatic_quality_status="blocked", review_status="review_recorded", latest_review_outcome="failed")
        value["records"].append(record("transcript_assessment", kind="automatic", evaluator="fixture-policy", reasoning_effort=None, outcome="blocked",
            available_at="2026-09-01T12:01:00Z", assessment_json=json.dumps({"findings": [{"severity": "error", "message": "Missing coverage <&>", "pointer": "/analyst_focus"}], "word_count": 123})))
        value["records"].append(record("transcript_assessment", kind="review", evaluator="fixture-reviewer", reasoning_effort="high", outcome="failed", assessment_json='{broken'))
        page = self.render(value)
        readable = page.split('id="original-data"')[0]
        for text in ("Automatic quality", "Independent review", "Blocked", "Review recorded", "failed", "Missing coverage &lt;&amp;&gt;", "/analyst_focus", "123", "Assessment JSON could not be formatted"):
            self.assertIn(text, readable)
        self.assertNotIn(">Approved<", page)
        self.assertIn("No assessment records on this page", self.render())

    def test_deep_assessment_tree_has_bounded_formatted_depth(self):
        nested = {"detail": "retained"}
        for _ in range(20):
            nested = {"child": nested}
        value = result()
        value["records"].append(record("transcript_assessment", kind="automatic", outcome="blocked", assessment_json=json.dumps(nested)))
        page = self.render(value)
        self.assertIn('class="tx-nested-json"', page)
        self.assertIn("retained", page)

    def test_browse_filters_pagination_and_capture_only_detail_links(self):
        value = {"records": [record("transcript_capture", capture_id="capture_fixture", symbol="TEST", fiscal_year=2025, fiscal_quarter=1,
            extraction_available=True, extraction_available_at="2026-09-01T12:00:00Z", automatic_quality_status="accepted_with_flags", review_status="unreviewed", latest_review_outcome=None)],
            "truncation": {"has_more": True, "next_cursor": "opaque+cursor/=value"}}
        query = {"ticker": "TEST", "fiscal_year": "2025", "fiscal_quarter": "1", "cursor": "old"}
        page = self.render(value, query=query)
        self.assertIn('value="TEST"', page)
        self.assertIn('<option value="1" selected>', page)
        self.assertIn("First results", page)
        links = Markup(page).links
        detail = next(a for a in links if a.get("aria-label", "").startswith("Read extraction:"))
        self.assertEqual(parse_qs(urlparse(detail["href"]).query), {"capture_id": ["capture_fixture"]})
        next_page = next(a for a in links if a.get("class") == "tx-next")
        self.assertEqual(parse_qs(urlparse(next_page["href"]).query), {"ticker": ["TEST"], "fiscal_year": ["2025"], "fiscal_quarter": ["1"], "cursor": ["opaque+cursor/=value"]})
        self.assertNotIn('data-inspector-table', page)

    def test_assessment_pagination_preserves_capture_and_points_to_review_section(self):
        value = result()
        value["truncation"] = {"has_more": True, "next_cursor": "opaque-assessment"}
        page = self.render(value, query={"capture_id": "capture_fixture", "cursor": "old"})
        link = next(a for a in Markup(page).links if a.get("class") == "tx-next")
        self.assertEqual(parse_qs(urlparse(link["href"]).query), {"capture_id": ["capture_fixture"], "cursor": ["opaque-assessment"]})
        self.assertEqual(urlparse(link["href"]).fragment, "assessments")
        self.assertIn("First assessments", page)

    def test_browse_empty_unavailable_and_error_are_not_success(self):
        self.assertIn("No matching transcripts", self.render({"records": []}, query={}))
        value = {"records": [record("transcript_capture", capture_id="missing", extraction_available=False)]}
        self.assertIn("View availability", self.render(value, query={}))
        page = self.render({}, query={"ticker": 'A" autofocus="x'}, error="<timed out>")
        self.assertIn("Extractions could not be loaded", page)
        self.assertNotIn(' autofocus="x', page)
        self.assertNotIn("No matching transcripts", page)


if __name__ == "__main__":
    unittest.main()
