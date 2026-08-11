"""Offline Stage 4 company-fixture parsing and publication.

The Stage 4 company lane accepts only manifest-verified synthetic JSON.  Its
strict parser deliberately keeps CIK, accession, source availability, raw SEC
facts, mapping lineage, share semantics, corporate actions, and earnings
research records distinct before any database writer runs.

The reviewed Stage 4 company migrations are bound through append-only domain
writers. Nothing in this module opens a
default store, reads a caller path, or performs provider/network work.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import weakref
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..contracts import IngestionReceipt
from ..errors import ConflictError, Issue, ResourceLimitError, ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..stores import HeldWriteLocks, StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date


_MAX_FIXTURE_BYTES = 1_048_576
_MAX_ISSUERS = 1_000
_MAX_LINKS = 10_000
_MAX_FILINGS = 10_000
_MAX_FACTS = 50_000
_MAX_MAPPINGS = 10_000
_MAX_ACTIONS = 10_000
_MAX_EARNINGS_RECORDS = 10_000
_FAMILIES = frozenset({"sec", "actions", "earnings"})

@dataclass(frozen=True, slots=True)
class _FamilySpec:
    collector_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    identity_dataset_id: str
    provider: str


_FAMILY_SPECS: Mapping[str, _FamilySpec] = {
    "sec": _FamilySpec(
        collector_id="fixture.company.sec_import",
        evidence_dataset_id="fixture.company.sec_evidence",
        canonical_dataset_id="fixture.company.fundamentals",
        identity_dataset_id="fixture.company.issuers",
        provider="sec",
    ),
    "actions": _FamilySpec(
        collector_id="fixture.company.actions_import",
        evidence_dataset_id="fixture.company.action_evidence",
        canonical_dataset_id="fixture.company.corporate_actions",
        identity_dataset_id="fixture.company.issuers",
        provider="fmp",
    ),
    "earnings": _FamilySpec(
        collector_id="fixture.company.expectations_import",
        evidence_dataset_id="fixture.company.expectation_evidence",
        canonical_dataset_id="fixture.company.expectations",
        identity_dataset_id="fixture.company.issuers",
        provider="fmp",
    ),
}


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _nonempty(value: object, *, pointer: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(pointer, "required", "Expected a nonempty string")
    return value.strip()


def _nullable_text(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    return _nonempty(value, pointer=pointer)

_SEC_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")

def _sec_accession(value: object, *, pointer: str) -> str:
    accession = _nonempty(value, pointer=pointer)
    if _SEC_ACCESSION_PATTERN.fullmatch(accession) is None:
        raise _error(
            pointer,
            "format",
            "SEC accession number must match 10 digits, a hyphen, 2 digits, a hyphen, and 6 digits",
        )
    return accession


def _nullable_sec_accession(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    return _sec_accession(value, pointer=pointer)


def _mapping(value: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error(pointer, "type", "Expected an object with string keys")
    return value


def _array(value: object, *, pointer: str, maximum: int) -> Sequence[Any]:
    if not isinstance(value, list):
        raise _error(pointer, "type", "Expected an array")
    if len(value) > maximum:
        raise ResourceLimitError(f"{pointer} exceeds the supported fixture bound")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    pointer: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    fields = set(value)
    if not required.issubset(fields) or not fields.issubset(required | optional):
        raise _error(pointer, "shape", "Object fields do not match the reviewed fixture shape")


def _date(value: object, *, pointer: str) -> str:
    return parse_date(_nonempty(value, pointer=pointer), pointer=pointer).isoformat()


def _temporal(value: object, *, precision: object, pointer: str) -> TemporalValue:
    parsed = TemporalValue.parse(_nonempty(value, pointer=pointer), pointer=pointer)
    declared = _nonempty(precision, pointer=pointer.rsplit("/", 1)[0] + "/precision")
    if declared not in {TemporalPrecision.DATE.value, TemporalPrecision.DATETIME.value}:
        raise _error(pointer.rsplit("/", 1)[0] + "/precision", "enum", "Unsupported precision")
    if parsed.precision.value != declared:
        raise _error(pointer, "precision", "Temporal precision must match the source value")
    return parsed


def _decimal(value: object, *, pointer: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise _error(pointer, "type", "Expected a finite decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _error(pointer, "format", "Expected a finite decimal string") from exc
    if not result.is_finite():
        raise _error(pointer, "finite", "Expected a finite decimal string")
    return result


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _string_map(value: object, *, pointer: str) -> tuple[dict[str, str], str]:
    raw = _mapping(value, pointer=pointer)
    result: dict[str, str] = {}
    for key, item in raw.items():
        result[_nonempty(key, pointer=f"{pointer}/<key>")] = _nonempty(
            item, pointer=f"{pointer}/{key}"
        )
    rendered = dumps_strict(result)
    return result, rendered


def _interval_overlaps(
    left_from: str,
    left_through: str | None,
    right_from: str,
    right_through: str | None,
) -> bool:
    return (left_through is None or right_from <= left_through) and (
        right_through is None or left_from <= right_through
    )


def _require_local_capture(
    availability: TemporalValue,
    captured: TemporalValue,
    *,
    pointer: str,
) -> None:
    if availability.precision is not TemporalPrecision.DATETIME or availability.raw != captured.raw:
        raise _error(
            pointer,
            "availability",
            "Local-capture records must use the exact datetime capture availability",
        )


@dataclass(frozen=True, slots=True)
class _IssuerLink:
    link_kind: str
    provider: str
    provider_identifier: str
    valid_from: str
    valid_through: str | None
    available_at: TemporalValue
    confidence: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "link_kind": self.link_kind,
            "provider": self.provider,
            "provider_identifier": self.provider_identifier,
            "valid_from": self.valid_from,
            "valid_through": self.valid_through,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class _Issuer:
    issuer_key: str
    cik: str
    legal_name: str
    entity_type: str | None
    state: str
    available_at: TemporalValue
    links: tuple[_IssuerLink, ...]

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "issuer_key": self.issuer_key,
            "cik": self.cik,
            "legal_name": self.legal_name,
            "entity_type": self.entity_type,
            "state": self.state,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "links": [item.semantic_mapping() for item in self.links],
        }


@dataclass(frozen=True, slots=True)
class _Filing:
    accession_number: str
    first_observed_issuer_key: str
    issuer_keys: tuple[str, ...]
    form: str
    filing_date: str
    accepted_at: TemporalValue
    report_period: str | None
    primary_document: str
    source_url: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "accession_number": self.accession_number,
            "first_observed_issuer_key": self.first_observed_issuer_key,
            "issuer_keys": list(self.issuer_keys),
            "form": self.form,
            "filing_date": self.filing_date,
            "accepted_at": self.accepted_at.raw,
            "accepted_precision": self.accepted_at.precision.value,
            "report_period": self.report_period,
            "primary_document": self.primary_document,
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class _MetricMapping:
    mapping_version: str
    taxonomy: str
    concept: str
    unit: str
    metric_code: str
    metric_label: str
    metric_kind: str
    share_semantics: str | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "mapping_version": self.mapping_version,
            "taxonomy": self.taxonomy,
            "concept": self.concept,
            "unit": self.unit,
            "metric_code": self.metric_code,
            "metric_label": self.metric_label,
            "metric_kind": self.metric_kind,
            "share_semantics": self.share_semantics,
        }


@dataclass(frozen=True, slots=True)
class _Fact:
    source_fact_key: str
    issuer_key: str
    accession_number: str
    taxonomy: str
    concept: str
    unit: str
    period_start: str | None
    period_end: str
    period_kind: str
    fiscal_year: int
    fiscal_period: str
    filed_at: str
    accepted_at: TemporalValue
    value: Decimal
    dimensions: Mapping[str, str]
    dimensions_json: str
    mapping_version: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "source_fact_key": self.source_fact_key,
            "issuer_key": self.issuer_key,
            "accession_number": self.accession_number,
            "taxonomy": self.taxonomy,
            "concept": self.concept,
            "unit": self.unit,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "period_kind": self.period_kind,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "filed_at": self.filed_at,
            "accepted_at": self.accepted_at.raw,
            "accepted_precision": self.accepted_at.precision.value,
            "value": _decimal_text(self.value),
            "dimensions": dict(self.dimensions),
            "mapping_version": self.mapping_version,
        }


@dataclass(frozen=True, slots=True)
class _Action:
    action_key: str
    issuer_key: str
    provider: str
    provider_symbol: str
    action_type: str
    event_date: str
    available_at: TemporalValue
    source_url: str
    terms: Mapping[str, str]
    terms_json: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "action_key": self.action_key,
            "issuer_key": self.issuer_key,
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "action_type": self.action_type,
            "event_date": self.event_date,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "source_url": self.source_url,
            "terms": dict(self.terms),
        }


@dataclass(frozen=True, slots=True)
class _EarningsEvent:
    event_key: str
    issuer_key: str
    provider: str
    event_date: str
    fiscal_period_end: str
    event_type: str
    status: str
    available_at: TemporalValue

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "event_key": self.event_key,
            "issuer_key": self.issuer_key,
            "provider": self.provider,
            "event_date": self.event_date,
            "fiscal_period_end": self.fiscal_period_end,
            "event_type": self.event_type,
            "status": self.status,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
        }


@dataclass(frozen=True, slots=True)
class _Expectation:
    expectation_key: str
    issuer_key: str
    provider: str
    event_date: str
    event_key: str | None
    metric_code: str
    fiscal_period_end: str
    value: Decimal
    currency: str
    available_at: TemporalValue

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "expectation_key": self.expectation_key,
            "issuer_key": self.issuer_key,
            "provider": self.provider,
            "event_date": self.event_date,
            "event_key": self.event_key,
            "metric_code": self.metric_code,
            "fiscal_period_end": self.fiscal_period_end,
            "value": _decimal_text(self.value),
            "currency": self.currency,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
        }


@dataclass(frozen=True, slots=True)
class _Guidance:
    guidance_key: str
    issuer_key: str
    provider: str
    accession_number: str | None
    metric_code: str
    target_period_end: str
    value_shape: str
    value_low: Decimal | None
    value_high: Decimal | None
    value_point: Decimal | None
    currency: str
    review_state: str
    source_url: str
    available_at: TemporalValue

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "guidance_key": self.guidance_key,
            "issuer_key": self.issuer_key,
            "provider": self.provider,
            "accession_number": self.accession_number,
            "metric_code": self.metric_code,
            "target_period_end": self.target_period_end,
            "value_shape": self.value_shape,
            "value_low": None if self.value_low is None else _decimal_text(self.value_low),
            "value_high": None if self.value_high is None else _decimal_text(self.value_high),
            "value_point": None if self.value_point is None else _decimal_text(self.value_point),
            "currency": self.currency,
            "review_state": self.review_state,
            "source_url": self.source_url,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
        }


@dataclass(frozen=True, slots=True)
class _CompanyPayload:
    family: str
    issuers: tuple[_Issuer, ...]
    filings: tuple[_Filing, ...]
    facts: tuple[_Fact, ...]
    mappings: tuple[_MetricMapping, ...]
    actions: tuple[_Action, ...]
    earnings_events: tuple[_EarningsEvent, ...]
    expectations: tuple[_Expectation, ...]
    guidance: tuple[_Guidance, ...]

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "family": self.family,
            "issuers": [item.semantic_mapping() for item in self.issuers],
            "filings": [item.semantic_mapping() for item in self.filings],
            "facts": [item.semantic_mapping() for item in self.facts],
            "mappings": [item.semantic_mapping() for item in self.mappings],
            "actions": [item.semantic_mapping() for item in self.actions],
            "earnings_events": [item.semantic_mapping() for item in self.earnings_events],
            "expectations": [item.semantic_mapping() for item in self.expectations],
            "guidance": [item.semantic_mapping() for item in self.guidance],
        }


@dataclass(frozen=True, slots=True)
class ParsedCompanyFixture:
    """Strict, pre-write Stage 4 fixture representation.

    This is intentionally useful to focused tests and to the integration owner
    when fixture-manifest semantic pins are generated.  It contains no open
    database connection or caller-selected filesystem state.
    """

    fixture: Fixture
    payload: _CompanyPayload
    scope: Mapping[str, object]
    captured_at: TemporalValue
    normalization_version: str
    semantic_identity: str


class _PreparedCompanyFixture:
    """Opaque company candidate produced only by one importer instance."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("Prepared company fixtures are created by prepare_fixture")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("Prepared company fixtures cannot be subclassed")


@dataclass(frozen=True, slots=True)
class _PreparedCompanyFixtureState:
    owner_token: object
    store_map: StoreMap
    fixture_manifest: FixtureManifest
    fixture_id: str
    fixture_sha256: str
    fixture_byte_count: int
    collector_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    identity_dataset_id: str | None
    store: str
    fixture: Fixture
    parsed: ParsedCompanyFixture
    family: str
    output_dataset_ids: tuple[str, ...]
    scope: Mapping[str, object]
    semantic_identity: str
    run_id: str


_PREPARED_COMPANY_STATES: weakref.WeakKeyDictionary[
    _PreparedCompanyFixture, _PreparedCompanyFixtureState
] = weakref.WeakKeyDictionary()


def _new_prepared_company_fixture(
    state: _PreparedCompanyFixtureState,
) -> _PreparedCompanyFixture:
    prepared = object.__new__(_PreparedCompanyFixture)
    _PREPARED_COMPANY_STATES[prepared] = state
    return prepared


def _parse_link(raw: object, *, pointer: str) -> _IssuerLink:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "link_kind",
            "provider",
            "provider_identifier",
            "valid_from",
            "valid_through",
            "available_at",
            "available_precision",
            "confidence",
        },
    )
    link_kind = _nonempty(value["link_kind"], pointer=f"{pointer}/link_kind")
    if link_kind not in {"ticker", "security", "provider_symbol"}:
        raise _error(f"{pointer}/link_kind", "enum", "Unsupported issuer link kind")
    valid_from = _date(value["valid_from"], pointer=f"{pointer}/valid_from")
    valid_through = (
        None
        if value["valid_through"] is None
        else _date(value["valid_through"], pointer=f"{pointer}/valid_through")
    )
    if valid_through is not None and valid_through < valid_from:
        raise _error(f"{pointer}/valid_through", "range", "Issuer link interval is inverted")
    confidence = _nonempty(value["confidence"], pointer=f"{pointer}/confidence")
    if confidence not in {"confirmed", "unconfirmed"}:
        raise _error(f"{pointer}/confidence", "enum", "Unsupported issuer-link confidence")
    return _IssuerLink(
        link_kind=link_kind,
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        provider_identifier=_nonempty(
            value["provider_identifier"], pointer=f"{pointer}/provider_identifier"
        ),
        valid_from=valid_from,
        valid_through=valid_through,
        available_at=_temporal(
            value["available_at"],
            precision=value["available_precision"],
            pointer=f"{pointer}/available_at",
        ),
        confidence=confidence,
    )


def _parse_issuer(raw: object, *, pointer: str) -> _Issuer:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "issuer_key",
            "cik",
            "legal_name",
            "entity_type",
            "state",
            "available_at",
            "available_precision",
            "links",
        },
    )
    cik = _nonempty(value["cik"], pointer=f"{pointer}/cik")
    if len(cik) != 10 or not cik.isdigit():
        raise _error(f"{pointer}/cik", "format", "CIK must be a ten-digit SEC identity")
    state = _nonempty(value["state"], pointer=f"{pointer}/state")
    if state not in {"active", "inactive"}:
        raise _error(f"{pointer}/state", "enum", "Issuer state must be active or inactive")
    links_raw = _array(value["links"], pointer=f"{pointer}/links", maximum=_MAX_LINKS)
    links = tuple(
        _parse_link(link, pointer=f"{pointer}/links/{ordinal}")
        for ordinal, link in enumerate(links_raw)
    )
    link_keys = [
        (item.link_kind, item.provider, item.provider_identifier, item.valid_from)
        for item in links
    ]
    if len(link_keys) != len(set(link_keys)):
        raise _error(f"{pointer}/links", "unique", "Issuer links repeat a natural key")
    return _Issuer(
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        cik=cik,
        legal_name=_nonempty(value["legal_name"], pointer=f"{pointer}/legal_name"),
        entity_type=_nullable_text(value["entity_type"], pointer=f"{pointer}/entity_type"),
        state=state,
        available_at=_temporal(
            value["available_at"],
            precision=value["available_precision"],
            pointer=f"{pointer}/available_at",
        ),
        links=tuple(
            sorted(
                links,
                key=lambda item: (
                    item.link_kind,
                    item.provider,
                    item.provider_identifier,
                    item.valid_from,
                    item.valid_through or "",
                ),
            )
        ),
    )


def _parse_filing(raw: object, *, pointer: str) -> _Filing:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "accession_number",
            "first_observed_issuer_key",
            "issuer_keys",
            "form",
            "filing_date",
            "accepted_at",
            "accepted_precision",
            "report_period",
            "primary_document",
            "source_url",
        },
    )
    issuer_keys = tuple(
        sorted(
            {
                _nonempty(item, pointer=f"{pointer}/issuer_keys/{ordinal}")
                for ordinal, item in enumerate(
                    _array(value["issuer_keys"], pointer=f"{pointer}/issuer_keys", maximum=_MAX_ISSUERS)
                )
            }
        )
    )
    if not issuer_keys:
        raise _error(f"{pointer}/issuer_keys", "minimum", "A filing needs an issuer membership")
    first = _nonempty(
        value["first_observed_issuer_key"], pointer=f"{pointer}/first_observed_issuer_key"
    )
    if first not in issuer_keys:
        raise _error(
            f"{pointer}/first_observed_issuer_key",
            "membership",
            "First-observed issuer must be a filing member",
        )
    report_period = (
        None
        if value["report_period"] is None
        else _date(value["report_period"], pointer=f"{pointer}/report_period")
    )
    return _Filing(
        accession_number=_sec_accession(
            value["accession_number"], pointer=f"{pointer}/accession_number"
        ),
        first_observed_issuer_key=first,
        issuer_keys=issuer_keys,
        form=_nonempty(value["form"], pointer=f"{pointer}/form"),
        filing_date=_date(value["filing_date"], pointer=f"{pointer}/filing_date"),
        accepted_at=_temporal(
            value["accepted_at"],
            precision=value["accepted_precision"],
            pointer=f"{pointer}/accepted_at",
        ),
        report_period=report_period,
        primary_document=_nonempty(value["primary_document"], pointer=f"{pointer}/primary_document"),
        source_url=_nonempty(value["source_url"], pointer=f"{pointer}/source_url"),
    )


def _parse_mapping(raw: object, *, pointer: str) -> _MetricMapping:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "mapping_version",
            "taxonomy",
            "concept",
            "unit",
            "metric_code",
            "metric_label",
            "metric_kind",
            "share_semantics",
        },
    )
    metric_kind = _nonempty(value["metric_kind"], pointer=f"{pointer}/metric_kind")
    if metric_kind not in {"fundamental", "share_count"}:
        raise _error(f"{pointer}/metric_kind", "enum", "Unsupported metric kind")
    share_semantics = _nullable_text(
        value["share_semantics"], pointer=f"{pointer}/share_semantics"
    )
    if metric_kind == "fundamental" and share_semantics is not None:
        raise _error(
            f"{pointer}/share_semantics",
            "semantic_separation",
            "Fundamentals cannot claim share-count semantics",
        )
    if metric_kind == "share_count" and share_semantics not in {"instant", "weighted_average"}:
        raise _error(
            f"{pointer}/share_semantics",
            "semantic_separation",
            "Share-count mappings require instant or weighted-average semantics",
        )
    return _MetricMapping(
        mapping_version=_nonempty(value["mapping_version"], pointer=f"{pointer}/mapping_version"),
        taxonomy=_nonempty(value["taxonomy"], pointer=f"{pointer}/taxonomy"),
        concept=_nonempty(value["concept"], pointer=f"{pointer}/concept"),
        unit=_nonempty(value["unit"], pointer=f"{pointer}/unit"),
        metric_code=_nonempty(value["metric_code"], pointer=f"{pointer}/metric_code"),
        metric_label=_nonempty(value["metric_label"], pointer=f"{pointer}/metric_label"),
        metric_kind=metric_kind,
        share_semantics=share_semantics,
    )


def _parse_fact(raw: object, *, pointer: str) -> _Fact:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "source_fact_key",
            "issuer_key",
            "accession_number",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            "period_kind",
            "fiscal_year",
            "fiscal_period",
            "filed_at",
            "accepted_at",
            "accepted_precision",
            "value",
            "dimensions",
            "mapping_version",
        },
    )
    period_kind = _nonempty(value["period_kind"], pointer=f"{pointer}/period_kind")
    if period_kind not in {"instant", "duration"}:
        raise _error(f"{pointer}/period_kind", "enum", "Fact period kind must be instant or duration")
    period_start = (
        None
        if value["period_start"] is None
        else _date(value["period_start"], pointer=f"{pointer}/period_start")
    )
    period_end = _date(value["period_end"], pointer=f"{pointer}/period_end")
    if period_kind == "instant" and period_start is not None:
        raise _error(f"{pointer}/period_start", "period", "Instant facts cannot have a period start")
    if period_kind == "duration" and period_start is None:
        raise _error(f"{pointer}/period_start", "period", "Duration facts require a period start")
    if period_start is not None and period_start > period_end:
        raise _error(f"{pointer}/period_start", "range", "Fact period is inverted")
    fiscal_year = value["fiscal_year"]
    if isinstance(fiscal_year, bool) or not isinstance(fiscal_year, int) or not 1900 <= fiscal_year <= 9999:
        raise _error(f"{pointer}/fiscal_year", "range", "Fiscal year must be a four-digit integer")
    dimensions, dimensions_json = _string_map(value["dimensions"], pointer=f"{pointer}/dimensions")
    return _Fact(
        source_fact_key=_nonempty(value["source_fact_key"], pointer=f"{pointer}/source_fact_key"),
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        accession_number=_sec_accession(
            value["accession_number"], pointer=f"{pointer}/accession_number"
        ),
        taxonomy=_nonempty(value["taxonomy"], pointer=f"{pointer}/taxonomy"),
        concept=_nonempty(value["concept"], pointer=f"{pointer}/concept"),
        unit=_nonempty(value["unit"], pointer=f"{pointer}/unit"),
        period_start=period_start,
        period_end=period_end,
        period_kind=period_kind,
        fiscal_year=fiscal_year,
        fiscal_period=_nonempty(value["fiscal_period"], pointer=f"{pointer}/fiscal_period"),
        filed_at=_date(value["filed_at"], pointer=f"{pointer}/filed_at"),
        accepted_at=_temporal(
            value["accepted_at"],
            precision=value["accepted_precision"],
            pointer=f"{pointer}/accepted_at",
        ),
        value=_decimal(value["value"], pointer=f"{pointer}/value"),
        dimensions=dimensions,
        dimensions_json=dimensions_json,
        mapping_version=_nonempty(value["mapping_version"], pointer=f"{pointer}/mapping_version"),
    )

def _parse_action(
    raw: object,
    *,
    pointer: str,
    captured_at: TemporalValue,
) -> _Action:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "action_key",
            "issuer_key",
            "provider",
            "provider_symbol",
            "action_type",
            "event_date",
            "available_at",
            "available_precision",
            "source_url",
            "terms",
        },
    )
    action_type = _nonempty(value["action_type"], pointer=f"{pointer}/action_type")
    terms = _mapping(value["terms"], pointer=f"{pointer}/terms")
    if action_type == "cash_dividend":
        _exact_keys(
            terms,
            pointer=f"{pointer}/terms",
            required={"cash_amount", "currency"},
        )
        cash_amount = _decimal(terms["cash_amount"], pointer=f"{pointer}/terms/cash_amount")
        if cash_amount <= 0:
            raise _error(f"{pointer}/terms/cash_amount", "range", "Cash dividend must be positive")
        normalized_terms = {
            "cash_amount": _decimal_text(cash_amount),
            "currency": _nonempty(terms["currency"], pointer=f"{pointer}/terms/currency"),
        }
    elif action_type == "split":
        _exact_keys(
            terms,
            pointer=f"{pointer}/terms",
            required={"numerator", "denominator"},
        )
        numerator = _decimal(terms["numerator"], pointer=f"{pointer}/terms/numerator")
        denominator = _decimal(terms["denominator"], pointer=f"{pointer}/terms/denominator")
        if numerator <= 0 or denominator <= 0:
            raise _error(f"{pointer}/terms", "range", "Split terms must be positive")
        normalized_terms = {
            "numerator": _decimal_text(numerator),
            "denominator": _decimal_text(denominator),
        }
    else:
        raise _error(f"{pointer}/action_type", "enum", "Unsupported corporate action type")
    availability = _temporal(
        value["available_at"],
        precision=value["available_precision"],
        pointer=f"{pointer}/available_at",
    )
    _require_local_capture(availability, captured_at, pointer=f"{pointer}/available_at")
    return _Action(
        action_key=_nonempty(value["action_key"], pointer=f"{pointer}/action_key"),
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        provider_symbol=_nonempty(value["provider_symbol"], pointer=f"{pointer}/provider_symbol"),
        action_type=action_type,
        event_date=_date(value["event_date"], pointer=f"{pointer}/event_date"),
        available_at=availability,
        source_url=_nonempty(value["source_url"], pointer=f"{pointer}/source_url"),
        terms=normalized_terms,
        terms_json=dumps_strict(normalized_terms),
    )


def _parse_earnings_event(
    raw: object,
    *,
    pointer: str,
    captured_at: TemporalValue,
) -> _EarningsEvent:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "event_key",
            "issuer_key",
            "provider",
            "event_date",
            "fiscal_period_end",
            "event_type",
            "status",
            "available_at",
            "available_precision",
        },
    )
    event_type = _nonempty(value["event_type"], pointer=f"{pointer}/event_type")
    if event_type not in {"preliminary", "results"}:
        raise _error(f"{pointer}/event_type", "enum", "Unsupported earnings event type")
    status = _nonempty(value["status"], pointer=f"{pointer}/status")
    if status not in {"scheduled", "reported", "cancelled"}:
        raise _error(f"{pointer}/status", "enum", "Unsupported earnings event status")
    availability = _temporal(
        value["available_at"],
        precision=value["available_precision"],
        pointer=f"{pointer}/available_at",
    )
    _require_local_capture(availability, captured_at, pointer=f"{pointer}/available_at")
    return _EarningsEvent(
        event_key=_nonempty(value["event_key"], pointer=f"{pointer}/event_key"),
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        event_date=_date(value["event_date"], pointer=f"{pointer}/event_date"),
        fiscal_period_end=_date(value["fiscal_period_end"], pointer=f"{pointer}/fiscal_period_end"),
        event_type=event_type,
        status=status,
        available_at=availability,
    )


def _parse_expectation(
    raw: object,
    *,
    pointer: str,
    captured_at: TemporalValue,
) -> _Expectation:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "expectation_key",
            "issuer_key",
            "provider",
            "event_date",
            "event_key",
            "metric_code",
            "fiscal_period_end",
            "value",
            "currency",
            "available_at",
            "available_precision",
        },
    )
    availability = _temporal(
        value["available_at"],
        precision=value["available_precision"],
        pointer=f"{pointer}/available_at",
    )
    _require_local_capture(availability, captured_at, pointer=f"{pointer}/available_at")
    return _Expectation(
        expectation_key=_nonempty(value["expectation_key"], pointer=f"{pointer}/expectation_key"),
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        event_date=_date(value["event_date"], pointer=f"{pointer}/event_date"),
        event_key=_nullable_text(value["event_key"], pointer=f"{pointer}/event_key"),
        metric_code=_nonempty(value["metric_code"], pointer=f"{pointer}/metric_code"),
        fiscal_period_end=_date(value["fiscal_period_end"], pointer=f"{pointer}/fiscal_period_end"),
        value=_decimal(value["value"], pointer=f"{pointer}/value"),
        currency=_nonempty(value["currency"], pointer=f"{pointer}/currency"),
        available_at=availability,
    )


def _parse_guidance(
    raw: object,
    *,
    pointer: str,
    captured_at: TemporalValue,
) -> _Guidance:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "guidance_key",
            "issuer_key",
            "provider",
            "accession_number",
            "metric_code",
            "target_period_end",
            "value_shape",
            "value_low",
            "value_high",
            "value_point",
            "currency",
            "review_state",
            "source_url",
            "available_at",
            "available_precision",
        },
    )
    value_shape = _nonempty(value["value_shape"], pointer=f"{pointer}/value_shape")
    if value_shape not in {"point", "range"}:
        raise _error(f"{pointer}/value_shape", "enum", "Guidance value shape must be point or range")
    low = None if value["value_low"] is None else _decimal(value["value_low"], pointer=f"{pointer}/value_low")
    high = None if value["value_high"] is None else _decimal(value["value_high"], pointer=f"{pointer}/value_high")
    point = None if value["value_point"] is None else _decimal(value["value_point"], pointer=f"{pointer}/value_point")
    if value_shape == "point" and (point is None or low is not None or high is not None):
        raise _error(f"{pointer}/value_shape", "shape", "Point guidance requires only value_point")
    if value_shape == "range" and (low is None or high is None or point is not None):
        raise _error(f"{pointer}/value_shape", "shape", "Range guidance requires value_low and value_high")
    if low is not None and high is not None and low > high:
        raise _error(f"{pointer}/value_low", "range", "Guidance range is inverted")
    review_state = _nonempty(value["review_state"], pointer=f"{pointer}/review_state")
    if review_state not in {"reviewed", "unreviewed", "rejected"}:
        raise _error(f"{pointer}/review_state", "enum", "Unsupported guidance review state")
    availability = _temporal(
        value["available_at"],
        precision=value["available_precision"],
        pointer=f"{pointer}/available_at",
    )
    _require_local_capture(availability, captured_at, pointer=f"{pointer}/available_at")
    return _Guidance(
        guidance_key=_nonempty(value["guidance_key"], pointer=f"{pointer}/guidance_key"),
        issuer_key=_nonempty(value["issuer_key"], pointer=f"{pointer}/issuer_key"),
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        accession_number=_nullable_sec_accession(
            value["accession_number"], pointer=f"{pointer}/accession_number"
        ),
        metric_code=_nonempty(value["metric_code"], pointer=f"{pointer}/metric_code"),
        target_period_end=_date(value["target_period_end"], pointer=f"{pointer}/target_period_end"),
        value_shape=value_shape,
        value_low=low,
        value_high=high,
        value_point=point,
        currency=_nonempty(value["currency"], pointer=f"{pointer}/currency"),
        review_state=review_state,
        source_url=_nonempty(value["source_url"], pointer=f"{pointer}/source_url"),
        available_at=availability,
    )


def _scope_string_array(value: object, *, pointer: str) -> tuple[str, ...]:
    entries = tuple(
        _nonempty(item, pointer=f"{pointer}/{ordinal}")
        for ordinal, item in enumerate(_array(value, pointer=pointer, maximum=_MAX_ISSUERS))
    )
    if not entries or len(entries) != len(set(entries)):
        raise _error(pointer, "unique", "Scope identity array must be nonempty and unique")
    return tuple(sorted(entries))


def _fixture_scope(fixture: Fixture, *, family: str) -> dict[str, object]:
    raw = _mapping(fixture.request_scope, pointer="/request_scope")
    if family == "sec":
        _exact_keys(
            raw,
            pointer="/request_scope",
            required={"scope_kind", "provider", "ciks", "completeness"},
        )
        identities = _scope_string_array(raw["ciks"], pointer="/request_scope/ciks")
        identity_key = "ciks"
        expected_kind = "sec_company"
    elif family == "actions":
        _exact_keys(
            raw,
            pointer="/request_scope",
            required={"scope_kind", "provider", "issuer_keys", "completeness"},
        )
        identities = _scope_string_array(raw["issuer_keys"], pointer="/request_scope/issuer_keys")
        identity_key = "issuer_keys"
        expected_kind = "company_actions"
    else:
        _exact_keys(
            raw,
            pointer="/request_scope",
            required={"scope_kind", "provider", "issuer_keys", "completeness"},
        )
        identities = _scope_string_array(raw["issuer_keys"], pointer="/request_scope/issuer_keys")
        identity_key = "issuer_keys"
        expected_kind = "company_earnings"
    scope_kind = _nonempty(raw["scope_kind"], pointer="/request_scope/scope_kind")
    provider = _nonempty(raw["provider"], pointer="/request_scope/provider")
    completeness = _nonempty(raw["completeness"], pointer="/request_scope/completeness")
    spec = _FAMILY_SPECS[family]
    if scope_kind != expected_kind or provider != spec.provider or fixture.provider != spec.provider:
        raise _error("/request_scope", "contract", "Fixture scope does not match its reviewed family")
    if completeness != "complete":
        raise _error("/request_scope/completeness", "contract", "Stage 4 company fixtures must be complete")
    return {
        "scope_kind": scope_kind,
        "provider": provider,
        identity_key: list(identities),
        "completeness": completeness,
    }


def _normalization_version(fixture: Fixture) -> str:
    metadata = _mapping(fixture.metadata, pointer="/metadata")
    return _nonempty(metadata.get("normalization_version"), pointer="/metadata/normalization_version")


def _validate_link_intervals(issuers: Sequence[_Issuer]) -> None:
    candidates: dict[tuple[str, str, str], list[tuple[str, _IssuerLink]]] = {}
    for issuer in issuers:
        for link in issuer.links:
            candidates.setdefault(
                (link.link_kind, link.provider, link.provider_identifier), []
            ).append((issuer.issuer_key, link))
    for key, values in candidates.items():
        ordered = sorted(values, key=lambda item: (item[1].valid_from, item[0]))
        for index, (left_issuer, left) in enumerate(ordered):
            for right_issuer, right in ordered[index + 1 :]:
                if _interval_overlaps(
                    left.valid_from,
                    left.valid_through,
                    right.valid_from,
                    right.valid_through,
                ):
                    raise _error(
                        "/issuers",
                        "identifier_overlap",
                        f"Issuer link {key[0]}:{key[1]}:{key[2]} overlaps {left_issuer} and {right_issuer}",
                    )


def _validate_payload_references(payload: _CompanyPayload) -> None:
    filing_by_accession = {filing.accession_number: filing for filing in payload.filings}
    issuer_keys = {issuer.issuer_key for issuer in payload.issuers}
    referenced_issuer_keys = {
        key
        for filing in payload.filings
        for key in filing.issuer_keys
    }
    referenced_issuer_keys.update(fact.issuer_key for fact in payload.facts)
    referenced_issuer_keys.update(action.issuer_key for action in payload.actions)
    referenced_issuer_keys.update(event.issuer_key for event in payload.earnings_events)
    referenced_issuer_keys.update(expectation.issuer_key for expectation in payload.expectations)
    referenced_issuer_keys.update(guidance.issuer_key for guidance in payload.guidance)
    unknown_issuer_keys = referenced_issuer_keys - issuer_keys
    if unknown_issuer_keys:
        raise _error(
            "/issuers",
            "reference",
            "Every fixture record must resolve a declared CIK-backed issuer",
        )
    symbol_links: list[tuple[str, _IssuerLink, str]] = []
    for issuer in payload.issuers:
        # This runs before the coordinator is invoked, so a ticker without one
        # unambiguous covering security identity cannot leave a failed-run row.
        symbol_links.extend(
            (issuer.issuer_key, link, security_identifier)
            for link, security_identifier in _symbol_links(issuer)
        )
    for index, (left_issuer, left_link, left_security) in enumerate(symbol_links):
        for right_issuer, right_link, right_security in symbol_links[index + 1 :]:
            if (
                left_link.provider == right_link.provider
                and left_link.provider_identifier == right_link.provider_identifier
                and _interval_overlaps(
                    left_link.valid_from,
                    left_link.valid_through,
                    right_link.valid_from,
                    right_link.valid_through,
                )
                and (
                    left_issuer != right_issuer
                    or left_security != right_security
                )
            ):
                raise _error(
                    "/issuers",
                    "symbol_overlap",
                    "One provider symbol cannot resolve to overlapping issuer or security identities",
                )
    mapping_by_source = {
        (mapping.mapping_version, mapping.taxonomy, mapping.concept, mapping.unit): mapping
        for mapping in payload.mappings
    }
    for fact in payload.facts:
        filing = filing_by_accession.get(fact.accession_number)
        if filing is not None and fact.issuer_key not in filing.issuer_keys:
            raise _error(
                "/facts",
                "filing_membership",
                "A local SEC fact issuer must be a member of its local filing",
            )
        mapping = mapping_by_source.get(
            (fact.mapping_version, fact.taxonomy, fact.concept, fact.unit)
        )
        if mapping is not None and mapping.metric_kind == "share_count":
            if mapping.share_semantics not in {"instant", "weighted_average"}:
                raise _error(
                    "/facts",
                    "semantic_separation",
                    "Share facts require an explicit share-count semantic",
                )
    events_by_slot: dict[tuple[str, str, str], set[str]] = {}
    for event in payload.earnings_events:
        events_by_slot.setdefault((event.issuer_key, event.provider, event.event_date), set()).add(
            event.event_key
        )
    for expectation in payload.expectations:
        candidates = events_by_slot.get(
            (expectation.issuer_key, expectation.provider, expectation.event_date), set()
        )
        if expectation.event_key is None and len(candidates) > 1:
            raise _error(
                "/expectations",
                "event_ambiguity",
                "Same-date earnings candidates require an explicit event key",
            )
        if expectation.event_key is not None and candidates and expectation.event_key not in candidates:
            raise _error(
                "/expectations",
                "event_membership",
                "Expectation event key does not resolve within its local event candidates",
            )
    for guidance in payload.guidance:
        if guidance.accession_number is not None and guidance.accession_number in filing_by_accession:
            filing = filing_by_accession[guidance.accession_number]
            if guidance.issuer_key not in filing.issuer_keys:
                raise _error(
                    "/guidance",
                    "filing_membership",
                    "Guidance issuer must be a member of its local filing",
                )


def _payload_for_fixture(fixture: Fixture) -> tuple[_CompanyPayload, dict[str, object], TemporalValue, str]:
    if fixture.store != "company":
        raise ValidationError("Company Stage 4 importer accepts only company fixtures")
    if fixture.fixture_schema_version != "1.0.0":
        raise ValidationError("Company fixture schema version is unsupported")
    raw = loads_strict(fixture.bytes, max_bytes=_MAX_FIXTURE_BYTES)
    root = _mapping(raw, pointer="/")
    _exact_keys(
        root,
        pointer="/",
        required={
            "schema_version",
            "fixture_kind",
            "family",
            "captured_at",
            "captured_precision",
            "issuers",
            "filings",
            "facts",
            "mappings",
            "actions",
            "earnings_events",
            "expectations",
            "guidance",
        },
    )
    if root["schema_version"] != "1.0.0" or root["fixture_kind"] != "company_stage4_snapshot":
        raise ValidationError("Unsupported Stage 4 company fixture")
    family = _nonempty(root["family"], pointer="/family")
    if family not in _FAMILIES:
        raise _error("/family", "enum", "Unsupported Stage 4 company fixture family")
    spec = _FAMILY_SPECS[family]
    if (
        fixture.ingestion_family_id != spec.collector_id
        or fixture.evidence_dataset_id != spec.evidence_dataset_id
        or fixture.canonical_dataset_id != spec.canonical_dataset_id
        or fixture.identity_dataset_id != spec.identity_dataset_id
    ):
        raise ValidationError("Company fixture manifest contract does not match its family")
    captured_at = _temporal(
        root["captured_at"], precision=root["captured_precision"], pointer="/captured_at"
    )
    if captured_at.precision is not TemporalPrecision.DATETIME:
        raise _error("/captured_at", "precision", "Company fixture captures must be aware datetimes")
    if captured_at.raw != fixture.captured_at:
        raise _error("/captured_at", "manifest", "Fixture capture time does not match its verified manifest")
    scope = _fixture_scope(fixture, family=family)
    normalization_version = _normalization_version(fixture)
    issuers = tuple(
        _parse_issuer(item, pointer=f"/issuers/{ordinal}")
        for ordinal, item in enumerate(_array(root["issuers"], pointer="/issuers", maximum=_MAX_ISSUERS))
    )
    issuer_keys = [issuer.issuer_key for issuer in issuers]
    ciks = [issuer.cik for issuer in issuers]
    if len(issuer_keys) != len(set(issuer_keys)) or len(ciks) != len(set(ciks)):
        raise _error("/issuers", "unique", "Issuer keys and CIK identities must be unique")
    _validate_link_intervals(issuers)
    filings = tuple(
        _parse_filing(item, pointer=f"/filings/{ordinal}")
        for ordinal, item in enumerate(_array(root["filings"], pointer="/filings", maximum=_MAX_FILINGS))
    )
    if len({item.accession_number for item in filings}) != len(filings):
        raise _error("/filings", "unique", "SEC accession numbers must be unique per fixture")
    mappings = tuple(
        _parse_mapping(item, pointer=f"/mappings/{ordinal}")
        for ordinal, item in enumerate(_array(root["mappings"], pointer="/mappings", maximum=_MAX_MAPPINGS))
    )
    mapping_keys = [
        (item.mapping_version, item.taxonomy, item.concept, item.unit) for item in mappings
    ]
    if len(mapping_keys) != len(set(mapping_keys)):
        raise _error("/mappings", "unique", "Metric mapping versions must be unique per source concept")
    metric_contracts: dict[str, tuple[str, str, str | None]] = {}
    for mapping in mappings:
        contract = (mapping.metric_label, mapping.metric_kind, mapping.share_semantics)
        existing = metric_contracts.setdefault(mapping.metric_code, contract)
        if existing != contract:
            raise _error(
                "/mappings",
                "metric_contract",
                "One metric code cannot have conflicting meaning within a fixture",
            )
    facts = tuple(
        _parse_fact(item, pointer=f"/facts/{ordinal}")
        for ordinal, item in enumerate(_array(root["facts"], pointer="/facts", maximum=_MAX_FACTS))
    )
    if len({item.source_fact_key for item in facts}) != len(facts):
        raise _error("/facts", "unique", "Source fact keys must be unique per fixture")
    if any(fact.dimensions for fact in facts):
        raise _error(
            "/facts",
            "unsupported_dimensions",
            "The frozen Stage 4 raw-fact schema does not represent dimensional facts",
        )
    fact_naturals = {
        (
            item.issuer_key,
            item.taxonomy,
            item.concept,
            item.unit,
            item.period_end,
            item.accession_number,
        )
        for item in facts
    }
    if len(fact_naturals) != len(facts):
        raise _error("/facts", "natural_key", "SEC facts collide within one immutable capture")
    actions = tuple(
        _parse_action(item, pointer=f"/actions/{ordinal}", captured_at=captured_at)
        for ordinal, item in enumerate(_array(root["actions"], pointer="/actions", maximum=_MAX_ACTIONS))
    )
    if len({item.action_key for item in actions}) != len(actions):
        raise _error("/actions", "unique", "Action keys must be unique per fixture")
    earnings_events = tuple(
        _parse_earnings_event(item, pointer=f"/earnings_events/{ordinal}", captured_at=captured_at)
        for ordinal, item in enumerate(
            _array(root["earnings_events"], pointer="/earnings_events", maximum=_MAX_EARNINGS_RECORDS)
        )
    )
    if len({item.event_key for item in earnings_events}) != len(earnings_events):
        raise _error("/earnings_events", "unique", "Earnings event keys must be unique per fixture")
    expectations = tuple(
        _parse_expectation(item, pointer=f"/expectations/{ordinal}", captured_at=captured_at)
        for ordinal, item in enumerate(
            _array(root["expectations"], pointer="/expectations", maximum=_MAX_EARNINGS_RECORDS)
        )
    )
    if len({item.expectation_key for item in expectations}) != len(expectations):
        raise _error("/expectations", "unique", "Expectation keys must be unique per fixture")
    expectation_naturals = {
        (item.issuer_key, item.provider, item.metric_code, item.fiscal_period_end)
        for item in expectations
    }
    if len(expectation_naturals) != len(expectations):
        raise _error(
            "/expectations",
            "natural_key",
            "Consensus records collide within one immutable capture",
        )
    guidance = tuple(
        _parse_guidance(item, pointer=f"/guidance/{ordinal}", captured_at=captured_at)
        for ordinal, item in enumerate(
            _array(root["guidance"], pointer="/guidance", maximum=_MAX_EARNINGS_RECORDS)
        )
    )
    if len({item.guidance_key for item in guidance}) != len(guidance):
        raise _error("/guidance", "unique", "Guidance keys must be unique per fixture")
    guidance_naturals = {
        (item.issuer_key, item.provider, item.metric_code, item.target_period_end)
        for item in guidance
    }
    if len(guidance_naturals) != len(guidance):
        raise _error(
            "/guidance",
            "natural_key",
            "Guidance records collide within one immutable capture",
        )
    if family == "sec" and (actions or earnings_events or expectations or guidance):
        raise _error("/family", "content", "SEC fixtures cannot contain local-capture action or earnings records")
    if family == "actions" and (earnings_events or expectations or guidance):
        raise _error("/family", "content", "Action fixtures cannot contain earnings research records")
    if family == "earnings" and (filings or facts or mappings or actions):
        raise _error("/family", "content", "Earnings fixtures cannot contain SEC or action records")
    payload = _CompanyPayload(
        family=family,
        issuers=tuple(sorted(issuers, key=lambda item: item.cik)),
        filings=tuple(sorted(filings, key=lambda item: item.accession_number)),
        facts=tuple(sorted(facts, key=lambda item: item.source_fact_key)),
        mappings=tuple(
            sorted(
                mappings,
                key=lambda item: (item.mapping_version, item.taxonomy, item.concept, item.unit),
            )
        ),
        actions=tuple(sorted(actions, key=lambda item: item.action_key)),
        earnings_events=tuple(sorted(earnings_events, key=lambda item: item.event_key)),
        expectations=tuple(sorted(expectations, key=lambda item: item.expectation_key)),
        guidance=tuple(sorted(guidance, key=lambda item: item.guidance_key)),
    )
    _validate_payload_references(payload)
    return payload, scope, captured_at, normalization_version


def parse_stage4_company_fixture(fixture: Fixture) -> ParsedCompanyFixture:
    """Parse one reviewed company fixture without opening a database.

    Publication validates the returned semantic identity against the manifest
    pin. Keeping that check outside this parser lets the integration owner
    generate reviewed fixture-manifest fragments from this normalization.
    """

    payload, scope, captured_at, normalization_version = _payload_for_fixture(fixture)
    material = {
        "ingestion_family_id": fixture.ingestion_family_id,
        "canonical_dataset_id": fixture.canonical_dataset_id,
        "provider": fixture.provider,
        "scope": scope,
        "normalization_version": normalization_version,
        "payload": payload.semantic_mapping(),
    }
    semantic_identity = hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()
    return ParsedCompanyFixture(
        fixture=fixture,
        payload=payload,
        scope=scope,
        captured_at=captured_at,
        normalization_version=normalization_version,
        semantic_identity=semantic_identity,
    )


_EXPECTATION_METRICS: Mapping[str, tuple[str, str, str]] = {
    "revenue": ("Revenue", "USD", "currency"),
}


def _sha256_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(dumps_strict(dict(value)).encode("utf-8")).hexdigest()


def _scope_digest(scope: Mapping[str, object]) -> str:
    return _sha256_json(scope)


def _issuer_id(issuer: _Issuer) -> str:
    return stable_id("company_issuer", issuer.cik)


def _issuer_index(payload: _CompanyPayload) -> dict[str, _Issuer]:
    return {issuer.issuer_key: issuer for issuer in payload.issuers}


def _same(row: sqlite3.Row, **expected: object) -> bool:
    return all(row[key] == value for key, value in expected.items())


def _scoped_domain_scope(
    parsed: ParsedCompanyFixture,
    *,
    issuer_id: str,
    provider: str | None = None,
    domain: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "fixture_id": parsed.fixture.id,
        "family": parsed.payload.family,
        "domain": domain,
        "issuer_id": issuer_id,
        "request_scope": dict(parsed.scope),
    }
    if provider is not None:
        result["provider"] = provider
    return result


def _ensure_company_issuer(
    connection: sqlite3.Connection,
    *,
    issuer: _Issuer,
    run_id: str,
) -> int:
    issuer_id = _issuer_id(issuer)
    existing = connection.execute(
        "SELECT issuer_id, cik FROM company_issuers WHERE issuer_id=? OR cik=?",
        (issuer_id, issuer.cik),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO company_issuers (issuer_id, cik, created_run_id) VALUES (?, ?, ?)",
            (issuer_id, issuer.cik, run_id),
        )
        return 1
    if str(existing["issuer_id"]) != issuer_id or str(existing["cik"]) != issuer.cik:
        raise ConflictError("CIK-backed issuer identity conflicts with immutable prior state")
    return 0


def _require_company_issuer(connection: sqlite3.Connection, issuer: _Issuer) -> str:
    issuer_id = _issuer_id(issuer)
    row = connection.execute(
        "SELECT cik FROM company_issuers WHERE issuer_id=?",
        (issuer_id,),
    ).fetchone()
    if row is None or str(row["cik"]) != issuer.cik:
        raise ValidationError("Company fixture refers to an issuer without SEC identity evidence")
    return issuer_id


def _insert_sec_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    run_id: str,
) -> tuple[str, int]:
    scope = _scoped_domain_scope(parsed, issuer_id=issuer_id, domain="sec")
    scope_json = dumps_strict(scope)
    artifact_id = stable_id(
        "company_sec_artifact",
        parsed.fixture.evidence_dataset_id,
        parsed.fixture.sha256,
        issuer_id,
        _scope_digest(scope),
    )
    existing = connection.execute(
        """
        SELECT artifact_id, source_name, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               available_at, available_precision
        FROM company_sec_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    expected = {
        "source_name": parsed.fixture.ingestion_family_id,
        "content_sha256": parsed.fixture.sha256,
        "media_type": "application/json",
        "byte_count": parsed.fixture.byte_count,
        "source_reference": parsed.fixture.resource_name,
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("SEC artifact identity conflicts with immutable prior evidence")
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


def _sec_snapshot_identity(
    parsed: ParsedCompanyFixture,
    *,
    issuer_id: str,
    snapshot_kind: str,
) -> str:
    return _sha256_json(
        {
            "fixture_semantic_identity": parsed.semantic_identity,
            "issuer_id": issuer_id,
            "snapshot_kind": snapshot_kind,
        }
    )


def _insert_sec_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    snapshot_kind: str,
    artifact_id: str,
    run_id: str,
) -> tuple[str, int]:
    scope = _scoped_domain_scope(parsed, issuer_id=issuer_id, domain="sec")
    semantic_identity = _sec_snapshot_identity(
        parsed, issuer_id=issuer_id, snapshot_kind=snapshot_kind
    )
    snapshot_id = stable_id("company_sec_snapshot", semantic_identity)
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, snapshot_kind, artifact_id, scope_json,
               completeness, captured_at, captured_precision, available_at,
               available_precision
        FROM company_sec_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    expected = {
        "semantic_identity": semantic_identity,
        "issuer_id": issuer_id,
        "snapshot_kind": snapshot_kind,
        "artifact_id": artifact_id,
        "scope_json": dumps_strict(scope),
        "completeness": "complete",
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("SEC snapshot identity conflicts with immutable prior evidence")
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
            semantic_identity,
            issuer_id,
            snapshot_kind,
            artifact_id,
            expected["scope_json"],
            "complete",
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            run_id,
        ),
    )
    return snapshot_id, 1


def _ensure_issuer_version(
    connection: sqlite3.Connection,
    *,
    issuer: _Issuer,
    issuer_id: str,
    submission_snapshot_id: str,
    run_id: str,
    source_row: int,
) -> int:
    prior = connection.execute(
        """
        SELECT issuer_version_id, legal_name, entity_type, name_state, missing_reason,
               available_at, available_precision, version_sequence
        FROM company_issuer_versions
        WHERE issuer_id=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id,),
    ).fetchone()
    expected = {
        "legal_name": issuer.legal_name,
        "entity_type": issuer.entity_type,
        "name_state": "present",
        "missing_reason": None,
        "available_at": issuer.available_at.raw,
        "available_precision": issuer.available_at.precision.value,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    version_id = stable_id(
        "company_issuer_version",
        issuer_id,
        str(sequence),
        issuer.available_at.raw or "",
        issuer.legal_name,
    )
    connection.execute(
        """
        INSERT INTO company_issuer_versions (
            issuer_version_id, issuer_id, legal_name, entity_type, name_state,
            missing_reason, available_at, available_precision, version_sequence,
            supersedes_issuer_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, 'present', NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            issuer_id,
            issuer.legal_name,
            issuer.entity_type,
            issuer.available_at.raw,
            issuer.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["issuer_version_id"]),
            submission_snapshot_id,
            run_id,
            source_row,
        ),
    )
    return 1


def _security_identifier_for_link(issuer: _Issuer, link: _IssuerLink) -> str:
    candidates = [
        candidate
        for candidate in issuer.links
        if candidate.link_kind == "security"
        and candidate.valid_from <= link.valid_from
        and (
            candidate.valid_through is None
            or (link.valid_through is not None and candidate.valid_through >= link.valid_through)
        )
    ]
    if len(candidates) != 1:
        raise ValidationError("Ticker/provider-symbol link requires exactly one covering security identity")
    return candidates[0].provider_identifier


def _symbol_links(issuer: _Issuer) -> tuple[tuple[_IssuerLink, str], ...]:
    result = [
        (link, _security_identifier_for_link(issuer, link))
        for link in issuer.links
        if link.link_kind in {"ticker", "provider_symbol"}
    ]
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item[0].provider,
                item[0].provider_identifier,
                item[1],
                item[0].valid_from,
                item[0].valid_through or "",
            ),
        )
    )


def _insert_ticker_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    issuer_id: str,
    link: _IssuerLink,
    security_identifier: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    membership_id = stable_id(
        "company_ticker_membership",
        snapshot_id,
        str(source_row),
        link.provider,
        link.provider_identifier,
        security_identifier,
    )
    expected = {
        "snapshot_id": snapshot_id,
        "issuer_id": issuer_id,
        "provider": link.provider,
        "provider_symbol": link.provider_identifier,
        "security_identifier": security_identifier,
        "valid_from": link.valid_from,
        "valid_through": link.valid_through,
        "source_row": source_row,
    }
    existing = connection.execute(
        """
        SELECT snapshot_id, issuer_id, provider, provider_symbol, security_identifier,
               valid_from, valid_through, source_row
        FROM company_ticker_snapshot_membership
        WHERE ticker_membership_id=?
        """,
        (membership_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Ticker evidence identity conflicts with immutable prior state")
        return membership_id, 0
    conflict = connection.execute(
        """
        SELECT ticker_membership_id
        FROM company_ticker_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("Ticker snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_ticker_snapshot_membership (
            ticker_membership_id, snapshot_id, issuer_id, provider, provider_symbol,
            security_identifier, valid_from, valid_through, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            membership_id,
            snapshot_id,
            issuer_id,
            link.provider,
            link.provider_identifier,
            security_identifier,
            link.valid_from,
            link.valid_through,
            run_id,
            source_row,
        ),
    )
    return membership_id, 1


def _assert_symbol_not_overlapping_other_issuer(
    connection: sqlite3.Connection,
    *,
    issuer_id: str,
    link: _IssuerLink,
) -> None:
    rows = connection.execute(
        """
        SELECT issuer_id, valid_from, valid_through
        FROM company_issuer_security_link_assertions
        WHERE provider=? AND provider_symbol=? AND assertion_state='active'
        """,
        (link.provider, link.provider_identifier),
    )
    for row in rows:
        if str(row["issuer_id"]) == issuer_id:
            continue
        if _interval_overlaps(
            str(row["valid_from"]),
            None if row["valid_through"] is None else str(row["valid_through"]),
            link.valid_from,
            link.valid_through,
        ):
            raise ConflictError("Provider symbol overlaps a different CIK-backed issuer")


def _append_link_assertion(
    connection: sqlite3.Connection,
    *,
    issuer_id: str,
    link: _IssuerLink,
    security_identifier: str,
    ticker_membership_id: str,
    run_id: str,
) -> int:
    _assert_symbol_not_overlapping_other_issuer(connection, issuer_id=issuer_id, link=link)
    prior = connection.execute(
        """
        SELECT link_assertion_id, assertion_state, confidence, available_at,
               available_precision, assertion_sequence
        FROM company_issuer_security_link_assertions
        WHERE issuer_id=? AND provider=? AND provider_symbol=?
          AND security_identifier=? AND valid_from=?
          AND COALESCE(valid_through, '')=COALESCE(?, '')
        ORDER BY assertion_sequence DESC
        LIMIT 1
        """,
        (
            issuer_id,
            link.provider,
            link.provider_identifier,
            security_identifier,
            link.valid_from,
            link.valid_through,
        ),
    ).fetchone()
    expected = {
        "assertion_state": "active",
        "confidence": link.confidence,
        "available_at": link.available_at.raw,
        "available_precision": link.available_at.precision.value,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["assertion_sequence"]) + 1
    assertion_id = stable_id(
        "company_issuer_security_assertion",
        issuer_id,
        link.provider,
        link.provider_identifier,
        security_identifier,
        link.valid_from,
        link.valid_through or "",
        str(sequence),
        link.available_at.raw,
    )
    connection.execute(
        """
        INSERT INTO company_issuer_security_link_assertions (
            link_assertion_id, issuer_id, provider, provider_symbol,
            security_identifier, valid_from, valid_through, assertion_state,
            confidence, available_at, available_precision, assertion_sequence,
            supersedes_link_assertion_id, ticker_membership_id, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assertion_id,
            issuer_id,
            link.provider,
            link.provider_identifier,
            security_identifier,
            link.valid_from,
            link.valid_through,
            link.confidence,
            link.available_at.raw,
            link.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["link_assertion_id"]),
            ticker_membership_id,
            run_id,
        ),
    )
    return 1


def _write_symbol_links(
    connection: sqlite3.Connection,
    *,
    issuer: _Issuer,
    issuer_id: str,
    submission_snapshot_id: str,
    run_id: str,
) -> int:
    written = 0
    for source_row, (link, security_identifier) in enumerate(_symbol_links(issuer), start=1):
        membership_id, appended = _insert_ticker_membership(
            connection,
            snapshot_id=submission_snapshot_id,
            issuer_id=issuer_id,
            link=link,
            security_identifier=security_identifier,
            run_id=run_id,
            source_row=source_row,
        )
        written += appended
        written += _append_link_assertion(
            connection,
            issuer_id=issuer_id,
            link=link,
            security_identifier=security_identifier,
            ticker_membership_id=membership_id,
            run_id=run_id,
        )
    return written


def _metric_id(mapping: _MetricMapping) -> str:
    return stable_id("company_metric", mapping.metric_code)


def _mapping_id(mapping: _MetricMapping) -> str:
    return stable_id(
        "company_metric_mapping",
        _metric_id(mapping),
        mapping.mapping_version,
        mapping.taxonomy,
        mapping.concept,
        mapping.unit,
    )


def _mapping_share_semantics(mapping: _MetricMapping) -> str:
    return mapping.share_semantics if mapping.share_semantics is not None else "not_share"


def _ensure_metric_mapping(
    connection: sqlite3.Connection,
    *,
    mapping: _MetricMapping,
    parsed: ParsedCompanyFixture,
) -> tuple[str, str, int]:
    metric_id = _metric_id(mapping)
    share_semantics = _mapping_share_semantics(mapping)
    definition = connection.execute(
        """
        SELECT display_name, base_unit, share_semantics, definition_version
        FROM company_metric_definitions WHERE metric_id=?
        """,
        (metric_id,),
    ).fetchone()
    definition_expected = {
        "display_name": mapping.metric_label,
        "base_unit": mapping.unit,
        "share_semantics": share_semantics,
        "definition_version": "stage4.company.metric.v1",
    }
    written = 0
    if definition is None:
        connection.execute(
            """
            INSERT INTO company_metric_definitions (
                metric_id, display_name, base_unit, share_semantics,
                definition_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metric_id,
                mapping.metric_label,
                mapping.unit,
                share_semantics,
                "stage4.company.metric.v1",
                parsed.captured_at.raw,
            ),
        )
        written += 1
    elif not _same(definition, **definition_expected):
        raise ConflictError("Metric code conflicts with immutable metric definition")
    mapping_id = _mapping_id(mapping)
    existing = connection.execute(
        """
        SELECT metric_id, mapping_version, taxonomy, concept, unit, mapping_state
        FROM company_metric_mappings WHERE mapping_id=?
        """,
        (mapping_id,),
    ).fetchone()
    mapping_expected = {
        "metric_id": metric_id,
        "mapping_version": mapping.mapping_version,
        "taxonomy": mapping.taxonomy,
        "concept": mapping.concept,
        "unit": mapping.unit,
        "mapping_state": "active",
    }
    if existing is None:
        connection.execute(
            """
            INSERT INTO company_metric_mappings (
                mapping_id, metric_id, mapping_version, taxonomy, concept, unit,
                mapping_state, available_at, available_precision
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                mapping_id,
                metric_id,
                mapping.mapping_version,
                mapping.taxonomy,
                mapping.concept,
                mapping.unit,
                parsed.captured_at.raw,
                parsed.captured_at.precision.value,
            ),
        )
        written += 1
    elif not _same(existing, **mapping_expected):
        raise ConflictError("Mapping identity conflicts with immutable prior lineage")
    return metric_id, mapping_id, written


def _lookup_mapping(
    connection: sqlite3.Connection,
    *,
    fact: _Fact,
) -> tuple[str, str, str]:
    row = connection.execute(
        """
        SELECT mapping.mapping_id, mapping.metric_id, metric.share_semantics
        FROM company_metric_mappings AS mapping
        JOIN company_metric_definitions AS metric ON metric.metric_id=mapping.metric_id
        WHERE mapping.mapping_version=? AND mapping.taxonomy=? AND mapping.concept=?
          AND mapping.unit=? AND mapping.mapping_state='active'
        """,
        (fact.mapping_version, fact.taxonomy, fact.concept, fact.unit),
    ).fetchone()
    if row is None:
        raise ValidationError("SEC fact refers to an unavailable reviewed metric mapping")
    return (
        str(row["metric_id"]),
        str(row["mapping_id"]),
        str(row["share_semantics"]),
    )


def _insert_or_validate_filing(
    connection: sqlite3.Connection,
    *,
    filing: _Filing,
    first_observed_issuer_id: str,
    first_observed_snapshot_id: str,
    run_id: str,
) -> int:
    expected = {
        "first_observed_issuer_id": first_observed_issuer_id,
        "form_type": filing.form,
        "filing_date": filing.filing_date,
        "filing_date_precision": "date",
        "accepted_at": filing.accepted_at.raw,
        "accepted_precision": filing.accepted_at.precision.value,
        "report_period_start": None,
        "report_period_end": filing.report_period,
        "primary_document": filing.primary_document,
        "source_url": filing.source_url,
        "first_observed_snapshot_id": first_observed_snapshot_id,
        "available_at": filing.accepted_at.raw,
        "available_precision": filing.accepted_at.precision.value,
    }
    existing = connection.execute(
        """
        SELECT first_observed_issuer_id, form_type, filing_date,
               filing_date_precision, accepted_at, accepted_precision,
               report_period_start, report_period_end, primary_document, source_url,
               first_observed_snapshot_id, available_at, available_precision
        FROM company_sec_filings WHERE accession_number=?
        """,
        (filing.accession_number,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("SEC accession conflicts with immutable filing metadata")
        return 0
    connection.execute(
        """
        INSERT INTO company_sec_filings (
            accession_number, first_observed_issuer_id, form_type, filing_date,
            filing_date_precision, accepted_at, accepted_precision,
            report_period_start, report_period_end, primary_document, source_url,
            first_observed_snapshot_id, available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, 'date', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filing.accession_number,
            first_observed_issuer_id,
            filing.form,
            filing.filing_date,
            filing.accepted_at.raw,
            filing.accepted_at.precision.value,
            filing.report_period,
            filing.primary_document,
            filing.source_url,
            first_observed_snapshot_id,
            filing.accepted_at.raw,
            filing.accepted_at.precision.value,
            run_id,
        ),
    )
    return 1


def _insert_filing_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    accession_number: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row
        FROM company_sec_filing_snapshot_membership
        WHERE snapshot_id=? AND accession_number=?
        """,
        (snapshot_id, accession_number),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("Filing membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT accession_number
        FROM company_sec_filing_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("Filing snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_sec_filing_snapshot_membership (
            snapshot_id, accession_number, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, accession_number, run_id, source_row),
    )
    return 1


def _append_sec_fact(
    connection: sqlite3.Connection,
    *,
    fact: _Fact,
    issuer_id: str,
    source_snapshot_id: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    prior = connection.execute(
        """
        SELECT fact_version_id, fiscal_year, fiscal_period, reference_period_start,
               filed_at, value_text, value_state, missing_reason, available_at,
               available_precision, source_snapshot_id, version_sequence
        FROM company_sec_fact_versions
        WHERE issuer_id=? AND taxonomy=? AND concept=? AND unit=?
          AND reference_period_end=? AND accession_number=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (
            issuer_id,
            fact.taxonomy,
            fact.concept,
            fact.unit,
            fact.period_end,
            fact.accession_number,
        ),
    ).fetchone()
    expected = {
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "reference_period_start": fact.period_start,
        "filed_at": fact.filed_at,
        "value_text": _decimal_text(fact.value),
        "value_state": "present",
        "missing_reason": None,
        "available_at": fact.accepted_at.raw,
        "available_precision": fact.accepted_at.precision.value,
        "source_snapshot_id": source_snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["fact_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    fact_version_id = stable_id(
        "company_sec_fact_version",
        issuer_id,
        fact.taxonomy,
        fact.concept,
        fact.unit,
        fact.period_end,
        fact.accession_number,
        str(sequence),
        _decimal_text(fact.value),
        fact.accepted_at.raw,
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
            fact.taxonomy,
            fact.concept,
            fact.unit,
            fact.fiscal_year,
            fact.fiscal_period,
            fact.period_start,
            fact.period_end,
            fact.filed_at,
            fact.accession_number,
            _decimal_text(fact.value),
            fact.accepted_at.raw,
            fact.accepted_at.precision.value,
            sequence,
            None if prior is None else str(prior["fact_version_id"]),
            source_snapshot_id,
            run_id,
            source_row,
        ),
    )
    return fact_version_id, 1


def _insert_fact_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    fact_version_id: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_sec_fact_snapshot_membership
        WHERE snapshot_id=? AND fact_version_id=?
        """,
        (snapshot_id, fact_version_id),
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
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("SEC fact snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_sec_fact_snapshot_membership (
            snapshot_id, fact_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, fact_version_id, run_id, source_row),
    )
    return 1


def _append_fundamental(
    connection: sqlite3.Connection,
    *,
    fact: _Fact,
    issuer_id: str,
    metric_id: str,
    mapping_id: str,
    share_semantics: str,
    fact_version_id: str,
    source_snapshot_id: str,
    run_id: str,
    source_row: int,
) -> int:
    prior = connection.execute(
        """
        SELECT fundamental_version_id, mapping_id, share_semantics, fiscal_year,
               fiscal_period, reference_period_start, accession_number,
               source_fact_version_id, value_text, value_state, missing_reason,
               available_at, available_precision, source_snapshot_id,
               version_sequence
        FROM company_fundamental_observation_versions
        WHERE issuer_id=? AND metric_id=? AND reference_period_end=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id, metric_id, fact.period_end),
    ).fetchone()
    expected = {
        "mapping_id": mapping_id,
        "share_semantics": share_semantics,
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "reference_period_start": fact.period_start,
        "accession_number": fact.accession_number,
        "source_fact_version_id": fact_version_id,
        "value_text": _decimal_text(fact.value),
        "value_state": "present",
        "missing_reason": None,
        "available_at": fact.accepted_at.raw,
        "available_precision": fact.accepted_at.precision.value,
        "source_snapshot_id": source_snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    fundamental_id = stable_id(
        "company_fundamental_version",
        issuer_id,
        metric_id,
        fact.period_end,
        str(sequence),
        _decimal_text(fact.value),
        fact.accepted_at.raw,
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
            share_semantics,
            fact.fiscal_year,
            fact.fiscal_period,
            fact.period_start,
            fact.period_end,
            fact.accession_number,
            fact_version_id,
            _decimal_text(fact.value),
            fact.accepted_at.raw,
            fact.accepted_at.precision.value,
            sequence,
            None if prior is None else str(prior["fundamental_version_id"]),
            source_snapshot_id,
            run_id,
            source_row,
        ),
    )
    return 1


def _write_sec_records(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    run_id: str,
) -> int:
    payload = parsed.payload
    issuers = _issuer_index(payload)
    issuer_ids = {key: _issuer_id(issuer) for key, issuer in issuers.items()}
    submission_snapshots: dict[str, str] = {}
    fact_snapshots: dict[str, str] = {}
    fact_issuer_keys = {fact.issuer_key for fact in payload.facts}
    written = 0
    for issuer_key, issuer in sorted(issuers.items(), key=lambda item: item[1].cik):
        issuer_id = issuer_ids[issuer_key]
        written += _ensure_company_issuer(connection, issuer=issuer, run_id=run_id)
        artifact_id, appended = _insert_sec_artifact(
            connection, parsed=parsed, issuer_id=issuer_id, run_id=run_id
        )
        written += appended
        submission_snapshot_id, appended = _insert_sec_snapshot(
            connection,
            parsed=parsed,
            issuer_id=issuer_id,
            snapshot_kind="submissions",
            artifact_id=artifact_id,
            run_id=run_id,
        )
        submission_snapshots[issuer_key] = submission_snapshot_id
        written += appended
        written += _ensure_issuer_version(
            connection,
            issuer=issuer,
            issuer_id=issuer_id,
            submission_snapshot_id=submission_snapshot_id,
            run_id=run_id,
            source_row=1,
        )
        written += _write_symbol_links(
            connection,
            issuer=issuer,
            issuer_id=issuer_id,
            submission_snapshot_id=submission_snapshot_id,
            run_id=run_id,
        )
        if issuer_key in fact_issuer_keys:
            fact_snapshot_id, appended = _insert_sec_snapshot(
                connection,
                parsed=parsed,
                issuer_id=issuer_id,
                snapshot_kind="companyfacts",
                artifact_id=artifact_id,
                run_id=run_id,
            )
            fact_snapshots[issuer_key] = fact_snapshot_id
            written += appended
    for mapping in payload.mappings:
        _, _, appended = _ensure_metric_mapping(connection, mapping=mapping, parsed=parsed)
        written += appended
    for filing in payload.filings:
        written += _insert_or_validate_filing(
            connection,
            filing=filing,
            first_observed_issuer_id=issuer_ids[filing.first_observed_issuer_key],
            first_observed_snapshot_id=submission_snapshots[filing.first_observed_issuer_key],
            run_id=run_id,
        )
        for source_row, issuer_key in enumerate(filing.issuer_keys, start=1):
            written += _insert_filing_membership(
                connection,
                snapshot_id=submission_snapshots[issuer_key],
                accession_number=filing.accession_number,
                run_id=run_id,
                source_row=source_row,
            )
    for source_row, fact in enumerate(payload.facts, start=1):
        issuer_id = issuer_ids[fact.issuer_key]
        source_snapshot_id = fact_snapshots[fact.issuer_key]
        metric_id, mapping_id, share_semantics = _lookup_mapping(connection, fact=fact)
        fact_version_id, appended = _append_sec_fact(
            connection,
            fact=fact,
            issuer_id=issuer_id,
            source_snapshot_id=source_snapshot_id,
            run_id=run_id,
            source_row=source_row,
        )
        written += appended
        if not appended:
            continue
        written += _insert_fact_membership(
            connection,
            snapshot_id=source_snapshot_id,
            fact_version_id=fact_version_id,
            run_id=run_id,
            source_row=source_row,
        )
        written += _append_fundamental(
            connection,
            fact=fact,
            issuer_id=issuer_id,
            metric_id=metric_id,
            mapping_id=mapping_id,
            share_semantics=share_semantics,
            fact_version_id=fact_version_id,
            source_snapshot_id=source_snapshot_id,
            run_id=run_id,
            source_row=source_row,
        )
    return written


def _action_scope(
    parsed: ParsedCompanyFixture,
    *,
    issuer_id: str,
    provider: str,
    actions: Sequence[_Action],
) -> dict[str, object]:
    scope = _scoped_domain_scope(
        parsed,
        issuer_id=issuer_id,
        provider=provider,
        domain="corporate_actions",
    )
    scope["source_urls"] = sorted(action.source_url for action in actions)
    return scope


def _insert_action_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    provider: str,
    actions: Sequence[_Action],
    run_id: str,
) -> tuple[str, int]:
    scope = _action_scope(
        parsed, issuer_id=issuer_id, provider=provider, actions=actions
    )
    scope_json = dumps_strict(scope)
    artifact_id = stable_id(
        "company_action_artifact",
        parsed.fixture.evidence_dataset_id,
        parsed.fixture.sha256,
        issuer_id,
        provider,
        _scope_digest(scope),
    )
    existing = connection.execute(
        """
        SELECT issuer_id, provider, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               available_at, available_precision
        FROM company_action_source_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    expected = {
        "issuer_id": issuer_id,
        "provider": provider,
        "content_sha256": parsed.fixture.sha256,
        "media_type": "application/json",
        "byte_count": parsed.fixture.byte_count,
        "source_reference": parsed.fixture.resource_name,
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Corporate-action artifact conflicts with immutable prior evidence")
        return artifact_id, 0
    connection.execute(
        """
        INSERT INTO company_action_source_artifacts (
            artifact_id, issuer_id, provider, content_sha256, media_type, byte_count,
            source_reference, request_scope_json, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            issuer_id,
            provider,
            parsed.fixture.sha256,
            "application/json",
            parsed.fixture.byte_count,
            parsed.fixture.resource_name,
            scope_json,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            run_id,
        ),
    )
    return artifact_id, 1


def _insert_action_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    provider: str,
    actions: Sequence[_Action],
    artifact_id: str,
    run_id: str,
) -> tuple[str, int]:
    scope = _action_scope(
        parsed, issuer_id=issuer_id, provider=provider, actions=actions
    )
    semantic_identity = _sha256_json(
        {
            "fixture_semantic_identity": parsed.semantic_identity,
            "issuer_id": issuer_id,
            "provider": provider,
            "domain": "corporate_actions",
        }
    )
    snapshot_id = stable_id("company_action_snapshot", semantic_identity)
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, provider, artifact_id, scope_json,
               completeness, tombstone_authoritative, captured_at, captured_precision,
               available_at, available_precision
        FROM company_action_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    expected = {
        "semantic_identity": semantic_identity,
        "issuer_id": issuer_id,
        "provider": provider,
        "artifact_id": artifact_id,
        "scope_json": dumps_strict(scope),
        "completeness": "complete",
        "tombstone_authoritative": 0,
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Corporate-action snapshot conflicts with immutable prior evidence")
        return snapshot_id, 0
    connection.execute(
        """
        INSERT INTO company_action_snapshots (
            snapshot_id, semantic_identity, issuer_id, provider, artifact_id,
            scope_json, completeness, tombstone_authoritative, captured_at,
            captured_precision, available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, 'complete', 0, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            semantic_identity,
            issuer_id,
            provider,
            artifact_id,
            expected["scope_json"],
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            run_id,
        ),
    )
    return snapshot_id, 1


def _action_values(action: _Action) -> tuple[float | None, str | None, float | None, float | None]:
    if action.action_type == "cash_dividend":
        return (
            float(Decimal(action.terms["cash_amount"])),
            action.terms["currency"],
            None,
            None,
        )
    return (
        None,
        None,
        float(Decimal(action.terms["denominator"])),
        float(Decimal(action.terms["numerator"])),
    )


def _append_action(
    connection: sqlite3.Connection,
    *,
    action: _Action,
    issuer_id: str,
    snapshot_id: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    cash_amount, currency, split_from, split_to = _action_values(action)
    prior = connection.execute(
        """
        SELECT action_version_id, provider_symbol, action_kind, event_date,
               record_date, pay_date, declared_date, cash_amount, currency,
               split_from_quantity, split_to_quantity, action_state, missing_reason,
               available_at, available_precision, source_snapshot_id,
               version_sequence
        FROM company_corporate_action_versions
        WHERE issuer_id=? AND provider=? AND provider_event_id=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id, action.provider, action.action_key),
    ).fetchone()
    expected = {
        "provider_symbol": action.provider_symbol,
        "action_kind": action.action_type,
        "event_date": action.event_date,
        "record_date": None,
        "pay_date": None,
        "declared_date": None,
        "cash_amount": cash_amount,
        "currency": currency,
        "split_from_quantity": split_from,
        "split_to_quantity": split_to,
        "action_state": "active",
        "missing_reason": None,
        "available_at": action.available_at.raw,
        "available_precision": action.available_at.precision.value,
        "source_snapshot_id": snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["action_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    action_version_id = stable_id(
        "company_corporate_action_version",
        issuer_id,
        action.provider,
        action.action_key,
        str(sequence),
        action.action_type,
        action.event_date,
        action.available_at.raw,
    )
    connection.execute(
        """
        INSERT INTO company_corporate_action_versions (
            action_version_id, issuer_id, provider, provider_symbol,
            provider_event_id, action_kind, event_date, record_date, pay_date,
            declared_date, cash_amount, currency, split_from_quantity,
            split_to_quantity, action_state, missing_reason, available_at,
            available_precision, version_sequence, supersedes_action_version_id,
            source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, 'active',
                  NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_version_id,
            issuer_id,
            action.provider,
            action.provider_symbol,
            action.action_key,
            action.action_type,
            action.event_date,
            cash_amount,
            currency,
            split_from,
            split_to,
            action.available_at.raw,
            action.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["action_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return action_version_id, 1


def _insert_action_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    action_version_id: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_action_snapshot_membership
        WHERE snapshot_id=? AND action_version_id=?
        """,
        (snapshot_id, action_version_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("Corporate-action membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT action_version_id FROM company_action_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("Corporate-action snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_action_snapshot_membership (
            snapshot_id, action_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, action_version_id, run_id, source_row),
    )
    return 1


def _write_action_records(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    run_id: str,
) -> int:
    issuers = _issuer_index(parsed.payload)
    grouped: dict[tuple[str, str], list[_Action]] = {}
    for action in parsed.payload.actions:
        grouped.setdefault((action.issuer_key, action.provider), []).append(action)
    written = 0
    for (issuer_key, provider), actions in sorted(grouped.items()):
        issuer = issuers[issuer_key]
        issuer_id = _require_company_issuer(connection, issuer)
        ordered_actions = tuple(sorted(actions, key=lambda item: item.action_key))
        artifact_id, appended = _insert_action_artifact(
            connection,
            parsed=parsed,
            issuer_id=issuer_id,
            provider=provider,
            actions=ordered_actions,
            run_id=run_id,
        )
        written += appended
        snapshot_id, appended = _insert_action_snapshot(
            connection,
            parsed=parsed,
            issuer_id=issuer_id,
            provider=provider,
            actions=ordered_actions,
            artifact_id=artifact_id,
            run_id=run_id,
        )
        written += appended
        for source_row, action in enumerate(ordered_actions, start=1):
            action_version_id, appended = _append_action(
                connection,
                action=action,
                issuer_id=issuer_id,
                snapshot_id=snapshot_id,
                run_id=run_id,
                source_row=source_row,
            )
            written += appended
            if appended:
                written += _insert_action_membership(
                    connection,
                    snapshot_id=snapshot_id,
                    action_version_id=action_version_id,
                    run_id=run_id,
                    source_row=source_row,
                )
    return written


def _ensure_expectation_metric(
    connection: sqlite3.Connection,
    *,
    metric_code: str,
    parsed: ParsedCompanyFixture,
) -> tuple[str, int]:
    try:
        display_name, base_unit, value_kind = _EXPECTATION_METRICS[metric_code]
    except KeyError as exc:
        raise ValidationError("Earnings record uses an unsupported reviewed metric") from exc
    metric_id = stable_id("company_expectation_metric", metric_code)
    existing = connection.execute(
        """
        SELECT display_name, base_unit, value_kind, definition_version
        FROM company_expectation_metric_definitions
        WHERE expectation_metric_id=?
        """,
        (metric_id,),
    ).fetchone()
    expected = {
        "display_name": display_name,
        "base_unit": base_unit,
        "value_kind": value_kind,
        "definition_version": "stage4.company.expectation_metric.v1",
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Expectation metric conflicts with immutable definition")
        return metric_id, 0
    connection.execute(
        """
        INSERT INTO company_expectation_metric_definitions (
            expectation_metric_id, display_name, base_unit, value_kind,
            definition_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            metric_id,
            display_name,
            base_unit,
            value_kind,
            "stage4.company.expectation_metric.v1",
            parsed.captured_at.raw,
        ),
    )
    return metric_id, 1


def _earnings_scope(
    parsed: ParsedCompanyFixture,
    *,
    issuer_id: str,
    provider: str,
    guidance: Sequence[_Guidance],
) -> dict[str, object]:
    scope = _scoped_domain_scope(
        parsed,
        issuer_id=issuer_id,
        provider=provider,
        domain="earnings",
    )
    scope["guidance_source_urls"] = sorted(item.source_url for item in guidance)
    return scope


def _insert_earnings_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    provider: str,
    guidance: Sequence[_Guidance],
    run_id: str,
) -> tuple[str, int]:
    scope = _earnings_scope(
        parsed,
        issuer_id=issuer_id,
        provider=provider,
        guidance=guidance,
    )
    scope_json = dumps_strict(scope)
    artifact_id = stable_id(
        "company_earnings_artifact",
        parsed.fixture.evidence_dataset_id,
        parsed.fixture.sha256,
        issuer_id,
        provider,
        _scope_digest(scope),
    )
    existing = connection.execute(
        """
        SELECT issuer_id, provider, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               available_at, available_precision
        FROM company_earnings_source_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    expected = {
        "issuer_id": issuer_id,
        "provider": provider,
        "content_sha256": parsed.fixture.sha256,
        "media_type": "application/json",
        "byte_count": parsed.fixture.byte_count,
        "source_reference": parsed.fixture.resource_name,
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Earnings artifact conflicts with immutable prior evidence")
        return artifact_id, 0
    connection.execute(
        """
        INSERT INTO company_earnings_source_artifacts (
            artifact_id, issuer_id, provider, content_sha256, media_type, byte_count,
            source_reference, request_scope_json, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            issuer_id,
            provider,
            parsed.fixture.sha256,
            "application/json",
            parsed.fixture.byte_count,
            parsed.fixture.resource_name,
            scope_json,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            run_id,
        ),
    )
    return artifact_id, 1


def _insert_earnings_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    issuer_id: str,
    provider: str,
    guidance: Sequence[_Guidance],
    snapshot_kind: str,
    artifact_id: str,
    run_id: str,
) -> tuple[str, int]:
    scope = _earnings_scope(
        parsed,
        issuer_id=issuer_id,
        provider=provider,
        guidance=guidance,
    )
    semantic_identity = _sha256_json(
        {
            "fixture_semantic_identity": parsed.semantic_identity,
            "issuer_id": issuer_id,
            "provider": provider,
            "snapshot_kind": snapshot_kind,
        }
    )
    snapshot_id = stable_id("company_earnings_snapshot", semantic_identity)
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, provider, snapshot_kind, artifact_id,
               scope_json, completeness, captured_at, captured_precision,
               available_at, available_precision
        FROM company_earnings_source_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    expected = {
        "semantic_identity": semantic_identity,
        "issuer_id": issuer_id,
        "provider": provider,
        "snapshot_kind": snapshot_kind,
        "artifact_id": artifact_id,
        "scope_json": dumps_strict(scope),
        "completeness": "complete",
        "captured_at": parsed.captured_at.raw,
        "captured_precision": parsed.captured_at.precision.value,
        "available_at": parsed.captured_at.raw,
        "available_precision": parsed.captured_at.precision.value,
    }
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("Earnings snapshot conflicts with immutable prior evidence")
        return snapshot_id, 0
    connection.execute(
        """
        INSERT INTO company_earnings_source_snapshots (
            snapshot_id, semantic_identity, issuer_id, provider, snapshot_kind,
            artifact_id, scope_json, completeness, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            semantic_identity,
            issuer_id,
            provider,
            snapshot_kind,
            artifact_id,
            expected["scope_json"],
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            run_id,
        ),
    )
    return snapshot_id, 1


def _append_consensus(
    connection: sqlite3.Connection,
    *,
    expectation: _Expectation,
    issuer_id: str,
    metric_id: str,
    snapshot_id: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    prior = connection.execute(
        """
        SELECT consensus_version_id, fiscal_year, fiscal_period,
               reference_period_start, value, value_state, missing_reason,
               available_at, available_precision, source_snapshot_id,
               version_sequence
        FROM company_consensus_observation_versions
        WHERE issuer_id=? AND expectation_metric_id=? AND reference_period_end=?
          AND statistic_kind='mean'
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id, metric_id, expectation.fiscal_period_end),
    ).fetchone()
    expected = {
        "fiscal_year": None,
        "fiscal_period": None,
        "reference_period_start": None,
        "value": float(expectation.value),
        "value_state": "present",
        "missing_reason": None,
        "available_at": expectation.available_at.raw,
        "available_precision": expectation.available_at.precision.value,
        "source_snapshot_id": snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["consensus_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    consensus_id = stable_id(
        "company_consensus_version",
        issuer_id,
        metric_id,
        expectation.fiscal_period_end,
        "mean",
        str(sequence),
        _decimal_text(expectation.value),
        expectation.available_at.raw,
    )
    connection.execute(
        """
        INSERT INTO company_consensus_observation_versions (
            consensus_version_id, issuer_id, expectation_metric_id, fiscal_year,
            fiscal_period, reference_period_start, reference_period_end,
            statistic_kind, value, value_state, missing_reason, available_at,
            available_precision, version_sequence,
            supersedes_consensus_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, 'mean', ?, 'present', NULL,
                  ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            consensus_id,
            issuer_id,
            metric_id,
            expectation.fiscal_period_end,
            float(expectation.value),
            expectation.available_at.raw,
            expectation.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["consensus_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return consensus_id, 1


def _insert_consensus_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    consensus_id: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_consensus_snapshot_membership
        WHERE snapshot_id=? AND consensus_version_id=?
        """,
        (snapshot_id, consensus_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("Consensus membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT consensus_version_id FROM company_consensus_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("Consensus snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_consensus_snapshot_membership (
            snapshot_id, consensus_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, consensus_id, run_id, source_row),
    )
    return 1


def _append_earnings_event(
    connection: sqlite3.Connection,
    *,
    event: _EarningsEvent,
    issuer_id: str,
    snapshot_id: str,
    run_id: str,
    source_row: int,
) -> tuple[str, int]:
    prior = connection.execute(
        """
        SELECT event_version_id, event_state, event_at, event_precision,
               fiscal_year, fiscal_period, reference_period_end, missing_reason,
               available_at, available_precision, source_snapshot_id,
               version_sequence
        FROM company_earnings_event_versions
        WHERE issuer_id=? AND provider=? AND provider_event_key=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id, event.provider, event.event_key),
    ).fetchone()
    expected = {
        "event_state": event.status,
        "event_at": event.event_date,
        "event_precision": "date",
        "fiscal_year": None,
        "fiscal_period": None,
        "reference_period_end": event.fiscal_period_end,
        "missing_reason": None,
        "available_at": event.available_at.raw,
        "available_precision": event.available_at.precision.value,
        "source_snapshot_id": snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["event_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    event_version_id = stable_id(
        "company_earnings_event_version",
        issuer_id,
        event.provider,
        event.event_key,
        str(sequence),
        event.status,
        event.event_date,
        event.available_at.raw,
    )
    connection.execute(
        """
        INSERT INTO company_earnings_event_versions (
            event_version_id, issuer_id, provider, provider_event_key, event_state,
            event_at, event_precision, fiscal_year, fiscal_period,
            reference_period_end, missing_reason, available_at,
            available_precision, version_sequence, supersedes_event_version_id,
            source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, 'date', NULL, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_version_id,
            issuer_id,
            event.provider,
            event.event_key,
            event.status,
            event.event_date,
            event.fiscal_period_end,
            event.available_at.raw,
            event.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["event_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return event_version_id, 1


def _insert_earnings_event_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    event_version_id: str,
    run_id: str,
    source_row: int,
) -> int:
    existing = connection.execute(
        """
        SELECT source_row FROM company_earnings_event_snapshot_membership
        WHERE snapshot_id=? AND event_version_id=?
        """,
        (snapshot_id, event_version_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("Earnings-event membership conflicts with immutable source ordering")
        return 0
    conflict = connection.execute(
        """
        SELECT event_version_id FROM company_earnings_event_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflict is not None:
        raise ConflictError("Earnings-event snapshot source-row identity conflicts with prior state")
    connection.execute(
        """
        INSERT INTO company_earnings_event_snapshot_membership (
            snapshot_id, event_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, event_version_id, run_id, source_row),
    )
    return 1


def _append_guidance(
    connection: sqlite3.Connection,
    *,
    guidance: _Guidance,
    issuer_id: str,
    metric_id: str,
    snapshot_id: str,
    run_id: str,
    source_row: int,
) -> int:
    prior = connection.execute(
        """
        SELECT guidance_version_id, accession_number, source_url,
               target_period_start, guidance_shape, point_value, low_value,
               high_value, narrative, missing_reason, review_state, available_at,
               available_precision, source_snapshot_id, version_sequence
        FROM company_guidance_versions
        WHERE issuer_id=? AND expectation_metric_id=? AND target_period_end=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (issuer_id, metric_id, guidance.target_period_end),
    ).fetchone()
    expected = {
        "accession_number": guidance.accession_number,
        "source_url": guidance.source_url,
        "target_period_start": None,
        "guidance_shape": guidance.value_shape,
        "point_value": None if guidance.value_point is None else float(guidance.value_point),
        "low_value": None if guidance.value_low is None else float(guidance.value_low),
        "high_value": None if guidance.value_high is None else float(guidance.value_high),
        "narrative": None,
        "missing_reason": None,
        "review_state": guidance.review_state,
        "available_at": guidance.available_at.raw,
        "available_precision": guidance.available_at.precision.value,
        "source_snapshot_id": snapshot_id,
    }
    if prior is not None and _same(prior, **expected):
        return 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    guidance_version_id = stable_id(
        "company_guidance_version",
        issuer_id,
        metric_id,
        guidance.target_period_end,
        str(sequence),
        guidance.value_shape,
        guidance.source_url,
        guidance.available_at.raw,
    )
    connection.execute(
        """
        INSERT INTO company_guidance_versions (
            guidance_version_id, issuer_id, expectation_metric_id, accession_number,
            source_url, target_period_start, target_period_end, guidance_shape,
            point_value, low_value, high_value, narrative, missing_reason,
            review_state, available_at, available_precision, version_sequence,
            supersedes_guidance_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guidance_version_id,
            issuer_id,
            metric_id,
            guidance.accession_number,
            guidance.source_url,
            guidance.target_period_end,
            guidance.value_shape,
            None if guidance.value_point is None else float(guidance.value_point),
            None if guidance.value_low is None else float(guidance.value_low),
            None if guidance.value_high is None else float(guidance.value_high),
            guidance.review_state,
            guidance.available_at.raw,
            guidance.available_at.precision.value,
            sequence,
            None if prior is None else str(prior["guidance_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return 1


def _write_earnings_records(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedCompanyFixture,
    run_id: str,
) -> int:
    payload = parsed.payload
    issuers = _issuer_index(payload)
    metric_ids: dict[str, str] = {}
    written = 0
    metric_codes = sorted(
        {
            item.metric_code
            for item in (*payload.expectations, *payload.guidance)
        }
    )
    for metric_code in metric_codes:
        metric_id, appended = _ensure_expectation_metric(
            connection, metric_code=metric_code, parsed=parsed
        )
        metric_ids[metric_code] = metric_id
        written += appended
    grouped: dict[tuple[str, str], dict[str, list[object]]] = {}
    for event in payload.earnings_events:
        grouped.setdefault((event.issuer_key, event.provider), {}).setdefault("events", []).append(event)
    for expectation in payload.expectations:
        grouped.setdefault((expectation.issuer_key, expectation.provider), {}).setdefault(
            "consensus", []
        ).append(expectation)
    for item in payload.guidance:
        grouped.setdefault((item.issuer_key, item.provider), {}).setdefault("guidance", []).append(item)
    for (issuer_key, provider), records in sorted(grouped.items()):
        issuer_id = _require_company_issuer(connection, issuers[issuer_key])
        guidance = tuple(sorted(records.get("guidance", []), key=lambda item: item.guidance_key))
        artifact_id, appended = _insert_earnings_artifact(
            connection,
            parsed=parsed,
            issuer_id=issuer_id,
            provider=provider,
            guidance=guidance,
            run_id=run_id,
        )
        written += appended
        snapshots: dict[str, str] = {}
        for kind, record_kind in (
            ("consensus", "consensus"),
            ("earnings_event", "events"),
            ("guidance", "guidance"),
        ):
            if record_kind not in records:
                continue
            snapshot_id, appended = _insert_earnings_snapshot(
                connection,
                parsed=parsed,
                issuer_id=issuer_id,
                provider=provider,
                guidance=guidance,
                snapshot_kind=kind,
                artifact_id=artifact_id,
                run_id=run_id,
            )
            snapshots[kind] = snapshot_id
            written += appended
        for source_row, expectation in enumerate(
            sorted(records.get("consensus", []), key=lambda item: item.expectation_key),
            start=1,
        ):
            consensus_id, appended = _append_consensus(
                connection,
                expectation=expectation,
                issuer_id=issuer_id,
                metric_id=metric_ids[expectation.metric_code],
                snapshot_id=snapshots["consensus"],
                run_id=run_id,
                source_row=source_row,
            )
            written += appended
            if appended:
                written += _insert_consensus_membership(
                    connection,
                    snapshot_id=snapshots["consensus"],
                    consensus_id=consensus_id,
                    run_id=run_id,
                    source_row=source_row,
                )
        for source_row, event in enumerate(
            sorted(records.get("events", []), key=lambda item: item.event_key),
            start=1,
        ):
            event_version_id, appended = _append_earnings_event(
                connection,
                event=event,
                issuer_id=issuer_id,
                snapshot_id=snapshots["earnings_event"],
                run_id=run_id,
                source_row=source_row,
            )
            written += appended
            if appended:
                written += _insert_earnings_event_membership(
                    connection,
                    snapshot_id=snapshots["earnings_event"],
                    event_version_id=event_version_id,
                    run_id=run_id,
                    source_row=source_row,
                )
        for source_row, item in enumerate(guidance, start=1):
            written += _append_guidance(
                connection,
                guidance=item,
                issuer_id=issuer_id,
                metric_id=metric_ids[item.metric_code],
                snapshot_id=snapshots["guidance"],
                run_id=run_id,
                source_row=source_row,
            )
    return written


_OUTPUT_DATASET_IDS: Mapping[str, tuple[str, ...]] = {
    "sec": (
        "fixture.company.sec_evidence",
        "fixture.company.issuers",
        "fixture.company.filings",
        "fixture.company.fundamentals",
        "fixture.company.filing_issuer_membership",
    ),
    "actions": (
        "fixture.company.action_evidence",
        "fixture.company.corporate_actions",
    ),
    "earnings": (
        "fixture.company.expectation_evidence",
        "fixture.company.expectations",
    ),
}


def _preflight_publication(parsed: ParsedCompanyFixture) -> None:
    if parsed.semantic_identity != parsed.fixture.expected_semantic_identity:
        raise ValidationError("Company fixture semantic identity does not match the reviewed manifest")
    family = parsed.payload.family
    if family not in _OUTPUT_DATASET_IDS:
        raise ValidationError("Unsupported company fixture family")
    if family == "actions" and any(
        action.provider != _FAMILY_SPECS[family].provider
        for action in parsed.payload.actions
    ):
        raise ValidationError("Corporate-action provider does not match its reviewed fixture family")
    if family == "earnings":
        records = (*parsed.payload.earnings_events, *parsed.payload.expectations, *parsed.payload.guidance)
        if any(record.provider != _FAMILY_SPECS[family].provider for record in records):
            raise ValidationError("Earnings provider does not match its reviewed fixture family")
        for expectation in parsed.payload.expectations:
            definition = _EXPECTATION_METRICS.get(expectation.metric_code)
            if definition is None or expectation.currency != definition[1]:
                raise ValidationError("Consensus expectation has an unsupported reviewed metric or unit")
        for guidance in parsed.payload.guidance:
            definition = _EXPECTATION_METRICS.get(guidance.metric_code)
            if definition is None or guidance.currency != definition[1]:
                raise ValidationError("Guidance has an unsupported reviewed metric or unit")


def _fetched_count(payload: _CompanyPayload) -> int:
    return sum(
        (
            len(payload.issuers),
            len(payload.filings),
            len(payload.facts),
            len(payload.mappings),
            len(payload.actions),
            len(payload.earnings_events),
            len(payload.expectations),
            len(payload.guidance),
        )
    )


class CompanyStage4FixtureImporter:
    """Publish one complete manifest-verified offline Stage 4 company capture."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map, code_version="stage4.0.0")
        self._prepared_owner_token = object()

    def prepare_fixture(self, fixture_id: str) -> _PreparedCompanyFixture:
        """Validate and stage one reviewed company fixture without opening a store."""

        fixture = self._fixture_manifest.get(fixture_id)
        parsed = parse_stage4_company_fixture(fixture)
        _preflight_publication(parsed)
        family = parsed.payload.family
        outputs = _OUTPUT_DATASET_IDS[family]
        scope = {
            "fixture_id": fixture.id,
            "family": family,
            "collector_id": fixture.ingestion_family_id,
            "request_scope": dict(parsed.scope),
            "completeness": "complete",
        }
        run_id = stable_id(
            "stage4_company_run",
            fixture.canonical_dataset_id,
            parsed.semantic_identity,
        )
        return _new_prepared_company_fixture(
            _PreparedCompanyFixtureState(
                owner_token=self._prepared_owner_token,
                store_map=self._store_map,
                fixture_manifest=self._fixture_manifest,
                fixture_id=fixture.id,
                fixture_sha256=fixture.sha256,
                fixture_byte_count=fixture.byte_count,
                collector_id=fixture.ingestion_family_id,
                evidence_dataset_id=fixture.evidence_dataset_id,
                canonical_dataset_id=fixture.canonical_dataset_id,
                identity_dataset_id=fixture.identity_dataset_id,
                store=fixture.store,
                fixture=fixture,
                parsed=parsed,
                family=family,
                output_dataset_ids=outputs,
                scope=scope,
                semantic_identity=parsed.semantic_identity,
                run_id=run_id,
            )
        )

    def _prepared_state(
        self,
        prepared: _PreparedCompanyFixture,
    ) -> _PreparedCompanyFixtureState:
        if not isinstance(prepared, _PreparedCompanyFixture):
            raise ValidationError("Prepared company fixture has an invalid type")
        state = _PREPARED_COMPANY_STATES.get(prepared)
        if (
            state is None
            or state.owner_token is not self._prepared_owner_token
            or state.store_map is not self._store_map
            or state.fixture_manifest is not self._fixture_manifest
            or state.fixture.id != state.fixture_id
            or state.fixture.sha256 != state.fixture_sha256
            or state.fixture.byte_count != state.fixture_byte_count
            or state.fixture.ingestion_family_id != state.collector_id
            or state.fixture.evidence_dataset_id != state.evidence_dataset_id
            or state.fixture.canonical_dataset_id != state.canonical_dataset_id
            or state.fixture.identity_dataset_id != state.identity_dataset_id
            or state.fixture.store != state.store
            or state.store != StoreRole.COMPANY.value
            or state.parsed.fixture is not state.fixture
            or state.parsed.payload.family != state.family
            or state.family not in _OUTPUT_DATASET_IDS
            or state.output_dataset_ids != _OUTPUT_DATASET_IDS[state.family]
            or state.parsed.semantic_identity != state.semantic_identity
            or state.semantic_identity != state.fixture.expected_semantic_identity
        ):
            raise ValidationError("Prepared company fixture is not valid for this importer")
        return state

    def publish_prepared(
        self,
        prepared: _PreparedCompanyFixture,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Publish a prior validated company candidate without parsing or fetching."""

        state = self._prepared_state(prepared)
        parsed = state.parsed

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(connection, parsed, active_run_id)

        return self._coordinator.execute(
            role=StoreRole.COMPANY,
            dataset_id=state.canonical_dataset_id,
            output_dataset_ids=state.output_dataset_ids,
            semantic_identity=state.semantic_identity,
            run_id=state.run_id,
            command=state.collector_id,
            scope=dict(state.scope),
            started_at=parsed.captured_at.raw,
            completed_at=parsed.captured_at.raw,
            fetched_count=_fetched_count(parsed.payload),
            writer=writer,
            held_locks=held_locks,
        )

    def import_fixture(
        self,
        fixture_id: str,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        return self.publish_prepared(
            self.prepare_fixture(fixture_id),
            held_locks=held_locks,
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        parsed: ParsedCompanyFixture,
        run_id: str,
    ) -> WriteResult:
        payload = parsed.payload
        if payload.family in {"sec", "actions"}:
            written = _write_sec_records(connection, parsed=parsed, run_id=run_id)
        else:
            written = 0
        if payload.actions:
            written += _write_action_records(connection, parsed=parsed, run_id=run_id)
        if payload.earnings_events or payload.expectations or payload.guidance:
            written += _write_earnings_records(connection, parsed=parsed, run_id=run_id)
        artifact_id = stable_id(
            "stage4_company_control_artifact",
            parsed.fixture.evidence_dataset_id,
            parsed.fixture.sha256,
            parsed.semantic_identity,
        )
        snapshot_id = stable_id(
            "stage4_company_control_snapshot",
            parsed.fixture.canonical_dataset_id,
            parsed.semantic_identity,
        )
        artifact = ArtifactWrite(
            artifact_id=artifact_id,
            dataset_id=parsed.fixture.evidence_dataset_id,
            content_sha256=parsed.fixture.sha256,
            media_type="application/json",
            byte_count=parsed.fixture.byte_count,
            source_reference=parsed.fixture.resource_name,
            request_scope=dict(parsed.scope),
            captured_at=parsed.captured_at.raw,
            captured_precision=parsed.captured_at.precision.value,
            normalization_version=parsed.normalization_version,
        )
        return WriteResult(
            written_count=written,
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=parsed.fixture.canonical_dataset_id,
                semantic_identity=parsed.semantic_identity,
                scope=dict(parsed.scope),
                completeness="complete",
                row_count=_fetched_count(payload),
                captured_at=parsed.captured_at.raw,
                captured_precision=parsed.captured_at.precision.value,
                validation_state="validated",
                artifact_ids=(artifact_id,),
                warnings=parsed.fixture.expected_warnings,
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage4_company_fixture_quality",
                        run_id,
                        parsed.fixture.canonical_dataset_id,
                    ),
                    dataset_id=parsed.fixture.canonical_dataset_id,
                    rule_id="fixture.batch_contract",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "family": payload.family,
                        "fetched_count": _fetched_count(payload),
                        "written_count": written,
                        "completeness": "complete",
                    },
                ),
            ),
            warnings=parsed.fixture.expected_warnings,
        )


__all__ = (
    "CompanyStage4FixtureImporter",
    "ParsedCompanyFixture",
    "parse_stage4_company_fixture",
)
