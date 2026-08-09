"""Shared temporal precision and point-in-time comparison rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum

from .errors import Issue, ValidationError


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


class TemporalPrecision(StrEnum):
    DATE = "date"
    DATETIME = "datetime"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class DateOnlyPolicy(StrEnum):
    COMPLETED_DATE = "completed_date"
    CALENDAR_DATE_INCLUSIVE = "calendar_date_inclusive"


@dataclass(frozen=True, slots=True)
class TemporalValue:
    raw: str | None
    precision: TemporalPrecision
    value: date | datetime | None

    @classmethod
    def parse(cls, raw: str, *, pointer: str = "/as_of") -> "TemporalValue":
        if not isinstance(raw, str):
            raise ValidationError(
                "Temporal value must be a string",
                issues=(Issue(pointer, "type", "Expected an ISO date or aware datetime"),),
            )
        if _DATE_RE.fullmatch(raw):
            try:
                parsed = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValidationError(
                    "Invalid calendar date",
                    issues=(Issue(pointer, "format", "Expected a real YYYY-MM-DD date"),),
                ) from exc
            return cls(raw=raw, precision=TemporalPrecision.DATE, value=parsed)
        if _DATETIME_RE.fullmatch(raw):
            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            try:
                parsed_dt = datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise ValidationError(
                    "Invalid aware datetime",
                    issues=(Issue(pointer, "format", "Expected ISO datetime with Z or numeric offset"),),
                ) from exc
            if parsed_dt.tzinfo is None or parsed_dt.utcoffset() is None:
                raise ValidationError(
                    "Naive datetimes are not allowed",
                    issues=(Issue(pointer, "timezone", "An explicit offset is required"),),
                )
            return cls(raw=raw, precision=TemporalPrecision.DATETIME, value=parsed_dt)
        raise ValidationError(
            "Invalid temporal value",
            issues=(Issue(pointer, "format", "Expected YYYY-MM-DD or offset-aware ISO datetime"),),
        )

    @classmethod
    def inferred(cls, raw: str | None = None) -> "TemporalValue":
        return cls(raw=raw, precision=TemporalPrecision.INFERRED, value=None)

    @classmethod
    def unknown(cls) -> "TemporalValue":
        return cls(raw=None, precision=TemporalPrecision.UNKNOWN, value=None)

    def to_dict(self) -> dict[str, str | None]:
        return {"value": self.raw, "precision": self.precision.value}


@dataclass(frozen=True, slots=True)
class AvailabilityDecision:
    included: bool
    warnings: tuple[str, ...] = ()


def parse_date(raw: str, *, pointer: str) -> date:
    value = TemporalValue.parse(raw, pointer=pointer)
    if value.precision is not TemporalPrecision.DATE:
        raise ValidationError(
            "A calendar date is required",
            issues=(Issue(pointer, "format", "Expected YYYY-MM-DD"),),
        )
    assert isinstance(value.value, date) and not isinstance(value.value, datetime)
    return value.value


def availability_at_or_before(
    availability: TemporalValue,
    cutoff: TemporalValue,
    policy: DateOnlyPolicy,
) -> AvailabilityDecision:
    """Apply the accepted mixed-precision availability predicate.

    Inferred and unknown availability fail closed.  Date-only availability is
    never expanded into an invented timestamp.
    """

    if cutoff.precision not in (TemporalPrecision.DATE, TemporalPrecision.DATETIME):
        raise ValidationError("Cutoff must be an exact date or aware datetime")
    if availability.precision in (TemporalPrecision.INFERRED, TemporalPrecision.UNKNOWN):
        return AvailabilityDecision(False)

    if availability.precision is TemporalPrecision.DATE:
        assert isinstance(availability.value, date) and not isinstance(availability.value, datetime)
        available_date = availability.value
        if cutoff.precision is TemporalPrecision.DATE:
            assert isinstance(cutoff.value, date) and not isinstance(cutoff.value, datetime)
            return AvailabilityDecision(available_date <= cutoff.value)
        assert isinstance(cutoff.value, datetime)
        written_cutoff_date = cutoff.value.date()
        if policy is DateOnlyPolicy.COMPLETED_DATE:
            return AvailabilityDecision(available_date < written_cutoff_date)
        included = available_date <= written_cutoff_date
        warnings = (
            ("date_only_same_day_intraday_safety_not_established",)
            if included and available_date == written_cutoff_date
            else ()
        )
        return AvailabilityDecision(included, warnings)

    assert availability.precision is TemporalPrecision.DATETIME
    assert isinstance(availability.value, datetime)
    if cutoff.precision is TemporalPrecision.DATE:
        assert isinstance(cutoff.value, date) and not isinstance(cutoff.value, datetime)
        return AvailabilityDecision(availability.value.date() <= cutoff.value)
    assert isinstance(cutoff.value, datetime)
    available_instant = availability.value.astimezone(timezone.utc)
    cutoff_instant = cutoff.value.astimezone(timezone.utc)
    return AvailabilityDecision(available_instant <= cutoff_instant)
