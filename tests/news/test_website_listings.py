from __future__ import annotations

import tempfile
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.news.website_listings import (
    MAX_BYTES, MAX_ROWS, SOURCE_URLS, StdlibWebsiteTransport,
    WebsiteResponse, collect_website_news, parse_website_response, _WebsiteRequest,
)

CLOCK = datetime(2026, 9, 6, 4, 55, tzinfo=timezone.utc)


def finviz(*, title="Example &amp; headline", stamp="Sep-05", url="https://publisher.example/news/1", summary="A short description"):
    return ('<table><tr class="news_table-row">'
            '<td class="news_date-cell">' + stamp + '</td>'
            '<td class="news_link-cell" data-boxover-text="' + summary + '">'
            '<a href="' + url + '">' + title + '</a></td></tr></table>').encode()


def rss(*, title="Example headline", item_id="123", stamp="Sat, 05 Sep 2026 20:43:54 GMT"):
    return ('<rss version="2.0"><channel><item><title>' + title + '</title>'
            '<guid isPermaLink="false">' + item_id + '</guid>'
            '<link>https://www.financialjuice.com/News/' + item_id + '/Example.aspx?xy=rss</link>'
            '<pubDate>' + stamp + '</pubDate><description /></item></channel></rss>').encode()


def parse(source, body, when=CLOCK):
    return parse_website_response(source, WebsiteResponse(200,
        "text/html; charset=utf-8" if source == "finviz" else "text/xml", body),
        captured_at=when)


class WebsiteListingTests(unittest.TestCase):
    def test_finviz_decodes_text_and_preserves_imprecise_time(self):
        for stamp in ("12:02AM", "Sep-05"):
            with self.subTest(stamp=stamp):
                row = parse("finviz", finviz(stamp=stamp)).headlines[0]
                self.assertEqual(row.headline, "Example & headline")
                self.assertEqual(row.summary, "A short description")
                self.assertEqual(row.published_date_raw, stamp)
                self.assertEqual(row.published_precision, "unknown")
                self.assertIsNone(row.published_normalized_at)

    def test_finviz_date_only_stays_date_only(self):
        row = parse("finviz", finviz(stamp="2026-09-05")).headlines[0]
        self.assertEqual(row.published_precision, "date")
        self.assertIsNone(row.published_normalized_at)

    def test_financialjuice_identity_and_utc_timestamp(self):
        row = parse("financialjuice", rss()).headlines[0]
        self.assertEqual(row.source_item_id, "123")
        self.assertEqual(row.source_name, "FinancialJuice")
        self.assertEqual(row.published_normalized_at, "2026-09-05T20:43:54.000000Z")
        self.assertEqual(row.published_precision, "datetime_offset")

    def test_semantic_replay_ignores_capture_clock_markup_and_tracking(self):
        first = parse("finviz", finviz(url="https://publisher.example/news/1?utm_source=a"))
        later = parse("finviz", b"<!-- new advert -->" +
            finviz(url="https://publisher.example/news/1?utm_source=b"),
            CLOCK + timedelta(hours=1))
        self.assertNotEqual(first.response_sha256, later.response_sha256)
        self.assertNotEqual(first.captured_at, later.captured_at)
        self.assertEqual(first.semantic_sha256, later.semantic_sha256)

    def test_correction_keeps_identity_and_changes_content(self):
        first = parse("financialjuice", rss()).headlines[0]
        correction = parse("financialjuice", rss(title="Corrected headline")).headlines[0]
        self.assertEqual(first.article_id, correction.article_id)
        self.assertNotEqual(first.content_sha256, correction.content_sha256)

    def test_duplicate_rows_do_not_duplicate_items(self):
        capture = parse("finviz", finviz() + finviz())
        self.assertEqual(capture.provider_row_count, 2)
        self.assertEqual(capture.duplicate_row_count, 1)
        self.assertEqual(len(capture.headlines), 1)
        self.assertEqual(capture.headlines[0].source_row, 1)

    def test_conflicting_duplicate_identity_fails(self):
        with self.assertRaises(ValidationError):
            parse("finviz", finviz() + finviz(title="Changed title"))

    def test_login_challenge_and_javascript_shell_fail(self):
        for body in (b"<html>Login</html>", b"<script>loadNews()</script>",
                     b"<html>Checking your browser</html>",
                     b'<tr class="news_table-row"><td>truncated'):
            with self.subTest(body=body):
                with self.assertRaises(ValidationError):
                    parse("finviz", body)
        for body in (b"<html>Login</html>", b"<rss><channel /></rss>",
                     b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss><channel /></rss>'):
            with self.subTest(body=body):
                with self.assertRaises(ValidationError):
                    parse("financialjuice", body)

    def test_unsafe_links_and_ambiguous_identity_fail(self):
        for url in ("javascript:alert(1)", "/relative", "https://user:pass@example.org/1",
                    "https://example.org:bad/1", "https://example.org/with space"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    parse("finviz", finviz(url=url))
        with self.assertRaises(ValidationError):
            parse("financialjuice", rss().replace(b"<guid isPermaLink=\"false\">123", b"<guid isPermaLink=\"false\">999"))
        with self.assertRaises(ValidationError):
            parse("financialjuice", rss().replace(b"www.financialjuice.com/News", b"evil.example/News"))

    def test_publication_offset_is_not_invented(self):
        with self.assertRaises(ValidationError):
            parse("financialjuice", rss(stamp="Sat, 05 Sep 2026 20:43:54"))
        with self.assertRaises(ValidationError):
            parse_website_response("finviz", WebsiteResponse(200, "text/html", finviz()),
                                   captured_at=datetime(2026, 9, 6))

    def test_byte_row_and_text_bounds(self):
        with self.assertRaises(ResourceLimitError):
            WebsiteResponse(200, "text/html", b"x" * (MAX_BYTES + 1))
        with self.assertRaises(ResourceLimitError):
            parse("finviz", finviz() * (MAX_ROWS + 1))
        with self.assertRaises(ResourceLimitError):
            parse("finviz", finviz(title="x" * 4097))

    def test_status_and_media_mismatch_fail(self):
        for response in (WebsiteResponse(403, "text/html", finviz()),
                         WebsiteResponse(200, "application/json", finviz())):
            with self.assertRaises(ValidationError):
                parse_website_response("finviz", response, captured_at=CLOCK)

    def test_single_attempt_and_no_database_or_file_access(self):
        calls = []
        class Transport:
            def get(self, source_id):
                calls.append(source_id)
                return WebsiteResponse(200, "text/html", finviz())
        with tempfile.TemporaryDirectory() as tmp:
            with patch("sqlite3.connect", side_effect=AssertionError("unexpected database access")):
                capture = collect_website_news("finviz", transport=Transport(), clock=lambda: CLOCK)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(calls, ["finviz"])
        self.assertEqual(capture.request_url, SOURCE_URLS["finviz"])
        class Failure:
            def get(self, source_id):
                calls.append(source_id)
                raise StoreUnavailableError("offline failure")
        with self.assertRaises(StoreUnavailableError):
            collect_website_news("financialjuice", transport=Failure())
        self.assertEqual(calls, ["finviz", "financialjuice"])

    def test_transport_rejects_arbitrary_target_before_connecting(self):
        with patch("http.client.HTTPSConnection") as connection:
            with self.assertRaises(ValidationError):
                StdlibWebsiteTransport().get("https://elsewhere.example/")
            connection.assert_not_called()

    def test_transport_enforces_declared_size_and_deadline(self):
        class Response:
            status = 200
            def getheader(self, name, default=None):
                return str(MAX_BYTES + 1) if name == "Content-Length" else default
        class Connection:
            sock = None
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return Response()
            def close(self):
                pass
        with patch("http.client.HTTPSConnection", return_value=Connection()):
            with self.assertRaises(ResourceLimitError):
                _WebsiteRequest().get("finviz")
        with patch("http.client.HTTPSConnection", return_value=Connection()):
            with patch("quant_data.news.website_listings.time.monotonic", side_effect=[0, 61]):
                with self.assertRaises(ResourceLimitError):
                    _WebsiteRequest().get("finviz")

    def test_redirect_is_not_followed_or_retried(self):
        class Response:
            status = 302
        class Connection:
            def __init__(self):
                self.requests = []
            def request(self, *args, **kwargs):
                self.requests.append((args, kwargs))
            def getresponse(self):
                return Response()
            def close(self):
                pass
        connection = Connection()
        with patch("http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(StoreUnavailableError):
                _WebsiteRequest().get("financialjuice")
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(connection.requests[0][0], ("GET", "/feed.ashx?xy=rss"))
        headers = connection.requests[0][1]["headers"]
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)


    def test_worker_is_one_credential_free_bounded_process(self):
        body = finviz()
        result = subprocess.CompletedProcess([], 0,
            b'{"status":200,"content_type":"text/html"}\n' + body, b"")
        with patch("quant_data.news.website_listings.subprocess.run", return_value=result) as run:
            response = StdlibWebsiteTransport().get("finviz")
        self.assertEqual(response.body, body)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], [sys.executable, "-m", "quant_data.news.website_listings", "finviz"])
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["env"], {"PYTHONDONTWRITEBYTECODE": "1"})
        with patch("quant_data.news.website_listings.subprocess.run") as run:
            with self.assertRaises(ValidationError):
                StdlibWebsiteTransport().get("https://not-allowed.example")
            run.assert_not_called()

    def test_worker_protocol_round_trip_without_network(self):
        child = """import sys
from quant_data.news import website_listings as module
class Response:
 def get(self, source):
  assert source == "finviz"
  return module.WebsiteResponse(200, "text/html", b"<html>retained & bytes</html>")
module._WebsiteRequest = Response
sys.argv = ["website_listings", "finviz"]
raise SystemExit(module._worker_main())
"""
        real_run = subprocess.run
        def run_worker(_args, **kwargs):
            return real_run([sys.executable, "-c", child], **kwargs)
        with patch("quant_data.news.website_listings.subprocess.run", side_effect=run_worker) as run:
            response = StdlibWebsiteTransport().get("finviz")
        self.assertEqual(response, WebsiteResponse(200, "text/html", b"<html>retained & bytes</html>"))
        run.assert_called_once()

    def test_worker_deadline_terminates_slow_header_acquisition(self):
        # Exercise real HTTPResponse header parsing, but use only an in-memory
        # stream in the child: no DNS, socket, provider or credential.
        child = r"""import http.client, io, pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
class Slow(io.BytesIO):
 def readline(self, *args):
  marker.write_text(str(int(marker.read_text()) + 1) if marker.exists() else "1")
  time.sleep(0.04)
  return super().readline(*args)
class Socket:
 def makefile(self, *args):
  return Slow(b"HTTP/1.1 200 OK\r\n" + b"X-Test: value\r\n" * 70 + b"\r\n")
http.client.HTTPResponse(Socket()).begin()
"""
        real_run = subprocess.run
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            marker = Path(directory) / "headers"
            def run_worker(_args, **kwargs):
                return real_run([sys.executable, "-c", child, str(marker)], **kwargs)
            start = time.monotonic()
            with patch("quant_data.news.website_listings.TIMEOUT_SECONDS", 0.3):
                with patch("quant_data.news.website_listings.subprocess.run", side_effect=run_worker) as run:
                    with self.assertRaises(ResourceLimitError):
                        StdlibWebsiteTransport().get("finviz")
            self.assertLess(time.monotonic() - start, 3)
            self.assertTrue(marker.exists())
            consumed = int(marker.read_text())
            self.assertLess(consumed, 70)
            time.sleep(0.1)
            self.assertEqual(int(marker.read_text()), consumed)  # Worker was reaped, not left running.
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
