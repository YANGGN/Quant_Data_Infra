"""Fixed, credential-free Finviz HTML and FinancialJuice RSS collectors.

This module fetches and normalizes bounded headline listings. It does not open a
database or schedule a job. A publisher can consume the immutable capture after
all network work has finished. Article destinations are metadata, never fetched.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError

SOURCE_URLS = {
    "finviz": "https://finviz.com/news",
    "financialjuice": "https://www.financialjuice.com/feed.ashx?xy=rss",
}
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 1000
TIMEOUT_SECONDS = 60
NORMALIZATION_VERSION = "news.website_listings.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _text(value: str | None, *, limit: int) -> str:
    result = " ".join((value or "").split())
    if len(result) > limit:
        raise ResourceLimitError("Website news text exceeds its bound")
    return result


def _urls(value: str | None) -> tuple[str, str]:
    if not value or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValidationError("Website news article URL is invalid")
    try:
        url = urlsplit(value)
        if (url.scheme not in {"https", "http"} or not url.hostname
                or url.username is not None or url.password is not None
                or "\\" in value):
            raise ValueError
        port = url.port
    except ValueError as exc:
        raise ValidationError("Website news article URL is invalid") from exc
    host = url.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and (url.scheme, port) not in {("https", 443), ("http", 80)}:
        host += ":" + str(port)
    query = [(key, val) for key, val in parse_qsl(url.query, keep_blank_values=True)
             if not key.lower().startswith("utm_")
             and key.lower() not in {"fbclid", "gclid"}]
    canonical = urlunsplit((url.scheme, host, url.path or "/",
                           urlencode(query), ""))
    return value, canonical


@dataclass(frozen=True, slots=True)
class WebsiteHeadline:
    source_id: str
    source_item_id: str
    headline: str
    summary: str
    source_name: str
    source_url: str
    canonical_url: str
    published_date_raw: str | None
    published_normalized_at: str | None
    published_precision: str
    source_row: int

    @property
    def article_id(self) -> str:
        return _digest({"source": self.source_id, "item": self.source_item_id})

    def content(self) -> dict[str, object]:
        # Tracking changes and capture clocks cannot create a content version.
        return {
            "headline": self.headline, "summary": self.summary,
            "source_name": self.source_name, "canonical_url": self.canonical_url,
            "published_date_raw": self.published_date_raw,
            "published_normalized_at": self.published_normalized_at,
            "published_precision": self.published_precision,
        }

    @property
    def content_sha256(self) -> str:
        return _digest(self.content())


@dataclass(frozen=True, slots=True)
class WebsiteResponse:
    status: int
    content_type: str
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("Website news response status is invalid")
        if not isinstance(self.content_type, str) or not isinstance(self.body, bytes):
            raise ValidationError("Website news response is invalid")
        if len(self.body) > MAX_BYTES:
            raise ResourceLimitError("Website news response exceeds its byte bound")


@dataclass(frozen=True, slots=True)
class WebsiteCapture:
    source_id: str
    request_url: str
    captured_at: str
    response: WebsiteResponse
    headlines: tuple[WebsiteHeadline, ...]
    provider_row_count: int
    duplicate_row_count: int
    normalization_version: str = NORMALIZATION_VERSION

    @property
    def response_sha256(self) -> str:
        return hashlib.sha256(self.response.body).hexdigest()

    @property
    def semantic_sha256(self) -> str:
        return _digest({
            "source_id": self.source_id,
            "normalization_version": self.normalization_version,
            "headlines": sorted((h.article_id, h.content_sha256) for h in self.headlines),
        })


class WebsiteTransport(Protocol):
    def get(self, source_id: str) -> WebsiteResponse: ...


class StdlibWebsiteTransport:
    """One isolated request with a process deadline covering DNS, TLS and headers."""

    def get(self, source_id: str) -> WebsiteResponse:
        if source_id not in SOURCE_URLS:
            raise ValidationError("Website news source is unsupported")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "quant_data.news.website_listings", source_id],
                cwd=Path(__file__).resolve().parents[2],
                env={"PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run terminates and reaps the worker before raising.
            raise ResourceLimitError("Website news request exceeded its deadline") from exc
        except OSError as exc:
            raise StoreUnavailableError("Website news request could not start") from exc
        if result.returncode:
            error = {64: ValidationError, 75: ResourceLimitError}.get(
                result.returncode, StoreUnavailableError)
            raise error("Website news request was rejected")
        if len(result.stdout) > MAX_BYTES + 65536:
            raise ResourceLimitError("Website news worker response exceeds its bound")
        try:
            header, separator, body = result.stdout.partition(b"\n")
            metadata = json.loads(header)
            if not separator or set(metadata) != {"status", "content_type"}:
                raise ValueError
            return WebsiteResponse(metadata["status"], metadata["content_type"], body)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValidationError("Website news worker response is invalid") from exc


class _WebsiteRequest:
    """One fixed TLS request; no redirects, cookies, credentials, or retries."""

    def get(self, source_id: str) -> WebsiteResponse:
        if source_id not in SOURCE_URLS:
            raise ValidationError("Website news source is unsupported")
        url = urlsplit(SOURCE_URLS[source_id])
        target = url.path + ("?" + url.query if url.query else "")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        conn = http.client.HTTPSConnection(url.hostname, timeout=TIMEOUT_SECONDS)
        try:
            conn.request("GET", target, headers={
                "User-Agent": "QuantDataInfra/1.0 (news listing collector)",
                "Accept": "text/html" if source_id == "finviz" else "application/rss+xml,application/xml,text/xml",
                "Accept-Encoding": "identity",
            })
            network_socket = getattr(conn, "sock", None)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResourceLimitError("Website news request exceeded its deadline")
            if network_socket is not None:
                network_socket.settimeout(remaining)
            response = conn.getresponse()
            if response.status != 200:
                raise StoreUnavailableError("Website news returned non-success status " + str(response.status))
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding != "identity":
                raise ValidationError("Website news response encoding is unsupported")
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError as exc:
                    raise ValidationError("Website news response length is invalid") from exc
                if declared < 0 or declared > MAX_BYTES:
                    raise ResourceLimitError("Website news response exceeds its byte bound")
            chunks: list[bytes] = []
            received = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ResourceLimitError("Website news request exceeded its deadline")
                if network_socket is not None:
                    network_socket.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - received))
                if time.monotonic() > deadline:
                    raise ResourceLimitError("Website news request exceeded its deadline")
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_BYTES:
                    raise ResourceLimitError("Website news response exceeds its byte bound")
                chunks.append(chunk)
            return WebsiteResponse(response.status,
                response.getheader("Content-Type") or "", b"".join(chunks))
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Website news request failed") from exc
        finally:
            conn.close()


class _FinvizParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.row: dict[str, str] | None = None
        self.cell: str | None = None
        self.anchor = False
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "tr" and "news_table-row" in classes:
            if self.row is not None:
                raise ValidationError("Finviz listing rows are malformed")
            self.row = {"headline": "", "published": "", "summary": "", "url": ""}
        if self.row is None:
            return
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag == "td":
            self.cell = ("link" if "news_link-cell" in classes else
                         "date" if "news_date-cell" in classes else None)
            if self.cell == "link":
                self.row["summary"] = attr.get("data-boxover-text") or ""
        if tag == "a" and self.cell == "link":
            if self.row["url"]:
                raise ValidationError("Finviz listing row has ambiguous links")
            self.row["url"] = attr.get("href") or ""
            self.anchor = True

    def handle_data(self, data: str) -> None:
        if self.row is None or self.suppressed:
            return
        if self.cell == "link" and self.anchor:
            self.row["headline"] += data
        elif self.cell == "date":
            self.row["published"] += data

    def handle_endtag(self, tag: str) -> None:
        if self.row is None:
            return
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        if tag == "a":
            self.anchor = False
        if tag == "td":
            self.cell = None
        if tag == "tr":
            if len(self.rows) >= MAX_ROWS:
                raise ResourceLimitError("Finviz listing exceeds its row bound")
            self.rows.append(self.row)
            self.row = None
            self.cell = None
            self.anchor = False
            self.suppressed = 0


def _finviz(body: bytes) -> list[WebsiteHeadline]:
    parser = _FinvizParser()
    try:
        parser.feed(body.decode("utf-8-sig"))
        parser.close()
    except UnicodeError as exc:
        raise ValidationError("Finviz listing encoding is invalid") from exc
    if parser.row is not None or not parser.rows:
        raise ValidationError("Finviz response has no complete supported listing")
    result = []
    for source_row, row in enumerate(parser.rows, start=1):
        source_url, canonical_url = _urls(row["url"])
        title = _text(row["headline"], limit=4096)
        if not title:
            raise ValidationError("Finviz listing headline is missing")
        raw = _text(row["published"], limit=128) or None
        # Finviz displays time-only/month-day labels. Do not infer year, date,
        # timezone, or a publication instant from the local capture clock.
        precision = "unknown" if raw else "missing"
        if raw and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                from datetime import date
                date.fromisoformat(raw)
            except ValueError as exc:
                raise ValidationError("Finviz publication date is invalid") from exc
            precision = "date"
        result.append(WebsiteHeadline(
            "finviz", canonical_url, title,
            _text(row["summary"], limit=32768),
            urlsplit(source_url).hostname or "finviz", source_url, canonical_url,
            raw, None, precision, source_row,
        ))
    return result


def _financialjuice(body: bytes) -> list[WebsiteHeadline]:
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
        raise ValidationError("FinancialJuice RSS declarations are unsupported")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValidationError("FinancialJuice RSS is invalid") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValidationError("FinancialJuice response is not an RSS channel")
    entries = channel.findall("item")
    if not entries:
        raise ValidationError("FinancialJuice response has no supported headlines")
    if len(entries) > MAX_ROWS:
        raise ResourceLimitError("FinancialJuice RSS exceeds its row bound")
    result = []
    for source_row, entry in enumerate(entries, start=1):
        title = _text(entry.findtext("title"), limit=4096)
        item_id = _text(entry.findtext("guid"), limit=128)
        source_url, canonical_url = _urls(entry.findtext("link"))
        url = urlsplit(source_url)
        path_id = re.match(r"^/News/([0-9]+)/[^/]+\.aspx$", url.path)
        if (not title or not item_id.isascii() or not item_id.isdecimal()
                or url.hostname not in {"financialjuice.com", "www.financialjuice.com"}
                or path_id is None or path_id.group(1) != item_id):
            raise ValidationError("FinancialJuice item identity is invalid")
        raw = _text(entry.findtext("pubDate"), limit=128) or None
        instant = None
        precision = "missing"
        if raw is not None:
            try:
                published = parsedate_to_datetime(raw)
            except (ValueError, TypeError, IndexError) as exc:
                raise ValidationError("FinancialJuice publication time is invalid") from exc
            if published.tzinfo is None or published.utcoffset() is None:
                raise ValidationError("FinancialJuice publication offset is missing")
            instant = published.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            precision = "datetime_offset"
        result.append(WebsiteHeadline(
            "financialjuice", item_id, title,
            _text(entry.findtext("description"), limit=32768),
            "FinancialJuice", source_url, canonical_url, raw, instant, precision, source_row,
        ))
    return result


def parse_website_response(
    source_id: str, response: WebsiteResponse, *, captured_at: datetime,
) -> WebsiteCapture:
    if source_id not in SOURCE_URLS:
        raise ValidationError("Website news source is unsupported")
    if (not isinstance(captured_at, datetime) or captured_at.tzinfo is None
            or captured_at.utcoffset() is None):
        raise ValidationError("Website news capture time must have an offset")
    if not isinstance(response, WebsiteResponse) or response.status != 200:
        raise ValidationError("Website news response was rejected")
    media = response.content_type.split(";", 1)[0].strip().lower()
    allowed = {"text/html"} if source_id == "finviz" else {
        "application/rss+xml", "application/xml", "text/xml",
    }
    if media not in allowed:
        raise ValidationError("Website news response media type is unsupported")
    rows = _finviz(response.body) if source_id == "finviz" else _financialjuice(response.body)
    by_id: dict[str, WebsiteHeadline] = {}
    for row in rows:
        prior = by_id.get(row.article_id)
        if prior is not None and prior.content_sha256 != row.content_sha256:
            raise ValidationError("Website news response has conflicting duplicate items")
        by_id.setdefault(row.article_id, row)
    return WebsiteCapture(
        source_id, SOURCE_URLS[source_id],
        captured_at.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        response, tuple(by_id.values()), len(rows), len(rows) - len(by_id),
    )


def collect_website_news(
    source_id: str, *, transport: WebsiteTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> WebsiteCapture:
    if source_id not in SOURCE_URLS:
        raise ValidationError("Website news source is unsupported")
    response = (transport if transport is not None else StdlibWebsiteTransport()).get(source_id)
    return parse_website_response(source_id, response, captured_at=clock())


def _worker_main() -> int:
    """Private fixed-source worker; stdout carries metadata and bounded raw bytes."""
    if len(sys.argv) != 2 or sys.argv[1] not in SOURCE_URLS:
        return 64
    try:
        response = _WebsiteRequest().get(sys.argv[1])
        header = json.dumps({"status": response.status, "content_type": response.content_type},
                            ensure_ascii=True, separators=(",", ":")).encode()
        if len(header) > 65535:
            return 75
        sys.stdout.buffer.write(header + b"\n" + response.body)
        return 0
    except ResourceLimitError:
        return 75
    except ValidationError:
        return 64
    except Exception:
        return 69


if __name__ == "__main__":
    raise SystemExit(_worker_main())
