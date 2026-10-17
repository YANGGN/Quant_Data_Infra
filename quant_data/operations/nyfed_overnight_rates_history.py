"""Manual-only, bounded NY Fed overnight reference-rate history operation."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import stat
import sys
from typing import Final, Protocol

from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from .fmp_macro_calendar_history import (
    MACRO_STORE,
    PROJECT_ROOT,
    FmpMacroCalendarTransport,
    FmpMacroCalendarTransportResponse,
    _JSON_MEDIA_TYPE,
    _StdlibTransport,
)


NYFED_OVERNIGHT_RATES_URL: Final = (
    "https://markets.newyorkfed.org/api/rates/all/search.json"
)
NYFED_OVERNIGHT_RATES_HISTORY_COLLECTOR_ID: Final = (
    "nyfed.macro.overnight_rates_history"
)
NYFED_OVERNIGHT_RATES_HISTORY_HANDLER: Final = (
    "macro.nyfed_overnight_rates_history"
)
MAX_WINDOW_DAYS: Final = 5_000
_TIMEOUT_SECONDS: Final = 60
_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class NyFedOvernightRatesWindow:
    """One caller-supplied inclusive request window."""

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
            raise ValidationError("NY Fed overnight-rate window is invalid")

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class NyFedOvernightRatesHistoryReport:
    requested: int
    published: int
    unchanged: int
    written_series: int
    written_observation_versions: int
    start_date: date
    end_date: date

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_series": self.written_series,
            "written_observation_versions": self.written_observation_versions,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


ParseNyFedOvernightRates = Callable[..., object]
PublisherFactory = Callable[[], _Publisher]


def _utc_text(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError("NY Fed capture time must include an offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("NY Fed source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("NY Fed source media type is invalid")
    return result


def _validate_response(
    response: FmpMacroCalendarTransportResponse,
) -> FmpMacroCalendarTransportResponse:
    if not isinstance(response, FmpMacroCalendarTransportResponse):
        raise StoreUnavailableError("NY Fed transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("NY Fed provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("NY Fed provider response is outside policy")
    if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("NY Fed provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("NY Fed provider response body is invalid")
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise ResourceLimitError("NY Fed provider response exceeds its byte bound")
    try:
        json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("NY Fed provider JSON is invalid") from exc
    return response


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("NY Fed overnight-rate target is unavailable") from exc
    if (
        root != project_root
        or target != macro_store
        or project_root.is_symlink()
        or macro_store.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISREG(target_info.st_mode)
        or target_info.st_nlink != 1
        or macro_store != project_root / "data" / "macro.sqlite"
    ):
        raise ValidationError("NY Fed overnight-rate target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError(
                "NY Fed overnight-rate canonical target binding is invalid"
            )
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError(
            "NY Fed overnight-rate fixtures must use a temporary root"
        ) from exc


def _as_date(value: date | str, *, name: str) -> date:
    if isinstance(value, datetime):
        raise ValidationError(f"{name} must be an ISO calendar date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{name} must be an ISO calendar date") from exc


def _publication_result(value: object) -> tuple[str, int, int]:
    outcome = getattr(value, "outcome", None)
    written_series = getattr(value, "written_series", None)
    written_versions = getattr(value, "written_observation_versions", None)
    if (
        outcome not in {"published", "unchanged"}
        or isinstance(written_series, bool)
        or not isinstance(written_series, int)
        or written_series < 0
        or isinstance(written_versions, bool)
        or not isinstance(written_versions, int)
        or written_versions < 0
        or (
            outcome == "unchanged"
            and (written_series != 0 or written_versions != 0)
        )
    ):
        raise ConflictError("NY Fed publisher outcome is invalid")
    return outcome, written_series, written_versions


class NyFedOvernightRatesHistoryRunner:
    """Fetch and publish exactly one bounded NY Fed response."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseNyFedOvernightRates,
        publisher_factory: PublisherFactory,
        transport: FmpMacroCalendarTransport,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._parser = parser
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._utcnow = utcnow
        _require_bound_target(
            project_root=self._project_root,
            macro_store=self._macro_store,
            canonical=_canonical,
        )
        if (
            not callable(self._parser)
            or not callable(self._publisher_factory)
            or not callable(getattr(self._transport, "request", None))
            or not callable(self._utcnow)
        ):
            raise ValidationError("NY Fed runner dependencies are invalid")

    def _request(
        self, window: NyFedOvernightRatesWindow
    ) -> FmpMacroCalendarTransportResponse:
        response = self._transport.request(
            url=NYFED_OVERNIGHT_RATES_URL,
            parameters=window.parameters,
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
            },
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        return _validate_response(response)

    def run(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
    ) -> NyFedOvernightRatesHistoryReport:
        window = NyFedOvernightRatesWindow(
            start_date=_as_date(start_date, name="start_date"),
            end_date=_as_date(end_date, name="end_date"),
        )
        publisher = self._publisher_factory()
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("NY Fed publisher is invalid")

        response = self._request(window)
        capture = self._parser(
            response.body,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        outcome, written_series, written_versions = _publication_result(
            publisher.publish(capture)
        )
        return NyFedOvernightRatesHistoryReport(
            requested=1,
            published=int(outcome == "published"),
            unchanged=int(outcome == "unchanged"),
            written_series=written_series,
            written_observation_versions=written_versions,
            start_date=window.start_date,
            end_date=window.end_date,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )


def _domain_api() -> tuple[ParseNyFedOvernightRates, PublisherFactory]:
    from ..macro.nyfed_overnight_rates import (
        NyFedOvernightRatesPublisher,
        parse_nyfed_overnight_rates,
    )

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> NyFedOvernightRatesPublisher:
        return NyFedOvernightRatesPublisher(
            project_root=PROJECT_ROOT,
            macro_store=MACRO_STORE,
            registry=registry,
            _canonical=True,
        )

    return parse_nyfed_overnight_rates, publisher_factory


def populate_nyfed_overnight_rates_live(
    *, start_date: date | str, end_date: date | str
) -> NyFedOvernightRatesHistoryReport:
    """Run one explicit canonical NY Fed history request."""

    _require_canonical_target()
    parser, publisher_factory = _domain_api()
    return NyFedOvernightRatesHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        _canonical=True,
    ).run(start_date=start_date, end_date=end_date)


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        report = populate_nyfed_overnight_rates_live(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except (RegistryError, ConflictError):
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
                "contract": "quant_data.nyfed_overnight_rates_history_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = (
    "MACRO_STORE",
    "MAX_WINDOW_DAYS",
    "NYFED_OVERNIGHT_RATES_HISTORY_COLLECTOR_ID",
    "NYFED_OVERNIGHT_RATES_HISTORY_HANDLER",
    "NYFED_OVERNIGHT_RATES_URL",
    "NyFedOvernightRatesHistoryReport",
    "NyFedOvernightRatesHistoryRunner",
    "NyFedOvernightRatesWindow",
    "PROJECT_ROOT",
    "populate_nyfed_overnight_rates_live",
)
