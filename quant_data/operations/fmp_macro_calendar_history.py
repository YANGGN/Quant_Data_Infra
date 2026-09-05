"""Manual-only FMP US GDP/CPI economic-calendar history runner.

This operation deliberately stays small.  It issues the fixed, historical
FMP calendar requests serially, validates and parses each response outside the
macro publisher's write section, and delegates canonical persistence and
semantic replay to that publisher.  It has no scheduler, retry, migration, or
provider-response cache path.

The fixture-facing runner accepts injected transport, parser, publisher, and
credential seams.  Canonical imports remain lazy so fixture tests cannot
accidentally configure a canonical writer.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import http.client
import json
import os
from pathlib import Path
import stat
import sys
from typing import Final, Protocol
from urllib.parse import urlencode, urlsplit

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MACRO_STORE: Final = PROJECT_ROOT / "data" / "macro.sqlite"

FMP_ECONOMIC_CALENDAR_URL: Final = "https://financialmodelingprep.com/stable/economic-calendar"
BACKFILL_START_DATE: Final = date(2013, 1, 1)
BACKFILL_END_DATE: Final = date(2026, 8, 17)
MAX_WINDOW_DAYS: Final = 90

_TIMEOUT_SECONDS: Final = 60
_MAX_RESPONSE_BYTES: Final = 1024 * 1024
_JSON_MEDIA_TYPE: Final = "application/json"
_USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class FmpMacroCalendarWindow:
    """One fixed inclusive historical request window."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start_date, date)
            or isinstance(self.start_date, datetime)
            or not isinstance(self.end_date, date)
            or isinstance(self.end_date, datetime)
            or self.start_date > self.end_date
            or (self.end_date - self.start_date).days + 1 > MAX_WINDOW_DAYS
        ):
            raise ValidationError("FMP calendar window is invalid")

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "country": "US",
            "from": self.start_date.isoformat(),
            "to": self.end_date.isoformat(),
        }


def build_fmp_macro_calendar_windows(
    *,
    start_date: date = BACKFILL_START_DATE,
    end_date: date = BACKFILL_END_DATE,
) -> tuple[FmpMacroCalendarWindow, ...]:
    """Build contiguous, inclusive windows bounded to 90 calendar days."""

    if (
        not isinstance(start_date, date)
        or isinstance(start_date, datetime)
        or not isinstance(end_date, date)
        or isinstance(end_date, datetime)
        or start_date > end_date
    ):
        raise ValidationError("FMP calendar history range is invalid")
    windows: list[FmpMacroCalendarWindow] = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end_date)
        windows.append(FmpMacroCalendarWindow(cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


FMP_MACRO_CALENDAR_WINDOWS: Final = build_fmp_macro_calendar_windows()


@dataclass(frozen=True, slots=True)
class FmpMacroCalendarTransportResponse:
    """Bounded metadata returned from exactly one provider request."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


class FmpMacroCalendarTransport(Protocol):
    """The operation's intentionally one-attempt transport seam."""

    def request(
        self,
        *,
        url: str,
        parameters: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> FmpMacroCalendarTransportResponse: ...


class _Publisher(Protocol):
    def completed_windows(self) -> tuple[tuple[str, str], ...]: ...

    def publish(self, capture: object) -> object: ...


ParseFmpMacroCalendar = Callable[..., object]
PublisherFactory = Callable[[], _Publisher]
CredentialReader = Callable[..., str]


@dataclass(frozen=True, slots=True)
class FmpMacroCalendarHistoryReport:
    """Concise, credential- and path-free result for a fixed run."""

    requested: int
    published: int
    unchanged: int
    written_versions: int

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_versions": self.written_versions,
        }


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("FMP calendar capture time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("FMP calendar source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("FMP calendar source media type is invalid")
    return result


def _validate_https_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValidationError("FMP calendar source URL is invalid")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname is None
            or parsed.port is not None and not 1 <= parsed.port <= 65535
        ):
            raise ValidationError("FMP calendar source URL is invalid")
    except ValueError as exc:
        raise ValidationError("FMP calendar source URL is invalid") from exc
    return value


def _validate_response(
    response: FmpMacroCalendarTransportResponse,
) -> FmpMacroCalendarTransportResponse:
    if not isinstance(response, FmpMacroCalendarTransportResponse):
        raise StoreUnavailableError("FMP calendar transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("FMP calendar provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("FMP calendar provider response is outside policy")
    if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("FMP calendar provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("FMP calendar provider response body is invalid")
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise ResourceLimitError("FMP calendar provider response exceeds its byte bound")
    try:
        json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("FMP calendar provider JSON is invalid") from exc
    return response


class _StdlibTransport:
    """One bounded HTTPS request.  ``http.client`` follows no redirects."""

    def request(
        self,
        *,
        url: str,
        parameters: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> FmpMacroCalendarTransportResponse:
        if (
            not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(parameters, Mapping)
            or not isinstance(headers, Mapping)
        ):
            raise ValidationError("FMP calendar provider request is invalid")
        _validate_https_url(url)
        try:
            query = urlencode(
                {
                    key: value
                    for key, value in parameters.items()
                    if isinstance(key, str) and isinstance(value, str)
                },
                doseq=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("FMP calendar provider request is invalid") from exc
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in parameters.items()
        ):
            raise ValidationError("FMP calendar provider request is invalid")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in headers.items()
        ):
            raise ValidationError("FMP calendar provider request is invalid")
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if query:
            target += "?" + query
        try:
            connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout_seconds)
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP calendar provider transport failed") from exc
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError("FMP calendar provider length is invalid") from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError("FMP calendar provider response exceeds its byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("FMP calendar provider response exceeds its byte bound")
            return FmpMacroCalendarTransportResponse(
                status=int(response.status),
                media_type=response.getheader("Content-Type") or "",
                body=body,
                redirected=(
                    300 <= int(response.status) < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP calendar provider transport failed") from exc
        finally:
            connection.close()


class FmpMacroCalendarHistoryRunner:
    """Run the fixed historical calendar plan through injected domain seams."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseFmpMacroCalendar,
        publisher_factory: PublisherFactory,
        transport: FmpMacroCalendarTransport,
        credential_environment: Mapping[str, str],
        credential_reader: CredentialReader = read_project_credential,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._parser = parser
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._credential_environment = credential_environment
        self._credential_reader = credential_reader
        self._utcnow = utcnow
        self._validate_binding(canonical=_canonical)
        if (
            not callable(self._parser)
            or not callable(self._publisher_factory)
            or not callable(getattr(self._transport, "request", None))
            or not isinstance(self._credential_environment, Mapping)
            or not callable(self._credential_reader)
            or not callable(self._utcnow)
        ):
            raise ValidationError("FMP calendar runner dependencies are invalid")

    def _validate_binding(self, *, canonical: bool) -> None:
        try:
            root = self._project_root.resolve(strict=True)
            target = self._macro_store.resolve(strict=True)
            root_info = self._project_root.lstat()
            target_info = self._macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("FMP calendar target is unavailable") from exc
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
            raise ValidationError("FMP calendar target binding is invalid")
        if canonical:
            if self._project_root != PROJECT_ROOT or self._macro_store != MACRO_STORE:
                raise ValidationError("FMP calendar canonical target binding is invalid")
        else:
            try:
                self._project_root.relative_to(Path("/tmp").resolve(strict=True))
            except ValueError as exc:
                raise ValidationError("FMP calendar fixtures must use a temporary root") from exc

    @staticmethod
    def _publication_result(value: object) -> tuple[str, int]:
        outcome = getattr(value, "outcome", None)
        semantic_identity = getattr(value, "semantic_identity", None)
        written_versions = getattr(value, "written_versions", None)
        if (
            outcome not in {"published", "unchanged"}
            or not isinstance(semantic_identity, str)
            or not semantic_identity
            or len(semantic_identity) > 256
            or isinstance(written_versions, bool)
            or not isinstance(written_versions, int)
            or written_versions < 0
            or (outcome == "unchanged") != (written_versions == 0)
        ):
            raise ConflictError("FMP calendar publisher outcome is invalid")
        return outcome, written_versions

    def _read_api_key_once(self) -> str:
        try:
            value = self._credential_reader(
                project_root=self._project_root,
                name="FMP_API_KEY",
                environment=self._credential_environment,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("Credential is missing or invalid") from exc
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValidationError("Credential is missing or invalid")
        return value

    def _request(
        self, window: FmpMacroCalendarWindow, api_key: str
    ) -> FmpMacroCalendarTransportResponse:
        response = self._transport.request(
            url=FMP_ECONOMIC_CALENDAR_URL,
            parameters=window.parameters,
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
                "apikey": api_key,
            },
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        return _validate_response(response)

    def _run_window(
        self,
        *,
        publisher: _Publisher,
        window: FmpMacroCalendarWindow,
        api_key: str,
    ) -> tuple[str, int]:
        response = self._request(window, api_key)
        capture = self._parser(
            response.body,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        return self._publication_result(publisher.publish(capture))

    def run(self) -> FmpMacroCalendarHistoryReport:
        publisher = self._publisher_factory()
        if (
            not callable(getattr(publisher, "publish", None))
            or not callable(getattr(publisher, "completed_windows", None))
        ):
            raise ConflictError("FMP calendar publisher is invalid")
        completed = publisher.completed_windows()
        if (
            not isinstance(completed, tuple)
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or any(not isinstance(value, str) for value in item)
                for item in completed
            )
        ):
            raise ConflictError("FMP calendar completed-window state is invalid")
        planned = tuple(
            (window.start_date.isoformat(), window.end_date.isoformat())
            for window in FMP_MACRO_CALENDAR_WINDOWS
        )
        if completed != planned[: len(completed)]:
            raise ConflictError("FMP calendar completed windows are not a plan prefix")
        pending = FMP_MACRO_CALENDAR_WINDOWS[len(completed) :]
        if not pending:
            return FmpMacroCalendarHistoryReport(
                requested=0,
                published=0,
                unchanged=0,
                written_versions=0,
            )
        # This is intentionally the only credential lookup.  It follows all
        # local binding/dependency checks and immediately precedes the first
        # one-at-a-time transport request; the validated value is reused only
        # in memory for the remaining fixed windows.
        api_key = self._read_api_key_once()
        results = tuple(
            self._run_window(publisher=publisher, window=window, api_key=api_key)
            for window in pending
        )
        return FmpMacroCalendarHistoryReport(
            requested=len(results),
            published=sum(outcome == "published" for outcome, _ in results),
            unchanged=sum(outcome == "unchanged" for outcome, _ in results),
            written_versions=sum(written_versions for _, written_versions in results),
        )


def _require_canonical_target() -> None:
    """Reject a missing or substituted canonical target before domain imports."""

    try:
        root = PROJECT_ROOT.resolve(strict=True)
        target = MACRO_STORE.resolve(strict=True)
        root_info = PROJECT_ROOT.lstat()
        target_info = MACRO_STORE.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("FMP calendar canonical target is unavailable") from exc
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
        raise ValidationError("FMP calendar canonical target binding is invalid")


def _domain_api() -> tuple[ParseFmpMacroCalendar, PublisherFactory]:
    """Load the write-side domain only for the fixed canonical operation."""

    _require_canonical_target()
    from ..macro.fmp_release_surprises import (
        FmpMacroCalendarPublisher,
        parse_fmp_us_gdp_cpi_calendar,
    )

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> _Publisher:
        return FmpMacroCalendarPublisher.for_canonical(registry=registry)

    return parse_fmp_us_gdp_cpi_calendar, publisher_factory


def populate_fmp_macro_calendar_history_live() -> FmpMacroCalendarHistoryReport:
    """Run the approved manual GDP/CPI calendar history backfill once."""

    parser, publisher_factory = _domain_api()
    return FmpMacroCalendarHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        credential_environment=os.environ,
        _canonical=True,
    ).run()


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    return _SafeArgumentParser(add_help=False)


def main(argv: list[str] | None = None) -> int:
    try:
        _parser().parse_args(argv)
        report = populate_fmp_macro_calendar_history_live()
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except RegistryError:
        code, name = 75, "temporary_conflict"
    except ConflictError:
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
                "contract": "quant_data.fmp_macro_calendar_history_error",
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
    "BACKFILL_END_DATE",
    "BACKFILL_START_DATE",
    "FMP_ECONOMIC_CALENDAR_URL",
    "FMP_MACRO_CALENDAR_WINDOWS",
    "MACRO_STORE",
    "MAX_WINDOW_DAYS",
    "PROJECT_ROOT",
    "FmpMacroCalendarHistoryReport",
    "FmpMacroCalendarHistoryRunner",
    "FmpMacroCalendarTransport",
    "FmpMacroCalendarTransportResponse",
    "FmpMacroCalendarWindow",
    "build_fmp_macro_calendar_windows",
    "populate_fmp_macro_calendar_history_live",
)
