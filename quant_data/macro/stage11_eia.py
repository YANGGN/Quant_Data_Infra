"""Bounded EIA capture and normalization primitives for Stage 11.

The module is intentionally side-effect free outside an explicitly supplied
transport.  It neither opens a database nor schedules work.  The caller owns
one-second pacing, retry policy, local capture time, and publication.  These
primitives only prepare the reviewed requests, reject incomplete/unsafe
provider responses, and return immutable credential-free candidates.

Every retained response body is strict-JSON canonicalized after recursively
removing echoed ``api_key``/``UserID`` fields.  A known credential is also
redacted from arbitrary reflected strings during capture, so raw provider
evidence cannot retain it.
"""

from __future__ import annotations

import hashlib
import http.client
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from .stage11_retry import (
    raise_for_transient_http_status,
    transient_error_from_transport_exception,
)


EIA_HOST = "api.eia.gov"
EIA_RETAIL_PATH = "/v2/electricity/retail-sales/data"
EIA_WEEKLY_PATH = "/v2/seriesid/PET.WCESTUS1.W"
EIA_WEEKLY_SERIES_ID = "PET.WCESTUS1.W"
# The /v2/seriesid compatibility route accepts the complete legacy ID, while
# its translated petroleum rows expose the underlying fixed facet code. Keep
# both identities exact instead of treating either as a general alias.
_EIA_WEEKLY_PROVIDER_SERIES_ID = "WCESTUS1"
EIA_RETAIL_METRICS = ("sales", "revenue", "price", "customers")
EIA_RETAIL_CANONICAL_SERIES_BY_METRIC = {
    "sales": "macro.eia.electricity.retail_sales",
    "revenue": "macro.eia.electricity.retail_revenue",
    "price": "macro.eia.electricity.retail_price",
    "customers": "macro.eia.electricity.retail_customers",
}
EIA_RETAIL_FREQUENCY = "monthly"
EIA_RETAIL_STATE_ID = "US"
EIA_RETAIL_SECTOR_ID = "ALL"
EIA_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
EIA_RETAIL_MAX_ROWS_PER_PAGE = 5_000
EIA_RETAIL_PAGE_LENGTH = EIA_RETAIL_MAX_ROWS_PER_PAGE
EIA_RETAIL_MAX_PAGES = 8
EIA_RETAIL_MAX_TOTAL_ROWS = EIA_RETAIL_PAGE_LENGTH * EIA_RETAIL_MAX_PAGES
# These public bounds mirror ``config/stage11_macro_scope.json`` exactly.
EIA_RETAIL_MAX_RESPONSE_BYTES = EIA_MAX_RESPONSE_BYTES
EIA_WEEKLY_MAX_RESPONSE_BYTES = EIA_MAX_RESPONSE_BYTES
EIA_WEEKLY_MAX_ROWS = 5_000
EIA_TIMEOUT_SECONDS = 45
EIA_WEEKLY_CANONICAL_SERIES_ID = "macro.eia.weekly.petroleum_stock"

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_CREDENTIAL_FIELD = frozenset({"userid", "apikey"})
_UNSAFE_KEY_TOKENS = (
    "apikey",
    "userid",
    "authorization",
    "authentication",
    "credential",
    "token",
    "bearer",
    "cookie",
    "header",
    "url",
    "uri",
    "query",
)
_UNSAFE_HEADER_KEYS = frozenset(
    {
        "useragent",
        "referer",
        "origin",
        "host",
        "accept",
        "contenttype",
        "contentlength",
        "contentencoding",
        "cachecontrol",
        "etag",
        "server",
        "date",
        "location",
        "endpoint",
        "link",
        "request",
        "parameter",
        "parameters",
        "params",
    }
)
_URL_LIKE_VALUE = re.compile(
    r"(?i)(?:^|\s)(?:[a-z][a-z0-9+.-]{0,31}:|//|www\.)|^/[a-z0-9._~!$&'()*+,;=:@%/-]+"
)
_HEADER_LIKE_VALUE = re.compile(r"^[a-z][a-z0-9-]{0,127}\s*:\s*\S", re.IGNORECASE)
_UNSAFE_VALUE_TOKEN = re.compile(
    r"(?i)(?:api[-_]?key|user[-_]?id|authorization|authentication|credential|token|bearer|cookie|header)"
)
_DROP = object()
_RETAIL_VALUE_UNIT_FIELDS = tuple(
    (metric, f"{metric}-units") for metric in EIA_RETAIL_METRICS
)
_RETAIL_REQUIRED_FIELDS = frozenset(
    {
        "period",
        "stateid",
        "sectorid",
        *(metric for metric, _ in _RETAIL_VALUE_UNIT_FIELDS),
        *(unit_field for _, unit_field in _RETAIL_VALUE_UNIT_FIELDS),
    }
)
_RETAIL_ALLOWED_FIELDS = _RETAIL_REQUIRED_FIELDS | frozenset(
    {"stateDescription", "sectorName"}
)
# ``/v2/seriesid/<id>`` responses are path-bound.  The live Stage 11 diagnostic
# confirmed that EIA also repeats the series identity and fixed display metadata
# in one exact eleven-field row shape.  Keep that shape closed and require its
# redundant identity to match the reviewed prepared request.  The smaller
# path-bound and legacy shapes remain accepted for the deterministic fixture
# corpus.
_WEEKLY_PATH_BOUND_ROW_FIELDS = frozenset(
    {"period", "series-description", "value", "units"}
)
_WEEKLY_LEGACY_ROW_FIELDS = frozenset({"period", "series", "value", "units"})
_WEEKLY_PROVIDER_ROW_FIELDS = frozenset(
    {
        "area-name",
        "duoarea",
        "period",
        "process",
        "process-name",
        "product",
        "product-name",
        "series",
        "series-description",
        "units",
        "value",
    }
)
_WEEKLY_PROVIDER_METADATA_FIELDS = (
    "area-name",
    "duoarea",
    "process",
    "process-name",
    "product",
    "product-name",
    "series-description",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _validate_api_key(value: object) -> str:
    if not isinstance(value, str) or _API_KEY.fullmatch(value) is None:
        raise ValidationError("EIA credential is missing or invalid")
    return value


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"EIA {field_name} is invalid")
    return value


def _validate_text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"EIA {field_name} must be nonempty text")
    return value


def _validate_month(value: object) -> str:
    if not isinstance(value, str) or _MONTH.fullmatch(value) is None:
        raise ValidationError("EIA retail period must be YYYY-MM")
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ValidationError("EIA retail period must be YYYY-MM") from exc
    return value


def _validate_date(value: object, field_name: str = "period") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"EIA weekly {field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"EIA weekly {field_name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"EIA weekly {field_name} must be an ISO date")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError(f"EIA {field_name} must be a finite number")
    if isinstance(value, str) and (not value or value.strip() != value or "," in value):
        raise ValidationError(f"EIA {field_name} must be a finite number")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"EIA {field_name} must be a finite number") from exc
    if not parsed.is_finite():
        raise ValidationError(f"EIA {field_name} must be finite")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("EIA numeric value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _integer(value: object, field_name: str, *, maximum: int) -> int:
    parsed = _decimal(value, field_name)
    if parsed != parsed.to_integral_value():
        raise ValidationError(f"EIA {field_name} must be an integer")
    result = int(parsed)
    if result < 0 or result > maximum:
        raise ValidationError(f"EIA {field_name} is outside the supported range")
    return result


def _require_bytes(value: object, *, maximum: int, kind: str) -> bytes:
    if not isinstance(value, bytes):
        raise ValidationError(f"EIA {kind} body must be bytes")
    if not value:
        raise ValidationError(f"EIA {kind} body is empty")
    if len(value) > maximum:
        raise ResourceLimitError(f"EIA {kind} response exceeds the reviewed byte bound")
    return value


def _mapping_of_strings(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"EIA transport {field_name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValidationError(f"EIA transport {field_name} must contain strings")
        result[key] = item
    return result


def _unsafe_key(key: str) -> bool:
    lowered = key.casefold().strip()
    normalized = lowered.replace("_", "").replace("-", "").replace(" ", "")
    return (
        normalized in _CREDENTIAL_FIELD
        or normalized in _UNSAFE_HEADER_KEYS
        or lowered.startswith("x-")
        or any(token in normalized for token in _UNSAFE_KEY_TOKENS)
    )


def _unsafe_string(value: str, *, credential_value: str | None) -> bool:
    return (
        (credential_value is not None and credential_value in value)
        or _URL_LIKE_VALUE.search(value) is not None
        or _HEADER_LIKE_VALUE.search(value) is not None
        or _UNSAFE_VALUE_TOKEN.search(value) is not None
    )


def _sanitize_response(value: object, *, credential_value: str | None) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("EIA response object keys must be strings")
            if _unsafe_key(key):
                continue
            sanitized = _sanitize_response(item, credential_value=credential_value)
            if sanitized is not _DROP:
                result[key] = sanitized
        return result
    if isinstance(value, list):
        result: list[object] = []
        for item in value:
            sanitized = _sanitize_response(item, credential_value=credential_value)
            if sanitized is not _DROP:
                result.append(sanitized)
        return result
    if isinstance(value, str):
        if _unsafe_string(value, credential_value=credential_value):
            return _DROP
        return value
    return value


def _load_sanitized_response(
    body: object,
    *,
    maximum: int,
    kind: str,
    credential_value: str | None,
) -> tuple[dict[str, Any], bytes]:
    raw = _require_bytes(body, maximum=maximum, kind=kind)
    parsed = loads_strict(raw, max_bytes=maximum)
    if not isinstance(parsed, dict):
        raise ValidationError(f"EIA {kind} response must be a JSON object")
    sanitized = _sanitize_response(parsed, credential_value=credential_value)
    if not isinstance(sanitized, dict):
        raise ValidationError(f"EIA {kind} response must be a JSON object")
    retained = dumps_strict(sanitized, max_bytes=maximum).encode("utf-8")
    return sanitized, retained


def _response_payload(value: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    if "error" in value or "Error" in value:
        raise StoreUnavailableError("EIA provider response was unavailable")
    response = value.get("response")
    if not isinstance(response, dict):
        raise ValidationError(f"EIA {kind} response lacks a response object")
    if "error" in response or "Error" in response:
        raise StoreUnavailableError("EIA provider response was unavailable")
    return response


@dataclass(frozen=True, slots=True)
class CapturedEiaResponse:
    """Bounded raw EIA HTTP response whose body is omitted from ``repr``."""

    status: int
    content_type: str = field(repr=False)
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("EIA response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("EIA response content type is invalid")
        _require_bytes(
            self.body,
            maximum=max(EIA_RETAIL_MAX_RESPONSE_BYTES, EIA_WEEKLY_MAX_RESPONSE_BYTES),
            kind="response",
        )


@runtime_checkable
class EiaTransport(Protocol):
    """Injectable transport for one validated EIA request."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedEiaResponse: ...


@dataclass(frozen=True, slots=True)
class PreparedEiaRetailPage:
    """Credential-free immutable page of the frozen retail-sales query."""

    offset: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.offset, bool)
            or not isinstance(self.offset, int)
            or self.offset < 0
            or self.offset % EIA_RETAIL_PAGE_LENGTH != 0
            or self.offset >= EIA_RETAIL_MAX_TOTAL_ROWS
        ):
            raise ValidationError("EIA retail page offset is outside the approved scope")

    @property
    def endpoint_path(self) -> str:
        return EIA_RETAIL_PATH

    @property
    def page_number(self) -> int:
        return self.offset // EIA_RETAIL_PAGE_LENGTH + 1

    @property
    def query(self) -> dict[str, str]:
        return {
            "data[0]": "sales",
            "data[1]": "revenue",
            "data[2]": "price",
            "data[3]": "customers",
            "facets[stateid][]": EIA_RETAIL_STATE_ID,
            "facets[sectorid][]": EIA_RETAIL_SECTOR_ID,
            "frequency": EIA_RETAIL_FREQUENCY,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": str(EIA_RETAIL_PAGE_LENGTH),
            "offset": str(self.offset),
        }

    def request_scope(self) -> dict[str, object]:
        return {
            "provider": "eia",
            "endpoint_path": self.endpoint_path,
            "frequency": EIA_RETAIL_FREQUENCY,
            "state_id": EIA_RETAIL_STATE_ID,
            "sector_id": EIA_RETAIL_SECTOR_ID,
            "metrics": list(EIA_RETAIL_METRICS),
            "sort": {"column": "period", "direction": "asc"},
            "page_length": EIA_RETAIL_PAGE_LENGTH,
            "offset": self.offset,
        }


@dataclass(frozen=True, slots=True)
class PreparedEiaWeeklyCapture:
    """Credential-free immutable request for the fixed weekly EIA series."""

    endpoint_path: str = field(default=EIA_WEEKLY_PATH, repr=False)
    series_id: str = EIA_WEEKLY_SERIES_ID

    def __post_init__(self) -> None:
        if self.endpoint_path != EIA_WEEKLY_PATH or self.series_id != EIA_WEEKLY_SERIES_ID:
            raise ValidationError("EIA weekly request is outside the approved Stage 11 scope")

    @property
    def query(self) -> dict[str, str]:
        return {}

    def request_scope(self) -> dict[str, object]:
        return {
            "provider": "eia",
            "endpoint_path": self.endpoint_path,
            "series_id": self.series_id,
            "frequency": "weekly",
        }


def prepare_eia_retail_page(offset: int = 0) -> PreparedEiaRetailPage:
    """Prepare one fixed-size offset page without executing network I/O."""

    return PreparedEiaRetailPage(offset=offset)


def prepare_eia_weekly_capture() -> PreparedEiaWeeklyCapture:
    """Prepare the one exact weekly series request."""

    return PreparedEiaWeeklyCapture()


def _require_exact_weekly_capture(value: object) -> PreparedEiaWeeklyCapture:
    """Return only the one reviewed path-bound weekly request.

    ``PreparedEiaWeeklyCapture`` validates this in ``__post_init__``.  The
    explicit check here is deliberately repeated at parser and transport
    boundaries so an object that has been tampered with after construction
    cannot broaden a request or bind a response to another provider series.
    """

    if not isinstance(value, PreparedEiaWeeklyCapture):
        raise ValidationError("EIA weekly capture requires a prepared request")
    if value.endpoint_path != EIA_WEEKLY_PATH or value.series_id != EIA_WEEKLY_SERIES_ID:
        raise ValidationError("EIA weekly capture is outside the approved Stage 11 scope")
    return value


class StdlibEiaTransport:
    """TLS-verified standard-library EIA transport; redirects are not followed."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedEiaResponse:
        query_values = _mapping_of_strings(query, "query")
        header_values = _mapping_of_strings(headers, "headers")
        if header_values != {"Accept": "application/json"} or timeout_seconds != EIA_TIMEOUT_SECONDS:
            raise ValidationError("EIA transport request is outside the approved scope")
        key = query_values.get("api_key")
        _validate_api_key(key)
        if path == EIA_RETAIL_PATH:
            offset_text = query_values.get("offset")
            if not isinstance(offset_text, str) or not offset_text.isdecimal():
                raise ValidationError("EIA transport request is outside the approved scope")
            expected = prepare_eia_retail_page(int(offset_text)).query
            if (
                max_bytes != EIA_RETAIL_MAX_RESPONSE_BYTES
                or set(query_values) != {*expected, "api_key"}
                or any(query_values[item] != expected[item] for item in expected)
            ):
                raise ValidationError("EIA transport request is outside the approved scope")
        elif path == EIA_WEEKLY_PATH:
            if max_bytes != EIA_WEEKLY_MAX_RESPONSE_BYTES or set(query_values) != {"api_key"}:
                raise ValidationError("EIA transport request is outside the approved scope")
        else:
            raise ValidationError("EIA transport request is outside the approved scope")
        target = f"{path}?{urlencode(query_values)}"
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(EIA_HOST, timeout=EIA_TIMEOUT_SECONDS)
            connection.request("GET", target, headers=header_values)
            response = connection.getresponse()
            # Retryable status handling precedes response metadata and body work.
            raise_for_transient_http_status(response.status)
            declared_size = response.getheader("Content-Length")
            if declared_size is not None:
                if not isinstance(declared_size, str) or not declared_size.isdecimal():
                    raise StoreUnavailableError("EIA provider response was unavailable")
                if int(declared_size) > max_bytes:
                    raise ResourceLimitError("EIA response exceeds the reviewed byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("EIA response exceeds the reviewed byte bound")
            return CapturedEiaResponse(
                status=response.status,
                content_type=response.getheader("Content-Type") or "application/octet-stream",
                body=body,
            )
        except (ResourceLimitError, StoreUnavailableError, ValidationError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise transient_error_from_transport_exception(exc) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except (OSError, http.client.HTTPException):
                    # A fully read response remains usable despite close failure.
                    pass


@dataclass(frozen=True, slots=True)
class EiaRetailObservation:
    """One metric value expanded from an EIA monthly retail source row."""

    period: str
    metric: str
    value: Decimal
    unit: str
    source_row: int

    def __post_init__(self) -> None:
        _validate_month(self.period)
        if self.metric not in EIA_RETAIL_METRICS:
            raise ValidationError("EIA retail metric is outside the approved scope")
        _decimal(self.value, self.metric)
        _validate_text(self.unit, f"{self.metric} unit")
        if isinstance(self.source_row, bool) or not isinstance(self.source_row, int) or self.source_row < 1:
            raise ValidationError("EIA retail source row is invalid")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "period": self.period,
            "metric": self.metric,
            "value": _decimal_text(self.value),
            "unit": self.unit,
        }

    @property
    def canonical_series_id(self) -> str:
        return EIA_RETAIL_CANONICAL_SERIES_BY_METRIC[self.metric]


def _retail_page_semantic_material(
    prepared: PreparedEiaRetailPage,
    *,
    total: int,
    rows: tuple[EiaRetailObservation, ...],
) -> dict[str, object]:
    return {
        "provider": "eia",
        "request_scope": prepared.request_scope(),
        "pagination_total": total,
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class EiaRetailPageCapture:
    """One fully validated, redacted page of the fixed EIA retail query."""

    request: PreparedEiaRetailPage = field(repr=False)
    total: int
    raw_row_count: int
    raw_bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    rows: tuple[EiaRetailObservation, ...] = field(repr=False)
    semantic_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.request, PreparedEiaRetailPage):
            raise ValidationError("EIA retail page request is invalid")
        if isinstance(self.total, bool) or not isinstance(self.total, int):
            raise ValidationError("EIA retail total is invalid")
        if self.total < 1 or self.total > EIA_RETAIL_MAX_TOTAL_ROWS:
            raise ValidationError("EIA retail total is outside the supported range")
        if self.request.offset >= self.total:
            raise ValidationError("EIA retail page offset is beyond the declared total")
        if isinstance(self.raw_row_count, bool) or not isinstance(self.raw_row_count, int):
            raise ValidationError("EIA retail page row count is invalid")
        expected_count = min(EIA_RETAIL_PAGE_LENGTH, self.total - self.request.offset)
        if self.raw_row_count != expected_count:
            raise ValidationError("EIA retail page does not prove its declared total")
        _validate_digest(self.raw_bytes_sha256, "retail raw response digest")
        _require_bytes(
            self.raw_bytes,
            maximum=EIA_RETAIL_MAX_RESPONSE_BYTES,
            kind="retail",
        )
        if self.raw_bytes_sha256 != _sha256(self.raw_bytes):
            raise ValidationError("EIA retail raw response digest is invalid")
        if (
            not isinstance(self.rows, tuple)
            or not self.rows
            or len(self.rows) > self.raw_row_count * len(EIA_RETAIL_METRICS)
        ):
            raise ValidationError("EIA retail normalized row count is invalid")
        previous: tuple[str, str] | None = None
        units: dict[str, str] = {}
        metric_counts = {metric: 0 for metric in EIA_RETAIL_METRICS}
        for row in self.rows:
            if not isinstance(row, EiaRetailObservation):
                raise ValidationError("EIA retail normalized rows are invalid")
            key = (row.period, row.metric)
            if previous is not None and key <= previous:
                raise ValidationError("EIA retail normalized rows must be strictly ordered")
            previous = key
            if row.metric in units and units[row.metric] != row.unit:
                raise ValidationError("EIA retail units are inconsistent within a page")
            units[row.metric] = row.unit
            metric_counts[row.metric] += 1
        if (
            any(
                metric_counts[metric] != self.raw_row_count
                for metric in ("sales", "revenue", "price")
            )
            or metric_counts["customers"] > self.raw_row_count
        ):
            raise ValidationError("EIA retail normalized metric coverage is invalid")
        _validate_digest(self.semantic_sha256, "retail page semantic digest")
        if self.semantic_sha256 != _sha256_json(
            _retail_page_semantic_material(self.request, total=self.total, rows=self.rows)
        ):
            raise ValidationError("EIA retail page semantic digest is invalid")

    @property
    def tombstone_authoritative(self) -> bool:
        """A provider page is never authority to infer an omitted tombstone."""

        return False

    @property
    def raw_bytes_size(self) -> int:
        return len(self.raw_bytes)


def parse_eia_retail_page_response(
    *,
    body: bytes,
    prepared: PreparedEiaRetailPage | None = None,
    credential_value: str | None = None,
) -> EiaRetailPageCapture:
    """Parse one exact page and prove its own total/count boundary."""

    request = prepare_eia_retail_page() if prepared is None else prepared
    if not isinstance(request, PreparedEiaRetailPage):
        raise ValidationError("EIA retail capture requires a prepared page")
    if credential_value is not None:
        _validate_api_key(credential_value)
    value, retained_bytes = _load_sanitized_response(
        body,
        maximum=EIA_RETAIL_MAX_RESPONSE_BYTES,
        kind="retail",
        credential_value=credential_value,
    )
    response = _response_payload(value, kind="retail")
    if response.get("frequency") != EIA_RETAIL_FREQUENCY:
        raise ValidationError("EIA retail response frequency is invalid")
    if response.get("dateFormat") != "YYYY-MM":
        raise ValidationError("EIA retail response date format is invalid")
    total = _integer(response.get("total"), "retail total", maximum=EIA_RETAIL_MAX_TOTAL_ROWS)
    data = response.get("data")
    if not isinstance(data, list) or len(data) > EIA_RETAIL_PAGE_LENGTH:
        raise ValidationError("EIA retail response data rows are invalid")
    expected_count = min(EIA_RETAIL_PAGE_LENGTH, max(0, total - request.offset))
    if total < 1 or len(data) != expected_count:
        raise ValidationError("EIA retail page does not prove its declared total")
    rows: list[EiaRetailObservation] = []
    seen_periods: set[str] = set()
    for raw_index, raw in enumerate(data, start=1):
        if not isinstance(raw, dict) or not _RETAIL_REQUIRED_FIELDS.issubset(raw) or not set(raw).issubset(_RETAIL_ALLOWED_FIELDS):
            raise ValidationError("EIA retail response data row has an unsupported shape")
        if raw["stateid"] != EIA_RETAIL_STATE_ID or raw["sectorid"] != EIA_RETAIL_SECTOR_ID:
            raise ValidationError("EIA retail response row is outside the approved US/ALL scope")
        period = _validate_month(raw["period"])
        if period in seen_periods:
            raise ValidationError("EIA retail response contains duplicate periods")
        seen_periods.add(period)
        for metric_index, (metric, unit_field) in enumerate(
            _RETAIL_VALUE_UNIT_FIELDS, start=1
        ):
            unit = _validate_text(raw[unit_field], unit_field)
            # EIA legitimately reports null for unavailable historical metric
            # values (notably early customer counts). The complete paginated
            # raw row remains immutable evidence, while no canonical value is
            # invented for that metric/period.
            if raw[metric] is None:
                if metric != "customers":
                    raise ValidationError(f"EIA {metric} must be a finite number")
                continue
            rows.append(
                EiaRetailObservation(
                    period=period,
                    metric=metric,
                    value=_decimal(raw[metric], metric),
                    unit=unit,
                    source_row=(raw_index - 1) * len(EIA_RETAIL_METRICS) + metric_index,
                )
            )
    ordered_rows = tuple(sorted(rows, key=lambda row: (row.period, row.metric)))
    semantic_sha256 = _sha256_json(
        _retail_page_semantic_material(request, total=total, rows=ordered_rows)
    )
    return EiaRetailPageCapture(
        request=request,
        total=total,
        raw_row_count=len(data),
        raw_bytes_sha256=_sha256(retained_bytes),
        raw_bytes=retained_bytes,
        rows=ordered_rows,
        semantic_sha256=semantic_sha256,
    )


def _retail_cohort_scope(
    pages: tuple[EiaRetailPageCapture, ...],
) -> dict[str, object]:
    total = pages[0].total
    return {
        "provider": "eia",
        "endpoint_path": EIA_RETAIL_PATH,
        "frequency": EIA_RETAIL_FREQUENCY,
        "state_id": EIA_RETAIL_STATE_ID,
        "sector_id": EIA_RETAIL_SECTOR_ID,
        "metrics": list(EIA_RETAIL_METRICS),
        "sort": {"column": "period", "direction": "asc"},
        "page_length": EIA_RETAIL_PAGE_LENGTH,
        "pagination_total": total,
        "page_offsets": [page.request.offset for page in pages],
        "tombstone_authoritative": False,
    }


def _retail_cohort_semantic_material(
    pages: tuple[EiaRetailPageCapture, ...],
    rows: tuple[EiaRetailObservation, ...],
) -> dict[str, object]:
    return {
        "provider": "eia",
        "request_scope": _retail_cohort_scope(pages),
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class EiaRetailCapture:
    """A complete paginated US/ALL retail-sales cohort before persistence."""

    pages: tuple[EiaRetailPageCapture, ...] = field(repr=False)
    rows: tuple[EiaRetailObservation, ...] = field(repr=False)
    semantic_sha256: str = ""
    completeness: str = "complete"
    tombstone_authoritative: bool = False

    def __post_init__(self) -> None:
        if self.completeness != "complete" or self.tombstone_authoritative is not False:
            raise ValidationError("EIA retail cohort cannot authorize tombstones")
        if not isinstance(self.pages, tuple) or not self.pages:
            raise ValidationError("EIA retail cohort pages are invalid")
        if not all(isinstance(page, EiaRetailPageCapture) for page in self.pages):
            raise ValidationError("EIA retail cohort pages are invalid")
        total = self.pages[0].total
        page_count = (total + EIA_RETAIL_PAGE_LENGTH - 1) // EIA_RETAIL_PAGE_LENGTH
        expected_offsets = tuple(index * EIA_RETAIL_PAGE_LENGTH for index in range(page_count))
        actual_offsets = tuple(page.request.offset for page in self.pages)
        if actual_offsets != expected_offsets or any(page.total != total for page in self.pages):
            raise ValidationError("EIA retail pagination is incomplete or has a gap")
        if sum(page.raw_row_count for page in self.pages) != total:
            raise ValidationError("EIA retail pagination total does not match page rows")
        expected_rows = tuple(
            sorted(
                (row for page in self.pages for row in page.rows),
                key=lambda row: (row.period, row.metric),
            )
        )
        if self.rows != expected_rows:
            raise ValidationError("EIA retail cohort rows do not match pages")
        if not self.rows or len(self.rows) > total * len(EIA_RETAIL_METRICS):
            raise ValidationError("EIA retail cohort normalized total is invalid")
        previous: tuple[str, str] | None = None
        units: dict[str, str] = {}
        metric_counts = {metric: 0 for metric in EIA_RETAIL_METRICS}
        for row in self.rows:
            key = (row.period, row.metric)
            if previous is not None and key <= previous:
                raise ValidationError("EIA retail cohort contains duplicate observations")
            previous = key
            if row.metric in units and units[row.metric] != row.unit:
                raise ValidationError("EIA retail units are inconsistent across pages")
            units[row.metric] = row.unit
            metric_counts[row.metric] += 1
        if (
            any(
                metric_counts[metric] != total
                for metric in ("sales", "revenue", "price")
            )
            or metric_counts["customers"] > total
        ):
            raise ValidationError("EIA retail cohort metric coverage is invalid")
        _validate_digest(self.semantic_sha256, "retail cohort semantic digest")
        if self.semantic_sha256 != _sha256_json(_retail_cohort_semantic_material(self.pages, self.rows)):
            raise ValidationError("EIA retail cohort semantic digest is invalid")

    @property
    def request_scope(self) -> dict[str, object]:
        return _retail_cohort_scope(self.pages)


def assemble_eia_retail_capture(
    pages: tuple[EiaRetailPageCapture, ...] | list[EiaRetailPageCapture],
) -> EiaRetailCapture:
    """Assemble pages only when their totals prove complete gap-free pagination."""

    if not isinstance(pages, (tuple, list)):
        raise ValidationError("EIA retail pages are invalid")
    provided = tuple(pages)
    if not provided or not all(isinstance(page, EiaRetailPageCapture) for page in provided):
        raise ValidationError("EIA retail pages are invalid")
    ordered_pages = tuple(sorted(provided, key=lambda page: page.request.offset))
    rows = tuple(
        sorted(
            (row for page in ordered_pages for row in page.rows),
            key=lambda row: (row.period, row.metric),
        )
    )
    return EiaRetailCapture(
        pages=ordered_pages,
        rows=rows,
        semantic_sha256=_sha256_json(_retail_cohort_semantic_material(ordered_pages, rows)),
    )


@dataclass(frozen=True, slots=True)
class EiaWeeklyObservation:
    """One normalized weekly petroleum observation with per-row content identity."""

    period: str
    value: Decimal
    unit: str
    source_row: int
    content_sha256: str

    def __post_init__(self) -> None:
        _validate_date(self.period)
        _decimal(self.value, "weekly value")
        _validate_text(self.unit, "weekly unit")
        if isinstance(self.source_row, bool) or not isinstance(self.source_row, int) or self.source_row < 1:
            raise ValidationError("EIA weekly source row is invalid")
        _validate_digest(self.content_sha256, "weekly content digest")
        if self.content_sha256 != _sha256_json(self.semantic_mapping()):
            raise ValidationError("EIA weekly content digest is invalid")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "series_id": EIA_WEEKLY_SERIES_ID,
            "period": self.period,
            "value": _decimal_text(self.value),
            "unit": self.unit,
        }

    @property
    def canonical_series_id(self) -> str:
        return EIA_WEEKLY_CANONICAL_SERIES_ID


def _weekly_semantic_material(
    prepared: PreparedEiaWeeklyCapture,
    rows: tuple[EiaWeeklyObservation, ...],
) -> dict[str, object]:
    return {
        "provider": "eia",
        "request_scope": prepared.request_scope(),
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class EiaWeeklyCapture:
    """A complete canonical capture of the reviewed weekly series."""

    request: PreparedEiaWeeklyCapture = field(repr=False)
    raw_bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    rows: tuple[EiaWeeklyObservation, ...] = field(repr=False)
    semantic_sha256: str = ""
    earliest_period: str = ""
    latest_period: str = ""

    def __post_init__(self) -> None:
        _require_exact_weekly_capture(self.request)
        _validate_digest(self.raw_bytes_sha256, "weekly raw response digest")
        _require_bytes(
            self.raw_bytes,
            maximum=EIA_WEEKLY_MAX_RESPONSE_BYTES,
            kind="weekly",
        )
        if self.raw_bytes_sha256 != _sha256(self.raw_bytes):
            raise ValidationError("EIA weekly raw response digest is invalid")
        if not isinstance(self.rows, tuple) or not self.rows or len(self.rows) > EIA_WEEKLY_MAX_ROWS:
            raise ValidationError("EIA weekly row count is invalid")
        prior: str | None = None
        unit: str | None = None
        for row in self.rows:
            if not isinstance(row, EiaWeeklyObservation):
                raise ValidationError("EIA weekly normalized rows are invalid")
            if prior is not None and row.period <= prior:
                raise ValidationError("EIA weekly observations must be strictly ordered")
            prior = row.period
            if unit is not None and row.unit != unit:
                raise ValidationError("EIA weekly units are inconsistent")
            unit = row.unit
        _validate_date(self.earliest_period, "earliest period")
        _validate_date(self.latest_period, "latest period")
        if self.earliest_period != self.rows[0].period or self.latest_period != self.rows[-1].period:
            raise ValidationError("EIA weekly coverage is invalid")
        _validate_digest(self.semantic_sha256, "weekly semantic digest")
        if self.semantic_sha256 != _sha256_json(_weekly_semantic_material(self.request, self.rows)):
            raise ValidationError("EIA weekly semantic digest is invalid")

    @property
    def raw_bytes_size(self) -> int:
        return len(self.raw_bytes)

    @property
    def request_scope(self) -> dict[str, object]:
        return self.request.request_scope()


def parse_eia_weekly_response(
    *,
    body: bytes,
    prepared: PreparedEiaWeeklyCapture | None = None,
    credential_value: str | None = None,
) -> EiaWeeklyCapture:
    """Parse the full fixed-series response and reject a truncated total."""

    request = _require_exact_weekly_capture(
        prepare_eia_weekly_capture() if prepared is None else prepared
    )
    if credential_value is not None:
        _validate_api_key(credential_value)
    value, retained_bytes = _load_sanitized_response(
        body,
        maximum=EIA_WEEKLY_MAX_RESPONSE_BYTES,
        kind="weekly",
        credential_value=credential_value,
    )
    response = _response_payload(value, kind="weekly")
    if response.get("frequency") != "weekly":
        raise ValidationError("EIA weekly response frequency is invalid")
    if response.get("dateFormat") != "YYYY-MM-DD":
        raise ValidationError("EIA weekly response date format is invalid")
    total = _integer(response.get("total"), "weekly total", maximum=EIA_WEEKLY_MAX_ROWS)
    data = response.get("data")
    if not isinstance(data, list) or not data or len(data) != total:
        raise ValidationError("EIA weekly response does not prove complete history")
    rows: list[EiaWeeklyObservation] = []
    seen_periods: set[str] = set()
    for source_row, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise ValidationError("EIA weekly response data row is invalid")
        raw_fields = frozenset(raw)
        if raw_fields == _WEEKLY_PATH_BOUND_ROW_FIELDS:
            # The documented endpoint shape has no provider identity field:
            # ``series-description`` is display metadata only and may not
            # influence the canonical series binding.
            _validate_text(raw["series-description"], "weekly series description")
        elif raw_fields == _WEEKLY_PROVIDER_ROW_FIELDS:
            if raw["series"] != _EIA_WEEKLY_PROVIDER_SERIES_ID:
                raise ValidationError("EIA weekly response contains an unexpected series")
            if isinstance(raw["value"], bool) or not isinstance(
                raw["value"], (int, Decimal)
            ):
                raise ValidationError("EIA weekly provider value must be numeric")
            for field_name in _WEEKLY_PROVIDER_METADATA_FIELDS:
                _validate_text(raw[field_name], f"weekly {field_name}")
        elif raw_fields == _WEEKLY_LEGACY_ROW_FIELDS:
            # Existing offline evidence predates the provider-shaped fixture.
            # Its optional compatibility is safe only when it redundantly
            # states the identity already fixed by the prepared request.
            if raw["series"] != request.series_id:
                raise ValidationError("EIA weekly response contains an unexpected series")
        else:
            raise ValidationError("EIA weekly response data row has an unsupported shape")
        period = _validate_date(raw["period"])
        if period in seen_periods:
            raise ValidationError("EIA weekly response contains duplicate periods")
        seen_periods.add(period)
        semantic_row = {
            "series_id": request.series_id,
            "period": period,
            "value": _decimal_text(_decimal(raw["value"], "weekly value")),
            "unit": _validate_text(raw["units"], "weekly unit"),
        }
        rows.append(
            EiaWeeklyObservation(
                period=period,
                value=_decimal(raw["value"], "weekly value"),
                unit=semantic_row["unit"],
                source_row=source_row,
                content_sha256=_sha256_json(semantic_row),
            )
        )
    ordered_rows = tuple(sorted(rows, key=lambda row: row.period))
    semantic_sha256 = _sha256_json(_weekly_semantic_material(request, ordered_rows))
    return EiaWeeklyCapture(
        request=request,
        raw_bytes_sha256=_sha256(retained_bytes),
        raw_bytes=retained_bytes,
        rows=ordered_rows,
        semantic_sha256=semantic_sha256,
        earliest_period=ordered_rows[0].period,
        latest_period=ordered_rows[-1].period,
    )


def _require_success_response(
    response: object,
    *,
    maximum: int,
    kind: str,
) -> CapturedEiaResponse:
    if not isinstance(response, CapturedEiaResponse):
        raise ValidationError("EIA transport returned an invalid response type")
    # Injected transports need the same pre-body transient classification.
    raise_for_transient_http_status(response.status)
    _require_bytes(response.body, maximum=maximum, kind=kind)
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    if response.status != 200 or content_type != "application/json":
        raise StoreUnavailableError("EIA provider response was unavailable")
    return response


def _require_transport(value: object) -> EiaTransport:
    if not isinstance(value, EiaTransport):
        raise ValidationError("EIA transport does not implement the reviewed interface")
    return value


def capture_eia_retail_page(
    prepared: PreparedEiaRetailPage,
    *,
    api_key: str,
    transport: EiaTransport,
) -> EiaRetailPageCapture:
    """Perform one injected retail page request and normalize it before writes."""

    if not isinstance(prepared, PreparedEiaRetailPage):
        raise ValidationError("EIA retail capture requires a prepared page")
    key = _validate_api_key(api_key)
    client = _require_transport(transport)
    try:
        response = client.get(
            path=prepared.endpoint_path,
            query={**prepared.query, "api_key": key},
            headers={"Accept": "application/json"},
            timeout_seconds=EIA_TIMEOUT_SECONDS,
            max_bytes=EIA_RETAIL_MAX_RESPONSE_BYTES,
        )
    except (OSError, http.client.HTTPException) as exc:
        raise transient_error_from_transport_exception(exc) from None
    response = _require_success_response(
        response,
        maximum=EIA_RETAIL_MAX_RESPONSE_BYTES,
        kind="retail",
    )
    return parse_eia_retail_page_response(
        body=response.body,
        prepared=prepared,
        credential_value=key,
    )


def capture_eia_weekly(
    prepared: PreparedEiaWeeklyCapture,
    *,
    api_key: str,
    transport: EiaTransport,
) -> EiaWeeklyCapture:
    """Perform the one injected weekly-series request before any writes."""

    prepared = _require_exact_weekly_capture(prepared)
    key = _validate_api_key(api_key)
    client = _require_transport(transport)
    try:
        response = client.get(
            path=prepared.endpoint_path,
            query={"api_key": key},
            headers={"Accept": "application/json"},
            timeout_seconds=EIA_TIMEOUT_SECONDS,
            max_bytes=EIA_WEEKLY_MAX_RESPONSE_BYTES,
        )
    except (OSError, http.client.HTTPException) as exc:
        raise transient_error_from_transport_exception(exc) from None
    response = _require_success_response(
        response,
        maximum=EIA_WEEKLY_MAX_RESPONSE_BYTES,
        kind="weekly",
    )
    return parse_eia_weekly_response(
        body=response.body,
        prepared=prepared,
        credential_value=key,
    )
