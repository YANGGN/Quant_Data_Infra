"""Fixed-target GDP and CPI vintage population and refresh.

The runner deliberately has a small surface: it obtains the two approved
official source families, fully validates and parses each response outside a
database write section, then hands the opaque domain capture to the macro
publisher.  The domain publisher owns the short SQLite publication section;
this operation owns neither a database path override nor a credential.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
from pathlib import Path
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

BEA_GDP_VINTAGE_URL: Final = "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx"
BLS_CPI_API_URL: Final = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_CPI_SERIES: Final = ("CUSR0000SA0", "CUSR0000SA0L1E")

_XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TEXT_MEDIA_TYPE: Final = "text/plain"
_JSON_MEDIA_TYPE: Final = "application/json"
_TIMEOUT_SECONDS: Final = 60
_MAX_BEA_BYTES: Final = 8 * 1024 * 1024
_MAX_BLS_ARCHIVE_BYTES: Final = 8 * 1024 * 1024
_MAX_BLS_API_BYTES: Final = 2 * 1024 * 1024
_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)"
)
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class _BlsArchive:
    """One authoritative annual revision file from BLS's archive page.

    The resource's Last-Modified header is the provider-supplied vintage date.
    It is deliberately not guessed from a filename or a calendar convention.
    """

    identifier: str
    source_resource: str
    media_type: str


_BLS_CPI_ARCHIVES: Final = (
    _BlsArchive(
        "bls-cpi-revision-2012",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2012.txt",
        _TEXT_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2013",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2013.txt",
        _TEXT_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2014",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2014.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2015",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2015.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2016",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2016.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2017",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2017.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2018",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2018.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2019",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2019.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2020",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2020.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2021",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2021.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2022",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2022.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2023",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2023.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2024",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2024.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
    _BlsArchive(
        "bls-cpi-revision-2025",
        "https://www.bls.gov/cpi/tables/seasonal-adjustment/"
        "revised-seasonally-adjusted-indexes-2025.xlsx",
        _XLSX_MEDIA_TYPE,
    ),
)


@dataclass(frozen=True, slots=True)
class MacroVintageTransportResponse:
    """Bounded response metadata supplied by one physical request."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False
    source_published_at: str | None = None


class MacroVintageTransport(Protocol):
    """Minimal injected transport; it has no retry capability."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> MacroVintageTransportResponse: ...


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


PublisherFactory = Callable[[], _Publisher]
ParseBea = Callable[..., object]
ParseBlsRevision = Callable[..., object]
ParseBlsCurrent = Callable[..., object]


@dataclass(frozen=True, slots=True)
class MacroVintageUnitReport:
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
class MacroVintageReleaseReport:
    mode: str
    requested: int
    published: int
    unchanged: int
    units: tuple[MacroVintageUnitReport, ...]

    def mapping(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "published": self.published,
            "requested": self.requested,
            "unchanged": self.unchanged,
            "units": [unit.mapping() for unit in self.units],
            "version": _VERSION,
        }


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Macro vintage capture time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _header_date(value: str | None, *, required: bool) -> str | None:
    """Normalize an HTTP Last-Modified value without manufacturing precision."""

    if value is None:
        if required:
            raise StoreUnavailableError("Macro vintage source omitted Last-Modified")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise StoreUnavailableError("Macro vintage source Last-Modified is invalid")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError) as exc:
        raise StoreUnavailableError("Macro vintage source Last-Modified is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StoreUnavailableError("Macro vintage source Last-Modified lacks an offset")
    return parsed.astimezone(timezone.utc).date().isoformat()


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("Macro vintage source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("Macro vintage source media type is invalid")
    return result


def _validate_response(
    response: MacroVintageTransportResponse,
    *,
    expected_media_type: str,
    maximum: int,
) -> MacroVintageTransportResponse:
    if not isinstance(response, MacroVintageTransportResponse):
        raise StoreUnavailableError("Macro vintage transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("Macro vintage provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("Macro vintage provider response is outside policy")
    if _media_type(response.media_type) != expected_media_type:
        raise StoreUnavailableError("Macro vintage provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("Macro vintage provider response body is invalid")
    if len(response.body) > maximum:
        raise ResourceLimitError("Macro vintage provider response exceeds its byte bound")
    return response


class _StdlibTransport:
    """One bounded HTTPS request.  ``http.client`` follows no redirects."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> MacroVintageTransportResponse:
        if (
            method not in {"GET", "POST"}
            or not isinstance(url, str)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(body, (bytes, type(None)))
        ):
            raise ValidationError("Macro vintage provider request is invalid")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValidationError("Macro vintage provider URL is invalid")
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout_seconds)
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError("Macro vintage provider length is invalid") from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError("Macro vintage provider response exceeds its byte bound")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ResourceLimitError("Macro vintage provider response exceeds its byte bound")
            return MacroVintageTransportResponse(
                status=int(response.status),
                media_type=response.getheader("Content-Type") or "",
                body=payload,
                redirected=(
                    300 <= int(response.status) < 400
                    or response.getheader("Location") is not None
                ),
                source_published_at=response.getheader("Last-Modified"),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Macro vintage provider transport failed") from exc
        finally:
            connection.close()


class MacroVintageReleaseRunner:
    """Injectable fixture runner for the fixed GDP/CPI source contract.

    It intentionally owns no lock and opens no SQLite connection.  Transport,
    parsing, and semantic validation occur before ``publisher.publish``; the
    macro domain publisher owns its short write lock and transaction.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        publisher_factory: PublisherFactory,
        transport: MacroVintageTransport,
        parse_bea_gdp_vintage_xlsx: ParseBea,
        parse_bls_cpi_revision: ParseBlsRevision,
        parse_bls_current_json: ParseBlsCurrent,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._parse_bea = parse_bea_gdp_vintage_xlsx
        self._parse_bls_revision = parse_bls_cpi_revision
        self._parse_bls_current = parse_bls_current_json
        self._utcnow = utcnow
        self._validate_binding(canonical=_canonical)

    def _validate_binding(self, *, canonical: bool) -> None:
        try:
            root = self._project_root.resolve(strict=True)
            target = self._macro_store.resolve(strict=True)
            root_info = self._project_root.lstat()
            target_info = self._macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Macro vintage target is unavailable") from exc
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
            raise ValidationError("Macro vintage target binding is invalid")
        if canonical:
            if self._project_root != PROJECT_ROOT or self._macro_store != MACRO_STORE:
                raise ValidationError("Macro vintage canonical binding is invalid")
        else:
            try:
                self._project_root.relative_to(Path("/tmp").resolve(strict=True))
            except ValueError as exc:
                raise ValidationError("Macro vintage fixtures must use a temporary root") from exc

    @staticmethod
    def _publication_report(identifier: str, value: object) -> MacroVintageUnitReport:
        outcome = getattr(value, "outcome", None)
        if outcome not in {"published", "unchanged"}:
            raise ConflictError("Macro vintage publisher outcome is invalid")
        semantic_identity = getattr(value, "semantic_identity", None)
        if semantic_identity is not None and (
            not isinstance(semantic_identity, str) or len(semantic_identity) > 256
        ):
            raise ConflictError("Macro vintage publisher semantic identity is invalid")
        written_versions = getattr(value, "written_versions", None)
        if isinstance(written_versions, bool) or not isinstance(written_versions, int) or written_versions < 0:
            raise ConflictError("Macro vintage publisher version count is invalid")
        if (outcome == "unchanged") != (written_versions == 0):
            raise ConflictError("Macro vintage publisher outcome/count binding is invalid")
        return MacroVintageUnitReport(
            identifier=identifier,
            outcome=outcome,
            semantic_identity=semantic_identity,
            written_versions=written_versions,
        )

    def _request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        expected_media_type: str,
        maximum: int,
    ) -> MacroVintageTransportResponse:
        response = self._transport.request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=maximum,
        )
        return _validate_response(
            response, expected_media_type=expected_media_type, maximum=maximum
        )

    def _publish(self, publisher: _Publisher, identifier: str, capture: object) -> MacroVintageUnitReport:
        return self._publication_report(identifier, publisher.publish(capture))

    def _run_bea(self, publisher: _Publisher) -> MacroVintageUnitReport:
        response = self._request(
            method="GET",
            url=BEA_GDP_VINTAGE_URL,
            headers={"Accept": _XLSX_MEDIA_TYPE, "User-Agent": _USER_AGENT},
            body=None,
            expected_media_type=_XLSX_MEDIA_TYPE,
            maximum=_MAX_BEA_BYTES,
        )
        capture = self._parse_bea(
            response.body,
            captured_at=_utc_text(self._utcnow()),
            source_published_at=_header_date(response.source_published_at, required=False),
        )
        return self._publish(publisher, "bea-gdp-vintage-history", capture)

    def _run_bls_archive(self, publisher: _Publisher, resource: _BlsArchive) -> MacroVintageUnitReport:
        response = self._request(
            method="GET",
            url=resource.source_resource,
            headers={"Accept": resource.media_type, "User-Agent": _USER_AGENT},
            body=None,
            expected_media_type=resource.media_type,
            maximum=_MAX_BLS_ARCHIVE_BYTES,
        )
        capture = self._parse_bls_revision(
            response.body,
            source_resource=resource.source_resource,
            vintage_at=_header_date(response.source_published_at, required=True),
            captured_at=_utc_text(self._utcnow()),
            media_type=_media_type(response.media_type),
        )
        return self._publish(publisher, resource.identifier, capture)

    def _run_bls_current(self, publisher: _Publisher) -> MacroVintageUnitReport:
        captured = self._utcnow()
        end_year = captured.astimezone(timezone.utc).year
        body = dumps_strict(
            {
                "endyear": str(end_year),
                "seriesid": list(BLS_CPI_SERIES),
                "startyear": str(end_year - 9),
            }
        ).encode("utf-8")
        response = self._request(
            method="POST",
            url=BLS_CPI_API_URL,
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "Content-Type": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
            },
            body=body,
            expected_media_type=_JSON_MEDIA_TYPE,
            maximum=_MAX_BLS_API_BYTES,
        )
        capture = self._parse_bls_current(
            response.body,
            captured_at=_utc_text(captured),
        )
        return self._publish(publisher, "bls-cpi-current", capture)

    def run(self, mode: Literal["backfill", "refresh"] | str) -> MacroVintageReleaseReport:
        if mode not in {"backfill", "refresh"}:
            raise ValidationError("Macro vintage mode is invalid")
        publisher = self._publisher_factory()
        if not hasattr(publisher, "publish"):
            raise ConflictError("Macro vintage publisher is invalid")
        units: list[MacroVintageUnitReport] = [self._run_bea(publisher)]
        if mode == "backfill":
            units.extend(self._run_bls_archive(publisher, item) for item in _BLS_CPI_ARCHIVES)
        units.append(self._run_bls_current(publisher))
        return MacroVintageReleaseReport(
            mode=mode,
            requested=len(units),
            published=sum(item.outcome == "published" for item in units),
            unchanged=sum(item.outcome == "unchanged" for item in units),
            units=tuple(units),
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
    """Fail before registry work rather than creating an unexpected store."""

    try:
        root = PROJECT_ROOT.resolve(strict=True)
        target = MACRO_STORE.resolve(strict=True)
        root_info = PROJECT_ROOT.lstat()
        target_info = MACRO_STORE.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Macro vintage canonical target is unavailable") from exc
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
        raise ValidationError("Macro vintage canonical target binding is invalid")


def _domain_api(
    *, register_target: bool,
) -> tuple[ParseBea, ParseBlsRevision, ParseBlsCurrent, PublisherFactory]:
    """Load the write-side domain only for a canonical invocation.

    Keeping this import lazy lets the injectable operation test surface remain
    dependency-free and prevents a fixture test from accidentally configuring a
    canonical publisher.
    """

    _require_canonical_target()
    from ..macro.live_vintages import (
        MacroLiveVintagePublisher,
        parse_bea_gdp_vintage_xlsx,
        parse_bls_cpi_revision,
        parse_bls_current_json,
    )

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    if register_target:
        # The manual backfill is the one bounded registration point.  The
        # recurring refresh must never turn a scheduler invocation into a
        # migration/registry-write path.
        migrate_and_register_store(
            _canonical_store_map(),
            registry,
            StoreRole.MACRO,
            applied_at=_utc_text(datetime.now(timezone.utc)),
        )

    def publisher_factory() -> _Publisher:
        return MacroLiveVintagePublisher.for_canonical(registry=registry)

    return (
        parse_bea_gdp_vintage_xlsx,
        parse_bls_cpi_revision,
        parse_bls_current_json,
        publisher_factory,
    )


def _run_canonical(mode: Literal["backfill", "refresh"]) -> MacroVintageReleaseReport:
    parse_bea, parse_bls_revision, parse_bls_current, publisher_factory = _domain_api(
        register_target=(mode == "backfill")
    )
    return MacroVintageReleaseRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        parse_bea_gdp_vintage_xlsx=parse_bea,
        parse_bls_cpi_revision=parse_bls_revision,
        parse_bls_current_json=parse_bls_current,
        utcnow=lambda: datetime.now(timezone.utc),
        _canonical=True,
    ).run(mode)


def populate_gdp_cpi_vintages_live() -> MacroVintageReleaseReport:
    """Populate the one bounded historical GDP/CPI vintage cohort."""

    return _run_canonical("backfill")


def refresh_gdp_cpi_vintages_live() -> MacroVintageReleaseReport:
    """Refresh only the approved current GDP/CPI sources."""

    return _run_canonical("refresh")


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=("backfill", "refresh"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.mode == "backfill":
            report = populate_gdp_cpi_vintages_live()
        else:
            report = refresh_gdp_cpi_vintages_live()
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
                "contract": "quant_data.macro_vintage_release_error",
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
    "BEA_GDP_VINTAGE_URL",
    "BLS_CPI_API_URL",
    "BLS_CPI_SERIES",
    "MACRO_STORE",
    "MacroVintageReleaseReport",
    "MacroVintageReleaseRunner",
    "MacroVintageTransport",
    "MacroVintageTransportResponse",
    "PROJECT_ROOT",
    "populate_gdp_cpi_vintages_live",
    "refresh_gdp_cpi_vintages_live",
)
