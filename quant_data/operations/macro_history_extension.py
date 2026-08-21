"""Fixed-source runner for the approved one-time macro-history extension.

The operation layer deliberately knows no SQLite or lock primitive.  It obtains
one bounded response, parses it into an opaque domain capture, and only then
hands that capture to the publisher.  The publisher owns canonical writes and
semantic replay; this module has no retry or scheduler path.

The injected runner is usable in fixture tests without importing the write-side
macro domain.  Canonical parser/publisher imports remain lazy, so a fixture
cannot accidentally configure a canonical writer.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import os
from pathlib import Path
import re
import stat
import sys
from typing import Final, Literal, Protocol
from urllib.parse import urlsplit

from ..errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..migrations import migrate_and_register_store
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, StoreRole


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MACRO_STORE: Final = PROJECT_ROOT / "data" / "macro.sqlite"

CPI_CURRENT_HISTORY_MODE: Final = "cpi-current-history"
GDP_CPI_VINTAGES_MODE: Final = "gdp-cpi-vintages"
BACKFILL_MODE: Final = "backfill"
_VALID_MODES: Final = frozenset(
    {CPI_CURRENT_HISTORY_MODE, GDP_CPI_VINTAGES_MODE, BACKFILL_MODE}
)

_TIMEOUT_SECONDS: Final = 60
_USER_AGENT: Final = "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)"
_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_PARSER_KEY: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_JSON_MEDIA_TYPE: Final = "application/json"
_XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_BLS_API_BYTES: Final = 2 * 1024 * 1024
_MAX_RTDSM_BYTES: Final = 8 * 1024 * 1024
_VERSION: Final = "1.0.0"

BLS_CPI_API_URL: Final = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
PHILADELPHIA_FED_NOMINAL_OUTPUT_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/NOUTPUTQvQd.xlsx"
)
PHILADELPHIA_FED_REAL_OUTPUT_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx"
)
PHILADELPHIA_FED_CPI_ALL_ITEMS_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/pcpiMvMd.xlsx"
)
PHILADELPHIA_FED_CPI_CORE_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/pcpixMvMd.xlsx"
)

_BLS_ALL_ITEMS_CODE: Final = "CUSR0000SA0"
_BLS_CORE_CODE: Final = "CUSR0000SA0L1E"
_PRESEEDED_BLS_CPI_1947_1956_PATH: Final = (
    PROJECT_ROOT
    / "data"
    / ".macro-history-extension-v1"
    / "responses"
    / "001-bls-cpi-1947-1956.json"
)
_PRESEEDED_BLS_CPI_1947_1956_SHA256: Final = (
    "6b73bc126ed7e96e6b267c289f5c5f4041c76df7bb37011967863070eef78a80"
)
_PRESEEDED_BLS_CPI_1947_1956_BYTES: Final = 10_463
_PRESEEDED_BLS_CPI_1947_1956_CAPTURED_AT: Final = "2026-08-17T16:20:53.745543Z"
_PRESEEDED_RESPONSE_DIRECTORY: Final = (
    PROJECT_ROOT / "data" / ".macro-history-extension-v1" / "responses"
)


@dataclass(frozen=True, slots=True)
class MacroHistorySource:
    """One review-bound, credential-free source request.

    ``parser_key`` selects an injected domain parser.  It is not supplied by a
    caller at run time, which keeps the source/parser binding fixed once a
    canonical source table is installed.
    """

    identifier: str
    mode: Literal["cpi-current-history", "gdp-cpi-vintages"]
    method: Literal["GET", "POST"]
    source_resource: str
    media_type: str
    maximum_bytes: int
    parser_key: str
    request_body: bytes | None = None
    request_content_type: str | None = None
    start_year: int | None = None
    end_year: int | None = None


def _bls_cpi_request_body(
    *, start_year: int, end_year: int, series_codes: tuple[str, ...]
) -> bytes:
    return dumps_strict(
        {
            "endyear": str(end_year),
            "seriesid": list(series_codes),
            "startyear": str(start_year),
        }
    ).encode("utf-8")


# BLS's unregistered v2 API permits at most ten inclusive years per request.
# The first window deliberately requests only all-items CPI because the reviewed
# core series begins in 1957.  The sequence is fixed and non-overlapping.
MACRO_HISTORY_SOURCES: Final = (
    MacroHistorySource(
        identifier="bls-cpi-history-1947-1956",
        mode=CPI_CURRENT_HISTORY_MODE,
        method="POST",
        source_resource=BLS_CPI_API_URL,
        media_type=_JSON_MEDIA_TYPE,
        maximum_bytes=_MAX_BLS_API_BYTES,
        parser_key="bls_cpi_history",
        request_body=_bls_cpi_request_body(
            start_year=1947, end_year=1956, series_codes=(_BLS_ALL_ITEMS_CODE,)
        ),
        request_content_type=_JSON_MEDIA_TYPE,
        start_year=1947,
        end_year=1956,
    ),
    *(
        MacroHistorySource(
            identifier=f"bls-cpi-history-{start_year}-{end_year}",
            mode=CPI_CURRENT_HISTORY_MODE,
            method="POST",
            source_resource=BLS_CPI_API_URL,
            media_type=_JSON_MEDIA_TYPE,
            maximum_bytes=_MAX_BLS_API_BYTES,
            parser_key="bls_cpi_history",
            request_body=_bls_cpi_request_body(
                start_year=start_year,
                end_year=end_year,
                series_codes=(_BLS_ALL_ITEMS_CODE, _BLS_CORE_CODE),
            ),
            request_content_type=_JSON_MEDIA_TYPE,
            start_year=start_year,
            end_year=end_year,
        )
        for start_year, end_year in (
            (1957, 1966),
            (1967, 1976),
            (1977, 1986),
            (1987, 1996),
            (1997, 2006),
            (2007, 2007),
        )
    ),
    MacroHistorySource(
        identifier="philadelphia-fed-noutput-vintages",
        mode=GDP_CPI_VINTAGES_MODE,
        method="GET",
        source_resource=PHILADELPHIA_FED_NOMINAL_OUTPUT_URL,
        media_type=_XLSX_MEDIA_TYPE,
        maximum_bytes=_MAX_RTDSM_BYTES,
        parser_key="rtdsm_nominal_output",
    ),
    MacroHistorySource(
        identifier="philadelphia-fed-routput-vintages",
        mode=GDP_CPI_VINTAGES_MODE,
        method="GET",
        source_resource=PHILADELPHIA_FED_REAL_OUTPUT_URL,
        media_type=_XLSX_MEDIA_TYPE,
        maximum_bytes=_MAX_RTDSM_BYTES,
        parser_key="rtdsm_real_output",
    ),
    MacroHistorySource(
        identifier="philadelphia-fed-pcpi-vintages",
        mode=GDP_CPI_VINTAGES_MODE,
        method="GET",
        source_resource=PHILADELPHIA_FED_CPI_ALL_ITEMS_URL,
        media_type=_XLSX_MEDIA_TYPE,
        maximum_bytes=_MAX_RTDSM_BYTES,
        parser_key="rtdsm_cpi_all_items",
    ),
    MacroHistorySource(
        identifier="philadelphia-fed-pcpix-vintages",
        mode=GDP_CPI_VINTAGES_MODE,
        method="GET",
        source_resource=PHILADELPHIA_FED_CPI_CORE_URL,
        media_type=_XLSX_MEDIA_TYPE,
        maximum_bytes=_MAX_RTDSM_BYTES,
        parser_key="rtdsm_cpi_core",
    ),
)


@dataclass(frozen=True, slots=True)
class MacroHistoryTransportResponse:
    """Bounded metadata from exactly one physical provider request."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


@dataclass(frozen=True, slots=True)
class SealedMacroHistoryResponse:
    """A prior, review-bound response that must never be re-requested.

    The supplied digest binds the exact bytes.  ``captured_at`` is the actual
    original capture time, not a time manufactured when the response is later
    adopted into the canonical evidence layer.
    """

    identifier: str
    body: bytes
    response_sha256: str
    captured_at: str


class MacroHistoryTransport(Protocol):
    """Minimal injected transport; there is deliberately no retry method."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> MacroHistoryTransportResponse: ...


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


PublisherFactory = Callable[[], _Publisher]
ParseMacroHistorySource = Callable[..., object]


@dataclass(frozen=True, slots=True)
class MacroHistoryUnitReport:
    identifier: str
    outcome: str
    semantic_identity: str | None
    written_versions: int

    def mapping(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "outcome": self.outcome,
            "semantic_identity": self.semantic_identity,
            "written_versions": self.written_versions,
        }


@dataclass(frozen=True, slots=True)
class MacroHistoryExtensionReport:
    mode: str
    requested: int
    published: int
    unchanged: int
    units: tuple[MacroHistoryUnitReport, ...]

    def mapping(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "units": [unit.mapping() for unit in self.units],
        }


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Macro history capture time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _captured_at_text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValidationError("Macro history sealed capture time is invalid")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError("Macro history sealed capture time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("Macro history sealed capture time is invalid")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("Macro history source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("Macro history source media type is invalid")
    return result


def _validate_https_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValidationError("Macro history source URL is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError("Macro history source URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ValidationError("Macro history source URL is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Macro history source URL is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("Macro history source URL is invalid")
    return value


def _validate_source(source: object) -> MacroHistorySource:
    if not isinstance(source, MacroHistorySource):
        raise ValidationError("Macro history source descriptor is invalid")
    if not isinstance(source.identifier, str) or _IDENTIFIER.fullmatch(source.identifier) is None:
        raise ValidationError("Macro history source identifier is invalid")
    if source.mode not in {CPI_CURRENT_HISTORY_MODE, GDP_CPI_VINTAGES_MODE}:
        raise ValidationError("Macro history source mode is invalid")
    if not isinstance(source.parser_key, str) or _PARSER_KEY.fullmatch(source.parser_key) is None:
        raise ValidationError("Macro history source parser binding is invalid")
    if isinstance(source.maximum_bytes, bool) or not isinstance(source.maximum_bytes, int):
        raise ValidationError("Macro history source byte bound is invalid")
    if source.maximum_bytes <= 0 or source.maximum_bytes > 64 * 1024 * 1024:
        raise ValidationError("Macro history source byte bound is invalid")
    try:
        _media_type(source.media_type)
    except StoreUnavailableError as exc:
        raise ValidationError("Macro history source media type is invalid") from exc
    _validate_https_url(source.source_resource)
    if source.method not in {"GET", "POST"}:
        raise ValidationError("Macro history source method is invalid")
    if source.method == "GET":
        if source.request_body is not None or source.request_content_type is not None:
            raise ValidationError("Macro history GET source body is invalid")
    else:
        if not isinstance(source.request_body, bytes) or not source.request_body:
            raise ValidationError("Macro history POST source body is invalid")
        try:
            content_type = _media_type(source.request_content_type)
        except StoreUnavailableError as exc:
            raise ValidationError("Macro history POST source media type is invalid") from exc
        if content_type != _JSON_MEDIA_TYPE:
            raise ValidationError("Macro history POST source media type is invalid")
    if (source.start_year is None) != (source.end_year is None):
        raise ValidationError("Macro history source year range is invalid")
    if source.start_year is not None:
        if (
            isinstance(source.start_year, bool)
            or isinstance(source.end_year, bool)
            or not isinstance(source.start_year, int)
            or not isinstance(source.end_year, int)
            or source.start_year < 1900
            or source.end_year < source.start_year
            or source.end_year - source.start_year > 9
        ):
            raise ValidationError("Macro history source year range is invalid")
    return source


def load_sealed_preflight_response(
    *,
    path: str | Path,
    identifier: str,
    expected_sha256: str,
    expected_bytes: int,
    captured_at: str,
    expected_owner_uid: int | None = None,
) -> SealedMacroHistoryResponse:
    """Read one private preflight response after strict physical checks.

    This is intentionally a local adoption seam, not a request cache.  The
    caller provides a fixed path and byte/digest binding established by the
    preflight.  A missing, replaced, linked, permission-changed, truncated, or
    digest-mismatched response fails closed rather than causing a re-request.
    """

    candidate = Path(path)
    if not candidate.is_absolute() or not isinstance(identifier, str):
        raise ValidationError("Macro history sealed response binding is invalid")
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise ValidationError("Macro history sealed response binding is invalid")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise ValidationError("Macro history sealed response binding is invalid")
    if (
        isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or expected_bytes <= 0
        or expected_bytes > 64 * 1024 * 1024
    ):
        raise ValidationError("Macro history sealed response binding is invalid")
    normalized_capture = _captured_at_text(captured_at)
    owner_uid = os.getuid() if expected_owner_uid is None else expected_owner_uid
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid < 0:
        raise ValidationError("Macro history sealed response binding is invalid")
    try:
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            raise StoreUnavailableError("Macro history sealed response path is not direct")
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except StoreUnavailableError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Macro history sealed response is unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != owner_uid
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size != expected_bytes
        ):
            raise StoreUnavailableError("Macro history sealed response binding is invalid")
        chunks: list[bytes] = []
        remaining = expected_bytes + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
    except StoreUnavailableError:
        raise
    except OSError as exc:
        raise StoreUnavailableError("Macro history sealed response is unavailable") from exc
    finally:
        os.close(descriptor)
    if len(body) != expected_bytes or _sha256(body) != expected_sha256:
        raise StoreUnavailableError("Macro history sealed response digest is invalid")
    return SealedMacroHistoryResponse(
        identifier=identifier,
        body=body,
        response_sha256=expected_sha256,
        captured_at=normalized_capture,
    )


def _validate_response(
    response: MacroHistoryTransportResponse,
    *,
    expected_media_type: str,
    maximum: int,
) -> MacroHistoryTransportResponse:
    if not isinstance(response, MacroHistoryTransportResponse):
        raise StoreUnavailableError("Macro history transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("Macro history provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("Macro history provider response is outside policy")
    if _media_type(response.media_type) != _media_type(expected_media_type):
        raise StoreUnavailableError("Macro history provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("Macro history provider response body is invalid")
    if len(response.body) > maximum:
        raise ResourceLimitError("Macro history provider response exceeds its byte bound")
    return response


class _StdlibTransport:
    """One bounded HTTPS request; ``http.client`` follows no redirects."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> MacroHistoryTransportResponse:
        if (
            method not in {"GET", "POST"}
            or not isinstance(url, str)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(body, (bytes, type(None)))
        ):
            raise ValidationError("Macro history provider request is invalid")
        _validate_https_url(url)
        parsed = urlsplit(url)
        target = parsed.path or "/"
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout_seconds)
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError("Macro history provider length is invalid") from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError(
                        "Macro history provider response exceeds its byte bound"
                    )
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ResourceLimitError("Macro history provider response exceeds its byte bound")
            return MacroHistoryTransportResponse(
                status=int(response.status),
                media_type=response.getheader("Content-Type") or "",
                body=payload,
                redirected=(
                    300 <= int(response.status) < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Macro history provider transport failed") from exc
        finally:
            connection.close()


class MacroHistoryExtensionRunner:
    """Run fixed macro-history source units through injected domain interfaces.

    A runner does not open a SQLite connection or import a lock.  It makes no
    retry attempt: a transport, validation, parser, or publisher failure stops
    the run at that unit and propagates to the caller.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        sources: tuple[MacroHistorySource, ...],
        parsers: Mapping[str, ParseMacroHistorySource],
        publisher_factory: PublisherFactory,
        transport: MacroHistoryTransport,
        preseeded_responses: tuple[SealedMacroHistoryResponse, ...] = (),
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._sources = tuple(_validate_source(source) for source in sources)
        self._parsers = dict(parsers)
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._utcnow = utcnow
        self._validate_binding(canonical=_canonical)
        self._validate_source_bindings()
        self._preseeded = self._validate_preseeded_responses(preseeded_responses)

    def _validate_binding(self, *, canonical: bool) -> None:
        try:
            root = self._project_root.resolve(strict=True)
            target = self._macro_store.resolve(strict=True)
            root_info = self._project_root.lstat()
            target_info = self._macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Macro history target is unavailable") from exc
        if (
            root != self._project_root
            or target != self._macro_store
            or self._project_root.is_symlink()
            or self._macro_store.is_symlink()
            or not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(target_info.st_mode)
            or target_info.st_nlink != 1
            or self._macro_store != self._project_root / "data" / "macro.sqlite"
        ):
            raise ValidationError("Macro history target binding is invalid")
        if canonical:
            if self._project_root != PROJECT_ROOT or self._macro_store != MACRO_STORE:
                raise ValidationError("Macro history canonical target binding is invalid")
        else:
            try:
                self._project_root.relative_to(Path("/tmp").resolve(strict=True))
            except ValueError as exc:
                raise ValidationError("Macro history fixtures must use a temporary root") from exc

    def _validate_source_bindings(self) -> None:
        if not self._sources:
            raise ValidationError("Macro history source table is empty")
        identifiers: set[str] = set()
        for source in self._sources:
            if source.identifier in identifiers:
                raise ValidationError("Macro history source identifiers must be unique")
            identifiers.add(source.identifier)
            parser = self._parsers.get(source.parser_key)
            if not callable(parser):
                raise ValidationError("Macro history source parser is unavailable")

    def _validate_preseeded_responses(
        self, responses: tuple[SealedMacroHistoryResponse, ...]
    ) -> dict[str, SealedMacroHistoryResponse]:
        source_by_identifier = {source.identifier: source for source in self._sources}
        result: dict[str, SealedMacroHistoryResponse] = {}
        for response in responses:
            if not isinstance(response, SealedMacroHistoryResponse):
                raise ValidationError("Macro history sealed response is invalid")
            source = source_by_identifier.get(response.identifier)
            if source is None or response.identifier in result:
                raise ValidationError("Macro history sealed response binding is invalid")
            if (
                not isinstance(response.body, bytes)
                or not response.body
                or len(response.body) > source.maximum_bytes
                or not isinstance(response.response_sha256, str)
                or _SHA256.fullmatch(response.response_sha256) is None
                or _sha256(response.body) != response.response_sha256
            ):
                raise ValidationError("Macro history sealed response digest is invalid")
            result[response.identifier] = SealedMacroHistoryResponse(
                identifier=response.identifier,
                body=response.body,
                response_sha256=response.response_sha256,
                captured_at=_captured_at_text(response.captured_at),
            )
        return result

    @staticmethod
    def _publication_report(identifier: str, value: object) -> MacroHistoryUnitReport:
        outcome = getattr(value, "outcome", None)
        if outcome not in {"published", "unchanged"}:
            raise ConflictError("Macro history publisher outcome is invalid")
        semantic_identity = getattr(value, "semantic_identity", None)
        if semantic_identity is not None and (
            not isinstance(semantic_identity, str) or len(semantic_identity) > 256
        ):
            raise ConflictError("Macro history publisher semantic identity is invalid")
        written_versions = getattr(value, "written_versions", None)
        if (
            isinstance(written_versions, bool)
            or not isinstance(written_versions, int)
            or written_versions < 0
        ):
            raise ConflictError("Macro history publisher version count is invalid")
        if (outcome == "unchanged") != (written_versions == 0):
            raise ConflictError("Macro history publisher outcome/count binding is invalid")
        return MacroHistoryUnitReport(
            identifier=identifier,
            outcome=outcome,
            semantic_identity=semantic_identity,
            written_versions=written_versions,
        )

    def _request(self, source: MacroHistorySource) -> MacroHistoryTransportResponse:
        headers = {"Accept": _media_type(source.media_type), "User-Agent": _USER_AGENT}
        if source.request_content_type is not None:
            headers["Content-Type"] = _media_type(source.request_content_type)
        response = self._transport.request(
            method=source.method,
            url=source.source_resource,
            headers=headers,
            body=source.request_body,
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=source.maximum_bytes,
        )
        return _validate_response(
            response,
            expected_media_type=source.media_type,
            maximum=source.maximum_bytes,
        )

    def _run_source(
        self, publisher: _Publisher, source: MacroHistorySource
    ) -> MacroHistoryUnitReport:
        sealed = self._preseeded.get(source.identifier)
        if sealed is None:
            response = self._request(source)
            body = response.body
            captured_at = _utc_text(self._utcnow())
        else:
            body = sealed.body
            captured_at = sealed.captured_at
        parser = self._parsers[source.parser_key]
        arguments: dict[str, object] = {"captured_at": captured_at}
        if source.start_year is not None:
            arguments["start_year"] = source.start_year
            arguments["end_year"] = source.end_year
        capture = parser(body, **arguments)
        return self._publication_report(source.identifier, publisher.publish(capture))

    def run(
        self,
        mode: Literal["cpi-current-history", "gdp-cpi-vintages", "backfill"] | str,
    ) -> MacroHistoryExtensionReport:
        if mode not in _VALID_MODES:
            raise ValidationError("Macro history mode is invalid")
        publisher = self._publisher_factory()
        if not hasattr(publisher, "publish"):
            raise ConflictError("Macro history publisher is invalid")
        selected = (
            self._sources
            if mode == BACKFILL_MODE
            else tuple(source for source in self._sources if source.mode == mode)
        )
        if not selected:
            raise ValidationError("Macro history mode has no reviewed source units")
        units = tuple(self._run_source(publisher, source) for source in selected)
        return MacroHistoryExtensionReport(
            mode=mode,
            requested=len(units),
            published=sum(unit.outcome == "published" for unit in units),
            unchanged=sum(unit.outcome == "unchanged" for unit in units),
            units=units,
        )


_CANONICAL_SEALED_RESPONSES: Final = (
    (
        "bls-cpi-history-1947-1956",
        _PRESEEDED_BLS_CPI_1947_1956_PATH,
        _PRESEEDED_BLS_CPI_1947_1956_SHA256,
        _PRESEEDED_BLS_CPI_1947_1956_BYTES,
        _PRESEEDED_BLS_CPI_1947_1956_CAPTURED_AT,
    ),
    (
        "philadelphia-fed-noutput-vintages",
        _PRESEEDED_RESPONSE_DIRECTORY / "008-philly-noutput.xlsx",
        "6ef256144270f7ee35ee58c51136e3f8a31851fbaaed4385f8770bbf3afb1f09",
        243_172,
        "2026-08-17T16:15:36.774677Z",
    ),
    (
        "philadelphia-fed-routput-vintages",
        _PRESEEDED_RESPONSE_DIRECTORY / "009-philly-routput.xlsx",
        "a89244fc00b14f50d2faf8aa146676bb6ff1bd3914854fc3facde1bd931bc180",
        243_160,
        "2026-08-17T16:15:49.245983Z",
    ),
    (
        "philadelphia-fed-pcpi-vintages",
        _PRESEEDED_RESPONSE_DIRECTORY / "010-philly-pcpi.xlsx",
        "11851cd13c73b743bc3be30564fc18283cedd11211a070a3a82ce6b64b6cca10",
        820_155,
        "2026-08-17T16:15:49.719992Z",
    ),
    (
        "philadelphia-fed-pcpix-vintages",
        _PRESEEDED_RESPONSE_DIRECTORY / "011-philly-pcpix.xlsx",
        "dcdab3493a10a880312164f8da7e1ac98c44b5c76080e9314c89dc74c9dd3e38",
        810_570,
        "2026-08-17T16:15:50.097609Z",
    ),
)


def _canonical_store_map() -> StoreMap:
    data = PROJECT_ROOT / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=MACRO_STORE,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _require_canonical_target() -> None:
    """Fail before registry work rather than creating a target unexpectedly."""

    try:
        root = PROJECT_ROOT.resolve(strict=True)
        target = MACRO_STORE.resolve(strict=True)
        root_info = PROJECT_ROOT.lstat()
        target_info = MACRO_STORE.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Macro history canonical target is unavailable") from exc
    if (
        root != PROJECT_ROOT
        or target != MACRO_STORE
        or PROJECT_ROOT.is_symlink()
        or MACRO_STORE.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISREG(target_info.st_mode)
        or target_info.st_nlink != 1
        or MACRO_STORE.parent != PROJECT_ROOT / "data"
    ):
        raise ValidationError("Macro history canonical target binding is invalid")


def _canonical_preseeded_responses(mode: str) -> tuple[SealedMacroHistoryResponse, ...]:
    selected_ids = {
        source.identifier
        for source in MACRO_HISTORY_SOURCES
        if mode == BACKFILL_MODE or source.mode == mode
    }
    return tuple(
        load_sealed_preflight_response(
            path=path,
            identifier=identifier,
            expected_sha256=digest,
            expected_bytes=size,
            captured_at=captured_at,
        )
        for identifier, path, digest, size, captured_at in _CANONICAL_SEALED_RESPONSES
        if identifier in selected_ids
    )


def _domain_api() -> tuple[Mapping[str, ParseMacroHistorySource], PublisherFactory]:
    """Load the write-side domain only for a fixed canonical invocation."""

    _require_canonical_target()
    from ..macro.live_vintages import (
        MacroLiveVintagePublisher,
        parse_bls_cpi_history_json,
        parse_rtdsm_cpi_all_items_vintage_xlsx,
        parse_rtdsm_cpi_core_vintage_xlsx,
        parse_rtdsm_nominal_output_vintage_xlsx,
        parse_rtdsm_real_output_vintage_xlsx,
    )

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    # This is a manual-only one-time extension.  No scheduler path reaches
    # this call, and no database lock is held by this module during requests.
    migrate_and_register_store(
        _canonical_store_map(),
        registry,
        StoreRole.MACRO,
        applied_at=_utc_text(datetime.now(timezone.utc)),
    )

    def publisher_factory() -> _Publisher:
        return MacroLiveVintagePublisher.for_canonical(registry=registry)

    return (
        {
            "bls_cpi_history": parse_bls_cpi_history_json,
            "rtdsm_nominal_output": parse_rtdsm_nominal_output_vintage_xlsx,
            "rtdsm_real_output": parse_rtdsm_real_output_vintage_xlsx,
            "rtdsm_cpi_all_items": parse_rtdsm_cpi_all_items_vintage_xlsx,
            "rtdsm_cpi_core": parse_rtdsm_cpi_core_vintage_xlsx,
        },
        publisher_factory,
    )


def populate_macro_history_extension_live(
    mode: Literal["cpi-current-history", "gdp-cpi-vintages", "backfill"] = BACKFILL_MODE,
) -> MacroHistoryExtensionReport:
    """Run the approved one-time history extension at the fixed macro target."""

    if mode not in _VALID_MODES:
        raise ValidationError("Macro history mode is invalid")
    # Validate every retained response before a registry/migration operation.
    # A bad local seal must not turn into either a network retry or a partial
    # database transition.
    preseeded_responses = _canonical_preseeded_responses(mode)
    parsers, publisher_factory = _domain_api()
    return MacroHistoryExtensionRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        sources=MACRO_HISTORY_SOURCES,
        parsers=parsers,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        preseeded_responses=preseeded_responses,
        _canonical=True,
    ).run(mode)


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument(
        "--mode",
        choices=(CPI_CURRENT_HISTORY_MODE, GDP_CPI_VINTAGES_MODE, BACKFILL_MODE),
        required=True,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        report = populate_macro_history_extension_live(arguments.mode)
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except (ConflictError, MigrationError, RegistryError):
        code, name = 75, "temporary_conflict"
    except ResourceLimitError:
        code, name = 74, "local_io"
    except Exception:
        code, name = 70, "internal_failure"
    else:
        sys.stdout.write(dumps_strict(report.mapping()) + "\n")
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.macro_history_extension_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = (
    "BACKFILL_MODE",
    "BLS_CPI_API_URL",
    "CPI_CURRENT_HISTORY_MODE",
    "GDP_CPI_VINTAGES_MODE",
    "MACRO_HISTORY_SOURCES",
    "MACRO_STORE",
    "MacroHistoryExtensionReport",
    "MacroHistoryExtensionRunner",
    "MacroHistorySource",
    "MacroHistoryTransport",
    "MacroHistoryTransportResponse",
    "PHILADELPHIA_FED_CPI_ALL_ITEMS_URL",
    "PHILADELPHIA_FED_CPI_CORE_URL",
    "PHILADELPHIA_FED_NOMINAL_OUTPUT_URL",
    "PHILADELPHIA_FED_REAL_OUTPUT_URL",
    "PROJECT_ROOT",
    "SealedMacroHistoryResponse",
    "load_sealed_preflight_response",
    "populate_macro_history_extension_live",
)
