"""Repeatable current FMP stock-latest news collector.

This successor is deliberately separate from the one-shot 0005 collector.  It
reserves one immutable intent per UTC hourly poll slot, completes exactly one
bounded request outside the news-store write lock, and appends immutable
evidence plus article/version lineage for accepted rows only.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import sqlite3
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Lock
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, StoreWriteLock, read_connection, stable_id, writer_connection


FMP_NEWS_HOST = "financialmodelingprep.com"
FMP_STOCK_LATEST_CURRENT_PATH = "/stable/news/stock-latest"
FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID = "fmp.news.stock_latest_current"
FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID = "news.fmp.stock_latest_current_evidence"
FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID = "news.fmp.stock_latest_current_articles"
FMP_STOCK_LATEST_CURRENT_MIGRATION_ID = "news:0006_fmp_stock_latest_current"
FMP_STOCK_LATEST_CURRENT_PROFILE_ID = "fmp.stock_latest.page0.limit1000.current.v1"
FMP_STOCK_LATEST_CURRENT_PROVIDER_NAMESPACE = "fmp.news.stock_latest_current"
FMP_STOCK_LATEST_CURRENT_NORMALIZATION_VERSION = "fmp.news.stock_latest.current.v1"
FMP_STOCK_LATEST_CURRENT_PAGE = 0
FMP_STOCK_LATEST_CURRENT_LIMIT = 1000
FMP_STOCK_LATEST_CURRENT_MAX_BYTES = 64 * 1024 * 1024
FMP_STOCK_LATEST_CURRENT_TIMEOUT_SECONDS = 60

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_hour(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _utc_hour_text(value: object, field_name: str) -> str:
    return _utc_hour(value, field_name).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_content_type(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    pieces = [piece.strip() for piece in value.split(";")]
    if pieces[0].lower() != "application/json":
        return False
    if len(pieces) == 1:
        return True
    if len(pieces) != 2 or "=" not in pieces[1]:
        return False
    name, charset = (part.strip() for part in pieces[1].split("=", 1))
    return name.lower() == "charset" and bool(charset)


def _absolute_http_url(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or character.isspace() for character in value)
    ):
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL")
    return value


def _url_host(value: str) -> str:
    try:
        host = urlsplit(value).hostname
    except ValueError as exc:
        raise ValidationError("FMP URL host is invalid") from exc
    if not host:
        raise ValidationError("FMP URL host is invalid")
    return host


def _usable_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _published_metadata(value: str) -> tuple[str | None, str, str]:
    try:
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return None, "date", "unknown"
    except ValueError:
        pass
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None, "unknown", "unknown"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "datetime_naive", "unknown"
    return parsed.isoformat(timespec="microseconds"), "datetime_offset", "known"


@dataclass(frozen=True, slots=True)
class FmpStockLatestCurrentRequest:
    """The exact endpoint and one UTC hourly slot; callers cannot broaden it."""

    poll_slot: datetime
    page: int = FMP_STOCK_LATEST_CURRENT_PAGE
    limit: int = FMP_STOCK_LATEST_CURRENT_LIMIT

    def __post_init__(self) -> None:
        if (
            isinstance(self.page, bool)
            or isinstance(self.limit, bool)
            or self.page != FMP_STOCK_LATEST_CURRENT_PAGE
            or self.limit != FMP_STOCK_LATEST_CURRENT_LIMIT
        ):
            raise ValidationError("FMP current stock-latest request is outside the approved scope")
        object.__setattr__(self, "poll_slot", _utc_hour(self.poll_slot, "poll_slot"))

    @property
    def poll_slot_text(self) -> str:
        return _utc_hour_text(self.poll_slot, "poll_slot")

    def request_scope(self) -> dict[str, object]:
        return {
            "endpoint_path": FMP_STOCK_LATEST_CURRENT_PATH,
            "limit": FMP_STOCK_LATEST_CURRENT_LIMIT,
            "page": FMP_STOCK_LATEST_CURRENT_PAGE,
            "poll_slot": self.poll_slot_text,
            "provider": "fmp",
        }


@dataclass(frozen=True, slots=True)
class CapturedFmpStockLatestCurrentResponse:
    """Bounded raw bytes from an injected or stdlib transport."""

    status: int
    content_type: str
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("FMP current stock-latest response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("FMP current stock-latest response content type is invalid")
        if not isinstance(self.body, bytes):
            raise ValidationError("FMP current stock-latest response body must be bytes")
        if len(self.body) > FMP_STOCK_LATEST_CURRENT_MAX_BYTES:
            raise ResourceLimitError("FMP current stock-latest response exceeds the reviewed byte bound")


@runtime_checkable
class FmpStockLatestCurrentTransport(Protocol):
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStockLatestCurrentResponse: ...


class StdlibFmpStockLatestCurrentTransport:
    """TLS-verified stdlib transport with no redirect or retry behavior."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStockLatestCurrentResponse:
        expected_query = {
            "page": str(FMP_STOCK_LATEST_CURRENT_PAGE),
            "limit": str(FMP_STOCK_LATEST_CURRENT_LIMIT),
        }
        if (
            path != FMP_STOCK_LATEST_CURRENT_PATH
            or dict(query) != expected_query
            or set(headers) != {"apikey", "Accept"}
            or headers.get("Accept") != "application/json"
            or not isinstance(headers.get("apikey"), str)
            or _API_KEY.fullmatch(headers["apikey"]) is None
            or timeout_seconds != FMP_STOCK_LATEST_CURRENT_TIMEOUT_SECONDS
            or max_bytes != FMP_STOCK_LATEST_CURRENT_MAX_BYTES
        ):
            raise ValidationError("FMP current stock-latest transport request is outside scope")
        connection = http.client.HTTPSConnection(FMP_NEWS_HOST, timeout=timeout_seconds)
        try:
            connection.request("GET", f"{path}?{urlencode(expected_query)}", headers=dict(headers))
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                raise StoreUnavailableError("FMP current stock-latest redirects are not allowed")
            if response.status != 200:
                raise StoreUnavailableError("FMP current stock-latest provider response was unavailable")
            content_type = response.getheader("Content-Type") or ""
            if not _json_content_type(content_type):
                raise StoreUnavailableError("FMP current stock-latest provider response was unavailable")
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise StoreUnavailableError("FMP current stock-latest provider response was unavailable") from exc
                if declared_size < 0 or declared_size > max_bytes:
                    raise ResourceLimitError("FMP current stock-latest response exceeds the reviewed byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("FMP current stock-latest response exceeds the reviewed byte bound")
            return CapturedFmpStockLatestCurrentResponse(response.status, content_type, body)
        except (ResourceLimitError, ValidationError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP current stock-latest provider request failed") from exc
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class FmpStockLatestCurrentReceipt:
    attempt_id: str
    poll_slot: str
    outcome: str
    capture_id: str | None
    provider_row_count: int
    accepted_row_count: int
    rejected_row_count: int
    written_count: int


@dataclass(frozen=True, slots=True)
class _ArticleRow:
    symbol: str
    site: str
    source_url: str
    title: str
    body_text: str
    image_url: str | None
    published_date_raw: str | None
    published_normalized_at: str | None
    published_precision: str
    published_offset_status: str
    source_row: int

    @property
    def article_id(self) -> str:
        return stable_id(
            "fmp_stock_latest_current_article",
            FMP_STOCK_LATEST_CURRENT_PROVIDER_NAMESPACE,
            self.site,
            self.source_url,
            self.symbol,
        )

    def mutable_content(self) -> dict[str, object]:
        return {
            "body_text": self.body_text,
            "image_url": self.image_url,
            "published_date_raw": self.published_date_raw,
            "published_normalized_at": self.published_normalized_at,
            "published_offset_status": self.published_offset_status,
            "published_precision": self.published_precision,
            "title": self.title,
        }

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "article_id": self.article_id,
            "mutable_content_sha256": _sha256_json(self.mutable_content()),
        }


@dataclass(frozen=True, slots=True)
class _ParsedRows:
    rows: tuple[_ArticleRow, ...]
    provider_row_count: int
    rejected_row_count: int

    @property
    def accepted_row_count(self) -> int:
        return len(self.rows)


def _parse_row(raw: object, source_row: int) -> _ArticleRow | None:
    if not isinstance(raw, dict):
        return None
    symbol = _usable_string(raw.get("symbol"))
    title = _usable_string(raw.get("title"))
    if symbol is None or title is None:
        return None
    try:
        source_url = _absolute_http_url(raw.get("url"), "url")
    except ValidationError:
        return None
    site = _usable_string(raw.get("site")) or _url_host(source_url)
    body_text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    try:
        image_url = _absolute_http_url(raw.get("image"), "image")
    except ValidationError:
        image_url = None
    published_value = raw.get("publishedDate")
    if isinstance(published_value, str) and published_value.strip():
        published_date_raw = published_value
        published_normalized_at, published_precision, published_offset_status = _published_metadata(
            published_date_raw
        )
    else:
        published_date_raw = None
        published_normalized_at = None
        published_precision = "missing"
        published_offset_status = "missing"
    return _ArticleRow(
        symbol=symbol,
        site=site,
        source_url=source_url,
        title=title,
        body_text=body_text,
        image_url=image_url,
        published_date_raw=published_date_raw,
        published_normalized_at=published_normalized_at,
        published_precision=published_precision,
        published_offset_status=published_offset_status,
        source_row=source_row,
    )


def _parse_rows(body: bytes) -> _ParsedRows:
    try:
        decoded = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("FMP current stock-latest response must be strict UTF-8") from exc
    try:
        payload = loads_strict(decoded, max_bytes=FMP_STOCK_LATEST_CURRENT_MAX_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ValidationError("FMP current stock-latest response is not bounded strict JSON") from exc
    if not isinstance(payload, list) or len(payload) > FMP_STOCK_LATEST_CURRENT_LIMIT:
        raise ValidationError("FMP current stock-latest response must be an array of at most 1000 rows")
    rows: list[_ArticleRow] = []
    rejected = 0
    for source_row, raw in enumerate(payload, start=1):
        parsed = _parse_row(raw, source_row)
        if parsed is None:
            rejected += 1
        else:
            rows.append(parsed)
    return _ParsedRows(tuple(rows), len(payload), rejected)


def _validate_registry(registry: Registry) -> None:
    expected_migrations = (
        "news:0001_foundation",
        "news:0002_control_plane",
        "news:0003_immutable_items",
        "news:0004_search_index",
        "news:0005_fmp_stock_latest",
        FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
    )
    if not isinstance(registry, Registry):
        raise ValidationError("FMP current stock-latest importer requires a registry")
    migrations = tuple(sorted(registry.migrations_for(StoreRole.NEWS.value), key=lambda item: item.ordinal))
    if (
        tuple(item.id for item in migrations[:6]) != expected_migrations
        or tuple(item.ordinal for item in migrations[:6]) != (1, 2, 3, 4, 5, 6)
        or migrations[5].store != StoreRole.NEWS.value
        or migrations[5].reconstruction_state != "fixture_validated"
        or registry.store(StoreRole.NEWS.value).migration_order[:6]
        != expected_migrations
    ):
        raise ValidationError("FMP current stock-latest registry ledger is invalid")
    collector_matches = [
        item for item in registry.collectors
        if item.get("id") == FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID
    ]
    if len(collector_matches) != 1:
        raise ValidationError("FMP current stock-latest collector is not registered exactly once")
    collector = collector_matches[0]
    if (
        collector.get("handler") != "news.fmp_stock_latest_current"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("output_datasets", ())) != (
            FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
            FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
        )
    ):
        raise ValidationError("FMP current stock-latest collector declaration is invalid")
    datasets = {item.id: item for item in registry.datasets}
    expected_relations = {
        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID: (
            "fmp_stock_latest_current_attempts",
            "fmp_stock_latest_current_outcomes",
            "fmp_stock_latest_current_captures",
        ),
        FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID: (
            "fmp_stock_latest_current_articles",
            "fmp_stock_latest_current_article_versions",
            "fmp_stock_latest_current_capture_articles",
        ),
    }
    if set(expected_relations) - set(datasets):
        raise ValidationError("FMP current stock-latest datasets are not registered")
    for dataset_id, relations in expected_relations.items():
        dataset = datasets[dataset_id]
        if (
            dataset.store != StoreRole.NEWS.value
            or dataset.relations != relations
            or dataset.tool_ids != ("news.search",)
            or dataset.dashboard_ids
            or dataset.export_ids
        ):
            raise ValidationError("FMP current stock-latest dataset routing is invalid")


class FmpStockLatestCurrentAttempt:
    """Opaque persisted pre-request intent capability."""

    __slots__ = ("__weakref__",)


class PreparedFmpStockLatestCurrentCapture:
    """Opaque fully validated response capability without a raw-body API."""

    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _AttemptState:
    owner_token: object
    attempt_id: str
    poll_slot: str
    request_scope: Mapping[str, object]
    request_scope_sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    attempt: _AttemptState
    response: CapturedFmpStockLatestCurrentResponse
    captured_at: str
    parsed: _ParsedRows
    semantic_identity: str
    capture_id: str


_ATTEMPTS: weakref.WeakKeyDictionary[FmpStockLatestCurrentAttempt, _AttemptState] = weakref.WeakKeyDictionary()
_PREPARED: weakref.WeakKeyDictionary[PreparedFmpStockLatestCurrentCapture, _PreparedState] = weakref.WeakKeyDictionary()


class _ResponseRejected(ValidationError):
    def __init__(
        self,
        response: CapturedFmpStockLatestCurrentResponse | None = None,
        parsed: _ParsedRows | None = None,
    ) -> None:
        super().__init__("FMP current stock-latest response was rejected")
        self.response = response
        self.parsed = parsed


class FmpStockLatestCurrentImporter:
    """Reserve an hourly slot, capture outside locks, then append one result."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("FMP current stock-latest importer requires stores and a clock")
        _validate_registry(registry)
        self.store_map = store_map
        self.registry = registry
        self._clock = clock
        self._owner_token = object()
        self._capture_lock = Lock()
        self._consumed_attempt_ids: set[str] = set()

    def _write(self, callback: Callable[[sqlite3.Connection], object]) -> object:
        path = self.store_map.path(StoreRole.NEWS)
        with StoreWriteLock(path), writer_connection(self.store_map, StoreRole.NEWS) as connection:
            return callback(connection)

    def _consume_attempt_before_transport(
        self,
        attempt: FmpStockLatestCurrentAttempt,
    ) -> _AttemptState:
        with self._capture_lock:
            state = _ATTEMPTS.get(attempt) if isinstance(attempt, FmpStockLatestCurrentAttempt) else None
            if state is None or state.owner_token is not self._owner_token:
                raise ValidationError("FMP current stock-latest attempt is foreign or expired")
            if state.attempt_id in self._consumed_attempt_ids:
                raise ConflictError("FMP current stock-latest attempt capability was already consumed")
            with read_connection(self.store_map, StoreRole.NEWS) as connection:
                terminal = connection.execute(
                    "SELECT 1 FROM fmp_stock_latest_current_outcomes WHERE attempt_id=?",
                    (state.attempt_id,),
                ).fetchone()
            if terminal is not None:
                self._consumed_attempt_ids.add(state.attempt_id)
                raise ConflictError("FMP current stock-latest attempt already has a terminal outcome")
            self._consumed_attempt_ids.add(state.attempt_id)
            return state

    def reserve_attempt(
        self,
        request: FmpStockLatestCurrentRequest | None = None,
    ) -> FmpStockLatestCurrentAttempt:
        request = FmpStockLatestCurrentRequest(self._clock()) if request is None else request
        if not isinstance(request, FmpStockLatestCurrentRequest):
            raise ValidationError("FMP current stock-latest request is invalid")
        scope = request.request_scope()
        scope_sha256 = _sha256_json(scope)
        intent_recorded_at = _utc_text(self._clock(), "intent_recorded_at")
        attempt_id = stable_id(
            "fmp_stock_latest_current_attempt",
            FMP_STOCK_LATEST_CURRENT_PROFILE_ID,
            scope_sha256,
        )

        def reserve(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT attempt_id FROM fmp_stock_latest_current_attempts
                    WHERE collector_id=? AND profile_id=? AND poll_slot=?
                    """,
                    (
                        FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
                        FMP_STOCK_LATEST_CURRENT_PROFILE_ID,
                        request.poll_slot_text,
                    ),
                ).fetchone()
                if existing is not None:
                    raise ConflictError("An FMP current stock-latest intent already blocks this hour")
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_current_attempts (
                        attempt_id, dataset_id, collector_id, profile_id, poll_slot,
                        request_scope_json, request_scope_sha256,
                        intent_recorded_at, intent_recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        attempt_id,
                        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
                        FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
                        FMP_STOCK_LATEST_CURRENT_PROFILE_ID,
                        request.poll_slot_text,
                        dumps_strict(scope),
                        scope_sha256,
                        intent_recorded_at,
                    ),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

        self._write(reserve)
        attempt = FmpStockLatestCurrentAttempt()
        _ATTEMPTS[attempt] = _AttemptState(
            owner_token=self._owner_token,
            attempt_id=attempt_id,
            poll_slot=request.poll_slot_text,
            request_scope=scope,
            request_scope_sha256=scope_sha256,
        )
        return attempt

    def capture_attempt(
        self,
        attempt: FmpStockLatestCurrentAttempt,
        *,
        api_key: str,
        transport: FmpStockLatestCurrentTransport,
    ) -> PreparedFmpStockLatestCurrentCapture:
        if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
            raise ValidationError("FMP current stock-latest credential is missing or invalid")
        if not isinstance(transport, FmpStockLatestCurrentTransport):
            raise ValidationError("FMP current stock-latest transport is invalid")
        state = self._consume_attempt_before_transport(attempt)
        request_failed = False
        try:
            response = transport.get(
                path=FMP_STOCK_LATEST_CURRENT_PATH,
                query={"page": "0", "limit": "1000"},
                headers={"apikey": api_key, "Accept": "application/json"},
                timeout_seconds=FMP_STOCK_LATEST_CURRENT_TIMEOUT_SECONDS,
                max_bytes=FMP_STOCK_LATEST_CURRENT_MAX_BYTES,
            )
        except Exception:
            request_failed = True
        if request_failed:
            raise StoreUnavailableError(
                "FMP current stock-latest provider request failed"
            )
        if not isinstance(response, CapturedFmpStockLatestCurrentResponse):
            raise _ResponseRejected()
        if response.status != 200 or not _json_content_type(response.content_type):
            raise _ResponseRejected(response)
        try:
            parsed = _parse_rows(response.body)
        except ValidationError:
            raise _ResponseRejected(response) from None
        if parsed.provider_row_count and not parsed.accepted_row_count:
            raise _ResponseRejected(response, parsed)
        captured_at = _utc_text(self._clock(), "captured_at")
        semantic_rows = sorted((row.semantic_mapping() for row in parsed.rows), key=dumps_strict)
        semantic_identity = _sha256_json(
            {
                "collector_id": FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
                "normalization_version": FMP_STOCK_LATEST_CURRENT_NORMALIZATION_VERSION,
                "normalized_partial_page": semantic_rows,
                "request_scope": dict(state.request_scope),
            }
        )
        capture_id = stable_id(
            "fmp_stock_latest_current_capture",
            state.attempt_id,
            semantic_identity,
        )
        prepared = PreparedFmpStockLatestCurrentCapture()
        _PREPARED[prepared] = _PreparedState(
            owner_token=self._owner_token,
            attempt=state,
            response=response,
            captured_at=captured_at,
            parsed=parsed,
            semantic_identity=semantic_identity,
            capture_id=capture_id,
        )
        return prepared

    def record_terminal_failure(
        self,
        attempt: FmpStockLatestCurrentAttempt,
        *,
        outcome_kind: str,
        response: CapturedFmpStockLatestCurrentResponse | None = None,
        parsed: _ParsedRows | None = None,
    ) -> FmpStockLatestCurrentReceipt:
        state = _ATTEMPTS.get(attempt) if isinstance(attempt, FmpStockLatestCurrentAttempt) else None
        if state is None or state.owner_token is not self._owner_token:
            raise ValidationError("FMP current stock-latest attempt is foreign or expired")
        if outcome_kind not in {"request_failed", "response_rejected", "publication_failed"}:
            raise ValidationError("FMP current stock-latest terminal outcome is invalid")
        recorded_at = _utc_text(self._clock(), "recorded_at")
        counts = (None, None, None) if parsed is None else (
            parsed.provider_row_count,
            parsed.accepted_row_count,
            parsed.rejected_row_count,
        )
        outcome_id = stable_id("fmp_stock_latest_current_outcome", state.attempt_id, outcome_kind)

        def record(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM fmp_stock_latest_current_outcomes WHERE attempt_id=?",
                    (state.attempt_id,),
                ).fetchone() is not None:
                    raise ConflictError("FMP current stock-latest attempt already has a terminal outcome")
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_current_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        provider_row_count, accepted_row_count, rejected_row_count,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        state.attempt_id,
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
        return FmpStockLatestCurrentReceipt(
            attempt_id=state.attempt_id,
            poll_slot=state.poll_slot,
            outcome=outcome_kind,
            capture_id=None,
            provider_row_count=0 if counts[0] is None else counts[0],
            accepted_row_count=0 if counts[1] is None else counts[1],
            rejected_row_count=0 if counts[2] is None else counts[2],
            written_count=1,
        )

    def publish_prepared(
        self,
        prepared: PreparedFmpStockLatestCurrentCapture,
    ) -> FmpStockLatestCurrentReceipt:
        state = _PREPARED.get(prepared) if isinstance(prepared, PreparedFmpStockLatestCurrentCapture) else None
        if state is None or state.owner_token is not self._owner_token:
            raise ValidationError("FMP current stock-latest prepared capture is foreign or expired")
        outcome_id = stable_id("fmp_stock_latest_current_outcome", state.attempt.attempt_id, "succeeded")

        def publish(connection: sqlite3.Connection) -> FmpStockLatestCurrentReceipt:
            existing = connection.execute(
                """
                SELECT outcome.outcome_kind, capture.capture_id, capture.semantic_identity
                FROM fmp_stock_latest_current_outcomes AS outcome
                LEFT JOIN fmp_stock_latest_current_captures AS capture
                  ON capture.outcome_id=outcome.outcome_id
                WHERE outcome.attempt_id=?
                """,
                (state.attempt.attempt_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["outcome_kind"] == "succeeded"
                    and existing["capture_id"] == state.capture_id
                    and existing["semantic_identity"] == state.semantic_identity
                ):
                    return FmpStockLatestCurrentReceipt(
                        attempt_id=state.attempt.attempt_id,
                        poll_slot=state.attempt.poll_slot,
                        outcome="unchanged",
                        capture_id=state.capture_id,
                        provider_row_count=state.parsed.provider_row_count,
                        accepted_row_count=state.parsed.accepted_row_count,
                        rejected_row_count=state.parsed.rejected_row_count,
                        written_count=0,
                    )
                raise ConflictError("FMP current stock-latest attempt already has a terminal outcome")
            connection.execute("BEGIN IMMEDIATE")
            try:
                response_sha256 = _sha256(state.response.body)
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_current_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        provider_row_count, accepted_row_count, rejected_row_count,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, 'succeeded', ?, ?, 200, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        state.attempt.attempt_id,
                        response_sha256,
                        len(state.response.body),
                        state.response.content_type,
                        state.parsed.provider_row_count,
                        state.parsed.accepted_row_count,
                        state.parsed.rejected_row_count,
                        state.captured_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_current_captures (
                        capture_id, dataset_id, attempt_id, outcome_id,
                        request_scope_sha256, response_sha256, response_bytes,
                        http_status, content_type, semantic_identity, completeness,
                        captured_at, captured_precision, provider_row_count,
                        accepted_row_count, rejected_row_count, normalization_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 200, ?, ?, 'partial',
                              ?, 'datetime', ?, ?, ?, ?)
                    """,
                    (
                        state.capture_id,
                        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
                        state.attempt.attempt_id,
                        outcome_id,
                        state.attempt.request_scope_sha256,
                        response_sha256,
                        state.response.body,
                        state.response.content_type,
                        state.semantic_identity,
                        state.captured_at,
                        state.parsed.provider_row_count,
                        state.parsed.accepted_row_count,
                        state.parsed.rejected_row_count,
                        FMP_STOCK_LATEST_CURRENT_NORMALIZATION_VERSION,
                    ),
                )
                written = 2
                for row in state.parsed.rows:
                    article = connection.execute(
                        "SELECT article_id FROM fmp_stock_latest_current_articles WHERE article_id=?",
                        (row.article_id,),
                    ).fetchone()
                    if article is None:
                        connection.execute(
                            """
                            INSERT INTO fmp_stock_latest_current_articles (
                                article_id, provider_namespace, site, source_url,
                                symbol, created_capture_id
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row.article_id,
                                FMP_STOCK_LATEST_CURRENT_PROVIDER_NAMESPACE,
                                row.site,
                                row.source_url,
                                row.symbol,
                                state.capture_id,
                            ),
                        )
                        written += 1
                    latest = connection.execute(
                        """
                        SELECT article_version_id, mutable_content_sha256, version_sequence
                        FROM fmp_stock_latest_current_article_versions
                        WHERE article_id=?
                        ORDER BY version_sequence DESC
                        LIMIT 1
                        """,
                        (row.article_id,),
                    ).fetchone()
                    content_sha256 = _sha256_json(row.mutable_content())
                    if latest is not None and latest["mutable_content_sha256"] == content_sha256:
                        version_id = str(latest["article_version_id"])
                    else:
                        sequence = 1 if latest is None else int(latest["version_sequence"]) + 1
                        supersedes = None if latest is None else str(latest["article_version_id"])
                        version_id = stable_id(
                            "fmp_stock_latest_current_article_version",
                            row.article_id,
                            content_sha256,
                            str(sequence),
                        )
                        connection.execute(
                            """
                            INSERT INTO fmp_stock_latest_current_article_versions (
                                article_version_id, article_id, mutable_content_sha256,
                                title, body_text, image_url, published_date_raw,
                                published_normalized_at, published_precision,
                                published_offset_status, version_sequence,
                                supersedes_article_version_id, capture_id, source_row
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                version_id,
                                row.article_id,
                                content_sha256,
                                row.title,
                                row.body_text,
                                row.image_url,
                                row.published_date_raw,
                                row.published_normalized_at,
                                row.published_precision,
                                row.published_offset_status,
                                sequence,
                                supersedes,
                                state.capture_id,
                                row.source_row,
                            ),
                        )
                        written += 1
                    connection.execute(
                        """
                        INSERT INTO fmp_stock_latest_current_capture_articles (
                            capture_id, article_version_id, source_row
                        ) VALUES (?, ?, ?)
                        """,
                        (state.capture_id, version_id, row.source_row),
                    )
                    written += 1
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            return FmpStockLatestCurrentReceipt(
                attempt_id=state.attempt.attempt_id,
                poll_slot=state.attempt.poll_slot,
                outcome="succeeded",
                capture_id=state.capture_id,
                provider_row_count=state.parsed.provider_row_count,
                accepted_row_count=state.parsed.accepted_row_count,
                rejected_row_count=state.parsed.rejected_row_count,
                written_count=written,
            )

        return self._write(publish)  # type: ignore[return-value]

    def run_once(
        self,
        *,
        api_key: str,
        transport: FmpStockLatestCurrentTransport,
        request: FmpStockLatestCurrentRequest | None = None,
    ) -> FmpStockLatestCurrentReceipt:
        """Issue at most one exact request in one permanent UTC hourly slot."""

        if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
            raise ValidationError("FMP current stock-latest credential is missing or invalid")
        if not isinstance(transport, FmpStockLatestCurrentTransport):
            raise ValidationError("FMP current stock-latest transport is invalid")
        attempt = self.reserve_attempt(request)
        try:
            prepared = self.capture_attempt(attempt, api_key=api_key, transport=transport)
        except _ResponseRejected as exc:
            self.record_terminal_failure(
                attempt,
                outcome_kind="response_rejected",
                response=exc.response,
                parsed=exc.parsed,
            )
            raise ValidationError("FMP current stock-latest response was rejected") from None
        except ConflictError:
            raise
        except Exception:
            self.record_terminal_failure(attempt, outcome_kind="request_failed")
            raise
        try:
            return self.publish_prepared(prepared)
        except Exception:
            self.record_terminal_failure(attempt, outcome_kind="publication_failed")
            raise


__all__ = (
    "CapturedFmpStockLatestCurrentResponse",
    "FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID",
    "FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID",
    "FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID",
    "FMP_STOCK_LATEST_CURRENT_LIMIT",
    "FMP_STOCK_LATEST_CURRENT_MAX_BYTES",
    "FMP_STOCK_LATEST_CURRENT_MIGRATION_ID",
    "FMP_STOCK_LATEST_CURRENT_PAGE",
    "FMP_STOCK_LATEST_CURRENT_PATH",
    "FMP_STOCK_LATEST_CURRENT_TIMEOUT_SECONDS",
    "FmpStockLatestCurrentImporter",
    "FmpStockLatestCurrentReceipt",
    "FmpStockLatestCurrentRequest",
    "FmpStockLatestCurrentTransport",
    "PreparedFmpStockLatestCurrentCapture",
    "StdlibFmpStockLatestCurrentTransport",
)
