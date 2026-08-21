"""Fixed-target historical employment vintages and monthly BLS refresh.

The manual backfill obtains exactly the two approved Philadelphia Fed RTDSM
workbooks and one BLS current response.  The scheduled path is intentionally
separate: it runs only in the monthly first-Friday release window and obtains
only the BLS current response.  Parsing completes before publication, so this
operation never holds a SQLite write lock while performing network work.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timezone
import http.client
from pathlib import Path
import stat
import sys
from typing import Final, Literal, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

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

PHILADELPHIA_FED_PAYROLL_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/employMvMd.xlsx"
)
PHILADELPHIA_FED_UNEMPLOYMENT_URL: Final = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/rucQvMd.xlsx"
)
BLS_EMPLOYMENT_API_URL: Final = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_EMPLOYMENT_SERIES: Final = ("CES0000000001", "LNS14000000")

TIMEZONE: Final = "America/New_York"
MONTHLY_RELEASE_TIME: Final = "10:05"
_XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_JSON_MEDIA_TYPE: Final = "application/json"
_TIMEOUT_SECONDS: Final = 60
_MAX_RTDSM_BYTES: Final = 8 * 1024 * 1024
_MAX_BLS_API_BYTES: Final = 2 * 1024 * 1024
_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)"
)
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class EmploymentVintageTransportResponse:
    """One bounded physical provider response."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


class EmploymentVintageTransport(Protocol):
    """Minimal injected transport; a runner has no retry path."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> EmploymentVintageTransportResponse: ...


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


PublisherFactory = Callable[[], _Publisher]
ParseRtdsmPayroll = Callable[..., object]
ParseRtdsmUnemployment = Callable[..., object]
ParseBlsCurrentEmployment = Callable[..., object]


@dataclass(frozen=True, slots=True)
class EmploymentVintageUnitReport:
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
class EmploymentVintageReleaseReport:
    mode: str
    outcome: str
    requested: int
    published: int
    unchanged: int
    units: tuple[EmploymentVintageUnitReport, ...]

    def mapping(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "outcome": self.outcome,
            "published": self.published,
            "requested": self.requested,
            "unchanged": self.unchanged,
            "units": [unit.mapping() for unit in self.units],
            "version": _VERSION,
        }


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Employment vintage capture time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def is_monthly_employment_release_window(now: datetime) -> bool:
    """Whether ``now`` is at/after the first-Friday 10:05 New York gate."""

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("Employment vintage schedule time is invalid")
    local = now.astimezone(ZoneInfo(TIMEZONE))
    return (
        local.weekday() == 4
        and local.day <= 7
        and local.timetz().replace(tzinfo=None) >= datetime_time(10, 5)
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("Employment vintage source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("Employment vintage source media type is invalid")
    return result


def _validate_response(
    response: EmploymentVintageTransportResponse,
    *,
    expected_media_type: str,
    maximum: int,
) -> EmploymentVintageTransportResponse:
    if not isinstance(response, EmploymentVintageTransportResponse):
        raise StoreUnavailableError("Employment vintage transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("Employment vintage provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("Employment vintage provider response is outside policy")
    if _media_type(response.media_type) != expected_media_type:
        raise StoreUnavailableError("Employment vintage provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("Employment vintage provider response body is invalid")
    if len(response.body) > maximum:
        raise ResourceLimitError("Employment vintage provider response exceeds its byte bound")
    return response


class _StdlibTransport:
    """One bounded HTTPS request; ``http.client`` never follows redirects."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_bytes: int,
    ) -> EmploymentVintageTransportResponse:
        if (
            method not in {"GET", "POST"}
            or not isinstance(url, str)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(body, (bytes, type(None)))
        ):
            raise ValidationError("Employment vintage provider request is invalid")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValidationError("Employment vintage provider URL is invalid")
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
                    raise StoreUnavailableError("Employment vintage provider length is invalid") from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError(
                        "Employment vintage provider response exceeds its byte bound"
                    )
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ResourceLimitError("Employment vintage provider response exceeds its byte bound")
            return EmploymentVintageTransportResponse(
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
            raise StoreUnavailableError("Employment vintage provider transport failed") from exc
        finally:
            connection.close()


class EmploymentVintageReleaseRunner:
    """Injectable runner for the frozen employment population contract.

    This runner does not import locking or SQLite primitives.  Each response is
    fetched and parsed before the opaque capture reaches the domain publisher.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        publisher_factory: PublisherFactory,
        transport: EmploymentVintageTransport,
        parse_rtdsm_payroll_vintage_xlsx: ParseRtdsmPayroll,
        parse_rtdsm_unemployment_vintage_xlsx: ParseRtdsmUnemployment,
        parse_bls_employment_current_json: ParseBlsCurrentEmployment,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._parse_payroll = parse_rtdsm_payroll_vintage_xlsx
        self._parse_unemployment = parse_rtdsm_unemployment_vintage_xlsx
        self._parse_bls_current = parse_bls_employment_current_json
        self._utcnow = utcnow
        self._validate_binding(canonical=_canonical)

    def _validate_binding(self, *, canonical: bool) -> None:
        try:
            root = self._project_root.resolve(strict=True)
            target = self._macro_store.resolve(strict=True)
            root_info = self._project_root.lstat()
            target_info = self._macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Employment vintage target is unavailable") from exc
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
            raise ValidationError("Employment vintage target binding is invalid")
        if canonical:
            if self._project_root != PROJECT_ROOT or self._macro_store != MACRO_STORE:
                raise ValidationError("Employment vintage canonical binding is invalid")
        else:
            try:
                self._project_root.relative_to(Path("/tmp").resolve(strict=True))
            except ValueError as exc:
                raise ValidationError("Employment vintage fixtures must use a temporary root") from exc

    @staticmethod
    def _publication_report(identifier: str, value: object) -> EmploymentVintageUnitReport:
        outcome = getattr(value, "outcome", None)
        if outcome not in {"published", "unchanged"}:
            raise ConflictError("Employment vintage publisher outcome is invalid")
        semantic_identity = getattr(value, "semantic_identity", None)
        if semantic_identity is not None and (
            not isinstance(semantic_identity, str) or len(semantic_identity) > 256
        ):
            raise ConflictError("Employment vintage publisher semantic identity is invalid")
        written_versions = getattr(value, "written_versions", None)
        if (
            isinstance(written_versions, bool)
            or not isinstance(written_versions, int)
            or written_versions < 0
        ):
            raise ConflictError("Employment vintage publisher version count is invalid")
        if (outcome == "unchanged") != (written_versions == 0):
            raise ConflictError("Employment vintage publisher outcome/count binding is invalid")
        return EmploymentVintageUnitReport(
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
    ) -> EmploymentVintageTransportResponse:
        response = self._transport.request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=maximum,
        )
        return _validate_response(
            response,
            expected_media_type=expected_media_type,
            maximum=maximum,
        )

    def _publish(
        self, publisher: _Publisher, identifier: str, capture: object
    ) -> EmploymentVintageUnitReport:
        return self._publication_report(identifier, publisher.publish(capture))

    def _run_payroll(self, publisher: _Publisher) -> EmploymentVintageUnitReport:
        response = self._request(
            method="GET",
            url=PHILADELPHIA_FED_PAYROLL_URL,
            headers={"Accept": _XLSX_MEDIA_TYPE, "User-Agent": _USER_AGENT},
            body=None,
            expected_media_type=_XLSX_MEDIA_TYPE,
            maximum=_MAX_RTDSM_BYTES,
        )
        capture = self._parse_payroll(response.body, captured_at=_utc_text(self._utcnow()))
        return self._publish(publisher, "philadelphia-fed-payroll-rtdsm", capture)

    def _run_unemployment(self, publisher: _Publisher) -> EmploymentVintageUnitReport:
        response = self._request(
            method="GET",
            url=PHILADELPHIA_FED_UNEMPLOYMENT_URL,
            headers={"Accept": _XLSX_MEDIA_TYPE, "User-Agent": _USER_AGENT},
            body=None,
            expected_media_type=_XLSX_MEDIA_TYPE,
            maximum=_MAX_RTDSM_BYTES,
        )
        capture = self._parse_unemployment(response.body, captured_at=_utc_text(self._utcnow()))
        return self._publish(publisher, "philadelphia-fed-unemployment-rtdsm", capture)

    def _run_bls_current(self, publisher: _Publisher) -> EmploymentVintageUnitReport:
        captured = self._utcnow()
        end_year = captured.astimezone(timezone.utc).year
        body = dumps_strict(
            {
                "endyear": str(end_year),
                "seriesid": list(BLS_EMPLOYMENT_SERIES),
                "startyear": str(end_year - 9),
            }
        ).encode("utf-8")
        response = self._request(
            method="POST",
            url=BLS_EMPLOYMENT_API_URL,
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "Content-Type": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
            },
            body=body,
            expected_media_type=_JSON_MEDIA_TYPE,
            maximum=_MAX_BLS_API_BYTES,
        )
        capture = self._parse_bls_current(response.body, captured_at=_utc_text(captured))
        return self._publish(publisher, "bls-employment-current", capture)

    def run(self, mode: Literal["backfill", "refresh"] | str) -> EmploymentVintageReleaseReport:
        if mode not in {"backfill", "refresh"}:
            raise ValidationError("Employment vintage mode is invalid")
        if mode == "refresh" and not is_monthly_employment_release_window(self._utcnow()):
            return EmploymentVintageReleaseReport(
                mode="refresh",
                outcome="skipped_not_release_window",
                requested=0,
                published=0,
                unchanged=0,
                units=(),
            )
        publisher = self._publisher_factory()
        if not hasattr(publisher, "publish"):
            raise ConflictError("Employment vintage publisher is invalid")
        units: list[EmploymentVintageUnitReport] = []
        if mode == "backfill":
            units.append(self._run_payroll(publisher))
            units.append(self._run_unemployment(publisher))
        units.append(self._run_bls_current(publisher))
        return EmploymentVintageReleaseReport(
            mode=mode,
            outcome="complete",
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
        raise StoreUnavailableError("Employment vintage canonical target is unavailable") from exc
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
        raise ValidationError("Employment vintage canonical target binding is invalid")


def _domain_api(
    *, register_target: bool,
) -> tuple[ParseRtdsmPayroll, ParseRtdsmUnemployment, ParseBlsCurrentEmployment, PublisherFactory]:
    """Load frozen write-side interfaces only for a canonical invocation."""

    _require_canonical_target()
    from ..macro.live_vintages import (
        MacroLiveVintagePublisher,
        parse_bls_employment_current_json,
        parse_rtdsm_payroll_vintage_xlsx,
        parse_rtdsm_unemployment_vintage_xlsx,
    )

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    if register_target:
        # The one manual backfill may register the reviewed live datasets. A
        # recurring timer must never become a migration/registry-write path.
        migrate_and_register_store(
            _canonical_store_map(),
            registry,
            StoreRole.MACRO,
            applied_at=_utc_text(datetime.now(timezone.utc)),
        )

    def publisher_factory() -> _Publisher:
        return MacroLiveVintagePublisher.for_canonical(registry=registry)

    return (
        parse_rtdsm_payroll_vintage_xlsx,
        parse_rtdsm_unemployment_vintage_xlsx,
        parse_bls_employment_current_json,
        publisher_factory,
    )


def _run_canonical(mode: Literal["backfill", "refresh"]) -> EmploymentVintageReleaseReport:
    payroll, unemployment, bls_current, publisher_factory = _domain_api(
        register_target=(mode == "backfill")
    )
    return EmploymentVintageReleaseRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        parse_rtdsm_payroll_vintage_xlsx=payroll,
        parse_rtdsm_unemployment_vintage_xlsx=unemployment,
        parse_bls_employment_current_json=bls_current,
        utcnow=lambda: datetime.now(timezone.utc),
        _canonical=True,
    ).run(mode)


def populate_employment_vintages_live() -> EmploymentVintageReleaseReport:
    """Run the sole fixed-target historical employment population."""

    return _run_canonical("backfill")


def refresh_employment_vintages_live() -> EmploymentVintageReleaseReport:
    """Run the release-gated monthly BLS payroll/unemployment refresh."""

    now = datetime.now(timezone.utc)
    if not is_monthly_employment_release_window(now):
        return EmploymentVintageReleaseReport(
            mode="refresh",
            outcome="skipped_not_release_window",
            requested=0,
            published=0,
            unchanged=0,
            units=(),
        )
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
            report = populate_employment_vintages_live()
        else:
            report = refresh_employment_vintages_live()
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
                "contract": "quant_data.employment_vintage_release_error",
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
    "BLS_EMPLOYMENT_API_URL",
    "BLS_EMPLOYMENT_SERIES",
    "EmploymentVintageReleaseReport",
    "EmploymentVintageReleaseRunner",
    "EmploymentVintageTransport",
    "EmploymentVintageTransportResponse",
    "MACRO_STORE",
    "MONTHLY_RELEASE_TIME",
    "PHILADELPHIA_FED_PAYROLL_URL",
    "PHILADELPHIA_FED_UNEMPLOYMENT_URL",
    "PROJECT_ROOT",
    "TIMEZONE",
    "is_monthly_employment_release_window",
    "populate_employment_vintages_live",
    "refresh_employment_vintages_live",
)
