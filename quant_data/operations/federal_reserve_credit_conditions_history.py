"""Manual-only, bounded H.8/SLOOS FRED graph CSV history operation.

The injected runner fetches every fixed singleton FRED response before the
generic official-conditions publisher constructs its canonical write path.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import stat
import sys
import tempfile
from typing import Final, Protocol
from urllib.parse import urlencode

from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..macro.federal_reserve_credit_conditions import (
    H8_COLLECTOR_ID,
    H8_HANDLER,
    H8_SOURCE_KEY,
    MAX_RESPONSE_BYTES,
    MAX_TOTAL_RESPONSE_BYTES,
    MAX_WINDOW_DAYS,
    SLOOS_COLLECTOR_ID,
    SLOOS_HANDLER,
    SLOOS_SOURCE_KEY,
    SOURCE_METADATA_BY_KEY,
    FederalReserveCreditSourceMetadata,
)
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from .fmp_macro_calendar_history import MACRO_STORE, PROJECT_ROOT
from .official_conditions_history import (
    CsvResponse,
    CsvTransport,
    StdlibCsvTransport,
)


FRED_GRAPH_URL: Final = "https://fred.stlouisfed.org/graph/fredgraph.csv"
H8_HISTORY_COLLECTOR_ID: Final = H8_COLLECTOR_ID
SLOOS_HISTORY_COLLECTOR_ID: Final = SLOOS_COLLECTOR_ID
H8_HISTORY_HANDLER: Final = H8_HANDLER
SLOOS_HISTORY_HANDLER: Final = SLOOS_HANDLER
MAX_REQUESTS: Final = max(
    item.max_requests for item in SOURCE_METADATA_BY_KEY.values()
)
_TIMEOUT_SECONDS: Final = 60
_USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"
_ACCEPTED_MEDIA_TYPES: Final = frozenset(
    {
        "text/csv",
        "application/csv",
        "application/octet-stream",
        "text/plain",
    }
)


@dataclass(frozen=True, slots=True)
class FederalReserveCreditWindow:
    source_key: str
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_key, str)
            or self.source_key not in SOURCE_METADATA_BY_KEY
            or not isinstance(self.start_date, date)
            or isinstance(self.start_date, datetime)
            or not isinstance(self.end_date, date)
            or isinstance(self.end_date, datetime)
            or self.start_date > self.end_date
            or (self.end_date - self.start_date).days + 1 > MAX_WINDOW_DAYS
        ):
            raise ValidationError("Federal Reserve credit window is invalid")

    @property
    def metadata(self) -> FederalReserveCreditSourceMetadata:
        return SOURCE_METADATA_BY_KEY[self.source_key]

    def parameters_for(self, series_code: str) -> dict[str, str]:
        if (
            not isinstance(series_code, str)
            or series_code not in self.metadata.fred_series
        ):
            raise ValidationError(
                "Federal Reserve credit series is outside the fixed manifest"
            )
        return {
            "id": series_code,
            "cosd": self.start_date.isoformat(),
            "coed": self.end_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class FederalReserveCreditHistoryReport:
    requested: int
    published: int
    unchanged: int
    normalized_rows: int
    written_series: int
    written_observation_versions: int
    source_key: str
    start_date: date
    end_date: date

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "normalized_rows": self.normalized_rows,
            "written_series": self.written_series,
            "written_observation_versions": self.written_observation_versions,
            "source_key": self.source_key,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


ParseFederalReserveCredit = Callable[..., object]
PublisherFactory = Callable[[str], _Publisher]


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
        raise ValidationError("Federal Reserve credit capture time must include an offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("Federal Reserve credit source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("Federal Reserve credit source media type is invalid")
    return result


def _fred_graph_url(
    window: FederalReserveCreditWindow, *, series_code: str
) -> str:
    """Build one fixed FRED graph URL for a manifest member."""

    return FRED_GRAPH_URL + "?" + urlencode(window.parameters_for(series_code))


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Federal Reserve credit target is unavailable") from exc
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
        raise ValidationError("Federal Reserve credit target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError(
                "Federal Reserve credit canonical target binding is invalid"
            )
        return
    try:
        project_root.relative_to(Path(tempfile.gettempdir()).resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("Federal Reserve credit fixtures must use a temporary root") from exc


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
        raise ConflictError("Federal Reserve credit publisher outcome is invalid")
    return outcome, written_series, written_versions



class FederalReserveCreditHistoryRunner:
    """Fetch all bounded singleton FRED CSVs before invoking the publisher."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseFederalReserveCredit,
        publisher_factory: PublisherFactory,
        transport: CsvTransport,
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
            raise ValidationError("Federal Reserve credit runner dependencies are invalid")

    def _request(
        self,
        window: FederalReserveCreditWindow,
        *,
        series_code: str,
        remaining_bytes: int,
    ) -> bytes:
        if (
            isinstance(remaining_bytes, bool)
            or not isinstance(remaining_bytes, int)
            or remaining_bytes <= 0
            or remaining_bytes > MAX_TOTAL_RESPONSE_BYTES
        ):
            raise ValidationError(
                "Federal Reserve credit remaining byte budget is invalid"
            )
        max_bytes = min(MAX_RESPONSE_BYTES, remaining_bytes)
        response = self._transport.request(
            url=_fred_graph_url(window, series_code=series_code),
            headers={
                "Accept": "text/csv",
                "User-Agent": _USER_AGENT,
            },
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=max_bytes,
        )
        if not isinstance(response, CsvResponse):
            raise StoreUnavailableError(
                "Federal Reserve credit transport returned an invalid response"
            )
        if response.redirected:
            raise StoreUnavailableError(
                "Federal Reserve credit provider redirect is not allowed"
            )
        if isinstance(response.status, bool) or response.status != 200:
            raise StoreUnavailableError(
                "Federal Reserve credit provider response is outside policy"
            )
        if _media_type(response.media_type) not in _ACCEPTED_MEDIA_TYPES:
            raise StoreUnavailableError(
                "Federal Reserve credit provider media type is invalid"
            )
        if not isinstance(response.body, bytes) or not response.body:
            raise StoreUnavailableError(
                "Federal Reserve credit provider response body is invalid"
            )
        if len(response.body) > max_bytes:
            raise ResourceLimitError(
                "Federal Reserve credit provider responses exceed their byte bound"
            )
        return response.body


    def run(
        self,
        *,
        source_key: str,
        start_date: date | str,
        end_date: date | str,
    ) -> FederalReserveCreditHistoryReport:
        window = FederalReserveCreditWindow(
            source_key=source_key,
            start_date=_as_date(start_date, name="start_date"),
            end_date=_as_date(end_date, name="end_date"),
        )
        bodies: list[bytes] = []
        remaining_bytes = MAX_TOTAL_RESPONSE_BYTES
        for series_code in window.metadata.fred_series:
            body = self._request(
                window,
                series_code=series_code,
                remaining_bytes=remaining_bytes,
            )
            bodies.append(body)
            remaining_bytes -= len(body)
        capture = self._parser(
            tuple(bodies),
            source_key=window.source_key,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        observations = getattr(capture, "observations", None)
        if not isinstance(observations, tuple):
            raise ConflictError("Federal Reserve credit parser result is invalid")
        publisher = self._publisher_factory(window.source_key)
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("Federal Reserve credit publisher is invalid")
        outcome, written_series, written_versions = _publication_result(
            publisher.publish(capture)
        )
        return FederalReserveCreditHistoryReport(
            requested=len(window.metadata.fred_series),
            published=int(outcome == "published"),
            unchanged=int(outcome == "unchanged"),
            normalized_rows=len(observations),
            written_series=written_series,
            written_observation_versions=written_versions,
            source_key=window.source_key,
            start_date=window.start_date,
            end_date=window.end_date,
        )

    def run_h8(
        self, *, start_date: date | str, end_date: date | str
    ) -> FederalReserveCreditHistoryReport:
        return self.run(
            source_key=H8_SOURCE_KEY,
            start_date=start_date,
            end_date=end_date,
        )

    def run_sloos(
        self, *, start_date: date | str, end_date: date | str
    ) -> FederalReserveCreditHistoryReport:
        return self.run(
            source_key=SLOOS_SOURCE_KEY,
            start_date=start_date,
            end_date=end_date,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )


def _domain_api() -> tuple[ParseFederalReserveCredit, PublisherFactory]:
    from ..macro.federal_reserve_credit_conditions import (
        parse_federal_reserve_credit_conditions,
    )
    from ..macro.official_conditions import OfficialConditionsPublisher

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory(source_key: str) -> OfficialConditionsPublisher:
        return OfficialConditionsPublisher(
            source_key=source_key,
            macro_store=MACRO_STORE,
            project_root=PROJECT_ROOT,
            registry=registry,
            _canonical=True,
        )

    return parse_federal_reserve_credit_conditions, publisher_factory


def populate_federal_reserve_credit_conditions_live(
    *,
    source_key: str,
    start_date: date | str,
    end_date: date | str,
) -> FederalReserveCreditHistoryReport:
    """Run one explicit canonical, finite H.8 or SLOOS collection."""

    _require_canonical_target()
    parser, publisher_factory = _domain_api()
    return FederalReserveCreditHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=StdlibCsvTransport(),
        _canonical=True,
    ).run(
        source_key=source_key,
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
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        report = populate_federal_reserve_credit_conditions_live(
            source_key=arguments.source_key,
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
                "contract": "quant_data.federal_reserve_credit_conditions_history_error",
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
    "FRED_GRAPH_URL",
    "FederalReserveCreditHistoryReport",
    "FederalReserveCreditHistoryRunner",
    "FederalReserveCreditWindow",
    "H8_HISTORY_COLLECTOR_ID",
    "H8_HISTORY_HANDLER",
    "MAX_REQUESTS",
    "SLOOS_HISTORY_COLLECTOR_ID",
    "SLOOS_HISTORY_HANDLER",
    "populate_federal_reserve_credit_conditions_live",
)
