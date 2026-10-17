"""Manual-only, bounded Treasury securities-auction history operation."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import stat
import sys
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
from ..macro.treasury_securities_auctions import (
    TREASURY_SECURITIES_AUCTIONS_COLLECTOR_ID,
    TREASURY_SECURITIES_AUCTIONS_FIELDS,
    TREASURY_SECURITIES_AUCTIONS_HANDLER,
    TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY,
)
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from .fmp_macro_calendar_history import MACRO_STORE, PROJECT_ROOT
from .official_conditions_history import (
    CsvResponse,
    CsvTransport,
    StdlibCsvTransport,
    _validated_response,
)


TREASURY_SECURITIES_AUCTIONS_URL: Final = (
    "https://api.fiscaldata.treasury.gov/services/api/"
    "fiscal_service/v1/accounting/od/auctions_query"
)
MAX_WINDOW_DAYS: Final = 366
PAGE_SIZE: Final = 1_000
TIMEOUT_SECONDS: Final = 60
USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class TreasurySecuritiesAuctionsWindow:
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
            raise ValidationError("Treasury securities auction window is invalid")

    @property
    def url(self) -> str:
        query = urlencode(
            {
                "filter": (
                    f"auction_date:gte:{self.start_date.isoformat()},"
                    f"auction_date:lte:{self.end_date.isoformat()}"
                ),
                "fields": ",".join(TREASURY_SECURITIES_AUCTIONS_FIELDS),
                "sort": "auction_date,cusip",
                "page[size]": str(PAGE_SIZE),
            }
        )
        return f"{TREASURY_SECURITIES_AUCTIONS_URL}?{query}"


@dataclass(frozen=True, slots=True)
class TreasurySecuritiesAuctionsHistoryReport:
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


ParseTreasurySecuritiesAuctions = Callable[..., object]
PublisherFactory = Callable[[], _Publisher]


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
        raise ValidationError("Treasury securities auction capture time must include an offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Treasury securities auction target is unavailable") from exc
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
        raise ValidationError("Treasury securities auction target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError("Treasury securities auction canonical target is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("Treasury securities auction fixtures must use a temporary root") from exc


def _response_body(response: object) -> bytes:
    if not isinstance(response, CsvResponse):
        raise StoreUnavailableError("Treasury securities auction transport is invalid")
    media_type = response.media_type.split(";", 1)[0].strip().casefold()
    if media_type != "application/json":
        raise StoreUnavailableError("Treasury securities auction media type is invalid")
    return _validated_response(response)


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
        raise ConflictError("Treasury securities auction publisher outcome is invalid")
    return outcome, written_series, written_versions


class TreasurySecuritiesAuctionsHistoryRunner:
    """Fetch, parse, then publish exactly one bounded source page."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseTreasurySecuritiesAuctions,
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
            not callable(self._parser)
            or not callable(self._publisher_factory)
            or not callable(getattr(self._transport, "request", None))
            or not callable(self._utcnow)
        ):
            raise ValidationError("Treasury securities auction runner dependencies are invalid")

    def run(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
    ) -> TreasurySecuritiesAuctionsHistoryReport:
        window = TreasurySecuritiesAuctionsWindow(
            _as_date(start_date, name="start_date"),
            _as_date(end_date, name="end_date"),
        )
        response = self._transport.request(
            url=window.url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout_seconds=TIMEOUT_SECONDS,
            max_bytes=16 * 1024 * 1024,
        )
        body = _response_body(response)
        capture = self._parser(
            body,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        publisher = self._publisher_factory()
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("Treasury securities auction publisher is invalid")
        outcome, written_series, written_versions = _publication_result(
            publisher.publish(capture)
        )
        return TreasurySecuritiesAuctionsHistoryReport(
            1,
            int(outcome == "published"),
            int(outcome == "unchanged"),
            written_series,
            written_versions,
            window.start_date,
            window.end_date,
        )


def _domain_api() -> tuple[ParseTreasurySecuritiesAuctions, PublisherFactory]:
    from ..macro.official_conditions import OfficialConditionsPublisher
    from ..macro.treasury_securities_auctions import (
        parse_treasury_securities_auctions,
    )

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> OfficialConditionsPublisher:
        return OfficialConditionsPublisher(
            source_key=TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY,
            project_root=PROJECT_ROOT,
            macro_store=MACRO_STORE,
            registry=registry,
            _canonical=True,
        )

    return parse_treasury_securities_auctions, publisher_factory


def populate_treasury_securities_auctions_history_live(
    *, start_date: date | str, end_date: date | str
) -> TreasurySecuritiesAuctionsHistoryReport:
    parser, publisher_factory = _domain_api()
    return TreasurySecuritiesAuctionsHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=StdlibCsvTransport(),
        _canonical=True,
    ).run(start_date=start_date, end_date=end_date)


class _ArgumentFailure(Exception):
    pass


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
        report = populate_treasury_securities_auctions_history_live(
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
                "contract": "quant_data.treasury_securities_auctions_history_error",
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
    "MAX_WINDOW_DAYS",
    "PAGE_SIZE",
    "TREASURY_SECURITIES_AUCTIONS_COLLECTOR_ID",
    "TREASURY_SECURITIES_AUCTIONS_HANDLER",
    "TREASURY_SECURITIES_AUCTIONS_URL",
    "TreasurySecuritiesAuctionsHistoryReport",
    "TreasurySecuritiesAuctionsHistoryRunner",
    "TreasurySecuritiesAuctionsWindow",
    "populate_treasury_securities_auctions_history_live",
)
