"""Bounded current multi-source news capture and immutable publication.

The core has one fixed feed catalog.  Callers choose only a feed identifier,
an hourly slot, and (for Alpaca/Benzinga) a bounded already-approved symbol
batch.  Raw provider responses remain private evidence; public readers project
only stored headline metadata.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import sqlite3
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, StoreWriteLock, stable_id, writer_connection


CURRENT_MULTI_SOURCE_MIGRATION_ID = "news:0007_current_multi_source"
LEGACY_FMP_NEWS_MIGRATION_ID = "news:0008_adopt_fmp_news_legacy"
LEGACY_FMP_NEWS_DATASET_ID = "news.fmp.stock_latest_legacy_articles"
CURRENT_MULTI_SOURCE_COLLECTOR_ID = "news.current_multi_source"
CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID = "news.current_multi_source_evidence"
CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID = "news.current_multi_source_articles"
CURRENT_MULTI_SOURCE_NORMALIZATION_VERSION = "news.current_multi_source.v1"
CURRENT_MULTI_SOURCE_MAX_BYTES = 64 * 1024 * 1024
CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS = 60
CURRENT_MULTI_SOURCE_MAX_ROWS = 1000
ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST = 50

FMP_PRESS_RELEASES_URL = "https://financialmodelingprep.com/stable/news/press-releases-latest"
FMP_GENERAL_URL = "https://financialmodelingprep.com/stable/news/general-latest"
FED_PRESS_RSS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
ECB_PRESS_RSS_URL = "https://www.ecb.europa.eu/rss/press.html"
BEA_NEWS_RSS_URL = "https://apps.bea.gov/rss/rss.xml"
EIA_PRESS_RSS_URL = "https://www.eia.gov/rss/press_rss.xml"
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._^:-]{0,63}$")


@dataclass(frozen=True, slots=True)
class _FeedSpec:
    feed_id: str
    provider: str
    profile_id: str
    url: str
    format: str
    credential_kind: str | None
    max_rows: int


_FEED_SPECS = {
    "fmp_press_releases": _FeedSpec(
        "fmp_press_releases",
        "fmp",
        "fmp.press_releases.page0.limit1000.current.v1",
        FMP_PRESS_RELEASES_URL,
        "json",
        "fmp",
        1000,
    ),
    "fmp_general": _FeedSpec(
        "fmp_general",
        "fmp",
        "fmp.general.page0.limit1000.current.v1",
        FMP_GENERAL_URL,
        "json",
        "fmp",
        1000,
    ),
    "fed_press": _FeedSpec(
        "fed_press",
        "fed",
        "rss.fed.press.current.v1",
        FED_PRESS_RSS_URL,
        "xml",
        None,
        1000,
    ),
    "ecb_press": _FeedSpec(
        "ecb_press",
        "ecb",
        "rss.ecb.press.current.v1",
        ECB_PRESS_RSS_URL,
        "xml",
        None,
        1000,
    ),
    "bea_news": _FeedSpec(
        "bea_news",
        "bea",
        "rss.bea.news.current.v1",
        BEA_NEWS_RSS_URL,
        "xml",
        None,
        1000,
    ),
    "eia_press": _FeedSpec(
        "eia_press",
        "eia",
        "rss.eia.press.current.v1",
        EIA_PRESS_RSS_URL,
        "xml",
        None,
        1000,
    ),
    "alpaca_benzinga": _FeedSpec(
        "alpaca_benzinga",
        "alpaca",
        "alpaca.benzinga.symbol_batch.limit50.current.v1",
        ALPACA_NEWS_URL,
        "json",
        "alpaca",
        ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST,
    ),
}
CURRENT_MULTI_SOURCE_FEED_IDS = tuple(_FEED_SPECS)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _utc_hour(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_hour_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _absolute_http_url(value: object) -> str | None:
    candidate = _text(value)
    if candidate is None or any(ord(character) < 32 or character.isspace() for character in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _site(value: object, source_url: str | None, fallback: str) -> str:
    explicit = _text(value)
    if explicit is not None:
        return explicit
    if source_url is not None:
        try:
            host = urlsplit(source_url).hostname
        except ValueError:
            host = None
        if host:
            return host
    return fallback


def _published_metadata(value: str | None) -> tuple[str | None, str, str]:
    if value is None:
        return None, "missing", "missing"
    try:
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return None, "date", "unknown"
    except ValueError:
        pass
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None, "unknown", "unknown"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "datetime_naive", "unknown"
    return (
        parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "datetime_offset",
        "known",
    )


def _symbols(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw = (value,) if isinstance(value, str) else value
    if isinstance(raw, (bytes, str)) or not isinstance(raw, Sequence):
        return ()
    result: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        candidate = item.strip().upper()
        if _SYMBOL.fullmatch(candidate):
            result.add(candidate)
    return tuple(sorted(result))


def _source_item_key(
    *,
    provider_id: object,
    source_url: str | None,
    published_raw: str | None,
    title: str,
) -> str | None:
    identifier = (
        str(provider_id)
        if isinstance(provider_id, int) and not isinstance(provider_id, bool)
        else _text(provider_id)
    )
    if identifier is not None:
        return f"id:{identifier}"
    if source_url is not None:
        return f"url:{source_url}"
    if published_raw is not None:
        return f"fallback:{published_raw}\x1f{title}"
    return None


@dataclass(frozen=True, slots=True)
class CurrentMultiSourceRequest:
    """One fixed current feed and, only for Alpaca, one bounded symbol batch."""

    feed_id: str
    poll_slot: datetime
    symbols: tuple[str, ...] | list[str] | None = None

    def __post_init__(self) -> None:
        spec = _FEED_SPECS.get(self.feed_id)
        if spec is None:
            raise ValidationError("Current multi-source news feed is unsupported")
        slot = _utc_hour(self.poll_slot, "poll_slot")
        symbols = _symbols(self.symbols)
        if spec.feed_id == "alpaca_benzinga":
            if not symbols or len(symbols) > ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST:
                raise ResourceLimitError(
                    "Alpaca company-news request requires 1 through 50 symbols"
                )
        elif self.symbols is not None:
            raise ValidationError("Only Alpaca company-news requests accept symbols")
        object.__setattr__(self, "poll_slot", slot)
        object.__setattr__(self, "symbols", symbols)

    @property
    def poll_slot_text(self) -> str:
        return _utc_hour_text(self.poll_slot)

    @property
    def spec(self) -> _FeedSpec:
        return _FEED_SPECS[self.feed_id]

    def request_scope(self) -> dict[str, object]:
        return {
            "feed_id": self.feed_id,
            "poll_slot": self.poll_slot_text,
            "provider": self.spec.provider,
            "profile_id": self.spec.profile_id,
            "symbols": list(self.symbols),
            "url": self.spec.url,
        }


@dataclass(frozen=True, slots=True)
class CurrentMultiSourceCredentials:
    """Credential placeholders supplied by the operational wrapper, never stored."""

    fmp_api_key: str | None = None
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None

    def __post_init__(self) -> None:
        for value in (self.fmp_api_key, self.alpaca_api_key, self.alpaca_api_secret):
            if value is not None and (not isinstance(value, str) or _API_KEY.fullmatch(value) is None):
                raise ValidationError("Current multi-source news credential is invalid")


@dataclass(frozen=True, slots=True)
class CapturedCurrentMultiSourceResponse:
    status: int
    content_type: str
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("Current multi-source response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("Current multi-source response content type is invalid")
        if not isinstance(self.body, bytes):
            raise ValidationError("Current multi-source response body must be bytes")
        if len(self.body) > CURRENT_MULTI_SOURCE_MAX_BYTES:
            raise ResourceLimitError("Current multi-source response exceeds the byte bound")


@runtime_checkable
class CurrentMultiSourceTransport(Protocol):
    def get(
        self,
        *,
        url: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedCurrentMultiSourceResponse: ...


class StdlibCurrentMultiSourceTransport:
    """TLS-only, no-redirect, no-retry transport for fixed feed requests."""

    def get(
        self,
        *,
        url: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedCurrentMultiSourceResponse:
        if (
            url not in {spec.url for spec in _FEED_SPECS.values()}
            or not isinstance(query, Mapping)
            or not isinstance(headers, Mapping)
            or timeout_seconds != CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS
            or max_bytes != CURRENT_MULTI_SOURCE_MAX_BYTES
        ):
            raise ValidationError("Current multi-source transport request is outside scope")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValidationError("Current multi-source transport URL is invalid")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        if query:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}{urlencode(dict(query))}"
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout_seconds)
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                raise StoreUnavailableError("Current multi-source news redirects are not allowed")
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise StoreUnavailableError("Current multi-source provider response was unavailable") from exc
                if declared_size < 0 or declared_size > max_bytes:
                    raise ResourceLimitError("Current multi-source response exceeds the byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("Current multi-source response exceeds the byte bound")
            return CapturedCurrentMultiSourceResponse(
                status=response.status,
                content_type=response.getheader("Content-Type") or "application/octet-stream",
                body=body,
            )
        except (ResourceLimitError, ValidationError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Current multi-source provider request failed") from exc
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class CurrentMultiSourceReceipt:
    attempt_id: str
    feed_id: str
    poll_slot: str
    outcome: str
    capture_id: str | None
    provider_row_count: int
    accepted_row_count: int
    rejected_row_count: int
    written_count: int


@dataclass(frozen=True, slots=True)
class _Article:
    feed_id: str
    provider: str
    source_item_key: str
    title: str
    summary: str
    site: str
    source_url: str | None
    published_date_raw: str | None
    published_normalized_at: str | None
    published_precision: str
    published_offset_status: str
    symbols: tuple[str, ...]
    source_row: int

    @property
    def article_id(self) -> str:
        return stable_id("current_multi_source_article", self.feed_id, self.source_item_key)

    def mutable_content(self) -> dict[str, object]:
        return {
            "published_date_raw": self.published_date_raw,
            "published_normalized_at": self.published_normalized_at,
            "published_offset_status": self.published_offset_status,
            "published_precision": self.published_precision,
            "site": self.site,
            "source_url": self.source_url,
            "summary": self.summary,
            "symbols": list(self.symbols),
            "title": self.title,
        }

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "article_id": self.article_id,
            "mutable_content_sha256": _sha256_json(self.mutable_content()),
        }


@dataclass(frozen=True, slots=True)
class _Parsed:
    articles: tuple[_Article, ...]
    provider_row_count: int
    rejected_row_count: int

    @property
    def accepted_row_count(self) -> int:
        return len(self.articles)


def _article(
    *,
    spec: _FeedSpec,
    source_row: int,
    provider_id: object,
    title: object,
    summary: object,
    site: object,
    source_url: object,
    published: object,
    symbols: object,
) -> _Article | None:
    headline = _text(title)
    if headline is None:
        return None
    url = _absolute_http_url(source_url)
    published_raw = _text(published)
    source_item_key = _source_item_key(
        provider_id=provider_id,
        source_url=url,
        published_raw=published_raw,
        title=headline,
    )
    if source_item_key is None:
        return None
    normalized_at, precision, offset_status = _published_metadata(published_raw)
    return _Article(
        feed_id=spec.feed_id,
        provider=spec.provider,
        source_item_key=source_item_key,
        title=headline,
        summary=_text(summary) or "",
        site=_site(site, url, spec.provider),
        source_url=url,
        published_date_raw=published_raw,
        published_normalized_at=normalized_at,
        published_precision=precision,
        published_offset_status=offset_status,
        symbols=_symbols(symbols),
        source_row=source_row,
    )


def _json_rows(spec: _FeedSpec, body: bytes) -> _Parsed:
    try:
        decoded = body.decode("utf-8", errors="strict")
        payload = loads_strict(decoded, max_bytes=CURRENT_MULTI_SOURCE_MAX_BYTES)
    except (UnicodeDecodeError, ResourceLimitError, ValidationError) as exc:
        raise ValidationError("Current multi-source JSON response is invalid") from exc
    if spec.feed_id == "alpaca_benzinga":
        if isinstance(payload, Mapping):
            payload = payload.get("news")
    if not isinstance(payload, list) or len(payload) > spec.max_rows:
        raise ValidationError("Current multi-source JSON response exceeds its row bound")
    articles: list[_Article] = []
    rejected = 0
    seen: set[str] = set()
    for source_row, raw in enumerate(payload, start=1):
        if not isinstance(raw, Mapping):
            rejected += 1
            continue
        if spec.feed_id == "alpaca_benzinga":
            parsed = _article(
                spec=spec,
                source_row=source_row,
                provider_id=raw.get("id"),
                title=raw.get("headline"),
                summary=raw.get("summary"),
                site=raw.get("source"),
                source_url=raw.get("url"),
                published=raw.get("updated_at") or raw.get("created_at"),
                symbols=raw.get("symbols"),
            )
        else:
            parsed = _article(
                spec=spec,
                source_row=source_row,
                provider_id=raw.get("id") or raw.get("newsId"),
                title=raw.get("title") or raw.get("headline"),
                summary=raw.get("summary"),
                site=raw.get("site") or raw.get("publisher") or raw.get("source"),
                source_url=raw.get("url") or raw.get("link"),
                published=raw.get("publishedDate") or raw.get("published_at") or raw.get("date"),
                symbols=raw.get("symbols") if raw.get("symbols") is not None else raw.get("symbol"),
            )
        if parsed is None or parsed.article_id in seen:
            rejected += 1
            continue
        seen.add(parsed.article_id)
        articles.append(parsed)
    return _Parsed(tuple(articles), len(payload), rejected)


def _local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = set(names)
    for child in element:
        if _local_name(child) in wanted:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return None


def _entry_url(element: ElementTree.Element) -> str | None:
    for child in element:
        if _local_name(child) != "link":
            continue
        candidate = _absolute_http_url(child.attrib.get("href") or "".join(child.itertext()).strip())
        if candidate is not None:
            return candidate
    return None


def _rss_rows(spec: _FeedSpec, body: bytes) -> _Parsed:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ValidationError("Current multi-source RSS response is invalid") from exc
    entries = [
        element for element in root.iter() if _local_name(element) in {"item", "entry"}
    ]
    if len(entries) > spec.max_rows:
        raise ValidationError("Current multi-source RSS response exceeds its row bound")
    articles: list[_Article] = []
    rejected = 0
    seen: set[str] = set()
    for source_row, entry in enumerate(entries, start=1):
        parsed = _article(
            spec=spec,
            source_row=source_row,
            provider_id=_element_text(entry, "guid", "id"),
            title=_element_text(entry, "title"),
            summary=_element_text(entry, "description", "summary"),
            site=spec.provider,
            source_url=_entry_url(entry),
            published=_element_text(entry, "pubdate", "published", "updated", "date"),
            symbols=None,
        )
        if parsed is None or parsed.article_id in seen:
            rejected += 1
            continue
        seen.add(parsed.article_id)
        articles.append(parsed)
    return _Parsed(tuple(articles), len(entries), rejected)


def _content_type_matches(spec: _FeedSpec, content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if spec.format == "json":
        return media_type == "application/json" or media_type.endswith("+json")
    return media_type in {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    } or media_type.endswith("+xml")


def _parse_response(spec: _FeedSpec, response: CapturedCurrentMultiSourceResponse) -> _Parsed:
    if response.status != 200 or not _content_type_matches(spec, response.content_type):
        raise ValidationError("Current multi-source provider response was rejected")
    parsed = _rss_rows(spec, response.body) if spec.format == "xml" else _json_rows(spec, response.body)
    if parsed.provider_row_count and not parsed.accepted_row_count:
        raise ValidationError("Current multi-source response has no usable rows")
    return parsed


def _validate_registry(registry: Registry) -> None:
    if not isinstance(registry, Registry):
        raise ValidationError("Current multi-source importer requires a registry")
    migrations = {
        item.id: item for item in registry.migrations_for(StoreRole.NEWS.value)
    }
    migration = migrations.get(CURRENT_MULTI_SOURCE_MIGRATION_ID)
    if (
        migration is None
        or migration.ordinal != 7
        or migration.store != StoreRole.NEWS.value
        or CURRENT_MULTI_SOURCE_MIGRATION_ID not in registry.store(StoreRole.NEWS.value).migration_order
    ):
        raise ValidationError("Current multi-source migration is not registered")
    datasets = {item.id: item for item in registry.datasets}
    expected_relations = {
        CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID: (
            "current_multi_source_attempts",
            "current_multi_source_outcomes",
            "current_multi_source_captures",
        ),
        CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID: (
            "current_multi_source_articles",
            "current_multi_source_article_versions",
            "current_multi_source_capture_articles",
            "current_multi_source_article_symbols",
        ),
    }
    for dataset_id, relations in expected_relations.items():
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != StoreRole.NEWS.value
            or dataset.relations != relations
            or not dataset.active
        ):
            raise ValidationError("Current multi-source dataset routing is invalid")
    matches = [
        item for item in registry.collectors if item.get("id") == CURRENT_MULTI_SOURCE_COLLECTOR_ID
    ]
    if len(matches) != 1:
        raise ValidationError("Current multi-source collector is not registered exactly once")
    collector = matches[0]
    if (
        collector.get("handler") != "news.current_multi_source"
        or collector.get("network") is not True
        or tuple(collector.get("output_datasets", ())) != (
            CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID,
            CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID,
        )
    ):
        raise ValidationError("Current multi-source collector declaration is invalid")


def _wire_request(
    request: CurrentMultiSourceRequest,
    credentials: CurrentMultiSourceCredentials,
) -> tuple[str, dict[str, str], dict[str, str]]:
    spec = request.spec
    if spec.credential_kind == "fmp":
        if credentials.fmp_api_key is None:
            raise ValidationError("FMP news credential placeholder FMP_API_KEY is required")
        return (
            spec.url,
            {"page": "0", "limit": "1000"},
            {"apikey": credentials.fmp_api_key, "Accept": "application/json"},
        )
    if spec.credential_kind == "alpaca":
        if credentials.alpaca_api_key is None or credentials.alpaca_api_secret is None:
            raise ValidationError(
                "Alpaca news credential placeholders ALPACA_API_KEY and ALPACA_API_SECRET are required"
            )
        return (
            spec.url,
            {
                "symbols": ",".join(request.symbols),
                "limit": str(ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST),
                "include_content": "false",
                "sort": "desc",
            },
            {
                "APCA-API-KEY-ID": credentials.alpaca_api_key,
                "APCA-API-SECRET-KEY": credentials.alpaca_api_secret,
                "Accept": "application/json",
            },
        )
    return (
        spec.url,
        {},
        {
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )


class CurrentMultiSourceImporter:
    """Append one bounded fixed-feed result after network work finishes."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("Current multi-source importer requires stores and a clock")
        _validate_registry(registry)
        self.store_map = store_map
        self.registry = registry
        self._clock = clock

    def _write(self, callback: Callable[[sqlite3.Connection], object]) -> object:
        with StoreWriteLock(self.store_map.path(StoreRole.NEWS)), writer_connection(
            self.store_map, StoreRole.NEWS
        ) as connection:
            return callback(connection)

    def _reserve(self, request: CurrentMultiSourceRequest) -> tuple[str, str, dict[str, object], str]:
        scope = request.request_scope()
        scope_sha256 = _sha256_json(scope)
        attempt_id = stable_id(
            "current_multi_source_attempt", request.spec.profile_id, scope_sha256
        )
        recorded_at = _utc_text(self._clock(), "intent_recorded_at")

        def reserve(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT 1 FROM current_multi_source_attempts
                    WHERE collector_id=? AND feed_id=? AND request_scope_sha256=?
                    """,
                    (CURRENT_MULTI_SOURCE_COLLECTOR_ID, request.feed_id, scope_sha256),
                ).fetchone()
                if existing is not None:
                    raise ConflictError("Current multi-source news intent already blocks this scope")
                connection.execute(
                    """
                    INSERT INTO current_multi_source_attempts (
                        attempt_id, dataset_id, collector_id, feed_id, profile_id, poll_slot,
                        request_scope_json, request_scope_sha256,
                        intent_recorded_at, intent_recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        attempt_id,
                        CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID,
                        CURRENT_MULTI_SOURCE_COLLECTOR_ID,
                        request.feed_id,
                        request.spec.profile_id,
                        request.poll_slot_text,
                        dumps_strict(scope),
                        scope_sha256,
                        recorded_at,
                    ),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

        self._write(reserve)
        return attempt_id, request.poll_slot_text, scope, scope_sha256

    def _record_failure(
        self,
        *,
        attempt_id: str,
        request: CurrentMultiSourceRequest,
        outcome_kind: str,
        response: CapturedCurrentMultiSourceResponse | None,
        parsed: _Parsed | None,
    ) -> CurrentMultiSourceReceipt:
        if outcome_kind not in {"request_failed", "response_rejected", "publication_failed"}:
            raise ValidationError("Current multi-source outcome is invalid")
        outcome_id = stable_id("current_multi_source_outcome", attempt_id, outcome_kind)
        recorded_at = _utc_text(self._clock(), "recorded_at")
        counts = (None, None, None) if parsed is None else (
            parsed.provider_row_count,
            parsed.accepted_row_count,
            parsed.rejected_row_count,
        )

        def record(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM current_multi_source_outcomes WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone() is not None:
                    raise ConflictError("Current multi-source news attempt already has an outcome")
                connection.execute(
                    """
                    INSERT INTO current_multi_source_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        provider_row_count, accepted_row_count, rejected_row_count,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        attempt_id,
                        outcome_kind,
                        None if response is None else _sha256(response.body),
                        None if response is None else len(response.body),
                        None if response is None else response.status,
                        None if response is None else response.content_type,
                        *counts,
                        recorded_at,
                    ),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

        self._write(record)
        return CurrentMultiSourceReceipt(
            attempt_id=attempt_id,
            feed_id=request.feed_id,
            poll_slot=request.poll_slot_text,
            outcome=outcome_kind,
            capture_id=None,
            provider_row_count=0 if counts[0] is None else counts[0],
            accepted_row_count=0 if counts[1] is None else counts[1],
            rejected_row_count=0 if counts[2] is None else counts[2],
            written_count=1,
        )

    def _publish(
        self,
        *,
        attempt_id: str,
        request: CurrentMultiSourceRequest,
        scope: Mapping[str, object],
        scope_sha256: str,
        response: CapturedCurrentMultiSourceResponse,
        parsed: _Parsed,
    ) -> CurrentMultiSourceReceipt:
        captured_at = _utc_text(self._clock(), "captured_at")
        semantic_rows = sorted((item.semantic_mapping() for item in parsed.articles), key=dumps_strict)
        semantic_identity = _sha256_json(
            {
                "collector_id": CURRENT_MULTI_SOURCE_COLLECTOR_ID,
                "normalization_version": CURRENT_MULTI_SOURCE_NORMALIZATION_VERSION,
                "normalized_partial_feed": semantic_rows,
                "request_scope": dict(scope),
            }
        )
        capture_id = stable_id("current_multi_source_capture", attempt_id, semantic_identity)
        outcome_id = stable_id("current_multi_source_outcome", attempt_id, "succeeded")

        def publish(connection: sqlite3.Connection) -> CurrentMultiSourceReceipt:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM current_multi_source_outcomes WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone() is not None:
                    raise ConflictError("Current multi-source news attempt already has an outcome")
                response_sha256 = _sha256(response.body)
                connection.execute(
                    """
                    INSERT INTO current_multi_source_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        provider_row_count, accepted_row_count, rejected_row_count,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, 'succeeded', ?, ?, 200, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        attempt_id,
                        response_sha256,
                        len(response.body),
                        response.content_type,
                        parsed.provider_row_count,
                        parsed.accepted_row_count,
                        parsed.rejected_row_count,
                        captured_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO current_multi_source_captures (
                        capture_id, dataset_id, attempt_id, outcome_id, feed_id,
                        request_scope_sha256, response_sha256, response_bytes,
                        http_status, content_type, semantic_identity, completeness,
                        captured_at, captured_precision, provider_row_count,
                        accepted_row_count, rejected_row_count, normalization_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 200, ?, ?, 'partial',
                              ?, 'datetime', ?, ?, ?, ?)
                    """,
                    (
                        capture_id,
                        CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID,
                        attempt_id,
                        outcome_id,
                        request.feed_id,
                        scope_sha256,
                        response_sha256,
                        response.body,
                        response.content_type,
                        semantic_identity,
                        captured_at,
                        parsed.provider_row_count,
                        parsed.accepted_row_count,
                        parsed.rejected_row_count,
                        CURRENT_MULTI_SOURCE_NORMALIZATION_VERSION,
                    ),
                )
                written = 2
                for item in parsed.articles:
                    existing = connection.execute(
                        "SELECT article_id FROM current_multi_source_articles WHERE article_id=?",
                        (item.article_id,),
                    ).fetchone()
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO current_multi_source_articles (
                                article_id, feed_id, provider, source_item_key, created_capture_id
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                item.article_id,
                                item.feed_id,
                                item.provider,
                                item.source_item_key,
                                capture_id,
                            ),
                        )
                        written += 1
                    content_sha256 = _sha256_json(item.mutable_content())
                    latest = connection.execute(
                        """
                        SELECT article_version_id, mutable_content_sha256, version_sequence
                        FROM current_multi_source_article_versions
                        WHERE article_id=?
                        ORDER BY version_sequence DESC
                        LIMIT 1
                        """,
                        (item.article_id,),
                    ).fetchone()
                    if latest is not None and latest["mutable_content_sha256"] == content_sha256:
                        version_id = str(latest["article_version_id"])
                    else:
                        sequence = 1 if latest is None else int(latest["version_sequence"]) + 1
                        supersedes = None if latest is None else str(latest["article_version_id"])
                        version_id = stable_id(
                            "current_multi_source_article_version",
                            item.article_id,
                            content_sha256,
                            str(sequence),
                        )
                        connection.execute(
                            """
                            INSERT INTO current_multi_source_article_versions (
                                article_version_id, article_id, mutable_content_sha256,
                                title, summary, site, source_url, published_date_raw,
                                published_normalized_at, published_precision,
                                published_offset_status, version_sequence,
                                supersedes_article_version_id, capture_id, source_row
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                version_id,
                                item.article_id,
                                content_sha256,
                                item.title,
                                item.summary,
                                item.site,
                                item.source_url,
                                item.published_date_raw,
                                item.published_normalized_at,
                                item.published_precision,
                                item.published_offset_status,
                                sequence,
                                supersedes,
                                capture_id,
                                item.source_row,
                            ),
                        )
                        written += 1
                        for symbol in item.symbols:
                            connection.execute(
                                """
                                INSERT INTO current_multi_source_article_symbols (
                                    article_version_id, provider_symbol
                                ) VALUES (?, ?)
                                """,
                                (version_id, symbol),
                            )
                            written += 1
                    connection.execute(
                        """
                        INSERT INTO current_multi_source_capture_articles (
                            capture_id, article_version_id, source_row
                        ) VALUES (?, ?, ?)
                        """,
                        (capture_id, version_id, item.source_row),
                    )
                    written += 1
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            return CurrentMultiSourceReceipt(
                attempt_id=attempt_id,
                feed_id=request.feed_id,
                poll_slot=request.poll_slot_text,
                outcome="succeeded",
                capture_id=capture_id,
                provider_row_count=parsed.provider_row_count,
                accepted_row_count=parsed.accepted_row_count,
                rejected_row_count=parsed.rejected_row_count,
                written_count=written,
            )

        return self._write(publish)  # type: ignore[return-value]

    def run_once(
        self,
        *,
        request: CurrentMultiSourceRequest,
        credentials: CurrentMultiSourceCredentials,
        transport: CurrentMultiSourceTransport,
    ) -> CurrentMultiSourceReceipt:
        """Reserve one feed scope, fetch outside the lock, then append one result."""

        if not isinstance(request, CurrentMultiSourceRequest):
            raise ValidationError("Current multi-source news request is invalid")
        if not isinstance(credentials, CurrentMultiSourceCredentials):
            raise ValidationError("Current multi-source news credentials are invalid")
        if not isinstance(transport, CurrentMultiSourceTransport):
            raise ValidationError("Current multi-source news transport is invalid")
        attempt_id, _, scope, scope_sha256 = self._reserve(request)
        response: CapturedCurrentMultiSourceResponse | None = None
        parsed: _Parsed | None = None
        try:
            url, query, headers = _wire_request(request, credentials)
            response = transport.get(
                url=url,
                query=query,
                headers=headers,
                timeout_seconds=CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
                max_bytes=CURRENT_MULTI_SOURCE_MAX_BYTES,
            )
            if not isinstance(response, CapturedCurrentMultiSourceResponse):
                raise ValidationError("Current multi-source transport returned an invalid response")
            parsed = _parse_response(request.spec, response)
        except ValidationError:
            self._record_failure(
                attempt_id=attempt_id,
                request=request,
                outcome_kind="response_rejected",
                response=response,
                parsed=parsed,
            )
            raise
        except Exception:
            self._record_failure(
                attempt_id=attempt_id,
                request=request,
                outcome_kind="request_failed",
                response=response,
                parsed=parsed,
            )
            raise
        try:
            return self._publish(
                attempt_id=attempt_id,
                request=request,
                scope=scope,
                scope_sha256=scope_sha256,
                response=response,
                parsed=parsed,
            )
        except Exception:
            self._record_failure(
                attempt_id=attempt_id,
                request=request,
                outcome_kind="publication_failed",
                response=response,
                parsed=parsed,
            )
            raise


__all__ = (
    "ALPACA_NEWS_MAX_SYMBOLS_PER_REQUEST",
    "ALPACA_NEWS_URL",
    "BEA_NEWS_RSS_URL",
    "CURRENT_MULTI_SOURCE_ARTICLES_DATASET_ID",
    "CURRENT_MULTI_SOURCE_COLLECTOR_ID",
    "CURRENT_MULTI_SOURCE_EVIDENCE_DATASET_ID",
    "CURRENT_MULTI_SOURCE_FEED_IDS",
    "CURRENT_MULTI_SOURCE_MAX_BYTES",
    "CURRENT_MULTI_SOURCE_MIGRATION_ID",
    "CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS",
    "CapturedCurrentMultiSourceResponse",
    "CurrentMultiSourceCredentials",
    "CurrentMultiSourceImporter",
    "CurrentMultiSourceReceipt",
    "CurrentMultiSourceRequest",
    "CurrentMultiSourceTransport",
    "ECB_PRESS_RSS_URL",
    "EIA_PRESS_RSS_URL",
    "FED_PRESS_RSS_URL",
    "FMP_GENERAL_URL",
    "FMP_PRESS_RELEASES_URL",
    "StdlibCurrentMultiSourceTransport",
)
