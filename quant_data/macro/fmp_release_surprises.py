"""FMP GDP/CPI calendar normalization and derived release surprises.

The module deliberately reuses the established economic-calendar relations.
Consensus is retained as FMP supplied it, GDP actuals come from the matching
official BEA release vintage, and surprises are calculated on read rather than
stored.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Iterable

from ..errors import (
    ConflictError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection, stable_id


CALENDAR_DATASET_ID: Final = "fixture.macro.economic_calendar"
CALENDAR_COLLECTOR_ID: Final = "fmp.macro.gdp_cpi_release_calendar_history"
NORMALIZATION_VERSION: Final = "fmp_us_gdp_cpi_calendar_v1"
SOURCE_REFERENCE: Final = "fmp/economic-calendar/us-gdp-cpi.json"
SOURCE_RESOURCE: Final = "fmp_stable_economic_calendar"
EVENT_DATE_AVAILABILITY_ASSUMPTION: Final = "event_at_utc"

# Employment releases reuse the established calendar tables while retaining a
# separate collector and normalization identity from the completed GDP/CPI
# history operation.
EMPLOYMENT_CALENDAR_COLLECTOR_ID: Final = (
    "fmp.macro.employment_release_calendar_refresh"
)
_LEGACY_EMPLOYMENT_NORMALIZATION_VERSION: Final = "fmp_us_employment_calendar_v1"
_PAYROLL_REFERENCE_PERIOD_NORMALIZATION_VERSION: Final = (
    "fmp_us_employment_calendar_v2"
)
EMPLOYMENT_NORMALIZATION_VERSION: Final = "fmp_us_employment_calendar_v3"
EMPLOYMENT_SOURCE_REFERENCE: Final = "fmp/economic-calendar/us-employment.json"
EMPLOYMENT_SOURCE_RESOURCE: Final = "fmp_stable_economic_calendar"

GDP_ADVANCE_KIND: Final = "us_gdp_real_qoq_saar_advance"
CPI_HEADLINE_MOM_KIND: Final = "us_cpi_headline_mom"
CPI_HEADLINE_YOY_KIND: Final = "us_cpi_headline_yoy"
CPI_CORE_MOM_KIND: Final = "us_cpi_core_mom"
CPI_CORE_YOY_KIND: Final = "us_cpi_core_yoy"
NONFARM_PAYROLLS_KIND: Final = "us_nonfarm_payrolls_change_thousands"
UNEMPLOYMENT_RATE_KIND: Final = "us_unemployment_rate"
SUPPORTED_KINDS: Final = (
    GDP_ADVANCE_KIND,
    CPI_HEADLINE_MOM_KIND,
    CPI_HEADLINE_YOY_KIND,
    CPI_CORE_MOM_KIND,
    CPI_CORE_YOY_KIND,
)
EMPLOYMENT_KINDS: Final = (
    NONFARM_PAYROLLS_KIND,
    UNEMPLOYMENT_RATE_KIND,
)
CPI_KINDS: Final = (
    CPI_HEADLINE_MOM_KIND,
    CPI_HEADLINE_YOY_KIND,
    CPI_CORE_MOM_KIND,
    CPI_CORE_YOY_KIND,
)
ALL_SURPRISE_KINDS: Final = SUPPORTED_KINDS + EMPLOYMENT_KINDS
MAX_SOURCE_ROWS: Final = 2_000

# The integer is a deterministic preference when FMP returns overlapping old
# and new names for the same release.  The older ``Inflation Rate`` names are
# the stable historical headline labels; explicit ``Core`` names are stable
# for core CPI.  Bare ``CPI MoM/YoY`` is resolved in release context below
# because older FMP rows used it for core CPI while newer rows use it for
# headline CPI.
_ALIASES: Final = {
    "gdp growth rate qoq": (GDP_ADVANCE_KIND, 0),
    "gross domestic product qoq": (GDP_ADVANCE_KIND, 1),
    "inflation rate mom": (CPI_HEADLINE_MOM_KIND, 0),
    "consumer price index cpi mom": (CPI_HEADLINE_MOM_KIND, 1),
    "consumer price index mom": (CPI_HEADLINE_MOM_KIND, 1),
    "inflation rate yoy": (CPI_HEADLINE_YOY_KIND, 0),
    "consumer price index cpi yoy": (CPI_HEADLINE_YOY_KIND, 1),
    "consumer price index yoy": (CPI_HEADLINE_YOY_KIND, 1),
    "core inflation rate mom": (CPI_CORE_MOM_KIND, 0),
    "core cpi mom": (CPI_CORE_MOM_KIND, 1),
    "core consumer price index cpi mom": (CPI_CORE_MOM_KIND, 2),
    "core consumer price index mom": (CPI_CORE_MOM_KIND, 2),
    "core inflation rate yoy": (CPI_CORE_YOY_KIND, 0),
    "core cpi yoy": (CPI_CORE_YOY_KIND, 1),
    "core consumer price index cpi yoy": (CPI_CORE_YOY_KIND, 2),
    "core consumer price index yoy": (CPI_CORE_YOY_KIND, 2),
}
_AMBIGUOUS_CPI: Final = {
    "cpi mom": "mom",
    "cpi yoy": "yoy",
}
_EMPLOYMENT_ALIASES: Final = {
    "non farm payrolls": NONFARM_PAYROLLS_KIND,
    "nonfarm payrolls": NONFARM_PAYROLLS_KIND,
    "unemployment rate": UNEMPLOYMENT_RATE_KIND,
}
_EMPLOYMENT_EXCLUSIONS: Final = frozenset(
    {
        "adp employment change",
        "private payrolls",
        "private nonfarm payrolls",
        "nonfarm payrolls private",
        "government payrolls",
        "manufacturing payrolls",
        "u 6 unemployment rate",
        "initial claims",
        "continuing claims",
        "initial jobless claims",
        "continuing jobless claims",
    }
)
_MONTH = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    "jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    "nov(?:ember)?|dec(?:ember)?"
)
_MONTH_NUMBER: Final = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_TRAILING_PERIOD = re.compile(
    rf"\s*\((?:q[1-4]|{_MONTH}|mon)\)\s*$", re.IGNORECASE
)
_TRAILING_RELEASE_MONTH = re.compile(
    rf"\s*\((?P<month>{_MONTH})\)\s*$", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")
_GDP_REFERENCE_PERIOD = re.compile(r"^\d{4}Q[1-4]$")
_GDP_RELEASE_STAGE_PRIORITY: Final = {
    "advance": 0,
    "initial": 0,
    "second": 1,
    "third": 2,
}
_GDP_RELEASE_MAPPING_BASIS: Final = {
    "advance": "exact_bea_advance_date",
    "initial": "exact_bea_initial_date",
    "second": "exact_bea_second_release_date",
    "third": "exact_bea_third_release_date",
}
_CPI_FMP_EVENT_MAPPING_BASIS: Final = "same_fmp_event"
_CPI_FMP_COALESCED_MAPPING_BASIS: Final = (
    "same_day_fmp_cpi_complementary_fields"
)
_EMPLOYMENT_REFERENCE_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_PAYROLL_PROVIDER_EVENT_ID = re.compile(
    r"^fmp_employment_v2:(?P<period>\d{4}-(?:0[1-9]|1[0-2])):[0-9a-f]{64}$"
)
_UNEMPLOYMENT_PROVIDER_EVENT_ID = re.compile(
    r"^fmp_unemployment_v3:(?P<period>\d{4}-(?:0[1-9]|1[0-2])):[0-9a-f]{64}$"
)


@dataclass(frozen=True, slots=True)
class FmpCalendarEvent:
    kind: str
    event_at: str
    event_precision: str
    source_name: str
    actual_text: str | None
    consensus_text: str | None
    previous_text: str | None
    actual: Decimal | None
    consensus: Decimal | None
    previous: Decimal | None
    unit: str
    source_rows: tuple[int, ...]
    reference_period: str | None = None


@dataclass(frozen=True, slots=True)
class FmpCalendarCapture:
    response_bytes: bytes
    response_sha256: str
    semantic_identity: str
    captured_at: str
    captured_precision: str
    request_start_date: str
    request_end_date: str
    events: tuple[FmpCalendarEvent, ...]


@dataclass(frozen=True, slots=True)
class FmpCalendarPublishReport:
    outcome: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_events: int
    written_versions: int


@dataclass(frozen=True, slots=True)
class ReleaseSurprise:
    event_id: str
    event_version_id: str
    kind: str
    event_at: str
    reference_period: str | None
    consensus_mapping_basis: str
    consensus: Decimal | None
    fmp_actual: Decimal | None
    official_actual: Decimal | None
    surprise: Decimal | None
    unit: str
    actual_source: str
    availability_assumption: str
    status: str
    official_version_id: str | None
    official_prior_version_id: str | None = None
    release_stage: str | None = None
    is_fallback: bool = False
    coalesced_event_version_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _CalendarCandidate:
    event: FmpCalendarEvent
    priority: int
    ambiguous_dimension: str | None = None


@dataclass(frozen=True, slots=True)
class _GdpOfficialSelection:
    official_actual: Decimal | None
    official_version_id: str | None
    reference_period: str | None
    consensus_mapping_basis: str
    release_stage: str | None
    status: str


@dataclass(frozen=True, slots=True)
class _EmploymentOfficialSelection:
    official_actual: Decimal
    official_version_id: str
    official_prior_version_id: str | None
    reference_period: str
    consensus_mapping_basis: str


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _utc_datetime(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _fail(f"FMP calendar {label} is invalid")
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
    except ValueError as exc:
        raise _fail(f"FMP calendar {label} is invalid") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _decimal(value: object, *, label: str) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise _fail(f"FMP calendar {label} is invalid")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise _fail(f"FMP calendar {label} is invalid") from exc
    if not parsed.is_finite():
        raise _fail(f"FMP calendar {label} is invalid")
    text = format(parsed, "f")
    if text in {"-0", "-0.0"}:
        text = "0"
        parsed = Decimal(0)
    return parsed, text


def _normalized_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise _fail("FMP calendar event name is invalid")
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = _TRAILING_PERIOD.sub("", text)
    return _SPACE.sub(" ", _NON_ALNUM.sub(" ", text)).strip()


def _employment_reference_month(value: str) -> str | None:
    match = _TRAILING_RELEASE_MONTH.search(value)
    if match is None:
        return None
    return match.group("month").casefold()


def _employment_reference_period_for_event(
    source_name: str,
    event_at: str,
) -> str | None:
    try:
        release = datetime.strptime(event_at[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise _fail("FMP payroll event date is invalid") from exc
    month_name = _employment_reference_month(source_name)
    if month_name is None:
        if not 1 <= release.day <= 10:
            return None
        reference_month = 12 if release.month == 1 else release.month - 1
        reference_year = release.year - int(release.month == 1)
        return f"{reference_year}-{reference_month:02d}"

    reference_month = _MONTH_NUMBER.get(month_name[:3])
    if reference_month is None:
        raise _fail("FMP payroll reference month is invalid")
    reference_year = release.year - int(reference_month >= release.month)
    release_index = release.year * 12 + release.month
    reference_index = reference_year * 12 + reference_month
    lag_months = release_index - reference_index
    if not 1 <= lag_months <= 3:
        return None
    return f"{reference_year}-{reference_month:02d}"


def _classification_for_name(
    value: object,
) -> tuple[str | None, int, str | None] | None:
    normalized = _normalized_name(value)
    mapped = _ALIASES.get(normalized)
    if mapped is not None:
        return mapped[0], mapped[1], None
    ambiguous = _AMBIGUOUS_CPI.get(normalized)
    if ambiguous is not None:
        return None, 10, ambiguous

    known_exclusion = (
        "gdpnow" in normalized
        or "price index" in normalized
        or "deflator" in normalized
        or "gdp sales" in normalized
        or "gdp growth annualized" in normalized
        or normalized == "gdp growth rate yoy"
        or normalized == "gdp consumer spending yoy"
        or normalized == "gdp consumer spending qoq"
        or normalized
        in {
            "cpi",
            "cpi s a",
            "core cpi",
            "consumer price index cpi",
            "consumer price index",
        }
    )
    if known_exclusion:
        return None
    candidate = (
        "gdp" in normalized
        or "gross domestic product" in normalized
        or "cpi" in normalized
        or "inflation rate" in normalized
    ) and any(token in normalized.split() for token in ("qoq", "mom", "yoy"))
    if candidate:
        raise _fail(f"FMP calendar GDP/CPI event alias is not reviewed: {normalized}")
    return None


def _employment_classification_for_name(
    value: object,
) -> tuple[str, int] | None:
    normalized = _normalized_name(value)
    kind = _EMPLOYMENT_ALIASES.get(normalized)
    if kind is not None:
        return kind, 0
    if normalized in _EMPLOYMENT_EXCLUSIONS:
        return None
    words = frozenset(normalized.split())
    if "payroll" in words or "payrolls" in words or "unemployment" in words:
        raise _fail(
            f"FMP calendar employment event alias is not reviewed: {normalized}"
        )
    return None


def _event_values(event: FmpCalendarEvent) -> tuple[object, ...]:
    return (
        event.actual_text,
        event.consensus_text,
        event.previous_text,
        event.unit,
    )


def _select_candidate(
    candidates: Iterable[_CalendarCandidate],
    *,
    kind: str,
) -> FmpCalendarEvent | None:
    ordered = sorted(
        candidates,
        key=lambda item: (item.priority, min(item.event.source_rows)),
    )
    if not ordered:
        return None
    selected_priority = ordered[0].priority
    selected = replace(ordered[0].event, kind=kind)
    rows = list(selected.source_rows)
    for candidate in ordered[1:]:
        comparable = replace(candidate.event, kind=kind)
        if _event_values(comparable) == _event_values(selected):
            rows.extend(comparable.source_rows)
            continue
        if kind == GDP_ADVANCE_KIND or candidate.priority == selected_priority:
            raise _fail("FMP calendar duplicate aliases conflict")
        # FMP keeps overlapping legacy/current CPI aliases around its naming
        # transitions, occasionally with different previous/estimate fields.
        # The reviewed priority selects one canonical consensus deterministically.
    return replace(selected, source_rows=tuple(sorted(set(rows))))


def _merge_equivalent_alias(
    selected: FmpCalendarEvent,
    alias: FmpCalendarEvent,
) -> FmpCalendarEvent:
    return replace(
        selected,
        source_rows=tuple(sorted(set(selected.source_rows + alias.source_rows))),
    )


def _event_material(event: FmpCalendarEvent) -> dict[str, object]:
    return {
        "actual": event.actual_text,
        "consensus": event.consensus_text,
        "event_at": event.event_at,
        "kind": event.kind,
        "previous": event.previous_text,
        "unit": event.unit,
    }


def parse_fmp_us_gdp_cpi_calendar(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> FmpCalendarCapture:
    """Normalize the reviewed US GDP/CPI aliases from one FMP response."""

    if not isinstance(body, bytes) or not body:
        raise _fail("FMP calendar response body is invalid")
    captured = _utc_datetime(captured_at, label="capture time")
    raw = loads_strict(body)
    if not isinstance(raw, list):
        raise _fail("FMP calendar response must be an array")
    if len(raw) > MAX_SOURCE_ROWS:
        raise ResourceLimitError("FMP calendar response exceeds its row bound")
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise _fail("FMP calendar request window is invalid") from exc
    if start > end:
        raise _fail("FMP calendar request window is invalid")

    candidates_by_time: dict[str, list[_CalendarCandidate]] = {}
    allowed = {
        "date",
        "country",
        "event",
        "currency",
        "previous",
        "estimate",
        "actual",
        "change",
        "impact",
        "changePercentage",
        "unit",
    }
    required = {"date", "country", "event", "currency", "previous", "estimate", "actual", "unit"}
    for source_row, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not required.issubset(item) or set(item) - allowed:
            raise _fail("FMP calendar row shape is invalid")
        if item["country"] != "US":
            continue
        classification = _classification_for_name(item["event"])
        if classification is None:
            continue
        if item["actual"] is None and item["estimate"] is None:
            continue
        kind, priority, ambiguous_dimension = classification
        if item["currency"] != "USD" or item["unit"] != "%":
            raise _fail("FMP calendar GDP/CPI currency or unit is invalid")
        actual, actual_text = _decimal(item["actual"], label="actual")
        consensus, consensus_text = _decimal(item["estimate"], label="estimate")
        previous, previous_text = _decimal(item["previous"], label="previous")
        event_at = _utc_datetime(item["date"], label="event time")
        event_date = datetime.strptime(event_at[:10], "%Y-%m-%d").date()
        if not start <= event_date <= end:
            raise _fail("FMP calendar event is outside its request window")
        candidate = FmpCalendarEvent(
            kind=kind or "",
            event_at=event_at,
            event_precision="datetime",
            source_name=str(item["event"]),
            actual_text=actual_text,
            consensus_text=consensus_text,
            previous_text=previous_text,
            actual=actual,
            consensus=consensus,
            previous=previous,
            unit="%",
            source_rows=(source_row,),
        )
        candidates_by_time.setdefault(event_at, []).append(
            _CalendarCandidate(
                event=candidate,
                priority=priority,
                ambiguous_dimension=ambiguous_dimension,
            )
        )

    events: dict[tuple[str, str], FmpCalendarEvent] = {}
    for event_at, candidates in candidates_by_time.items():
        explicit = [item for item in candidates if item.ambiguous_dimension is None]
        selected: dict[str, FmpCalendarEvent] = {}
        for kind in SUPPORTED_KINDS:
            event = _select_candidate(
                (item for item in explicit if item.event.kind == kind),
                kind=kind,
            )
            if event is not None:
                selected[kind] = event

        for dimension in ("mom", "yoy"):
            ambiguous = _select_candidate(
                (
                    item
                    for item in candidates
                    if item.ambiguous_dimension == dimension
                ),
                kind=(
                    CPI_HEADLINE_MOM_KIND
                    if dimension == "mom"
                    else CPI_HEADLINE_YOY_KIND
                ),
            )
            if ambiguous is None:
                continue
            headline_kind = (
                CPI_HEADLINE_MOM_KIND
                if dimension == "mom"
                else CPI_HEADLINE_YOY_KIND
            )
            core_kind = (
                CPI_CORE_MOM_KIND if dimension == "mom" else CPI_CORE_YOY_KIND
            )
            headline = selected.get(headline_kind)
            core = selected.get(core_kind)
            if headline is not None and core is not None:
                # Explicit names win.  FMP sometimes retains a third legacy
                # alias with a different historical estimate for the same
                # release; it is not a separate macro fact.
                continue
            if headline is not None:
                if _event_values(ambiguous) == _event_values(headline):
                    selected[headline_kind] = _merge_equivalent_alias(
                        headline, ambiguous
                    )
                else:
                    selected[core_kind] = replace(ambiguous, kind=core_kind)
                continue
            if core is not None:
                if _event_values(ambiguous) == _event_values(core):
                    selected[core_kind] = _merge_equivalent_alias(core, ambiguous)
                else:
                    selected[headline_kind] = replace(
                        ambiguous, kind=headline_kind
                    )
                continue
            # With no contextual alias, current FMP terminology treats CPI
            # MoM/YoY as headline CPI.
            selected[headline_kind] = replace(ambiguous, kind=headline_kind)

        for kind, event in selected.items():
            events[(kind, event_at)] = event

    normalized = tuple(events[key] for key in sorted(events))
    if not normalized:
        raise _fail("FMP calendar response contains no reviewed GDP/CPI events")
    semantic_material = {
        "events": [_event_material(event) for event in normalized],
        "normalization_version": NORMALIZATION_VERSION,
        "request_scope": {
            "country": "US",
            "event_families": ["gdp", "cpi"],
            "from": start_date,
            "to": end_date,
        },
    }
    semantic_identity = hashlib.sha256(
        dumps_strict(semantic_material).encode("utf-8")
    ).hexdigest()
    return FmpCalendarCapture(
        response_bytes=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        semantic_identity=semantic_identity,
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start_date,
        request_end_date=end_date,
        events=normalized,
    )



def parse_fmp_us_employment_calendar(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> FmpCalendarCapture:
    # Normalize the reviewed US payroll and unemployment FMP aliases.
    if not isinstance(body, bytes) or not body:
        raise _fail("FMP calendar response body is invalid")
    captured = _utc_datetime(captured_at, label="capture time")
    raw = loads_strict(body)
    if not isinstance(raw, list):
        raise _fail("FMP calendar response must be an array")
    if len(raw) > MAX_SOURCE_ROWS:
        raise ResourceLimitError("FMP calendar response exceeds its row bound")
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise _fail("FMP calendar request window is invalid") from exc
    if start > end:
        raise _fail("FMP calendar request window is invalid")

    candidates_by_identity: dict[
        tuple[str, str | None], list[_CalendarCandidate]
    ] = {}
    allowed = {
        "date",
        "country",
        "event",
        "currency",
        "previous",
        "estimate",
        "actual",
        "change",
        "impact",
        "changePercentage",
        "unit",
    }
    required = {
        "date",
        "country",
        "event",
        "currency",
        "previous",
        "estimate",
        "actual",
        "unit",
    }
    for source_row, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not required.issubset(item) or set(item) - allowed:
            raise _fail("FMP calendar row shape is invalid")
        if item["country"] != "US":
            continue
        classification = _employment_classification_for_name(item["event"])
        if classification is None:
            continue
        if item["actual"] is None and item["estimate"] is None:
            continue
        kind, priority = classification
        expected_wire_unit = "K" if kind == NONFARM_PAYROLLS_KIND else "%"
        if item["currency"] != "USD" or item["unit"] != expected_wire_unit:
            raise _fail("FMP calendar employment currency or unit is invalid")
        actual, actual_text = _decimal(item["actual"], label="actual")
        consensus, consensus_text = _decimal(item["estimate"], label="estimate")
        previous, previous_text = _decimal(item["previous"], label="previous")
        event_at = _utc_datetime(item["date"], label="event time")
        event_date = datetime.strptime(event_at[:10], "%Y-%m-%d").date()
        if not start <= event_date <= end:
            raise _fail("FMP calendar event is outside its request window")
        reference_period = _employment_reference_period_for_event(
            str(item["event"]), event_at
        )
        if reference_period is None:
            continue
        canonical_unit = (
            "thousands_persons"
            if kind == NONFARM_PAYROLLS_KIND
            else "percent"
        )
        candidate = FmpCalendarEvent(
            kind=kind,
            event_at=event_at,
            event_precision="datetime",
            source_name=str(item["event"]),
            actual_text=actual_text,
            consensus_text=consensus_text,
            previous_text=previous_text,
            actual=actual,
            consensus=consensus,
            previous=previous,
            unit=canonical_unit,
            source_rows=(source_row,),
            reference_period=reference_period,
        )
        candidates_by_identity.setdefault((event_at, reference_period), []).append(
            _CalendarCandidate(event=candidate, priority=priority)
        )

    events: dict[tuple[str, str, str], FmpCalendarEvent] = {}
    for (event_at, reference_period), candidates in candidates_by_identity.items():
        for kind in EMPLOYMENT_KINDS:
            kind_candidates = tuple(
                item for item in candidates if item.event.kind == kind
            )
            event = _select_candidate(
                kind_candidates,
                kind=kind,
            )
            if event is not None:
                events[(kind, event_at, reference_period or "")] = event

    normalized = tuple(events[key] for key in sorted(events))
    if not normalized:
        raise _fail("FMP calendar response contains no reviewed employment events")
    semantic_material = {
        "events": [
            {**_event_material(event), "reference_period": event.reference_period}
            for event in normalized
        ],
        "normalization_version": EMPLOYMENT_NORMALIZATION_VERSION,
        "request_scope": {
            "country": "US",
            "event_families": ["employment"],
            "from": start_date,
            "to": end_date,
        },
    }
    semantic_identity = hashlib.sha256(
        dumps_strict(semantic_material).encode("utf-8")
    ).hexdigest()
    return FmpCalendarCapture(
        response_bytes=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        semantic_identity=semantic_identity,
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start_date,
        request_end_date=end_date,
        events=normalized,
    )

def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _registry_binding(registry: object) -> None:
    if not isinstance(registry, Registry):
        raise _fail("FMP calendar publisher requires the reviewed registry")
    datasets = {item.id: item for item in registry.datasets}
    collectors = {str(item["id"]): item for item in registry.collectors}
    dataset = datasets.get(CALENDAR_DATASET_ID)
    collector = collectors.get(CALENDAR_COLLECTOR_ID)
    if (
        dataset is None
        or collector is None
        or CALENDAR_COLLECTOR_ID not in dataset.collector_ids
        or collector["output_datasets"] != [CALENDAR_DATASET_ID]
    ):
        raise _fail("FMP calendar registry binding is invalid")



def _employment_registry_binding(registry: object) -> None:
    if not isinstance(registry, Registry):
        raise _fail("FMP employment calendar publisher requires the reviewed registry")
    datasets = {item.id: item for item in registry.datasets}
    collectors = {str(item["id"]): item for item in registry.collectors}
    dataset = datasets.get(CALENDAR_DATASET_ID)
    collector = collectors.get(EMPLOYMENT_CALENDAR_COLLECTOR_ID)
    if (
        dataset is None
        or collector is None
        or EMPLOYMENT_CALENDAR_COLLECTOR_ID not in dataset.collector_ids
        or collector["output_datasets"] != [CALENDAR_DATASET_ID]
    ):
        raise _fail("FMP employment calendar registry binding is invalid")

class FmpMacroCalendarPublisher:
    """Publish a parsed FMP capture to the existing calendar relations."""

    _canonical_root = Path(__file__).resolve().parents[2]

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
    ) -> None:
        self._configure(
            macro_store=macro_store,
            project_root=project_root,
            registry=registry,
            canonical=False,
        )

    @classmethod
    def for_canonical(cls, *, registry: object) -> "FmpMacroCalendarPublisher":
        instance = object.__new__(cls)
        root = cls._canonical_root.resolve(strict=True)
        instance._configure(
            macro_store=root / "data" / "macro.sqlite",
            project_root=root,
            registry=registry,
            canonical=True,
        )
        return instance

    def _configure(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        canonical: bool,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("FMP calendar publisher paths are invalid")
        root = project_root.resolve(strict=True)
        store = macro_store.resolve(strict=True)
        if not root.is_dir() or not store.is_file() or store.is_symlink():
            raise _fail("FMP calendar publisher target is unavailable")
        try:
            store.relative_to(root)
        except ValueError as exc:
            raise _fail("FMP calendar publisher target is outside the project") from exc
        if canonical:
            if root != self._canonical_root.resolve(strict=True) or store != root / "data" / "macro.sqlite":
                raise _fail("FMP calendar canonical target binding is invalid")
        else:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("FMP calendar fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("FMP calendar fixture root is too broad")
        _registry_binding(registry)
        self._root = root
        self._store = store
        self._registry = registry
        self._stores = _store_map(root, store)

    def completed_windows(self) -> tuple[tuple[str, str], ...]:
        """Return the exact successfully published request windows.

        This is the operation's small continuation checkpoint.  It is derived
        from the existing successful ingestion receipts; no second journal or
        schema is introduced.
        """

        expected_keys = {
            "availability_assumption",
            "country",
            "event_kinds",
            "from",
            "normalization_version",
            "to",
        }
        windows: list[tuple[str, str]] = []
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT scope_json
                FROM ingestion_runs
                WHERE dataset_id=? AND command=? AND status='succeeded'
                ORDER BY scope_json
                """,
                (CALENDAR_DATASET_ID, CALENDAR_COLLECTOR_ID),
            ).fetchall()
        for row in rows:
            try:
                scope = loads_strict(str(row["scope_json"]))
            except (TypeError, ValueError) as exc:
                raise ConflictError("FMP calendar completed-window scope is invalid") from exc
            if (
                not isinstance(scope, dict)
                or set(scope) != expected_keys
                or scope.get("availability_assumption")
                != EVENT_DATE_AVAILABILITY_ASSUMPTION
                or scope.get("country") != "US"
                or scope.get("event_kinds") != list(SUPPORTED_KINDS)
                or scope.get("normalization_version") != NORMALIZATION_VERSION
                or not isinstance(scope.get("from"), str)
                or not isinstance(scope.get("to"), str)
            ):
                raise ConflictError("FMP calendar completed-window scope is invalid")
            start_date = str(scope["from"])
            end_date = str(scope["to"])
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ConflictError("FMP calendar completed-window scope is invalid") from exc
            if start > end:
                raise ConflictError("FMP calendar completed-window scope is invalid")
            windows.append((start_date, end_date))
        return tuple(sorted(set(windows)))

    def publish(self, capture: FmpCalendarCapture) -> FmpCalendarPublishReport:
        if not isinstance(capture, FmpCalendarCapture) or not capture.events:
            raise _fail("FMP calendar capture is invalid")
        expected_capture = parse_fmp_us_gdp_cpi_calendar(
            capture.response_bytes,
            captured_at=capture.captured_at,
            start_date=capture.request_start_date,
            end_date=capture.request_end_date,
        )
        if capture != expected_capture:
            raise _fail("FMP calendar capture binding is invalid")

        run_id = stable_id("fmp_calendar_run", capture.semantic_identity)
        artifact_id = stable_id("fmp_calendar_artifact", capture.response_sha256)
        snapshot_id = stable_id("fmp_calendar_snapshot", capture.semantic_identity)
        scope = {
            "availability_assumption": EVENT_DATE_AVAILABILITY_ASSUMPTION,
            "country": "US",
            "event_kinds": list(SUPPORTED_KINDS),
            "from": capture.request_start_date,
            "normalization_version": NORMALIZATION_VERSION,
            "to": capture.request_end_date,
        }
        counts = {"events": 0, "versions": 0}

        def writer(connection: sqlite3.Connection, actual_run_id: str) -> WriteResult:
            if actual_run_id != run_id:
                raise ConflictError("FMP calendar run identity changed")
            connection.execute(
                """
                INSERT INTO macro_source_artifacts (
                    artifact_id, dataset_id, sha256, media_type, byte_count,
                    request_scope_json, captured_at, captured_precision,
                    source_resource, normalization_version, run_id
                ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, 'datetime', ?, ?, ?)
                """,
                (
                    artifact_id,
                    CALENDAR_DATASET_ID,
                    capture.response_sha256,
                    len(capture.response_bytes),
                    dumps_strict(scope),
                    capture.captured_at,
                    SOURCE_RESOURCE,
                    NORMALIZATION_VERSION,
                    run_id,
                ),
            )
            warnings = [
                "Historical FMP consensus uses event time as an availability assumption."
            ]
            connection.execute(
                """
                INSERT INTO macro_source_snapshots (
                    snapshot_id, series_id, release_id, artifact_id,
                    semantic_identity, completeness, row_count, validation_state,
                    warnings_json, run_id, quality_flags_json
                ) VALUES (?, NULL, NULL, ?, ?, 'complete', ?, 'validated', ?, ?, '[]')
                """,
                (
                    snapshot_id,
                    artifact_id,
                    capture.semantic_identity,
                    len(capture.events),
                    dumps_strict(warnings),
                    run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO macro_snapshot_scopes (
                    scope_id, snapshot_id, scope_json, scope_digest,
                    completeness, tombstone_authoritative
                ) VALUES (?, ?, ?, ?, 'complete', 0)
                """,
                (
                    stable_id("fmp_calendar_scope", capture.semantic_identity),
                    snapshot_id,
                    dumps_strict(scope),
                    hashlib.sha256(dumps_strict(scope).encode("utf-8")).hexdigest(),
                ),
            )

            for event in capture.events:
                provider_event_id = stable_id(
                    "fmp_calendar_provider_event", event.kind, event.event_at
                )
                event_id = stable_id("calendar_event", "fmp", provider_event_id)
                existing = connection.execute(
                    """
                    SELECT event_id, event_at, event_precision, country, name, series_id
                    FROM economic_calendar
                    WHERE provider='fmp' AND provider_event_id=?
                    """,
                    (provider_event_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO economic_calendar (
                            event_id, provider, provider_event_id, event_at,
                            event_precision, country, name, series_id, created_run_id
                        ) VALUES (?, 'fmp', ?, ?, 'datetime', 'US', ?, NULL, ?)
                        """,
                        (event_id, provider_event_id, event.event_at, event.kind, run_id),
                    )
                    counts["events"] += 1
                else:
                    if tuple(existing) != (
                        event_id,
                        event.event_at,
                        "datetime",
                        "US",
                        event.kind,
                        None,
                    ):
                        raise ConflictError("FMP calendar persisted event identity drifted")

                current = connection.execute(
                    """
                    SELECT event_version_id, correction_sequence, actual_value,
                           consensus_value, previous_value, unit, state
                    FROM economic_calendar_event_versions
                    WHERE event_id=?
                    ORDER BY correction_sequence DESC LIMIT 1
                    """,
                    (event_id,),
                ).fetchone()
                expected = (
                    event.actual_text,
                    event.consensus_text,
                    event.previous_text,
                    event.unit,
                    "active",
                )
                if current is not None and tuple(current)[2:] == expected:
                    continue
                sequence = 1 if current is None else int(current["correction_sequence"]) + 1
                previous_id = None if current is None else str(current["event_version_id"])
                event_version_id = stable_id(
                    "calendar_event_version",
                    event_id,
                    str(sequence),
                    capture.semantic_identity,
                )
                connection.execute(
                    """
                    INSERT INTO economic_calendar_event_versions (
                        event_version_id, event_id, actual_value, consensus_value,
                        previous_value, unit, available_at, available_precision,
                        captured_at, captured_precision, correction_sequence,
                        supersedes_event_version_id, artifact_id, source_snapshot_id,
                        run_id, source_row, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime', ?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        event_version_id,
                        event_id,
                        event.actual_text,
                        event.consensus_text,
                        event.previous_text,
                        event.unit,
                        event.event_at,
                        capture.captured_at,
                        sequence,
                        previous_id,
                        artifact_id,
                        snapshot_id,
                        run_id,
                        min(event.source_rows),
                    ),
                )
                counts["versions"] += 1

            generic_artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=CALENDAR_DATASET_ID,
                content_sha256=capture.response_sha256,
                media_type="application/json",
                byte_count=len(capture.response_bytes),
                source_reference=SOURCE_REFERENCE,
                request_scope=scope,
                captured_at=capture.captured_at,
                captured_precision="datetime",
                normalization_version=NORMALIZATION_VERSION,
            )
            generic_snapshot = SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CALENDAR_DATASET_ID,
                semantic_identity=capture.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.events),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
                warnings=tuple(warnings),
            )
            quality = QualityWrite(
                quality_result_id=stable_id(
                    "fmp_calendar_quality", capture.semantic_identity
                ),
                dataset_id=CALENDAR_DATASET_ID,
                rule_id="closed_gdp_cpi_alias_mapping",
                rule_version="1.0.0",
                severity="informational",
                outcome="passed",
                subject_kind="snapshot",
                subject_id=snapshot_id,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                observed={"event_count": len(capture.events)},
            )
            return WriteResult(
                written_count=counts["events"] + counts["versions"],
                artifacts=(generic_artifact,),
                snapshot=generic_snapshot,
                quality_results=(quality,),
                warnings=tuple(warnings),
            )

        receipt = IngestionCoordinator(
            self._stores, code_version=NORMALIZATION_VERSION
        ).execute(
            role=StoreRole.MACRO,
            dataset_id=CALENDAR_DATASET_ID,
            output_dataset_ids=(CALENDAR_DATASET_ID,),
            semantic_identity=capture.semantic_identity,
            run_id=run_id,
            command=CALENDAR_COLLECTOR_ID,
            scope=scope,
            started_at=capture.captured_at,
            completed_at=capture.captured_at,
            fetched_count=len(capture.events),
            writer=writer,
        )
        return FmpCalendarPublishReport(
            outcome="unchanged" if receipt.outcome == "unchanged" else "published",
            semantic_identity=capture.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_events=counts["events"],
            written_versions=counts["versions"],
        )



class FmpEmploymentCalendarPublisher(FmpMacroCalendarPublisher):
    """Publish payroll/unemployment captures through the existing relations."""

    def _configure(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        canonical: bool,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("FMP employment calendar publisher paths are invalid")
        root = project_root.resolve(strict=True)
        store = macro_store.resolve(strict=True)
        if not root.is_dir() or not store.is_file() or store.is_symlink():
            raise _fail("FMP employment calendar publisher target is unavailable")
        try:
            store.relative_to(root)
        except ValueError as exc:
            raise _fail(
                "FMP employment calendar publisher target is outside the project"
            ) from exc
        if canonical:
            if (
                root != self._canonical_root.resolve(strict=True)
                or store != root / "data" / "macro.sqlite"
            ):
                raise _fail(
                    "FMP employment calendar canonical target binding is invalid"
                )
        else:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail(
                    "FMP employment calendar fixture root must be temporary"
                ) from exc
            if root == temporary:
                raise _fail("FMP employment calendar fixture root is too broad")
        _employment_registry_binding(registry)
        self._root = root
        self._store = store
        self._registry = registry
        self._stores = _store_map(root, store)

    def completed_windows(self) -> tuple[tuple[str, str], ...]:
        expected_keys = {
            "availability_assumption",
            "country",
            "event_kinds",
            "from",
            "normalization_version",
            "to",
        }
        windows: list[tuple[str, str]] = []
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT scope_json
                FROM ingestion_runs
                WHERE dataset_id=? AND command=? AND status='succeeded'
                ORDER BY scope_json
                """,
                (CALENDAR_DATASET_ID, EMPLOYMENT_CALENDAR_COLLECTOR_ID),
            ).fetchall()
        for row in rows:
            try:
                scope = loads_strict(str(row["scope_json"]))
            except (TypeError, ValueError) as exc:
                raise ConflictError(
                    "FMP employment calendar completed-window scope is invalid"
                ) from exc
            if (
                not isinstance(scope, dict)
                or set(scope) != expected_keys
                or scope.get("availability_assumption")
                != EVENT_DATE_AVAILABILITY_ASSUMPTION
                or scope.get("country") != "US"
                or scope.get("event_kinds") != list(EMPLOYMENT_KINDS)
                or scope.get("normalization_version")
                not in {
                    _LEGACY_EMPLOYMENT_NORMALIZATION_VERSION,
                    _PAYROLL_REFERENCE_PERIOD_NORMALIZATION_VERSION,
                    EMPLOYMENT_NORMALIZATION_VERSION,
                }
                or not isinstance(scope.get("from"), str)
                or not isinstance(scope.get("to"), str)
            ):
                raise ConflictError(
                    "FMP employment calendar completed-window scope is invalid"
                )
            start_date = str(scope["from"])
            end_date = str(scope["to"])
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ConflictError(
                    "FMP employment calendar completed-window scope is invalid"
                ) from exc
            if start > end:
                raise ConflictError(
                    "FMP employment calendar completed-window scope is invalid"
                )
            windows.append((start_date, end_date))
        return tuple(sorted(set(windows)))

    def publish(self, capture: FmpCalendarCapture) -> FmpCalendarPublishReport:
        if not isinstance(capture, FmpCalendarCapture) or not capture.events:
            raise _fail("FMP employment calendar capture is invalid")
        expected_capture = parse_fmp_us_employment_calendar(
            capture.response_bytes,
            captured_at=capture.captured_at,
            start_date=capture.request_start_date,
            end_date=capture.request_end_date,
        )
        if capture != expected_capture:
            raise _fail("FMP employment calendar capture binding is invalid")

        run_id = stable_id(
            "fmp_employment_calendar_run", capture.semantic_identity
        )
        artifact_id = stable_id(
            "fmp_employment_calendar_artifact_v3", capture.response_sha256
        )
        snapshot_id = stable_id(
            "fmp_employment_calendar_snapshot", capture.semantic_identity
        )
        scope = {
            "availability_assumption": EVENT_DATE_AVAILABILITY_ASSUMPTION,
            "country": "US",
            "event_kinds": list(EMPLOYMENT_KINDS),
            "from": capture.request_start_date,
            "normalization_version": EMPLOYMENT_NORMALIZATION_VERSION,
            "to": capture.request_end_date,
        }
        counts = {"events": 0, "versions": 0}

        def writer(connection: sqlite3.Connection, actual_run_id: str) -> WriteResult:
            if actual_run_id != run_id:
                raise ConflictError("FMP employment calendar run identity changed")
            connection.execute(
                """
                INSERT INTO macro_source_artifacts (
                    artifact_id, dataset_id, sha256, media_type, byte_count,
                    request_scope_json, captured_at, captured_precision,
                    source_resource, normalization_version, run_id
                ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, 'datetime', ?, ?, ?)
                """,
                (
                    artifact_id,
                    CALENDAR_DATASET_ID,
                    capture.response_sha256,
                    len(capture.response_bytes),
                    dumps_strict(scope),
                    capture.captured_at,
                    EMPLOYMENT_SOURCE_RESOURCE,
                    EMPLOYMENT_NORMALIZATION_VERSION,
                    run_id,
                ),
            )
            warnings = [
                "FMP employment consensus uses event time as an availability assumption."
            ]
            connection.execute(
                """
                INSERT INTO macro_source_snapshots (
                    snapshot_id, series_id, release_id, artifact_id,
                    semantic_identity, completeness, row_count, validation_state,
                    warnings_json, run_id, quality_flags_json
                ) VALUES (?, NULL, NULL, ?, ?, 'complete', ?, 'validated', ?, ?, '[]')
                """,
                (
                    snapshot_id,
                    artifact_id,
                    capture.semantic_identity,
                    len(capture.events),
                    dumps_strict(warnings),
                    run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO macro_snapshot_scopes (
                    scope_id, snapshot_id, scope_json, scope_digest,
                    completeness, tombstone_authoritative
                ) VALUES (?, ?, ?, ?, 'complete', 0)
                """,
                (
                    stable_id(
                        "fmp_employment_calendar_scope", capture.semantic_identity
                    ),
                    snapshot_id,
                    dumps_strict(scope),
                    hashlib.sha256(
                        dumps_strict(scope).encode("utf-8")
                    ).hexdigest(),
                ),
            )

            for event in capture.events:
                if event.kind == NONFARM_PAYROLLS_KIND:
                    if (
                        event.reference_period is None
                        or _EMPLOYMENT_REFERENCE_PERIOD.fullmatch(
                            event.reference_period
                        )
                        is None
                    ):
                        raise ConflictError(
                            "FMP payroll reference period is unavailable"
                        )
                    provider_digest = hashlib.sha256(
                        "\x00".join(
                            (event.kind, event.event_at, event.reference_period)
                        ).encode("utf-8")
                    ).hexdigest()
                    provider_event_id = (
                        f"fmp_employment_v2:{event.reference_period}:{provider_digest}"
                    )
                elif event.kind == UNEMPLOYMENT_RATE_KIND:
                    if (
                        event.reference_period is None
                        or _EMPLOYMENT_REFERENCE_PERIOD.fullmatch(
                            event.reference_period
                        )
                        is None
                    ):
                        raise ConflictError(
                            "FMP unemployment reference period is unavailable"
                        )
                    provider_digest = hashlib.sha256(
                        "\x00".join(
                            (event.kind, event.event_at, event.reference_period)
                        ).encode("utf-8")
                    ).hexdigest()
                    provider_event_id = (
                        "fmp_unemployment_v3:"
                        f"{event.reference_period}:{provider_digest}"
                    )
                else:
                    raise ConflictError("FMP employment event kind is invalid")
                event_id = stable_id("calendar_event", "fmp", provider_event_id)
                existing = connection.execute(
                    """
                    SELECT event_id, event_at, event_precision, country, name, series_id
                    FROM economic_calendar
                    WHERE provider='fmp' AND provider_event_id=?
                    """,
                    (provider_event_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO economic_calendar (
                            event_id, provider, provider_event_id, event_at,
                            event_precision, country, name, series_id, created_run_id
                        ) VALUES (?, 'fmp', ?, ?, 'datetime', 'US', ?, NULL, ?)
                        """,
                        (
                            event_id,
                            provider_event_id,
                            event.event_at,
                            event.kind,
                            run_id,
                        ),
                    )
                    counts["events"] += 1
                elif tuple(existing) != (
                    event_id,
                    event.event_at,
                    "datetime",
                    "US",
                    event.kind,
                    None,
                ):
                    raise ConflictError(
                        "FMP employment calendar persisted event identity drifted"
                    )

                current = connection.execute(
                    """
                    SELECT event_version_id, correction_sequence, actual_value,
                           consensus_value, previous_value, unit, state
                    FROM economic_calendar_event_versions
                    WHERE event_id=?
                    ORDER BY correction_sequence DESC LIMIT 1
                    """,
                    (event_id,),
                ).fetchone()
                expected = (
                    event.actual_text,
                    event.consensus_text,
                    event.previous_text,
                    event.unit,
                    "active",
                )
                if current is not None and tuple(current)[2:] == expected:
                    continue
                sequence = (
                    1 if current is None else int(current["correction_sequence"]) + 1
                )
                previous_id = (
                    None if current is None else str(current["event_version_id"])
                )
                event_version_id = stable_id(
                    "calendar_event_version",
                    event_id,
                    str(sequence),
                    capture.semantic_identity,
                )
                connection.execute(
                    """
                    INSERT INTO economic_calendar_event_versions (
                        event_version_id, event_id, actual_value, consensus_value,
                        previous_value, unit, available_at, available_precision,
                        captured_at, captured_precision, correction_sequence,
                        supersedes_event_version_id, artifact_id, source_snapshot_id,
                        run_id, source_row, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime', ?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        event_version_id,
                        event_id,
                        event.actual_text,
                        event.consensus_text,
                        event.previous_text,
                        event.unit,
                        event.event_at,
                        capture.captured_at,
                        sequence,
                        previous_id,
                        artifact_id,
                        snapshot_id,
                        run_id,
                        min(event.source_rows),
                    ),
                )
                counts["versions"] += 1

            generic_artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=CALENDAR_DATASET_ID,
                content_sha256=capture.response_sha256,
                media_type="application/json",
                byte_count=len(capture.response_bytes),
                source_reference=EMPLOYMENT_SOURCE_REFERENCE,
                request_scope=scope,
                captured_at=capture.captured_at,
                captured_precision="datetime",
                normalization_version=EMPLOYMENT_NORMALIZATION_VERSION,
            )
            generic_snapshot = SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CALENDAR_DATASET_ID,
                semantic_identity=capture.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.events),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
                warnings=tuple(warnings),
            )
            quality = QualityWrite(
                quality_result_id=stable_id(
                    "fmp_employment_calendar_quality", capture.semantic_identity
                ),
                dataset_id=CALENDAR_DATASET_ID,
                rule_id="closed_employment_alias_mapping",
                rule_version="1.0.0",
                severity="informational",
                outcome="passed",
                subject_kind="snapshot",
                subject_id=snapshot_id,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                observed={"event_count": len(capture.events)},
            )
            return WriteResult(
                written_count=counts["events"] + counts["versions"],
                artifacts=(generic_artifact,),
                snapshot=generic_snapshot,
                quality_results=(quality,),
                warnings=tuple(warnings),
            )

        receipt = IngestionCoordinator(
            self._stores, code_version=EMPLOYMENT_NORMALIZATION_VERSION
        ).execute(
            role=StoreRole.MACRO,
            dataset_id=CALENDAR_DATASET_ID,
            output_dataset_ids=(CALENDAR_DATASET_ID,),
            semantic_identity=capture.semantic_identity,
            run_id=run_id,
            command=EMPLOYMENT_CALENDAR_COLLECTOR_ID,
            scope=scope,
            started_at=capture.captured_at,
            completed_at=capture.captured_at,
            fetched_count=len(capture.events),
            writer=writer,
        )
        return FmpCalendarPublishReport(
            outcome="unchanged" if receipt.outcome == "unchanged" else "published",
            semantic_identity=capture.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_events=counts["events"],
            written_versions=counts["versions"],
        )

class MacroReleaseSurpriseRepository:
    """Read-only, on-demand surprise calculation over calendar and vintages."""

    def __init__(self, *, macro_store: Path, registry: object) -> None:
        if not isinstance(macro_store, Path) or not macro_store.is_absolute():
            raise _fail("Macro surprise store path is invalid")
        store = macro_store.resolve(strict=True)
        if not store.is_file() or store.is_symlink():
            raise StoreUnavailableError("Macro surprise store is unavailable")
        _registry_binding(registry)
        self._store = store
        self._registry = registry
        root = store.parent.parent
        self._stores = _store_map(root, store)

    @staticmethod
    def _gdp_selection(
        rows: Iterable[sqlite3.Row],
        *,
        reference_period: str,
    ) -> _GdpOfficialSelection:
        candidates = tuple(rows)
        if not candidates:
            return _GdpOfficialSelection(
                official_actual=None,
                official_version_id=None,
                reference_period=reference_period,
                consensus_mapping_basis="unmatched_bea_release_date",
                release_stage=None,
                status="missing_official_actual",
            )
        if len(candidates) != 1:
            return _GdpOfficialSelection(
                official_actual=None,
                official_version_id=None,
                reference_period=reference_period,
                consensus_mapping_basis="ambiguous_bea_release_date",
                release_stage=None,
                status="ambiguous_official_actual",
            )
        row = candidates[0]
        release_stage = str(row["release_stage"])
        if (
            release_stage not in _GDP_RELEASE_STAGE_PRIORITY
            or str(row["period"]) != reference_period
            or str(row["unit"]) != "percent"
        ):
            raise ConflictError("Official GDP release mapping is invalid")
        try:
            value = Decimal(str(row["value_text"]))
        except InvalidOperation as exc:
            raise ConflictError("Official GDP release value is invalid") from exc
        return _GdpOfficialSelection(
            official_actual=value,
            official_version_id=str(row["version_id"]),
            reference_period=reference_period,
            consensus_mapping_basis=_GDP_RELEASE_MAPPING_BASIS[release_stage],
            release_stage=release_stage,
            status="ok",
        )

    @classmethod
    def _official_gdp_same_date(
        cls,
        connection: sqlite3.Connection,
        event_date: str,
        reference_period: str,
    ) -> _GdpOfficialSelection | None:
        rows = connection.execute(
            """
            SELECT version.version_id, version.value_text, version.period,
                   version.unit, release.release_stage
            FROM macro_live_vintage_releases AS release
            JOIN macro_live_vintage_observation_versions AS version
              ON version.release_id = release.release_id
             AND version.series_id = release.series_id
            WHERE release.series_id='macro.gdp.real_qoq_saar_pct'
              AND version.period=?
              AND substr(release.available_at, 1, 10)=?
              AND (
                    (
                        release.is_first_release=1
                        AND release.release_stage IN ('advance', 'initial')
                    )
                    OR (
                        release.is_first_release=0
                        AND release.release_stage IN ('second', 'third')
                    )
                  )
              AND version.correction_sequence=(
                  SELECT MAX(later.correction_sequence)
                  FROM macro_live_vintage_observation_versions AS later
                  WHERE later.series_id=version.series_id
                    AND later.period=version.period
                    AND later.release_id=version.release_id
              )
            ORDER BY release.release_stage, version.version_id
            """,
            (reference_period, event_date),
        ).fetchall()
        if not rows:
            return None
        return cls._gdp_selection(rows, reference_period=reference_period)

    @staticmethod
    def _preceding_gdp_quarter(event_date: str) -> str:
        parsed = datetime.strptime(event_date, "%Y-%m-%d")
        if parsed.month <= 3:
            return f"{parsed.year - 1}Q4"
        return f"{parsed.year}Q{(parsed.month - 1) // 3}"


    @staticmethod
    def _preceding_cpi_month(event_date: str) -> str:
        try:
            parsed = datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ConflictError("CPI calendar event date is invalid") from exc
        if parsed.month == 1:
            return f"{parsed.year - 1}-12"
        return f"{parsed.year}-{parsed.month - 1:02d}"

    @staticmethod
    def _coalesce_cpi_candidates(
        candidates: Iterable[ReleaseSurprise],
    ) -> tuple[ReleaseSurprise, ...]:
        rows = tuple(candidates)
        if len(rows) < 2:
            return rows
        if any(
            row.kind not in CPI_KINDS
            or row.actual_source != "fmp_calendar"
            or row.consensus_mapping_basis != _CPI_FMP_EVENT_MAPPING_BASIS
            or row.unit != "%"
            or row.reference_period is None
            or row.official_version_id is not None
            or row.official_prior_version_id is not None
            or row.release_stage is not None
            or row.is_fallback
            or row.coalesced_event_version_ids
            for row in rows
        ):
            raise ConflictError("FMP CPI candidate mapping is invalid")
        group_identity = {
            (row.kind, row.reference_period, row.event_at[:10]) for row in rows
        }
        if len(group_identity) != 1:
            raise ConflictError("FMP CPI candidate group is invalid")

        # Preserve ordinary complete or incomplete rows exactly as they were.
        # Coalescing is reserved for the narrow case where every row supplies
        # exactly one side and the release-day values are uniquely compatible.
        if any(
            (row.fmp_actual is None) == (row.consensus is None) for row in rows
        ):
            return rows
        actuals = {
            row.fmp_actual for row in rows if row.fmp_actual is not None
        }
        consensuses = {
            row.consensus for row in rows if row.consensus is not None
        }
        if not actuals or not consensuses:
            return rows
        if (
            len(actuals) != 1
            or len(consensuses) != 1
            or any(not value.is_finite() for value in (*actuals, *consensuses))
        ):
            # Conflicting complementary rows cannot safely represent one
            # release surprise, so omit this derived group while retaining all
            # immutable source events in the calendar tables.
            return ()

        actual = next(iter(actuals))
        consensus = next(iter(consensuses))
        primary = max(
            rows,
            key=lambda row: (row.event_at, row.event_id, row.event_version_id),
        )
        companion_ids = tuple(
            row.event_version_id
            for row in sorted(
                rows,
                key=lambda row: (row.event_at, row.event_id, row.event_version_id),
            )
            if row.event_version_id != primary.event_version_id
        )
        if not companion_ids or len(set(companion_ids)) != len(companion_ids):
            raise ConflictError("FMP CPI complementary lineage is invalid")
        return (
            replace(
                primary,
                consensus_mapping_basis=_CPI_FMP_COALESCED_MAPPING_BASIS,
                consensus=consensus,
                fmp_actual=actual,
                official_actual=actual,
                surprise=actual - consensus,
                status="ok",
                coalesced_event_version_ids=companion_ids,
            ),
        )

    @staticmethod
    def _payroll_reference_period(provider_event_id: str) -> str | None:
        match = _PAYROLL_PROVIDER_EVENT_ID.fullmatch(provider_event_id)
        return None if match is None else match.group("period")

    @staticmethod
    def _unemployment_reference_period(provider_event_id: str) -> str | None:
        match = _UNEMPLOYMENT_PROVIDER_EVENT_ID.fullmatch(provider_event_id)
        return None if match is None else match.group("period")

    @staticmethod
    def _employment_reference_period(event_date: str) -> str | None:
        try:
            parsed = datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ConflictError("Employment calendar event date is invalid") from exc
        if not 1 <= parsed.day <= 10:
            return None
        if parsed.month == 1:
            return f"{parsed.year - 1}-12"
        return f"{parsed.year}-{parsed.month - 1:02d}"

    @staticmethod
    def _employment_previous_month(reference_period: str) -> str:
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_period) is None:
            raise ConflictError("Employment reference period is invalid")
        year = int(reference_period[:4])
        month = int(reference_period[-2:])
        if month == 1:
            return f"{year - 1}-12"
        return f"{year}-{month - 1:02d}"

    @staticmethod
    def _employment_release_window(reference_period: str) -> tuple[str, str]:
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_period) is None:
            raise ConflictError("Employment reference period is invalid")
        year = int(reference_period[:4])
        month = int(reference_period[-2:])
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-10"

    @classmethod
    def _employment_event_count(
        cls,
        connection: sqlite3.Connection,
        *,
        kind: str,
        reference_period: str,
    ) -> int:
        start_date, end_date = cls._employment_release_window(reference_period)
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM economic_calendar AS event
            JOIN economic_calendar_event_versions AS version
              ON version.event_id=event.event_id
            WHERE event.provider='fmp'
              AND event.name=?
              AND substr(event.event_at, 1, 10)>=?
              AND substr(event.event_at, 1, 10)<=?
              AND version.state='active'
              AND version.correction_sequence=(
                  SELECT MAX(later.correction_sequence)
                  FROM economic_calendar_event_versions AS later
                  WHERE later.event_id=event.event_id
                    AND later.state='active'
              )
            """,
            (kind, start_date, end_date),
        ).fetchone()
        if row is None:
            raise ConflictError("Employment calendar identity count is unavailable")
        return int(row[0])

    @staticmethod
    def _employment_series(kind: str) -> tuple[str, str]:
        if kind == NONFARM_PAYROLLS_KIND:
            return "macro.bls.total_nonfarm_payrolls_sa", "thousands_persons"
        if kind == UNEMPLOYMENT_RATE_KIND:
            return "macro.bls.unemployment_rate_sa", "percent"
        raise ConflictError("Employment surprise kind is invalid")

    @staticmethod
    def _employment_value(
        row: sqlite3.Row,
        *,
        expected_unit: str,
    ) -> Decimal:
        if str(row["unit"]) != expected_unit:
            raise ConflictError("Official employment unit is invalid")
        try:
            value = Decimal(str(row["value_text"]))
        except InvalidOperation as exc:
            raise ConflictError("Official employment value is invalid") from exc
        if not value.is_finite():
            raise ConflictError("Official employment value is invalid")
        return value

    @classmethod
    def _employment_selection_from_rows(
        cls,
        *,
        kind: str,
        reference_period: str,
        previous_period: str | None,
        rows_by_period: dict[str, sqlite3.Row],
        consensus_mapping_basis: str,
    ) -> _EmploymentOfficialSelection:
        _, expected_unit = cls._employment_series(kind)
        target = rows_by_period[reference_period]
        target_value = cls._employment_value(target, expected_unit=expected_unit)
        if kind == NONFARM_PAYROLLS_KIND:
            if previous_period is None:
                raise ConflictError("Payroll prior reference period is unavailable")
            prior = rows_by_period[previous_period]
            prior_value = cls._employment_value(prior, expected_unit=expected_unit)
            return _EmploymentOfficialSelection(
                official_actual=target_value - prior_value,
                official_version_id=str(target["version_id"]),
                official_prior_version_id=str(prior["version_id"]),
                reference_period=reference_period,
                consensus_mapping_basis=consensus_mapping_basis,
            )
        return _EmploymentOfficialSelection(
            official_actual=target_value,
            official_version_id=str(target["version_id"]),
            official_prior_version_id=None,
            reference_period=reference_period,
            consensus_mapping_basis=consensus_mapping_basis,
        )

    @classmethod
    def _official_employment_same_day_bls(
        cls,
        connection: sqlite3.Connection,
        *,
        kind: str,
        reference_period: str,
        event_date: str,
    ) -> _EmploymentOfficialSelection | None:
        series_id, _ = cls._employment_series(kind)
        previous_period = (
            cls._employment_previous_month(reference_period)
            if kind == NONFARM_PAYROLLS_KIND
            else None
        )
        periods = (
            (reference_period, previous_period)
            if previous_period is not None
            else (reference_period,)
        )
        placeholders = ",".join("?" for _ in periods)
        rows = connection.execute(
            f"""
            SELECT capture.capture_id, capture.captured_at,
                   version.version_id, version.period, version.value_text,
                   version.unit
            FROM macro_live_vintage_captures AS capture
            JOIN macro_live_vintage_capture_membership AS membership
              ON membership.capture_id=capture.capture_id
            JOIN macro_live_vintage_observation_versions AS version
              ON version.version_id=membership.version_id
             AND version.series_id=membership.series_id
            WHERE capture.provider='bls'
              AND substr(capture.captured_at, 1, 10)=?
              AND version.series_id=?
              AND version.period IN ({placeholders})
            ORDER BY capture.captured_at, capture.capture_id,
                     version.period, version.version_id
            """,
            (event_date, series_id, *periods),
        ).fetchall()
        required = frozenset(periods)
        candidates: dict[str, dict[str, sqlite3.Row]] = {}
        order: list[str] = []
        duplicates: set[str] = set()
        for row in rows:
            capture_id = str(row["capture_id"])
            if capture_id not in candidates:
                candidates[capture_id] = {}
                order.append(capture_id)
            period = str(row["period"])
            if period in candidates[capture_id]:
                duplicates.add(capture_id)
                continue
            candidates[capture_id][period] = row
        for capture_id in order:
            by_period = candidates[capture_id]
            if capture_id in duplicates or frozenset(by_period) != required:
                continue
            return cls._employment_selection_from_rows(
                kind=kind,
                reference_period=reference_period,
                previous_period=previous_period,
                rows_by_period=by_period,
                consensus_mapping_basis="same_day_bls_capture",
            )
        return None

    @classmethod
    def _official_employment_rtdsm_proxy(
        cls,
        connection: sqlite3.Connection,
        *,
        kind: str,
        reference_period: str,
    ) -> _EmploymentOfficialSelection | None:
        series_id, expected_unit = cls._employment_series(kind)
        target_rows = connection.execute(
            """
            SELECT release.release_id, release.source_release_order,
                   release.available_at, version.version_id, version.period,
                   version.value_text, version.unit
            FROM macro_live_vintage_releases AS release
            JOIN macro_live_vintage_captures AS capture
              ON capture.capture_id=release.capture_id
            JOIN macro_live_vintage_observation_versions AS version
              ON version.release_id=release.release_id
             AND version.series_id=release.series_id
            WHERE release.series_id=?
              AND capture.provider='philadelphia_fed'
              AND version.period=?
              AND version.correction_sequence=(
                  SELECT MAX(later.correction_sequence)
                  FROM macro_live_vintage_observation_versions AS later
                  WHERE later.series_id=version.series_id
                    AND later.period=version.period
                    AND later.release_id=version.release_id
              )
            ORDER BY release.source_release_order, release.available_at,
                     release.release_id, version.version_id
            """,
            (series_id, reference_period),
        ).fetchall()
        if not target_rows:
            return None
        target = target_rows[0]
        target_value = cls._employment_value(target, expected_unit=expected_unit)
        if kind == UNEMPLOYMENT_RATE_KIND:
            return _EmploymentOfficialSelection(
                official_actual=target_value,
                official_version_id=str(target["version_id"]),
                official_prior_version_id=None,
                reference_period=reference_period,
                consensus_mapping_basis="first_rtdsm_vintage_proxy",
            )

        previous_period = cls._employment_previous_month(reference_period)
        prior_rows = connection.execute(
            """
            SELECT release.source_release_order, release.available_at,
                   release.release_id, version.version_id, version.period,
                   version.value_text, version.unit
            FROM macro_live_vintage_releases AS release
            JOIN macro_live_vintage_captures AS capture
              ON capture.capture_id=release.capture_id
            JOIN macro_live_vintage_observation_versions AS version
              ON version.release_id=release.release_id
             AND version.series_id=release.series_id
            WHERE release.series_id=?
              AND capture.provider='philadelphia_fed'
              AND version.period=?
              AND release.source_release_order<=?
              AND version.correction_sequence=(
                  SELECT MAX(later.correction_sequence)
                  FROM macro_live_vintage_observation_versions AS later
                  WHERE later.series_id=version.series_id
                    AND later.period=version.period
                    AND later.release_id=version.release_id
              )
            ORDER BY release.source_release_order DESC, release.available_at DESC,
                     release.release_id DESC, version.version_id DESC
            """,
            (
                series_id,
                previous_period,
                str(target["source_release_order"]),
            ),
        ).fetchall()
        if not prior_rows:
            return None
        prior = prior_rows[0]
        prior_value = cls._employment_value(prior, expected_unit=expected_unit)
        return _EmploymentOfficialSelection(
            official_actual=target_value - prior_value,
            official_version_id=str(target["version_id"]),
            official_prior_version_id=str(prior["version_id"]),
            reference_period=reference_period,
            consensus_mapping_basis="first_rtdsm_vintage_proxy",
        )

    @staticmethod
    def _best_gdp_release(
        candidates: Iterable[ReleaseSurprise],
    ) -> ReleaseSurprise | None:
        by_priority: dict[int, list[ReleaseSurprise]] = {}
        for candidate in candidates:
            stage = candidate.release_stage
            if stage not in _GDP_RELEASE_STAGE_PRIORITY:
                raise ConflictError("GDP release stage is invalid")
            by_priority.setdefault(_GDP_RELEASE_STAGE_PRIORITY[stage], []).append(
                candidate
            )
        first_missing: ReleaseSurprise | None = None
        for priority in sorted(by_priority):
            stage_candidates = by_priority[priority]
            signatures = {
                (
                    item.event_at[:10],
                    item.reference_period,
                    item.consensus,
                    item.fmp_actual,
                    item.official_actual,
                    item.surprise,
                    item.unit,
                    item.actual_source,
                    item.consensus_mapping_basis,
                    item.status,
                    item.official_version_id,
                    item.release_stage,
                    item.is_fallback,
                )
                for item in stage_candidates
            }
            if len(signatures) != 1:
                return None
            candidate = max(
                stage_candidates,
                key=lambda item: (
                    item.event_at,
                    item.event_id,
                    item.event_version_id,
                ),
            )
            if candidate.status == "ok":
                return candidate
            if first_missing is None:
                first_missing = candidate
        return first_missing

    def query(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> tuple[ReleaseSurprise, ...]:
        selected = ALL_SURPRISE_KINDS if kinds is None else kinds
        if (
            not isinstance(selected, tuple)
            or not selected
            or any(item not in ALL_SURPRISE_KINDS for item in selected)
            or len(set(selected)) != len(selected)
        ):
            raise _fail("Macro surprise kinds are invalid")
        for label, value in (("start_date", start_date), ("end_date", end_date)):
            if value is not None:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except (TypeError, ValueError) as exc:
                    raise _fail(f"Macro surprise {label} is invalid") from exc
        if start_date is not None and end_date is not None and start_date > end_date:
            raise _fail("Macro surprise date range is invalid")

        placeholders = ",".join("?" for _ in selected)
        sql = f"""
            SELECT event.event_id, event.provider_event_id,
                   event.name, event.event_at,
                   version.event_version_id, version.actual_value,
                   version.consensus_value, version.unit
            FROM economic_calendar AS event
            JOIN economic_calendar_event_versions AS version
              ON version.event_id=event.event_id
            WHERE event.provider='fmp'
              AND event.country='US'
              AND event.name IN ({placeholders})
              AND version.state='active'
              AND version.correction_sequence=(
                  SELECT MAX(later.correction_sequence)
                  FROM economic_calendar_event_versions AS later
                  WHERE later.event_id=event.event_id AND later.state='active'
              )
            ORDER BY event.event_at, event.name, event.event_id
        """
        results: list[ReleaseSurprise] = []
        gdp_candidates: dict[str, list[ReleaseSurprise]] = {}
        cpi_candidates: dict[
            tuple[str, str, str], list[ReleaseSurprise]
        ] = {}
        payroll_candidates: dict[str, list[ReleaseSurprise]] = {}
        unemployment_candidates: dict[str, list[ReleaseSurprise]] = {}
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            rows = connection.execute(sql, selected).fetchall()
            for row in rows:
                kind = str(row["name"])
                event_at = str(row["event_at"])
                event_date = event_at[:10]
                consensus = (
                    None
                    if row["consensus_value"] is None
                    else Decimal(str(row["consensus_value"]))
                )
                fmp_actual = (
                    None
                    if row["actual_value"] is None
                    else Decimal(str(row["actual_value"]))
                )
                official_version_id: str | None = None
                official_prior_version_id: str | None = None
                release_stage: str | None = None
                is_fallback = False
                if kind == GDP_ADVANCE_KIND:
                    reference_period = self._preceding_gdp_quarter(event_date)
                    gdp_selection = self._official_gdp_same_date(
                        connection,
                        event_date,
                        reference_period,
                    )
                    if (
                        gdp_selection is None
                        or gdp_selection.status != "ok"
                        or gdp_selection.official_actual is None
                        or gdp_selection.official_version_id is None
                        or gdp_selection.reference_period != reference_period
                        or gdp_selection.release_stage is None
                    ):
                        continue
                    official_actual = gdp_selection.official_actual
                    official_version_id = gdp_selection.official_version_id
                    consensus_mapping_basis = (
                        gdp_selection.consensus_mapping_basis
                    )
                    release_stage = gdp_selection.release_stage
                    status = "ok"
                    actual_source = "bea_gdp_vintage"
                    is_fallback = (
                        _GDP_RELEASE_STAGE_PRIORITY[release_stage] > 0
                    )
                elif kind in CPI_KINDS:
                    reference_period = self._preceding_cpi_month(event_date)
                    official_actual = fmp_actual
                    consensus_mapping_basis = _CPI_FMP_EVENT_MAPPING_BASIS
                    status = "ok" if fmp_actual is not None else "missing_actual"
                    actual_source = "fmp_calendar"
                elif kind == NONFARM_PAYROLLS_KIND:
                    reference_period = self._payroll_reference_period(
                        str(row["provider_event_id"])
                    )
                    if reference_period is None:
                        continue
                    official_actual = fmp_actual
                    consensus_mapping_basis = "same_fmp_payroll_reference_period"
                    status = "ok" if fmp_actual is not None else "missing_actual"
                    actual_source = "fmp_calendar"
                elif kind == UNEMPLOYMENT_RATE_KIND:
                    reference_period = self._unemployment_reference_period(
                        str(row["provider_event_id"])
                    )
                    if reference_period is None:
                        continue
                    official_actual = fmp_actual
                    consensus_mapping_basis = (
                        "same_fmp_unemployment_reference_period"
                    )
                    status = "ok" if fmp_actual is not None else "missing_actual"
                    actual_source = "fmp_calendar"
                else:
                    official_actual = fmp_actual
                    reference_period = None
                    consensus_mapping_basis = "same_fmp_event"
                    status = "ok" if fmp_actual is not None else "missing_actual"
                    actual_source = "fmp_calendar"
                if consensus is None and status == "ok":
                    status = "missing_consensus"
                surprise = (
                    official_actual - consensus
                    if status == "ok"
                    and official_actual is not None
                    and consensus is not None
                    else None
                )
                item = ReleaseSurprise(
                    event_id=str(row["event_id"]),
                    event_version_id=str(row["event_version_id"]),
                    kind=kind,
                    event_at=event_at,
                    reference_period=reference_period,
                    consensus_mapping_basis=consensus_mapping_basis,
                    consensus=consensus,
                    fmp_actual=fmp_actual,
                    official_actual=official_actual,
                    surprise=surprise,
                    unit=str(row["unit"]),
                    actual_source=actual_source,
                    availability_assumption=EVENT_DATE_AVAILABILITY_ASSUMPTION,
                    status=status,
                    official_version_id=official_version_id,
                    official_prior_version_id=official_prior_version_id,
                    release_stage=release_stage,
                    is_fallback=is_fallback,
                )
                if kind == GDP_ADVANCE_KIND:
                    gdp_candidates.setdefault(reference_period, []).append(item)
                elif kind in CPI_KINDS:
                    cpi_candidates.setdefault(
                        (kind, reference_period, event_date), []
                    ).append(item)
                elif kind == NONFARM_PAYROLLS_KIND:
                    payroll_candidates.setdefault(reference_period, []).append(item)
                elif kind == UNEMPLOYMENT_RATE_KIND:
                    unemployment_candidates.setdefault(reference_period, []).append(
                        item
                    )
                else:
                    results.append(item)

        for reference_period in sorted(gdp_candidates):
            selected_gdp = self._best_gdp_release(
                gdp_candidates[reference_period]
            )
            if selected_gdp is not None:
                results.append(selected_gdp)
        for key in sorted(cpi_candidates):
            results.extend(self._coalesce_cpi_candidates(cpi_candidates[key]))
        for reference_period in sorted(payroll_candidates):
            candidates = payroll_candidates[reference_period]
            if len(candidates) != 1:
                continue
            results.append(candidates[0])
        for reference_period in sorted(unemployment_candidates):
            candidates = unemployment_candidates[reference_period]
            if len(candidates) != 1:
                continue
            results.append(candidates[0])
        results.sort(key=lambda item: (item.event_at, item.kind, item.event_id))
        return tuple(
            item
            for item in results
            if (start_date is None or item.event_at[:10] >= start_date)
            and (end_date is None or item.event_at[:10] <= end_date)
        )

__all__ = (
    "ALL_SURPRISE_KINDS",
    "CALENDAR_COLLECTOR_ID",
    "CALENDAR_DATASET_ID",
    "CPI_CORE_MOM_KIND",
    "CPI_CORE_YOY_KIND",
    "CPI_HEADLINE_MOM_KIND",
    "CPI_HEADLINE_YOY_KIND",
    "CPI_KINDS",
    "EMPLOYMENT_CALENDAR_COLLECTOR_ID",
    "EMPLOYMENT_KINDS",
    "EMPLOYMENT_NORMALIZATION_VERSION",
    "EVENT_DATE_AVAILABILITY_ASSUMPTION",
    "FmpCalendarCapture",
    "FmpCalendarEvent",
    "FmpCalendarPublishReport",
    "FmpEmploymentCalendarPublisher",
    "FmpMacroCalendarPublisher",
    "GDP_ADVANCE_KIND",
    "MAX_SOURCE_ROWS",
    "MacroReleaseSurpriseRepository",
    "NONFARM_PAYROLLS_KIND",
    "NORMALIZATION_VERSION",
    "ReleaseSurprise",
    "SUPPORTED_KINDS",
    "UNEMPLOYMENT_RATE_KIND",
    "parse_fmp_us_employment_calendar",
    "parse_fmp_us_gdp_cpi_calendar",
)
