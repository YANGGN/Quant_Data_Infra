"""Manual-only bounded fetches for fixed official macro sources."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from email.message import Message
import os
from pathlib import Path
import stat
import sys
from typing import Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..macro.official_conditions import (
    BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
    MAX_RESPONSE_BYTES,
    OfficialConditionsPublisher,
    OfficialConditionsPublishReport,
    parse_bls_price_wage_productivity,
    parse_bis_credit_conditions,
    parse_chicagofed_financial_conditions,
    parse_federal_reserve_h41,
    parse_nyfed_cmdi,
    parse_eia_natural_gas_storage,
    parse_nber_us_recession,
    parse_treasury_tga,
)
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from .fmp_macro_calendar_history import MACRO_STORE, PROJECT_ROOT


FRED_H41_URL: Final = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CHICAGO_NFCI_URL: Final = (
    "https://api.data.chicagofed.org/NFCI/nfci-data-series-csv.csv"
)
BIS_CREDIT_GAP_URL: Final = (
    "https://stats.bis.org/api/v2/data/dataflow/BIS/"
    "WS_CREDIT_GAP/1.0/Q.US.P.A.A+C"
)
BIS_DSR_URL: Final = (
    "https://stats.bis.org/api/v2/data/dataflow/BIS/"
    "WS_DSR/1.0/Q.US.P"
)
NYFED_CMDI_URL: Final = (
    "https://www.newyorkfed.org/medialibrary/research/interactives/"
    "data/cmdi/cmdi_interactive_data.xlsx"
)
TREASURY_TGA_URL: Final = (
    "https://api.fiscaldata.treasury.gov/services/api/"
    "fiscal_service/v1/accounting/dts/operating_cash_balance"
)
EIA_NATURAL_GAS_STORAGE_URL: Final = (
    "https://api.eia.gov/v2/seriesid/NG.NW2_EPG0_SWO_R48_BCF.W"
)
NBER_BUSINESS_CYCLE_DATES_URL: Final = (
    "https://data.nber.org/cycles/business_cycle_dates.json"
)
BLS_PRICE_WAGE_PRODUCTIVITY_URL: Final = (
    "https://api.bls.gov/publicAPI/v2/timeseries/data/"
)
_TIMEOUT_SECONDS: Final = 60
_USER_AGENT: Final = "QuantDataInfra/1.0"
_ACCEPTED_MEDIA_TYPES: Final = frozenset(
    {
        "text/csv",
        "application/csv",
        "application/octet-stream",
        "application/json",
        "application/vnd.ms-excel",
        "text/plain",
        "application/zip",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)


@dataclass(frozen=True, slots=True)
class CsvResponse:
    status: int
    media_type: str
    body: bytes
    redirected: bool


class CsvTransport(Protocol):
    def request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
        method: str = "GET",
        body: bytes | None = None,
    ) -> CsvResponse: ...


class StdlibCsvTransport:
    """One-attempt standard-library CSV transport."""

    def request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
        method: str = "GET",
        body: bytes | None = None,
    ) -> CsvResponse:
        if (
            method not in {"GET", "POST"}
            or (method == "GET" and body is not None)
            or (body is not None and not isinstance(body, bytes))
        ):
            raise ValidationError("Official provider request is invalid")
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with build_opener().open(
                request, timeout=timeout_seconds
            ) as response:
                body = response.read(max_bytes + 1)
                media_type = _content_type(response.headers)
                return CsvResponse(
                    status=int(response.status),
                    media_type=media_type,
                    body=body,
                    redirected=response.geturl() != url,
                )
        except HTTPError as exc:
            return CsvResponse(
                status=int(exc.code),
                media_type=_content_type(exc.headers),
                body=exc.read(max_bytes + 1),
                redirected=False,
            )
        except (OSError, TimeoutError, URLError) as exc:
            raise StoreUnavailableError(
                "Official CSV provider request failed"
            ) from exc


def _content_type(headers: Message | None) -> str:
    if headers is None:
        return ""
    value = headers.get_content_type()
    return value.casefold() if isinstance(value, str) else ""


def _validated_response(response: object) -> bytes:
    if not isinstance(response, CsvResponse):
        raise StoreUnavailableError(
            "Official CSV transport returned an invalid response"
        )
    if (
        response.redirected
        or isinstance(response.status, bool)
        or response.status != 200
        or response.media_type.casefold() not in _ACCEPTED_MEDIA_TYPES
        or not isinstance(response.body, bytes)
        or not response.body
    ):
        raise StoreUnavailableError(
            "Official CSV provider response is outside policy"
        )
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError(
            "Official CSV provider response exceeds its byte bound"
        )
    return response.body


def _utc_text(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            "Official conditions capture time must include an offset"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _date_text(value: str, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be an ISO calendar date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValidationError(
            f"{name} must be an ISO calendar date"
        ) from exc


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
            "Official conditions target is unavailable"
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
            "Official conditions target binding is invalid"
        )
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError(
                "Official conditions canonical target binding is invalid"
            )
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError(
            "Official conditions fixtures must use a temporary root"
        ) from exc


def _h41_url(start_date: str, end_date: str) -> str:
    query = urlencode(
        {
            "id": "WALCL,WRESBAL,WTREGEN",
            "cosd": start_date,
            "coed": end_date,
        }
    )
    return f"{FRED_H41_URL}?{query}"


def _bis_url(base: str, start_period: str, end_period: str) -> str:
    return (
        f"{base}?"
        + urlencode(
            {
                "format": "csv",
                "detail": "dataonly",
                "startPeriod": start_period,
                "endPeriod": end_period,
            }
        )
    )


PublisherFactory = Callable[[str], OfficialConditionsPublisher]


def _treasury_tga_url(start_date: str, end_date: str) -> str:
    query = urlencode(
        {
            "filter": (
                f"record_date:gte:{start_date},record_date:lte:{end_date},"
                "account_type:eq:Treasury General Account "
                "(TGA) Closing Balance"
            ),
            "fields": "record_date,account_type,open_today_bal",
            "sort": "record_date",
            "page[size]": "10000",
        }
    )
    return f"{TREASURY_TGA_URL}?{query}"


def _eia_natural_gas_storage_url(api_key: str) -> str:
    query = urlencode({"api_key": api_key})
    return f"{EIA_NATURAL_GAS_STORAGE_URL}?{query}"


class OfficialConditionsHistoryRunner:
    """Fetch, parse, then publish one selected fixed source."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        registry: Registry,
        transport: CsvTransport,
        publisher_factory: PublisherFactory,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._registry = registry
        self._transport = transport
        self._publisher_factory = publisher_factory
        self._utcnow = utcnow
        _require_bound_target(
            project_root=self._project_root,
            macro_store=self._macro_store,
            canonical=_canonical,
        )
        if (
            not isinstance(registry, Registry)
            or not callable(getattr(transport, "request", None))
            or not callable(publisher_factory)
            or not callable(utcnow)
        ):
            raise ValidationError(
                "Official conditions runner dependencies are invalid"
            )

    def _request(
        self,
        url: str,
        *,
        accept: str = "text/csv",
        method: str = "GET",
        body: bytes | None = None,
    ) -> bytes:
        headers = {
            "Accept": accept,
            "User-Agent": _USER_AGENT,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport.request(
            url=url,
            headers=headers,
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=MAX_RESPONSE_BYTES,
            method=method,
            body=body,
        )
        return _validated_response(response)

    def _read_eia_api_key_once(self) -> str:
        try:
            value = read_project_credential(
                project_root=self._project_root,
                name="EIA_API_KEY",
                environment=os.environ,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("EIA credential is missing or invalid") from exc
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(
                character.isspace()
                or ord(character) < 32
                or ord(character) == 127
                for character in value
            )
        ):
            raise ValidationError("EIA credential is missing or invalid")
        return value


    def _publisher(self, source_key: str) -> OfficialConditionsPublisher:
        publisher = self._publisher_factory(source_key)
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError(
                "Official conditions publisher is invalid"
            )
        return publisher

    def run_h41(
        self, *, start_date: str, end_date: str
    ) -> OfficialConditionsPublishReport:
        start = _date_text(start_date, name="start_date")
        end = _date_text(end_date, name="end_date")
        if start > end:
            raise ValidationError("H.4.1 request window is invalid")
        body = self._request(_h41_url(start, end))
        capture = parse_federal_reserve_h41(
            body,
            captured_at=_utc_text(self._utcnow()),
            start_date=start,
            end_date=end,
        )
        return self._publisher("h41").publish(capture)

    def run_chicago(
        self, *, start_date: str, end_date: str
    ) -> OfficialConditionsPublishReport:
        start = _date_text(start_date, name="start_date")
        end = _date_text(end_date, name="end_date")
        if start > end:
            raise ValidationError(
                "Chicago Fed request window is invalid"
            )
        body = self._request(CHICAGO_NFCI_URL)
        capture = parse_chicagofed_financial_conditions(
            body,
            captured_at=_utc_text(self._utcnow()),
            start_date=start,
            end_date=end,
        )
        return self._publisher("chicago").publish(capture)

    def run_bis(
        self, *, start_period: str, end_period: str
    ) -> OfficialConditionsPublishReport:
        credit_gap = self._request(
            _bis_url(
                BIS_CREDIT_GAP_URL,
                start_period,
                end_period,
            )
        )
        dsr = self._request(
            _bis_url(BIS_DSR_URL, start_period, end_period)
        )
        capture = parse_bis_credit_conditions(
            credit_gap,
            dsr,
            captured_at=_utc_text(self._utcnow()),
            start_period=start_period,
            end_period=end_period,
        )
        return self._publisher("bis").publish(capture)


    def run_cmdi(
        self, *, start_date: str, end_date: str
    ) -> OfficialConditionsPublishReport:
        start = _date_text(start_date, name="start_date")
        end = _date_text(end_date, name="end_date")
        if start > end:
            raise ValidationError(
                "NY Fed CMDI request window is invalid"
            )
        body = self._request(
            NYFED_CMDI_URL,
            accept=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        capture = parse_nyfed_cmdi(
            body,
            captured_at=_utc_text(self._utcnow()),
            start_date=start,
            end_date=end,
        )
        return self._publisher("cmdi").publish(capture)


    def run_treasury_tga(
        self, *, start_date: str, end_date: str
    ) -> OfficialConditionsPublishReport:
        """Fetch one complete bounded Treasury TGA page."""

        start = _date_text(start_date, name="start_date")
        end = _date_text(end_date, name="end_date")
        if start > end:
            raise ValidationError("Treasury TGA request window is invalid")
        body = self._request(
            _treasury_tga_url(start, end), accept="application/json"
        )
        capture = parse_treasury_tga(
            body,
            captured_at=_utc_text(self._utcnow()),
            start_date=start,
            end_date=end,
        )
        return self._publisher("treasury_tga").publish(capture)


    def run_eia_natural_gas_storage(
        self,
    ) -> OfficialConditionsPublishReport:
        """Fetch the fixed full-history EIA legacy series once."""

        api_key = self._read_eia_api_key_once()
        body = self._request(
            _eia_natural_gas_storage_url(api_key),
            accept="application/json",
        )
        capture = parse_eia_natural_gas_storage(
            body,
            captured_at=_utc_text(self._utcnow()),
            credential=api_key,
        )
        return self._publisher("eia_gas").publish(capture)


    def run_nber_us_recession(
        self,
    ) -> OfficialConditionsPublishReport:
        """Fetch the fixed NBER turning-point JSON once."""

        body = self._request(
            NBER_BUSINESS_CYCLE_DATES_URL, accept="application/json"
        )
        capture = parse_nber_us_recession(
            body, captured_at=_utc_text(self._utcnow())
        )
        return self._publisher("nber_recession").publish(capture)

    def run_bls_price_wage_productivity(
        self,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        series_codes: tuple[str, ...] | None = None,
    ) -> OfficialConditionsPublishReport:
        """Fetch one fixed credential-free BLS series window."""

        captured = self._utcnow()
        manifest_codes = tuple(
            item.provider_code
            for item in BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST
        )
        if start_year is None and end_year is None and series_codes is None:
            end_year = captured.year
            start_year = end_year - 9
            requested_series = manifest_codes
        elif start_year is None or end_year is None or series_codes is None:
            raise ValidationError("BLS history scope must be complete")
        else:
            if (
                isinstance(start_year, bool)
                or isinstance(end_year, bool)
                or not isinstance(start_year, int)
                or not isinstance(end_year, int)
                or start_year > end_year
                or end_year - start_year > 9
            ):
                raise ValidationError("BLS history window is invalid")
            if (
                not isinstance(series_codes, tuple)
                or not series_codes
                or len(set(series_codes)) != len(series_codes)
                or any(code not in manifest_codes for code in series_codes)
            ):
                raise ValidationError("BLS history series scope is invalid")
            requested_series = series_codes
        request_body = dumps_strict(
            {
                "seriesid": list(requested_series),
                "startyear": str(start_year),
                "endyear": str(end_year),
            }
        ).encode("utf-8")
        body = self._request(
            BLS_PRICE_WAGE_PRODUCTIVITY_URL,
            accept="application/json",
            method="POST",
            body=request_body,
        )
        capture = parse_bls_price_wage_productivity(
            body,
            captured_at=_utc_text(captured),
            start_year=start_year,
            end_year=end_year,
            series_codes=requested_series,
        )
        return self._publisher("bls_price_wage_productivity").publish(capture)


def _live_runner() -> OfficialConditionsHistoryRunner:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )
    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher(source_key: str) -> OfficialConditionsPublisher:
        return OfficialConditionsPublisher(
            source_key=source_key,
            project_root=PROJECT_ROOT,
            macro_store=MACRO_STORE,
            registry=registry,
            _canonical=True,
        )

    return OfficialConditionsHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        registry=registry,
        transport=StdlibCsvTransport(),
        publisher_factory=publisher,
        _canonical=True,
    )


def populate_federal_reserve_h41_live(
    *, start_date: str, end_date: str
) -> OfficialConditionsPublishReport:
    return _live_runner().run_h41(
        start_date=start_date, end_date=end_date
    )


def populate_chicagofed_financial_conditions_live(
    *, start_date: str, end_date: str
) -> OfficialConditionsPublishReport:
    return _live_runner().run_chicago(
        start_date=start_date, end_date=end_date
    )


def populate_bis_credit_conditions_live(
    *, start_period: str, end_period: str
) -> OfficialConditionsPublishReport:
    return _live_runner().run_bis(
        start_period=start_period, end_period=end_period
    )


def populate_nyfed_cmdi_live(
    *, start_date: str, end_date: str
) -> OfficialConditionsPublishReport:
    return _live_runner().run_cmdi(
        start_date=start_date, end_date=end_date
    )


def populate_treasury_tga_live(
    *, start_date: str, end_date: str
) -> OfficialConditionsPublishReport:
    return _live_runner().run_treasury_tga(
        start_date=start_date, end_date=end_date
    )


def populate_eia_natural_gas_storage_live(
) -> OfficialConditionsPublishReport:
    return _live_runner().run_eia_natural_gas_storage()


def populate_nber_us_recession_live(
) -> OfficialConditionsPublishReport:
    return _live_runner().run_nber_us_recession()


def populate_bls_price_wage_productivity_live(
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    series_codes: tuple[str, ...] | None = None,
) -> OfficialConditionsPublishReport:
    return _live_runner().run_bls_price_wage_productivity(
        start_year=start_year,
        end_year=end_year,
        series_codes=series_codes,
    )


class _ArgumentFailure(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument(
        "source",
        choices=("h41", "chicago", "bis", "cmdi", "treasury_tga", "eia_gas",
                 "nber_recession", "bls_price_wage_productivity"),
    )
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--series")
    return parser


def _year(value: str) -> int:
    if (
        len(value) != 4
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise _ArgumentFailure
    return int(value)


def _series_codes(value: str) -> tuple[str, ...]:
    codes = tuple(item.strip() for item in value.split(","))
    if not codes or any(not code for code in codes):
        raise _ArgumentFailure
    return codes


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if (
            arguments.source != "bls_price_wage_productivity"
            and arguments.series is not None
        ):
            raise _ArgumentFailure
        if arguments.source == "eia_gas":
            if arguments.start is not None or arguments.end is not None:
                raise _ArgumentFailure
            report = populate_eia_natural_gas_storage_live()
        elif arguments.source == "nber_recession":
            if arguments.start is not None or arguments.end is not None:
                raise _ArgumentFailure
            report = populate_nber_us_recession_live()
        elif arguments.source == "bls_price_wage_productivity":
            if (
                arguments.start is None
                and arguments.end is None
                and arguments.series is None
            ):
                report = populate_bls_price_wage_productivity_live()
            elif (
                arguments.start is None
                or arguments.end is None
                or arguments.series is None
            ):
                raise _ArgumentFailure
            else:
                report = populate_bls_price_wage_productivity_live(
                    start_year=_year(arguments.start),
                    end_year=_year(arguments.end),
                    series_codes=_series_codes(arguments.series),
                )
        else:
            if arguments.start is None or arguments.end is None:
                raise _ArgumentFailure
            if arguments.source == "h41":
                report = populate_federal_reserve_h41_live(
                    start_date=arguments.start,
                    end_date=arguments.end,
                )
            elif arguments.source == "chicago":
                report = populate_chicagofed_financial_conditions_live(
                    start_date=arguments.start,
                    end_date=arguments.end,
                )
            elif arguments.source == "bis":
                report = populate_bis_credit_conditions_live(
                    start_period=arguments.start,
                    end_period=arguments.end,
                )
            elif arguments.source == "cmdi":
                report = populate_nyfed_cmdi_live(
                    start_date=arguments.start,
                    end_date=arguments.end,
                )
            elif arguments.source == "treasury_tga":
                report = populate_treasury_tga_live(
                    start_date=arguments.start,
                    end_date=arguments.end,
                )
            else:
                raise _ArgumentFailure
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except ResourceLimitError:
        code, name = 74, "local_io"
    except (RegistryError, ConflictError):
        code, name = 75, "temporary_conflict"
    except Exception:
        code, name = 70, "internal_failure"
    else:
        sys.stdout.write(dumps_strict(asdict(report)) + "\n")
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.official_conditions_history_error",
                "error": name,
                "exit_code": code,
                "version": "1.0.0",
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = (
    "BIS_CREDIT_GAP_URL",
    "BIS_DSR_URL",
    "BLS_PRICE_WAGE_PRODUCTIVITY_URL",
    "CHICAGO_NFCI_URL",
    "NYFED_CMDI_URL",
    "CsvResponse",
    "CsvTransport",
    "FRED_H41_URL",
    "OfficialConditionsHistoryRunner",
    "StdlibCsvTransport",
    "populate_bis_credit_conditions_live",
    "populate_chicagofed_financial_conditions_live",
    "populate_federal_reserve_h41_live",
    "populate_nyfed_cmdi_live",
    "populate_bls_price_wage_productivity_live",
    "populate_eia_natural_gas_storage_live",
    "populate_nber_us_recession_live",
)
