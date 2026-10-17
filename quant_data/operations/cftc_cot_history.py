"""Manual-only, bounded CFTC futures-only COT history operation."""

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


TFF_FUTURES_ONLY: Final = "tff_futures_only"
DISAGGREGATED_FUTURES_ONLY: Final = "disaggregated_futures_only"
CFTC_COT_URL_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
    DISAGGREGATED_FUTURES_ONLY: "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
}
CFTC_COT_HISTORY_COLLECTOR_ID_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "cftc.macro.tff_futures_only_history",
    DISAGGREGATED_FUTURES_ONLY: "cftc.macro.disaggregated_futures_only_history",
}
CFTC_COT_HISTORY_HANDLER_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "macro.cftc_tff_futures_only_history",
    DISAGGREGATED_FUTURES_ONLY: "macro.cftc_disaggregated_futures_only_history",
}
MAX_WINDOW_DAYS: Final = 21
MAX_PAGES: Final = 8
PAGE_SIZE: Final = 500
_TIMEOUT_SECONDS: Final = 60
_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class CftcCotWindow:
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
            raise ValidationError("CFTC COT window is invalid")


@dataclass(frozen=True, slots=True)
class CftcCotHistoryReport:
    requested: int
    published: int
    unchanged: int
    empty: int
    written_series: int
    written_observation_versions: int
    report_family: str
    start_date: date
    end_date: date

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "empty": self.empty,
            "written_series": self.written_series,
            "written_observation_versions": self.written_observation_versions,
            "report_family": self.report_family,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


ParseCftcCot = Callable[..., object]
PublisherFactory = Callable[[], _Publisher]


def _family(value: object) -> str:
    if not isinstance(value, str) or value not in CFTC_COT_URL_BY_FAMILY:
        raise ValidationError("CFTC COT report family is invalid")
    return value


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


def _utc_text(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError("CFTC COT capture time must include an offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("CFTC COT source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("CFTC COT source media type is invalid")
    return result


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("CFTC COT target is unavailable") from exc
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
        raise ValidationError("CFTC COT target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError("CFTC COT canonical target binding is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("CFTC COT fixtures must use a temporary root") from exc


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
        or (outcome == "unchanged" and (written_series or written_versions))
    ):
        raise ConflictError("CFTC COT publisher outcome is invalid")
    return outcome, written_series, written_versions



class CftcCotHistoryRunner:
    """Fetch all bounded pages before parser or publisher construction."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseCftcCot,
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
            not callable(parser)
            or not callable(publisher_factory)
            or not callable(getattr(transport, "request", None))
            or not callable(utcnow)
        ):
            raise ValidationError("CFTC COT runner dependencies are invalid")

    @staticmethod
    def _parameters(window: CftcCotWindow, offset: int) -> dict[str, str]:
        start = f"{window.start_date.isoformat()}T00:00:00.000"
        end = f"{window.end_date.isoformat()}T23:59:59.999"
        return {
            "$where": (
                "report_date_as_yyyy_mm_dd >= '" + start + "' AND "
                "report_date_as_yyyy_mm_dd <= '" + end + "'"
            ),
            "$order": "report_date_as_yyyy_mm_dd ASC,cftc_contract_market_code ASC",
            "$limit": str(PAGE_SIZE),
            "$offset": str(offset),
        }

    def _request(
        self,
        *,
        family: str,
        window: CftcCotWindow,
        offset: int,
        remaining_bytes: int,
    ) -> tuple[bytes, int]:
        response = self._transport.request(
            url=CFTC_COT_URL_BY_FAMILY[family],
            parameters=self._parameters(window, offset),
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
            },
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=remaining_bytes,
        )
        if not isinstance(response, FmpMacroCalendarTransportResponse):
            raise StoreUnavailableError("CFTC COT transport returned an invalid response")
        if response.redirected:
            raise StoreUnavailableError("CFTC COT provider redirect is not allowed")
        if isinstance(response.status, bool) or response.status != 200:
            raise StoreUnavailableError("CFTC COT provider response is outside policy")
        if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
            raise StoreUnavailableError("CFTC COT provider media type is invalid")
        if not isinstance(response.body, bytes) or not response.body:
            raise StoreUnavailableError("CFTC COT provider response body is invalid")
        if len(response.body) > remaining_bytes:
            raise ResourceLimitError("CFTC COT provider response exceeds its byte bound")
        try:
            payload = json.loads(response.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreUnavailableError("CFTC COT provider JSON is invalid") from exc
        if not isinstance(payload, list):
            raise StoreUnavailableError("CFTC COT provider JSON must be an array")
        return response.body, len(payload)

    def run(
        self,
        *,
        report_family: str,
        start_date: date | str,
        end_date: date | str,
    ) -> CftcCotHistoryReport:
        family = _family(report_family)
        window = CftcCotWindow(
            _as_date(start_date, name="start_date"),
            _as_date(end_date, name="end_date"),
        )
        pages: list[bytes] = []
        used_bytes = 0
        for page_number in range(MAX_PAGES):
            if used_bytes >= _MAX_RESPONSE_BYTES:
                raise ResourceLimitError("CFTC COT provider response exceeds its byte bound")
            body, rows = self._request(
                family=family,
                window=window,
                offset=page_number * PAGE_SIZE,
                remaining_bytes=_MAX_RESPONSE_BYTES - used_bytes,
            )
            used_bytes += len(body)
            pages.append(body)
            if rows == 0:
                if page_number == 0:
                    return CftcCotHistoryReport(
                        1, 0, 0, 1, 0, 0, family, window.start_date, window.end_date
                    )
                break
            if rows < PAGE_SIZE:
                break
        else:
            raise ResourceLimitError("CFTC COT provider pagination exceeds its page bound")

        capture = self._parser(
            tuple(pages),
            report_family=family,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        publisher = self._publisher_factory()
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("CFTC COT publisher is invalid")
        outcome, written_series, written_versions = _publication_result(
            publisher.publish(capture)
        )
        return CftcCotHistoryReport(
            1,
            int(outcome == "published"),
            int(outcome == "unchanged"),
            0,
            written_series,
            written_versions,
            family,
            window.start_date,
            window.end_date,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )


def _domain_api() -> tuple[ParseCftcCot, PublisherFactory]:
    from ..macro.cftc_cot import CftcCotPublisher, parse_cftc_cot

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> CftcCotPublisher:
        return CftcCotPublisher(
            project_root=PROJECT_ROOT,
            macro_store=MACRO_STORE,
            registry=registry,
            _canonical=True,
        )

    return parse_cftc_cot, publisher_factory


def populate_cftc_cot_live(
    *,
    report_family: str,
    start_date: date | str,
    end_date: date | str,
) -> CftcCotHistoryReport:
    """Run one explicit canonical, finite CFTC COT request."""

    _require_canonical_target()
    parser, publisher_factory = _domain_api()
    return CftcCotHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        _canonical=True,
    ).run(
        report_family=report_family,
        start_date=start_date,
        end_date=end_date,
    )


class _ArgumentFailure(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--report-family", required=True)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        report = populate_cftc_cot_live(
            report_family=arguments.report_family,
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
                "contract": "quant_data.cftc_cot_history_error",
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
    "CFTC_COT_HISTORY_COLLECTOR_ID_BY_FAMILY",
    "CFTC_COT_HISTORY_HANDLER_BY_FAMILY",
    "CFTC_COT_URL_BY_FAMILY",
    "CftcCotHistoryReport",
    "CftcCotHistoryRunner",
    "CftcCotWindow",
    "DISAGGREGATED_FUTURES_ONLY",
    "MAX_WINDOW_DAYS",
    "PAGE_SIZE",
    "TFF_FUTURES_ONLY",
    "populate_cftc_cot_live",
)
