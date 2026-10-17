"""Credential-free GDP/CPI/employment vintage parsing and isolated publication.

The operation layer owns transport, scheduling, credentials, durable request
intent, and any retry policy.  This module accepts only completed source bytes
and explicit source/local temporal metadata, validates them with the standard
library, and writes one short append-only transaction to the dedicated live
vintage relations added by migration ``0013``.

The public parser results deliberately retain no request headers, query values,
or credentials.  GDP's official BEA workbook gives source-release dates. BLS
annual archive snapshots are dated from the caller's official Last-Modified
metadata, while the current BLS API has no asserted provider release instant
and therefore remains local-capture availability. Philadelphia Fed RTDSM
employment matrix headers identify a month or quarter, not a provider-release
instant, so their availability is likewise local capture only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree as ET

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreWriteLock, stable_id
from ..temporal import TemporalPrecision, TemporalValue


NORMALIZATION_VERSION = "macro.live_vintage.v2"
BEA_GDP_VINTAGE_HISTORY_RESOURCE = (
    "https://apps.bea.gov/national/xls/gdp-gdi-vintage-history.xlsx"
)
BLS_CURRENT_RESOURCE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
PHILADELPHIA_FED_PAYROLL_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/employMvMd.xlsx"
)
PHILADELPHIA_FED_UNEMPLOYMENT_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/rucQvMd.xlsx"
)
PHILADELPHIA_FED_NOMINAL_OUTPUT_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/NOUTPUTQvQd.xlsx"
)
PHILADELPHIA_FED_REAL_OUTPUT_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx"
)
PHILADELPHIA_FED_CPI_ALL_ITEMS_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/pcpiMvMd.xlsx"
)
PHILADELPHIA_FED_CPI_CORE_RTDSM_RESOURCE = (
    "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
    "real-time-data/data-files/xlsx/pcpixMvMd.xlsx"
)

GDP_REAL_SERIES_ID = "macro.gdp.real_qoq_saar_pct"
GDP_NOMINAL_SERIES_ID = "macro.gdp.nominal_billions"
GDI_REAL_SERIES_ID = "macro.gdi.real_qoq_saar_pct"
GDI_NOMINAL_SERIES_ID = "macro.gdi.nominal_billions"
CPI_ALL_ITEMS_SERIES_ID = "macro.bls.cpi_u_all_items_sa"
CPI_CORE_SERIES_ID = "macro.bls.cpi_u_core_sa"
TOTAL_NONFARM_PAYROLLS_SERIES_ID = "macro.bls.total_nonfarm_payrolls_sa"
UNEMPLOYMENT_RATE_SERIES_ID = "macro.bls.unemployment_rate_sa"
RTDSM_NOMINAL_OUTPUT_SERIES_ID = "macro.philadelphia_fed.nominal_output"
RTDSM_REAL_OUTPUT_SERIES_ID = "macro.philadelphia_fed.real_output"

_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_XLSX_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_XLSX_MEMBERS = 512
_MAX_BLS_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_BLS_LINES = 5_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUARTER = re.compile(r"^(\d{4})\s*:?[ ]*Q([1-4])$", re.IGNORECASE)
_QUARTER_REVERSED = re.compile(r"^Q([1-4])\s*(\d{4})$", re.IGNORECASE)
_MONTH_PERIOD = re.compile(r"^M(0[1-9]|1[0-2])$")
_DATE_PREFIX = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_SERIES_SPECS: dict[str, dict[str, str]] = {
    GDP_REAL_SERIES_ID: {
        "provider": "bea",
        "provider_series_code": "A191RL",
        "title": "Real gross domestic product, percent change from preceding period",
        "frequency": "quarterly",
        "unit": "percent",
        "value_representation": "rate",
        "availability_basis": "source_release",
    },
    GDP_NOMINAL_SERIES_ID: {
        "provider": "bea",
        "provider_series_code": "A191RC",
        "title": "Gross domestic product",
        "frequency": "quarterly",
        "unit": "billions_usd",
        "value_representation": "level",
        "availability_basis": "source_release",
    },
    GDI_REAL_SERIES_ID: {
        "provider": "bea",
        "provider_series_code": "A261RL",
        "title": "Real gross domestic income, percent change from preceding period",
        "frequency": "quarterly",
        "unit": "percent",
        "value_representation": "rate",
        "availability_basis": "source_release",
    },
    GDI_NOMINAL_SERIES_ID: {
        "provider": "bea",
        "provider_series_code": "A261RC",
        "title": "Gross domestic income",
        "frequency": "quarterly",
        "unit": "billions_usd",
        "value_representation": "level",
        "availability_basis": "source_release",
    },
    CPI_ALL_ITEMS_SERIES_ID: {
        "provider": "bls",
        "provider_series_code": "CUSR0000SA0",
        "title": "Consumer Price Index for All Urban Consumers: All Items, seasonally adjusted",
        "frequency": "monthly",
        "unit": "index",
        "value_representation": "level",
        "availability_basis": "mixed",
    },
    CPI_CORE_SERIES_ID: {
        "provider": "bls",
        "provider_series_code": "CUSR0000SA0L1E",
        "title": "Consumer Price Index for All Urban Consumers: All Items Less Food and Energy, seasonally adjusted",
        "frequency": "monthly",
        "unit": "index",
        "value_representation": "level",
        "availability_basis": "mixed",
    },
    TOTAL_NONFARM_PAYROLLS_SERIES_ID: {
        "provider": "bls",
        "provider_series_code": "CES0000000001",
        "title": "All Employees, Total Nonfarm, Seasonally Adjusted",
        "frequency": "monthly",
        "unit": "thousands_persons",
        "value_representation": "level",
        "availability_basis": "mixed",
    },
    UNEMPLOYMENT_RATE_SERIES_ID: {
        "provider": "bls",
        "provider_series_code": "LNS14000000",
        "title": "Civilian Unemployment Rate, Seasonally Adjusted",
        "frequency": "monthly",
        "unit": "percent",
        "value_representation": "rate",
        "availability_basis": "mixed",
    },
    RTDSM_NOMINAL_OUTPUT_SERIES_ID: {
        "provider": "philadelphia_fed",
        "provider_series_code": "NOUTPUT",
        "title": "RTDSM Nominal GNP/GDP, seasonally adjusted annual rate",
        "frequency": "quarterly",
        "unit": "billions_usd",
        "value_representation": "level",
        "availability_basis": "local_capture",
    },
    RTDSM_REAL_OUTPUT_SERIES_ID: {
        "provider": "philadelphia_fed",
        "provider_series_code": "ROUTPUT",
        "title": "RTDSM Real GNP/GDP, seasonally adjusted annual rate",
        "frequency": "quarterly",
        "unit": "billions_real_usd",
        "value_representation": "level",
        "availability_basis": "local_capture",
    },
}
_CPI_SERIES_IDS = frozenset({CPI_ALL_ITEMS_SERIES_ID, CPI_CORE_SERIES_ID})
_EMPLOYMENT_SERIES_IDS = frozenset(
    {TOTAL_NONFARM_PAYROLLS_SERIES_ID, UNEMPLOYMENT_RATE_SERIES_ID}
)
_CPI_BLS_SERIES_BY_CODE = {
    spec["provider_series_code"]: series_id
    for series_id, spec in _SERIES_SPECS.items()
    if series_id in _CPI_SERIES_IDS
}
_EMPLOYMENT_BLS_SERIES_BY_CODE = {
    spec["provider_series_code"]: series_id
    for series_id, spec in _SERIES_SPECS.items()
    if series_id in _EMPLOYMENT_SERIES_IDS
}
_GDP_SERIES_IDS = frozenset({GDP_REAL_SERIES_ID, GDP_NOMINAL_SERIES_ID})
_GDI_SERIES_IDS = frozenset({GDI_REAL_SERIES_ID, GDI_NOMINAL_SERIES_ID})
_BEA_GDP_GDI_SERIES_IDS = _GDP_SERIES_IDS | _GDI_SERIES_IDS
_RTDSM_OUTPUT_SERIES_IDS = frozenset(
    {RTDSM_NOMINAL_OUTPUT_SERIES_ID, RTDSM_REAL_OUTPUT_SERIES_ID}
)
_CAPTURE_PROVIDERS_BY_SERIES = {
    GDP_REAL_SERIES_ID: frozenset({"bea"}),
    GDP_NOMINAL_SERIES_ID: frozenset({"bea"}),
    GDI_REAL_SERIES_ID: frozenset({"bea"}),
    GDI_NOMINAL_SERIES_ID: frozenset({"bea"}),
    CPI_ALL_ITEMS_SERIES_ID: frozenset({"bls", "philadelphia_fed"}),
    CPI_CORE_SERIES_ID: frozenset({"bls", "philadelphia_fed"}),
    TOTAL_NONFARM_PAYROLLS_SERIES_ID: frozenset({"bls", "philadelphia_fed"}),
    UNEMPLOYMENT_RATE_SERIES_ID: frozenset({"bls", "philadelphia_fed"}),
    RTDSM_NOMINAL_OUTPUT_SERIES_ID: frozenset({"philadelphia_fed"}),
    RTDSM_REAL_OUTPUT_SERIES_ID: frozenset({"philadelphia_fed"}),
}
_CAPTURE_SERIES_BY_PROVIDER_FAMILY = {
    ("bea", "gdp"): _BEA_GDP_GDI_SERIES_IDS,
    ("bls", "cpi"): _CPI_SERIES_IDS,
    ("bls", "employment"): _EMPLOYMENT_SERIES_IDS,
    ("philadelphia_fed", "employment"): _EMPLOYMENT_SERIES_IDS,
    ("bls", "cpi_history"): _CPI_SERIES_IDS,
    ("philadelphia_fed", "cpi_history"): _CPI_SERIES_IDS,
    ("philadelphia_fed", "gdp_history"): _RTDSM_OUTPUT_SERIES_IDS,
}
_EMPLOY64M12 = re.compile(r"^EMPLOY(?P<year>\d{2})M(?P<month>[1-9]|1[0-2])$")
_RUC65Q4 = re.compile(r"^RUC(?P<year>\d{2})Q(?P<quarter>[1-4])$")
_NOUTPUT65Q4 = re.compile(r"^NOUTPUT(?P<year>\d{2})Q(?P<quarter>[1-4])$")
_ROUTPUT65Q4 = re.compile(r"^ROUTPUT(?P<year>\d{2})Q(?P<quarter>[1-4])$")
_PCPI98M11 = re.compile(r"^PCPI(?P<year>\d{2})M(?P<month>[1-9]|1[0-2])$")
_PCPIX98M11 = re.compile(r"^PCPIX(?P<year>\d{2})M(?P<month>[1-9]|1[0-2])$")
_RTDSM_SOURCE_ROW_MULTIPLIER = 10_000
_MAX_RTDSM_VINTAGE_COLUMNS = _RTDSM_SOURCE_ROW_MULTIPLIER - 1
_STAGE_ORDER = {
    "initial": 10,
    "advance": 20,
    "preliminary": 25,
    "second": 30,
    "third": 40,
    "final": 50,
    "revised": 60,
    "updated": 70,
}
_UNSAFE_JSON_KEY_PARTS = frozenset(
    {
        "apikey",
        "authorization",
        "authentication",
        "cookie",
        "credential",
        "password",
        "token",
        "userid",
    }
)
_BLS_CPI_HISTORY_WINDOWS: dict[tuple[int, int], frozenset[str]] = {
    (1947, 1956): frozenset({CPI_ALL_ITEMS_SERIES_ID}),
    (1957, 1966): _CPI_SERIES_IDS,
    (1967, 1976): _CPI_SERIES_IDS,
    (1977, 1986): _CPI_SERIES_IDS,
    (1987, 1996): _CPI_SERIES_IDS,
    (1997, 2006): _CPI_SERIES_IDS,
    (2007, 2007): _CPI_SERIES_IDS,
}


@dataclass(frozen=True, slots=True)
class VintageObservation:
    """One source-native GDP or CPI value and its release/vintage identity."""

    series_id: str
    provider_series_code: str
    period: str
    value_text: str
    unit: str
    source_vintage_identity: str
    vintage_at: str | None
    vintage_precision: str | None
    source_release_order: str
    available_at: str
    available_precision: str
    availability_basis: str
    release_stage: str | None
    is_first_release: bool | None
    first_release_evidence: str | None
    source_published_at: str | None
    source_published_precision: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class VintageCapture:
    """Completed, credential-free source evidence prepared before publication."""

    provider: str
    family: str
    source_resource: str
    media_type: str
    response_bytes: bytes = field(repr=False)
    response_sha256: str
    semantic_identity: str
    captured_at: str
    captured_precision: str
    source_published_at: str | None
    source_published_precision: str | None
    availability_basis: str
    normalization_version: str
    observations: tuple[VintageObservation, ...]


@dataclass(frozen=True, slots=True)
class VintagePublishReport:
    """Small receipt-safe summary of one append-only publication attempt."""

    outcome: str
    semantic_identity: str
    capture_id: str | None
    written_captures: int
    written_series: int
    written_releases: int
    written_versions: int
    written_current: int
    written_memberships: int


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_digest(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _require_text(value: object, field_name: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or _CONTROL.search(value) is not None
    ):
        raise _fail(f"Live vintage {field_name} must be nonempty safe text")
    return value


def _require_body(value: object, field_name: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise _fail(f"Live vintage {field_name} must be nonempty bytes")
    if len(value) > _MAX_SOURCE_BYTES:
        raise ResourceLimitError("Live vintage source body exceeds the byte bound")
    return value


def _require_source_resource(value: object) -> str:
    """Accept a source locator only when it cannot carry request credentials."""

    text = _require_text(value, "source_resource")
    lowered = text.casefold()
    if "?" in text or "#" in text or any(
        token in lowered
        for token in ("api_key", "apikey", "authorization", "token", "userid")
    ):
        raise _fail("Live vintage source_resource cannot contain request material")
    return text


def _temporal(
    value: object,
    field_name: str,
    *,
    require_datetime: bool = False,
    require_date: bool = False,
) -> tuple[str, str]:
    if not isinstance(value, str):
        raise _fail(f"Live vintage {field_name} must be an ISO temporal value")
    parsed = TemporalValue.parse(value, pointer=f"/{field_name}")
    if require_datetime and parsed.precision is not TemporalPrecision.DATETIME:
        raise _fail(f"Live vintage {field_name} must be an aware datetime")
    if require_date and parsed.precision is not TemporalPrecision.DATE:
        raise _fail(f"Live vintage {field_name} must be a calendar date")
    if parsed.precision is TemporalPrecision.DATE:
        return value, "date"
    assert isinstance(parsed.value, datetime)
    normalized = parsed.value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return normalized.replace("+00:00", "Z"), "datetime"


def _source_published_temporal(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip() or _CONTROL.search(value) is not None:
        raise _fail("Live vintage source_published_at is invalid")
    try:
        return _temporal(value, "source_published_at")
    except ValidationError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError) as exc:
            raise _fail("Live vintage source_published_at is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _fail("Live vintage source_published_at must be timezone-aware")
        normalized = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return normalized.replace("+00:00", "Z"), "datetime"


def _decimal_text(value: object, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise _fail(f"Live vintage {field_name} must be a finite number")
    raw = str(value).strip()
    if not raw:
        raise _fail(f"Live vintage {field_name} must be a finite number")
    normalized = raw.replace(",", "").replace("$", "").replace("%", "")
    try:
        parsed = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"Live vintage {field_name} must be a finite number") from exc
    if not parsed.is_finite():
        raise _fail(f"Live vintage {field_name} must be finite")
    if parsed.is_zero():
        return "0"
    return format(parsed.normalize(), "f")


def _optional_bea_gdi_value(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return _decimal_text(value, field_name)
    normalized = value.strip()
    if not normalized or normalized.casefold() in {
        "...",
        ".....",
        "…",
        "--",
        "n/a",
        "na",
        "n.a.",
    }:
        return None
    try:
        return _decimal_text(normalized, field_name)
    except ValidationError as exc:
        raise _fail(
            f"Live vintage {field_name} has unsupported source value {normalized!r}"
        ) from exc


def _bls_xlsx_index_text(value: object) -> str:
    """Remove Excel binary-float residue at BLS's 3-decimal index precision."""

    normalized = _decimal_text(value, "BLS CPI value")
    rounded = Decimal(normalized).quantize(Decimal("0.001"))
    return _decimal_text(format(rounded, "f"), "BLS CPI value")


def _normalize_quarter(value: object) -> str:
    if not isinstance(value, str):
        raise _fail("BEA GDP reference period must be text")
    text = value.strip()
    match = _QUARTER.fullmatch(text)
    if match is not None:
        year, quarter = match.groups()
    else:
        reversed_match = _QUARTER_REVERSED.fullmatch(text)
        if reversed_match is None:
            raise _fail("BEA GDP reference period must be YYYYQ1 through YYYYQ4")
        quarter, year = reversed_match.groups()
    if int(year) < 1800:
        raise _fail("BEA GDP reference period is outside the supported range")
    return f"{year}Q{quarter}"


def _normalize_month(year: object, period: object) -> str:
    if not isinstance(year, str) or not re.fullmatch(r"\d{4}", year):
        raise _fail("BLS CPI year must be YYYY")
    if int(year) < 1800:
        raise _fail("BLS CPI year is outside the supported range")
    if not isinstance(period, str):
        raise _fail("BLS CPI period is invalid")
    match = _MONTH_PERIOD.fullmatch(period)
    if match is None:
        raise _fail("BLS CPI period must be M01 through M12")
    return f"{year}-{match.group(1)}"


def _normalize_stage(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or _CONTROL.search(value) is not None:
        raise _fail("BEA GDP release stage is invalid")
    compact = " ".join(value.strip().casefold().replace("estimate", "").split())
    aliases = {
        "advance": "advance",
        "second": "second",
        "third": "third",
        "revised": "revised",
        "final": "final",
        "preliminary": "preliminary",
        "initial": "initial",
        "updated": "updated",
    }
    result = aliases.get(compact)
    if result is None:
        raise _fail("BEA GDP release stage is outside the reviewed workbook vocabulary")
    return result


def _parse_bea_release_date(value: object) -> str:
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        serial = int(value)
        if value != serial or not 1 <= serial <= 100_000:
            raise _fail("BEA GDP release date is invalid")
        # Excel's 1900 leap-year compatibility offset is intentional.
        base = date(1899, 12, 30)
        return (base + timedelta(days=serial)).isoformat()
    if not isinstance(value, str) or not value.strip():
        raise _fail("BEA GDP release date is invalid")
    text = value.strip()
    try:
        return _temporal(text, "BEA GDP release date", require_date=True)[0]
    except ValidationError:
        pass
    match = _DATE_PREFIX.search(text)
    if match is not None:
        candidate = match.group(0)
        for pattern in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(candidate, pattern).date().isoformat()
            except ValueError:
                continue
    for pattern in ("%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise _fail("BEA GDP release date is invalid")


def _column_from_reference(reference: str) -> str:
    match = re.fullmatch(r"([A-Z]+)\d+", reference)
    if match is None:
        raise _fail("BEA workbook cell reference is invalid")
    return match.group(1)


def _column_index(column: str) -> int:
    """Return a one-based Excel column index for a validated column label."""

    if not re.fullmatch(r"[A-Z]+", column):
        raise _fail("Live vintage workbook column is invalid")
    result = 0
    for character in column:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _xml_root(raw: bytes, label: str) -> ET.Element:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise _fail(f"BEA workbook {label} cannot declare entities")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise _fail(f"BEA workbook {label} is malformed XML") from exc


def _zip_members(body: bytes) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise _fail("BEA GDP body is not a valid XLSX workbook") from exc
    infos = archive.infolist()
    if not infos or len(infos) > _MAX_XLSX_MEMBERS:
        archive.close()
        raise ResourceLimitError("BEA GDP workbook has too many members")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.flag_bits & 0x1:
            archive.close()
            raise _fail("BEA GDP workbook cannot be encrypted")
        if info.filename.startswith("/") or ".." in Path(info.filename).parts:
            archive.close()
            raise _fail("BEA GDP workbook has an unsafe member path")
        total += info.file_size
        if total > _MAX_XLSX_EXPANDED_BYTES:
            archive.close()
            raise ResourceLimitError("BEA GDP workbook exceeds the expanded byte bound")
        if info.filename in members:
            archive.close()
            raise _fail("BEA GDP workbook has duplicate member names")
        members[info.filename] = info
    return archive, members


def _read_zip_member(
    archive: zipfile.ZipFile,
    members: Mapping[str, zipfile.ZipInfo],
    name: str,
    *,
    required: bool = True,
) -> bytes | None:
    info = members.get(name)
    if info is None:
        if required:
            raise _fail(f"BEA GDP workbook is missing {name}")
        return None
    try:
        return archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _fail(f"BEA GDP workbook cannot read {name}") from exc


def _shared_strings(archive: zipfile.ZipFile, members: Mapping[str, zipfile.ZipInfo]) -> list[str]:
    raw = _read_zip_member(archive, members, "xl/sharedStrings.xml", required=False)
    if raw is None:
        return []
    root = _xml_root(raw, "shared strings")
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    result: list[str] = []
    for item in root.findall(f"{namespace}si"):
        result.append("".join(text.text or "" for text in item.iter(f"{namespace}t")))
    return result


def _worksheet_by_name(
    archive: zipfile.ZipFile,
    members: Mapping[str, zipfile.ZipInfo],
    *,
    accepted_names: frozenset[str],
    label: str,
) -> str:
    workbook_raw = _read_zip_member(archive, members, "xl/workbook.xml")
    rels_raw = _read_zip_member(archive, members, "xl/_rels/workbook.xml.rels")
    workbook = _xml_root(workbook_raw or b"", "workbook")
    rels = _xml_root(rels_raw or b"", "workbook relationships")
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    document_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    targets = {
        relation.attrib.get("Id"): relation.attrib.get("Target")
        for relation in rels.findall(f"{package_rel}Relationship")
    }
    for sheet in workbook.findall(f".//{main}sheet"):
        if sheet.attrib.get("name", "").strip().casefold() not in accepted_names:
            continue
        relationship_id = sheet.attrib.get(f"{document_rel}id")
        target = targets.get(relationship_id)
        if not isinstance(target, str) or not target:
            break
        if target.startswith("/") or ".." in Path(target).parts:
            break
        result = "xl/" + target.lstrip("./")
        if result in members:
            return result
        break
    raise _fail(f"{label} workbook lacks the reviewed worksheet")


def _xlsx_rows(
    body: bytes,
    *,
    accepted_sheet_names: frozenset[str] = frozenset({"vintage history"}),
    label: str = "BEA GDP",
) -> tuple[tuple[int, dict[str, str]], ...]:
    archive, members = _zip_members(body)
    try:
        shared = _shared_strings(archive, members)
        sheet_name = _worksheet_by_name(
            archive,
            members,
            accepted_names=accepted_sheet_names,
            label=label,
        )
        sheet_raw = _read_zip_member(archive, members, sheet_name)
        root = _xml_root(sheet_raw or b"", f"{label} worksheet")
        main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        result: list[tuple[int, dict[str, str]]] = []
        seen_rows: set[int] = set()
        for row in root.findall(f".//{main}sheetData/{main}row"):
            raw_number = row.attrib.get("r")
            if not isinstance(raw_number, str) or not raw_number.isdigit() or int(raw_number) <= 0:
                raise _fail("BEA GDP workbook row number is invalid")
            row_number = int(raw_number)
            if row_number in seen_rows:
                raise _fail("BEA GDP workbook repeats a row number")
            seen_rows.add(row_number)
            cells: dict[str, str] = {}
            for cell in row.findall(f"{main}c"):
                reference = cell.attrib.get("r")
                if not isinstance(reference, str):
                    raise _fail("BEA GDP workbook cell reference is missing")
                reference_match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
                if reference_match is None or int(reference_match.group(2)) != row_number:
                    raise _fail("BEA GDP workbook cell reference is invalid")
                column = reference_match.group(1)
                if column in cells:
                    raise _fail("BEA GDP workbook repeats a cell column")
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(
                        text.text or "" for text in cell.iter(f"{main}t")
                    )
                else:
                    value_node = cell.find(f"{main}v")
                    if value_node is None or value_node.text is None:
                        continue
                    value = value_node.text
                    if cell_type == "s":
                        if not value.isdigit() or int(value) >= len(shared):
                            raise _fail("BEA GDP workbook shared string index is invalid")
                        value = shared[int(value)]
                cells[column] = value
            result.append((row_number, cells))
        return tuple(result)
    finally:
        archive.close()


def _observation_semantic(observation: VintageObservation) -> dict[str, object]:
    # Local capture time is availability evidence, not a provider release
    # identity.  A BLS current payload with the same normalized batch on the
    # following day must therefore replay without another capture/version
    # write, even though its defensible local availability instant differs.
    semantic_available_at: str | None = observation.available_at
    semantic_available_precision: str | None = observation.available_precision
    if observation.availability_basis == "local_capture":
        semantic_available_at = None
        semantic_available_precision = None
    return {
        "series_id": observation.series_id,
        "provider_series_code": observation.provider_series_code,
        "period": observation.period,
        "value_text": observation.value_text,
        "unit": observation.unit,
        "source_vintage_identity": observation.source_vintage_identity,
        "vintage_at": observation.vintage_at,
        "vintage_precision": observation.vintage_precision,
        "source_release_order": observation.source_release_order,
        "available_at": semantic_available_at,
        "available_precision": semantic_available_precision,
        "availability_basis": observation.availability_basis,
        "release_stage": observation.release_stage,
        "is_first_release": observation.is_first_release,
        "first_release_evidence": observation.first_release_evidence,
        "source_published_at": observation.source_published_at,
        "source_published_precision": observation.source_published_precision,
    }


def _capture_semantic_identity(
    *,
    provider: str,
    family: str,
    source_resource: str,
    media_type: str,
    source_published_at: str | None,
    source_published_precision: str | None,
    availability_basis: str,
    observations: Iterable[VintageObservation],
) -> str:
    normalized = sorted(
        (_observation_semantic(item) for item in observations),
        key=lambda item: (
            str(item["series_id"]),
            str(item["period"]),
            str(item["source_vintage_identity"]),
        ),
    )
    return _canonical_json_digest(
        {
            "normalization_version": NORMALIZATION_VERSION,
            "provider": provider,
            "family": family,
            "source_resource": source_resource,
            "media_type": media_type.casefold(),
            "source_published_at": source_published_at,
            "source_published_precision": source_published_precision,
            "availability_basis": availability_basis,
            "observations": normalized,
        }
    )


def _build_capture(
    *,
    provider: str,
    family: str,
    source_resource: str,
    media_type: str,
    response_bytes: bytes,
    captured_at: str,
    source_published_at: str | None,
    source_published_precision: str | None,
    availability_basis: str,
    observations: Iterable[VintageObservation],
) -> VintageCapture:
    completed = tuple(observations)
    if not completed:
        raise _fail("Live vintage capture has no selected observations")
    semantic_identity = _capture_semantic_identity(
        provider=provider,
        family=family,
        source_resource=source_resource,
        media_type=media_type,
        source_published_at=source_published_at,
        source_published_precision=source_published_precision,
        availability_basis=availability_basis,
        observations=completed,
    )
    return VintageCapture(
        provider=provider,
        family=family,
        source_resource=source_resource,
        media_type=media_type,
        response_bytes=response_bytes,
        response_sha256=_sha256(response_bytes),
        semantic_identity=semantic_identity,
        captured_at=captured_at,
        captured_precision="datetime",
        source_published_at=source_published_at,
        source_published_precision=source_published_precision,
        availability_basis=availability_basis,
        normalization_version=NORMALIZATION_VERSION,
        observations=completed,
    )


def parse_bea_gdp_vintage_xlsx(
    body: bytes,
    *,
    captured_at: str,
    source_published_at: str | None = None,
) -> VintageCapture:
    """Parse the official BEA GDP/GDI Vintage History workbook without I/O."""

    raw = _require_body(body, "BEA GDP body")
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    published, published_precision = _source_published_temporal(source_published_at)
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise _fail("BEA GDP body is not an XLSX workbook")

    parsed_rows: list[tuple[int, str, str, str, str, str | None, str | None, str]] = []
    current_period: str | None = None
    for source_row, cells in _xlsx_rows(raw):
        raw_period = cells.get("A")
        if raw_period is not None:
            try:
                current_period = _normalize_quarter(raw_period)
            except ValidationError:
                # The workbook also has headings and explanatory notes in
                # column A.  A real quarter begins a section; its following
                # vintage rows intentionally leave column A blank.
                pass
        raw_stage = cells.get("B")
        if raw_stage is None or raw_stage.strip().casefold() == "vintage":
            continue
        if current_period is None:
            raise _fail("BEA GDP/GDI workbook vintage row precedes its reference period")
        stage = _normalize_stage(raw_stage)
        required = (cells.get("B"), cells.get("C"), cells.get("E"), cells.get("G"))
        if any(value is None for value in required):
            raise _fail("BEA GDP/GDI workbook data row is incomplete")
        nominal = _decimal_text(cells["C"], "BEA nominal GDP value")
        real = _decimal_text(cells["E"], "BEA real GDP value")
        raw_nominal_gdi = cells.get("D")
        raw_real_gdi = cells.get("F")
        nominal_gdi = _optional_bea_gdi_value(
            raw_nominal_gdi,
            "BEA nominal GDI value",
        )
        real_gdi = _optional_bea_gdi_value(
            raw_real_gdi,
            "BEA real GDI value",
        )
        release_date = _parse_bea_release_date(cells["G"])
        parsed_rows.append(
            (
                source_row,
                current_period,
                stage,
                nominal,
                real,
                nominal_gdi,
                real_gdi,
                release_date,
            )
        )
    if not parsed_rows:
        raise _fail("BEA GDP/GDI workbook has no Vintage History data rows")

    earliest_by_series_period: dict[tuple[str, str], str] = {}
    for _, period, _, nominal, real, nominal_gdi, real_gdi, release_date in parsed_rows:
        for series_id, value_text in (
            (GDP_REAL_SERIES_ID, real),
            (GDP_NOMINAL_SERIES_ID, nominal),
            (GDI_REAL_SERIES_ID, real_gdi),
            (GDI_NOMINAL_SERIES_ID, nominal_gdi),
        ):
            if value_text is None:
                continue
            key = (series_id, period)
            earliest_by_series_period[key] = min(
                earliest_by_series_period.get(key, release_date), release_date
            )

    observations: list[VintageObservation] = []
    seen: set[tuple[str, str, str]] = set()
    for (
        source_row,
        period,
        stage,
        nominal,
        real,
        nominal_gdi,
        real_gdi,
        release_date,
    ) in parsed_rows:
        # Preserve the existing GDP release identity so adding the GDI columns
        # does not rewrite historical GDP release declarations.
        identity = f"bea-gdp:{period}:{release_date}:{stage}"
        release_order = f"{release_date}:{_STAGE_ORDER[stage]:03d}:{period}"
        for series_id, value_text in (
            (GDP_REAL_SERIES_ID, real),
            (GDP_NOMINAL_SERIES_ID, nominal),
            (GDI_REAL_SERIES_ID, real_gdi),
            (GDI_NOMINAL_SERIES_ID, nominal_gdi),
        ):
            if value_text is None:
                continue
            key = (series_id, period, identity)
            if key in seen:
                raise _fail("BEA GDP/GDI workbook has duplicate vintage observations")
            seen.add(key)
            first = release_date == earliest_by_series_period[(series_id, period)]
            source_name = "gdp" if series_id in _GDP_SERIES_IDS else "gdi"
            first_evidence = (
                f"bea_{source_name}_vintage_history:{period}:earliest_release_date:{release_date}"
                if first
                else None
            )
            spec = _SERIES_SPECS[series_id]
            observations.append(
                VintageObservation(
                    series_id=series_id,
                    provider_series_code=spec["provider_series_code"],
                    period=period,
                    value_text=value_text,
                    unit=spec["unit"],
                    source_vintage_identity=identity,
                    vintage_at=release_date,
                    vintage_precision="date",
                    source_release_order=release_order,
                    available_at=release_date,
                    available_precision="date",
                    availability_basis="source_release",
                    release_stage=stage,
                    is_first_release=first,
                    first_release_evidence=first_evidence,
                    source_published_at=None,
                    source_published_precision=None,
                    source_row=source_row,
                )
            )
    return _build_capture(
        provider="bea",
        family="gdp",
        source_resource=BEA_GDP_VINTAGE_HISTORY_RESOURCE,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        response_bytes=raw,
        captured_at=captured,
        source_published_at=published,
        source_published_precision=published_precision,
        availability_basis="source_release",
        observations=observations,
    )


def _decompress_bls_body(body: bytes) -> bytes:
    if not body.startswith(b"\x1f\x8b"):
        return body
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as stream:
            payload = stream.read(_MAX_BLS_EXPANDED_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise _fail("BLS CPI archive is not a valid gzip payload") from exc
    if len(payload) > _MAX_BLS_EXPANDED_BYTES:
        raise ResourceLimitError("BLS CPI archive exceeds the expanded byte bound")
    return payload


def _bls_archive_identity(source_resource: str, vintage_at: str) -> tuple[str, str]:
    resource_digest = _sha256(source_resource.encode("utf-8"))[:16]
    return (
        f"bls-cpi-archive:{vintage_at}:{resource_digest}",
        f"{vintage_at}:archive:{resource_digest}",
    )


def _bls_embedded_vintage(payload: bytes) -> str | None:
    """Return the source's own publication date when the archive carries it."""

    if zipfile.is_zipfile(io.BytesIO(payload)):
        archive, members = _zip_members(payload)
        try:
            raw = _read_zip_member(
                archive,
                members,
                "docProps/core.xml",
                required=False,
            )
            if raw is None:
                return None
            root = _xml_root(raw, "BLS CPI workbook properties")
            namespace = "{http://purl.org/dc/terms/}"
            for name in ("modified", "created"):
                node = root.find(f"{namespace}{name}")
                if node is None or node.text is None:
                    continue
                value, _ = _temporal(
                    node.text.strip(),
                    f"BLS CPI workbook {name}",
                    require_datetime=True,
                )
                return value[:10]
            return None
        finally:
            archive.close()

    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail("BLS CPI archive is not strict UTF-8") from exc
    match = re.search(
        r"\breleased\s+([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    return None if match is None else _parse_bea_release_date(match.group(1))


def _bls_text_observations(payload: bytes) -> tuple[tuple[str, str, str, int], ...]:
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail("BLS CPI archive is not strict UTF-8") from exc
    observations: list[tuple[str, str, str, int]] = []
    current_series: str | None = None
    adjusted_series = False
    for source_line, line in enumerate(text.splitlines(), start=1):
        if source_line > _MAX_BLS_LINES:
            raise ResourceLimitError("BLS CPI archive exceeds the line bound")
        stripped = line.strip()
        if not stripped:
            continue
        first = stripped.split()[0]
        if first.startswith(("CUSR", "CWSR")):
            current_series = _CPI_BLS_SERIES_BY_CODE.get(first)
            adjusted_series = False
            continue
        if current_series is None:
            continue
        upper = stripped.upper()
        if "SEASONALLY ADJUSTED SERIES" in upper:
            adjusted_series = True
            continue
        parts = stripped.split()
        if not adjusted_series or re.fullmatch(r"\d{4}", parts[0]) is None:
            continue
        if len(parts) < 2 or len(parts) > 13:
            raise _fail("BLS CPI text archive selected row is incomplete")
        for month, raw_value in enumerate(parts[1:], start=1):
            period = _normalize_month(parts[0], f"M{month:02d}")
            observations.append(
                (
                    current_series,
                    period,
                    _decimal_text(raw_value, "BLS CPI value"),
                    source_line * 100 + month,
                )
            )
    return tuple(observations)


def _bls_xlsx_observations(payload: bytes) -> tuple[tuple[str, str, str, int], ...]:
    rows = _xlsx_rows(
        payload,
        accepted_sheet_names=frozenset({"u_s__city_avg"}),
        label="BLS CPI",
    )
    month_names = (
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    )
    columns: dict[str, str] | None = None
    observations: list[tuple[str, str, str, int]] = []
    for source_row, cells in rows:
        if columns is None:
            candidate = {
                re.sub(r"[^a-z0-9]", "", value.casefold()): column
                for column, value in cells.items()
                if value.strip()
            }
            required = {"seriesid", "datatype", "year", *month_names}
            if required.issubset(candidate):
                columns = candidate
            continue
        provider_code = cells.get(columns["seriesid"])
        if provider_code not in _CPI_BLS_SERIES_BY_CODE:
            continue
        data_type = cells.get(columns["datatype"], "")
        if " ".join(data_type.strip().casefold().split()) != "seasonally adjusted index":
            continue
        year = cells.get(columns["year"])
        if year is None:
            raise _fail("BLS CPI workbook selected row lacks a year")
        series_id = _CPI_BLS_SERIES_BY_CODE[provider_code]
        for month, month_name in enumerate(month_names, start=1):
            raw_value = cells.get(columns[month_name])
            if raw_value is None or not raw_value.strip():
                continue
            observations.append(
                (
                    series_id,
                    _normalize_month(year, f"M{month:02d}"),
                    _bls_xlsx_index_text(raw_value),
                    source_row * 100 + month,
                )
            )
    if columns is None:
        raise _fail("BLS CPI workbook lacks the reviewed header")
    return tuple(observations)


def parse_bls_cpi_revision(
    body: bytes,
    *,
    source_resource: str,
    vintage_at: str,
    captured_at: str,
    media_type: str,
) -> VintageCapture:
    """Parse an official BLS annual seasonal-adjustment archive snapshot."""

    raw = _require_body(body, "BLS CPI archive body")
    resource = _require_source_resource(source_resource)
    media = _require_text(media_type, "media_type", maximum=255)
    fallback_vintage, _ = _temporal(vintage_at, "vintage_at", require_date=True)
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    payload = _decompress_bls_body(raw)
    vintage = _bls_embedded_vintage(payload) or fallback_vintage
    vintage_precision = "date"
    identity, release_order = _bls_archive_identity(resource, vintage)
    observations: list[VintageObservation] = []
    seen: set[tuple[str, str]] = set()
    selected_series: set[str] = set()
    staged = (
        _bls_xlsx_observations(payload)
        if zipfile.is_zipfile(io.BytesIO(payload))
        else _bls_text_observations(payload)
    )
    for series_id, period, value_text, source_row in staged:
        provider_code = _SERIES_SPECS[series_id]["provider_series_code"]
        key = (series_id, period)
        if key in seen:
            raise _fail("BLS CPI archive has duplicate selected observations")
        seen.add(key)
        selected_series.add(series_id)
        observations.append(
            VintageObservation(
                series_id=series_id,
                provider_series_code=provider_code,
                period=period,
                value_text=value_text,
                unit="index",
                source_vintage_identity=identity,
                vintage_at=vintage,
                vintage_precision=vintage_precision,
                source_release_order=release_order,
                available_at=vintage,
                available_precision="date",
                availability_basis="source_release",
                release_stage="annual_revision",
                is_first_release=None,
                first_release_evidence=None,
                source_published_at=vintage,
                source_published_precision=vintage_precision,
                source_row=source_row,
            )
        )
    if selected_series != {CPI_ALL_ITEMS_SERIES_ID, CPI_CORE_SERIES_ID}:
        raise _fail("BLS CPI archive does not contain both reviewed CPI series")
    return _build_capture(
        provider="bls",
        family="cpi",
        source_resource=resource,
        media_type=media,
        response_bytes=raw,
        captured_at=captured,
        source_published_at=vintage,
        source_published_precision=vintage_precision,
        availability_basis="source_release",
        observations=observations,
    )


@dataclass(frozen=True, slots=True)
class _RtdsmHeader:
    """One reviewed RTDSM snapshot column in source workbook order."""

    column: str
    identity: str
    ordinal: int
    vintage_index: int
    maximum_period_index: int


def _rtdsm_two_digit_year(value: str) -> int:
    """Map reviewed RTDSM two-digit years into the 1964-present range."""

    year = int(value)
    return 2000 + year if year <= 50 else 1900 + year


def _normalize_rtdsm_month(value: object) -> tuple[str, int]:
    if not isinstance(value, str):
        raise _fail("Philadelphia Fed RTDSM reference period must be text")
    match = re.fullmatch(r"(\d{4}):(0?[1-9]|1[0-2])", value.strip())
    if match is None:
        raise _fail("Philadelphia Fed RTDSM reference period must be YYYY:MM")
    year, month = match.groups()
    if int(year) < 1800:
        raise _fail("Philadelphia Fed RTDSM reference period is outside the supported range")
    return f"{year}-{int(month):02d}", int(year) * 12 + int(month)


def _normalize_rtdsm_quarter(value: object) -> tuple[str, int]:
    if not isinstance(value, str):
        raise _fail("Philadelphia Fed RTDSM reference quarter must be text")
    match = re.fullmatch(r"(\d{4}):Q?([1-4])", value.strip(), re.IGNORECASE)
    if match is None:
        raise _fail("Philadelphia Fed RTDSM reference quarter must be YYYY:Q")
    year, quarter = match.groups()
    if int(year) < 1800:
        raise _fail("Philadelphia Fed RTDSM reference quarter is outside the supported range")
    return f"{year}Q{quarter}", int(year) * 12 + int(quarter) * 3


def _rtdsm_header(
    value: object,
    *,
    pattern: re.Pattern[str],
    label: str,
) -> tuple[str, int, int]:
    if not isinstance(value, str):
        raise _fail(f"Philadelphia Fed {label} header must be text")
    identity = value.strip()
    match = pattern.fullmatch(identity)
    if match is None:
        raise _fail(f"Philadelphia Fed {label} header is outside the reviewed vocabulary")
    year = _rtdsm_two_digit_year(match.group("year"))
    period_key = "month" if "month" in match.groupdict() else "quarter"
    subperiod = int(match.group(period_key))
    return identity, year, subperiod


def _rtdsm_headers(
    cells: Mapping[str, str],
    *,
    pattern: re.Pattern[str],
    label: str,
    captured_at: str,
    monthly_vintages: bool,
) -> tuple[str, tuple[_RtdsmHeader, ...]]:
    """Validate the exact contiguous RTDSM header sequence left-to-right."""

    date_columns = [
        column
        for column, value in cells.items()
        if value.strip().casefold() == "date"
    ]
    if len(date_columns) != 1:
        raise _fail(f"Philadelphia Fed {label} workbook lacks one DATE header")
    date_column = date_columns[0]
    date_index = _column_index(date_column)
    ordered = sorted(cells.items(), key=lambda item: _column_index(item[0]))
    headers: list[_RtdsmHeader] = []
    expected_column = date_index + 1
    previous_vintage_index: int | None = None
    captured_value = TemporalValue.parse(captured_at, pointer="/captured_at")
    assert isinstance(captured_value.value, datetime)
    captured_datetime = captured_value.value.astimezone(timezone.utc)
    captured_limit = (
        captured_datetime.year * 12 + captured_datetime.month
        if monthly_vintages
        else captured_datetime.year * 4 + (captured_datetime.month - 1) // 3 + 1
    )
    for column, value in ordered:
        column_index = _column_index(column)
        if column_index < date_index:
            raise _fail(f"Philadelphia Fed {label} header has cells before DATE")
        if column == date_column:
            continue
        if column_index != expected_column:
            raise _fail(f"Philadelphia Fed {label} headers are not physically contiguous")
        expected_column += 1
        if len(headers) >= _MAX_RTDSM_VINTAGE_COLUMNS:
            raise ResourceLimitError(
                f"Philadelphia Fed {label} workbook has too many vintage columns"
            )
        identity, year, subperiod = _rtdsm_header(value, pattern=pattern, label=label)
        vintage_index = year * (12 if monthly_vintages else 4) + subperiod
        if previous_vintage_index is not None and vintage_index != previous_vintage_index + 1:
            raise _fail(f"Philadelphia Fed {label} headers are not chronologically contiguous")
        if vintage_index > captured_limit:
            raise _fail(f"Philadelphia Fed {label} header is later than local capture")
        previous_vintage_index = vintage_index
        headers.append(
            _RtdsmHeader(
                column=column,
                identity=identity,
                ordinal=len(headers) + 1,
                vintage_index=vintage_index,
                maximum_period_index=(
                    year * 12 + subperiod
                    if monthly_vintages
                    else year * 12 + subperiod * 3
                ),
            )
        )
    if not headers:
        raise _fail(f"Philadelphia Fed {label} workbook has no reviewed vintage headers")
    return date_column, tuple(headers)


def _rtdsm_matrix_capture(
    body: bytes,
    *,
    captured_at: str,
    source_resource: str,
    accepted_sheet_name: str,
    header_pattern: re.Pattern[str],
    header_label: str,
    monthly_vintages: bool,
    reference_frequency: str,
    family: str,
    series_id: str,
) -> VintageCapture:
    """Parse one source-native RTDSM matrix without performing any I/O."""

    raw = _require_body(body, f"Philadelphia Fed {header_label} body")
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise _fail(f"Philadelphia Fed {header_label} body is not an XLSX workbook")
    rows = _xlsx_rows(
        raw,
        accepted_sheet_names=frozenset({accepted_sheet_name}),
        label=f"Philadelphia Fed {header_label}",
    )
    header_rows = [
        (source_row, cells)
        for source_row, cells in rows
        if any(value.strip().casefold() == "date" for value in cells.values())
    ]
    if len(header_rows) != 1:
        raise _fail(f"Philadelphia Fed {header_label} workbook must have one matrix header")
    header_row, header_cells = header_rows[0]
    date_column, headers = _rtdsm_headers(
        header_cells,
        pattern=header_pattern,
        label=header_label,
        captured_at=captured,
        monthly_vintages=monthly_vintages,
    )

    data_rows: list[tuple[int, str, int, Mapping[str, str]]] = []
    seen_periods: set[str] = set()
    previous_period_index: int | None = None
    header_columns = {header.column for header in headers}
    for source_row, cells in rows:
        if source_row == header_row:
            continue
        raw_period = cells.get(date_column)
        if raw_period is None:
            continue
        try:
            period, period_index = (
                _normalize_rtdsm_month(raw_period)
                if reference_frequency == "monthly"
                else _normalize_rtdsm_quarter(raw_period)
            )
        except ValidationError:
            if any(column in cells for column in header_columns):
                raise
            continue
        if period in seen_periods:
            raise _fail(f"Philadelphia Fed {header_label} workbook repeats a reference period")
        if previous_period_index is not None and period_index <= previous_period_index:
            raise _fail(f"Philadelphia Fed {header_label} reference periods are not chronological")
        seen_periods.add(period)
        previous_period_index = period_index
        data_rows.append((source_row, period, period_index, cells))
    if not data_rows:
        raise _fail(f"Philadelphia Fed {header_label} workbook has no reference periods")

    spec = _SERIES_SPECS[series_id]
    last_value_by_period: dict[str, str] = {}
    observations: list[VintageObservation] = []
    for header in headers:
        for source_row, period, period_index, cells in data_rows:
            raw_value = cells.get(header.column)
            if raw_value is None or not raw_value.strip() or raw_value.strip().casefold() == "#n/a":
                continue
            if period_index > header.maximum_period_index:
                raise _fail(
                    f"Philadelphia Fed {header_label} workbook has a future observation cell"
                )
            value_text = _decimal_text(raw_value, f"Philadelphia Fed {header_label} value")
            if last_value_by_period.get(period) == value_text:
                continue
            last_value_by_period[period] = value_text
            # A matrix cell is identified by source row plus its chronological
            # vintage-column ordinal. This remains unique and stable inside one
            # single-series capture while preserving recoverable row provenance.
            source_ordinal = source_row * _RTDSM_SOURCE_ROW_MULTIPLIER + header.ordinal
            observations.append(
                VintageObservation(
                    series_id=series_id,
                    provider_series_code=spec["provider_series_code"],
                    period=period,
                    value_text=value_text,
                    unit=spec["unit"],
                    source_vintage_identity=header.identity,
                    vintage_at=None,
                    vintage_precision=None,
                    source_release_order=f"{header.ordinal:06d}:{header.identity}",
                    available_at=captured,
                    available_precision=captured_precision,
                    availability_basis="local_capture",
                    release_stage=None,
                    is_first_release=None,
                    first_release_evidence=None,
                    source_published_at=None,
                    source_published_precision=None,
                    source_row=source_ordinal,
                )
            )
    return _build_capture(
        provider="philadelphia_fed",
        family=family,
        source_resource=source_resource,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        response_bytes=raw,
        captured_at=captured,
        source_published_at=None,
        source_published_precision=None,
        availability_basis="local_capture",
        observations=observations,
    )


def parse_rtdsm_payroll_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse the official Philadelphia Fed monthly payroll RTDSM matrix."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_PAYROLL_RTDSM_RESOURCE,
        accepted_sheet_name="employ",
        header_pattern=_EMPLOY64M12,
        header_label="EMPLOY",
        monthly_vintages=True,
        reference_frequency="monthly",
        family="employment",
        series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
    )


def parse_rtdsm_unemployment_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse the official Philadelphia Fed quarterly-vintage unemployment matrix."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_UNEMPLOYMENT_RTDSM_RESOURCE,
        accepted_sheet_name="ruc",
        header_pattern=_RUC65Q4,
        header_label="RUC",
        monthly_vintages=False,
        reference_frequency="monthly",
        family="employment",
        series_id=UNEMPLOYMENT_RATE_SERIES_ID,
    )


def parse_rtdsm_nominal_output_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse quarterly RTDSM nominal GNP/GDP vintage levels."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_NOMINAL_OUTPUT_RTDSM_RESOURCE,
        accepted_sheet_name="noutput",
        header_pattern=_NOUTPUT65Q4,
        header_label="NOUTPUT",
        monthly_vintages=False,
        reference_frequency="quarterly",
        family="gdp_history",
        series_id=RTDSM_NOMINAL_OUTPUT_SERIES_ID,
    )


def parse_rtdsm_real_output_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse quarterly RTDSM real GNP/GDP vintage levels."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_REAL_OUTPUT_RTDSM_RESOURCE,
        accepted_sheet_name="routput",
        header_pattern=_ROUTPUT65Q4,
        header_label="ROUTPUT",
        monthly_vintages=False,
        reference_frequency="quarterly",
        family="gdp_history",
        series_id=RTDSM_REAL_OUTPUT_SERIES_ID,
    )


def parse_rtdsm_cpi_all_items_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse monthly RTDSM all-items CPI vintage levels."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_CPI_ALL_ITEMS_RTDSM_RESOURCE,
        accepted_sheet_name="pcpi",
        header_pattern=_PCPI98M11,
        header_label="PCPI",
        monthly_vintages=True,
        reference_frequency="monthly",
        family="cpi_history",
        series_id=CPI_ALL_ITEMS_SERIES_ID,
    )


def parse_rtdsm_cpi_core_vintage_xlsx(
    body: bytes, *, captured_at: str
) -> VintageCapture:
    """Parse monthly RTDSM core CPI vintage levels."""

    return _rtdsm_matrix_capture(
        body,
        captured_at=captured_at,
        source_resource=PHILADELPHIA_FED_CPI_CORE_RTDSM_RESOURCE,
        accepted_sheet_name="pcpix",
        header_pattern=_PCPIX98M11,
        header_label="PCPIX",
        monthly_vintages=True,
        reference_frequency="monthly",
        family="cpi_history",
        series_id=CPI_CORE_SERIES_ID,
    )


def _normalized_json_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _sanitize_json(value: object) -> tuple[object, bool]:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        changed = False
        for key, item in value.items():
            if not isinstance(key, str):
                raise _fail("BLS current response object keys must be strings")
            normalized = _normalized_json_key(key)
            if any(part in normalized for part in _UNSAFE_JSON_KEY_PARTS):
                changed = True
                continue
            sanitized, child_changed = _sanitize_json(item)
            result[key] = sanitized
            changed = changed or child_changed
        return result, changed
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            sanitized, child_changed = _sanitize_json(item)
            result.append(sanitized)
            changed = changed or child_changed
        return result, changed
    return value, False


def _parse_bls_current_evidence(body: bytes) -> tuple[dict[str, Any], bytes]:
    raw = _require_body(body, "BLS current response body")
    parsed = loads_strict(raw, max_bytes=_MAX_SOURCE_BYTES)
    if not isinstance(parsed, dict):
        raise _fail("BLS current response must be an object")
    sanitized, changed = _sanitize_json(parsed)
    if not isinstance(sanitized, dict):  # defensive: mappings remain mappings
        raise _fail("BLS current response must be an object")
    evidence = dumps_strict(sanitized, max_bytes=_MAX_SOURCE_BYTES).encode("utf-8") if changed else raw
    return sanitized, evidence


def parse_bls_current_json(body: bytes, *, captured_at: str) -> VintageCapture:
    """Parse a prospective BLS current API response with local availability.

    The response's volatile metadata and ``captured_at`` are intentionally not
    part of the normalized batch identity.  A later daily poll carrying the
    same selected observations is therefore an exact semantic no-op.
    """

    parsed, evidence = _parse_bls_current_evidence(body)
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    status = parsed.get("status")
    if status != "REQUEST_SUCCEEDED":
        raise _fail("BLS current response did not report success")
    results = parsed.get("Results")
    if not isinstance(results, dict):
        raise _fail("BLS current response Results is invalid")
    raw_series = results.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise _fail("BLS current response has no series")

    staged: list[tuple[str, str, str, int]] = []
    seen_series: set[str] = set()
    seen_values: set[tuple[str, str]] = set()
    for series_position, series in enumerate(raw_series, start=1):
        if not isinstance(series, dict):
            raise _fail("BLS current response series is invalid")
        provider_code = series.get("seriesID")
        if not isinstance(provider_code, str) or provider_code not in _CPI_BLS_SERIES_BY_CODE:
            raise _fail("BLS current response has an unsupported series")
        series_id = _CPI_BLS_SERIES_BY_CODE[provider_code]
        if series_id in seen_series:
            raise _fail("BLS current response repeats a series")
        seen_series.add(series_id)
        data = series.get("data")
        if not isinstance(data, list) or not data:
            raise _fail("BLS current response series has no observations")
        monthly_count = 0
        for data_position, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise _fail("BLS current response observation is invalid")
            source_period = item.get("period")
            if source_period == "M13":
                continue
            period = _normalize_month(item.get("year"), source_period)
            raw_value = item.get("value")
            if isinstance(raw_value, str) and raw_value.strip() == "-":
                # BLS uses '-' for an explicitly unavailable monthly index
                # (for example October 2025).  It is not a numeric zero and
                # must not become a canonical observation.
                continue
            value_text = _decimal_text(raw_value, "BLS current CPI value")
            key = (series_id, period)
            if key in seen_values:
                raise _fail("BLS current response repeats an observation")
            seen_values.add(key)
            monthly_count += 1
            # Source-row provenance is series-local.  Membership is keyed by
            # capture, series, and source row, so repeated positions across
            # the two reviewed series remain distinct.
            staged.append((series_id, period, value_text, data_position))
        if monthly_count == 0:
            raise _fail("BLS current response has no monthly observations")
    if seen_series != _CPI_SERIES_IDS:
        raise _fail("BLS current response does not contain both reviewed CPI series")

    batch_digest = _canonical_json_digest(
        [
            {"series_id": series_id, "period": period, "value_text": value_text}
            for series_id, period, value_text, _ in sorted(staged)
        ]
    )
    identity = f"bls-current:{batch_digest}"
    release_order = f"observed:{batch_digest}"
    observations: list[VintageObservation] = []
    for series_id, period, value_text, source_row in staged:
        spec = _SERIES_SPECS[series_id]
        observations.append(
            VintageObservation(
                series_id=series_id,
                provider_series_code=spec["provider_series_code"],
                period=period,
                value_text=value_text,
                unit="index",
                source_vintage_identity=identity,
                vintage_at=None,
                vintage_precision=None,
                source_release_order=release_order,
                available_at=captured,
                available_precision=captured_precision,
                availability_basis="local_capture",
                release_stage=None,
                is_first_release=None,
                first_release_evidence=None,
                source_published_at=None,
                source_published_precision=None,
                source_row=source_row,
            )
        )
    return _build_capture(
        provider="bls",
        family="cpi",
        source_resource=BLS_CURRENT_RESOURCE,
        media_type="application/json",
        response_bytes=evidence,
        captured_at=captured,
        source_published_at=None,
        source_published_precision=None,
        availability_basis="local_capture",
        observations=observations,
    )


def parse_bls_cpi_history_json(
    body: bytes,
    *,
    captured_at: str,
    start_year: int,
    end_year: int,
) -> VintageCapture:
    """Parse one fixed credential-free BLS deep-history response window."""

    if (
        isinstance(start_year, bool)
        or not isinstance(start_year, int)
        or isinstance(end_year, bool)
        or not isinstance(end_year, int)
    ):
        raise _fail("BLS CPI history years must be integers")
    expected_series = _BLS_CPI_HISTORY_WINDOWS.get((start_year, end_year))
    if expected_series is None:
        raise _fail("BLS CPI history window is outside the reviewed plan")

    parsed, evidence = _parse_bls_current_evidence(body)
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    if parsed.get("status") != "REQUEST_SUCCEEDED":
        raise _fail("BLS CPI history response did not report success")
    results = parsed.get("Results")
    if not isinstance(results, dict):
        raise _fail("BLS CPI history response Results is invalid")
    raw_series = results.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise _fail("BLS CPI history response has no series")

    expected_periods = {
        f"{year}-{month:02d}"
        for year in range(start_year, end_year + 1)
        for month in range(1, 13)
    }
    staged: list[tuple[str, str, str, int]] = []
    selected_series: set[str] = set()
    selected_periods: dict[str, set[str]] = {}
    for series in raw_series:
        if not isinstance(series, dict):
            raise _fail("BLS CPI history response series is invalid")
        provider_code = series.get("seriesID")
        if (
            not isinstance(provider_code, str)
            or provider_code not in _CPI_BLS_SERIES_BY_CODE
        ):
            raise _fail("BLS CPI history response has an unsupported series")
        series_id = _CPI_BLS_SERIES_BY_CODE[provider_code]
        if series_id not in expected_series or series_id in selected_series:
            raise _fail("BLS CPI history response series set is invalid")
        selected_series.add(series_id)
        periods: set[str] = set()
        data = series.get("data")
        if not isinstance(data, list) or not data:
            raise _fail("BLS CPI history response series has no observations")
        for data_position, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise _fail("BLS CPI history response observation is invalid")
            if item.get("period") == "M13":
                continue
            period = _normalize_month(item.get("year"), item.get("period"))
            if period not in expected_periods or period in periods:
                raise _fail("BLS CPI history response period is outside its reviewed window")
            raw_value = item.get("value")
            if isinstance(raw_value, str) and raw_value.strip() == "-":
                raise _fail("BLS CPI history response has an unavailable reviewed value")
            periods.add(period)
            staged.append(
                (
                    series_id,
                    period,
                    _decimal_text(raw_value, "BLS CPI history value"),
                    data_position,
                )
            )
        selected_periods[series_id] = periods
    if selected_series != expected_series or any(
        selected_periods.get(series_id) != expected_periods
        for series_id in expected_series
    ):
        raise _fail("BLS CPI history response is not a complete reviewed window")

    batch_digest = _canonical_json_digest(
        {
            "end_year": end_year,
            "observations": [
                {"series_id": series_id, "period": period, "value_text": value_text}
                for series_id, period, value_text, _ in sorted(staged)
            ],
            "start_year": start_year,
        }
    )
    identity = f"bls-cpi-history:{start_year}:{end_year}:{batch_digest}"
    release_order = f"observed:{start_year}:{end_year}:{batch_digest}"
    observations = tuple(
        VintageObservation(
            series_id=series_id,
            provider_series_code=_SERIES_SPECS[series_id]["provider_series_code"],
            period=period,
            value_text=value_text,
            unit="index",
            source_vintage_identity=identity,
            vintage_at=None,
            vintage_precision=None,
            source_release_order=release_order,
            available_at=captured,
            available_precision=captured_precision,
            availability_basis="local_capture",
            release_stage=None,
            is_first_release=None,
            first_release_evidence=None,
            source_published_at=None,
            source_published_precision=None,
            source_row=source_row,
        )
        for series_id, period, value_text, source_row in staged
    )
    return _build_capture(
        provider="bls",
        family="cpi_history",
        source_resource=BLS_CURRENT_RESOURCE,
        media_type="application/json",
        response_bytes=evidence,
        captured_at=captured,
        source_published_at=None,
        source_published_precision=None,
        availability_basis="local_capture",
        observations=observations,
    )


def parse_bls_employment_current_json(body: bytes, *, captured_at: str) -> VintageCapture:
    """Parse the fixed two-series BLS employment current response.

    Both requested series are mandatory. The API response and capture time are
    retained as evidence/availability, but volatile response metadata and the
    local capture instant are excluded from the normalized batch identity.
    """

    parsed, evidence = _parse_bls_current_evidence(body)
    captured, captured_precision = _temporal(
        captured_at, "captured_at", require_datetime=True
    )
    if parsed.get("status") != "REQUEST_SUCCEEDED":
        raise _fail("BLS current employment response did not report success")
    results = parsed.get("Results")
    if not isinstance(results, dict):
        raise _fail("BLS current employment response Results is invalid")
    raw_series = results.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise _fail("BLS current employment response has no series")

    staged: list[tuple[str, str, str, int]] = []
    seen_series: set[str] = set()
    seen_values: set[tuple[str, str]] = set()
    for series_position, series in enumerate(raw_series, start=1):
        del series_position  # source rows are series-local BLS positions.
        if not isinstance(series, dict):
            raise _fail("BLS current employment response series is invalid")
        provider_code = series.get("seriesID")
        if (
            not isinstance(provider_code, str)
            or provider_code not in _EMPLOYMENT_BLS_SERIES_BY_CODE
        ):
            raise _fail("BLS current employment response has an unsupported series")
        series_id = _EMPLOYMENT_BLS_SERIES_BY_CODE[provider_code]
        if series_id in seen_series:
            raise _fail("BLS current employment response repeats a series")
        seen_series.add(series_id)
        data = series.get("data")
        if not isinstance(data, list) or not data:
            raise _fail("BLS current employment response series has no observations")
        monthly_count = 0
        for data_position, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise _fail("BLS current employment response observation is invalid")
            source_period = item.get("period")
            if source_period == "M13":
                continue
            period = _normalize_month(item.get("year"), source_period)
            raw_value = item.get("value")
            if isinstance(raw_value, str) and raw_value.strip() == "-":
                continue
            value_text = _decimal_text(raw_value, "BLS current employment value")
            key = (series_id, period)
            if key in seen_values:
                raise _fail("BLS current employment response repeats an observation")
            seen_values.add(key)
            monthly_count += 1
            staged.append((series_id, period, value_text, data_position))
        if monthly_count == 0:
            raise _fail("BLS current employment response has no monthly observations")
    if seen_series != _EMPLOYMENT_SERIES_IDS:
        raise _fail(
            "BLS current employment response does not contain both reviewed employment series"
        )

    batch_digest = _canonical_json_digest(
        [
            {"series_id": series_id, "period": period, "value_text": value_text}
            for series_id, period, value_text, _ in sorted(staged)
        ]
    )
    identity = f"bls-employment-current:{batch_digest}"
    release_order = f"observed:{batch_digest}"
    observations: list[VintageObservation] = []
    for series_id, period, value_text, source_row in staged:
        spec = _SERIES_SPECS[series_id]
        observations.append(
            VintageObservation(
                series_id=series_id,
                provider_series_code=spec["provider_series_code"],
                period=period,
                value_text=value_text,
                unit=spec["unit"],
                source_vintage_identity=identity,
                vintage_at=None,
                vintage_precision=None,
                source_release_order=release_order,
                available_at=captured,
                available_precision=captured_precision,
                availability_basis="local_capture",
                release_stage=None,
                is_first_release=None,
                first_release_evidence=None,
                source_published_at=None,
                source_published_precision=None,
                source_row=source_row,
            )
        )
    return _build_capture(
        provider="bls",
        family="employment",
        source_resource=BLS_CURRENT_RESOURCE,
        media_type="application/json",
        response_bytes=evidence,
        captured_at=captured,
        source_published_at=None,
        source_published_precision=None,
        availability_basis="local_capture",
        observations=observations,
    )


def _validate_observation(observation: object, capture: VintageCapture) -> VintageObservation:
    if not isinstance(observation, VintageObservation):
        raise _fail("Live vintage capture observation has the wrong type")
    spec = _SERIES_SPECS.get(observation.series_id)
    if spec is None:
        raise _fail("Live vintage capture has an unsupported series")
    if (
        observation.provider_series_code != spec["provider_series_code"]
        or capture.provider not in _CAPTURE_PROVIDERS_BY_SERIES[observation.series_id]
    ):
        raise _fail("Live vintage observation series identity is invalid")
    if observation.unit != spec["unit"]:
        raise _fail("Live vintage observation unit is invalid")
    if observation.series_id in _BEA_GDP_GDI_SERIES_IDS | _RTDSM_OUTPUT_SERIES_IDS:
        _normalize_quarter(observation.period)
    else:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", observation.period):
            raise _fail("Live vintage monthly observation period is invalid")
    if _decimal_text(observation.value_text, "observation value") != observation.value_text:
        raise _fail("Live vintage observation value must be normalized")
    _require_text(observation.source_vintage_identity, "source_vintage_identity")
    _require_text(observation.source_release_order, "source_release_order")
    available, available_precision = _temporal(observation.available_at, "available_at")
    if available != observation.available_at or available_precision != observation.available_precision:
        raise _fail("Live vintage observation availability is not canonical")
    if observation.availability_basis not in {"source_release", "local_capture"}:
        raise _fail("Live vintage observation availability basis is invalid")
    if observation.availability_basis != capture.availability_basis:
        raise _fail("Live vintage observation availability basis does not match capture")
    if observation.vintage_at is None:
        if observation.vintage_precision is not None:
            raise _fail("Live vintage observation has invalid null vintage precision")
    else:
        vintage, vintage_precision = _temporal(observation.vintage_at, "vintage_at")
        if vintage != observation.vintage_at or vintage_precision != observation.vintage_precision:
            raise _fail("Live vintage observation vintage is not canonical")
    if observation.source_published_at is None:
        if observation.source_published_precision is not None:
            raise _fail("Live vintage observation source publication precision is invalid")
    else:
        published, published_precision = _temporal(
            observation.source_published_at, "source_published_at"
        )
        if (
            published != observation.source_published_at
            or published_precision != observation.source_published_precision
        ):
            raise _fail("Live vintage observation source publication is not canonical")
    if observation.is_first_release is not None and not isinstance(
        observation.is_first_release, bool
    ):
        raise _fail("Live vintage observation first-release flag is invalid")
    if observation.is_first_release is True and not observation.first_release_evidence:
        raise _fail("Live vintage first-release assertion lacks evidence")
    if observation.is_first_release is not True and observation.first_release_evidence is not None:
        raise _fail("Live vintage non-first release cannot carry first-release evidence")
    if observation.release_stage is not None:
        _require_text(observation.release_stage, "release_stage", maximum=128)
    if (
        isinstance(observation.source_row, bool)
        or not isinstance(observation.source_row, int)
        or observation.source_row <= 0
    ):
        raise _fail("Live vintage source row is invalid")
    return observation


def _validate_capture(capture: object) -> VintageCapture:
    if not isinstance(capture, VintageCapture):
        raise _fail("Live vintage publisher requires a VintageCapture")
    allowed_series = _CAPTURE_SERIES_BY_PROVIDER_FAMILY.get(
        (capture.provider, capture.family)
    )
    if allowed_series is None:
        raise _fail("Live vintage capture provider/family pairing is invalid")
    source_resource = _require_source_resource(capture.source_resource)
    media_type = _require_text(capture.media_type, "media_type", maximum=255)
    response = _require_body(capture.response_bytes, "response_bytes")
    if _sha256(response) != capture.response_sha256 or _SHA256.fullmatch(capture.response_sha256) is None:
        raise _fail("Live vintage capture response digest is invalid")
    captured, captured_precision = _temporal(
        capture.captured_at, "captured_at", require_datetime=True
    )
    if captured != capture.captured_at or captured_precision != capture.captured_precision:
        raise _fail("Live vintage capture time is not canonical")
    published, published_precision = _source_published_temporal(capture.source_published_at)
    if (
        published != capture.source_published_at
        or published_precision != capture.source_published_precision
    ):
        raise _fail("Live vintage capture source publication time is not canonical")
    if capture.availability_basis not in {"source_release", "local_capture"}:
        raise _fail("Live vintage capture availability basis is invalid")
    if capture.normalization_version != NORMALIZATION_VERSION:
        raise _fail("Live vintage capture normalization version is invalid")
    if not capture.observations:
        raise _fail("Live vintage capture has no observations")
    checked = tuple(_validate_observation(item, capture) for item in capture.observations)
    keys = {(item.series_id, item.period, item.source_vintage_identity) for item in checked}
    if len(keys) != len(checked):
        raise _fail("Live vintage capture has duplicate canonical observations")
    selected_series = {item.series_id for item in checked}
    if not selected_series.issubset(allowed_series):
        raise _fail("Live vintage capture contains a series outside its reviewed family")
    if capture.family == "gdp" and not _GDP_SERIES_IDS.issubset(selected_series):
        raise _fail("Live GDP/GDI capture must contain both reviewed GDP series")
    if capture.family == "cpi" and selected_series != _CPI_SERIES_IDS:
        raise _fail("Live CPI capture must contain both reviewed CPI series")
    if capture.provider == "bls" and capture.family == "cpi_history":
        if selected_series not in (
            {CPI_ALL_ITEMS_SERIES_ID},
            set(_CPI_SERIES_IDS),
        ):
            raise _fail("Live BLS CPI history capture series set is invalid")
        if (
            source_resource != BLS_CURRENT_RESOURCE
            or media_type.casefold() != "application/json"
            or capture.availability_basis != "local_capture"
            or capture.source_published_at is not None
        ):
            raise _fail("Live BLS CPI history capture declaration is invalid")
    if capture.provider == "bls" and capture.family == "employment":
        if selected_series != _EMPLOYMENT_SERIES_IDS:
            raise _fail("Live BLS employment capture must contain both reviewed series")
        if source_resource != BLS_CURRENT_RESOURCE or media_type.casefold() != "application/json":
            raise _fail("Live BLS employment capture source declaration is invalid")
        if capture.availability_basis != "local_capture":
            raise _fail("Live BLS employment capture availability is invalid")
    if capture.provider == "philadelphia_fed":
        if len(selected_series) != 1:
            raise _fail("Live Philadelphia Fed capture must contain one series")
        series_id = next(iter(selected_series))
        declarations = {
            TOTAL_NONFARM_PAYROLLS_SERIES_ID: (
                "employment",
                PHILADELPHIA_FED_PAYROLL_RTDSM_RESOURCE,
                _EMPLOY64M12,
            ),
            UNEMPLOYMENT_RATE_SERIES_ID: (
                "employment",
                PHILADELPHIA_FED_UNEMPLOYMENT_RTDSM_RESOURCE,
                _RUC65Q4,
            ),
            RTDSM_NOMINAL_OUTPUT_SERIES_ID: (
                "gdp_history",
                PHILADELPHIA_FED_NOMINAL_OUTPUT_RTDSM_RESOURCE,
                _NOUTPUT65Q4,
            ),
            RTDSM_REAL_OUTPUT_SERIES_ID: (
                "gdp_history",
                PHILADELPHIA_FED_REAL_OUTPUT_RTDSM_RESOURCE,
                _ROUTPUT65Q4,
            ),
            CPI_ALL_ITEMS_SERIES_ID: (
                "cpi_history",
                PHILADELPHIA_FED_CPI_ALL_ITEMS_RTDSM_RESOURCE,
                _PCPI98M11,
            ),
            CPI_CORE_SERIES_ID: (
                "cpi_history",
                PHILADELPHIA_FED_CPI_CORE_RTDSM_RESOURCE,
                _PCPIX98M11,
            ),
        }
        declaration = declarations.get(series_id)
        if declaration is None:
            raise _fail("Live Philadelphia Fed capture series is invalid")
        expected_family, expected_resource, expected_pattern = declaration
        if (
            capture.family != expected_family
            or source_resource != expected_resource
            or media_type.casefold()
            != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            or capture.availability_basis != "local_capture"
            or capture.source_published_at is not None
        ):
            raise _fail("Live Philadelphia Fed capture declaration is invalid")
        for observation in checked:
            _rtdsm_header(
                observation.source_vintage_identity,
                pattern=expected_pattern,
                label=expected_family,
            )
            if observation.vintage_at is not None:
                raise _fail("Live Philadelphia Fed vintage must remain absent")
    semantic = _capture_semantic_identity(
        provider=capture.provider,
        family=capture.family,
        source_resource=capture.source_resource,
        media_type=capture.media_type,
        source_published_at=capture.source_published_at,
        source_published_precision=capture.source_published_precision,
        availability_basis=capture.availability_basis,
        observations=checked,
    )
    if semantic != capture.semantic_identity or _SHA256.fullmatch(semantic) is None:
        raise _fail("Live vintage capture semantic identity is invalid")
    return capture


class MacroLiveVintagePublisher:
    """Publish one pre-parsed capture to an explicit temporary or fixed store."""

    _canonical_project_root = Path(__file__).resolve().parents[2]

    def __init__(
        self,
        *,
        market_store: Path,
        project_root: Path,
        registry: object,
    ) -> None:
        self._configure(
            market_store=market_store,
            project_root=project_root,
            registry=registry,
            canonical=False,
        )

    @classmethod
    def for_canonical(cls, *, registry: object) -> "MacroLiveVintagePublisher":
        """Bind the only operational path without accepting a caller path."""

        instance = object.__new__(cls)
        root = cls._canonical_project_root.resolve(strict=True)
        instance._configure(
            market_store=root / "data" / "macro.sqlite",
            project_root=root,
            registry=registry,
            canonical=True,
        )
        return instance

    def _configure(
        self,
        *,
        market_store: Path,
        project_root: Path,
        registry: object,
        canonical: bool,
    ) -> None:
        if registry is None:
            raise _fail("Live vintage publisher requires the reviewed registry")
        if not isinstance(market_store, Path) or not isinstance(project_root, Path):
            raise _fail("Live vintage publisher requires Path inputs")
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            raise _fail("Live vintage project root must be a directory")
        store = market_store.resolve(strict=False)
        if not store.is_absolute() or store == root or store.suffix != ".sqlite":
            raise _fail("Live vintage store path is invalid")
        try:
            store.relative_to(root)
        except ValueError as exc:
            raise _fail("Live vintage store must be contained by the explicit project root") from exc
        if canonical:
            expected_root = self._canonical_project_root.resolve(strict=True)
            expected_store = expected_root / "data" / "macro.sqlite"
            if root != expected_root or store != expected_store:
                raise _fail("Live vintage canonical publisher path is not fixed")
        else:
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temp_root)
                store.relative_to(temp_root)
            except ValueError as exc:
                raise _fail(
                    "Live vintage direct publisher is limited to an explicit temporary root"
                ) from exc
            if root == temp_root:
                raise _fail("Live vintage temporary project root is too broad")
        self._store = store
        self._project_root = root
        self._registry = registry

    def publish(self, capture: VintageCapture) -> VintagePublishReport:
        prepared = _validate_capture(capture)
        if not self._store.is_file():
            raise _fail("Live vintage macro store is unavailable; migration must be applied first")
        with StoreWriteLock(self._store):
            connection = sqlite3.connect(self._store, timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA recursive_triggers=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    report = self._publish_in_transaction(connection, prepared)
                    connection.commit()
                    return report
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            finally:
                connection.close()

    def _publish_in_transaction(
        self, connection: sqlite3.Connection, capture: VintageCapture
    ) -> VintagePublishReport:
        try:
            existing = connection.execute(
                "SELECT capture_id FROM macro_live_vintage_captures WHERE semantic_identity=?",
                (capture.semantic_identity,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise _fail("Live vintage macro migration is not applied") from exc
        if existing is not None:
            return VintagePublishReport(
                outcome="unchanged",
                semantic_identity=capture.semantic_identity,
                capture_id=str(existing["capture_id"]),
                written_captures=0,
                written_series=0,
                written_releases=0,
                written_versions=0,
                written_current=0,
                written_memberships=0,
            )

        capture_id = stable_id("mlvc", capture.semantic_identity)
        connection.execute(
            """
            INSERT INTO macro_live_vintage_captures (
                capture_id, provider, source_resource, media_type, response_sha256,
                response_bytes, semantic_identity, captured_at, captured_precision,
                source_published_at, source_published_precision, availability_basis,
                normalization_version, observation_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capture_id,
                capture.provider,
                capture.source_resource,
                capture.media_type,
                capture.response_sha256,
                capture.response_bytes,
                capture.semantic_identity,
                capture.captured_at,
                capture.captured_precision,
                capture.source_published_at,
                capture.source_published_precision,
                capture.availability_basis,
                capture.normalization_version,
                len(capture.observations),
            ),
        )
        counts = {
            "series": 0,
            "releases": 0,
            "versions": 0,
            "current": 0,
            "memberships": 0,
        }
        for observation in sorted(
            capture.observations,
            key=lambda item: (
                item.series_id,
                item.period,
                item.source_vintage_identity,
                item.source_row,
            ),
        ):
            self._ensure_series(connection, observation, capture_id, counts)
            release_id, inserted_release = self._ensure_release(
                connection, observation, capture_id, counts
            )
            if inserted_release and observation.series_id in _BEA_GDP_GDI_SERIES_IDS:
                self._insert_gdp_provenance(connection, observation, capture_id, release_id)
            version_id, inserted_version = self._ensure_version(
                connection, observation, capture_id, release_id, counts
            )
            connection.execute(
                """
                INSERT INTO macro_live_vintage_capture_membership (
                    capture_id, version_id, series_id, source_row
                ) VALUES (?, ?, ?, ?)
                """,
                (capture_id, version_id, observation.series_id, observation.source_row),
            )
            counts["memberships"] += 1
            if inserted_version:
                self._move_current_if_later(
                    connection, observation.series_id, observation.period, version_id, counts
                )
        return VintagePublishReport(
            outcome="published",
            semantic_identity=capture.semantic_identity,
            capture_id=capture_id,
            written_captures=1,
            written_series=counts["series"],
            written_releases=counts["releases"],
            written_versions=counts["versions"],
            written_current=counts["current"],
            written_memberships=counts["memberships"],
        )

    @staticmethod
    def _ensure_series(
        connection: sqlite3.Connection,
        observation: VintageObservation,
        capture_id: str,
        counts: dict[str, int],
    ) -> None:
        spec = _SERIES_SPECS[observation.series_id]
        existing = connection.execute(
            """
            SELECT provider, provider_series_code, title, frequency, unit,
                   value_representation, availability_basis
            FROM macro_live_vintage_series WHERE series_id=?
            """,
            (observation.series_id,),
        ).fetchone()
        expected = (
            spec["provider"],
            spec["provider_series_code"],
            spec["title"],
            spec["frequency"],
            spec["unit"],
            spec["value_representation"],
            spec["availability_basis"],
        )
        if existing is not None:
            actual = tuple(str(existing[name]) for name in existing.keys())
            if actual != expected:
                raise _fail("Live vintage persisted series declaration is inconsistent")
            return
        connection.execute(
            """
            INSERT INTO macro_live_vintage_series (
                series_id, provider, provider_series_code, title, frequency, unit,
                value_representation, availability_basis, created_capture_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (observation.series_id, *expected, capture_id),
        )
        counts["series"] += 1

    @staticmethod
    def _release_fields(
        observation: VintageObservation, capture_id: str
    ) -> tuple[object, ...]:
        return (
            observation.series_id,
            observation.source_vintage_identity,
            observation.vintage_at,
            observation.vintage_precision,
            observation.source_release_order,
            observation.available_at,
            observation.available_precision,
            observation.availability_basis,
            None if observation.is_first_release is None else int(observation.is_first_release),
            observation.first_release_evidence,
            observation.release_stage,
            observation.source_published_at,
            observation.source_published_precision,
            capture_id,
        )

    def _ensure_release(
        self,
        connection: sqlite3.Connection,
        observation: VintageObservation,
        capture_id: str,
        counts: dict[str, int],
    ) -> tuple[str, bool]:
        release_id = stable_id(
            "mlvr", observation.series_id, observation.source_vintage_identity
        )
        existing = connection.execute(
            """
            SELECT release_id, series_id, source_vintage_identity, vintage_at,
                   vintage_precision, source_release_order, available_at,
                   available_precision, availability_basis, is_first_release,
                   first_release_evidence, release_stage, source_published_at,
                   source_published_precision
            FROM macro_live_vintage_releases
            WHERE series_id=? AND source_vintage_identity=?
            """,
            (observation.series_id, observation.source_vintage_identity),
        ).fetchone()
        expected_without_capture = self._release_fields(observation, capture_id)[:-1]
        if existing is not None:
            actual = tuple(existing[name] for name in tuple(existing.keys())[1:])
            local_capture_replay = (
                observation.availability_basis == "local_capture"
                and actual[7] == "local_capture"
                and actual[:5] == expected_without_capture[:5]
                and actual[6:] == expected_without_capture[6:]
                and str(actual[5]) <= str(expected_without_capture[5])
            )
            if actual != expected_without_capture and not local_capture_replay:
                raise _fail("Live vintage persisted release declaration is inconsistent")
            return str(existing["release_id"]), False
        connection.execute(
            """
            INSERT INTO macro_live_vintage_releases (
                release_id, series_id, source_vintage_identity, vintage_at,
                vintage_precision, source_release_order, available_at,
                available_precision, availability_basis, is_first_release,
                first_release_evidence, release_stage, source_published_at,
                source_published_precision, capture_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (release_id, *self._release_fields(observation, capture_id)),
        )
        counts["releases"] += 1
        return release_id, True

    @staticmethod
    def _insert_gdp_provenance(
        connection: sqlite3.Connection,
        observation: VintageObservation,
        capture_id: str,
        release_id: str,
    ) -> None:
        if observation.release_stage is None:
            raise _fail("Live GDP release lacks a source stage")
        connection.execute(
            """
            INSERT INTO macro_live_vintage_gdp_provenance (
                provenance_id, release_id, capture_id, reference_period,
                release_stage, source_vintage_identity, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("mlvg", release_id),
                release_id,
                capture_id,
                observation.period,
                observation.release_stage,
                observation.source_vintage_identity,
                observation.source_row,
            ),
        )

    @staticmethod
    def _ensure_version(
        connection: sqlite3.Connection,
        observation: VintageObservation,
        capture_id: str,
        release_id: str,
        counts: dict[str, int],
    ) -> tuple[str, bool]:
        latest = connection.execute(
            """
            SELECT version_id, correction_sequence, value_text, value_sha256
            FROM macro_live_vintage_observation_versions
            WHERE series_id=? AND period=? AND release_id=?
            ORDER BY correction_sequence DESC
            LIMIT 1
            """,
            (observation.series_id, observation.period, release_id),
        ).fetchone()
        value_sha256 = _sha256(observation.value_text.encode("utf-8"))
        if latest is not None and (
            str(latest["value_text"]) == observation.value_text
            and str(latest["value_sha256"]) == value_sha256
        ):
            return str(latest["version_id"]), False
        correction_sequence = 1 if latest is None else int(latest["correction_sequence"]) + 1
        supersedes = None if latest is None else str(latest["version_id"])
        version_id = stable_id(
            "mlvv",
            observation.series_id,
            observation.period,
            release_id,
            str(correction_sequence),
            value_sha256,
            capture_id,
        )
        connection.execute(
            """
            INSERT INTO macro_live_vintage_observation_versions (
                version_id, series_id, period, release_id, source_vintage_identity,
                correction_sequence, value_text, value_sha256, unit, available_at,
                available_precision, captured_at, captured_precision,
                supersedes_version_id, capture_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?, ?)
            """,
            (
                version_id,
                observation.series_id,
                observation.period,
                release_id,
                observation.source_vintage_identity,
                correction_sequence,
                observation.value_text,
                value_sha256,
                observation.unit,
                observation.available_at,
                observation.available_precision,
                # Captured time is constrained by the version lineage trigger
                # to match the capture.  The observation's availability may
                # intentionally remain source-date-only.
                connection.execute(
                    "SELECT captured_at FROM macro_live_vintage_captures WHERE capture_id=?",
                    (capture_id,),
                ).fetchone()[0],
                supersedes,
                capture_id,
                observation.source_row,
            ),
        )
        counts["versions"] += 1
        return version_id, True

    @staticmethod
    def _version_rank(connection: sqlite3.Connection, version_id: str) -> tuple[str, str, int]:
        row = connection.execute(
            """
            SELECT release.available_at, release.source_release_order,
                   version.correction_sequence
            FROM macro_live_vintage_observation_versions AS version
            JOIN macro_live_vintage_releases AS release ON release.release_id=version.release_id
            WHERE version.version_id=?
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise _fail("Live vintage version is unavailable for current projection")
        return (str(row["available_at"]), str(row["source_release_order"]), int(row["correction_sequence"]))

    def _move_current_if_later(
        self,
        connection: sqlite3.Connection,
        series_id: str,
        period: str,
        candidate_version_id: str,
        counts: dict[str, int],
    ) -> None:
        existing = connection.execute(
            """
            SELECT current_version_id FROM macro_live_vintage_observations
            WHERE series_id=? AND period=?
            """,
            (series_id, period),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO macro_live_vintage_observations (
                    series_id, period, current_version_id
                ) VALUES (?, ?, ?)
                """,
                (series_id, period, candidate_version_id),
            )
            counts["current"] += 1
            return
        current_version_id = str(existing["current_version_id"])
        if self._version_rank(connection, candidate_version_id) <= self._version_rank(
            connection, current_version_id
        ):
            return
        connection.execute(
            """
            UPDATE macro_live_vintage_observations
            SET current_version_id=?
            WHERE series_id=? AND period=?
            """,
            (candidate_version_id, series_id, period),
        )
        counts["current"] += 1
