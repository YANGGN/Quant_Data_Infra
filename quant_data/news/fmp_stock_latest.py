"""One bounded manual FMP stock-latest news-page importer.

The importer is deliberately separate from the frozen Stage 4 fixture-news
adapter.  It commits one immutable request intent before a provider call,
releases the news-store lock for the call, then records one terminal outcome.
Only a completely validated response can create a raw capture or private
article/version/membership rows.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Lock
from typing import Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import (
    StoreMap,
    StoreRole,
    StoreWriteLock,
    read_connection,
    stable_id,
    writer_connection,
)


FMP_NEWS_HOST = "financialmodelingprep.com"
FMP_STOCK_LATEST_PATH = "/stable/news/stock-latest"
FMP_STOCK_LATEST_COLLECTOR_ID = "fmp.news.stock_latest"
FMP_STOCK_LATEST_EVIDENCE_DATASET_ID = "news.fmp.stock_latest_evidence"
FMP_STOCK_LATEST_ARTICLES_DATASET_ID = "news.fmp.stock_latest_articles"
FMP_STOCK_LATEST_MIGRATION_ID = "news:0005_fmp_stock_latest"
FMP_STOCK_LATEST_PROFILE_ID = "fmp.stock_latest.page0.limit1000.v1"
FMP_STOCK_LATEST_PROVIDER_NAMESPACE = "fmp.news.stock_latest"
FMP_STOCK_LATEST_NORMALIZATION_VERSION = "fmp.news.stock_latest.v1"
FMP_STOCK_LATEST_PAGE = 0
FMP_STOCK_LATEST_LIMIT = 1000
FMP_STOCK_LATEST_MAX_BYTES = 64 * 1024 * 1024
FMP_STOCK_LATEST_TIMEOUT_SECONDS = 60

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
        or any(ord(char) < 32 or char.isspace() for char in value)
    ):
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError(f"FMP {field_name} must be an absolute HTTP(S) URL")
    return value


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"FMP {field_name} must be a nonempty string")
    return value


def _published_metadata(value: str) -> tuple[str | None, str, str]:
    """Classify a provider string without inventing an offset or timestamp."""

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
class FmpStockLatestRequest:
    """The exact approved page; callers cannot broaden it."""

    page: int = FMP_STOCK_LATEST_PAGE
    limit: int = FMP_STOCK_LATEST_LIMIT

    def __post_init__(self) -> None:
        if (
            isinstance(self.page, bool)
            or isinstance(self.limit, bool)
            or self.page != FMP_STOCK_LATEST_PAGE
            or self.limit != FMP_STOCK_LATEST_LIMIT
        ):
            raise ValidationError("FMP stock-latest request is outside the approved scope")

    def request_scope(self) -> dict[str, object]:
        return {
            "endpoint_path": FMP_STOCK_LATEST_PATH,
            "limit": FMP_STOCK_LATEST_LIMIT,
            "page": FMP_STOCK_LATEST_PAGE,
            "provider": "fmp",
        }


@dataclass(frozen=True, slots=True)
class CapturedFmpStockLatestResponse:
    """Bounded raw bytes from an injected or stdlib transport."""

    status: int
    content_type: str
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("FMP stock-latest response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("FMP stock-latest response content type is invalid")
        if not isinstance(self.body, bytes):
            raise ValidationError("FMP stock-latest response body must be bytes")
        if len(self.body) > FMP_STOCK_LATEST_MAX_BYTES:
            raise ResourceLimitError("FMP stock-latest response exceeds the reviewed byte bound")


@runtime_checkable
class FmpStockLatestTransport(Protocol):
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStockLatestResponse: ...


class StdlibFmpStockLatestTransport:
    """TLS-verified stdlib transport with no redirect or retry behavior."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStockLatestResponse:
        expected_query = {
            "page": str(FMP_STOCK_LATEST_PAGE),
            "limit": str(FMP_STOCK_LATEST_LIMIT),
        }
        if (
            path != FMP_STOCK_LATEST_PATH
            or dict(query) != expected_query
            or set(headers) != {"apikey", "Accept"}
            or headers.get("Accept") != "application/json"
            or not isinstance(headers.get("apikey"), str)
            or _API_KEY.fullmatch(headers["apikey"]) is None
            or timeout_seconds != FMP_STOCK_LATEST_TIMEOUT_SECONDS
            or max_bytes != FMP_STOCK_LATEST_MAX_BYTES
        ):
            raise ValidationError("FMP stock-latest transport request is outside the approved scope")
        connection = http.client.HTTPSConnection(FMP_NEWS_HOST, timeout=timeout_seconds)
        try:
            connection.request("GET", f"{path}?{urlencode(expected_query)}", headers=dict(headers))
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                raise StoreUnavailableError("FMP stock-latest redirects are not allowed")
            if response.status != 200:
                raise StoreUnavailableError("FMP stock-latest provider response was unavailable")
            content_type = response.getheader("Content-Type") or ""
            if not _json_content_type(content_type):
                raise StoreUnavailableError("FMP stock-latest provider response was unavailable")
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise StoreUnavailableError("FMP stock-latest provider response was unavailable") from exc
                if declared_size < 0 or declared_size > max_bytes:
                    raise ResourceLimitError("FMP stock-latest response exceeds the reviewed byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("FMP stock-latest response exceeds the reviewed byte bound")
            return CapturedFmpStockLatestResponse(response.status, content_type, body)
        except (ResourceLimitError, ValidationError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP stock-latest provider request failed") from exc
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class FmpStockLatestReceipt:
    attempt_id: str
    outcome: str
    capture_id: str | None
    article_count: int
    written_count: int


@dataclass(frozen=True, slots=True)
class _ArticleRow:
    symbol: str
    site: str
    source_url: str
    title: str
    body_text: str
    image_url: str | None
    published_date_raw: str
    published_normalized_at: str | None
    published_precision: str
    published_offset_status: str
    source_row: int

    @property
    def article_id(self) -> str:
        return stable_id(
            "fmp_stock_latest_article",
            FMP_STOCK_LATEST_PROVIDER_NAMESPACE,
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


def _parse_rows(body: bytes) -> tuple[_ArticleRow, ...]:
    try:
        decoded = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("FMP stock-latest response must be strict UTF-8") from exc
    try:
        payload = loads_strict(decoded, max_bytes=FMP_STOCK_LATEST_MAX_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ValidationError("FMP stock-latest response is not bounded strict JSON") from exc
    if not isinstance(payload, list) or len(payload) > FMP_STOCK_LATEST_LIMIT:
        raise ValidationError("FMP stock-latest response must be an array of at most 1000 rows")
    rows: list[_ArticleRow] = []
    for source_row, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise ValidationError("FMP stock-latest response row must be an object")
        required = {"symbol", "publishedDate", "title", "text", "url", "site"}
        if not required.issubset(raw):
            raise ValidationError("FMP stock-latest response row is missing a required field")
        symbol = _nonempty_string(raw["symbol"], "symbol")
        published_date_raw = _nonempty_string(raw["publishedDate"], "publishedDate")
        title = _nonempty_string(raw["title"], "title")
        if not isinstance(raw["text"], str):
            raise ValidationError("FMP stock-latest text must be a string")
        source_url = _absolute_http_url(raw["url"], "url")
        site = _nonempty_string(raw["site"], "site")
        image = raw.get("image")
        if image is None or image == "":
            image_url = None
        else:
            image_url = _absolute_http_url(image, "image")
        normalized_at, precision, offset_status = _published_metadata(published_date_raw)
        rows.append(
            _ArticleRow(
                symbol=symbol,
                site=site,
                source_url=source_url,
                title=title,
                body_text=raw["text"],
                image_url=image_url,
                published_date_raw=published_date_raw,
                published_normalized_at=normalized_at,
                published_precision=precision,
                published_offset_status=offset_status,
                source_row=source_row,
            )
        )
    return tuple(rows)


def _validate_registry(registry: Registry) -> None:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.8.0"
        or registry.registry_version not in {"2.21.0", "2.23.0"}
    ):
        raise ValidationError("FMP stock-latest importer requires the reviewed canonical registry")
    collector_matches = [
        item for item in registry.collectors if item.get("id") == FMP_STOCK_LATEST_COLLECTOR_ID
    ]
    if len(collector_matches) != 1:
        raise ValidationError("FMP stock-latest collector is not registered exactly once")
    collector = collector_matches[0]
    if (
        collector.get("handler") != "news.fmp_stock_latest"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("output_datasets", ()))
        != (FMP_STOCK_LATEST_EVIDENCE_DATASET_ID, FMP_STOCK_LATEST_ARTICLES_DATASET_ID)
    ):
        raise ValidationError("FMP stock-latest collector declaration is invalid")
    migration = [
        item for item in registry.migrations if item.id == FMP_STOCK_LATEST_MIGRATION_ID
    ]
    if len(migration) != 1 or migration[0].store != StoreRole.NEWS.value or migration[0].ordinal != 5:
        raise ValidationError("FMP stock-latest migration declaration is invalid")
    datasets = {item.id: item for item in registry.datasets}
    if set((FMP_STOCK_LATEST_EVIDENCE_DATASET_ID, FMP_STOCK_LATEST_ARTICLES_DATASET_ID)) - set(datasets):
        raise ValidationError("FMP stock-latest datasets are not registered")
    if any(
        datasets[item].store != StoreRole.NEWS.value
        or datasets[item].tool_ids
        or datasets[item].dashboard_ids
        or datasets[item].export_ids
        for item in (FMP_STOCK_LATEST_EVIDENCE_DATASET_ID, FMP_STOCK_LATEST_ARTICLES_DATASET_ID)
    ):
        raise ValidationError("FMP stock-latest datasets must remain private news relations")


class FmpStockLatestAttempt:
    """Opaque, persisted pre-request intent capability."""

    __slots__ = ("__weakref__",)


class PreparedFmpStockLatestCapture:
    """Opaque, fully validated response capability with no public raw-body API."""

    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _AttemptState:
    owner_token: object
    attempt_id: str
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    intent_recorded_at: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    attempt: _AttemptState
    response: CapturedFmpStockLatestResponse
    captured_at: str
    rows: tuple[_ArticleRow, ...]
    semantic_identity: str
    capture_id: str


_ATTEMPTS: weakref.WeakKeyDictionary[FmpStockLatestAttempt, _AttemptState] = (
    weakref.WeakKeyDictionary()
)
_PREPARED: weakref.WeakKeyDictionary[PreparedFmpStockLatestCapture, _PreparedState] = (
    weakref.WeakKeyDictionary()
)


class _ResponseRejected(ValidationError):
    def __init__(self, response: CapturedFmpStockLatestResponse | None = None) -> None:
        super().__init__("FMP stock-latest response was rejected")
        self.response = response


class FmpStockLatestImporter:
    """Commit intent, capture outside locks, and publish one validated page."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("FMP stock-latest importer requires explicit stores and a clock")
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
        attempt: FmpStockLatestAttempt,
    ) -> _AttemptState:
        """Reserve this in-process capability before any provider call.

        The persisted outcome check closes the only restart/replay path.  Its
        read transaction is closed before this method returns, so the
        subsequent transport call holds no database lock or transaction.
        """

        with self._capture_lock:
            state = _ATTEMPTS.get(attempt) if isinstance(attempt, FmpStockLatestAttempt) else None
            if state is None or state.owner_token is not self._owner_token:
                raise ValidationError("FMP stock-latest attempt is foreign or expired")
            if state.attempt_id in self._consumed_attempt_ids:
                raise ConflictError("FMP stock-latest attempt capability was already consumed")
            with read_connection(self.store_map, StoreRole.NEWS) as connection:
                has_terminal_outcome = (
                    connection.execute(
                        "SELECT 1 FROM fmp_stock_latest_outcomes WHERE attempt_id=?",
                        (state.attempt_id,),
                    ).fetchone()
                    is not None
                )
            if has_terminal_outcome:
                self._consumed_attempt_ids.add(state.attempt_id)
                raise ConflictError("FMP stock-latest attempt already has a terminal outcome")
            self._consumed_attempt_ids.add(state.attempt_id)
            return state

    def reserve_attempt(self, request: FmpStockLatestRequest | None = None) -> FmpStockLatestAttempt:
        request = FmpStockLatestRequest() if request is None else request
        if not isinstance(request, FmpStockLatestRequest):
            raise ValidationError("FMP stock-latest request is invalid")
        scope = request.request_scope()
        scope_sha256 = _sha256_json(scope)
        intent_recorded_at = _utc_text(self._clock(), "intent_recorded_at")
        attempt_id = stable_id(
            "fmp_stock_latest_attempt",
            FMP_STOCK_LATEST_PROFILE_ID,
            scope_sha256,
        )

        def reserve(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT attempt_id FROM fmp_stock_latest_attempts
                    WHERE collector_id=? AND profile_id=? AND request_scope_sha256=?
                    """,
                    (
                        FMP_STOCK_LATEST_COLLECTOR_ID,
                        FMP_STOCK_LATEST_PROFILE_ID,
                        scope_sha256,
                    ),
                ).fetchone()
                if existing is not None:
                    raise ConflictError("An FMP stock-latest intent already blocks this profile")
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_attempts (
                        attempt_id, dataset_id, collector_id, profile_id,
                        request_scope_json, request_scope_sha256,
                        intent_recorded_at, intent_recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        attempt_id,
                        FMP_STOCK_LATEST_EVIDENCE_DATASET_ID,
                        FMP_STOCK_LATEST_COLLECTOR_ID,
                        FMP_STOCK_LATEST_PROFILE_ID,
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
        attempt = FmpStockLatestAttempt()
        _ATTEMPTS[attempt] = _AttemptState(
            owner_token=self._owner_token,
            attempt_id=attempt_id,
            request_scope=scope,
            request_scope_sha256=scope_sha256,
            intent_recorded_at=intent_recorded_at,
        )
        return attempt

    def capture_attempt(
        self,
        attempt: FmpStockLatestAttempt,
        *,
        api_key: str,
        transport: FmpStockLatestTransport,
    ) -> PreparedFmpStockLatestCapture:
        if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
            raise ValidationError("FMP stock-latest credential is missing or invalid")
        if not isinstance(transport, FmpStockLatestTransport):
            raise ValidationError("FMP stock-latest transport does not implement the reviewed interface")
        state = self._consume_attempt_before_transport(attempt)
        transport_failed = False
        try:
            response = transport.get(
                path=FMP_STOCK_LATEST_PATH,
                query={"page": "0", "limit": "1000"},
                headers={"apikey": api_key, "Accept": "application/json"},
                timeout_seconds=FMP_STOCK_LATEST_TIMEOUT_SECONDS,
                max_bytes=FMP_STOCK_LATEST_MAX_BYTES,
            )
        except Exception:
            transport_failed = True
        if transport_failed:
            # Raise after the exception handler so an arbitrary injected
            # transport failure cannot retain a key-bearing exception context.
            raise StoreUnavailableError("FMP stock-latest provider request failed")
        if not isinstance(response, CapturedFmpStockLatestResponse):
            raise _ResponseRejected()
        if response.status != 200 or not _json_content_type(response.content_type):
            raise _ResponseRejected(response)
        try:
            rows = _parse_rows(response.body)
        except ValidationError as exc:
            raise _ResponseRejected(response) from exc
        captured_at = _utc_text(self._clock(), "captured_at")
        semantic_rows = sorted(
            (row.semantic_mapping() for row in rows),
            key=dumps_strict,
        )
        semantic_identity = _sha256_json(
            {
                "collector_id": FMP_STOCK_LATEST_COLLECTOR_ID,
                "normalization_version": FMP_STOCK_LATEST_NORMALIZATION_VERSION,
                "normalized_partial_page": semantic_rows,
                "request_scope": dict(state.request_scope),
            }
        )
        capture_id = stable_id(
            "fmp_stock_latest_capture",
            state.attempt_id,
            semantic_identity,
        )
        prepared = PreparedFmpStockLatestCapture()
        _PREPARED[prepared] = _PreparedState(
            owner_token=self._owner_token,
            attempt=state,
            response=response,
            captured_at=captured_at,
            rows=rows,
            semantic_identity=semantic_identity,
            capture_id=capture_id,
        )
        return prepared

    def record_terminal_failure(
        self,
        attempt: FmpStockLatestAttempt,
        *,
        outcome_kind: str,
        response: CapturedFmpStockLatestResponse | None = None,
    ) -> FmpStockLatestReceipt:
        state = _ATTEMPTS.get(attempt) if isinstance(attempt, FmpStockLatestAttempt) else None
        if state is None or state.owner_token is not self._owner_token:
            raise ValidationError("FMP stock-latest attempt is foreign or expired")
        if outcome_kind not in {"request_failed", "response_rejected", "publication_failed"}:
            raise ValidationError("FMP stock-latest terminal outcome is invalid")
        recorded_at = _utc_text(self._clock(), "recorded_at")
        response_sha256 = None if response is None else _sha256(response.body)
        response_byte_count = None if response is None else len(response.body)
        status = None if response is None else response.status
        content_type = None if response is None else response.content_type
        outcome_id = stable_id("fmp_stock_latest_outcome", state.attempt_id, outcome_kind)

        def record(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM fmp_stock_latest_outcomes WHERE attempt_id=?",
                    (state.attempt_id,),
                ).fetchone() is not None:
                    raise ConflictError("FMP stock-latest attempt already has a terminal outcome")
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        state.attempt_id,
                        outcome_kind,
                        response_sha256,
                        response_byte_count,
                        status,
                        content_type,
                        recorded_at,
                    ),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

        self._write(record)
        return FmpStockLatestReceipt(
            attempt_id=state.attempt_id,
            outcome=outcome_kind,
            capture_id=None,
            article_count=0,
            written_count=1,
        )

    def publish_prepared(self, prepared: PreparedFmpStockLatestCapture) -> FmpStockLatestReceipt:
        state = _PREPARED.get(prepared) if isinstance(prepared, PreparedFmpStockLatestCapture) else None
        if state is None or state.owner_token is not self._owner_token:
            raise ValidationError("FMP stock-latest prepared capture is foreign or expired")
        outcome_id = stable_id("fmp_stock_latest_outcome", state.attempt.attempt_id, "succeeded")

        def publish(connection: sqlite3.Connection) -> FmpStockLatestReceipt:
            existing = connection.execute(
                """
                SELECT outcome.outcome_kind, capture.capture_id, capture.semantic_identity
                FROM fmp_stock_latest_outcomes AS outcome
                LEFT JOIN fmp_stock_latest_captures AS capture
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
                    return FmpStockLatestReceipt(
                        attempt_id=state.attempt.attempt_id,
                        outcome="unchanged",
                        capture_id=state.capture_id,
                        article_count=len(state.rows),
                        written_count=0,
                    )
                raise ConflictError("FMP stock-latest attempt already has a terminal outcome")
            connection.execute("BEGIN IMMEDIATE")
            try:
                response_sha256 = _sha256(state.response.body)
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_outcomes (
                        outcome_id, attempt_id, outcome_kind, response_sha256,
                        response_byte_count, http_status, content_type,
                        recorded_at, recorded_precision
                    ) VALUES (?, ?, 'succeeded', ?, ?, 200, ?, ?, 'datetime')
                    """,
                    (
                        outcome_id,
                        state.attempt.attempt_id,
                        response_sha256,
                        len(state.response.body),
                        state.response.content_type,
                        state.captured_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO fmp_stock_latest_captures (
                        capture_id, dataset_id, attempt_id, outcome_id,
                        request_scope_sha256, response_sha256, response_bytes,
                        http_status, content_type, semantic_identity, completeness,
                        captured_at, captured_precision, row_count,
                        normalization_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 200, ?, ?, 'partial',
                              ?, 'datetime', ?, ?)
                    """,
                    (
                        state.capture_id,
                        FMP_STOCK_LATEST_EVIDENCE_DATASET_ID,
                        state.attempt.attempt_id,
                        outcome_id,
                        state.attempt.request_scope_sha256,
                        response_sha256,
                        state.response.body,
                        state.response.content_type,
                        state.semantic_identity,
                        state.captured_at,
                        len(state.rows),
                        FMP_STOCK_LATEST_NORMALIZATION_VERSION,
                    ),
                )
                written = 2
                for row in state.rows:
                    article = connection.execute(
                        """
                        SELECT article_id FROM fmp_stock_latest_articles
                        WHERE article_id=?
                        """,
                        (row.article_id,),
                    ).fetchone()
                    if article is None:
                        connection.execute(
                            """
                            INSERT INTO fmp_stock_latest_articles (
                                article_id, provider_namespace, site, source_url,
                                symbol, created_capture_id
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row.article_id,
                                FMP_STOCK_LATEST_PROVIDER_NAMESPACE,
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
                        FROM fmp_stock_latest_article_versions
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
                            "fmp_stock_latest_article_version",
                            row.article_id,
                            content_sha256,
                            str(sequence),
                        )
                        connection.execute(
                            """
                            INSERT INTO fmp_stock_latest_article_versions (
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
                        INSERT INTO fmp_stock_latest_capture_articles (
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
            return FmpStockLatestReceipt(
                attempt_id=state.attempt.attempt_id,
                outcome="succeeded",
                capture_id=state.capture_id,
                article_count=len(state.rows),
                written_count=written,
            )

        return self._write(publish)  # type: ignore[return-value]

    def run_once(
        self,
        *,
        api_key: str,
        transport: FmpStockLatestTransport,
        request: FmpStockLatestRequest | None = None,
    ) -> FmpStockLatestReceipt:
        """Run the only permitted physical request and append one outcome."""

        if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
            raise ValidationError("FMP stock-latest credential is missing or invalid")
        if not isinstance(transport, FmpStockLatestTransport):
            raise ValidationError("FMP stock-latest transport does not implement the reviewed interface")
        attempt = self.reserve_attempt(request)
        try:
            prepared = self.capture_attempt(attempt, api_key=api_key, transport=transport)
        except _ResponseRejected as exc:
            self.record_terminal_failure(
                attempt,
                outcome_kind="response_rejected",
                response=exc.response,
            )
            raise ValidationError("FMP stock-latest response was rejected") from exc
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
    "CapturedFmpStockLatestResponse",
    "FMP_STOCK_LATEST_ARTICLES_DATASET_ID",
    "FMP_STOCK_LATEST_COLLECTOR_ID",
    "FMP_STOCK_LATEST_EVIDENCE_DATASET_ID",
    "FMP_STOCK_LATEST_LIMIT",
    "FMP_STOCK_LATEST_MAX_BYTES",
    "FMP_STOCK_LATEST_MIGRATION_ID",
    "FMP_STOCK_LATEST_PAGE",
    "FMP_STOCK_LATEST_PATH",
    "FMP_STOCK_LATEST_TIMEOUT_SECONDS",
    "FmpStockLatestImporter",
    "FmpStockLatestReceipt",
    "FmpStockLatestRequest",
    "FmpStockLatestTransport",
    "PreparedFmpStockLatestCapture",
    "StdlibFmpStockLatestTransport",
)
