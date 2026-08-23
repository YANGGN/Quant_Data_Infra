"""Fixed official macro-condition feeds using the existing macro store."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Final, Mapping
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date


OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[1]
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 20_000
_SMALL_SOURCE_ROW_LIMIT: Final = 5_000

CMDI_COLLECTOR_ID: Final = "nyfed.macro.cmdi_history"
CMDI_HANDLER: Final = "macro.nyfed_cmdi_history"
H41_COLLECTOR_ID: Final = "federal_reserve.macro.h41_history"
H41_HANDLER: Final = "macro.federal_reserve_h41_history"
CHICAGO_COLLECTOR_ID: Final = "chicagofed.macro.nfci_history"
CHICAGO_HANDLER: Final = "macro.chicagofed_nfci_history"
BIS_COLLECTOR_ID: Final = "bis.macro.credit_conditions_history"
BIS_HANDLER: Final = "macro.bis_credit_conditions_history"

TREASURY_TGA_COLLECTOR_ID: Final = (
    "treasury_fiscal_data.macro.tga_closing_balance_history"
)
TREASURY_TGA_HANDLER: Final = "macro.treasury_fiscal_tga_history"
EIA_GAS_COLLECTOR_ID: Final = "eia.macro.natural_gas_storage_history"
EIA_GAS_HANDLER: Final = "macro.eia_natural_gas_storage_history"
NBER_RECESSION_COLLECTOR_ID: Final = "nber.macro.us_recession_history"
NBER_RECESSION_HANDLER: Final = "macro.nber_us_recession_history"
BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID: Final = (
    "bls.macro.price_wage_productivity_history"
)
BLS_PRICE_WAGE_PRODUCTIVITY_HANDLER: Final = (
    "macro.bls_price_wage_productivity_history"
)


@dataclass(frozen=True, slots=True)
class OfficialSeriesSpec:
    series_id: str
    provider_code: str
    title: str
    frequency: str
    unit: str
    value_representation: str
    scale: str = "1"
    source_part: str = ""


H41_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.federal_reserve_h41.total_assets_less_eliminations_wednesday",
        "H41/H41/RESPPMA_N.WW",
        "Federal Reserve total assets, less eliminations (Wednesday level)",
        "weekly",
        "usd_millions",
        "amount",
        source_part="table5",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h41.reserve_balances_week_average",
        "H41/H41/RESH4R_XAW_N.WW",
        "Reserve balances with Federal Reserve Banks (week average)",
        "weekly",
        "usd_millions",
        "amount",
        source_part="table1",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h41.treasury_general_account_week_average",
        "H41/H41/RESPPLLDT_XAW_N.WW",
        "U.S. Treasury General Account at Federal Reserve Banks (week average)",
        "weekly",
        "usd_millions",
        "amount",
        source_part="table1",
    ),
)

CHICAGO_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.chicagofed.nfci",
        "NFCI",
        "Chicago Fed National Financial Conditions Index",
        "weekly",
        "index",
        "level",
    ),
    OfficialSeriesSpec(
        "macro.chicagofed.anfci",
        "ANFCI",
        "Chicago Fed Adjusted National Financial Conditions Index",
        "weekly",
        "index",
        "level",
    ),
)

BIS_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.bis.us_private_nonfinancial_credit_to_gdp",
        "WS_CREDIT_GAP/Q.US.P.A.A",
        "U.S. private non-financial sector credit to GDP",
        "quarterly",
        "percent_of_gdp",
        "ratio",
        source_part="credit_gap",
    ),
    OfficialSeriesSpec(
        "macro.bis.us_private_nonfinancial_credit_gap",
        "WS_CREDIT_GAP/Q.US.P.A.C",
        "U.S. private non-financial sector credit-to-GDP gap",
        "quarterly",
        "percentage_points_of_gdp",
        "difference",
        source_part="credit_gap",
    ),
    OfficialSeriesSpec(
        "macro.bis.us_private_nonfinancial_debt_service_ratio",
        "WS_DSR/Q.US.P",
        "U.S. private non-financial sector debt-service ratio",
        "quarterly",
        "percent_of_income",
        "ratio",
        source_part="dsr",
    ),
)


CMDI_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.nyfed.cmdi.market",
        "Market CMDI",
        "NY Fed Corporate Bond Market Distress Index — overall market",
        "weekly",
        "index",
        "level",
    ),
    OfficialSeriesSpec(
        "macro.nyfed.cmdi.investment_grade",
        "IG CMDI",
        "NY Fed Corporate Bond Market Distress Index — investment grade",
        "weekly",
        "index",
        "level",
    ),
    OfficialSeriesSpec(
        "macro.nyfed.cmdi.high_yield",
        "HY CMDI",
        "NY Fed Corporate Bond Market Distress Index — high yield",
        "weekly",
        "index",
        "level",
    ),
)

TREASURY_TGA_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.treasury_fiscal.daily.tga_closing_balance",
        "Treasury General Account (TGA) Closing Balance",
        "U.S. Treasury General Account closing balance",
        "daily",
        "usd_millions",
        "amount",
    ),
)

EIA_GAS_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.eia.weekly.lower_48_working_natural_gas_storage",
        "NG.NW2_EPG0_SWO_R48_BCF.W",
        "Lower 48 working natural gas in underground storage",
        "weekly",
        "bcf",
        "amount",
    ),
)

NBER_RECESSION_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.nber.us_recession_indicator",
        "business_cycle_dates",
        "U.S. recession indicator derived from NBER turning points",
        "monthly",
        "indicator",
        "level",
    ),
)

BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.bls.ppi_final_demand_sa",
        "WPSFD4",
        "Producer Price Index - Final Demand, seasonally adjusted",
        "monthly",
        "index_2009_11_100",
        "level",
    ),
    OfficialSeriesSpec(
        "macro.bls.average_hourly_earnings_total_private_sa",
        "CES0500000003",
        "Average Hourly Earnings - Total Private, seasonally adjusted",
        "monthly",
        "usd_per_hour",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.bls.nonfarm_business_labor_productivity_qoq",
        "PRS85006092",
        (
            "Nonfarm Business Labor Productivity - percent change "
            "from previous quarter"
        ),
        "quarterly",
        "percent_change_from_previous_quarter",
        "rate",
    ),
)


@dataclass(frozen=True, slots=True)
class _SourceDefinition:
    key: str
    provider: str
    collector_id: str
    handler: str
    normalization_version: str
    source_reference: str
    artifact_media_type: str
    manifest: tuple[OfficialSeriesSpec, ...]
    max_requests: int
    configuration_env: tuple[str, ...] = ()


_DEFINITIONS: Final = {
    "h41": _SourceDefinition(
        "h41",
        "federal_reserve_h41",
        H41_COLLECTOR_ID,
        H41_HANDLER,
        "federal_reserve_h41_v2",
        "fred/graph/WALCL+WRESBAL+WTREGEN",
        "application/zip",
        H41_MANIFEST,
        1,
    ),
    "chicago": _SourceDefinition(
        "chicago",
        "chicagofed",
        CHICAGO_COLLECTOR_ID,
        CHICAGO_HANDLER,
        "chicagofed_nfci_v1",
        "chicagofed/NFCI/nfci-data-series-csv.csv",
        "text/csv",
        CHICAGO_MANIFEST,
        1,
    ),
    "bis": _SourceDefinition(
        "bis",
        "bis",
        BIS_COLLECTOR_ID,
        BIS_HANDLER,
        "bis_us_credit_conditions_v1",
        "bis/WS_CREDIT_GAP+WS_DSR/us-private-nonfinancial",
        "application/vnd.quant-data.csv-bundle",
        BIS_MANIFEST,
        2,
    ),
    "cmdi": _SourceDefinition(
        "cmdi",
        "nyfed_cmdi",
        CMDI_COLLECTOR_ID,
        CMDI_HANDLER,
        "nyfed_cmdi_v1",
        "newyorkfed/cmdi/cmdi_interactive_data.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        CMDI_MANIFEST,
        1,
    ),
    "treasury_tga": _SourceDefinition(
        "treasury_tga",
        "treasury_fiscal_data",
        TREASURY_TGA_COLLECTOR_ID,
        TREASURY_TGA_HANDLER,
        "treasury_fiscal_tga_v1",
        "treasury_fiscal_data/v1/accounting/dts/operating_cash_balance",
        "application/json",
        TREASURY_TGA_MANIFEST,
        1,
    ),
    "eia_gas": _SourceDefinition(
        "eia_gas",
        "eia",
        EIA_GAS_COLLECTOR_ID,
        EIA_GAS_HANDLER,
        "eia_natural_gas_storage_v1",
        "eia/v2/seriesid/NG.NW2_EPG0_SWO_R48_BCF.W",
        "application/json",
        EIA_GAS_MANIFEST,
        1,
        configuration_env=("EIA_API_KEY",),
    ),
    "nber_recession": _SourceDefinition(
        "nber_recession",
        "nber",
        NBER_RECESSION_COLLECTOR_ID,
        NBER_RECESSION_HANDLER,
        "nber_us_recession_v1",
        "data.nber.org/cycles/business_cycle_dates.json",
        "application/json",
        NBER_RECESSION_MANIFEST,
        1,
    ),
    "bls_price_wage_productivity": _SourceDefinition(
        "bls_price_wage_productivity",
        "bls",
        BLS_PRICE_WAGE_PRODUCTIVITY_COLLECTOR_ID,
        BLS_PRICE_WAGE_PRODUCTIVITY_HANDLER,
        "bls_price_wage_productivity_v1",
        "bls/publicAPI/v2/timeseries/data/WPSFD4+CES0500000003+PRS85006092",
        "application/json",
        BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
        1,
    ),
}


@dataclass(frozen=True, slots=True)
class OfficialResponsePart:
    name: str
    body: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class OfficialObservation:
    series_id: str
    provider_code: str
    source_period: str
    period_start: str
    period_end: str
    value_text: str | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class OfficialConditionsCapture:
    source_key: str
    parts: tuple[OfficialResponsePart, ...]
    artifact_sha256: str
    semantic_identity: str
    captured_at: str
    request_scope_json: str
    observations: tuple[OfficialObservation, ...]


@dataclass(frozen=True, slots=True)
class OfficialConditionsPublishReport:
    source_key: str
    outcome: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_series: int
    written_observation_versions: int
    first_period: str
    last_period: str


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("Official source capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _normalized_decimal(raw: str) -> str:
    value = raw.strip().replace(",", "")
    if not value:
        raise _fail("Official source value must be a finite decimal or missing")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _fail("Official source value must be a finite decimal or missing") from exc
    if not parsed.is_finite():
        raise _fail("Official source value must be a finite decimal or missing")
    ensure_number_within_limits(parsed)
    normalized = parsed.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _value(
    raw: str, *, missing_tokens: frozenset[str]
) -> tuple[str | None, str | None]:
    value = raw.strip()
    if value.casefold() in missing_tokens:
        return None, "source_missing"
    return _normalized_decimal(value), None


def _decode_csv(body: bytes, *, source: str) -> str:
    if not isinstance(body, bytes) or not body:
        raise _fail(f"{source} response must contain CSV bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError(f"{source} response exceeds its byte bound")
    try:
        return body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail(f"{source} response must be UTF-8 CSV") from exc



def _decode_json(body: bytes, *, source: str) -> object:
    if not isinstance(body, bytes) or not body:
        raise _fail(f"{source} response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError(f"{source} response exceeds its byte bound")
    try:
        return loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    except ResourceLimitError:
        raise
    except ValidationError as exc:
        raise _fail(f"{source} response must be valid JSON") from exc


def _json_scalar_text(
    value: object, *, source: str, field: str
) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, bool) and isinstance(value, (int, Decimal)):
        return str(value)
    raise _fail(f"{source} {field} must be a string or number")


def _json_count(value: object, *, source: str, field: str) -> int:
    text = _json_scalar_text(value, source=source, field=field).strip()
    if not text.isascii() or not text.isdecimal():
        raise _fail(f"{source} {field} must be a non-negative integer")
    result = int(text)
    if result > MAX_RESPONSE_ROWS:
        raise ResourceLimitError(f"{source} {field} exceeds its row bound")
    return result


def _json_value(
    value: object, *, source: str, field: str
) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_missing"
    return _value(
        _json_scalar_text(value, source=source, field=field),
        missing_tokens=frozenset({""}),
    )

def _bounded_dates(
    start_date: str, end_date: str, *, source: str
) -> tuple[date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end:
        raise _fail(f"{source} request window is invalid")
    return start, end


def _semantic_material(
    definition: _SourceDefinition,
    scope: Mapping[str, object],
    observations: tuple[OfficialObservation, ...],
) -> dict[str, object]:
    return {
        "normalization_version": definition.normalization_version,
        "provider": definition.provider,
        "request_scope": dict(scope),
        "series_manifest": [
            {"provider_code": item.provider_code, "series_id": item.series_id}
            for item in definition.manifest
        ],
        "normalized_observations": [
            {
                "series_id": item.series_id,
                "source_period": item.source_period,
                "period_start": item.period_start,
                "period_end": item.period_end,
                "value": item.value_text,
                "missing_reason": item.missing_reason,
            }
            for item in observations
        ],
    }


def _capture(
    source_key: str,
    bodies: tuple[tuple[str, bytes], ...],
    *,
    captured_at: str,
    scope: Mapping[str, object],
    observations: list[OfficialObservation],
) -> OfficialConditionsCapture:
    definition = _DEFINITIONS[source_key]
    if len(bodies) != definition.max_requests:
        raise _fail("Official source response count is invalid")
    if not observations:
        raise _fail("Official source returned no selected observations")
    if len(observations) > MAX_RESPONSE_ROWS:
        raise ResourceLimitError("Official source observation count exceeds its bound")
    ordinal = {
        item.series_id: index for index, item in enumerate(definition.manifest)
    }
    observations.sort(
        key=lambda item: (
            item.period_start,
            item.period_end,
            ordinal[item.series_id],
        )
    )
    normalized = tuple(
        OfficialObservation(
            series_id=item.series_id,
            provider_code=item.provider_code,
            source_period=item.source_period,
            period_start=item.period_start,
            period_end=item.period_end,
            value_text=item.value_text,
            missing_reason=item.missing_reason,
            source_row=index,
        )
        for index, item in enumerate(observations, start=1)
    )
    parts = tuple(
        OfficialResponsePart(name, body, _sha256_bytes(body))
        for name, body in bodies
    )
    part_material = [
        {
            "name": item.name,
            "sha256": item.sha256,
            "byte_count": len(item.body),
        }
        for item in parts
    ]
    scope_json = dumps_strict(dict(scope))
    semantic_identity = _sha256_text(
        dumps_strict(_semantic_material(definition, dict(scope), normalized))
    )
    return OfficialConditionsCapture(
        source_key=source_key,
        parts=parts,
        artifact_sha256=_sha256_text(dumps_strict(part_material)),
        semantic_identity=semantic_identity,
        captured_at=_utc_capture(captured_at),
        request_scope_json=scope_json,
        observations=normalized,
    )


def _parse_fred_h41_csv(
    body: bytes,
    *,
    series: tuple[tuple[str, OfficialSeriesSpec], ...],
    start: date,
    end: date,
) -> list[OfficialObservation]:
    text = _decode_csv(body, source="Federal Reserve H.4.1 FRED")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    expected_header = ("observation_date",) + tuple(
        fred_id for fred_id, _spec in series
    )
    if (
        len(rows) < 2
        or len(rows) > MAX_RESPONSE_ROWS + 1
        or tuple(rows[0]) != expected_header
    ):
        raise _fail("Federal Reserve H.4.1 CSV shape is invalid")

    observations: list[OfficialObservation] = []
    seen: set[tuple[str, str]] = set()
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != len(expected_header):
            raise _fail("Federal Reserve H.4.1 row is incomplete")
        observed = parse_date(row[0].strip(), pointer="/time_period")
        if observed < start or observed > end:
            raise _fail(
                "Federal Reserve H.4.1 row is outside the requested window"
            )
        observed_text = observed.isoformat()
        for column, (_fred_id, spec) in enumerate(series, start=1):
            identity = (spec.series_id, observed_text)
            if identity in seen:
                raise _fail("Federal Reserve H.4.1 observations must be unique")
            seen.add(identity)
            value_text, missing_reason = _value(
                row[column], missing_tokens=frozenset({"", "."})
            )
            observations.append(
                OfficialObservation(
                    spec.series_id,
                    spec.provider_code,
                    observed_text,
                    observed_text,
                    observed_text,
                    value_text,
                    missing_reason,
                    0,
                )
            )
    return observations


def parse_federal_reserve_h41(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse one FRED ZIP carrying three underlying Board H.4.1 series."""

    start, end = _bounded_dates(
        start_date, end_date, source="Federal Reserve H.4.1"
    )
    try:
        with ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            expected = {
                "weekly,_as_of_wednesday.csv": (("WALCL", H41_MANIFEST[0]),),
                "weekly,_ending_wednesday.csv": (
                    ("WRESBAL", H41_MANIFEST[1]),
                    ("WTREGEN", H41_MANIFEST[2]),
                ),
            }
            if len(names) != len(set(names)) or not set(expected).issubset(names):
                raise _fail("Federal Reserve H.4.1 ZIP members are invalid")
            if sum(item.file_size for item in archive.infolist()) > MAX_RESPONSE_BYTES:
                raise _fail("Federal Reserve H.4.1 ZIP is too large")
            observations: list[OfficialObservation] = []
            for name, series in expected.items():
                observations.extend(
                    _parse_fred_h41_csv(
                        archive.read(name),
                        series=series,
                        start=start,
                        end=end,
                    )
                )
    except (BadZipFile, KeyError, OSError) as exc:
        raise _fail("Federal Reserve H.4.1 ZIP is invalid") from exc
    if {item.series_id for item in observations} != {
        item.series_id for item in H41_MANIFEST
    }:
        raise _fail("Federal Reserve H.4.1 series coverage is incomplete")
    return _capture(
        "h41",
        (("fred_h41", body),),
        captured_at=captured_at,
        scope={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "fred_series": ["WALCL", "WRESBAL", "WTREGEN"],
            "completeness": "complete",
        },
        observations=observations,
    )


_CHICAGO_HEADER: Final = (
    "Friday_of_Week",
    "NFCI",
    "ANFCI",
    "Risk",
    "Credit",
    "Leverage",
    "Nonfinancial_Leverage",
)


def parse_chicagofed_financial_conditions(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse only NFCI and ANFCI from the fixed Chicago Fed history CSV."""

    start, end = _bounded_dates(
        start_date, end_date, source="Chicago Fed NFCI"
    )
    text = _decode_csv(body, source="Chicago Fed NFCI")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != _CHICAGO_HEADER:
        raise _fail("Chicago Fed NFCI CSV header is invalid")
    observations: list[OfficialObservation] = []
    seen: set[tuple[str, str]] = set()
    by_code = {item.provider_code: item for item in CHICAGO_MANIFEST}
    row_count = 0
    for raw in reader:
        row_count += 1
        if row_count > _SMALL_SOURCE_ROW_LIMIT:
            raise ResourceLimitError(
                "Chicago Fed NFCI response exceeds its row bound"
            )
        try:
            observed = datetime.strptime(
                raw["Friday_of_Week"].strip(), "%m/%d/%Y"
            ).date()
        except (AttributeError, TypeError, ValueError) as exc:
            raise _fail("Chicago Fed week ending date is invalid") from exc
        if observed.weekday() != 4 or observed < start or observed > end:
            raise _fail(
                "Chicago Fed week ending date is outside the requested window"
            )
        period = observed.isoformat()
        for code in ("NFCI", "ANFCI"):
            identity = (code, period)
            if identity in seen:
                raise _fail("Chicago Fed index observations must be unique")
            seen.add(identity)
            spec = by_code[code]
            value_text, missing_reason = _value(
                raw[code], missing_tokens=frozenset({""})
            )
            observations.append(
                OfficialObservation(
                    spec.series_id,
                    code,
                    period,
                    period,
                    period,
                    value_text,
                    missing_reason,
                    0,
                )
            )
    if {item.series_id for item in observations} != {
        item.series_id for item in CHICAGO_MANIFEST
    }:
        raise _fail("Chicago Fed index series coverage is incomplete")
    return _capture(
        "chicago",
        (("nfci", body),),
        captured_at=captured_at,
        scope={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "series": ["NFCI", "ANFCI"],
            "completeness": "complete",
        },
        observations=observations,
    )


_XLSX_NS: Final = (
    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
)
_CMDI_HEADER: Final = (
    "eow_friday",
    "Market CMDI",
    "IG CMDI",
    "HY CMDI",
    "p5",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "Recession",
)
_XLSX_CELL_REFERENCE = re.compile(
    r"^(?P<column>[A-Z]+)(?P<row>[1-9][0-9]*)$"
)


def _xlsx_column(cell: ElementTree.Element) -> str:
    reference = cell.get("r", "")
    match = _XLSX_CELL_REFERENCE.fullmatch(reference)
    if match is None:
        raise _fail("NY Fed CMDI workbook cell reference is invalid")
    return match.group("column")


def _xlsx_value(
    cell: ElementTree.Element,
    shared_strings: tuple[str, ...],
) -> str:
    if cell.find(f"{_XLSX_NS}f") is not None:
        raise _fail("NY Fed CMDI workbook formulas are unsupported")
    value = cell.find(f"{_XLSX_NS}v")
    if value is None or value.text is None:
        return ""
    cell_type = cell.get("t")
    if cell_type is None or cell_type == "n":
        return value.text.strip()
    if cell_type != "s":
        raise _fail("NY Fed CMDI workbook cell type is invalid")
    try:
        index = int(value.text)
        return shared_strings[index]
    except (IndexError, TypeError, ValueError) as exc:
        raise _fail(
            "NY Fed CMDI workbook shared string is invalid"
        ) from exc


def _cmdi_xlsx_rows(
    body: bytes,
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(body, bytes) or not body:
        raise _fail("NY Fed CMDI response must contain XLSX bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError(
            "NY Fed CMDI response exceeds its byte bound"
        )
    try:
        with ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            required = {
                "xl/sharedStrings.xml",
                "xl/worksheets/sheet1.xml",
            }
            if (
                len(names) != len(set(names))
                or not required.issubset(names)
                or sum(
                    item.file_size for item in archive.infolist()
                )
                > MAX_RESPONSE_BYTES
            ):
                raise _fail("NY Fed CMDI workbook members are invalid")
            shared_root = ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml")
            )
            shared_strings = tuple(
                "".join(
                    node.text or ""
                    for node in item.iter(f"{_XLSX_NS}t")
                )
                for item in shared_root.findall(f"{_XLSX_NS}si")
            )
            sheet_root = ElementTree.fromstring(
                archive.read("xl/worksheets/sheet1.xml")
            )
    except (BadZipFile, ElementTree.ParseError, KeyError, OSError) as exc:
        raise _fail("NY Fed CMDI workbook is invalid") from exc

    sheet_data = sheet_root.find(f"{_XLSX_NS}sheetData")
    if sheet_data is None:
        raise _fail("NY Fed CMDI workbook sheet is invalid")
    rows: list[tuple[str, ...]] = []
    for row in sheet_data.findall(f"{_XLSX_NS}row"):
        cells: dict[str, str] = {}
        for cell in row.findall(f"{_XLSX_NS}c"):
            column = _xlsx_column(cell)
            if column in cells:
                raise _fail(
                    "NY Fed CMDI workbook cells must be unique"
                )
            cells[column] = _xlsx_value(cell, shared_strings)
        rows.append(
            tuple(
                cells.get(chr(ord("A") + index), "")
                for index in range(len(_CMDI_HEADER))
            )
        )
        if len(rows) > _SMALL_SOURCE_ROW_LIMIT + 1:
            raise ResourceLimitError(
                "NY Fed CMDI response exceeds its row bound"
            )
    if len(rows) < 2 or rows[0] != _CMDI_HEADER:
        raise _fail("NY Fed CMDI workbook header is invalid")
    return tuple(rows[1:])


def _excel_date(raw: str) -> date:
    try:
        serial = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise _fail("NY Fed CMDI week ending date is invalid") from exc
    if (
        not serial.is_finite()
        or serial != serial.to_integral_value()
        or serial < 1
        or serial > 100_000
    ):
        raise _fail("NY Fed CMDI week ending date is invalid")
    return date(1899, 12, 30) + timedelta(days=int(serial))


def parse_nyfed_cmdi(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse the three published CMDI series from the fixed NY Fed workbook."""

    start, end = _bounded_dates(
        start_date, end_date, source="NY Fed CMDI"
    )
    rows = _cmdi_xlsx_rows(body)
    by_code = {item.provider_code: item for item in CMDI_MANIFEST}
    observations: list[OfficialObservation] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        observed = _excel_date(row[0])
        if observed.weekday() != 4 or observed < start or observed > end:
            raise _fail(
                "NY Fed CMDI week ending date is outside the requested window"
            )
        period = observed.isoformat()
        for column, code in enumerate(
            ("Market CMDI", "IG CMDI", "HY CMDI"),
            start=1,
        ):
            identity = (code, period)
            if identity in seen:
                raise _fail("NY Fed CMDI observations must be unique")
            seen.add(identity)
            spec = by_code[code]
            value_text, missing_reason = _value(
                row[column], missing_tokens=frozenset({""})
            )
            observations.append(
                OfficialObservation(
                    spec.series_id,
                    code,
                    period,
                    period,
                    period,
                    value_text,
                    missing_reason,
                    0,
                )
            )
    if {item.series_id for item in observations} != {
        item.series_id for item in CMDI_MANIFEST
    }:
        raise _fail("NY Fed CMDI series coverage is incomplete")
    return _capture(
        "cmdi",
        (("cmdi_interactive_data", body),),
        captured_at=captured_at,
        scope={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "series": ["Market CMDI", "IG CMDI", "HY CMDI"],
            "completeness": "complete",
        },
        observations=observations,
    )


_TREASURY_TGA_ACCOUNT_TYPE: Final = (
    "Treasury General Account (TGA) Closing Balance"
)


def parse_treasury_tga(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse one complete bounded Treasury operating-cash-balance page."""

    start, end = _bounded_dates(
        start_date, end_date, source="Treasury TGA"
    )
    payload = _decode_json(body, source="Treasury TGA")
    if not isinstance(payload, dict):
        raise _fail("Treasury TGA response shape is invalid")
    rows = payload.get("data")
    metadata = payload.get("meta")
    if not isinstance(rows, list) or not isinstance(metadata, dict):
        raise _fail("Treasury TGA response shape is invalid")
    if not rows or len(rows) > 10_000:
        raise _fail("Treasury TGA response row count is invalid")
    if _json_count(
        metadata.get("total-pages"),
        source="Treasury TGA",
        field="total-pages",
    ) != 1:
        raise _fail("Treasury TGA response is not a complete page")
    if _json_count(
        metadata.get("total-count"),
        source="Treasury TGA",
        field="total-count",
    ) != len(rows):
        raise _fail("Treasury TGA response count is incomplete")

    spec = TREASURY_TGA_MANIFEST[0]
    observations: list[OfficialObservation] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise _fail("Treasury TGA response row is invalid")
        record_date = row.get("record_date")
        account_type = row.get("account_type")
        if not isinstance(record_date, str) or account_type != (
            _TREASURY_TGA_ACCOUNT_TYPE
        ):
            raise _fail("Treasury TGA response row is outside its fixed slice")
        observed = parse_date(record_date, pointer="/record_date")
        if observed < start or observed > end:
            raise _fail("Treasury TGA row is outside the requested window")
        period = observed.isoformat()
        if period in seen:
            raise _fail("Treasury TGA observations must be unique")
        seen.add(period)
        if "open_today_bal" not in row:
            raise _fail("Treasury TGA closing balance is missing")
        value_text, missing_reason = _json_value(
            row["open_today_bal"],
            source="Treasury TGA",
            field="open_today_bal",
        )
        observations.append(
            OfficialObservation(
                spec.series_id,
                spec.provider_code,
                period,
                period,
                period,
                value_text,
                missing_reason,
                0,
            )
        )
    return _capture(
        "treasury_tga",
        (("operating_cash_balance", body),),
        captured_at=captured_at,
        scope={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "account_type": _TREASURY_TGA_ACCOUNT_TYPE,
            "field": "open_today_bal",
            "completeness": "complete",
        },
        observations=observations,
    )


_EIA_GAS_LEGACY_SERIES_ID: Final = "NG.NW2_EPG0_SWO_R48_BCF.W"
_EIA_CREDENTIAL_FIELDS: Final = frozenset({
    "api_key",
    "api-key",
    "apikey",
})


def _sanitize_eia_payload(value: object, *, credential: str) -> object:
    if isinstance(value, str):
        if credential in value:
            raise _fail("EIA response retains its credential")
        return value
    if isinstance(value, list):
        return [
            _sanitize_eia_payload(item, credential=credential)
            for item in value
        ]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _fail("EIA response object key is invalid")
            if credential in key:
                raise _fail("EIA response retains its credential")
            if key.casefold() in _EIA_CREDENTIAL_FIELDS:
                continue
            sanitized[key] = _sanitize_eia_payload(
                item, credential=credential
            )
        return sanitized
    return value


def parse_eia_natural_gas_storage(
    body: bytes,
    *,
    captured_at: str,
    credential: str,
) -> OfficialConditionsCapture:
    """Parse one full legacy EIA natural-gas-storage history response."""

    if not isinstance(credential, str) or not credential:
        raise _fail("EIA credential is invalid")
    payload = _decode_json(body, source="EIA natural gas storage")
    sanitized = _sanitize_eia_payload(payload, credential=credential)
    try:
        sanitized_body = dumps_strict(
            sanitized, max_bytes=MAX_RESPONSE_BYTES
        ).encode("utf-8")
    except ResourceLimitError:
        raise
    except ValidationError as exc:
        raise _fail("EIA response cannot be safely retained") from exc
    if not isinstance(sanitized, dict):
        raise _fail("EIA natural gas storage response shape is invalid")
    response = sanitized.get("response")
    if not isinstance(response, dict):
        raise _fail("EIA natural gas storage response shape is invalid")
    frequency = response.get("frequency")
    rows = response.get("data")
    if (
        not isinstance(frequency, str)
        or frequency.casefold() != "weekly"
        or not isinstance(rows, list)
    ):
        raise _fail("EIA natural gas storage response shape is invalid")
    if not rows or len(rows) > _SMALL_SOURCE_ROW_LIMIT:
        raise _fail("EIA natural gas storage response row count is invalid")
    if _json_count(
        response.get("total"),
        source="EIA natural gas storage",
        field="total",
    ) != len(rows):
        raise _fail("EIA natural gas storage response is incomplete")

    spec = EIA_GAS_MANIFEST[0]
    observations: list[OfficialObservation] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise _fail("EIA natural gas storage response row is invalid")
        period_value = row.get("period")
        if not isinstance(period_value, str):
            raise _fail("EIA natural gas storage period is invalid")
        observed = parse_date(period_value, pointer="/period")
        period = observed.isoformat()
        if period in seen:
            raise _fail("EIA natural gas storage observations must be unique")
        seen.add(period)
        if "value" not in row:
            raise _fail("EIA natural gas storage value is missing")
        value_text, missing_reason = _json_value(
            row["value"],
            source="EIA natural gas storage",
            field="value",
        )
        observations.append(
            OfficialObservation(
                spec.series_id,
                spec.provider_code,
                period,
                period,
                period,
                value_text,
                missing_reason,
                0,
            )
        )
    return _capture(
        "eia_gas",
        (("natural_gas_storage", sanitized_body),),
        captured_at=captured_at,
        scope={
            "series_id": _EIA_GAS_LEGACY_SERIES_ID,
            "frequency": "weekly",
            "completeness": "complete",
        },
        observations=observations,
    )


_NBER_MONTH = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")


def _nber_month(value: object, *, field: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise _fail(f"NBER {field} must be an ISO month or date")
    raw = value.strip()
    match = _NBER_MONTH.fullmatch(raw)
    if match is not None:
        return int(match.group("year")), int(match.group("month"))
    try:
        observed = parse_date(raw, pointer=f"/{field}")
    except ValidationError as exc:
        raise _fail(f"NBER {field} must be an ISO month or date") from exc
    return observed.year, observed.month


def _next_month(value: tuple[int, int]) -> tuple[int, int]:
    year, month = value
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_bounds(value: tuple[int, int]) -> tuple[str, str, str]:
    year, month = value
    start = date(year, month, 1)
    next_start = date(*_next_month(value), 1)
    end = date.fromordinal(next_start.toordinal() - 1)
    return f"{year:04d}-{month:02d}", start.isoformat(), end.isoformat()


def parse_nber_us_recession(
    body: bytes,
    *,
    captured_at: str,
) -> OfficialConditionsCapture:
    """Derive a monthly U.S. recession indicator from NBER turning points."""

    payload = _decode_json(body, source="NBER business-cycle dates")
    if (
        not isinstance(payload, list)
        or not payload
        or len(payload) > _SMALL_SOURCE_ROW_LIMIT
    ):
        raise _fail("NBER business-cycle response shape is invalid")
    cycles: list[tuple[tuple[int, int], tuple[int, int]]] = []
    initial_trough: tuple[int, int] | None = None
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise _fail("NBER business-cycle response row is invalid")
        peak_value = row.get("peak")
        if index == 0 and (
            peak_value is None
            or (isinstance(peak_value, str) and not peak_value.strip())
        ):
            initial_trough = _nber_month(
                row.get("trough"), field="trough"
            )
            continue
        peak = _nber_month(peak_value, field="peak")
        trough = _nber_month(row.get("trough"), field="trough")
        if peak >= trough:
            raise _fail("NBER business-cycle turning points are invalid")
        cycles.append((peak, trough))
    if not cycles:
        raise _fail("NBER business-cycle response has no complete cycles")
    cycles.sort()
    if initial_trough is not None and initial_trough >= cycles[0][0]:
        raise _fail("NBER initial trough boundary is invalid")
    for index, (peak, trough) in enumerate(cycles):
        if index and peak <= cycles[index - 1][1]:
            raise _fail("NBER business-cycle turning points overlap")
        if peak >= trough:
            raise _fail("NBER business-cycle turning points are invalid")

    captured = _utc_capture(captured_at)
    capture_date = date.fromisoformat(captured[:10])
    through = (capture_date.year, capture_date.month)
    first_month = initial_trough or cycles[0][0]
    if through < first_month or any(trough > through for _, trough in cycles):
        raise _fail("NBER turning points exceed the local capture month")
    recession_months: set[tuple[int, int]] = set()
    for peak, trough in cycles:
        cursor = _next_month(peak)
        while cursor <= trough:
            recession_months.add(cursor)
            cursor = _next_month(cursor)

    spec = NBER_RECESSION_MANIFEST[0]
    observations: list[OfficialObservation] = []
    cursor = first_month
    while cursor <= through:
        source_period, period_start, period_end = _month_bounds(cursor)
        observations.append(
            OfficialObservation(
                spec.series_id,
                spec.provider_code,
                source_period,
                period_start,
                period_end,
                "1" if cursor in recession_months else "0",
                None,
                0,
            )
        )
        if len(observations) > _SMALL_SOURCE_ROW_LIMIT:
            raise ResourceLimitError("NBER derived history exceeds its row bound")
        cursor = _next_month(cursor)
    return _capture(
        "nber_recession",
        (("business_cycle_dates", body),),
        captured_at=captured,
        scope={
            "source": "business_cycle_dates",
            "first_month": _month_bounds(first_month)[0],
            "first_peak_month": _month_bounds(cycles[0][0])[0],
            "through_month": _month_bounds(through)[0],
            "derivation": "month_after_peak_through_trough_inclusive",
            "availability_basis": "local_capture",
            "completeness": "complete",
        },
        observations=observations,
    )


_QUARTER = re.compile(r"^(?P<year>[0-9]{4})-Q(?P<quarter>[1-4])$")


def _quarter(value: str) -> tuple[str, str, str, tuple[int, int]]:
    match = _QUARTER.fullmatch(value.strip())
    if match is None:
        raise _fail("BIS time period must be YYYY-Qn")
    year = int(match.group("year"))
    quarter = int(match.group("quarter"))
    first_month = (quarter - 1) * 3 + 1
    next_year = year + (1 if quarter == 4 else 0)
    next_month = 1 if quarter == 4 else first_month + 3
    start = date(year, first_month, 1)
    end = date.fromordinal(date(next_year, next_month, 1).toordinal() - 1)
    return value.strip(), start.isoformat(), end.isoformat(), (year, quarter)


def parse_bls_price_wage_productivity(
    body: bytes,
    *,
    captured_at: str,
    start_year: int,
    end_year: int,
    series_codes: tuple[str, ...] | None = None,
) -> OfficialConditionsCapture:
    """Parse the fixed PPI, earnings, and productivity BLS response."""

    if (
        isinstance(start_year, bool)
        or isinstance(end_year, bool)
        or not isinstance(start_year, int)
        or not isinstance(end_year, int)
        or start_year > end_year
        or end_year - start_year > 9
    ):
        raise _fail("BLS price, wage, and productivity window is invalid")
    all_by_code = {
        item.provider_code: item
        for item in BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST
    }
    if series_codes is None:
        selected_manifest = BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST
    else:
        if (
            not isinstance(series_codes, tuple)
            or not series_codes
            or any(
                not isinstance(code, str) or code not in all_by_code
                for code in series_codes
            )
            or len(set(series_codes)) != len(series_codes)
        ):
            raise _fail(
                "BLS price, wage, and productivity series scope is invalid"
            )
        selected_manifest = tuple(all_by_code[code] for code in series_codes)
    payload = _decode_json(body, source="BLS price, wage, and productivity")
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        raise _fail("BLS price, wage, and productivity response did not succeed")
    messages = payload.get("message")
    if (
        not isinstance(messages, list)
        or any(
            not isinstance(message, str) or bool(message.strip())
            for message in messages
        )
    ):
        raise _fail("BLS price, wage, and productivity response has a message")
    results = payload.get("Results")
    raw_series = results.get("series") if isinstance(results, dict) else None
    if (
        not isinstance(raw_series, list)
        or len(raw_series) != len(selected_manifest)
    ):
        raise _fail("BLS price, wage, and productivity series shape is invalid")

    by_code = {
        item.provider_code: item
        for item in selected_manifest
    }
    observations: list[OfficialObservation] = []
    seen_series: set[str] = set()
    seen_observations: set[tuple[str, str]] = set()
    for raw in raw_series:
        if not isinstance(raw, dict):
            raise _fail("BLS price, wage, and productivity series is invalid")
        provider_code = raw.get("seriesID")
        if (
            not isinstance(provider_code, str)
            or provider_code not in by_code
            or provider_code in seen_series
        ):
            raise _fail("BLS price, wage, and productivity series is unsupported")
        seen_series.add(provider_code)
        spec = by_code[provider_code]
        data = raw.get("data")
        if not isinstance(data, list) or not data:
            raise _fail("BLS price, wage, and productivity series has no data")
        selected = 0
        for row in data:
            if not isinstance(row, dict):
                raise _fail("BLS price, wage, and productivity row is invalid")
            raw_year = row.get("year")
            if (
                not isinstance(raw_year, str)
                or len(raw_year) != 4
                or not raw_year.isascii()
                or not raw_year.isdecimal()
            ):
                raise _fail("BLS price, wage, and productivity year is invalid")
            year = int(raw_year)
            if year < start_year or year > end_year:
                raise _fail(
                    "BLS price, wage, and productivity row is outside the window"
                )
            period = row.get("period")
            if spec.frequency == "monthly":
                if period == "M13":
                    continue
                if (
                    not isinstance(period, str)
                    or re.fullmatch(r"M(0[1-9]|1[0-2])", period) is None
                ):
                    raise _fail("BLS monthly period is invalid")
                source_period, period_start, period_end = _month_bounds(
                    (year, int(period[1:]))
                )
            else:
                if period == "Q05":
                    continue
                if (
                    not isinstance(period, str)
                    or re.fullmatch(r"Q0[1-4]", period) is None
                ):
                    raise _fail("BLS quarterly period is invalid")
                source_period, period_start, period_end, _ = _quarter(
                    f"{year}-Q{int(period[1:])}"
                )
            identity = (provider_code, source_period)
            if identity in seen_observations:
                raise _fail("BLS price, wage, and productivity rows must be unique")
            seen_observations.add(identity)
            raw_value = row.get("value")
            if isinstance(raw_value, str) and raw_value.strip() == "-":
                value_text, missing_reason = None, "source_missing"
            else:
                value_text, missing_reason = _json_value(
                    raw_value,
                    source="BLS price, wage, and productivity",
                    field="value",
                )
            observations.append(
                OfficialObservation(
                    spec.series_id,
                    provider_code,
                    source_period,
                    period_start,
                    period_end,
                    value_text,
                    missing_reason,
                    0,
                )
            )
            selected += 1
        if selected == 0:
            raise _fail("BLS price, wage, and productivity series has no observations")
    if seen_series != set(by_code):
        raise _fail("BLS price, wage, and productivity coverage is incomplete")
    return _capture(
        "bls_price_wage_productivity",
        (("bls_series", body),),
        captured_at=captured_at,
        scope={
            "start_year": start_year,
            "end_year": end_year,
            "series": [
                item.provider_code
                for item in selected_manifest
            ],
            "availability_basis": "local_capture",
            "completeness": "complete",
        },
        observations=observations,
    )


def _parse_bis_body(
    body: bytes,
    *,
    part: str,
    start_key: tuple[int, int],
    end_key: tuple[int, int],
) -> list[OfficialObservation]:
    text = _decode_csv(body, source="BIS")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    required = {"FREQ", "BORROWERS_CTY", "TIME_PERIOD", "OBS_VALUE"}
    if part == "credit_gap":
        required.update({"TC_BORROWERS", "TC_LENDERS", "CG_DTYPE"})
    else:
        required.add("DSR_BORROWERS")
    if not required.issubset(set(reader.fieldnames or ())):
        raise _fail("BIS CSV header is invalid")
    by_code = {item.provider_code: item for item in BIS_MANIFEST}
    observations: list[OfficialObservation] = []
    seen: set[tuple[str, str]] = set()
    row_count = 0
    for raw in reader:
        row_count += 1
        if row_count > _SMALL_SOURCE_ROW_LIMIT:
            raise ResourceLimitError("BIS response exceeds its row bound")
        if raw["FREQ"] != "Q" or raw["BORROWERS_CTY"] != "US":
            continue
        if part == "credit_gap":
            if (
                raw["TC_BORROWERS"] != "P"
                or raw["TC_LENDERS"] != "A"
            ):
                continue
            dtype = raw["CG_DTYPE"]
            if dtype not in {"A", "C"}:
                continue
            code = f"WS_CREDIT_GAP/Q.US.P.A.{dtype}"
        else:
            if raw["DSR_BORROWERS"] != "P":
                continue
            code = "WS_DSR/Q.US.P"
        source_period, period_start, period_end, period_key = _quarter(
            raw["TIME_PERIOD"]
        )
        if period_key < start_key or period_key > end_key:
            raise _fail("BIS observation is outside the requested period window")
        identity = (code, source_period)
        if identity in seen:
            raise _fail("BIS observations must be unique")
        seen.add(identity)
        spec = by_code[code]
        value_text, missing_reason = _value(
            raw["OBS_VALUE"], missing_tokens=frozenset({""})
        )
        observations.append(
            OfficialObservation(
                spec.series_id,
                code,
                source_period,
                period_start,
                period_end,
                value_text,
                missing_reason,
                0,
            )
        )
    return observations


def parse_bis_credit_conditions(
    credit_gap_body: bytes,
    dsr_body: bytes,
    *,
    captured_at: str,
    start_period: str,
    end_period: str,
) -> OfficialConditionsCapture:
    """Parse the fixed U.S. private non-financial BIS quarterly slice."""

    start_source, _, _, start_key = _quarter(start_period)
    end_source, _, _, end_key = _quarter(end_period)
    if start_key > end_key:
        raise _fail("BIS request period window is invalid")
    observations = _parse_bis_body(
        credit_gap_body,
        part="credit_gap",
        start_key=start_key,
        end_key=end_key,
    ) + _parse_bis_body(
        dsr_body,
        part="dsr",
        start_key=start_key,
        end_key=end_key,
    )
    if {item.series_id for item in observations} != {
        item.series_id for item in BIS_MANIFEST
    }:
        raise _fail("BIS credit-condition series coverage is incomplete")
    return _capture(
        "bis",
        (("credit_gap", credit_gap_body), ("dsr", dsr_body)),
        captured_at=captured_at,
        scope={
            "start_period": start_source,
            "end_period": end_source,
            "country": "US",
            "borrower_sector": "private_nonfinancial",
            "completeness": "complete",
        },
        observations=observations,
    )


def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _registry_binding(
    registry: object, definition: _SourceDefinition
) -> Registry:
    if not isinstance(registry, Registry):
        raise _fail(
            "Official conditions publisher requires the reviewed registry"
        )
    matches = [
        item
        for item in registry.collectors
        if str(item.get("id")) == definition.collector_id
    ]
    if len(matches) != 1:
        raise _fail(
            "Official conditions registry collector binding is invalid"
        )
    collector = matches[0]
    if (
        collector.get("handler") != definition.handler
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != list(definition.configuration_env)
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail(
            "Official conditions registry collector binding is invalid"
        )
    datasets = {item.id: item for item in registry.datasets}
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or definition.collector_id not in dataset.collector_ids
        ):
            raise _fail(
                "Official conditions registry dataset binding is invalid"
            )
    return registry


def _validated_capture(
    capture: object,
) -> tuple[
    OfficialConditionsCapture,
    _SourceDefinition,
    dict[str, object],
]:
    if not isinstance(capture, OfficialConditionsCapture):
        raise _fail("Official conditions capture binding is invalid")
    definition = _DEFINITIONS.get(capture.source_key)
    if definition is None or len(capture.parts) != definition.max_requests:
        raise _fail("Official conditions capture binding is invalid")
    for part in capture.parts:
        if (
            not isinstance(part, OfficialResponsePart)
            or not part.body
            or len(part.body) > MAX_RESPONSE_BYTES
            or part.sha256 != _sha256_bytes(part.body)
        ):
            raise _fail("Official conditions evidence binding is invalid")
    expected_artifact = _sha256_text(
        dumps_strict(
            [
                {
                    "name": item.name,
                    "sha256": item.sha256,
                    "byte_count": len(item.body),
                }
                for item in capture.parts
            ]
        )
    )
    scope = loads_strict(capture.request_scope_json, max_bytes=16_384)
    if (
        not isinstance(scope, dict)
        or scope.get("completeness") != "complete"
    ):
        raise _fail("Official conditions request scope is invalid")
    expected_semantic = _sha256_text(
        dumps_strict(
            _semantic_material(definition, scope, capture.observations)
        )
    )
    spec_by_id = {item.series_id: item for item in definition.manifest}
    seen: set[tuple[str, str, str]] = set()
    for index, observation in enumerate(capture.observations, start=1):
        spec = spec_by_id.get(observation.series_id)
        identity = (
            observation.series_id,
            observation.period_start,
            observation.period_end,
        )
        if (
            spec is None
            or observation.provider_code != spec.provider_code
            or observation.source_row != index
            or observation.period_start > observation.period_end
            or identity in seen
            or (
                (observation.value_text is None)
                == (observation.missing_reason is None)
            )
        ):
            raise _fail(
                "Official conditions normalized observation is invalid"
            )
        seen.add(identity)
    if (
        not capture.observations
        or len(capture.observations) > MAX_RESPONSE_ROWS
        or capture.artifact_sha256 != expected_artifact
        or capture.semantic_identity != expected_semantic
        or capture.captured_at != _utc_capture(capture.captured_at)
    ):
        raise _fail("Official conditions capture binding is invalid")
    return capture, definition, scope


class OfficialConditionsPublisher:
    """Publish one already-parsed fixed official-source capture."""

    def __init__(
        self,
        *,
        source_key: str,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        definition = _DEFINITIONS.get(source_key)
        if (
            definition is None
            or not isinstance(project_root, Path)
            or not isinstance(macro_store, Path)
        ):
            raise _fail("Official conditions publisher arguments are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail(
                "Official conditions publisher target is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail(
                "Official conditions publisher target binding is invalid"
            )
        if not _canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail(
                    "Official conditions fixture root must be temporary"
                ) from exc
            if root == temporary:
                raise _fail(
                    "Official conditions fixture root is too broad"
                )
        self._definition = definition
        self._registry = _registry_binding(registry, definition)
        self._coordinator = IngestionCoordinator(
            _store_map(root, store),
            code_version=definition.normalization_version,
        )

    def publish(
        self, capture: OfficialConditionsCapture
    ) -> OfficialConditionsPublishReport:
        prepared, definition, scope = _validated_capture(capture)
        if definition != self._definition:
            raise _fail(
                "Official conditions publisher source binding is invalid"
            )
        run_id = stable_id(
            f"{definition.key}_conditions_run",
            prepared.semantic_identity,
        )
        counts = {"series": 0, "versions": 0}

        def writer(
            connection: sqlite3.Connection, active_run_id: str
        ) -> WriteResult:
            return self._write_candidate(
                connection,
                prepared,
                definition,
                active_run_id,
                scope,
                counts,
            )

        receipt = self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=CANONICAL_DATASET_ID,
            output_dataset_ids=OUTPUT_DATASET_IDS,
            semantic_identity=prepared.semantic_identity,
            run_id=run_id,
            command=definition.collector_id,
            scope=scope,
            started_at=prepared.captured_at,
            completed_at=prepared.captured_at,
            fetched_count=len(prepared.observations),
            writer=writer,
        )
        if receipt.outcome not in {"succeeded", "unchanged"}:
            raise ConflictError(
                "Official conditions publisher returned an invalid outcome"
            )
        periods = [item.source_period for item in prepared.observations]
        return OfficialConditionsPublishReport(
            source_key=definition.key,
            outcome=(
                "published"
                if receipt.outcome == "succeeded"
                else "unchanged"
            ),
            semantic_identity=prepared.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_series=counts["series"],
            written_observation_versions=counts["versions"],
            first_period=min(periods),
            last_period=max(periods),
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        capture: OfficialConditionsCapture,
        definition: _SourceDefinition,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            f"{definition.key}_conditions_artifact",
            capture.semantic_identity,
            capture.artifact_sha256,
        )
        snapshot_id = stable_id(
            f"{definition.key}_conditions_snapshot",
            CANONICAL_DATASET_ID,
            capture.semantic_identity,
        )
        spec_by_id = {
            item.series_id: item for item in definition.manifest
        }
        for spec in definition.manifest:
            counts["series"] += int(
                _ensure_series(connection, definition, spec, run_id)
            )
        release_ids = {
            (item.series_id, item.source_period): _ensure_release(
                connection,
                definition,
                item,
                capture.captured_at,
                run_id,
            )
            for item in capture.observations
        }
        connection.execute(
            """
            INSERT INTO macro_source_artifacts (
                artifact_id, dataset_id, sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision,
                source_resource, normalization_version, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?, ?)
            """,
            (
                artifact_id,
                EVIDENCE_DATASET_ID,
                capture.artifact_sha256,
                definition.artifact_media_type,
                sum(len(item.body) for item in capture.parts),
                dumps_strict(scope),
                capture.captured_at,
                definition.source_reference,
                definition.normalization_version,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO macro_source_snapshots (
                snapshot_id, series_id, release_id, artifact_id,
                semantic_identity, completeness, row_count, validation_state,
                warnings_json, run_id, quality_flags_json
            ) VALUES (
                ?, NULL, NULL, ?, ?, 'complete', ?, 'validated', '[]', ?, '[]'
            )
            """,
            (
                snapshot_id,
                artifact_id,
                capture.semantic_identity,
                len(capture.observations),
                run_id,
            ),
        )
        scope_json = dumps_strict(scope)
        scope_digest = _sha256_text(scope_json)
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', 0)
            """,
            (
                stable_id(
                    f"{definition.key}_conditions_scope",
                    snapshot_id,
                    scope_digest,
                ),
                snapshot_id,
                scope_json,
                scope_digest,
            ),
        )
        for observation in capture.observations:
            spec = spec_by_id[observation.series_id]
            version_id, appended = _append_or_reuse_observation(
                connection,
                definition,
                spec,
                observation,
                release_ids[
                    (observation.series_id, observation.source_period)
                ],
                artifact_id,
                snapshot_id,
                capture.captured_at,
                run_id,
            )
            counts["versions"] += int(appended)
            connection.execute(
                """
                INSERT INTO macro_snapshot_observation_membership (
                    snapshot_id, version_id, source_row
                ) VALUES (?, ?, ?)
                """,
                (snapshot_id, version_id, observation.source_row),
            )
        byte_count = sum(len(item.body) for item in capture.parts)
        artifact = ArtifactWrite(
            artifact_id=artifact_id,
            dataset_id=EVIDENCE_DATASET_ID,
            content_sha256=capture.artifact_sha256,
            media_type=definition.artifact_media_type,
            byte_count=byte_count,
            source_reference=definition.source_reference,
            request_scope=scope,
            captured_at=capture.captured_at,
            captured_precision="datetime",
            normalization_version=definition.normalization_version,
        )
        return WriteResult(
            written_count=counts["series"] + counts["versions"],
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CANONICAL_DATASET_ID,
                semantic_identity=capture.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.observations),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        f"{definition.key}_conditions_quality",
                        snapshot_id,
                    ),
                    dataset_id=CANONICAL_DATASET_ID,
                    rule_id=(
                        f"{definition.key}_conditions.fixed_manifest"
                    ),
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "observation_count": len(capture.observations),
                        "series_count": len(definition.manifest),
                    },
                ),
            ),
        )


def _series_metadata(
    definition: _SourceDefinition, spec: OfficialSeriesSpec
) -> tuple[object, ...]:
    return (
        definition.provider,
        spec.provider_code,
        spec.title,
        spec.frequency,
        spec.unit,
        spec.value_representation,
        spec.scale,
        dumps_strict({}),
        dumps_strict(["latest", "as_of"]),
        "local_capture",
    )


def _ensure_series(
    connection: sqlite3.Connection,
    definition: _SourceDefinition,
    spec: OfficialSeriesSpec,
    run_id: str,
) -> bool:
    expected = _series_metadata(definition, spec)
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json,
               supported_modes_json, availability_basis
        FROM macro_series WHERE series_id=?
        """,
        (spec.series_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO macro_series (
                series_id, provider, provider_series_code, title, frequency,
                unit, value_representation, scale, dimensions_json,
                supported_modes_json, availability_basis, created_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (spec.series_id, *expected, run_id),
        )
        return True
    if tuple(existing) != expected:
        raise _fail(
            "Official conditions series conflicts with immutable catalog metadata"
        )
    return False


def _source_vintage_identity(
    definition: _SourceDefinition, observation: OfficialObservation
) -> str:
    return (
        f"{definition.provider}:{observation.provider_code}:"
        f"{observation.source_period}"
    )


def _ensure_release(
    connection: sqlite3.Connection,
    definition: _SourceDefinition,
    observation: OfficialObservation,
    captured_at: str,
    run_id: str,
) -> str:
    identity = _source_vintage_identity(definition, observation)
    expected = (
        observation.period_end,
        "date",
        observation.source_period,
        0,
        None,
        "current_state",
    )
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision, source_release_order,
               is_first_release, first_release_evidence, release_stage
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (observation.series_id, identity),
    ).fetchone()
    if existing is not None:
        if tuple(existing)[1:] != expected:
            raise _fail(
                "Official conditions release conflicts with immutable metadata"
            )
        return str(existing["release_id"])
    release_id = stable_id(
        f"{definition.key}_conditions_release",
        observation.series_id,
        identity,
    )
    connection.execute(
        """
        INSERT INTO macro_releases (
            release_id, series_id, source_vintage_identity, vintage_at,
            vintage_precision, source_release_order, available_at,
            available_precision, is_first_release, first_release_evidence,
            first_seen_run_id, release_stage, source_published_at,
            source_published_precision
        ) VALUES (
            ?, ?, ?, ?, 'date', ?, ?, 'datetime', 0, NULL, ?,
            'current_state', NULL, 'unknown'
        )
        """,
        (
            release_id,
            observation.series_id,
            identity,
            observation.period_end,
            observation.source_period,
            captured_at,
            run_id,
        ),
    )
    return release_id


def _stored_value_matches(
    stored: object, value_text: str | None
) -> bool:
    if stored is None:
        return value_text is None
    if value_text is None:
        return False
    try:
        return _normalized_decimal(str(stored)) == value_text
    except ValidationError as exc:
        raise ConflictError(
            "Stored official conditions value is invalid"
        ) from exc


def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    definition: _SourceDefinition,
    spec: OfficialSeriesSpec,
    observation: OfficialObservation,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
    dimensions_json = dumps_strict({})
    dimensions_digest = _sha256_text(dimensions_json)
    source_identity = _source_vintage_identity(definition, observation)
    existing = connection.execute(
        """
        SELECT version_id, correction_sequence, value_text, missing_reason,
               available_at, state
        FROM macro_observation_versions
        WHERE series_id=? AND period_start=? AND period_end=?
          AND dimensions_digest=? AND source_vintage_identity=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            dimensions_digest,
            source_identity,
        ),
    ).fetchone()
    if existing is not None and (
        _stored_value_matches(
            existing["value_text"], observation.value_text
        )
        and existing["missing_reason"] == observation.missing_reason
        and existing["state"] == "active"
    ):
        return str(existing["version_id"]), False
    if (
        existing is not None
        and str(existing["available_at"]) > captured_at
    ):
        raise _fail(
            "Official conditions correction capture precedes stored evidence"
        )
    correction_sequence = (
        1
        if existing is None
        else int(existing["correction_sequence"]) + 1
    )
    supersedes = (
        None if existing is None else str(existing["version_id"])
    )
    version_id = stable_id(
        f"{definition.key}_conditions_observation_version",
        observation.series_id,
        observation.source_period,
        str(correction_sequence),
        _sha256_text(
            dumps_strict(
                {
                    "value": observation.value_text,
                    "missing_reason": observation.missing_reason,
                    "available_at": captured_at,
                }
            )
        ),
    )
    connection.execute(
        """
        INSERT INTO macro_observation_versions (
            version_id, series_id, period_start, period_end, dimensions_json,
            dimensions_digest, release_id, source_vintage_identity,
            correction_sequence, value_text, missing_reason, unit,
            value_representation, scale, available_at, available_precision,
            captured_at, captured_precision, supersedes_version_id,
            artifact_id, snapshot_id, run_id, source_row, state,
            is_preliminary, quality_flags_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime',
            ?, 'datetime', ?, ?, ?, ?, ?, 'active', 0, '[]'
        )
        """,
        (
            version_id,
            observation.series_id,
            observation.period_start,
            observation.period_end,
            dimensions_json,
            dimensions_digest,
            release_id,
            source_identity,
            correction_sequence,
            observation.value_text,
            observation.missing_reason,
            spec.unit,
            spec.value_representation,
            spec.scale,
            captured_at,
            captured_at,
            supersedes,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
        ),
    )
    current = connection.execute(
        """
        SELECT current.current_version_id, version.available_at,
               version.correction_sequence
        FROM macro_observations AS current
        JOIN macro_observation_versions AS version
          ON version.version_id=current.current_version_id
        WHERE current.series_id=? AND current.period_start=?
          AND current.period_end=? AND current.dimensions_digest=?
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            dimensions_digest,
        ),
    ).fetchone()
    if current is None:
        connection.execute(
            """
            INSERT INTO macro_observations (
                series_id, period_start, period_end, dimensions_digest,
                current_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                observation.series_id,
                observation.period_start,
                observation.period_end,
                dimensions_digest,
                version_id,
            ),
        )
    elif (captured_at, correction_sequence) >= (
        str(current["available_at"]),
        int(current["correction_sequence"]),
    ):
        connection.execute(
            """
            UPDATE macro_observations SET current_version_id=?
            WHERE series_id=? AND period_start=? AND period_end=?
              AND dimensions_digest=?
            """,
            (
                version_id,
                observation.series_id,
                observation.period_start,
                observation.period_end,
                dimensions_digest,
            ),
        )
    return version_id, True


__all__ = (
    "BIS_COLLECTOR_ID",
    "BIS_HANDLER",
    "BIS_MANIFEST",
    "CANONICAL_DATASET_ID",
    "CHICAGO_COLLECTOR_ID",
    "CHICAGO_HANDLER",
    "CHICAGO_MANIFEST",
    "H41_COLLECTOR_ID",
    "H41_HANDLER",
    "H41_MANIFEST",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "OUTPUT_DATASET_IDS",
    "OfficialConditionsCapture",
    "OfficialConditionsPublishReport",
    "OfficialConditionsPublisher",
    "OfficialObservation",
    "OfficialResponsePart",
    "OfficialSeriesSpec",
    "parse_bis_credit_conditions",
    "parse_chicagofed_financial_conditions",
    "parse_federal_reserve_h41",
    "EIA_GAS_COLLECTOR_ID",
    "EIA_GAS_HANDLER",
    "EIA_GAS_MANIFEST",
    "NBER_RECESSION_COLLECTOR_ID",
    "NBER_RECESSION_HANDLER",
    "NBER_RECESSION_MANIFEST",
    "TREASURY_TGA_COLLECTOR_ID",
    "TREASURY_TGA_HANDLER",
    "TREASURY_TGA_MANIFEST",
    "parse_eia_natural_gas_storage",
    "parse_nber_us_recession",
    "parse_treasury_tga",
)
