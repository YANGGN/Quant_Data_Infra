"""Bounded SEC submissions and CompanyFacts normalization.

The host-owned operation fetches exactly one SEC submissions body and one
CompanyFacts body before calling this module.  This module has no credential or
network path: it validates the two injected bodies, stages a narrowly scoped
``sec-core-v2`` fact set, and publishes it atomically through the existing
company-store control plane.

The company schema has one raw-fact natural key per accession/concept/unit/end
date.  A CompanyFacts response can contain multiple duration contexts for that
same key.  This v1 pilot therefore selects one explicit context rather than
misrepresenting quarter/YTD/FY alternatives as correction versions.  The
selection policy is retained in the capture scope and warning metadata.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
import sqlite3
from time import monotonic
from typing import Any, Final

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue


AAPL_CIK: Final = "0000320193"
SEC_AAPL_COMPANYFACTS_COLLECTOR_ID: Final = "sec.company.aapl_fundamentals"
SEC_COMPANYFACTS_COLLECTOR_ID: Final = "sec.company.market_fundamentals"
SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID: Final = "fixture.company.sec_evidence"
SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID: Final = "fixture.company.fundamentals"
SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID: Final = "fixture.company.issuers"
SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID: Final = "fixture.company.filings"
SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID: Final = (
    "fixture.company.filing_issuer_membership"
)
# The market collector writes the same established company datasets; its
# collector and source identities are issuer-scoped rather than AAPL-scoped.
SEC_COMPANYFACTS_EVIDENCE_DATASET_ID: Final = SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID
SEC_COMPANYFACTS_CANONICAL_DATASET_ID: Final = SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID
SEC_COMPANYFACTS_IDENTITY_DATASET_ID: Final = SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID
SEC_COMPANYFACTS_FILINGS_DATASET_ID: Final = SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID
SEC_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID: Final = (
    SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID
)
SEC_AAPL_COMPANYFACTS_NORMALIZATION_VERSION: Final = "sec_aapl_companyfacts.v1"
SEC_COMPANYFACTS_NORMALIZATION_VERSION: Final = "sec_companyfacts.v1"
SEC_CORE_MAPPING_VERSION: Final = "sec-core-v2"

# The AAPL aliases retain the bounded pilot contract exactly.  The market
# collector still handles one issuer per call, but accepts the larger complete
# CompanyFacts bundle that issuers outside the pilot can legitimately return.
MAX_TOTAL_RESPONSE_BYTES: Final = 16 * 1024 * 1024
# Each endpoint can consume the entire bundle allowance; the combined body is
# still capped below.  This matches the operation's sequential remaining-byte
# bound without inventing endpoint-specific limits.
MAX_SUBMISSIONS_BYTES: Final = MAX_TOTAL_RESPONSE_BYTES
MAX_COMPANYFACTS_BYTES: Final = MAX_TOTAL_RESPONSE_BYTES
MAX_SOURCE_ROWS: Final = 10_000
MAX_SUBMISSION_FILINGS: Final = 2_000
MAX_CORE_FACT_CANDIDATES: Final = MAX_SOURCE_ROWS
MAX_GENERIC_TOTAL_RESPONSE_BYTES: Final = 64 * 1024 * 1024
MAX_GENERIC_SUBMISSIONS_BYTES: Final = MAX_GENERIC_TOTAL_RESPONSE_BYTES
MAX_GENERIC_COMPANYFACTS_BYTES: Final = MAX_GENERIC_TOTAL_RESPONSE_BYTES
MAX_GENERIC_SOURCE_ROWS: Final = 50_000
MAX_GENERIC_SUBMISSION_FILINGS: Final = MAX_GENERIC_SOURCE_ROWS
MAX_GENERIC_CORE_FACT_CANDIDATES: Final = MAX_GENERIC_SOURCE_ROWS
MAX_TEXT_LENGTH: Final = 1_024
_MAX_SEMANTIC_BYTES: Final = 48 * 1024 * 1024
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_SEC_FRAME = re.compile(r"^[A-Z]{2}[0-9]{4}(?:Q[1-4](?:I|YTD)?|I|YTD)?$")
_STANDARD_TAXONOMIES: Final = frozenset({"us-gaap", "dei"})
_SUPPORTED_PERIODIC_FORMS: Final = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})
_SUPPORTED_ANNUAL_FORMS: Final = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})
_SUPPORTED_FISCAL_PERIODS: Final = frozenset({"Q1", "Q2", "Q3", "Q4", "FY"})
_REQUIRED_DATASETS: Final = frozenset(
    {
        SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID,
        SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
        SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID,
        SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID,
        SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID,
    }
)
_CONTEXT_POLICY_WARNING: Final = (
    "companyfacts_v1_schema_policy_excludes_nonselected_context_variants"
)
_CORE_ROW_POLICY_WARNING: Final = (
    "companyfacts_v1_core_policy_excludes_nonperiodic_or_incomplete_fiscal_rows"
)


def _fail(message: str) -> ValidationError:
    return ValidationError(f"SEC AAPL CompanyFacts {message}")


def _text(value: object, label: str, *, maximum: int = MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _fail(f"{label} is invalid")
    return value.strip()


def _optional_text(value: object, label: str, *, maximum: int = MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(f"{label} is invalid")
    if not value.strip():
        return None
    return _text(value, label, maximum=maximum)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _fail(f"{label} is invalid")
    return value


def _array(value: object, label: str, *, maximum: int, allow_empty: bool = False) -> Sequence[Any]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise _fail(f"{label} is invalid")
    return value


def _cik(value: object, label: str) -> str:
    if isinstance(value, bool):
        raise _fail(f"{label} is invalid")
    if isinstance(value, int):
        if value < 1:
            raise _fail(f"{label} is invalid")
        text = str(value)
    elif isinstance(value, str) and value.isdecimal():
        text = value
    else:
        raise _fail(f"{label} is invalid")
    if not 1 <= len(text) <= 10 or int(text) < 1:
        raise _fail(f"{label} is invalid")
    return text.zfill(10)


def _date_text(value: object, label: str) -> str:
    text = _text(value, label, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise _fail(f"{label} is invalid") from exc
    if parsed.isoformat() != text:
        raise _fail(f"{label} is invalid")
    return text


def _optional_date(value: object, label: str) -> str | None:
    text = _optional_text(value, label, maximum=10)
    return None if text is None else _date_text(text, label)


def _accession(value: object, label: str) -> str:
    text = _text(value, label, maximum=20)
    if _ACCESSION.fullmatch(text) is None:
        raise _fail(f"{label} is invalid")
    return text


def _capture_time(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail("capture time is invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _acceptance_or_filed(
    acceptance: object,
    *,
    filed_at: str,
    label: str,
) -> TemporalValue:
    text = _optional_text(acceptance, label, maximum=64)
    if text is None:
        return TemporalValue.parse(filed_at, pointer=f"/{label}")
    try:
        parsed = TemporalValue.parse(text, pointer=f"/{label}")
    except ValidationError as exc:
        raise _fail(f"{label} is invalid") from exc
    if parsed.precision is not TemporalPrecision.DATETIME:
        raise _fail(f"{label} is invalid")
    return parsed


def _value_text(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise _fail(f"{label} is invalid")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise _fail(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise _fail(f"{label} is invalid")
    normalized = parsed.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _valid_frame(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 128 or value.strip() != value:
        return None
    return value if _SEC_FRAME.fullmatch(value) is not None else None


def _is_segmented(value: Mapping[str, Any]) -> bool:
    for field in ("segment", "segments", "dimensions"):
        candidate = value.get(field)
        if candidate not in (None, {}, []):
            return True
    return False


def _archive_url(cik: str, accession: str, primary_document: str | None) -> str:
    accession_path = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/"
    if primary_document is None:
        return base
    if (
        "\\" in primary_document
        or "//" in primary_document
        or primary_document.startswith("/")
        or any(part in {"", ".", ".."} for part in primary_document.split("/"))
        or re.fullmatch(r"[A-Za-z0-9._/-]+", primary_document) is None
    ):
        raise _fail("primary document is invalid")
    return base + primary_document


@dataclass(frozen=True, slots=True)
class SecCoreMapping:
    metric_code: str
    display_name: str
    taxonomy: str
    concept: str
    unit: str
    share_semantics: str


SEC_CORE_MAPPINGS: Final = (
    SecCoreMapping(
        "revenue",
        "Revenue",
        "us-gaap",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "USD",
        "not_share",
    ),
    SecCoreMapping(
        "net_income",
        "Net income",
        "us-gaap",
        "NetIncomeLoss",
        "USD",
        "not_share",
    ),
    SecCoreMapping("total_assets", "Total assets", "us-gaap", "Assets", "USD", "not_share"),
    SecCoreMapping(
        "total_liabilities", "Total liabilities", "us-gaap", "Liabilities", "USD", "not_share"
    ),
    SecCoreMapping(
        "operating_cash_flow",
        "Operating cash flow",
        "us-gaap",
        "NetCashProvidedByUsedInOperatingActivities",
        "USD",
        "not_share",
    ),
    SecCoreMapping(
        "shares_outstanding",
        "Shares outstanding",
        "dei",
        "EntityCommonStockSharesOutstanding",
        "shares",
        "instant",
    ),
    SecCoreMapping(
        "weighted_average_shares_basic",
        "Weighted-average basic shares",
        "us-gaap",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "shares",
        "weighted_average",
    ),
    SecCoreMapping(
        "weighted_average_shares_diluted",
        "Weighted-average diluted shares",
        "us-gaap",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "shares",
        "weighted_average",
    ),
    SecCoreMapping(
        "earnings_per_share_basic",
        "Basic earnings per share",
        "us-gaap",
        "EarningsPerShareBasic",
        "USD/shares",
        "not_share",
    ),
    SecCoreMapping(
        "earnings_per_share_diluted",
        "Diluted earnings per share",
        "us-gaap",
        "EarningsPerShareDiluted",
        "USD/shares",
        "not_share",
    ),
)
_MAPPING_BY_SOURCE: Final = {
    (mapping.taxonomy, mapping.concept, mapping.unit): mapping for mapping in SEC_CORE_MAPPINGS
}


@dataclass(frozen=True, slots=True)
class SecAaplFiling:
    accession_number: str
    form_type: str
    filing_date: str
    accepted_at: TemporalValue
    report_period_end: str | None
    primary_document: str | None
    source_url: str
    source_row: int
    origin: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "accession_number": self.accession_number,
            "accepted_at": self.accepted_at.raw,
            "accepted_precision": self.accepted_at.precision.value,
            "filing_date": self.filing_date,
            "form_type": self.form_type,
            "origin": self.origin,
            "primary_document": self.primary_document,
            "report_period_end": self.report_period_end,
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class SecAaplCoreFact:
    mapping: SecCoreMapping
    accession_number: str
    form_type: str
    filed_at: str
    fiscal_year: int
    fiscal_period: str
    period_start: str | None
    period_end: str
    value_text: str
    available_at: TemporalValue
    frame: str | None

    @property
    def period_kind(self) -> str:
        return "instant" if self.period_start is None else "duration"

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "accession_number": self.accession_number,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "concept": self.mapping.concept,
            "filed_at": self.filed_at,
            "fiscal_period": self.fiscal_period,
            "fiscal_year": self.fiscal_year,
            "form_type": self.form_type,
            "period_end": self.period_end,
            "period_start": self.period_start,
            "taxonomy": self.mapping.taxonomy,
            "unit": self.mapping.unit,
            "value_text": self.value_text,
        }


@dataclass(frozen=True, slots=True)
class ParsedSecAaplBundle:
    issuer_name: str
    entity_type: str | None
    filings: tuple[SecAaplFiling, ...]
    facts: tuple[SecAaplCoreFact, ...]
    scope: Mapping[str, object]
    captured_at: str
    semantic_identity: str
    submissions_sha256: str
    submissions_byte_count: int
    companyfacts_sha256: str
    companyfacts_byte_count: int
    submissions_filing_count: int
    source_core_fact_candidate_count: int
    excluded_unsupported_core_row_count: int
    excluded_context_variant_count: int
    cik: str = AAPL_CIK
    collector_id: str = SEC_AAPL_COMPANYFACTS_COLLECTOR_ID
    normalization_version: str = SEC_AAPL_COMPANYFACTS_NORMALIZATION_VERSION
    legacy_aapl: bool = True

    @property
    def fetched_count(self) -> int:
        return self.submissions_filing_count + self.source_core_fact_candidate_count


# The original AAPL name remains public.  The generic collector uses the same
# fully parsed representation, with its own collector/profile metadata.
ParsedSecCompanyFactsBundle = ParsedSecAaplBundle


def _submissions_filings(
    payload: Mapping[str, Any],
    *,
    cik: str = AAPL_CIK,
    maximum_filings: int = MAX_SUBMISSION_FILINGS,
) -> tuple[str, str | None, list[SecAaplFiling]]:
    if _cik(payload.get("cik"), "submissions CIK") != cik:
        raise _fail("submissions CIK is outside the requested issuer scope")
    name = _text(payload.get("name"), "submissions issuer name", maximum=512)
    entity_type = _optional_text(payload.get("entityType"), "submissions entity type", maximum=128)
    filings = _mapping(payload.get("filings"), "submissions filings")
    recent = _mapping(filings.get("recent"), "submissions recent filings")
    fields = {
        field: _array(
            recent.get(field),
            f"submissions recent {field}",
            maximum=maximum_filings,
            allow_empty=True,
        )
        for field in (
            "accessionNumber",
            "filingDate",
            "acceptanceDateTime",
            "form",
            "reportDate",
            "primaryDocument",
        )
    }
    lengths = {len(entries) for entries in fields.values()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise _fail("submissions recent filings are invalid")
    parsed: list[SecAaplFiling] = []
    accessions: set[str] = set()
    for index in range(next(iter(lengths))):
        accession = _accession(fields["accessionNumber"][index], f"submissions accession {index}")
        if accession in accessions:
            raise _fail("submissions accessions are duplicated")
        accessions.add(accession)
        filing_date = _date_text(fields["filingDate"][index], f"submissions filing date {index}")
        primary_document = _optional_text(
            fields["primaryDocument"][index], f"submissions primary document {index}", maximum=256
        )
        parsed.append(
            SecAaplFiling(
                accession_number=accession,
                form_type=_text(fields["form"][index], f"submissions form {index}", maximum=64),
                filing_date=filing_date,
                accepted_at=_acceptance_or_filed(
                    fields["acceptanceDateTime"][index],
                    filed_at=filing_date,
                    label=f"submissions acceptance date {index}",
                ),
                report_period_end=_optional_date(fields["reportDate"][index], f"submissions report date {index}"),
                primary_document=primary_document,
                source_url=_archive_url(cik, accession, primary_document),
                source_row=index + 1,
                origin="submissions_recent",
            )
        )
    return name, entity_type, parsed


def _fact_candidate(
    value: Mapping[str, Any],
    *,
    mapping: SecCoreMapping,
    label: str,
) -> SecAaplCoreFact:
    accession = _accession(value.get("accn"), f"{label} accession")
    filed_at = _date_text(value.get("filed"), f"{label} filed date")
    fiscal_year = value.get("fy")
    if isinstance(fiscal_year, bool) or not isinstance(fiscal_year, int) or not 1900 <= fiscal_year <= 9999:
        raise _fail(f"{label} fiscal year is invalid")
    fiscal_period = _text(value.get("fp"), f"{label} fiscal period", maximum=16)
    form_type = _text(value.get("form"), f"{label} form", maximum=64)
    period_end = _date_text(value.get("end"), f"{label} end date")
    raw_start = _optional_text(value.get("start"), f"{label} start date", maximum=10)
    period_start = None if raw_start is None else _date_text(raw_start, f"{label} start date")
    if period_start is not None and period_start > period_end:
        raise _fail(f"{label} period is invalid")
    return SecAaplCoreFact(
        mapping=mapping,
        accession_number=accession,
        form_type=form_type,
        filed_at=filed_at,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_start=period_start,
        period_end=period_end,
        value_text=_value_text(value.get("val"), f"{label} value"),
        available_at=TemporalValue.parse(filed_at, pointer=f"/{label}/filed"),
        frame=_valid_frame(value.get("frame")),
    )


def _is_supported_core_row(value: Mapping[str, Any]) -> bool:
    """Keep the v1 core map to periodic rows with usable fiscal context.

    SEC CompanyFacts legitimately includes facts from other forms and can omit
    ``fy`` or ``fp``.  Those rows cannot be represented as a dependable
    periodic fundamental in this bounded v1 path, so they are deliberately
    excluded before strict validation of eligible rows.
    """

    form_type = value.get("form")
    fiscal_year = value.get("fy")
    fiscal_period = value.get("fp")
    if form_type is None or fiscal_year is None or fiscal_period is None:
        return False
    if not isinstance(form_type, str) or not isinstance(fiscal_period, str):
        return False
    normalized_form = form_type.strip()
    normalized_period = fiscal_period.strip()
    if normalized_form in _SUPPORTED_PERIODIC_FORMS:
        return normalized_period in _SUPPORTED_FISCAL_PERIODS
    # Foreign private issuers and Canadian foreign issuers report annual
    # CompanyFacts on these forms.  Do not invent quarterly coverage for them.
    return normalized_form in _SUPPORTED_ANNUAL_FORMS and normalized_period == "FY"


def _duration_days(fact: SecAaplCoreFact) -> int:
    if fact.period_start is None:
        return 0
    return (date.fromisoformat(fact.period_end) - date.fromisoformat(fact.period_start)).days


def _same_candidate_value(left: SecAaplCoreFact, right: SecAaplCoreFact) -> bool:
    return (
        left.accession_number,
        left.form_type,
        left.filed_at,
        left.fiscal_year,
        left.fiscal_period,
        left.period_start,
        left.period_end,
        left.value_text,
    ) == (
        right.accession_number,
        right.form_type,
        right.filed_at,
        right.fiscal_year,
        right.fiscal_period,
        right.period_start,
        right.period_end,
        right.value_text,
    )


def _select_context(candidates: Sequence[SecAaplCoreFact]) -> SecAaplCoreFact:
    """Select one schema-representable CompanyFacts context.

    Frames are the preferred SEC supplied context marker.  At an equal frame
    priority, an FY duration keeps its annual/longest interval; all other
    duration facts keep the shortest interval (the quarter instead of YTD).
    A remaining equal-priority difference has no safe representation in the
    existing company fact key and therefore rejects the bundle before write.
    """

    if not candidates:  # pragma: no cover - callers always provide a group
        raise _fail("context candidates are invalid")
    framed = [item for item in candidates if item.frame is not None]
    pool = framed if framed else list(candidates)
    fy_durations = [
        item for item in pool if item.fiscal_period == "FY" and item.period_start is not None
    ]
    if fy_durations:
        longest = max(_duration_days(item) for item in fy_durations)
        finalists = [item for item in fy_durations if _duration_days(item) == longest]
    else:
        durations = [item for item in pool if item.period_start is not None]
        if durations:
            shortest = min(_duration_days(item) for item in durations)
            finalists = [item for item in durations if _duration_days(item) == shortest]
        else:
            finalists = list(pool)
    baseline = finalists[0]
    if any(not _same_candidate_value(baseline, item) for item in finalists[1:]):
        raise _fail("CompanyFacts context variants conflict at equal priority")
    return min(finalists, key=lambda item: item.frame or "")


def _companyfacts_candidates(
    payload: Mapping[str, Any],
    *,
    cik: str = AAPL_CIK,
    maximum_candidates: int = MAX_CORE_FACT_CANDIDATES,
) -> tuple[list[SecAaplCoreFact], int, int]:
    if _cik(payload.get("cik"), "CompanyFacts CIK") != cik:
        raise _fail("CompanyFacts CIK is outside the requested issuer scope")
    _optional_text(payload.get("entityName"), "CompanyFacts entity name", maximum=512)
    facts = _mapping(payload.get("facts"), "CompanyFacts facts")
    candidates: list[SecAaplCoreFact] = []
    source_core_rows = 0
    excluded_unsupported_rows = 0
    for taxonomy, taxonomy_value in facts.items():
        if taxonomy not in _STANDARD_TAXONOMIES:
            continue
        concepts = _mapping(taxonomy_value, f"CompanyFacts {taxonomy}")
        for concept, concept_value in concepts.items():
            if not isinstance(concept, str) or not concept or len(concept) > 256:
                raise _fail("CompanyFacts concept is invalid")
            definition = _mapping(concept_value, f"CompanyFacts {taxonomy} {concept}")
            units = _mapping(definition.get("units"), f"CompanyFacts {taxonomy} {concept} units")
            for unit, unit_value in units.items():
                if not isinstance(unit, str) or not unit or len(unit) > 128:
                    raise _fail("CompanyFacts unit is invalid")
                mapping = _MAPPING_BY_SOURCE.get((taxonomy, concept, unit))
                if mapping is None:
                    continue
                rows = _array(
                    unit_value,
                    f"CompanyFacts {taxonomy} {concept} {unit}",
                    maximum=maximum_candidates,
                    allow_empty=True,
                )
                for row_index, raw in enumerate(rows):
                    row = _mapping(raw, f"CompanyFacts {taxonomy} {concept} {unit} row {row_index}")
                    if _is_segmented(row):
                        continue
                    source_core_rows += 1
                    if source_core_rows > maximum_candidates:
                        raise ResourceLimitError(
                            "SEC CompanyFacts core fact count exceeds its bound"
                        )
                    if not _is_supported_core_row(row):
                        excluded_unsupported_rows += 1
                        continue
                    candidates.append(
                        _fact_candidate(
                            row,
                            mapping=mapping,
                            label=f"CompanyFacts {taxonomy} {concept} {unit} row {row_index}",
                        )
                    )
    if not candidates:
        raise _fail("CompanyFacts contains no unsegmented sec-core-v2 facts")
    return candidates, source_core_rows, excluded_unsupported_rows


def _select_core_facts(candidates: Sequence[SecAaplCoreFact]) -> tuple[tuple[SecAaplCoreFact, ...], int]:
    grouped: dict[tuple[str, str, str, str, str], list[SecAaplCoreFact]] = {}
    for candidate in candidates:
        key = (
            candidate.mapping.taxonomy,
            candidate.mapping.concept,
            candidate.mapping.unit,
            candidate.accession_number,
            candidate.period_end,
        )
        grouped.setdefault(key, []).append(candidate)
    selected: list[SecAaplCoreFact] = []
    excluded = 0
    for group in grouped.values():
        selected.append(_select_context(group))
        excluded += len(group) - 1
    selected.sort(
        key=lambda item: (
            item.mapping.taxonomy,
            item.mapping.concept,
            item.mapping.unit,
            item.period_end,
            item.accession_number,
            item.period_start or "",
            item.value_text,
        )
    )
    return tuple(selected), excluded


def _fallback_filings(
    submissions: Sequence[SecAaplFiling],
    facts: Sequence[SecAaplCoreFact],
    *,
    cik: str = AAPL_CIK,
) -> tuple[SecAaplFiling, ...]:
    known = {filing.accession_number: filing for filing in submissions}
    fallback: dict[str, SecAaplFiling] = {}
    for fact in facts:
        filing = known.get(fact.accession_number)
        if filing is not None:
            # SEC's two official endpoints can disagree on filing date/form for
            # one accession (notably around amendments). Retain each endpoint's
            # fields in its own relation; availability is reconciled below.
            continue
        candidate = SecAaplFiling(
            accession_number=fact.accession_number,
            form_type=fact.form_type,
            filing_date=fact.filed_at,
            accepted_at=TemporalValue.parse(fact.filed_at, pointer="/CompanyFacts/filed"),
            # CompanyFacts fact endpoints do not establish a filing-level report
            # period.  Do not infer one from an individual fact context.
            report_period_end=None,
            primary_document=None,
            source_url=_archive_url(cik, fact.accession_number, None),
            source_row=0,
            origin="companyfacts_fallback",
        )
        prior = fallback.get(candidate.accession_number)
        if prior is not None and (
            prior.form_type,
            prior.filing_date,
            prior.report_period_end,
        ) != (
            candidate.form_type,
            candidate.filing_date,
            candidate.report_period_end,
        ):
            raise _fail("CompanyFacts fallback filing metadata conflicts")
        fallback[candidate.accession_number] = candidate
    start = len(submissions)
    return tuple(
        SecAaplFiling(
            accession_number=item.accession_number,
            form_type=item.form_type,
            filing_date=item.filing_date,
            accepted_at=item.accepted_at,
            report_period_end=item.report_period_end,
            primary_document=item.primary_document,
            source_url=item.source_url,
            source_row=start + ordinal,
            origin=item.origin,
        )
        for ordinal, item in enumerate(
            sorted(fallback.values(), key=lambda candidate: candidate.accession_number), start=1
        )
    )


def _with_fact_availability(
    facts: Sequence[SecAaplCoreFact],
    filings: Sequence[SecAaplFiling],
) -> tuple[SecAaplCoreFact, ...]:
    filing_by_accession = {filing.accession_number: filing for filing in filings}
    resolved: list[SecAaplCoreFact] = []
    for fact in facts:
        filing = filing_by_accession.get(fact.accession_number)
        if filing is None:  # pragma: no cover - fallback construction protects this
            raise _fail("CompanyFacts fact has no filing lineage")
        available_at = filing.accepted_at
        if filing.filing_date != fact.filed_at:
            available_at = TemporalValue.parse(
                max(filing.filing_date, fact.filed_at),
                pointer="/CompanyFacts/filed",
            )
        resolved.append(
            SecAaplCoreFact(
                mapping=fact.mapping,
                accession_number=fact.accession_number,
                form_type=fact.form_type,
                filed_at=fact.filed_at,
                fiscal_year=fact.fiscal_year,
                fiscal_period=fact.fiscal_period,
                period_start=fact.period_start,
                period_end=fact.period_end,
                value_text=fact.value_text,
                available_at=available_at,
                frame=fact.frame,
            )
        )
    return tuple(resolved)


def _parse_sec_companyfacts_bundle(
    *,
    cik: str,
    submissions_body: bytes,
    companyfacts_body: bytes,
    captured_at: datetime,
    collector_id: str,
    normalization_version: str,
    maximum_total_bytes: int,
    maximum_submissions_bytes: int,
    maximum_companyfacts_bytes: int,
    maximum_submission_filings: int,
    maximum_core_fact_candidates: int,
    maximum_source_rows: int,
    legacy_aapl: bool,
) -> ParsedSecAaplBundle:
    """Parse one issuer's pre-fetched SEC endpoint bundle without a store."""

    if not isinstance(submissions_body, bytes) or not submissions_body:
        raise _fail("submissions body is invalid")
    if not isinstance(companyfacts_body, bytes) or not companyfacts_body:
        raise _fail("CompanyFacts body is invalid")
    prefix = "SEC AAPL" if legacy_aapl else "SEC"
    if len(submissions_body) > maximum_submissions_bytes:
        raise ResourceLimitError(f"{prefix} submissions body exceeds its byte bound")
    if len(companyfacts_body) > maximum_companyfacts_bytes:
        raise ResourceLimitError(f"{prefix} CompanyFacts body exceeds its byte bound")
    if len(submissions_body) + len(companyfacts_body) > maximum_total_bytes:
        raise ResourceLimitError(f"{prefix} response bundle exceeds its total byte bound")
    captured = _capture_time(captured_at)
    submissions_payload = _mapping(
        loads_strict(submissions_body, max_bytes=maximum_submissions_bytes), "submissions body"
    )
    companyfacts_payload = _mapping(
        loads_strict(companyfacts_body, max_bytes=maximum_companyfacts_bytes), "CompanyFacts body"
    )
    issuer_name, entity_type, submissions = _submissions_filings(
        submissions_payload,
        cik=cik,
        maximum_filings=maximum_submission_filings,
    )
    candidates, candidate_count, excluded_unsupported = _companyfacts_candidates(
        companyfacts_payload,
        cik=cik,
        maximum_candidates=maximum_core_fact_candidates,
    )
    selected, excluded = _select_core_facts(candidates)
    if len(selected) > maximum_source_rows:
        raise ResourceLimitError(f"{prefix} selected core facts exceed the collector bound")
    # The legacy pilot treated submission rows and CompanyFacts candidates as
    # one 10k envelope.  Preserve that exact guard for its replay contract;
    # generic calls instead allow the stated 50k source-core rows plus the
    # separately bounded one-issuer submissions manifest.
    if legacy_aapl and len(submissions) + candidate_count > maximum_source_rows:
        raise ResourceLimitError("SEC AAPL source rows exceed the collector bound")
    if not legacy_aapl and candidate_count > maximum_source_rows:
        raise ResourceLimitError("SEC CompanyFacts source-core rows exceed the collector bound")
    fallbacks = _fallback_filings(submissions, selected, cik=cik)
    filings = tuple(sorted((*submissions, *fallbacks), key=lambda item: item.accession_number))
    facts = _with_fact_availability(selected, filings)
    scope: dict[str, object] = {
        "cik": cik,
        "companyfacts_scope": "sec_core_v2_unsegmented_standard_taxonomy",
        "completeness": "complete_for_fixed_two_endpoint_bundle",
        "context_selection_policy": "valid_frame_then_fy_longest_else_shortest_duration",
        "core_row_eligibility_policy": "periodic_forms_with_concrete_fy_fp_only",
        "excluded_unsupported_core_row_count": excluded_unsupported,
        "filing_scope": "submissions_recent_plus_companyfacts_accession_fallback",
        "mapping_version": SEC_CORE_MAPPING_VERSION,
        "standard_taxonomies": sorted(_STANDARD_TAXONOMIES),
    }
    semantic_material = {
        "collector": collector_id,
        "normalization_version": normalization_version,
        "scope": scope,
        "issuer": {"cik": cik, "entity_type": entity_type, "legal_name": issuer_name},
        "filings": [item.semantic_mapping() for item in filings],
        "facts": [item.semantic_mapping() for item in facts],
    }
    semantic_identity = hashlib.sha256(
        dumps_strict(semantic_material, max_bytes=_MAX_SEMANTIC_BYTES).encode("utf-8")
    ).hexdigest()
    return ParsedSecAaplBundle(
        issuer_name=issuer_name,
        entity_type=entity_type,
        filings=filings,
        facts=facts,
        scope=scope,
        captured_at=captured,
        semantic_identity=semantic_identity,
        submissions_sha256=hashlib.sha256(submissions_body).hexdigest(),
        submissions_byte_count=len(submissions_body),
        companyfacts_sha256=hashlib.sha256(companyfacts_body).hexdigest(),
        companyfacts_byte_count=len(companyfacts_body),
        submissions_filing_count=len(submissions),
        source_core_fact_candidate_count=candidate_count,
        excluded_unsupported_core_row_count=excluded_unsupported,
        excluded_context_variant_count=excluded,
        cik=cik,
        collector_id=collector_id,
        normalization_version=normalization_version,
        legacy_aapl=legacy_aapl,
    )


def parse_sec_aapl_bundle(
    *,
    submissions_body: bytes,
    companyfacts_body: bytes,
    captured_at: datetime,
) -> ParsedSecAaplBundle:
    """Parse the two fixed AAPL SEC endpoint bodies without opening a store."""

    return _parse_sec_companyfacts_bundle(
        cik=AAPL_CIK,
        submissions_body=submissions_body,
        companyfacts_body=companyfacts_body,
        captured_at=captured_at,
        collector_id=SEC_AAPL_COMPANYFACTS_COLLECTOR_ID,
        normalization_version=SEC_AAPL_COMPANYFACTS_NORMALIZATION_VERSION,
        maximum_total_bytes=MAX_TOTAL_RESPONSE_BYTES,
        maximum_submissions_bytes=MAX_SUBMISSIONS_BYTES,
        maximum_companyfacts_bytes=MAX_COMPANYFACTS_BYTES,
        maximum_submission_filings=MAX_SUBMISSION_FILINGS,
        maximum_core_fact_candidates=MAX_CORE_FACT_CANDIDATES,
        maximum_source_rows=MAX_SOURCE_ROWS,
        legacy_aapl=True,
    )


def parse_sec_companyfacts_bundle(
    *,
    cik: object,
    submissions_body: bytes,
    companyfacts_body: bytes,
    captured_at: datetime,
) -> ParsedSecCompanyFactsBundle:
    """Parse one validated SEC issuer bundle without opening a store.

    The AAPL alias intentionally keeps the original semantic/source identity,
    so a later generic caller cannot duplicate the completed pilot capture.
    """

    normalized_cik = _cik(cik, "requested CIK")
    if normalized_cik == AAPL_CIK:
        return parse_sec_aapl_bundle(
            submissions_body=submissions_body,
            companyfacts_body=companyfacts_body,
            captured_at=captured_at,
        )
    return _parse_sec_companyfacts_bundle(
        cik=normalized_cik,
        submissions_body=submissions_body,
        companyfacts_body=companyfacts_body,
        captured_at=captured_at,
        collector_id=SEC_COMPANYFACTS_COLLECTOR_ID,
        normalization_version=SEC_COMPANYFACTS_NORMALIZATION_VERSION,
        maximum_total_bytes=MAX_GENERIC_TOTAL_RESPONSE_BYTES,
        maximum_submissions_bytes=MAX_GENERIC_SUBMISSIONS_BYTES,
        maximum_companyfacts_bytes=MAX_GENERIC_COMPANYFACTS_BYTES,
        maximum_submission_filings=MAX_GENERIC_SUBMISSION_FILINGS,
        maximum_core_fact_candidates=MAX_GENERIC_CORE_FACT_CANDIDATES,
        maximum_source_rows=MAX_GENERIC_SOURCE_ROWS,
        legacy_aapl=False,
    )


def _same(row: sqlite3.Row, **expected: object) -> bool:
    return all(row[key] == value for key, value in expected.items())


def _issuer_id(cik: str = AAPL_CIK) -> str:
    return stable_id("company_issuer", cik)


def _metric_id(mapping: SecCoreMapping) -> str:
    return stable_id("company_metric", mapping.metric_code)


def _mapping_id(mapping: SecCoreMapping) -> str:
    return stable_id(
        "company_metric_mapping",
        _metric_id(mapping),
        SEC_CORE_MAPPING_VERSION,
        mapping.taxonomy,
        mapping.concept,
        mapping.unit,
    )


def _domain_scope(parsed: ParsedSecAaplBundle, *, endpoint: str) -> dict[str, object]:
    common: dict[str, object] = {
        "cik": parsed.cik,
        "endpoint": endpoint,
        "normalization_version": parsed.normalization_version,
    }
    if endpoint == "submissions":
        membership_material = [
            {**filing.semantic_mapping(), "source_row": filing.source_row}
            for filing in parsed.filings
        ]
        return {
            **common,
            "filing_membership_identity": hashlib.sha256(
                dumps_strict(
                    membership_material,
                    max_bytes=_MAX_SEMANTIC_BYTES,
                ).encode("utf-8")
            ).hexdigest(),
            "filing_scope": parsed.scope["filing_scope"],
        }
    if endpoint != "companyfacts":  # pragma: no cover - internal invariant
        raise _fail("source endpoint is invalid")
    return {
        **common,
        "companyfacts_scope": parsed.scope["companyfacts_scope"],
        "context_selection_policy": parsed.scope["context_selection_policy"],
        "core_row_eligibility_policy": parsed.scope["core_row_eligibility_policy"],
        "excluded_unsupported_core_row_count": parsed.scope[
            "excluded_unsupported_core_row_count"
        ],
        "mapping_version": parsed.scope["mapping_version"],
        "standard_taxonomies": parsed.scope["standard_taxonomies"],
    }


def _source_reference(parsed: ParsedSecAaplBundle, *, endpoint: str) -> str:
    if parsed.legacy_aapl:
        return f"sec/edgar/aapl/{endpoint}.json"
    return f"sec/edgar/{parsed.cik}/{endpoint}.json"


def _profile_id(parsed: ParsedSecAaplBundle, *, legacy: str, generic: str) -> str:
    return legacy if parsed.legacy_aapl else generic


def _ensure_issuer(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    run_id: str,
) -> int:
    issuer_id = _issuer_id(parsed.cik)
    existing = connection.execute(
        "SELECT issuer_id, cik FROM company_issuers WHERE issuer_id=? OR cik=?",
        (issuer_id, parsed.cik),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO company_issuers (issuer_id, cik, created_run_id) VALUES (?, ?, ?)",
            (issuer_id, parsed.cik, run_id),
        )
        return 1
    if str(existing["issuer_id"]) != issuer_id or str(existing["cik"]) != parsed.cik:
        raise ConflictError("SEC CIK identity conflicts with immutable prior state")
    return 0


def _insert_domain_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    endpoint: str,
    content_sha256: str,
    byte_count: int,
    run_id: str,
) -> tuple[str, int]:
    scope = _domain_scope(parsed, endpoint=endpoint)
    scope_json = dumps_strict(scope)
    artifact_id = stable_id(
        _profile_id(
            parsed,
            legacy="sec_aapl_domain_artifact",
            generic="sec_companyfacts_domain_artifact",
        ),
        endpoint,
        content_sha256,
        scope_json,
    )
    expected = {
        "source_name": f"sec_edgar_{endpoint}",
        "content_sha256": content_sha256,
        "media_type": "application/json",
        "byte_count": byte_count,
        "source_reference": _source_reference(parsed, endpoint=endpoint),
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT source_name, content_sha256, media_type, byte_count, source_reference,
               request_scope_json, captured_at, captured_precision, available_at,
               available_precision
        FROM company_sec_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        if not _same(
            existing,
            **{
                key: value
                for key, value in expected.items()
                if key
                not in {
                    "captured_at",
                    "captured_precision",
                    "available_at",
                    "available_precision",
                }
            },
        ):
            raise ConflictError("SEC source artifact conflicts with immutable prior evidence")
        return artifact_id, 0
    connection.execute(
        """
        INSERT INTO company_sec_artifacts (
            artifact_id, source_name, content_sha256, media_type, byte_count,
            source_reference, request_scope_json, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            expected["source_name"],
            expected["content_sha256"],
            expected["media_type"],
            expected["byte_count"],
            expected["source_reference"],
            expected["request_scope_json"],
            expected["captured_at"],
            expected["captured_precision"],
            expected["available_at"],
            expected["available_precision"],
            run_id,
        ),
    )
    return artifact_id, 1


def _insert_domain_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    endpoint: str,
    artifact_id: str,
    run_id: str,
) -> tuple[str, int]:
    kind = "submissions" if endpoint == "submissions" else "companyfacts"
    scope = _domain_scope(parsed, endpoint=endpoint)
    semantic_identity = hashlib.sha256(
        dumps_strict(
            {
                "artifact_id": artifact_id,
                "issuer_id": _issuer_id(parsed.cik),
                "snapshot_kind": kind,
            }
        ).encode("utf-8")
    ).hexdigest()
    snapshot_id = stable_id(
        _profile_id(
            parsed,
            legacy="sec_aapl_domain_snapshot",
            generic="sec_companyfacts_domain_snapshot",
        ),
        semantic_identity,
    )
    expected = {
        "semantic_identity": semantic_identity,
        "issuer_id": _issuer_id(parsed.cik),
        "snapshot_kind": kind,
        "artifact_id": artifact_id,
        "scope_json": dumps_strict(scope),
        "completeness": "complete",
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, snapshot_kind, artifact_id, scope_json,
               completeness, captured_at, captured_precision, available_at,
               available_precision
        FROM company_sec_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    if existing is not None:
        if not _same(
            existing,
            **{
                key: value
                for key, value in expected.items()
                if key
                not in {
                    "captured_at",
                    "captured_precision",
                    "available_at",
                    "available_precision",
                }
            },
        ):
            raise ConflictError("SEC domain snapshot conflicts with immutable prior evidence")
        return snapshot_id, 0
    connection.execute(
        """
        INSERT INTO company_sec_snapshots (
            snapshot_id, semantic_identity, issuer_id, snapshot_kind, artifact_id,
            scope_json, completeness, captured_at, captured_precision, available_at,
            available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            expected["semantic_identity"],
            expected["issuer_id"],
            expected["snapshot_kind"],
            expected["artifact_id"],
            expected["scope_json"],
            expected["completeness"],
            expected["captured_at"],
            expected["captured_precision"],
            expected["available_at"],
            expected["available_precision"],
            run_id,
        ),
    )
    return snapshot_id, 1


def _ensure_issuer_version(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    submissions_snapshot_id: str,
    run_id: str,
) -> int:
    issuer_id = _issuer_id(parsed.cik)
    prior = connection.execute(
        """
        SELECT issuer_version_id, legal_name, entity_type, name_state, missing_reason,
               version_sequence
        FROM company_issuer_versions WHERE issuer_id=?
        ORDER BY version_sequence DESC LIMIT 1
        """,
        (issuer_id,),
    ).fetchone()
    expected = {
        "legal_name": parsed.issuer_name,
        "entity_type": parsed.entity_type,
        "name_state": "present",
        "missing_reason": None,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    version_id = stable_id(
        "company_issuer_version",
        issuer_id,
        str(sequence),
        parsed.issuer_name,
        parsed.entity_type or "",
    )
    connection.execute(
        """
        INSERT INTO company_issuer_versions (
            issuer_version_id, issuer_id, legal_name, entity_type, name_state,
            missing_reason, available_at, available_precision, version_sequence,
            supersedes_issuer_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, 'present', NULL, ?, 'datetime', ?, ?, ?, ?, 1)
        """,
        (
            version_id,
            issuer_id,
            parsed.issuer_name,
            parsed.entity_type,
            parsed.captured_at,
            sequence,
            None if prior is None else str(prior["issuer_version_id"]),
            submissions_snapshot_id,
            run_id,
        ),
    )
    return 1


def _insert_or_validate_filing(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    filing: SecAaplFiling,
    submissions_snapshot_id: str,
    run_id: str,
) -> int:
    expected = {
        "first_observed_issuer_id": _issuer_id(parsed.cik),
        "form_type": filing.form_type,
        "filing_date": filing.filing_date,
        "filing_date_precision": "date",
        "accepted_at": filing.accepted_at.raw,
        "accepted_precision": filing.accepted_at.precision.value,
        "report_period_start": None,
        "report_period_end": filing.report_period_end,
        "primary_document": filing.primary_document or "unavailable_from_companyfacts",
        "source_url": filing.source_url,
        "available_at": filing.accepted_at.raw,
        "available_precision": filing.accepted_at.precision.value,
    }
    existing = connection.execute(
        """
        SELECT first_observed_issuer_id, form_type, filing_date, filing_date_precision,
               accepted_at, accepted_precision, report_period_start, report_period_end,
               primary_document, source_url, available_at, available_precision
        FROM company_sec_filings WHERE accession_number=?
        """,
        (filing.accession_number,),
    ).fetchone()
    if existing is not None:
        # Accession numbers can represent joint filings. The first issuer,
        # archive URL, and conservative first-observed availability stay
        # immutable while a later issuer contributes its membership.
        timing_fields = {
            "accepted_at",
            "accepted_precision",
            "available_at",
            "available_precision",
        }
        shared_expected = {
            key: value
            for key, value in expected.items()
            if key
            not in {
                "first_observed_issuer_id",
                "source_url",
                *timing_fields,
            }
        }
        timing_matches = _same(
            existing,
            **{key: expected[key] for key in timing_fields},
        )
        if not _same(existing, **shared_expected):
            raise ConflictError("SEC filing accession conflicts with immutable prior metadata")
        if not timing_matches:
            stored_available = TemporalValue.parse(
                str(existing["available_at"]),
                pointer="/company_sec_filings/available_at",
            )
            if (
                stored_available.precision is not TemporalPrecision.DATETIME
                or filing.accepted_at.precision is not TemporalPrecision.DATETIME
                or existing["accepted_at"] != existing["available_at"]
                or existing["accepted_precision"] != existing["available_precision"]
                or stored_available.value < filing.accepted_at.value
                or any(
                    fact.accession_number == filing.accession_number
                    for fact in parsed.facts
                )
            ):
                raise ConflictError("SEC filing accession conflicts with immutable prior metadata")
        if parsed.legacy_aapl and not _same(existing, **expected):
            raise ConflictError("SEC AAPL filing accession conflicts with immutable prior metadata")
        return 0
    connection.execute(
        """
        INSERT INTO company_sec_filings (
            accession_number, first_observed_issuer_id, form_type, filing_date,
            filing_date_precision, accepted_at, accepted_precision, report_period_start,
            report_period_end, primary_document, source_url, first_observed_snapshot_id,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, 'date', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filing.accession_number,
            expected["first_observed_issuer_id"],
            expected["form_type"],
            expected["filing_date"],
            expected["accepted_at"],
            expected["accepted_precision"],
            expected["report_period_end"],
            expected["primary_document"],
            expected["source_url"],
            submissions_snapshot_id,
            expected["available_at"],
            expected["available_precision"],
            run_id,
        ),
    )
    return 1


def _insert_filing_membership(
    connection: sqlite3.Connection,
    *,
    submissions_snapshot_id: str,
    filing: SecAaplFiling,
    run_id: str,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_sec_filing_snapshot_membership
        WHERE snapshot_id=? AND accession_number=?
        """,
        (submissions_snapshot_id, filing.accession_number),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != filing.source_row:
            raise ConflictError("SEC filing membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT accession_number FROM company_sec_filing_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (submissions_snapshot_id, filing.source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("SEC filing source row conflicts with immutable prior membership")
    connection.execute(
        """
        INSERT INTO company_sec_filing_snapshot_membership (
            snapshot_id, accession_number, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (submissions_snapshot_id, filing.accession_number, run_id, filing.source_row),
    )
    return 1


def _ensure_mapping(
    connection: sqlite3.Connection,
    *,
    mapping: SecCoreMapping,
    captured_at: str,
) -> tuple[str, str, int]:
    metric_id = _metric_id(mapping)
    definition_expected = {
        "display_name": mapping.display_name,
        "base_unit": mapping.unit,
        "share_semantics": mapping.share_semantics,
        "definition_version": "stage4.company.metric.v1",
    }
    existing_definition = connection.execute(
        """
        SELECT display_name, base_unit, share_semantics, definition_version
        FROM company_metric_definitions WHERE metric_id=?
        """,
        (metric_id,),
    ).fetchone()
    written = 0
    if existing_definition is None:
        connection.execute(
            """
            INSERT INTO company_metric_definitions (
                metric_id, display_name, base_unit, share_semantics, definition_version,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metric_id,
                definition_expected["display_name"],
                definition_expected["base_unit"],
                definition_expected["share_semantics"],
                definition_expected["definition_version"],
                captured_at,
            ),
        )
        written += 1
    elif not _same(existing_definition, **definition_expected):
        raise ConflictError("SEC core metric definition conflicts with immutable prior state")
    mapping_id = _mapping_id(mapping)
    mapping_expected = {
        "metric_id": metric_id,
        "mapping_version": SEC_CORE_MAPPING_VERSION,
        "taxonomy": mapping.taxonomy,
        "concept": mapping.concept,
        "unit": mapping.unit,
        "mapping_state": "active",
    }
    existing_mapping = connection.execute(
        """
        SELECT metric_id, mapping_version, taxonomy, concept, unit, mapping_state
        FROM company_metric_mappings WHERE mapping_id=?
        """,
        (mapping_id,),
    ).fetchone()
    if existing_mapping is None:
        connection.execute(
            """
            INSERT INTO company_metric_mappings (
                mapping_id, metric_id, mapping_version, taxonomy, concept, unit,
                mapping_state, available_at, available_precision
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 'datetime')
            """,
            (
                mapping_id,
                metric_id,
                SEC_CORE_MAPPING_VERSION,
                mapping.taxonomy,
                mapping.concept,
                mapping.unit,
                captured_at,
            ),
        )
        written += 1
    elif not _same(existing_mapping, **mapping_expected):
        raise ConflictError("SEC core mapping conflicts with immutable prior state")
    return metric_id, mapping_id, written


def _append_fact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    fact: SecAaplCoreFact,
    companyfacts_snapshot_id: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    issuer_id = _issuer_id(parsed.cik)
    prior = connection.execute(
        """
        SELECT fact_version_id, version_sequence
        FROM company_sec_fact_versions
        WHERE issuer_id=? AND taxonomy=? AND concept=? AND unit=?
          AND reference_period_end=? AND accession_number=?
        ORDER BY version_sequence DESC LIMIT 1
        """,
        (
            issuer_id,
            fact.mapping.taxonomy,
            fact.mapping.concept,
            fact.mapping.unit,
            fact.period_end,
            fact.accession_number,
        ),
    ).fetchone()
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    fact_version_id = stable_id(
        _profile_id(
            parsed,
            legacy="sec_aapl_fact_version",
            generic="sec_companyfacts_fact_version",
        ),
        issuer_id,
        fact.mapping.taxonomy,
        fact.mapping.concept,
        fact.mapping.unit,
        fact.period_end,
        fact.accession_number,
        str(sequence),
        fact.value_text,
        fact.available_at.raw or "",
    )
    connection.execute(
        """
        INSERT INTO company_sec_fact_versions (
            fact_version_id, issuer_id, taxonomy, concept, unit, fiscal_year,
            fiscal_period, reference_period_start, reference_period_end, filed_at,
            accession_number, value_text, value_state, missing_reason, available_at,
            available_precision, version_sequence, supersedes_fact_version_id,
            source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_version_id,
            issuer_id,
            fact.mapping.taxonomy,
            fact.mapping.concept,
            fact.mapping.unit,
            fact.fiscal_year,
            fact.fiscal_period,
            fact.period_start,
            fact.period_end,
            fact.filed_at,
            fact.accession_number,
            fact.value_text,
            fact.available_at.raw,
            fact.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["fact_version_id"]),
            companyfacts_snapshot_id,
            run_id,
            source_row,
        ),
    )
    return fact_version_id, 1


def _insert_fact_membership(
    connection: sqlite3.Connection,
    *,
    companyfacts_snapshot_id: str,
    fact_version_id: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_sec_fact_snapshot_membership
        WHERE snapshot_id=? AND fact_version_id=?
        """,
        (companyfacts_snapshot_id, fact_version_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("SEC fact membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT fact_version_id FROM company_sec_fact_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (companyfacts_snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("SEC fact source row conflicts with immutable prior membership")
    connection.execute(
        """
        INSERT INTO company_sec_fact_snapshot_membership (
            snapshot_id, fact_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (companyfacts_snapshot_id, fact_version_id, run_id, source_row),
    )
    return 1


def _append_fundamental(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedSecAaplBundle,
    fact: SecAaplCoreFact,
    metric_id: str,
    mapping_id: str,
    fact_version_id: str,
    companyfacts_snapshot_id: str,
    run_id: str,
    source_row: int,
) -> int:
    issuer_id = _issuer_id(parsed.cik)
    prior = connection.execute(
        """
        SELECT fundamental_version_id, mapping_id, share_semantics, fiscal_year,
               fiscal_period, reference_period_start, accession_number,
               source_fact_version_id, value_text, value_state, missing_reason,
               available_at, available_precision, version_sequence
        FROM company_fundamental_observation_versions
        WHERE issuer_id=? AND metric_id=? AND reference_period_end=?
        ORDER BY version_sequence DESC LIMIT 1
        """,
        (issuer_id, metric_id, fact.period_end),
    ).fetchone()
    expected = {
        "mapping_id": mapping_id,
        "share_semantics": fact.mapping.share_semantics,
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "reference_period_start": fact.period_start,
        "accession_number": fact.accession_number,
        "source_fact_version_id": fact_version_id,
        "value_text": fact.value_text,
        "value_state": "present",
        "missing_reason": None,
        "available_at": fact.available_at.raw,
        "available_precision": fact.available_at.precision.value,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    fundamental_id = stable_id(
        _profile_id(
            parsed,
            legacy="sec_aapl_fundamental_version",
            generic="sec_companyfacts_fundamental_version",
        ),
        issuer_id,
        metric_id,
        fact.period_end,
        str(sequence),
        fact.value_text,
        fact.available_at.raw or "",
    )
    connection.execute(
        """
        INSERT INTO company_fundamental_observation_versions (
            fundamental_version_id, issuer_id, metric_id, mapping_id,
            share_semantics, fiscal_year, fiscal_period, reference_period_start,
            reference_period_end, accession_number, source_fact_version_id,
            value_text, value_state, missing_reason, available_at,
            available_precision, version_sequence,
            supersedes_fundamental_version_id, source_snapshot_id, run_id,
            source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fundamental_id,
            issuer_id,
            metric_id,
            mapping_id,
            fact.mapping.share_semantics,
            fact.fiscal_year,
            fact.fiscal_period,
            fact.period_start,
            fact.period_end,
            fact.accession_number,
            fact_version_id,
            fact.value_text,
            fact.available_at.raw,
            fact.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["fundamental_version_id"]),
            companyfacts_snapshot_id,
            run_id,
            source_row,
        ),
    )
    return 1


class SecCompanyFactsPublisher:
    """Publish one prepared issuer-scoped SEC bundle through company relations."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        legacy_aapl: bool = False,
    ) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise _fail("publisher dependencies are invalid")
        datasets = {item.id for item in registry.datasets_for(StoreRole.COMPANY.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise _fail("company SEC datasets are not registered")
        self._store_map = store_map
        self._legacy_aapl = legacy_aapl
        self._coordinator = IngestionCoordinator(
            store_map,
            code_version=(
                "sec_aapl_companyfacts.1.0.0"
                if legacy_aapl
                else "sec_companyfacts.1.1.0"
            ),
        )

    def publish(
        self,
        parsed: ParsedSecAaplBundle,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        if (
            not isinstance(parsed, ParsedSecAaplBundle)
            or parsed.legacy_aapl != self._legacy_aapl
        ):
            raise _fail("prepared bundle is invalid")
        run_id = stable_id(
            _profile_id(
                parsed,
                legacy="sec_aapl_companyfacts_run",
                generic="sec_companyfacts_run_v1_1",
            ),
            SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
            parsed.semantic_identity,
        )

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise _fail("coordinator supplied an unexpected run identity")
            written = _ensure_issuer(connection, parsed=parsed, run_id=active_run_id)
            submissions_artifact_id, appended = _insert_domain_artifact(
                connection,
                parsed=parsed,
                endpoint="submissions",
                content_sha256=parsed.submissions_sha256,
                byte_count=parsed.submissions_byte_count,
                run_id=active_run_id,
            )
            written += appended
            companyfacts_artifact_id, appended = _insert_domain_artifact(
                connection,
                parsed=parsed,
                endpoint="companyfacts",
                content_sha256=parsed.companyfacts_sha256,
                byte_count=parsed.companyfacts_byte_count,
                run_id=active_run_id,
            )
            written += appended
            submissions_snapshot_id, appended = _insert_domain_snapshot(
                connection,
                parsed=parsed,
                endpoint="submissions",
                artifact_id=submissions_artifact_id,
                run_id=active_run_id,
            )
            written += appended
            companyfacts_snapshot_id, companyfacts_snapshot_appended = _insert_domain_snapshot(
                connection,
                parsed=parsed,
                endpoint="companyfacts",
                artifact_id=companyfacts_artifact_id,
                run_id=active_run_id,
            )
            written += companyfacts_snapshot_appended
            written += _ensure_issuer_version(
                connection,
                parsed=parsed,
                submissions_snapshot_id=submissions_snapshot_id,
                run_id=active_run_id,
            )
            for filing in sorted(parsed.filings, key=lambda item: item.source_row):
                written += _insert_or_validate_filing(
                    connection,
                    parsed=parsed,
                    filing=filing,
                    submissions_snapshot_id=submissions_snapshot_id,
                    run_id=active_run_id,
                )
                written += _insert_filing_membership(
                    connection,
                    submissions_snapshot_id=submissions_snapshot_id,
                    filing=filing,
                    run_id=active_run_id,
                )
            resolved_mappings: dict[tuple[str, str, str], tuple[str, str]] = {}
            for mapping in SEC_CORE_MAPPINGS:
                metric_id, mapping_id, appended = _ensure_mapping(
                    connection, mapping=mapping, captured_at=parsed.captured_at
                )
                resolved_mappings[(mapping.taxonomy, mapping.concept, mapping.unit)] = (
                    metric_id,
                    mapping_id,
                )
                written += appended
            if companyfacts_snapshot_appended:
                for source_row, fact in enumerate(parsed.facts, start=1):
                    metric_id, mapping_id = resolved_mappings[
                        (fact.mapping.taxonomy, fact.mapping.concept, fact.mapping.unit)
                    ]
                    fact_version_id, appended = _append_fact(
                        connection,
                        parsed=parsed,
                        fact=fact,
                        companyfacts_snapshot_id=companyfacts_snapshot_id,
                        run_id=active_run_id,
                        source_row=source_row,
                    )
                    written += appended
                    written += _insert_fact_membership(
                        connection,
                        companyfacts_snapshot_id=companyfacts_snapshot_id,
                        fact_version_id=fact_version_id,
                        run_id=active_run_id,
                        source_row=source_row,
                    )
                    written += _append_fundamental(
                        connection,
                        parsed=parsed,
                        fact=fact,
                        metric_id=metric_id,
                        mapping_id=mapping_id,
                        fact_version_id=fact_version_id,
                        companyfacts_snapshot_id=companyfacts_snapshot_id,
                        run_id=active_run_id,
                        source_row=source_row,
                    )
            submissions_control_artifact_id = stable_id(
                _profile_id(
                    parsed,
                    legacy="sec_aapl_control_artifact",
                    generic="sec_companyfacts_control_artifact",
                ),
                "submissions",
                parsed.submissions_sha256,
                parsed.semantic_identity,
            )
            companyfacts_control_artifact_id = stable_id(
                _profile_id(
                    parsed,
                    legacy="sec_aapl_control_artifact",
                    generic="sec_companyfacts_control_artifact",
                ),
                "companyfacts",
                parsed.companyfacts_sha256,
                parsed.semantic_identity,
            )
            control_snapshot_id = stable_id(
                _profile_id(
                    parsed,
                    legacy="sec_aapl_control_snapshot",
                    generic="sec_companyfacts_control_snapshot",
                ),
                SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
                parsed.semantic_identity,
            )
            control_artifacts = (
                ArtifactWrite(
                    artifact_id=submissions_control_artifact_id,
                    dataset_id=SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID,
                    content_sha256=parsed.submissions_sha256,
                    media_type="application/json",
                    byte_count=parsed.submissions_byte_count,
                    source_reference=_source_reference(parsed, endpoint="submissions"),
                    request_scope=_domain_scope(parsed, endpoint="submissions"),
                    captured_at=parsed.captured_at,
                    captured_precision="datetime",
                    normalization_version=parsed.normalization_version,
                ),
                ArtifactWrite(
                    artifact_id=companyfacts_control_artifact_id,
                    dataset_id=SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID,
                    content_sha256=parsed.companyfacts_sha256,
                    media_type="application/json",
                    byte_count=parsed.companyfacts_byte_count,
                    source_reference=_source_reference(parsed, endpoint="companyfacts"),
                    request_scope=_domain_scope(parsed, endpoint="companyfacts"),
                    captured_at=parsed.captured_at,
                    captured_precision="datetime",
                    normalization_version=parsed.normalization_version,
                ),
            )
            warnings = (_CONTEXT_POLICY_WARNING,)
            if parsed.excluded_unsupported_core_row_count:
                warnings += (_CORE_ROW_POLICY_WARNING,)
            return WriteResult(
                written_count=written,
                artifacts=control_artifacts,
                snapshot=SnapshotWrite(
                    snapshot_id=control_snapshot_id,
                    dataset_id=SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
                    semantic_identity=parsed.semantic_identity,
                    scope={
                        "request_scope": dict(parsed.scope),
                        "source_core_fact_candidate_count": parsed.source_core_fact_candidate_count,
                        "excluded_unsupported_core_row_count": (
                            parsed.excluded_unsupported_core_row_count
                        ),
                        "excluded_context_variant_count": parsed.excluded_context_variant_count,
                    },
                    completeness="complete",
                    row_count=parsed.fetched_count,
                    captured_at=parsed.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(
                        submissions_control_artifact_id,
                        companyfacts_control_artifact_id,
                    ),
                    warnings=warnings,
                ),
                quality_results=(
                    QualityWrite(
                        quality_result_id=stable_id(
                            _profile_id(
                                parsed,
                                legacy="sec_aapl_companyfacts_quality",
                                generic="sec_companyfacts_quality",
                            ),
                            active_run_id,
                            SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
                        ),
                        dataset_id=SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
                        rule_id="sec_core_v2_context_selector",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=control_snapshot_id,
                        artifact_id=companyfacts_control_artifact_id,
                        snapshot_id=control_snapshot_id,
                        observed={
                            "excluded_unsupported_core_row_count": (
                                parsed.excluded_unsupported_core_row_count
                            ),
                            "excluded_context_variant_count": parsed.excluded_context_variant_count,
                            "filings": len(parsed.filings),
                            "selected_facts": len(parsed.facts),
                            "source_core_fact_candidate_count": parsed.source_core_fact_candidate_count,
                        },
                    ),
                ),
                warnings=warnings,
            )

        return self._coordinator.execute(
            role=StoreRole.COMPANY,
            dataset_id=SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
            output_dataset_ids=(
                SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID,
                SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID,
                SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID,
                SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
                SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID,
            ),
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=parsed.collector_id,
            scope={"request_scope": dict(parsed.scope)},
            started_at=parsed.captured_at,
            completed_at=parsed.captured_at,
            fetched_count=parsed.fetched_count,
            writer=writer,
            held_locks=held_locks,
        )


class SecAaplCompanyFactsPublisher(SecCompanyFactsPublisher):
    """Compatibility publisher for the completed fixed-AAPL pilot."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        super().__init__(store_map, registry, legacy_aapl=True)


def publish_sec_aapl_bundle(
    *,
    stores: StoreMap,
    registry: Registry,
    parsed: ParsedSecAaplBundle,
    held_locks: HeldWriteLocks | None = None,
) -> IngestionReceipt:
    """Publish a previously parsed fixed AAPL SEC bundle."""

    return SecAaplCompanyFactsPublisher(stores, registry).publish(
        parsed, held_locks=held_locks
    )


def publish_sec_companyfacts_bundle(
    *,
    stores: StoreMap,
    registry: Registry,
    parsed: ParsedSecCompanyFactsBundle,
    held_locks: HeldWriteLocks | None = None,
) -> IngestionReceipt:
    """Publish a previously parsed single-issuer SEC CompanyFacts bundle."""

    if not isinstance(parsed, ParsedSecAaplBundle):
        raise _fail("prepared bundle is invalid")
    if parsed.legacy_aapl:
        return publish_sec_aapl_bundle(
            stores=stores,
            registry=registry,
            parsed=parsed,
            held_locks=held_locks,
        )
    return SecCompanyFactsPublisher(stores, registry).publish(
        parsed, held_locks=held_locks
    )


def _validate_prewrite_deadline(deadline: float | None, *, legacy_aapl: bool) -> None:
    if deadline is None:
        return
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or deadline <= 0
    ):
        raise _fail("pre-write deadline is invalid")
    if monotonic() > deadline:
        prefix = "SEC AAPL" if legacy_aapl else "SEC"
        raise ResourceLimitError(f"{prefix} CompanyFacts capture exceeded 120 seconds")


def run_sec_aapl_companyfacts(
    *,
    stores: StoreMap,
    registry: Registry,
    submissions_body: bytes,
    companyfacts_body: bytes,
    captured_at: datetime,
    held_locks: HeldWriteLocks | None = None,
    deadline: float | None = None,
) -> IngestionReceipt:
    """Parse and publish pre-fetched AAPL SEC endpoint bodies once."""

    parsed = parse_sec_aapl_bundle(
        submissions_body=submissions_body,
        companyfacts_body=companyfacts_body,
        captured_at=captured_at,
    )
    _validate_prewrite_deadline(deadline, legacy_aapl=True)
    return publish_sec_aapl_bundle(
        stores=stores,
        registry=registry,
        parsed=parsed,
        held_locks=held_locks,
    )


def run_sec_companyfacts(
    *,
    cik: object,
    stores: StoreMap,
    registry: Registry,
    submissions_body: bytes,
    companyfacts_body: bytes,
    captured_at: datetime,
    held_locks: HeldWriteLocks | None = None,
    deadline: float | None = None,
) -> IngestionReceipt:
    """Parse and publish one pre-fetched SEC issuer bundle once.

    This function never performs network I/O and deliberately handles exactly
    one CIK in memory.  Its caller owns scheduling, request pacing, and any
    provider credential boundary.
    """

    normalized_cik = _cik(cik, "requested CIK")
    if normalized_cik == AAPL_CIK:
        return run_sec_aapl_companyfacts(
            stores=stores,
            registry=registry,
            submissions_body=submissions_body,
            companyfacts_body=companyfacts_body,
            captured_at=captured_at,
            held_locks=held_locks,
            deadline=deadline,
        )
    parsed = parse_sec_companyfacts_bundle(
        cik=normalized_cik,
        submissions_body=submissions_body,
        companyfacts_body=companyfacts_body,
        captured_at=captured_at,
    )
    _validate_prewrite_deadline(deadline, legacy_aapl=False)
    return publish_sec_companyfacts_bundle(
        stores=stores,
        registry=registry,
        parsed=parsed,
        held_locks=held_locks,
    )


__all__ = (
    "AAPL_CIK",
    "MAX_COMPANYFACTS_BYTES",
    "MAX_GENERIC_COMPANYFACTS_BYTES",
    "MAX_GENERIC_SOURCE_ROWS",
    "MAX_GENERIC_SUBMISSIONS_BYTES",
    "MAX_GENERIC_TOTAL_RESPONSE_BYTES",
    "MAX_SUBMISSIONS_BYTES",
    "MAX_TOTAL_RESPONSE_BYTES",
    "ParsedSecAaplBundle",
    "ParsedSecCompanyFactsBundle",
    "SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID",
    "SEC_AAPL_COMPANYFACTS_COLLECTOR_ID",
    "SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID",
    "SEC_AAPL_COMPANYFACTS_NORMALIZATION_VERSION",
    "SEC_COMPANYFACTS_CANONICAL_DATASET_ID",
    "SEC_COMPANYFACTS_COLLECTOR_ID",
    "SEC_COMPANYFACTS_EVIDENCE_DATASET_ID",
    "SEC_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID",
    "SEC_COMPANYFACTS_FILINGS_DATASET_ID",
    "SEC_COMPANYFACTS_IDENTITY_DATASET_ID",
    "SEC_COMPANYFACTS_NORMALIZATION_VERSION",
    "SEC_CORE_MAPPING_VERSION",
    "SEC_CORE_MAPPINGS",
    "SecAaplCompanyFactsPublisher",
    "SecCompanyFactsPublisher",
    "SecAaplCoreFact",
    "SecAaplFiling",
    "SecCoreMapping",
    "parse_sec_aapl_bundle",
    "parse_sec_companyfacts_bundle",
    "publish_sec_aapl_bundle",
    "publish_sec_companyfacts_bundle",
    "run_sec_aapl_companyfacts",
    "run_sec_companyfacts",
)
