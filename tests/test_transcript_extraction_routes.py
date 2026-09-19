"""Formatted-reader routes use bounded public tools and temporary stores only."""
from __future__ import annotations

import html
from urllib.parse import urlencode
import unittest
from unittest.mock import patch

from quant_data.canonical_inspector import CanonicalInspectorApplication, _render_page
from quant_data.errors import StoreUnavailableError
from quant_data.fingerprint import mutation_fingerprint
from tests.company import test_transcript_structured_store as fixture


class TranscriptExtractionRouteTests(unittest.TestCase):
    def setUp(self):
        self.seed = fixture.StructuredStoreTests()
        self.seed.setUp()
        self.addCleanup(self.seed.tearDown)
        self.seed.pub.publish([self.seed.row], [self.seed.assessment], published_at=fixture.PUBLISHED)
        self.app = CanonicalInspectorApplication(self.seed.stores, self.seed.registry)

    def request(self, target, method="GET"):
        before = mutation_fingerprint(self.seed.stores)
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            response = self.app.handle(method, target)
        self.assertEqual(before, mutation_fingerprint(self.seed.stores))
        return response

    def test_browse_filters_use_explicit_tool_version_and_link_to_same_capture(self):
        with patch.object(self.app.dispatcher, "call", wraps=self.app.dispatcher.call) as invoke:
            response = self.request("/transcript-extractions?ticker=aapl&fiscal_year=&fiscal_quarter=")
        self.assertEqual(response.status, 200, response.body)
        invoke.assert_called_once_with("company.search_transcripts", {"limit": 25, "ticker": "AAPL"}, tool_version="1.0.0")
        page = response.body.decode()
        self.assertIn("/transcript-extractions?capture_id=" + self.seed.capture, page)
        self.assertIn('href="/transcript-extractions" aria-current="page"', page)

    def test_detail_preserves_saved_content_and_original_capture_link(self):
        with patch.object(self.app.dispatcher, "call", wraps=self.app.dispatcher.call) as invoke:
            response = self.request("/transcript-extractions?" + urlencode({"capture_id": self.seed.capture}))
        self.assertEqual(response.status, 200, response.body)
        invoke.assert_called_once_with("company.get_transcript_extraction", {"limit": 10, "capture_id": self.seed.capture}, tool_version="1.0.0")
        page = response.body.decode()
        self.assertIn(html.escape(self.seed.draft["headline"]["message"]), page)
        self.assertIn("/?view=company-transcripts&amp;capture_id=" + self.seed.capture, page)
        self.assertIn("gpt-5.6-terra", page)
        self.assertIn("unreviewed", page.lower())

    def test_invalid_controls_fail_before_any_reader_and_methods_stay_closed(self):
        invalid = (
            "sql=SELECT", "database=/tmp/test.sqlite", "limit=100000", "ticker=AAPL&ticker=MSFT",
            "capture_id=invalid", "capture_id=", "fiscal_year=nan", "fiscal_quarter=5",
            "fiscal_year=2201", "fiscal_year=2025.0", "cursor=not-a-cursor",
            "capture_id=" + self.seed.capture + "&ticker=AAPL",
        )
        with patch.object(self.app.dispatcher, "call") as invoke:
            for query in invalid:
                with self.subTest(query=query):
                    response = self.request("/transcript-extractions?" + query)
                    self.assertEqual(response.status, 400, response.body)
            self.assertEqual(self.request("/transcript-extractions", "POST").status, 405)
            self.assertEqual(self.request("/transcript-extractions", "DELETE").status, 405)
        invoke.assert_not_called()

    def test_busy_store_has_readable_error_without_claiming_empty_results(self):
        with patch.object(self.app.dispatcher, "call", side_effect=StoreUnavailableError("Company store is busy <retry>")):
            response = self.request("/transcript-extractions?ticker=AAPL")
        self.assertEqual(response.status, 503)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        page = response.body.decode()
        self.assertIn("Company store is busy &lt;retry&gt;", page)
        self.assertNotIn("<retry>", page)
        self.assertIn("/transcript-extractions", page)

    def test_unknown_capture_and_new_asset_are_safe(self):
        response = self.request("/transcript-extractions?capture_id=equibles_transcript_" + "f" * 32)
        self.assertEqual(response.status, 200, response.body)
        self.assertNotIn(self.seed.draft["headline"]["message"], response.body.decode())
        asset = self.request("/assets/transcript-extraction.css")
        self.assertEqual(asset.status, 200)
        self.assertEqual(asset.content_type, "text/css; charset=utf-8")
        self.assertEqual(self.request("/assets/transcript-extraction.css?path=elsewhere").status, 400)

    def test_original_transcript_pages_link_to_reader_without_hiding_original_fields(self):
        row = {"capture_id": self.seed.capture, "symbol": "AAPL", "fiscal_year": 2026, "fiscal_quarter": 1}
        result = {"view": "company-transcripts", "columns": tuple(row), "rows": [row],
                  "query": {}, "page": 1, "limit": 25, "total": 1}
        page = _render_page(result, {}, self.seed.registry.revision, None)
        self.assertIn("Read extraction", page)
        self.assertIn("Read transcript", page)
        self.assertIn("/transcript-extractions?capture_id=" + self.seed.capture, page)
        for key in row:
            self.assertIn('data-field="' + key + '"', page)
        result.update(query={"capture_id": self.seed.capture}, transcript=row)
        detail = _render_page(result, {"capture_id": self.seed.capture}, self.seed.registry.revision, None)
        self.assertIn("/transcript-extractions?capture_id=" + self.seed.capture, detail)
