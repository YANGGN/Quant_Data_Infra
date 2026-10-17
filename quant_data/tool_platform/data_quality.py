"""Pure deterministic quality diagnostics for immutable typed time series.

The kernel deliberately reports duplicate and out-of-order observations instead
of sorting, discarding, or rejecting them.  It opens no store and exposes no
paths, SQL, or source artifacts; public adapters can turn its frozen summaries
into a QueryResult without changing their analytical meaning.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Any

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.temporal import TemporalPrecision, TemporalValue, parse_date


QUALITY_AUDIT_PRIMITIVE = "quant_data.tool_platform.data_quality.audit_time_series_quality"
QUALITY_AUDIT_PRIMITIVE_VERSION = "1.0.0"

MAX_AUDIT_SERIES = 20
MAX_AUDIT_OBSERVATIONS_PER_SERIES = 10_000
DEFAULT_DISCONTINUITY_LIMIT = 100
MAX_DISCONTINUITY_LIMIT = 1_000

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:=-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValidationError(f"Quality audit {field} is invalid")
    return value


def _optional_identifier(value: object, *, field: str) -> str | None:
    return None if value is None else _safe_identifier(value, field=field)


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValidationError("Quality audit input lineage digest is invalid")
    return value


@dataclass(frozen=True, slots=True)
class AvailabilityCounts:
    """Availability counts classified without repairing source declarations."""

    date: int
    datetime: int
    unknown: int
    invalid: int
    missing: int

    @property
    def total(self) -> int:
        return self.date + self.datetime + self.unknown + self.invalid + self.missing

    def to_primitive(self) -> dict[str, int]:
        return {
            "date": self.date,
            "datetime": self.datetime,
            "unknown": self.unknown,
            "invalid": self.invalid,
            "missing": self.missing,
        }


@dataclass(frozen=True, slots=True)
class CalendarDiscontinuity:
    """A bounded calendar-date interval, explicitly not a trading-session gap."""

    previous_period_end: str
    next_period_start: str
    calendar_span_start: str
    calendar_span_end: str
    calendar_day_count: int

    def to_primitive(self) -> dict[str, str | int]:
        return {
            "previous_period_end": self.previous_period_end,
            "next_period_start": self.next_period_start,
            "calendar_span_start": self.calendar_span_start,
            "calendar_span_end": self.calendar_span_end,
            "calendar_day_count": self.calendar_day_count,
        }


@dataclass(frozen=True, slots=True)
class DataQualitySeriesSummary:
    """Immutable, adapter-ready summary for one input series."""

    series_id: str
    input_lineage_digest: str
    observation_count: int
    first_period: tuple[str, str] | None
    last_period: tuple[str, str] | None
    requested_source_range: tuple[str | None, str | None]
    truncated: bool
    coverage_status: str
    coverage_notes: tuple[str, ...]
    explicit_missing_count: int
    missing_reasons: tuple[tuple[str, int], ...]
    duplicate_period_count: int
    duplicate_version_id_count: int
    duplicate_evidence_id_count: int
    duplicate_snapshot_id_count: int
    duplicate_run_id_count: int
    out_of_order_count: int
    calendar_discontinuity_count: int
    calendar_discontinuity_span_days: int
    calendar_discontinuities: tuple[CalendarDiscontinuity, ...]
    calendar_discontinuities_truncated: bool
    availability_counts: AvailabilityCounts
    availability_basis: str | None
    cutoff: str | None
    cutoff_precision: str | None
    date_only_policy: str | None
    point_in_time_status: str | None
    point_in_time_scope: str | None
    unsafe_reasons: tuple[str, ...]
    dataset_id: str | None
    store_role: str | None
    registry_revision: str | None
    distinct_version_id_count: int
    distinct_evidence_id_count: int
    distinct_snapshot_id_count: int
    distinct_run_id_count: int
    quality_flag_count: int
    distinct_quality_flag_count: int
    warning_count: int
    distinct_warning_count: int

    def to_primitive(self) -> dict[str, Any]:
        def period(value: tuple[str, str] | None) -> dict[str, str] | None:
            if value is None:
                return None
            return {"period_start": value[0], "period_end": value[1]}

        return {
            "contract": "quant_data.data_quality_series_summary",
            "contract_version": "1.0.0",
            "series_id": self.series_id,
            "input_lineage_digest": self.input_lineage_digest,
            "observation_count": self.observation_count,
            "period_coverage": {
                "first_period": period(self.first_period),
                "last_period": period(self.last_period),
                "requested_source_range": {
                    "start_date": self.requested_source_range[0],
                    "end_date": self.requested_source_range[1],
                },
                "truncated": self.truncated,
                "coverage_status": self.coverage_status,
                "coverage_notes": list(self.coverage_notes),
            },
            "missingness": {
                "explicit_missing_count": self.explicit_missing_count,
                "missing_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in self.missing_reasons
                ],
                "absent_row_count": None,
                "absent_row_count_status": "not_inferable_from_one_series",
            },
            "duplicates_and_order": {
                "duplicate_period_count": self.duplicate_period_count,
                "duplicate_version_id_count": self.duplicate_version_id_count,
                "duplicate_evidence_id_count": self.duplicate_evidence_id_count,
                "duplicate_snapshot_id_count": self.duplicate_snapshot_id_count,
                "duplicate_run_id_count": self.duplicate_run_id_count,
                "out_of_order_count": self.out_of_order_count,
            },
            "calendar_discontinuities": {
                "basis": "calendar_days_not_trading_sessions",
                "count": self.calendar_discontinuity_count,
                "calendar_span_days": self.calendar_discontinuity_span_days,
                "items": [item.to_primitive() for item in self.calendar_discontinuities],
                "items_truncated": self.calendar_discontinuities_truncated,
            },
            "availability": {
                "counts": self.availability_counts.to_primitive(),
                "basis": self.availability_basis,
                "cutoff": self.cutoff,
                "cutoff_precision": self.cutoff_precision,
                "date_only_policy": self.date_only_policy,
                "point_in_time_status": self.point_in_time_status,
                "point_in_time_scope": self.point_in_time_scope,
                "unsafe_reasons": list(self.unsafe_reasons),
            },
            "lineage": {
                "dataset_id": self.dataset_id,
                "store_role": self.store_role,
                "registry_revision": self.registry_revision,
                "distinct_version_id_count": self.distinct_version_id_count,
                "distinct_evidence_id_count": self.distinct_evidence_id_count,
                "distinct_snapshot_id_count": self.distinct_snapshot_id_count,
                "distinct_run_id_count": self.distinct_run_id_count,
            },
            "quality_flags": {
                "count": self.quality_flag_count,
                "distinct_count": self.distinct_quality_flag_count,
            },
            "warnings": {
                "count": self.warning_count,
                "distinct_count": self.distinct_warning_count,
            },
        }


def _optional_date(source: Mapping[str, Any], name: str, *, pointer: str) -> str | None:
    value = source.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"Quality audit {name} is invalid")
    parse_date(value, pointer=pointer)
    return value


def _optional_cutoff(source: Mapping[str, Any]) -> tuple[str | None, str | None]:
    value = source.get("cutoff")
    precision = source.get("cutoff_precision")
    if value is None:
        if precision is not None:
            raise ValidationError("Quality audit cutoff precision is invalid")
        return None, None
    if not isinstance(value, str):
        raise ValidationError("Quality audit cutoff is invalid")
    parsed = TemporalValue.parse(value, pointer="/series/audit/cutoff")
    if not isinstance(precision, str) or precision != parsed.precision.value:
        raise ValidationError("Quality audit cutoff precision is invalid")
    return value, precision


def _validate_observation(
    value: object, *, index: int
) -> tuple[Observation, date, date]:
    if not isinstance(value, Observation):
        raise ValidationError("Quality audit requires typed observations")
    prefix = f"/series/observations/{index}"
    start = parse_date(value.period_start, pointer=f"{prefix}/period_start")
    end = parse_date(value.period_end, pointer=f"{prefix}/period_end")
    if end < start:
        raise ValidationError("Quality audit observation period is invalid")
    if value.value is None:
        _safe_identifier(value.missing_reason, field="missing reason")
    elif (
        not isinstance(value.value, Decimal)
        or not value.value.is_finite()
        or value.missing_reason is not None
    ):
        raise ValidationError("Quality audit observation value is invalid")
    for item, field in (
        (value.unit, "observation unit"),
        (value.value_representation, "value representation"),
        (value.scale, "observation scale"),
        (value.vintage_at, "observation vintage"),
        (value.version_id, "version identity"),
        (value.evidence_id, "evidence identity"),
        (value.snapshot_id, "snapshot identity"),
        (value.run_id, "run identity"),
    ):
        _safe_identifier(item, field=field)
    if value.captured_precision != TemporalPrecision.DATETIME.value:
        raise ValidationError("Quality audit observation capture timing is invalid")
    if not isinstance(value.captured_at, str):
        raise ValidationError("Quality audit observation capture timing is invalid")
    try:
        captured = TemporalValue.parse(
            value.captured_at, pointer=f"{prefix}/captured_at"
        )
    except ValidationError as exc:
        raise ValidationError(
            "Quality audit observation capture timing is invalid"
        ) from exc
    if captured.precision is not TemporalPrecision.DATETIME:
        raise ValidationError("Quality audit observation capture timing is invalid")
    if value.available_at is not None and not isinstance(value.available_at, str):
        raise ValidationError("Quality audit availability value is invalid")
    if not isinstance(value.available_precision, str) or not value.available_precision:
        raise ValidationError("Quality audit availability precision is invalid")
    if not isinstance(value.dimensions, Mapping) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(item, str)
        or not item
        for key, item in value.dimensions.items()
    ):
        raise ValidationError("Quality audit dimensions are invalid")
    if not isinstance(value.quality_flags, tuple) or any(
        not isinstance(flag, str) or not _SAFE_IDENTIFIER_RE.fullmatch(flag)
        for flag in value.quality_flags
    ):
        raise ValidationError("Quality audit quality flags are invalid")
    return value, start, end


def _validate_series(
    value: object,
) -> tuple[TimeSeries, tuple[tuple[Observation, date, date], ...]]:
    if not isinstance(value, TimeSeries):
        raise ValidationError("Quality audit requires typed TimeSeries inputs")
    _safe_identifier(value.series_id, field="series identity")
    _validate_digest(value.lineage_digest)
    if (
        not isinstance(value.metadata, Mapping)
        or not isinstance(value.audit, Mapping)
        or not isinstance(value.provenance, Mapping)
        or not isinstance(value.observations, tuple)
        or not isinstance(value.warnings, tuple)
        or not isinstance(value.truncated, bool)
    ):
        raise ValidationError("Quality audit TimeSeries structure is invalid")
    if len(value.observations) > MAX_AUDIT_OBSERVATIONS_PER_SERIES:
        raise ResourceLimitError("Quality audit observation limit is exceeded")
    if any(
        not isinstance(warning, str) or not _SAFE_IDENTIFIER_RE.fullmatch(warning)
        for warning in value.warnings
    ):
        raise ValidationError("Quality audit warning codes are invalid")
    observations = tuple(
        _validate_observation(item, index=index)
        for index, item in enumerate(value.observations)
    )
    value.validate_lineage()
    return value, observations


def _availability_category(observation: Observation) -> str:
    value = observation.available_at
    precision = observation.available_precision
    if value is None:
        if precision in {
            TemporalPrecision.UNKNOWN.value,
            TemporalPrecision.INFERRED.value,
        }:
            return "unknown"
        return "missing"
    if not isinstance(value, str) or not value:
        return "invalid"
    if precision in {
        TemporalPrecision.UNKNOWN.value,
        TemporalPrecision.INFERRED.value,
    }:
        return "unknown"
    try:
        parsed = TemporalValue.parse(
            value, pointer="/series/observations/available_at"
        )
    except ValidationError:
        return "invalid"
    if precision == TemporalPrecision.DATE.value and parsed.precision is TemporalPrecision.DATE:
        return "date"
    if precision == TemporalPrecision.DATETIME.value and parsed.precision is TemporalPrecision.DATETIME:
        return "datetime"
    return "invalid"


def _duplicate_excess(values: Sequence[object]) -> int:
    return sum(count - 1 for count in Counter(values).values() if count > 1)


def _calendar_discontinuities(
    observations: Sequence[tuple[Observation, date, date]],
    *,
    limit: int,
) -> tuple[int, int, tuple[CalendarDiscontinuity, ...], bool]:
    unique = sorted(
        {
            (start, end, observation.period_start, observation.period_end)
            for observation, start, end in observations
        },
        key=lambda item: (item[0], item[1]),
    )
    all_items: list[CalendarDiscontinuity] = []
    for prior, following in zip(unique, unique[1:]):
        prior_end = prior[1]
        following_start = following[0]
        if following_start <= prior_end + timedelta(days=1):
            continue
        span_start = prior_end + timedelta(days=1)
        span_end = following_start - timedelta(days=1)
        all_items.append(
            CalendarDiscontinuity(
                previous_period_end=prior[3],
                next_period_start=following[2],
                calendar_span_start=span_start.isoformat(),
                calendar_span_end=span_end.isoformat(),
                calendar_day_count=(span_end - span_start).days + 1,
            )
        )
    bounded = tuple(all_items[:limit])
    return (
        len(all_items),
        sum(item.calendar_day_count for item in all_items),
        bounded,
        len(bounded) < len(all_items),
    )


def _optional_audit_or_metadata(
    series: TimeSeries, *, audit_name: str, metadata_name: str | None = None
) -> str | None:
    if audit_name in series.audit:
        return _optional_identifier(
            series.audit.get(audit_name), field=audit_name
        )
    if metadata_name is not None and metadata_name in series.metadata:
        return _optional_identifier(
            series.metadata.get(metadata_name), field=metadata_name
        )
    return None


def _requested_source_range(series: TimeSeries) -> tuple[str | None, str | None]:
    start = _optional_date(
        series.audit,
        "requested_start_date",
        pointer="/series/audit/requested_start_date",
    )
    end = _optional_date(
        series.audit,
        "requested_end_date",
        pointer="/series/audit/requested_end_date",
    )
    if (
        start is not None
        and end is not None
        and parse_date(end, pointer="/series/audit/requested_end_date")
        < parse_date(start, pointer="/series/audit/requested_start_date")
    ):
        raise ValidationError("Quality audit requested source range is invalid")
    return start, end


def _time_contract(
    series: TimeSeries,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None, tuple[str, ...]]:
    cutoff, cutoff_precision = _optional_cutoff(series.audit)
    raw_reasons = series.audit.get("unsafe_reasons", ())
    if isinstance(raw_reasons, (str, bytes)) or not isinstance(
        raw_reasons, (list, tuple)
    ):
        raise ValidationError("Quality audit unsafe reasons are invalid")
    unsafe_reasons = tuple(
        sorted(
            {
                _safe_identifier(item, field="point-in-time unsafe reason")
                for item in raw_reasons
            }
        )
    )
    return (
        _optional_audit_or_metadata(
            series,
            audit_name="availability_basis",
            metadata_name="availability_basis",
        ),
        cutoff,
        cutoff_precision,
        _optional_audit_or_metadata(series, audit_name="date_only_policy"),
        _optional_audit_or_metadata(series, audit_name="point_in_time_status"),
        _optional_audit_or_metadata(series, audit_name="point_in_time_scope"),
        unsafe_reasons,
    )


def _coverage(
    *,
    observation_count: int,
    truncated: bool,
    missing_count: int,
    discontinuity_count: int,
    requested_source_range: tuple[str | None, str | None],
) -> tuple[str, tuple[str, ...]]:
    notes = {"trading_session_coverage_not_established"}
    if requested_source_range != (None, None):
        notes.add("requested_source_range_declared")
    if truncated:
        notes.add("input_truncated")
    if missing_count:
        notes.add("explicit_missing_observations")
    if discontinuity_count:
        notes.add("calendar_discontinuities_detected")
    if truncated:
        status = "truncated"
    elif observation_count == 0:
        status = "empty"
    elif missing_count or discontinuity_count:
        status = "incomplete_or_not_established"
    else:
        status = "observed_rows_only"
    return status, tuple(sorted(notes))


def _audit_one(
    series: TimeSeries,
    observations: tuple[tuple[Observation, date, date], ...],
    *,
    discontinuity_limit: int,
) -> DataQualitySeriesSummary:
    ordered = sorted(observations, key=lambda item: (item[1], item[2]))
    first_period = (
        None
        if not ordered
        else (ordered[0][0].period_start, ordered[0][0].period_end)
    )
    last_period = (
        None
        if not ordered
        else (ordered[-1][0].period_start, ordered[-1][0].period_end)
    )
    highest_seen: tuple[date, date] | None = None
    out_of_order_count = 0
    for _, start, end in observations:
        key = (start, end)
        if highest_seen is not None and key < highest_seen:
            out_of_order_count += 1
        if highest_seen is None or key > highest_seen:
            highest_seen = key
    missing_reasons = Counter(
        observation.missing_reason
        for observation, _, _ in observations
        if observation.missing_reason is not None
    )
    availability = Counter(
        _availability_category(observation) for observation, _, _ in observations
    )
    (
        discontinuity_count,
        discontinuity_span_days,
        discontinuities,
        discontinuities_truncated,
    ) = _calendar_discontinuities(observations, limit=discontinuity_limit)
    requested_source_range = _requested_source_range(series)
    coverage_status, coverage_notes = _coverage(
        observation_count=len(observations),
        truncated=series.truncated,
        missing_count=sum(missing_reasons.values()),
        discontinuity_count=discontinuity_count,
        requested_source_range=requested_source_range,
    )
    (
        availability_basis,
        cutoff,
        cutoff_precision,
        date_only_policy,
        point_in_time_status,
        point_in_time_scope,
        unsafe_reasons,
    ) = _time_contract(series)
    version_ids = [item.version_id for item, _, _ in observations]
    evidence_ids = [item.evidence_id for item, _, _ in observations]
    snapshot_ids = [item.snapshot_id for item, _, _ in observations]
    run_ids = [item.run_id for item, _, _ in observations]
    quality_flags = [
        flag
        for observation, _, _ in observations
        for flag in observation.quality_flags
    ]
    return DataQualitySeriesSummary(
        series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        observation_count=len(observations),
        first_period=first_period,
        last_period=last_period,
        requested_source_range=requested_source_range,
        truncated=series.truncated,
        coverage_status=coverage_status,
        coverage_notes=coverage_notes,
        explicit_missing_count=sum(missing_reasons.values()),
        missing_reasons=tuple(sorted(missing_reasons.items())),
        duplicate_period_count=_duplicate_excess(
            [(item.period_start, item.period_end) for item, _, _ in observations]
        ),
        duplicate_version_id_count=_duplicate_excess(version_ids),
        duplicate_evidence_id_count=_duplicate_excess(evidence_ids),
        duplicate_snapshot_id_count=_duplicate_excess(snapshot_ids),
        duplicate_run_id_count=_duplicate_excess(run_ids),
        out_of_order_count=out_of_order_count,
        calendar_discontinuity_count=discontinuity_count,
        calendar_discontinuity_span_days=discontinuity_span_days,
        calendar_discontinuities=discontinuities,
        calendar_discontinuities_truncated=discontinuities_truncated,
        availability_counts=AvailabilityCounts(
            date=availability["date"],
            datetime=availability["datetime"],
            unknown=availability["unknown"],
            invalid=availability["invalid"],
            missing=availability["missing"],
        ),
        availability_basis=availability_basis,
        cutoff=cutoff,
        cutoff_precision=cutoff_precision,
        date_only_policy=date_only_policy,
        point_in_time_status=point_in_time_status,
        point_in_time_scope=point_in_time_scope,
        unsafe_reasons=unsafe_reasons,
        dataset_id=_optional_identifier(
            series.provenance.get("dataset_id"), field="dataset identity"
        ),
        store_role=_optional_identifier(
            series.provenance.get("store_role"), field="store role"
        ),
        registry_revision=_optional_identifier(
            series.provenance.get("registry_revision"),
            field="registry revision",
        ),
        distinct_version_id_count=len(set(version_ids)),
        distinct_evidence_id_count=len(set(evidence_ids)),
        distinct_snapshot_id_count=len(set(snapshot_ids)),
        distinct_run_id_count=len(set(run_ids)),
        quality_flag_count=len(quality_flags),
        distinct_quality_flag_count=len(set(quality_flags)),
        warning_count=len(series.warnings),
        distinct_warning_count=len(set(series.warnings)),
    )


def audit_time_series_quality(
    values: Sequence[TimeSeries],
    *,
    discontinuity_limit: int = DEFAULT_DISCONTINUITY_LIMIT,
) -> tuple[DataQualitySeriesSummary, ...]:
    """Audit one to twenty immutable series without store access or mutation.

    The result order is stable series identity order.  Calendar discontinuities
    are date spans between distinct observed periods; they never assert that a
    trading session, holiday, or exchange calendar was expected.
    """

    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, Sequence)
        or not values
        or len(values) > MAX_AUDIT_SERIES
    ):
        raise ValidationError("Quality audit requires one to twenty TimeSeries inputs")
    if (
        isinstance(discontinuity_limit, bool)
        or not isinstance(discontinuity_limit, int)
        or discontinuity_limit < 0
    ):
        raise ValidationError("Quality audit discontinuity limit is invalid")
    if discontinuity_limit > MAX_DISCONTINUITY_LIMIT:
        raise ResourceLimitError("Quality audit discontinuity limit is exceeded")
    validated = tuple(_validate_series(item) for item in values)
    identities = tuple(series.series_id for series, _ in validated)
    if len(set(identities)) != len(identities):
        raise ValidationError("Quality audit requires distinct series identities")
    return tuple(
        sorted(
            (
                _audit_one(
                    series,
                    observations,
                    discontinuity_limit=discontinuity_limit,
                )
                for series, observations in validated
            ),
            key=lambda item: item.series_id,
        )
    )


__all__ = (
    "AvailabilityCounts",
    "CalendarDiscontinuity",
    "DEFAULT_DISCONTINUITY_LIMIT",
    "DataQualitySeriesSummary",
    "MAX_AUDIT_OBSERVATIONS_PER_SERIES",
    "MAX_AUDIT_SERIES",
    "MAX_DISCONTINUITY_LIMIT",
    "QUALITY_AUDIT_PRIMITIVE",
    "QUALITY_AUDIT_PRIMITIVE_VERSION",
    "audit_time_series_quality",
)
