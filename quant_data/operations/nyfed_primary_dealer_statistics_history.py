"""Finite, injected NY Fed Primary Dealer Statistics history operation."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import stat
import sys
import time
from typing import Final, Protocol

from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..macro.nyfed_primary_dealer_statistics import (
    COLLECTOR_ID,
    HANDLER,
    MAX_RESPONSE_BYTES,
    MAX_SERIES_KEYS,
    MAX_TOTAL_BYTES,
    MAX_WINDOW_DAYS,
    NyFedPrimaryDealerCatalogSeries,
    SOURCE_KEY,
)
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from .fmp_macro_calendar_history import (
    MACRO_STORE,
    PROJECT_ROOT,
    FmpMacroCalendarTransport,
    FmpMacroCalendarTransportResponse,
    _StdlibTransport,
)


NYFED_PRIMARY_DEALER_CATALOG_URL: Final = (
    "https://markets.newyorkfed.org/api/pd/list/timeseries.json"
)
NYFED_PRIMARY_DEALER_HISTORY_TEMPLATE: Final = (
    "https://markets.newyorkfed.org/api/pd/get/"
    "{series_break}/timeseries/{series_keys}.json"
)
NYFED_PRIMARY_DEALER_COLLECTOR_ID: Final = COLLECTOR_ID
NYFED_PRIMARY_DEALER_HANDLER: Final = HANDLER
MAX_SERIES_PER_PAGE: Final = 50
MAX_DATA_PAGES: Final = 32
MAX_REQUESTS: Final = 1 + MAX_DATA_PAGES
MAX_ELAPSED_SECONDS: Final = 180.0
_TIMEOUT_SECONDS: Final = 60
_USER_AGENT: Final = "QuantDataInfra/1.0"
_JSON_MEDIA_TYPE: Final = "application/json"
_SERIES_BREAK = re.compile(r"SBN[0-9]{4}")
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class NyFedPrimaryDealerWindow:
    series_break: str
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if (
            not isinstance(self.series_break, str)
            or _SERIES_BREAK.fullmatch(self.series_break) is None
            or not isinstance(self.start_date, date)
            or isinstance(self.start_date, datetime)
            or not isinstance(self.end_date, date)
            or isinstance(self.end_date, datetime)
            or self.start_date > self.end_date
            or (self.end_date - self.start_date).days + 1 > MAX_WINDOW_DAYS
        ):
            raise ValidationError("NY Fed primary-dealer window is invalid")

    @property
    def parameters(self) -> dict[str, str]:
        return {}


@dataclass(frozen=True, slots=True)
class NyFedPrimaryDealerHistoryReport:
    requested: int
    pages: int
    selected_series: int
    normalized_rows: int
    published: int
    unchanged: int
    written_series: int
    written_observation_versions: int
    series_break: str
    start_date: date
    end_date: date

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "pages": self.pages,
            "selected_series": self.selected_series,
            "normalized_rows": self.normalized_rows,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_series": self.written_series,
            "written_observation_versions": self.written_observation_versions,
            "series_break": self.series_break,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


ParseCatalog = Callable[..., tuple[NyFedPrimaryDealerCatalogSeries, ...]]
ParseHistory = Callable[..., object]
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
        raise ValidationError("NY Fed capture time must include an offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("NY Fed provider media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("NY Fed provider media type is invalid")
    return result


def _response(
    value: object,
) -> FmpMacroCalendarTransportResponse:
    if not isinstance(value, FmpMacroCalendarTransportResponse):
        raise StoreUnavailableError("NY Fed transport returned an invalid response")
    if value.redirected:
        raise StoreUnavailableError("NY Fed provider redirect is not allowed")
    if isinstance(value.status, bool) or value.status != 200:
        raise StoreUnavailableError("NY Fed provider response is outside policy")
    if _media_type(value.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("NY Fed provider media type is invalid")
    if not isinstance(value.body, bytes) or not value.body:
        raise StoreUnavailableError("NY Fed provider response body is invalid")
    if len(value.body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("NY Fed provider response exceeds its byte bound")
    try:
        json.loads(value.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("NY Fed provider JSON is invalid") from exc
    return value


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError(
            "NY Fed primary-dealer target is unavailable"
        ) from exc
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
        raise ValidationError(
            "NY Fed primary-dealer target binding is invalid"
        )
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError(
                "NY Fed primary-dealer canonical target binding is invalid"
            )
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError(
            "NY Fed primary-dealer fixtures must use a temporary root"
        ) from exc


def _batches(
    catalog: tuple[NyFedPrimaryDealerCatalogSeries, ...],
) -> tuple[tuple[str, ...], ...]:
    keys = tuple(item.key_id for item in catalog)
    if not keys or len(keys) > MAX_SERIES_KEYS or len(keys) != len(set(keys)):
        raise ValidationError("NY Fed primary-dealer catalog result is invalid")
    result = tuple(
        keys[offset : offset + MAX_SERIES_PER_PAGE]
        for offset in range(0, len(keys), MAX_SERIES_PER_PAGE)
    )
    if len(result) > MAX_DATA_PAGES:
        raise ResourceLimitError(
            "NY Fed primary-dealer request exceeds its page bound"
        )
    return result


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
    ):
        raise ConflictError("NY Fed primary-dealer publisher result is invalid")
    return outcome, written_series, written_versions


class NyFedPrimaryDealerHistoryRunner:
    """Fetch all bounded pages before invoking the generic macro publisher."""

    def __init__(
        self,
        *,
        project_root: Path,
        macro_store: Path,
        catalog_parser: ParseCatalog,
        parser: ParseHistory,
        publisher_factory: PublisherFactory,
        transport: FmpMacroCalendarTransport,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float],
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._catalog_parser = catalog_parser
        self._parser = parser
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._utcnow = utcnow
        self._monotonic = monotonic
        _require_bound_target(
            project_root=self._project_root,
            macro_store=self._macro_store,
            canonical=_canonical,
        )
        dependencies = (
            self._catalog_parser,
            self._parser,
            self._publisher_factory,
            getattr(self._transport, "request", None),
            self._utcnow,
            self._monotonic,
        )
        if any(not callable(item) for item in dependencies):
            raise ValidationError(
                "NY Fed primary-dealer runner dependencies are invalid"
            )

    def _request(
        self, *, url: str, parameters: dict[str, str]
    ) -> FmpMacroCalendarTransportResponse:
        return _response(
            self._transport.request(
                url=url,
                parameters=parameters,
                headers={
                    "Accept": _JSON_MEDIA_TYPE,
                    "User-Agent": _USER_AGENT,
                },
                timeout_seconds=_TIMEOUT_SECONDS,
                max_bytes=MAX_RESPONSE_BYTES,
            )
        )

    def run(
        self,
        *,
        series_break: str,
        start_date: date | str,
        end_date: date | str,
    ) -> NyFedPrimaryDealerHistoryReport:
        window = NyFedPrimaryDealerWindow(
            series_break=series_break,
            start_date=_as_date(start_date, name="start_date"),
            end_date=_as_date(end_date, name="end_date"),
        )
        started = self._monotonic()
        catalog_response = self._request(
            url=NYFED_PRIMARY_DEALER_CATALOG_URL,
            parameters={},
        )
        catalog = self._catalog_parser(
            catalog_response.body,
            series_break=window.series_break,
        )
        if self._monotonic() - started > MAX_ELAPSED_SECONDS:
            raise ResourceLimitError(
                "NY Fed primary-dealer request exceeds its time bound"
            )
        pages = _batches(catalog)
        history_bodies: list[bytes] = []
        total_bytes = len(catalog_response.body)
        for keys in pages:
            path = NYFED_PRIMARY_DEALER_HISTORY_TEMPLATE.format(
                series_break=window.series_break,
                series_keys="_".join(keys),
            )
            response = self._request(url=path, parameters=window.parameters)
            total_bytes += len(response.body)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ResourceLimitError(
                    "NY Fed primary-dealer responses exceed total byte bound"
                )
            history_bodies.append(response.body)
            if self._monotonic() - started > MAX_ELAPSED_SECONDS:
                raise ResourceLimitError(
                    "NY Fed primary-dealer request exceeds its time bound"
                )
        capture = self._parser(
            catalog_response.body,
            tuple(history_bodies),
            captured_at=_utc_text(self._utcnow()),
            series_break=window.series_break,
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        observations = getattr(capture, "observations", None)
        if not isinstance(observations, tuple):
            raise ConflictError("NY Fed primary-dealer parser result is invalid")
        publisher = self._publisher_factory()
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("NY Fed primary-dealer publisher is invalid")
        outcome, written_series, written_versions = _publication_result(
            publisher.publish(capture)
        )
        return NyFedPrimaryDealerHistoryReport(
            requested=1 + len(pages),
            pages=len(pages),
            selected_series=len(catalog),
            normalized_rows=len(observations),
            published=int(outcome == "published"),
            unchanged=int(outcome == "unchanged"),
            written_series=written_series,
            written_observation_versions=written_versions,
            series_break=window.series_break,
            start_date=window.start_date,
            end_date=window.end_date,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )


def _domain_api() -> tuple[ParseCatalog, ParseHistory, PublisherFactory]:
    from ..macro.nyfed_primary_dealer_statistics import (
        parse_nyfed_primary_dealer_catalog,
        parse_nyfed_primary_dealer_statistics,
    )
    from ..macro.official_conditions import OfficialConditionsPublisher

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> OfficialConditionsPublisher:
        return OfficialConditionsPublisher(
            source_key=SOURCE_KEY,
            macro_store=MACRO_STORE,
            project_root=PROJECT_ROOT,
            registry=registry,
            _canonical=True,
        )

    return (
        parse_nyfed_primary_dealer_catalog,
        parse_nyfed_primary_dealer_statistics,
        publisher_factory,
    )


def populate_nyfed_primary_dealer_statistics_live(
    *,
    series_break: str,
    start_date: date | str,
    end_date: date | str,
) -> NyFedPrimaryDealerHistoryReport:
    """Run one explicit canonical, finite NY Fed primary-dealer request."""

    _require_canonical_target()
    catalog_parser, parser, publisher_factory = _domain_api()
    return NyFedPrimaryDealerHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        catalog_parser=catalog_parser,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        monotonic=time.monotonic,
        _canonical=True,
    ).run(
        series_break=series_break,
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
    parser.add_argument("--series-break", required=True)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        report = populate_nyfed_primary_dealer_statistics_live(
            series_break=arguments.series_break,
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
                "contract": "quant_data.nyfed_primary_dealer_statistics_history_error",
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
    "MAX_DATA_PAGES",
    "MAX_ELAPSED_SECONDS",
    "MAX_REQUESTS",
    "MAX_SERIES_PER_PAGE",
    "NYFED_PRIMARY_DEALER_CATALOG_URL",
    "NYFED_PRIMARY_DEALER_COLLECTOR_ID",
    "NYFED_PRIMARY_DEALER_HANDLER",
    "NYFED_PRIMARY_DEALER_HISTORY_TEMPLATE",
    "NyFedPrimaryDealerHistoryReport",
    "NyFedPrimaryDealerHistoryRunner",
    "NyFedPrimaryDealerWindow",
    "populate_nyfed_primary_dealer_statistics_live",
)
