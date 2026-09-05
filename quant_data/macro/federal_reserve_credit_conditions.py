"""Fixed Federal Reserve H.8 and SLOOS FRED-CSV normalization.

The later shared official-conditions hook owns registry and publisher binding.
This module intentionally only normalizes complete, bounded FRED graph CSV
responses into the existing OfficialConditionsCapture contract.
"""

from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
from typing import Final, Mapping

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, ensure_number_within_limits
from ..temporal import TemporalPrecision, TemporalValue, parse_date
from .official_conditions import (
    OfficialConditionsCapture,
    OfficialObservation,
    OfficialResponsePart,
    OfficialSeriesSpec,
)


H8_SOURCE_KEY: Final = "federal_reserve_h8"
SLOOS_SOURCE_KEY: Final = "federal_reserve_sloos"
H8_PROVIDER: Final = "federal_reserve_h8"
SLOOS_PROVIDER: Final = "federal_reserve_sloos"
H8_COLLECTOR_ID: Final = "federal_reserve.macro.h8_history"
SLOOS_COLLECTOR_ID: Final = "federal_reserve.macro.sloos_history"
H8_HANDLER: Final = "macro.federal_reserve_h8_history"
SLOOS_HANDLER: Final = "macro.federal_reserve_sloos_history"
H8_NORMALIZATION_VERSION: Final = "federal_reserve_h8_v2"
SLOOS_NORMALIZATION_VERSION: Final = "federal_reserve_sloos_v1"
FRED_GRAPH_SOURCE_REFERENCE: Final = "fred/graph/fredgraph.csv"
ARTIFACT_MEDIA_TYPE: Final = "text/csv"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES: Final = MAX_RESPONSE_BYTES
MAX_RESPONSE_ROWS: Final = 20_000
MAX_WINDOW_DAYS: Final = 5_000


H8_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.total_assets",
        "TLAACBW027SBOG",
        "Total assets, all commercial banks, seasonally adjusted",
        "weekly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.total_bank_credit",
        "TOTBKCR",
        "Total bank credit, all commercial banks, seasonally adjusted",
        "weekly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.total_loans_and_leases",
        "TOTLL",
        "Total loans and leases, all commercial banks, seasonally adjusted",
        "weekly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.business_loans",
        "BUSLOANS",
        "Commercial and industrial loans, all commercial banks",
        "monthly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.real_estate_loans",
        "REALLN",
        "Real estate loans, all commercial banks",
        "monthly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.consumer_loans",
        "CONSUMER",
        "Consumer loans, all commercial banks",
        "monthly",
        "usd_billions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_h8.deposits",
        "DPSACBW027SBOG",
        "Deposits, all commercial banks, seasonally adjusted",
        "weekly",
        "usd_billions",
        "amount",
    ),
)

SLOOS_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.ci_standards_large_middle_market",
        "DRTSCILM",
        "Net banks tightening C&I standards for large and middle-market firms",
        "quarterly",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.ci_standards_small_firms",
        "DRTSCIS",
        "Net banks tightening C&I standards for small firms",
        "quarterly",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.ci_demand_large_middle_market",
        "DRSDCILM",
        "Net banks reporting stronger C&I loan demand from large and middle-market firms",
        "quarterly",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.ci_demand_small_firms",
        "DRSDCIS",
        "Net banks reporting stronger C&I loan demand from small firms",
        "quarterly",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.credit_card_standards",
        "DRTSCLCC",
        "Net percentage of domestic banks tightening standards for credit card loans",
        "quarterly",
        "percent",
        "rate",
    ),
    OfficialSeriesSpec(
        "macro.federal_reserve_sloos.credit_card_demand",
        "DEMCC",
        "Net percentage of domestic banks reporting stronger demand for credit card loans",
        "quarterly",
        "percent",
        "rate",
    ),
)


@dataclass(frozen=True, slots=True)
class FederalReserveCreditSourceMetadata:
    """Complete source metadata consumed by the later generic-source hook."""

    key: str
    provider: str
    collector_id: str
    handler: str
    normalization_version: str
    source_reference: str
    artifact_media_type: str
    manifest: tuple[OfficialSeriesSpec, ...]
    fred_series: tuple[str, ...]
    frequency: str
    max_requests: int
    max_window_days: int
    configuration_env: tuple[str, ...] = ()


H8_SOURCE_METADATA: Final = FederalReserveCreditSourceMetadata(
    H8_SOURCE_KEY,
    H8_PROVIDER,
    H8_COLLECTOR_ID,
    H8_HANDLER,
    H8_NORMALIZATION_VERSION,
    FRED_GRAPH_SOURCE_REFERENCE,
    ARTIFACT_MEDIA_TYPE,
    H8_MANIFEST,
    tuple(item.provider_code for item in H8_MANIFEST),
    "mixed",
    len(H8_MANIFEST),
    MAX_WINDOW_DAYS,
)
SLOOS_SOURCE_METADATA: Final = FederalReserveCreditSourceMetadata(
    SLOOS_SOURCE_KEY,
    SLOOS_PROVIDER,
    SLOOS_COLLECTOR_ID,
    SLOOS_HANDLER,
    SLOOS_NORMALIZATION_VERSION,
    FRED_GRAPH_SOURCE_REFERENCE,
    ARTIFACT_MEDIA_TYPE,
    SLOOS_MANIFEST,
    tuple(item.provider_code for item in SLOOS_MANIFEST),
    "quarterly",
    len(SLOOS_MANIFEST),
    MAX_WINDOW_DAYS,
)
SOURCE_METADATA_BY_KEY: Final = {
    H8_SOURCE_KEY: H8_SOURCE_METADATA,
    SLOOS_SOURCE_KEY: SLOOS_SOURCE_METADATA,
}



def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _metadata(value: object) -> FederalReserveCreditSourceMetadata:
    if not isinstance(value, str) or value not in SOURCE_METADATA_BY_KEY:
        raise _fail("Federal Reserve credit source key is invalid")
    return SOURCE_METADATA_BY_KEY[value]


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("Federal Reserve credit capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("Federal Reserve credit request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def _value(value: str) -> tuple[str | None, str | None]:
    if value in {"", "."}:
        return None, "source_missing"
    if value != value.strip():
        raise _fail("Federal Reserve credit value is invalid")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _fail("Federal Reserve credit value is invalid") from exc
    if not parsed.is_finite():
        raise _fail("Federal Reserve credit value is invalid")
    ensure_number_within_limits(parsed)
    normalized = parsed.normalize()
    return ("0" if normalized.is_zero() else format(normalized, "f")), None


def _quarter_bounds(observed: date) -> tuple[str, str, str]:
    if observed.day != 1 or observed.month not in {1, 4, 7, 10}:
        raise _fail("Federal Reserve SLOOS observation date is not a quarter start")
    quarter = (observed.month - 1) // 3 + 1
    next_month = 1 if quarter == 4 else observed.month + 3
    next_year = observed.year + int(quarter == 4)
    end = date.fromordinal(date(next_year, next_month, 1).toordinal() - 1)
    return (
        f"{observed.year}-Q{quarter}",
        observed.isoformat(),
        end.isoformat(),
    )


def _semantic_material(
    metadata: FederalReserveCreditSourceMetadata,
    scope: Mapping[str, object],
    observations: tuple[OfficialObservation, ...],
) -> dict[str, object]:
    return {
        "normalization_version": metadata.normalization_version,
        "provider": metadata.provider,
        "request_scope": dict(scope),
        "series_manifest": [
            {"provider_code": item.provider_code, "series_id": item.series_id}
            for item in metadata.manifest
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


def _parse_fred_graph_csv(
    bodies: tuple[bytes, ...],
    *,
    metadata: FederalReserveCreditSourceMetadata,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    if (
        not isinstance(bodies, tuple)
        or len(bodies) != len(metadata.fred_series)
    ):
        raise _fail(
            "Federal Reserve credit response count must match the fixed series manifest"
        )
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    values: list[
        tuple[OfficialSeriesSpec, str, str, str, str | None, str | None]
    ] = []
    seen: set[tuple[str, str]] = set()
    by_code = {item.provider_code: item for item in metadata.manifest}
    ordinal = {item.provider_code: index for index, item in enumerate(metadata.manifest)}
    parts: list[OfficialResponsePart] = []
    total_bytes = 0
    total_rows = 0

    for part_index, (code, body) in enumerate(
        zip(metadata.fred_series, bodies),
        start=1,
    ):
        if not isinstance(body, bytes) or not body:
            raise _fail("Federal Reserve credit response must contain CSV bytes")
        if len(body) > MAX_RESPONSE_BYTES:
            raise ResourceLimitError(
                "Federal Reserve credit response exceeds its per-series byte bound"
            )
        total_bytes += len(body)
        if total_bytes > MAX_TOTAL_RESPONSE_BYTES:
            raise ResourceLimitError(
                "Federal Reserve credit responses exceed their aggregate byte bound"
            )
        try:
            text = body.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise _fail("Federal Reserve credit response is not UTF-8 CSV") from exc
        rows = list(csv.reader(io.StringIO(text, newline="")))
        expected_header = ("observation_date", code)
        if (
            len(rows) < 2
            or len(rows) > MAX_RESPONSE_ROWS + 1
            or tuple(rows[0]) != expected_header
        ):
            raise _fail("Federal Reserve credit CSV header or row count is invalid")
        total_rows += len(rows) - 1
        if total_rows > MAX_RESPONSE_ROWS:
            raise ResourceLimitError(
                "Federal Reserve credit responses exceed their aggregate row bound"
            )

        spec = by_code[code]
        selected_for_code = 0
        for row in rows[1:]:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != len(expected_header):
                raise _fail("Federal Reserve credit CSV row is incomplete")
            observed = parse_date(row[0].strip(), pointer="/observation_date")
            if spec.frequency == "quarterly":
                source_period, period_start, period_end = _quarter_bounds(observed)
            elif spec.frequency == "monthly":
                if observed.day != 1:
                    raise _fail("Federal Reserve monthly date must be a month start")
                source_period = f"{observed.year:04d}-{observed.month:02d}"
                period_start = observed.isoformat()
                period_end = date(observed.year, observed.month,
                                  calendar.monthrange(observed.year, observed.month)[1]).isoformat()
            else:
                source_period = observed.isoformat()
                period_start = source_period
                period_end = source_period
            if observed < start or observed > end:
                # FRED graph exports can contain a full series despite cosd/coed.
                continue
            identity = (spec.series_id, source_period)
            if identity in seen:
                raise _fail("Federal Reserve credit observations must be unique")
            seen.add(identity)
            value_text, missing_reason = _value(row[1])
            values.append(
                (
                    spec,
                    source_period,
                    period_start,
                    period_end,
                    value_text,
                    missing_reason,
                )
            )
            selected_for_code += 1
        if selected_for_code == 0:
            raise _fail(
                "Federal Reserve credit selected history is incomplete for the fixed series manifest"
            )
        parts.append(
            OfficialResponsePart(
                f"fred_graph_{part_index:04d}",
                body,
                _sha256_bytes(body),
            )
        )

    if not values:
        raise _fail("Federal Reserve credit selected history is empty")
    values.sort(key=lambda item: (item[2], ordinal[item[0].provider_code]))
    observations = tuple(
        OfficialObservation(
            series_id=spec.series_id,
            provider_code=spec.provider_code,
            source_period=source_period,
            period_start=period_start,
            period_end=period_end,
            value_text=value_text,
            missing_reason=missing_reason,
            source_row=source_row,
        )
        for source_row, (
            spec,
            source_period,
            period_start,
            period_end,
            value_text,
            missing_reason,
        ) in enumerate(values, start=1)
    )
    scope = {
        "availability_basis": "local_capture",
        "completeness": "complete",
        "fred_series": list(metadata.fred_series),
        "from": start_text,
        "source_key": metadata.key,
        "to": end_text,
        "tombstone_authoritative": False,
    }
    frozen_parts = tuple(parts)
    artifact_sha256 = _sha256_text(
        dumps_strict(
            [
                {
                    "name": part.name,
                    "sha256": part.sha256,
                    "byte_count": len(part.body),
                }
                for part in frozen_parts
            ]
        )
    )
    return OfficialConditionsCapture(
        source_key=metadata.key,
        parts=frozen_parts,
        artifact_sha256=artifact_sha256,
        semantic_identity=_sha256_text(
            dumps_strict(_semantic_material(metadata, scope, observations))
        ),
        captured_at=captured,
        request_scope_json=dumps_strict(scope),
        observations=observations,
    )



def parse_federal_reserve_credit_conditions(
    bodies: tuple[bytes, ...],
    *,
    source_key: str,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Dispatch fixed per-series H.8 or SLOOS CSVs through the capture seam."""

    return _parse_fred_graph_csv(
        bodies,
        metadata=_metadata(source_key),
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


def parse_federal_reserve_h8(
    bodies: tuple[bytes, ...],
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse the fixed seven singleton H.8 mixed-frequency FRED graph CSVs."""

    return _parse_fred_graph_csv(
        bodies,
        metadata=H8_SOURCE_METADATA,
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


def parse_federal_reserve_sloos(
    bodies: tuple[bytes, ...],
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Parse the fixed six singleton SLOOS quarterly FRED graph CSVs."""

    return _parse_fred_graph_csv(
        bodies,
        metadata=SLOOS_SOURCE_METADATA,
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


__all__ = (
    "ARTIFACT_MEDIA_TYPE",
    "FRED_GRAPH_SOURCE_REFERENCE",
    "FederalReserveCreditSourceMetadata",
    "H8_COLLECTOR_ID",
    "H8_HANDLER",
    "H8_MANIFEST",
    "H8_NORMALIZATION_VERSION",
    "H8_PROVIDER",
    "H8_SOURCE_KEY",
    "H8_SOURCE_METADATA",
    "MAX_RESPONSE_BYTES",
    "MAX_TOTAL_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "MAX_WINDOW_DAYS",
    "SLOOS_COLLECTOR_ID",
    "SLOOS_HANDLER",
    "SLOOS_MANIFEST",
    "SLOOS_NORMALIZATION_VERSION",
    "SLOOS_PROVIDER",
    "SLOOS_SOURCE_KEY",
    "SLOOS_SOURCE_METADATA",
    "SOURCE_METADATA_BY_KEY",
    "parse_federal_reserve_credit_conditions",
    "parse_federal_reserve_h8",
    "parse_federal_reserve_sloos",
)
