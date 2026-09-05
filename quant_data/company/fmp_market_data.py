"""Bounded FMP corporate-action and analyst-estimate normalization.

The host owns network, credentials, and durable raw-byte retention. This module
accepts one saved response at a time and has no network path. Each endpoint is
parsed and published independently so an unavailable sibling cannot suppress
valid data from another endpoint.

FMP responses used here do not state currency. Cash dividends therefore use
SOURCE_UNSPECIFIED and estimate metrics use provider-specific
source-unspecified units. These values are explicitly distinct from USD.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import PurePosixPath
import re
import sqlite3
from typing import Any, Final, Literal

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue


FMP_COMPANY_CORPORATE_ACTIONS_COLLECTOR_ID: Final = "fmp.company.corporate_actions_current"
FMP_COMPANY_ANALYST_ESTIMATES_COLLECTOR_ID: Final = "fmp.company.analyst_estimates_current"

# Existing Stage 4 private datasets. Registry ownership remains with the
# integration owner; this module never changes registry declarations.
FMP_COMPANY_ACTION_EVIDENCE_DATASET_ID: Final = "fixture.company.action_evidence"
FMP_COMPANY_CORPORATE_ACTIONS_DATASET_ID: Final = "fixture.company.corporate_actions"
FMP_COMPANY_EXPECTATION_EVIDENCE_DATASET_ID: Final = "fixture.company.expectation_evidence"
FMP_COMPANY_EXPECTATIONS_DATASET_ID: Final = "fixture.company.expectations"

FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION: Final = "fmp_company_market_data.v1"
MAX_FMP_RESPONSE_BYTES: Final = 1 * 1024 * 1024
MAX_FMP_ACTION_ROWS: Final = 1_000
MAX_FMP_ANALYST_ESTIMATE_ROWS: Final = 10
MAX_TEXT_LENGTH: Final = 1_024
_SOURCE_UNSPECIFIED_CURRENCY: Final = "SOURCE_UNSPECIFIED"
_SOURCE_UNSPECIFIED_UNIT: Final = "fmp_source_currency_unspecified"
_SOURCE_UNSPECIFIED_PER_SHARE_UNIT: Final = "fmp_source_currency_unspecified_per_share"
_SOURCE_UNSPECIFIED_WARNING: Final = "fmp_source_currency_unspecified"
_EMPTY_RESPONSE_WARNING: Final = "fmp_empty_success_response_no_tombstone"
_ACTION_LIMIT_WARNING: Final = "fmp_corporate_actions_may_be_truncated_at_limit_1000"
_ANALYST_LIMIT_WARNING: Final = "fmp_analyst_estimates_may_be_truncated_at_limit_10"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

SourceName = Literal["dividends", "splits", "analyst_estimates"]


def _fail(message: str) -> ValidationError:
    return ValidationError(f"FMP company market data {message}")


def _text(value: object, label: str, *, maximum: int = MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _fail(f"{label} is invalid")
    return value.strip()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(f"{label} is invalid")
    return value


def _date_text(value: object, label: str) -> str:
    text = _text(value, label, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise _fail(f"{label} is not an ISO calendar date") from exc
    if parsed.isoformat() != text:
        raise _fail(f"{label} is not an ISO calendar date")
    return text


def _optional_date(value: object, label: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _date_text(value, label)


def _decimal(value: object, label: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise _fail(f"{label} is not a finite number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"{label} is not a finite number") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise _fail(f"{label} is not a finite number")
    if not math.isfinite(float(parsed)):
        raise _fail(f"{label} exceeds the supported numeric range")
    return Decimal("0") if parsed.is_zero() else parsed.normalize()


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():  # pragma: no cover - parser protects this
        raise _fail("numeric value is not finite")
    text = format(value, "f")
    return "0" if text in {"-0", "-0.0"} else text


def _body_bytes(body: bytes | str) -> bytes:
    if isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raise _fail("response body must be bytes or text")
    if not raw:
        raise _fail("response body is empty")
    if len(raw) > MAX_FMP_RESPONSE_BYTES:
        raise ResourceLimitError("FMP company response exceeds the byte bound")
    return raw


def _source_reference(value: object) -> str:
    reference = _text(value, "source reference", maximum=4_096)
    path = PurePosixPath(reference)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in reference
        or path == PurePosixPath(".")
    ):
        raise _fail("source reference is not a private logical path")
    return reference


def _captured_at(value: object) -> str:
    if not isinstance(value, str):
        raise _fail("captured_at is invalid")
    parsed = TemporalValue.parse(value, pointer="/captured_at")
    if parsed.precision is not TemporalPrecision.DATETIME:
        raise _fail("captured_at must be an offset-aware datetime")
    return parsed.raw


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _fail(f"{label} is invalid")
    return value


def _response_array(raw: bytes, source: SourceName) -> tuple[Mapping[str, Any], ...]:
    parsed = loads_strict(raw, max_bytes=MAX_FMP_RESPONSE_BYTES)
    if isinstance(parsed, Mapping):
        raise _fail(f"{source} returned an error or unsupported response envelope")
    if not isinstance(parsed, list):
        raise _fail(f"{source} response must be a JSON array")
    return tuple(
        _mapping(item, f"{source} row {index}")
        for index, item in enumerate(parsed, start=1)
    )


@dataclass(frozen=True, slots=True)
class FmpCompanySubject:
    """One host-proved, already-existing company issuer."""

    issuer_id: str
    cik: str
    symbol: str
    instrument_id: str
    identity_evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer_id", _text(self.issuer_id, "issuer_id"))
        cik = _text(self.cik, "CIK", maximum=10)
        if len(cik) != 10 or not cik.isdigit():
            raise _fail("CIK is invalid")
        object.__setattr__(self, "cik", cik)
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", maximum=64))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        object.__setattr__(
            self,
            "identity_evidence_sha256",
            _digest(self.identity_evidence_sha256, "identity evidence digest"),
        )

    def semantic_mapping(self) -> dict[str, str]:
        return {
            "issuer_id": self.issuer_id,
            "cik": self.cik,
            "symbol": self.symbol,
            "instrument_id": self.instrument_id,
        }

    def provenance_mapping(self) -> dict[str, str]:
        return {
            **self.semantic_mapping(),
            "identity_evidence_sha256": self.identity_evidence_sha256,
        }


@dataclass(frozen=True, slots=True)
class _Dividend:
    provider_event_id: str
    event_date: str
    record_date: str | None
    pay_date: str | None
    declared_date: str | None
    cash_amount: Decimal

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "provider_event_id": self.provider_event_id,
            "event_date": self.event_date,
            "record_date": self.record_date,
            "pay_date": self.pay_date,
            "declared_date": self.declared_date,
            "cash_amount": _decimal_text(self.cash_amount),
            "currency": _SOURCE_UNSPECIFIED_CURRENCY,
        }


@dataclass(frozen=True, slots=True)
class _Split:
    provider_event_id: str
    event_date: str
    split_from_quantity: Decimal
    split_to_quantity: Decimal

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "provider_event_id": self.provider_event_id,
            "event_date": self.event_date,
            "split_from_quantity": _decimal_text(self.split_from_quantity),
            "split_to_quantity": _decimal_text(self.split_to_quantity),
        }


@dataclass(frozen=True, slots=True)
class _ConsensusObservation:
    metric_code: str
    display_name: str
    base_unit: str
    value_kind: Literal["currency", "per_share", "count"]
    reference_period_end: str
    statistic_kind: Literal["mean", "high", "low", "count"]
    value: Decimal | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "metric_code": self.metric_code,
            "base_unit": self.base_unit,
            "value_kind": self.value_kind,
            "reference_period_end": self.reference_period_end,
            "statistic_kind": self.statistic_kind,
            "value": None if self.value is None else _decimal_text(self.value),
            "missing_reason": "not_reported" if self.value is None else None,
        }


@dataclass(frozen=True, slots=True)
class ParsedFmpCompanyResponse:
    """One fully validated source response ready for a short transaction."""

    source: SourceName
    subject: FmpCompanySubject
    captured_at: str
    source_reference: str
    content_sha256: str
    byte_count: int
    raw_row_count: int
    semantic_identity: str
    completeness: Literal["complete", "partial"]
    warnings: tuple[str, ...]
    dividends: tuple[_Dividend, ...] = ()
    splits: tuple[_Split, ...] = ()
    consensus: tuple[_ConsensusObservation, ...] = ()

    @property
    def collector_id(self) -> str:
        return (
            FMP_COMPANY_CORPORATE_ACTIONS_COLLECTOR_ID
            if self.source in {"dividends", "splits"}
            else FMP_COMPANY_ANALYST_ESTIMATES_COLLECTOR_ID
        )

    @property
    def evidence_dataset_id(self) -> str:
        return (
            FMP_COMPANY_ACTION_EVIDENCE_DATASET_ID
            if self.source in {"dividends", "splits"}
            else FMP_COMPANY_EXPECTATION_EVIDENCE_DATASET_ID
        )

    @property
    def canonical_dataset_id(self) -> str:
        return (
            FMP_COMPANY_CORPORATE_ACTIONS_DATASET_ID
            if self.source in {"dividends", "splits"}
            else FMP_COMPANY_EXPECTATIONS_DATASET_ID
        )

    @property
    def normalized_row_count(self) -> int:
        if self.source == "dividends":
            return len(self.dividends)
        if self.source == "splits":
            return len(self.splits)
        return len(self.consensus)

    def request_scope(self) -> dict[str, object]:
        endpoint, request = _SOURCE_REQUESTS[self.source]
        return {
            "provider": "fmp",
            "source": self.source,
            "endpoint": endpoint,
            "request": {**request, "symbol": self.subject.symbol},
            "subject": self.subject.provenance_mapping(),
            "normalization_version": FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION,
            "response": {
                "content_sha256": self.content_sha256,
                "byte_count": self.byte_count,
                "source_reference": self.source_reference,
                "raw_row_count": self.raw_row_count,
            },
            "completeness": self.completeness,
            "tombstone_authoritative": False,
            "currency_policy": (
                "source_unspecified"
                if self.source in {"dividends", "analyst_estimates"}
                else "not_applicable"
            ),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _EstimateField:
    source_field: str
    metric_code: str
    display_name: str
    base_unit: str
    value_kind: Literal["currency", "per_share", "count"]
    statistic_kind: Literal["mean", "high", "low", "count"]


def _money_field(
    source_field: str,
    metric: str,
    statistic_kind: Literal["mean", "high", "low"],
) -> _EstimateField:
    return _EstimateField(
        source_field,
        f"fmp.analyst_estimate.{metric}.source_currency_unspecified",
        f"FMP {metric.replace('_', ' ')} estimate (source currency unspecified)",
        _SOURCE_UNSPECIFIED_UNIT,
        "currency",
        statistic_kind,
    )


def _per_share_field(
    source_field: str, statistic_kind: Literal["mean", "high", "low"]
) -> _EstimateField:
    return _EstimateField(
        source_field,
        "fmp.analyst_estimate.eps.source_currency_unspecified",
        "FMP EPS estimate (source currency unspecified)",
        _SOURCE_UNSPECIFIED_PER_SHARE_UNIT,
        "per_share",
        statistic_kind,
    )


_ESTIMATE_FIELDS: Final[tuple[_EstimateField, ...]] = (
    _money_field("revenueLow", "revenue", "low"),
    _money_field("revenueHigh", "revenue", "high"),
    _money_field("revenueAvg", "revenue", "mean"),
    _money_field("ebitdaLow", "ebitda", "low"),
    _money_field("ebitdaHigh", "ebitda", "high"),
    _money_field("ebitdaAvg", "ebitda", "mean"),
    _money_field("ebitLow", "ebit", "low"),
    _money_field("ebitHigh", "ebit", "high"),
    _money_field("ebitAvg", "ebit", "mean"),
    _money_field("netIncomeLow", "net_income", "low"),
    _money_field("netIncomeHigh", "net_income", "high"),
    _money_field("netIncomeAvg", "net_income", "mean"),
    _money_field("sgaExpenseLow", "sga_expense", "low"),
    _money_field("sgaExpenseHigh", "sga_expense", "high"),
    _money_field("sgaExpenseAvg", "sga_expense", "mean"),
    _per_share_field("epsLow", "low"),
    _per_share_field("epsHigh", "high"),
    _per_share_field("epsAvg", "mean"),
    _EstimateField(
        "numAnalystsRevenue",
        "fmp.analyst_estimate.num_analysts_revenue",
        "FMP revenue estimate analyst count",
        "count",
        "count",
        "count",
    ),
    _EstimateField(
        "numAnalystsEps",
        "fmp.analyst_estimate.num_analysts_eps",
        "FMP EPS estimate analyst count",
        "count",
        "count",
        "count",
    ),
)

_SOURCE_REQUESTS: Final[dict[SourceName, tuple[str, dict[str, object]]]] = {
    "dividends": ("/stable/dividends", {}),
    "splits": ("/stable/splits", {}),
    "analyst_estimates": (
        "/stable/analyst-estimates",
        {"period": "annual", "page": 0, "limit": MAX_FMP_ANALYST_ESTIMATE_ROWS},
    ),
}


def _require_symbol(row: Mapping[str, Any], subject: FmpCompanySubject, pointer: str) -> None:
    if _text(row.get("symbol"), f"{pointer}/symbol", maximum=64) != subject.symbol:
        raise _fail(f"{pointer}/symbol does not match the host-proved symbol")


def _parse_dividends(
    rows: Sequence[Mapping[str, Any]], subject: FmpCompanySubject
) -> tuple[_Dividend, ...]:
    if len(rows) > MAX_FMP_ACTION_ROWS:
        raise ResourceLimitError("FMP dividend response exceeds the row bound")
    parsed: list[_Dividend] = []
    keys: set[str] = set()
    for ordinal, row in enumerate(rows, start=1):
        pointer = f"/dividends/{ordinal - 1}"
        _require_symbol(row, subject, pointer)
        event_date = _date_text(row.get("date"), f"{pointer}/date")
        record_date = _optional_date(row.get("recordDate"), f"{pointer}/recordDate")
        pay_date = _optional_date(row.get("paymentDate"), f"{pointer}/paymentDate")
        declared_date = _optional_date(row.get("declarationDate"), f"{pointer}/declarationDate")
        # Mutable payment dates and ticker spelling are payload, not event
        # identity. Instrument identity keeps different share classes separate.
        event_id = stable_id(
            "fmp_company_dividend_event", subject.issuer_id, subject.instrument_id,
            "cash_dividend", event_date,
        )
        if event_id in keys:
            raise _fail(f"{pointer} duplicates an FMP dividend event")
        keys.add(event_id)
        parsed.append(
            _Dividend(
                event_id,
                event_date,
                record_date,
                pay_date,
                declared_date,
                _decimal(row.get("dividend"), f"{pointer}/dividend", nonnegative=True),
            )
        )
    return tuple(sorted(parsed, key=lambda item: item.provider_event_id))


def _parse_splits(
    rows: Sequence[Mapping[str, Any]], subject: FmpCompanySubject
) -> tuple[_Split, ...]:
    if len(rows) > MAX_FMP_ACTION_ROWS:
        raise ResourceLimitError("FMP split response exceeds the row bound")
    parsed: list[_Split] = []
    keys: set[str] = set()
    for ordinal, row in enumerate(rows, start=1):
        pointer = f"/splits/{ordinal - 1}"
        _require_symbol(row, subject, pointer)
        event_date = _date_text(row.get("date"), f"{pointer}/date")
        event_id = stable_id("fmp_company_split_event", subject.issuer_id,
                             subject.instrument_id, "split", event_date)
        if event_id in keys:
            raise _fail(f"{pointer} duplicates an FMP split event")
        keys.add(event_id)
        numerator = _decimal(row.get("numerator"), f"{pointer}/numerator", nonnegative=True)
        denominator = _decimal(row.get("denominator"), f"{pointer}/denominator", nonnegative=True)
        if numerator <= 0 or denominator <= 0:
            raise _fail(f"{pointer} has a nonpositive split ratio")
        parsed.append(_Split(event_id, event_date, denominator, numerator))
    return tuple(sorted(parsed, key=lambda item: item.provider_event_id))


def _parse_analyst_estimates(
    rows: Sequence[Mapping[str, Any]], subject: FmpCompanySubject
) -> tuple[_ConsensusObservation, ...]:
    if len(rows) > MAX_FMP_ANALYST_ESTIMATE_ROWS:
        raise ResourceLimitError("FMP analyst-estimates response exceeds the row bound")
    observations: list[_ConsensusObservation] = []
    periods: set[str] = set()
    for ordinal, row in enumerate(rows, start=1):
        pointer = f"/analyst_estimates/{ordinal - 1}"
        _require_symbol(row, subject, pointer)
        period_end = _date_text(row.get("date"), f"{pointer}/date")
        if period_end in periods:
            raise _fail(f"{pointer}/date duplicates an annual FMP estimate period")
        periods.add(period_end)
        for field in _ESTIMATE_FIELDS:
            if field.source_field not in row:
                # Source omission is distinct from an explicit null observation.
                continue
            raw_value = row[field.source_field]
            value = None if raw_value is None else _decimal(
                raw_value, f"{pointer}/{field.source_field}"
            )
            observations.append(
                _ConsensusObservation(
                    field.metric_code,
                    field.display_name,
                    field.base_unit,
                    field.value_kind,
                    period_end,
                    field.statistic_kind,
                    value,
                )
            )
    if rows and not observations:
        raise _fail("analyst-estimates contains no supported estimate fields")
    return tuple(
        sorted(
            observations,
            key=lambda item: (
                item.reference_period_end,
                item.metric_code,
                item.statistic_kind,
            ),
        )
    )


def _semantic_identity(
    *,
    source: SourceName,
    subject: FmpCompanySubject,
    completeness: Literal["complete", "partial"],
    records: Sequence[_Dividend | _Split | _ConsensusObservation],
) -> str:
    endpoint, request = _SOURCE_REQUESTS[source]
    material = {
        "normalization_version": FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION,
        "provider": "fmp",
        "source": source,
        "endpoint": endpoint,
        "request": {**request, "symbol": subject.symbol},
        "subject": subject.semantic_mapping(),
        "completeness": completeness,
        "currency_policy": (
            "source_unspecified"
            if source in {"dividends", "analyst_estimates"}
            else "not_applicable"
        ),
        "records": [record.semantic_mapping() for record in records],
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def parse_fmp_company_response(
    source: SourceName,
    subject: FmpCompanySubject,
    body: bytes | str,
    captured_at: str,
    source_reference: str,
) -> ParsedFmpCompanyResponse:
    """Parse one saved FMP source body without network or store access."""

    if source not in _SOURCE_REQUESTS:
        raise _fail("source is unsupported")
    if not isinstance(subject, FmpCompanySubject):
        raise _fail("subject is invalid")
    raw = _body_bytes(body)
    rows = _response_array(raw, source)
    captured = _captured_at(captured_at)
    reference = _source_reference(source_reference)
    warnings: list[str] = []
    completeness: Literal["complete", "partial"] = "complete"
    dividends: tuple[_Dividend, ...] = ()
    splits: tuple[_Split, ...] = ()
    consensus: tuple[_ConsensusObservation, ...] = ()
    if source == "dividends":
        dividends = _parse_dividends(rows, subject)
        warnings.append(_SOURCE_UNSPECIFIED_WARNING)
        if len(rows) == MAX_FMP_ACTION_ROWS:
            completeness = "partial"
            warnings.append(_ACTION_LIMIT_WARNING)
        records: Sequence[_Dividend | _Split | _ConsensusObservation] = dividends
    elif source == "splits":
        splits = _parse_splits(rows, subject)
        if len(rows) == MAX_FMP_ACTION_ROWS:
            completeness = "partial"
            warnings.append(_ACTION_LIMIT_WARNING)
        records = splits
    else:
        consensus = _parse_analyst_estimates(rows, subject)
        warnings.append(_SOURCE_UNSPECIFIED_WARNING)
        if len(rows) == MAX_FMP_ANALYST_ESTIMATE_ROWS:
            completeness = "partial"
            warnings.append(_ANALYST_LIMIT_WARNING)
        records = consensus
    if not rows:
        warnings.append(_EMPTY_RESPONSE_WARNING)
    semantic_identity = _semantic_identity(
        source=source,
        subject=subject,
        completeness=completeness,
        records=records,
    )
    return ParsedFmpCompanyResponse(
        source=source,
        subject=subject,
        captured_at=captured,
        source_reference=reference,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        raw_row_count=len(rows),
        semantic_identity=semantic_identity,
        completeness=completeness,
        warnings=tuple(warnings),
        dividends=dividends,
        splits=splits,
        consensus=consensus,
    )


def _same(row: sqlite3.Row, **expected: object) -> bool:
    return all(row[key] == value for key, value in expected.items())


def _require_existing_issuer(connection: sqlite3.Connection, subject: FmpCompanySubject) -> None:
    existing = connection.execute(
        "SELECT issuer_id, cik FROM company_issuers WHERE issuer_id=? OR cik=?",
        (subject.issuer_id, subject.cik),
    ).fetchone()
    if existing is None:
        raise ConflictError("FMP company subject issuer does not exist")
    if str(existing["issuer_id"]) != subject.issuer_id or str(existing["cik"]) != subject.cik:
        raise ConflictError("FMP company subject issuer/CIK conflicts with stored state")


def _domain_artifact_id(parsed: ParsedFmpCompanyResponse) -> str:
    return stable_id(
        "fmp_company_domain_artifact",
        parsed.source,
        parsed.subject.issuer_id,
        parsed.content_sha256,
        dumps_strict(parsed.request_scope()),
    )


def _control_artifact_id(parsed: ParsedFmpCompanyResponse) -> str:
    return stable_id(
        "fmp_company_control_artifact",
        parsed.evidence_dataset_id,
        parsed.source,
        parsed.content_sha256,
        dumps_strict(parsed.request_scope()),
    )


def _domain_snapshot_id(parsed: ParsedFmpCompanyResponse) -> str:
    return stable_id("fmp_company_domain_snapshot", parsed.source, parsed.semantic_identity)


def _control_snapshot_id(parsed: ParsedFmpCompanyResponse) -> str:
    return stable_id(
        "fmp_company_control_snapshot",
        parsed.evidence_dataset_id,
        parsed.semantic_identity,
    )


def _insert_action_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    artifact_id: str,
    run_id: str,
) -> int:
    scope_json = dumps_strict(parsed.request_scope())
    expected = {
        "issuer_id": parsed.subject.issuer_id,
        "provider": "fmp",
        "content_sha256": parsed.content_sha256,
        "media_type": "application/json",
        "byte_count": parsed.byte_count,
        "source_reference": parsed.source_reference,
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT issuer_id, provider, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               available_at, available_precision
        FROM company_action_source_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("FMP corporate-action artifact conflicts with immutable evidence")
        return 0
    connection.execute(
        """
        INSERT INTO company_action_source_artifacts (
            artifact_id, issuer_id, provider, content_sha256, media_type, byte_count,
            source_reference, request_scope_json, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime', ?)
        """,
        (
            artifact_id,
            parsed.subject.issuer_id,
            parsed.content_sha256,
            "application/json",
            parsed.byte_count,
            parsed.source_reference,
            scope_json,
            parsed.captured_at,
            parsed.captured_at,
            run_id,
        ),
    )
    return 1


def _insert_action_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    scope_json = dumps_strict(parsed.request_scope())
    expected = {
        "semantic_identity": parsed.semantic_identity,
        "issuer_id": parsed.subject.issuer_id,
        "provider": "fmp",
        "artifact_id": artifact_id,
        "scope_json": scope_json,
        "completeness": parsed.completeness,
        "tombstone_authoritative": 0,
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, provider, artifact_id, scope_json,
               completeness, tombstone_authoritative, captured_at, captured_precision,
               available_at, available_precision
        FROM company_action_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("FMP corporate-action snapshot conflicts with immutable evidence")
        return 0
    connection.execute(
        """
        INSERT INTO company_action_snapshots (
            snapshot_id, semantic_identity, issuer_id, provider, artifact_id,
            scope_json, completeness, tombstone_authoritative, captured_at,
            captured_precision, available_at, available_precision, run_id
        ) VALUES (?, ?, ?, 'fmp', ?, ?, ?, 0, ?, 'datetime', ?, 'datetime', ?)
        """,
        (
            snapshot_id,
            parsed.semantic_identity,
            parsed.subject.issuer_id,
            artifact_id,
            scope_json,
            parsed.completeness,
            parsed.captured_at,
            parsed.captured_at,
            run_id,
        ),
    )
    return 1


def _append_action(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    snapshot_id: str,
    action: _Dividend | _Split,
    source_row: int,
    run_id: str,
) -> tuple[str, int]:
    if isinstance(action, _Dividend):
        action_kind = "cash_dividend"
        record_date, pay_date, declared_date = (
            action.record_date,
            action.pay_date,
            action.declared_date,
        )
        cash_amount: float | None = float(action.cash_amount)
        currency: str | None = _SOURCE_UNSPECIFIED_CURRENCY
        split_from: float | None = None
        split_to: float | None = None
    else:
        action_kind = "split"
        record_date = pay_date = declared_date = None
        cash_amount = currency = None
        split_from = float(action.split_from_quantity)
        split_to = float(action.split_to_quantity)
    prior = connection.execute(
        """
        SELECT action_version_id, provider_symbol, action_kind, event_date,
               record_date, pay_date, declared_date, cash_amount, currency,
               split_from_quantity, split_to_quantity, action_state, missing_reason,
               version_sequence
        FROM company_corporate_action_versions
        WHERE issuer_id=? AND provider='fmp' AND provider_event_id=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (parsed.subject.issuer_id, action.provider_event_id),
    ).fetchone()
    # Source snapshot/capture time is lineage, not a changed action payload.
    expected = {
        "provider_symbol": parsed.subject.symbol,
        "action_kind": action_kind,
        "event_date": action.event_date,
        "record_date": record_date,
        "pay_date": pay_date,
        "declared_date": declared_date,
        "cash_amount": cash_amount,
        "currency": currency,
        "split_from_quantity": split_from,
        "split_to_quantity": split_to,
        "action_state": "active",
        "missing_reason": None,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["action_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    version_id = stable_id(
        "fmp_company_action_version",
        parsed.subject.issuer_id,
        action.provider_event_id,
        str(sequence),
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
        ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active',
                  NULL, ?, 'datetime', ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            parsed.subject.issuer_id,
            parsed.subject.symbol,
            action.provider_event_id,
            action_kind,
            action.event_date,
            record_date,
            pay_date,
            declared_date,
            cash_amount,
            currency,
            split_from,
            split_to,
            parsed.captured_at,
            sequence,
            None if prior is None else str(prior["action_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return version_id, 1


def _insert_action_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    version_id: str,
    source_row: int,
    run_id: str,
) -> int:
    existing = connection.execute(
        """
        SELECT action_version_id, source_row
        FROM company_action_snapshot_membership
        WHERE snapshot_id=? AND action_version_id=?
        """,
        (snapshot_id, version_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("FMP corporate-action membership conflicts with immutable evidence")
        return 0
    conflicting = connection.execute(
        """
        SELECT action_version_id FROM company_action_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflicting is not None:
        raise ConflictError("FMP corporate-action source-row lineage is ambiguous")
    connection.execute(
        """
        INSERT INTO company_action_snapshot_membership (
            snapshot_id, action_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, version_id, run_id, source_row),
    )
    return 1


def _write_actions(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    run_id: str,
) -> int:
    artifact_id = _domain_artifact_id(parsed)
    snapshot_id = _domain_snapshot_id(parsed)
    written = _insert_action_artifact(
        connection, parsed=parsed, artifact_id=artifact_id, run_id=run_id
    )
    written += _insert_action_snapshot(
        connection,
        parsed=parsed,
        artifact_id=artifact_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
    )
    actions: Sequence[_Dividend | _Split] = (
        parsed.dividends if parsed.source == "dividends" else parsed.splits
    )
    for source_row, action in enumerate(actions, start=1):
        version_id, appended = _append_action(
            connection,
            parsed=parsed,
            snapshot_id=snapshot_id,
            action=action,
            source_row=source_row,
            run_id=run_id,
        )
        written += appended
        # Existing action-membership integrity requires member versions to have
        # been written by this run. Reused immutable versions remain linked to
        # their earlier snapshot; only changed rows join this new snapshot.
        if appended:
            written += _insert_action_membership(
                connection,
                snapshot_id=snapshot_id,
                version_id=version_id,
                source_row=source_row,
                run_id=run_id,
            )
    return written


def _insert_earnings_artifact(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    artifact_id: str,
    run_id: str,
) -> int:
    scope_json = dumps_strict(parsed.request_scope())
    expected = {
        "issuer_id": parsed.subject.issuer_id,
        "provider": "fmp",
        "content_sha256": parsed.content_sha256,
        "media_type": "application/json",
        "byte_count": parsed.byte_count,
        "source_reference": parsed.source_reference,
        "request_scope_json": scope_json,
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT issuer_id, provider, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at, captured_precision,
               available_at, available_precision
        FROM company_earnings_source_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("FMP earnings artifact conflicts with immutable evidence")
        return 0
    connection.execute(
        """
        INSERT INTO company_earnings_source_artifacts (
            artifact_id, issuer_id, provider, content_sha256, media_type, byte_count,
            source_reference, request_scope_json, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime', ?)
        """,
        (
            artifact_id,
            parsed.subject.issuer_id,
            parsed.content_sha256,
            "application/json",
            parsed.byte_count,
            parsed.source_reference,
            scope_json,
            parsed.captured_at,
            parsed.captured_at,
            run_id,
        ),
    )
    return 1


def _insert_earnings_snapshot(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    scope_json = dumps_strict(parsed.request_scope())
    expected = {
        "semantic_identity": parsed.semantic_identity,
        "issuer_id": parsed.subject.issuer_id,
        "provider": "fmp",
        "snapshot_kind": "consensus",
        "artifact_id": artifact_id,
        "scope_json": scope_json,
        "completeness": parsed.completeness,
        "captured_at": parsed.captured_at,
        "captured_precision": "datetime",
        "available_at": parsed.captured_at,
        "available_precision": "datetime",
    }
    existing = connection.execute(
        """
        SELECT semantic_identity, issuer_id, provider, snapshot_kind, artifact_id,
               scope_json, completeness, captured_at, captured_precision,
               available_at, available_precision
        FROM company_earnings_source_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("FMP earnings snapshot conflicts with immutable evidence")
        return 0
    connection.execute(
        """
        INSERT INTO company_earnings_source_snapshots (
            snapshot_id, semantic_identity, issuer_id, provider, snapshot_kind,
            artifact_id, scope_json, completeness, captured_at, captured_precision,
            available_at, available_precision, run_id
        ) VALUES (?, ?, ?, 'fmp', 'consensus', ?, ?, ?, ?, 'datetime', ?, 'datetime', ?)
        """,
        (
            snapshot_id,
            parsed.semantic_identity,
            parsed.subject.issuer_id,
            artifact_id,
            scope_json,
            parsed.completeness,
            parsed.captured_at,
            parsed.captured_at,
            run_id,
        ),
    )
    return 1


def _metric_id(observation: _ConsensusObservation) -> str:
    return stable_id("company_expectation_metric", observation.metric_code)


def _ensure_expectation_metric(
    connection: sqlite3.Connection,
    *,
    observation: _ConsensusObservation,
    captured_at: str,
) -> tuple[str, int]:
    metric_id = _metric_id(observation)
    expected = {
        "display_name": observation.display_name,
        "base_unit": observation.base_unit,
        "value_kind": observation.value_kind,
        "definition_version": FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION,
    }
    existing = connection.execute(
        """
        SELECT display_name, base_unit, value_kind, definition_version
        FROM company_expectation_metric_definitions WHERE expectation_metric_id=?
        """,
        (metric_id,),
    ).fetchone()
    if existing is not None:
        if not _same(existing, **expected):
            raise ConflictError("FMP expectation metric conflicts with immutable state")
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
            observation.display_name,
            observation.base_unit,
            observation.value_kind,
            FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION,
            captured_at,
        ),
    )
    return metric_id, 1


def _append_consensus(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    observation: _ConsensusObservation,
    metric_id: str,
    snapshot_id: str,
    source_row: int,
    run_id: str,
) -> tuple[str, int]:
    value = None if observation.value is None else float(observation.value)
    value_state = "missing" if observation.value is None else "present"
    missing_reason = "not_reported" if observation.value is None else None
    prior = connection.execute(
        """
        SELECT consensus_version_id, fiscal_year, fiscal_period,
               reference_period_start, value, value_state, missing_reason,
               version_sequence
        FROM company_consensus_observation_versions
        WHERE issuer_id=? AND expectation_metric_id=? AND reference_period_end=?
              AND statistic_kind=?
        ORDER BY version_sequence DESC
        LIMIT 1
        """,
        (
            parsed.subject.issuer_id,
            metric_id,
            observation.reference_period_end,
            observation.statistic_kind,
        ),
    ).fetchone()
    # No fiscal start or quarter is inferred. Snapshot/capture changes alone do
    # not manufacture a revised estimate version.
    expected = {
        "fiscal_year": None,
        "fiscal_period": None,
        "reference_period_start": None,
        "value": value,
        "value_state": value_state,
        "missing_reason": missing_reason,
    }
    if prior is not None and _same(prior, **expected):
        return str(prior["consensus_version_id"]), 0
    sequence = 1 if prior is None else int(prior["version_sequence"]) + 1
    version_id = stable_id(
        "fmp_company_consensus_version",
        parsed.subject.issuer_id,
        metric_id,
        observation.reference_period_end,
        observation.statistic_kind,
        str(sequence),
    )
    connection.execute(
        """
        INSERT INTO company_consensus_observation_versions (
            consensus_version_id, issuer_id, expectation_metric_id, fiscal_year,
            fiscal_period, reference_period_start, reference_period_end,
            statistic_kind, value, value_state, missing_reason, available_at,
            available_precision, version_sequence, supersedes_consensus_version_id,
            source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            parsed.subject.issuer_id,
            metric_id,
            observation.reference_period_end,
            observation.statistic_kind,
            value,
            value_state,
            missing_reason,
            parsed.captured_at,
            sequence,
            None if prior is None else str(prior["consensus_version_id"]),
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    return version_id, 1


def _insert_consensus_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    version_id: str,
    source_row: int,
    run_id: str,
) -> int:
    existing = connection.execute(
        """
        SELECT consensus_version_id, source_row
        FROM company_consensus_snapshot_membership
        WHERE snapshot_id=? AND consensus_version_id=?
        """,
        (snapshot_id, version_id),
    ).fetchone()
    if existing is not None:
        if int(existing["source_row"]) != source_row:
            raise ConflictError("FMP consensus membership conflicts with immutable evidence")
        return 0
    conflicting = connection.execute(
        """
        SELECT consensus_version_id FROM company_consensus_snapshot_membership
        WHERE snapshot_id=? AND source_row=?
        """,
        (snapshot_id, source_row),
    ).fetchone()
    if conflicting is not None:
        raise ConflictError("FMP consensus normalized source-row lineage is ambiguous")
    connection.execute(
        """
        INSERT INTO company_consensus_snapshot_membership (
            snapshot_id, consensus_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, version_id, run_id, source_row),
    )
    return 1


def _write_estimates(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedFmpCompanyResponse,
    run_id: str,
) -> int:
    artifact_id = _domain_artifact_id(parsed)
    snapshot_id = _domain_snapshot_id(parsed)
    written = _insert_earnings_artifact(
        connection, parsed=parsed, artifact_id=artifact_id, run_id=run_id
    )
    written += _insert_earnings_snapshot(
        connection,
        parsed=parsed,
        artifact_id=artifact_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
    )
    metric_ids: dict[str, str] = {}
    for observation in parsed.consensus:
        if observation.metric_code in metric_ids:
            continue
        metric_id, appended = _ensure_expectation_metric(
            connection,
            observation=observation,
            captured_at=parsed.captured_at,
        )
        metric_ids[observation.metric_code] = metric_id
        written += appended
    # One raw annual FMP row contains many fields. The existing membership
    # relation permits one row ordinal per normalized observation, so this
    # deterministic ordering is the normalized source-row lineage; the raw
    # field/value remains in the retained artifact.
    for source_row, observation in enumerate(parsed.consensus, start=1):
        version_id, appended = _append_consensus(
            connection,
            parsed=parsed,
            observation=observation,
            metric_id=metric_ids[observation.metric_code],
            snapshot_id=snapshot_id,
            source_row=source_row,
            run_id=run_id,
        )
        written += appended
        # The existing consensus membership trigger has the same current-run
        # constraint as corporate actions.
        if appended:
            written += _insert_consensus_membership(
                connection,
                snapshot_id=snapshot_id,
                version_id=version_id,
                source_row=source_row,
                run_id=run_id,
            )
    return written


def _validate_prepared(parsed: ParsedFmpCompanyResponse) -> None:
    if not isinstance(parsed, ParsedFmpCompanyResponse):
        raise _fail("prepared FMP response is invalid")
    if parsed.source not in _SOURCE_REQUESTS or not isinstance(parsed.subject, FmpCompanySubject):
        raise _fail("prepared FMP response is invalid")
    if _digest(parsed.content_sha256, "response digest") != parsed.content_sha256:
        raise _fail("prepared FMP response is invalid")
    if (
        isinstance(parsed.byte_count, bool)
        or not isinstance(parsed.byte_count, int)
        or parsed.byte_count < 1
        or isinstance(parsed.raw_row_count, bool)
        or not isinstance(parsed.raw_row_count, int)
        or parsed.raw_row_count < 0
        or parsed.completeness not in {"complete", "partial"}
        or any(not isinstance(warning, str) or not warning for warning in parsed.warnings)
    ):
        raise _fail("prepared FMP response is invalid")
    _captured_at(parsed.captured_at)
    _source_reference(parsed.source_reference)
    if parsed.source == "dividends":
        records: Sequence[_Dividend | _Split | _ConsensusObservation] = parsed.dividends
        if parsed.splits or parsed.consensus:
            raise _fail("prepared FMP response has inconsistent source records")
    elif parsed.source == "splits":
        records = parsed.splits
        if parsed.dividends or parsed.consensus:
            raise _fail("prepared FMP response has inconsistent source records")
    else:
        records = parsed.consensus
        if parsed.dividends or parsed.splits:
            raise _fail("prepared FMP response has inconsistent source records")
    expected_semantic = _semantic_identity(
        source=parsed.source,
        subject=parsed.subject,
        completeness=parsed.completeness,
        records=records,
    )
    if parsed.semantic_identity != expected_semantic:
        raise _fail("prepared FMP response semantic identity is invalid")


class FmpCompanyMarketDataPublisher:
    """Atomically publish one validated response into existing company relations."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise _fail("publisher dependencies are invalid")
        registered = {item.id for item in registry.datasets_for(StoreRole.COMPANY.value)}
        required = {
            FMP_COMPANY_ACTION_EVIDENCE_DATASET_ID,
            FMP_COMPANY_CORPORATE_ACTIONS_DATASET_ID,
            FMP_COMPANY_EXPECTATION_EVIDENCE_DATASET_ID,
            FMP_COMPANY_EXPECTATIONS_DATASET_ID,
        }
        if not required.issubset(registered):
            raise _fail("existing company action and expectation datasets are not registered")
        self._coordinator = IngestionCoordinator(
            store_map,
            code_version="fmp_company_market_data.1.0.0",
        )

    def publish(
        self,
        parsed: ParsedFmpCompanyResponse,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        _validate_prepared(parsed)
        run_id = stable_id(
            "fmp_company_market_data_run",
            parsed.canonical_dataset_id,
            parsed.source,
            parsed.semantic_identity,
        )
        control_artifact_id = _control_artifact_id(parsed)
        control_snapshot_id = _control_snapshot_id(parsed)

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise _fail("coordinator supplied an unexpected run identity")
            _require_existing_issuer(connection, parsed.subject)
            if parsed.source in {"dividends", "splits"}:
                written = _write_actions(connection, parsed=parsed, run_id=active_run_id)
            else:
                written = _write_estimates(connection, parsed=parsed, run_id=active_run_id)
            artifact = ArtifactWrite(
                artifact_id=control_artifact_id,
                dataset_id=parsed.evidence_dataset_id,
                content_sha256=parsed.content_sha256,
                media_type="application/json",
                byte_count=parsed.byte_count,
                source_reference=parsed.source_reference,
                request_scope=parsed.request_scope(),
                captured_at=parsed.captured_at,
                captured_precision="datetime",
                normalization_version=FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION,
            )
            snapshot = SnapshotWrite(
                snapshot_id=control_snapshot_id,
                dataset_id=parsed.evidence_dataset_id,
                semantic_identity=parsed.semantic_identity,
                scope=parsed.request_scope(),
                completeness=parsed.completeness,
                row_count=parsed.normalized_row_count,
                captured_at=parsed.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(control_artifact_id,),
                warnings=parsed.warnings,
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=snapshot,
                quality_results=(),
                warnings=parsed.warnings,
            )

        return self._coordinator.execute(
            role=StoreRole.COMPANY,
            dataset_id=parsed.evidence_dataset_id,
            output_dataset_ids=(
                parsed.evidence_dataset_id,
                parsed.canonical_dataset_id,
            ),
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=parsed.collector_id,
            scope=parsed.request_scope(),
            started_at=parsed.captured_at,
            completed_at=parsed.captured_at,
            fetched_count=parsed.raw_row_count,
            writer=writer,
            held_locks=held_locks,
        )


__all__ = [
    "FMP_COMPANY_ACTION_EVIDENCE_DATASET_ID",
    "FMP_COMPANY_ANALYST_ESTIMATES_COLLECTOR_ID",
    "FMP_COMPANY_CORPORATE_ACTIONS_COLLECTOR_ID",
    "FMP_COMPANY_CORPORATE_ACTIONS_DATASET_ID",
    "FMP_COMPANY_EXPECTATION_EVIDENCE_DATASET_ID",
    "FMP_COMPANY_EXPECTATIONS_DATASET_ID",
    "FMP_COMPANY_MARKET_DATA_NORMALIZATION_VERSION",
    "FmpCompanyMarketDataPublisher",
    "FmpCompanySubject",
    "ParsedFmpCompanyResponse",
    "parse_fmp_company_response",
]
