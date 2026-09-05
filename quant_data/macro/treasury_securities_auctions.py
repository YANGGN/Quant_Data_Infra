"""Treasury Fiscal Data securities-auction normalization.

This module owns the fixed source shape.  The generic official-conditions
publisher remains responsible for macro evidence/version publication after
the integration owner adds the matching source definition and registry entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Final

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..temporal import TemporalPrecision, TemporalValue, parse_date
from .official_conditions import (
    MAX_RESPONSE_BYTES,
    OfficialConditionsCapture,
    OfficialObservation,
    OfficialResponsePart,
    OfficialSeriesSpec,
)


TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY: Final = "treasury_securities_auctions"
TREASURY_SECURITIES_AUCTIONS_PROVIDER: Final = "treasury_fiscal_data"
TREASURY_SECURITIES_AUCTIONS_COLLECTOR_ID: Final = (
    "treasury_fiscal_data.macro.securities_auctions_history"
)
TREASURY_SECURITIES_AUCTIONS_HANDLER: Final = (
    "macro.treasury_securities_auctions_history"
)
TREASURY_SECURITIES_AUCTIONS_NORMALIZATION_VERSION: Final = (
    "treasury_securities_auctions_v1"
)
TREASURY_SECURITIES_AUCTIONS_SOURCE_REFERENCE: Final = (
    "treasury_fiscal_data/v1/accounting/od/auctions_query"
)
TREASURY_SECURITIES_AUCTIONS_ARTIFACT_MEDIA_TYPE: Final = "application/json"
MAX_RESPONSE_ROWS: Final = 1_000


TREASURY_SECURITIES_AUCTIONS_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.offering_amount",
        "offering_amt",
        "U.S. Treasury securities auction offering amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.total_tendered",
        "total_tendered",
        "U.S. Treasury securities auction total tendered amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.total_accepted",
        "total_accepted",
        "U.S. Treasury securities auction total accepted amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.high_yield",
        "high_yield",
        "U.S. Treasury securities auction high yield",
        "event",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.high_discount_rate",
        "high_discnt_rate",
        "U.S. Treasury securities auction high discount rate",
        "event",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.high_discount_margin",
        "high_discnt_margin",
        "U.S. Treasury floating-rate note auction high discount margin",
        "event",
        "basis_points",
        "spread",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.bid_to_cover",
        "bid_to_cover_ratio",
        "U.S. Treasury securities auction bid-to-cover ratio",
        "event",
        "ratio",
        "ratio",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.direct_bidder_accepted",
        "direct_bidder_accepted",
        "U.S. Treasury auction direct bidder accepted amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.indirect_bidder_accepted",
        "indirect_bidder_accepted",
        "U.S. Treasury auction indirect bidder accepted amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.primary_dealer_accepted",
        "primary_dealer_accepted",
        "U.S. Treasury auction primary dealer accepted amount",
        "event",
        "usd",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.treasury_fiscal.auction.soma_accepted",
        "soma_accepted",
        "U.S. Treasury securities auction SOMA accepted amount",
        "event",
        "usd",
        "amount",
    ),
)

TREASURY_SECURITIES_AUCTIONS_FIELDS: Final = (
    "record_date",
    "cusip",
    "auction_date",
    "security_type",
    "security_term",
    "issue_date",
    "reopening",
    "auction_format",
    *(spec.provider_code for spec in TREASURY_SECURITIES_AUCTIONS_MANIFEST),
)


@dataclass(frozen=True, slots=True)
class TreasurySecuritiesAuctionsSourceMetadata:
    key: str
    provider: str
    collector_id: str
    handler: str
    normalization_version: str
    source_reference: str
    artifact_media_type: str
    manifest: tuple[OfficialSeriesSpec, ...]
    max_requests: int


TREASURY_SECURITIES_AUCTIONS_SOURCE_METADATA: Final = (
    TreasurySecuritiesAuctionsSourceMetadata(
        TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY,
        TREASURY_SECURITIES_AUCTIONS_PROVIDER,
        TREASURY_SECURITIES_AUCTIONS_COLLECTOR_ID,
        TREASURY_SECURITIES_AUCTIONS_HANDLER,
        TREASURY_SECURITIES_AUCTIONS_NORMALIZATION_VERSION,
        TREASURY_SECURITIES_AUCTIONS_SOURCE_REFERENCE,
        TREASURY_SECURITIES_AUCTIONS_ARTIFACT_MEDIA_TYPE,
        TREASURY_SECURITIES_AUCTIONS_MANIFEST,
        1,
    )
)


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _capture_time(value: str) -> str:
    parsed = TemporalValue.parse(value, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("Treasury securities auction capture time must be aware")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[date, date]:
    try:
        start = parse_date(start_date, pointer="/start_date")
        end = parse_date(end_date, pointer="/end_date")
    except ValidationError as exc:
        raise _fail("Treasury securities auction request window is invalid") from exc
    if start > end:
        raise _fail("Treasury securities auction request window is invalid")
    return start, end


def _count(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise _fail(f"Treasury securities auction {field} is invalid")
    if isinstance(value, int):
        result = value
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise _fail(f"Treasury securities auction {field} is invalid")
        result = int(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw.isascii() or not raw.isdecimal():
            raise _fail(f"Treasury securities auction {field} is invalid")
        result = int(raw)
    else:
        raise _fail(f"Treasury securities auction {field} is invalid")
    if result < 0:
        raise _fail(f"Treasury securities auction {field} is invalid")
    return result


def _date_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise _fail(f"Treasury securities auction {field} is invalid")
    try:
        return parse_date(value, pointer=f"/{field}").isoformat()
    except ValidationError as exc:
        raise _fail(f"Treasury securities auction {field} is invalid") from exc


def _text(row: dict[str, object], field: str, *, optional: bool = False) -> str | None:
    if field not in row or row[field] is None:
        if optional:
            return None
        raise _fail(f"Treasury securities auction {field} is missing")
    value = row[field]
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"Treasury securities auction {field} is invalid")
    return value.strip()


def _dimensions(row: dict[str, object]) -> tuple[tuple[str, str], ...]:
    cusip = _text(row, "cusip")
    security_type = _text(row, "security_type")
    security_term = _text(row, "security_term")
    if cusip is None or security_type is None or security_term is None:
        raise _fail("Treasury securities auction identity is invalid")
    values = {
        "auction_date": _date_text(row.get("auction_date"), field="auction_date"),
        "cusip": cusip,
        "issue_date": _date_text(row.get("issue_date"), field="issue_date"),
        "security_term": security_term,
        "security_type": security_type,
    }
    for field in ("reopening", "auction_format"):
        value = _text(row, field, optional=True)
        if value is not None:
            values[field] = value
    return tuple(sorted(values.items()))


def _record_date(row: dict[str, object]) -> None:
    if "record_date" not in row:
        raise _fail("Treasury securities auction record_date is missing")
    if row["record_date"] is not None:
        _date_text(row["record_date"], field="record_date")


def _value(value: object, *, field: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_missing"
    if isinstance(value, bool):
        raise _fail(f"Treasury securities auction {field} is invalid")
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.casefold() == "null":
            return None, "source_missing"
        raw = raw.replace(",", "")
    elif isinstance(value, (int, Decimal)):
        raw = str(value)
    else:
        raise _fail(f"Treasury securities auction {field} is invalid")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"Treasury securities auction {field} is invalid") from exc
    if not number.is_finite():
        raise _fail(f"Treasury securities auction {field} is invalid")
    ensure_number_within_limits(number)
    normalized = number.normalize()
    return ("0" if normalized.is_zero() else format(normalized, "f")), None


def _semantic_material(
    scope: dict[str, object], observations: tuple[OfficialObservation, ...]
) -> dict[str, object]:
    return {
        "normalization_version": TREASURY_SECURITIES_AUCTIONS_NORMALIZATION_VERSION,
        "provider": TREASURY_SECURITIES_AUCTIONS_PROVIDER,
        "request_scope": dict(scope),
        "series_manifest": [
            {"provider_code": spec.provider_code, "series_id": spec.series_id}
            for spec in TREASURY_SECURITIES_AUCTIONS_MANIFEST
        ],
        "normalized_observations": [
            {
                "series_id": item.series_id,
                "source_period": item.source_period,
                "period_start": item.period_start,
                "period_end": item.period_end,
                "value": item.value_text,
                "missing_reason": item.missing_reason,
                "dimensions": dict(item.dimensions),
            }
            for item in observations
        ],
    }


def parse_treasury_securities_auctions(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse one complete fixed Fiscal Data auction page.

    Auction date is the event period.  Record date remains date-only source
    evidence, so availability is local capture time rather than an invented
    publication timestamp.  A multi-page response fails closed.
    """

    start, end = _window(start_date, end_date)
    if not isinstance(body, bytes) or not body:
        raise _fail("Treasury securities auction response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("Treasury securities auction response exceeds its byte bound")
    try:
        payload = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    except ResourceLimitError:
        raise
    except ValidationError as exc:
        raise _fail("Treasury securities auction response must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise _fail("Treasury securities auction response shape is invalid")
    rows = payload.get("data")
    metadata = payload.get("meta")
    if not isinstance(rows, list) or not isinstance(metadata, dict):
        raise _fail("Treasury securities auction response shape is invalid")
    if (
        not rows
        or len(rows) > MAX_RESPONSE_ROWS
        or _count(metadata.get("total-pages"), field="total-pages") != 1
        or _count(metadata.get("total-count"), field="total-count") != len(rows)
    ):
        raise _fail("Treasury securities auction response is not one complete page")
    if any(not isinstance(row, dict) for row in rows):
        raise _fail("Treasury securities auction response row is invalid")

    observations: list[OfficialObservation] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for row in rows:
        _record_date(row)
        dimensions = _dimensions(row)
        auction_date = dict(dimensions)["auction_date"]
        observed = date.fromisoformat(auction_date)
        if observed < start or observed > end:
            raise _fail("Treasury securities auction row is outside the requested window")
        identity = (auction_date, dimensions)
        if identity in seen:
            raise _fail("Treasury securities auction identities must be unique")
        seen.add(identity)
        for spec in TREASURY_SECURITIES_AUCTIONS_MANIFEST:
            if spec.provider_code not in row:
                raise _fail("Treasury securities auction fixed metric is missing")
            value_text, missing_reason = _value(
                row[spec.provider_code], field=spec.provider_code
            )
            observations.append(
                OfficialObservation(
                    spec.series_id,
                    spec.provider_code,
                    auction_date,
                    auction_date,
                    auction_date,
                    value_text,
                    missing_reason,
                    0,
                    dimensions,
                )
            )

    ordinal = {
        spec.series_id: index
        for index, spec in enumerate(TREASURY_SECURITIES_AUCTIONS_MANIFEST)
    }
    normalized = tuple(
        OfficialObservation(
            item.series_id,
            item.provider_code,
            item.source_period,
            item.period_start,
            item.period_end,
            item.value_text,
            item.missing_reason,
            index,
            item.dimensions,
        )
        for index, item in enumerate(
            sorted(
                observations,
                key=lambda item: (
                    item.period_start,
                    item.period_end,
                    ordinal[item.series_id],
                    item.dimensions,
                ),
            ),
            start=1,
        )
    )
    scope: dict[str, object] = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "fields": list(TREASURY_SECURITIES_AUCTIONS_FIELDS),
        "filter_field": "auction_date",
        "sort": ["auction_date", "cusip"],
        "record_date": {
            "field": "record_date",
            "precision": "date",
            "availability_use": "evidence_only",
        },
        "availability_basis": "local_capture",
        "completeness": "complete",
    }
    part = OfficialResponsePart("auctions_page", body, _sha256_bytes(body))
    artifact_material = [
        {"name": part.name, "sha256": part.sha256, "byte_count": len(part.body)}
    ]
    return OfficialConditionsCapture(
        TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY,
        (part,),
        _sha256_text(dumps_strict(artifact_material)),
        _sha256_text(dumps_strict(_semantic_material(scope, normalized))),
        _capture_time(captured_at),
        dumps_strict(scope),
        normalized,
    )


__all__ = (
    "MAX_RESPONSE_ROWS",
    "TREASURY_SECURITIES_AUCTIONS_ARTIFACT_MEDIA_TYPE",
    "TREASURY_SECURITIES_AUCTIONS_COLLECTOR_ID",
    "TREASURY_SECURITIES_AUCTIONS_FIELDS",
    "TREASURY_SECURITIES_AUCTIONS_HANDLER",
    "TREASURY_SECURITIES_AUCTIONS_MANIFEST",
    "TREASURY_SECURITIES_AUCTIONS_NORMALIZATION_VERSION",
    "TREASURY_SECURITIES_AUCTIONS_PROVIDER",
    "TREASURY_SECURITIES_AUCTIONS_SOURCE_KEY",
    "TREASURY_SECURITIES_AUCTIONS_SOURCE_METADATA",
    "TREASURY_SECURITIES_AUCTIONS_SOURCE_REFERENCE",
    "TreasurySecuritiesAuctionsSourceMetadata",
    "parse_treasury_securities_auctions",
)
