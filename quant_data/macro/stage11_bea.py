"""Bounded BEA NIPA capture and normalization primitives for Stage 11.

This module deliberately contains no store, registry, scheduler, or
publication code.  It makes the one reviewed BEA request possible through an
injected transport, validates the provider response before any writer could
see it, and returns immutable credential-free values.  The retained response
is canonicalized *after* recursively removing provider echoes of ``UserID``
or API-key fields, so a credential cannot become evidence merely because a
provider reflected it in JSON.

The two requests ask BEA for the reviewed NIPA tables.  Normalization retains
only A191RL from T10101 and A191RC from T10105, then can assemble the two
captures into one cohort.  Earliest returned observations remain source
evidence rather than an asserted historical vintage.
"""

from __future__ import annotations

import hashlib
import http.client
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from .stage11_retry import (
    raise_for_transient_http_status,
    transient_error_from_transport_exception,
)


BEA_HOST = "apps.bea.gov"
BEA_NIPA_PATH = "/api/data/"
BEA_NIPA_METHOD = "GetData"
BEA_NIPA_DATASET = "NIPA"
BEA_NIPA_T10101 = "T10101"
BEA_NIPA_T10105 = "T10105"
BEA_NIPA_FREQUENCY = "Q"
BEA_NIPA_YEAR = "ALL"
BEA_NIPA_RESULT_FORMAT = "JSON"
BEA_NIPA_TABLE_SERIES = (
    (BEA_NIPA_T10101, "A191RL"),
    (BEA_NIPA_T10105, "A191RC"),
)
BEA_NIPA_SERIES_CODES = tuple(series_code for _, series_code in BEA_NIPA_TABLE_SERIES)
_BEA_NIPA_SERIES_BY_TABLE = dict(BEA_NIPA_TABLE_SERIES)
_BEA_NIPA_TABLE_BY_SERIES = {series_code: table_name for table_name, series_code in BEA_NIPA_TABLE_SERIES}
BEA_NIPA_CANONICAL_SERIES_BY_CODE = {
    "A191RL": "macro.gdp.real_qoq_saar_pct",
    "A191RC": "macro.gdp.nominal_billions",
}
BEA_NIPA_TIMEOUT_SECONDS = 45
# These public bounds mirror ``config/stage11_macro_scope.json`` exactly.
BEA_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
BEA_MAX_REQUESTS = 2
BEA_MAX_ROWS_PER_SERIES = 1_000
BEA_NIPA_MAX_RESPONSE_BYTES = BEA_MAX_RESPONSE_BYTES
BEA_NIPA_MAX_ROWS_PER_SERIES = BEA_MAX_ROWS_PER_SERIES
BEA_NIPA_MAX_ROWS = BEA_NIPA_MAX_ROWS_PER_SERIES
# A valid Stage 11 NIPA row is substantially larger than this, but retain a
# deliberately conservative floor so malformed/unselected provider rows cannot
# turn the reviewed eight-megabyte body bound into an unbounded parse loop.
_BEA_NIPA_MIN_PROVIDER_ROW_BYTES = 64
BEA_NIPA_MAX_TABLE_ROWS = (
    BEA_NIPA_MAX_RESPONSE_BYTES // _BEA_NIPA_MIN_PROVIDER_ROW_BYTES
)
BEA_AVAILABILITY_BASIS = "local_capture"

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _validate_api_key(value: object) -> str:
    if not isinstance(value, str) or _API_KEY.fullmatch(value) is None:
        raise ValidationError("BEA credential is missing or invalid")
    return value


def _validate_text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"BEA {field_name} must be nonempty text")
    return value


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"BEA {field_name} is invalid")
    return value


def _validate_quarter(value: object, field_name: str = "TimePeriod") -> str:
    if not isinstance(value, str) or _QUARTER.fullmatch(value) is None:
        raise ValidationError(f"BEA {field_name} must be YYYYQ1 through YYYYQ4")
    year = int(value[:4])
    if year < 1800 or year > 9999:
        raise ValidationError(f"BEA {field_name} must be YYYYQ1 through YYYYQ4")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError(f"BEA {field_name} must be a finite number")
    text = value.replace(",", "") if isinstance(value, str) else value
    if isinstance(text, str) and (not text or text.strip() != text):
        raise ValidationError(f"BEA {field_name} must be a finite number")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"BEA {field_name} must be a finite number") from exc
    if not parsed.is_finite():
        raise ValidationError(f"BEA {field_name} must be finite")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("BEA numeric value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _unit_mult(value: object) -> str:
    parsed = _decimal(value, "UNIT_MULT")
    if parsed != parsed.to_integral_value():
        raise ValidationError("BEA UNIT_MULT must be an integer")
    result = int(parsed)
    if result < -100 or result > 100:
        raise ValidationError("BEA UNIT_MULT is outside the supported range")
    return str(result)


def _require_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ValidationError("BEA response body must be bytes")
    if not value:
        raise ValidationError("BEA response body is empty")
    if len(value) > BEA_NIPA_MAX_RESPONSE_BYTES:
        raise ResourceLimitError("BEA response exceeds the reviewed byte bound")
    return value


def _mapping_of_strings(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"BEA transport {field_name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValidationError(f"BEA transport {field_name} must contain strings")
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
    """Drop unsafe response material before it can become retained evidence.

    Any credential/query/header/URL/URI-shaped field is removed wholesale.
    The known credential is additionally recognized in arbitrary string values
    during capture.  This is intentionally stricter than redaction: retained
    bytes must not contain a redacted URL or header name either.
    """

    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("BEA response object keys must be strings")
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
    body: object, *, credential_value: str | None
) -> tuple[dict[str, Any], bytes]:
    raw = _require_bytes(body)
    parsed = loads_strict(raw, max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES)
    if not isinstance(parsed, dict):
        raise ValidationError("BEA response must be a JSON object")
    sanitized = _sanitize_response(parsed, credential_value=credential_value)
    if not isinstance(sanitized, dict):  # defensive, the sanitizer preserves mappings
        raise ValidationError("BEA response must be a JSON object")
    retained = dumps_strict(sanitized, max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES).encode("utf-8")
    return sanitized, retained


@dataclass(frozen=True, slots=True)
class CapturedBeaResponse:
    """One bounded raw HTTP response.  Response bytes are never repr'd."""

    status: int
    content_type: str = field(repr=False)
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("BEA response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("BEA response content type is invalid")
        _require_bytes(self.body)


@runtime_checkable
class BeaTransport(Protocol):
    """Injectable transport for the exact reviewed BEA request."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedBeaResponse: ...


@dataclass(frozen=True, slots=True)
class PreparedBeaNipaCapture:
    """Credential-free immutable request for the approved BEA NIPA table."""

    endpoint_path: str = field(default=BEA_NIPA_PATH, repr=False)
    method: str = BEA_NIPA_METHOD
    dataset: str = BEA_NIPA_DATASET
    table_name: str = BEA_NIPA_T10101
    series_code: str = "A191RL"
    frequency: str = BEA_NIPA_FREQUENCY
    year: str = BEA_NIPA_YEAR
    result_format: str = BEA_NIPA_RESULT_FORMAT

    def __post_init__(self) -> None:
        if (
            self.endpoint_path != BEA_NIPA_PATH
            or self.method != BEA_NIPA_METHOD
            or self.dataset != BEA_NIPA_DATASET
            or self.frequency != BEA_NIPA_FREQUENCY
            or self.year != BEA_NIPA_YEAR
            or self.result_format != BEA_NIPA_RESULT_FORMAT
            or _BEA_NIPA_SERIES_BY_TABLE.get(self.table_name) != self.series_code
        ):
            raise ValidationError("BEA request is outside the approved Stage 11 scope")

    @property
    def query(self) -> dict[str, str]:
        return {
            "method": self.method,
            "datasetname": self.dataset,
            "TableName": self.table_name,
            "Frequency": self.frequency,
            "Year": self.year,
            "ResultFormat": self.result_format,
        }

    def request_scope(self) -> dict[str, object]:
        return {
            "provider": "bea",
            "endpoint_path": self.endpoint_path,
            "method": self.method,
            "dataset": self.dataset,
            "table_name": self.table_name,
            "frequency": self.frequency,
            "year": self.year,
            "result_format": self.result_format,
            "retained_series_codes": [self.series_code],
        }


class StdlibBeaTransport:
    """TLS-verified standard-library transport that never follows redirects."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedBeaResponse:
        query_values = _mapping_of_strings(query, "query")
        header_values = _mapping_of_strings(headers, "headers")
        table_name = query_values.get("TableName")
        series_code = _BEA_NIPA_SERIES_BY_TABLE.get(table_name)
        if series_code is None:
            raise ValidationError("BEA transport request is outside the approved scope")
        expected = PreparedBeaNipaCapture(
            table_name=table_name,
            series_code=series_code,
        ).query
        if (
            path != BEA_NIPA_PATH
            or set(query_values) != {*expected, "UserID"}
            or any(query_values[key] != value for key, value in expected.items())
            or _validate_api_key(query_values.get("UserID")) != query_values["UserID"]
            or header_values != {"Accept": "application/json"}
            or timeout_seconds != BEA_NIPA_TIMEOUT_SECONDS
            or max_bytes != BEA_NIPA_MAX_RESPONSE_BYTES
        ):
            raise ValidationError("BEA transport request is outside the approved scope")
        target = f"{BEA_NIPA_PATH}?{urlencode(query_values)}"
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(BEA_HOST, timeout=BEA_NIPA_TIMEOUT_SECONDS)
            connection.request("GET", target, headers=header_values)
            response = connection.getresponse()
            # A retryable status is classified before any response body or
            # response metadata can be retained or validated.
            raise_for_transient_http_status(response.status)
            declared_size = response.getheader("Content-Length")
            if declared_size is not None:
                if not isinstance(declared_size, str) or not declared_size.isdecimal():
                    raise StoreUnavailableError("BEA provider response was unavailable")
                if int(declared_size) > max_bytes:
                    raise ResourceLimitError("BEA response exceeds the reviewed byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("BEA response exceeds the reviewed byte bound")
            return CapturedBeaResponse(
                status=response.status,
                content_type=response.getheader("Content-Type") or "application/octet-stream",
                body=body,
            )
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
class BeaNipaObservation:
    """One normalized A191RL/A191RC quarterly observation."""

    table_name: str
    series_code: str
    period: str
    value: Decimal
    cl_unit: str
    unit_mult: str
    source_row: int

    def __post_init__(self) -> None:
        if _BEA_NIPA_TABLE_BY_SERIES.get(self.series_code) != self.table_name:
            raise ValidationError("BEA series code is outside the approved scope")
        _validate_quarter(self.period)
        _decimal(self.value, "DataValue")
        _validate_text(self.cl_unit, "CL_UNIT")
        _unit_mult(self.unit_mult)
        if isinstance(self.source_row, bool) or not isinstance(self.source_row, int) or self.source_row < 1:
            raise ValidationError("BEA source row is invalid")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "table_name": self.table_name,
            "series_code": self.series_code,
            "period": self.period,
            "value": _decimal_text(self.value),
            "cl_unit": self.cl_unit,
            "unit_mult": self.unit_mult,
        }

    @property
    def canonical_series_id(self) -> str:
        return BEA_NIPA_CANONICAL_SERIES_BY_CODE[self.series_code]


def _bea_semantic_material(
    prepared: PreparedBeaNipaCapture,
    rows: tuple[BeaNipaObservation, ...],
) -> dict[str, object]:
    return {
        "provider": "bea",
        "request_scope": prepared.request_scope(),
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class BeaNipaCapture:
    """Credential-free parsed BEA capture; retained bytes are canonical/redacted."""

    request: PreparedBeaNipaCapture = field(repr=False)
    raw_bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    rows: tuple[BeaNipaObservation, ...] = field(repr=False)
    semantic_sha256: str = ""
    utc_production_time: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, PreparedBeaNipaCapture):
            raise ValidationError("BEA capture request is invalid")
        _validate_digest(self.raw_bytes_sha256, "raw response digest")
        _require_bytes(self.raw_bytes)
        if self.raw_bytes_sha256 != _sha256(self.raw_bytes):
            raise ValidationError("BEA raw response digest is invalid")
        if not isinstance(self.rows, tuple) or not self.rows or len(self.rows) > BEA_NIPA_MAX_ROWS:
            raise ValidationError("BEA row count is invalid")
        previous: tuple[str, str, str] | None = None
        seen_series: set[str] = set()
        units: dict[str, tuple[str, str]] = {}
        for row in self.rows:
            if not isinstance(row, BeaNipaObservation):
                raise ValidationError("BEA normalized rows are invalid")
            key = (row.table_name, row.series_code, row.period)
            if previous is not None and key <= previous:
                raise ValidationError("BEA normalized rows must be strictly ordered")
            previous = key
            seen_series.add(row.series_code)
            unit_pair = (row.cl_unit, row.unit_mult)
            if row.series_code in units and units[row.series_code] != unit_pair:
                raise ValidationError("BEA units are inconsistent within a series")
            units[row.series_code] = unit_pair
        if seen_series != {self.request.series_code}:
            raise ValidationError("BEA response lacks the requested NIPA series")
        _validate_digest(self.semantic_sha256, "semantic digest")
        if self.semantic_sha256 != _sha256_json(_bea_semantic_material(self.request, self.rows)):
            raise ValidationError("BEA semantic digest is invalid")
        if self.utc_production_time is not None:
            _validate_text(self.utc_production_time, "UTCProductionTime")

    @property
    def raw_bytes_size(self) -> int:
        return len(self.raw_bytes)

    @property
    def earliest_period(self) -> str:
        return self.rows[0].period

    @property
    def latest_period(self) -> str:
        return self.rows[-1].period

    @property
    def request_scope(self) -> dict[str, object]:
        return self.request.request_scope()

    @property
    def availability_basis(self) -> str:
        """Availability is local capture time; no historical vintage is claimed."""

        return BEA_AVAILABILITY_BASIS


def prepare_bea_nipa_capture(table_name: str) -> PreparedBeaNipaCapture:
    """Create one credential-free reviewed BEA request by exact table name."""

    if not isinstance(table_name, str) or table_name not in _BEA_NIPA_SERIES_BY_TABLE:
        raise ValidationError("BEA table is outside the approved Stage 11 scope")
    return PreparedBeaNipaCapture(
        table_name=table_name,
        series_code=_BEA_NIPA_SERIES_BY_TABLE[table_name],
    )


def prepare_bea_nipa_captures() -> tuple[PreparedBeaNipaCapture, ...]:
    """Return the complete immutable two-request BEA cohort scope."""

    return tuple(prepare_bea_nipa_capture(table_name) for table_name, _ in BEA_NIPA_TABLE_SERIES)


def _bea_cohort_semantic_material(
    captures: tuple[BeaNipaCapture, ...],
    rows: tuple[BeaNipaObservation, ...],
) -> dict[str, object]:
    return {
        "provider": "bea",
        "request_scopes": [capture.request_scope for capture in captures],
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class BeaNipaHistoryCohort:
    """The complete two-table BEA history candidate before any persistence.

    The cohort proves that both reviewed requests succeeded and that their
    normalized observations are disjoint.  It deliberately does not invent a
    provider vintage or local capture time.
    """

    captures: tuple[BeaNipaCapture, ...] = field(repr=False)
    rows: tuple[BeaNipaObservation, ...] = field(repr=False)
    semantic_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.captures, tuple) or len(self.captures) != len(BEA_NIPA_TABLE_SERIES):
            raise ValidationError("BEA cohort must contain both reviewed captures")
        expected_pairs = tuple(BEA_NIPA_TABLE_SERIES)
        actual_pairs = tuple(
            (capture.request.table_name, capture.request.series_code)
            for capture in self.captures
            if isinstance(capture, BeaNipaCapture)
        )
        if actual_pairs != expected_pairs:
            raise ValidationError("BEA cohort capture scope is incomplete or unordered")
        if not isinstance(self.rows, tuple) or not self.rows:
            raise ValidationError("BEA cohort rows are invalid")
        expected_rows = tuple(
            sorted(
                (row for capture in self.captures for row in capture.rows),
                key=lambda row: (row.table_name, row.series_code, row.period),
            )
        )
        if self.rows != expected_rows:
            raise ValidationError("BEA cohort rows do not match the source captures")
        _validate_digest(self.semantic_sha256, "cohort semantic digest")
        if self.semantic_sha256 != _sha256_json(
            _bea_cohort_semantic_material(self.captures, self.rows)
        ):
            raise ValidationError("BEA cohort semantic digest is invalid")

    @property
    def request_scopes(self) -> tuple[dict[str, object], ...]:
        return tuple(capture.request_scope for capture in self.captures)

    @property
    def availability_basis(self) -> str:
        """The publication layer must supply its own local capture instant."""

        return BEA_AVAILABILITY_BASIS


def assemble_bea_nipa_history(
    captures: tuple[BeaNipaCapture, ...] | list[BeaNipaCapture],
) -> BeaNipaHistoryCohort:
    """Assemble exactly T10101/A191RL and T10105/A191RC into one cohort."""

    if not isinstance(captures, (tuple, list)):
        raise ValidationError("BEA cohort captures are invalid")
    provided = tuple(captures)
    if not all(isinstance(capture, BeaNipaCapture) for capture in provided):
        raise ValidationError("BEA cohort captures are invalid")
    by_pair = {
        (capture.request.table_name, capture.request.series_code): capture
        for capture in provided
    }
    if len(by_pair) != len(provided) or set(by_pair) != set(BEA_NIPA_TABLE_SERIES):
        raise ValidationError("BEA cohort must contain each reviewed request exactly once")
    ordered_captures = tuple(by_pair[pair] for pair in BEA_NIPA_TABLE_SERIES)
    rows = tuple(
        sorted(
            (row for capture in ordered_captures for row in capture.rows),
            key=lambda row: (row.table_name, row.series_code, row.period),
        )
    )
    return BeaNipaHistoryCohort(
        captures=ordered_captures,
        rows=rows,
        semantic_sha256=_sha256_json(_bea_cohort_semantic_material(ordered_captures, rows)),
    )


def parse_bea_nipa_response(
    *,
    body: bytes,
    prepared: PreparedBeaNipaCapture | None = None,
    credential_value: str | None = None,
) -> BeaNipaCapture:
    """Validate and normalize one reviewed BEA ``GetData/NIPA`` response.

    ``UTCProductionTime`` is retained as non-semantic evidence when present;
    it is intentionally absent from the semantic digest.  No local capture
    clock is created by this primitive.
    """

    request = prepare_bea_nipa_capture(BEA_NIPA_T10101) if prepared is None else prepared
    if not isinstance(request, PreparedBeaNipaCapture):
        raise ValidationError("BEA capture requires a prepared request")
    if credential_value is not None:
        _validate_api_key(credential_value)
    value, retained_bytes = _load_sanitized_response(
        body, credential_value=credential_value
    )
    if set(value) != {"BEAAPI"} or not isinstance(value["BEAAPI"], dict):
        raise ValidationError("BEA response has an unsupported shape")
    envelope = value["BEAAPI"]
    if "Results" not in envelope or not isinstance(envelope["Results"], dict):
        raise ValidationError("BEA response lacks results")
    results = envelope["Results"]
    if "Error" in results or "error" in results:
        raise StoreUnavailableError("BEA provider response was unavailable")
    data = results.get("Data")
    if (
        not isinstance(data, list)
        or not data
        or len(data) > BEA_NIPA_MAX_TABLE_ROWS
    ):
        raise ValidationError("BEA response data rows are invalid")
    rows: list[BeaNipaObservation] = []
    seen: set[tuple[str, str]] = set()
    for source_row, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise ValidationError("BEA response data row is invalid")
        series_code = raw.get("SeriesCode")
        if series_code != request.series_code:
            continue
        if len(rows) >= BEA_NIPA_MAX_ROWS_PER_SERIES:
            raise ValidationError("BEA response has too many selected NIPA observations")
        required = {
            "TableName",
            "SeriesCode",
            "TimePeriod",
            "DataValue",
            "CL_UNIT",
            "UNIT_MULT",
        }
        if not required.issubset(raw):
            raise ValidationError("BEA response data row lacks required fields")
        if raw["TableName"] != request.table_name:
            raise ValidationError("BEA response contains a table/series mismatch")
        period = _validate_quarter(raw["TimePeriod"])
        key = (series_code, period)
        if key in seen:
            raise ValidationError("BEA response contains duplicate NIPA observations")
        seen.add(key)
        rows.append(
            BeaNipaObservation(
                table_name=request.table_name,
                series_code=series_code,
                period=period,
                value=_decimal(raw["DataValue"], "DataValue"),
                cl_unit=_validate_text(raw["CL_UNIT"], "CL_UNIT"),
                unit_mult=_unit_mult(raw["UNIT_MULT"]),
                source_row=source_row,
            )
        )
    ordered_rows = tuple(
        sorted(rows, key=lambda row: (row.table_name, row.series_code, row.period))
    )
    if not ordered_rows:
        raise ValidationError("BEA response lacks approved NIPA observations")
    utc_production_time = results.get("UTCProductionTime")
    if utc_production_time is not None:
        _validate_text(utc_production_time, "UTCProductionTime")
    semantic_sha256 = _sha256_json(_bea_semantic_material(request, ordered_rows))
    return BeaNipaCapture(
        request=request,
        raw_bytes_sha256=_sha256(retained_bytes),
        raw_bytes=retained_bytes,
        rows=ordered_rows,
        semantic_sha256=semantic_sha256,
        utc_production_time=utc_production_time,
    )


def _require_success_response(response: object) -> CapturedBeaResponse:
    if not isinstance(response, CapturedBeaResponse):
        raise ValidationError("BEA transport returned an invalid response type")
    # Injected transports need the same pre-body transient classification.
    raise_for_transient_http_status(response.status)
    _require_bytes(response.body)
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    if response.status != 200 or content_type != "application/json":
        raise StoreUnavailableError("BEA provider response was unavailable")
    return response


def _require_transport(value: object) -> BeaTransport:
    if not isinstance(value, BeaTransport):
        raise ValidationError("BEA transport does not implement the reviewed interface")
    return value


def capture_bea_nipa(
    prepared: PreparedBeaNipaCapture,
    *,
    api_key: str,
    transport: BeaTransport,
) -> BeaNipaCapture:
    """Perform one injected BEA request, then normalize it before persistence."""

    if not isinstance(prepared, PreparedBeaNipaCapture):
        raise ValidationError("BEA capture requires a prepared request")
    key = _validate_api_key(api_key)
    client = _require_transport(transport)
    try:
        response = client.get(
            path=prepared.endpoint_path,
            query={**prepared.query, "UserID": key},
            headers={"Accept": "application/json"},
            timeout_seconds=BEA_NIPA_TIMEOUT_SECONDS,
            max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES,
        )
    except (OSError, http.client.HTTPException) as exc:
        raise transient_error_from_transport_exception(exc) from None
    response = _require_success_response(response)
    return parse_bea_nipa_response(
        body=response.body,
        prepared=prepared,
        credential_value=key,
    )
