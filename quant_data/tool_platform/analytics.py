"""Pure deterministic Stage 5 analytics over supplied in-memory values."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
import hashlib
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from quant_data.contracts import TimeSeries
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict

MAX_OBSERVATIONS = 10_000
MAX_INPUT_SERIES = 20
MAX_RETURN_HORIZON = 252
MAX_REGRESSION_ROWS = 5_000
MAX_REGRESSION_COLUMNS = 20
MAX_ROLLING_ESTIMATES = 25_000
MAX_EVENT_WINDOW = 252
_PRECISION = 34
_FREQUENCY_ORDER = {"daily": 1, "weekly": 2, "monthly": 3, "quarterly": 4, "annual": 5}


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a nonempty string")
    return value


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must use ISO calendar-date precision") from exc


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be a finite Decimal or integer")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise ValidationError(f"{field} must be a finite Decimal or integer; floats are rejected")


def _strings(values: Sequence[str], field: str, maximum: int = 128) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > maximum:
        raise ValidationError(f"{field} exceeds the supported bound")
    result = []
    for item in values:
        text = _text(item, field)
        if text not in result:
            result.append(text)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AnalyticsObservation:
    period_start: str
    period_end: str
    value: Decimal | None
    missing_reason: str | None
    source_lineage: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _date(self.period_end, "period_end") < _date(self.period_start, "period_start"):
            raise ValidationError("period_end cannot precede period_start")
        if (self.value is None) == (self.missing_reason is None):
            raise ValidationError("exactly one of value or missing_reason is required")
        if self.value is not None:
            object.__setattr__(self, "value", _decimal(self.value, "value"))
        if self.missing_reason is not None:
            object.__setattr__(self, "missing_reason", _text(self.missing_reason, "missing_reason"))
        object.__setattr__(self, "source_lineage", _strings(self.source_lineage, "source_lineage"))
        object.__setattr__(self, "quality_flags", _strings(self.quality_flags, "quality_flags"))

    def to_primitive(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "value": self.value,
            "missing_reason": self.missing_reason,
            "source_lineage": list(self.source_lineage),
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True, slots=True)
class AnalyticsSeries:
    series_id: str
    unit: str
    frequency: str
    observations: tuple[AnalyticsObservation, ...]
    lineage_digests: tuple[str, ...]
    transformations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _text(self.series_id, "series_id"))
        object.__setattr__(self, "unit", _text(self.unit, "unit"))
        frequency = _text(self.frequency, "frequency")
        if frequency not in _FREQUENCY_ORDER:
            raise ValidationError("unsupported analytics frequency")
        object.__setattr__(self, "frequency", frequency)
        observations = tuple(self.observations)
        if len(observations) > MAX_OBSERVATIONS:
            raise ResourceLimitError("series exceeds the observation limit")
        if not all(isinstance(item, AnalyticsObservation) for item in observations):
            raise ValidationError("AnalyticsSeries observations must be typed")
        prior: tuple[date, date] | None = None
        for item in observations:
            current = (_date(item.period_start, "period_start"), _date(item.period_end, "period_end"))
            if prior is not None and current <= prior:
                raise ValidationError("observations must be strictly ordered without duplicates")
            prior = current
        object.__setattr__(self, "observations", observations)
        lineage = _strings(self.lineage_digests, "lineage_digests")
        if not lineage:
            raise ValidationError("input lineage is required")
        object.__setattr__(self, "lineage_digests", lineage)
        object.__setattr__(self, "transformations", _strings(self.transformations, "transformations"))

    def to_primitive(self) -> dict[str, Any]:
        return {
            "contract": "quant_data.analytics_series",
            "contract_version": "1.0.0",
            "series_id": self.series_id,
            "unit": self.unit,
            "frequency": self.frequency,
            "observations": [item.to_primitive() for item in self.observations],
            "lineage_digests": list(self.lineage_digests),
            "transformations": list(self.transformations),
        }

    @property
    def lineage_digest(self) -> str:
        return hashlib.sha256(dumps_strict(self.to_primitive()).encode("utf-8")).hexdigest()


def as_analytics_series(value: TimeSeries | AnalyticsSeries) -> AnalyticsSeries:
    """Copy an existing typed series into the immutable analytics boundary."""

    if isinstance(value, AnalyticsSeries):
        return value
    if not isinstance(value, TimeSeries):
        raise ValidationError("analytics requires TimeSeries or AnalyticsSeries")
    value.validate_lineage()
    unit, frequency = value.metadata.get("unit"), value.metadata.get("frequency")
    if not isinstance(unit, str) or not isinstance(frequency, str):
        raise ValidationError("TimeSeries metadata must declare unit and frequency")
    return AnalyticsSeries(
        value.series_id,
        unit,
        frequency,
        tuple(
            AnalyticsObservation(
                item.period_start,
                item.period_end,
                item.value,
                item.missing_reason,
                (item.version_id, item.evidence_id, item.snapshot_id),
                tuple(item.quality_flags),
            )
            for item in value.observations
        ),
        (value.lineage_digest,),
    )


def describe_series(value: TimeSeries | AnalyticsSeries) -> dict[str, Any]:
    """Describe supplied data without imputing or discarding missing observations."""

    series = as_analytics_series(value)
    known = [item.value for item in series.observations if item.value is not None]
    missing = len(series.observations) - len(known)
    warnings = [] if known else ["all_observations_missing"]
    if not series.observations:
        warnings.append("empty_series")
    if len(known) < 2 and known:
        warnings.append("insufficient_sample_for_variance")
    if not known:
        minimum = maximum = mean = variance = None
        status = "not_established"
    else:
        with localcontext() as context:
            context.prec = _PRECISION
            mean = sum(known, Decimal("0")) / Decimal(len(known))
            minimum, maximum = min(known), max(known)
            variance = None if len(known) < 2 else sum(
                ((item - mean) ** 2 for item in known), Decimal("0")
            ) / Decimal(len(known) - 1)
        status = "established"
    return {
        "status": status,
        "count": len(series.observations),
        "nonmissing_count": len(known),
        "missing_count": missing,
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "sample_variance": variance,
        "warnings": tuple(sorted(warnings)),
        "input_lineage": series.lineage_digests,
    }


def _bucket(current: date, frequency: str) -> tuple[date, date]:
    if frequency == "monthly":
        month = current.month
        start = date(current.year, month, 1)
        next_month = date(current.year + (month == 12), 1 if month == 12 else month + 1, 1)
    elif frequency == "quarterly":
        month = ((current.month - 1) // 3) * 3 + 1
        start = date(current.year, month, 1)
        next_month = date(current.year + (month == 10), 1 if month == 10 else month + 3, 1)
    elif frequency == "annual":
        start, next_month = date(current.year, 1, 1), date(current.year + 1, 1, 1)
    else:
        raise ValidationError("only monthly, quarterly, and annual resampling are supported")
    return start, date.fromordinal(next_month.toordinal() - 1)


def _expected_source_periods(
    start: date, end: date, source_frequency: str
) -> tuple[tuple[date, date], ...] | None:
    """Return the only source periods that prove a target bucket is complete.

    Daily inputs must explicitly cover every calendar day. Weekly inputs are
    accepted only when their declared periods form consecutive seven-day
    intervals from the target-bucket boundary, with one final short interval
    allowed at the bucket end. Monthly and quarterly inputs must use their
    exact calendar periods. A source layout outside these rules is not enough
    evidence to value a derived calendar bucket.
    """

    if source_frequency == "daily":
        result: list[tuple[date, date]] = []
        current = start
        while current <= end:
            result.append((current, current))
            current = date.fromordinal(current.toordinal() + 1)
        return tuple(result)

    if source_frequency == "weekly":
        result = []
        current = start
        while current <= end:
            interval_end = min(end, date.fromordinal(current.toordinal() + 6))
            result.append((current, interval_end))
            current = date.fromordinal(interval_end.toordinal() + 1)
        return tuple(result)

    if source_frequency in {"monthly", "quarterly"}:
        result = []
        current = start
        while current <= end:
            period_start, period_end = _bucket(current, source_frequency)
            if period_start != current or period_end > end:
                return None
            result.append((period_start, period_end))
            current = date.fromordinal(period_end.toordinal() + 1)
        return tuple(result)

    # An annual input has no higher supported target, and any future source
    # frequency must add an explicit coverage proof rather than relying on a
    # row count or a guessed calendar.
    return None


def _bucket_coverage_is_proven(
    items: Sequence[AnalyticsObservation],
    *,
    start: date,
    end: date,
    source_frequency: str,
) -> bool:
    """Require exact source-period coverage before aggregation.

    This deliberately rejects gaps, duplicates, intervals crossing the target
    boundary, and irregular source-period layouts. A partial source series
    therefore remains an explicit missing derived observation rather than a
    misleading calendar aggregate.
    """

    expected = _expected_source_periods(start, end, source_frequency)
    if expected is None or len(items) != len(expected):
        return False
    actual = tuple(
        (
            _date(item.period_start, "period_start"),
            _date(item.period_end, "period_end"),
        )
        for item in items
    )
    return actual == expected


def resample_series(
    value: TimeSeries | AnalyticsSeries, *, target_frequency: str, method: str = "mean"
) -> AnalyticsSeries:
    """Aggregate complete calendar buckets only; incomplete buckets remain missing."""

    series = as_analytics_series(value)
    if target_frequency not in _FREQUENCY_ORDER or method not in {"mean", "sum", "last"}:
        raise ValidationError("unsupported resample target or method")
    if _FREQUENCY_ORDER[target_frequency] < _FREQUENCY_ORDER[series.frequency]:
        raise ValidationError("upsampling would invent observations")
    if target_frequency == series.frequency:
        return AnalyticsSeries(
            series.series_id, series.unit, series.frequency, series.observations,
            series.lineage_digests, (*series.transformations, "resample:identity"),
        )
    groups: dict[tuple[date, date], list[AnalyticsObservation]] = defaultdict(list)
    for item in series.observations:
        groups[_bucket(_date(item.period_start, "period_start"), target_frequency)].append(item)
    result = []
    for (start, end), items in sorted(groups.items()):
        lineage = tuple(token for item in items for token in item.source_lineage)
        flags = tuple(sorted({flag for item in items for flag in item.quality_flags}))
        if not _bucket_coverage_is_proven(
            items,
            start=start,
            end=end,
            source_frequency=series.frequency,
        ) or any(item.value is None for item in items):
            output = (None, "resample_incomplete_bucket")
        elif method == "last":
            chosen = items[-1]
            output = (chosen.value, None)
        else:
            with localcontext() as context:
                context.prec = _PRECISION
                total = sum((item.value for item in items if item.value is not None), Decimal("0"))
                output = (total if method == "sum" else total / Decimal(len(items)), None)
        result.append(AnalyticsObservation(start.isoformat(), end.isoformat(), output[0], output[1],
            lineage, flags))
    return AnalyticsSeries(series.series_id, series.unit, target_frequency, tuple(result),
        series.lineage_digests, (*series.transformations, f"resample:{method}:{target_frequency}"))


def align_series(
    values: Sequence[TimeSeries | AnalyticsSeries], *, join: str = "inner"
) -> dict[str, Any]:
    """Align matching unit/frequency series only under an explicit join policy."""

    if isinstance(values, (str, bytes)) or not values or len(values) > MAX_INPUT_SERIES:
        raise ValidationError("alignment requires one to twenty input series")
    series = tuple(as_analytics_series(value) for value in values)
    if len({item.series_id for item in series}) != len(series):
        raise ValidationError("alignment requires distinct series identities")
    if any(item.unit != series[0].unit or item.frequency != series[0].frequency for item in series):
        raise ValidationError("alignment rejects contradictory units or frequencies")
    if join not in {"inner", "outer"}:
        raise ValidationError("join must be inner or outer")
    indexed = [{(item.period_start, item.period_end): item for item in analytic.observations}
        for analytic in series]
    key_sets = [set(index) for index in indexed]
    keys = set.intersection(*key_sets) if join == "inner" else set.union(*key_sets)
    rows = []
    for start, end in sorted(keys):
        cells = tuple(index.get((start, end)) for index in indexed)
        rows.append({
            "period_start": start,
            "period_end": end,
            "values": tuple(None if cell is None else cell.value for cell in cells),
            "missing_reasons": tuple("absent_from_series" if cell is None else cell.missing_reason
                for cell in cells),
        })
    return {
        "series_ids": tuple(item.series_id for item in series),
        "unit": series[0].unit,
        "frequency": series[0].frequency,
        "join": join,
        "rows": tuple(rows),
        "input_lineage": tuple(token for item in series for token in item.lineage_digests),
    }


def return_series(
    value: TimeSeries | AnalyticsSeries,
    *,
    horizon: int = 1,
    direction: str = "trailing",
    method: str = "simple",
) -> AnalyticsSeries:
    """Build explicit trailing or forward returns without filling missing data."""

    series = as_analytics_series(value)
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= MAX_RETURN_HORIZON:
        raise ValidationError("horizon must be an integer from 1 through 252")
    if direction not in {"trailing", "forward"} or method not in {"simple", "log"}:
        raise ValidationError("unsupported return direction or method")
    output = []
    for index, anchor in enumerate(series.observations):
        other_index = index - horizon if direction == "trailing" else index + horizon
        if not 0 <= other_index < len(series.observations):
            output.append(AnalyticsObservation(anchor.period_start, anchor.period_end, None,
                "insufficient_history" if direction == "trailing" else "insufficient_forward_horizon",
                anchor.source_lineage, anchor.quality_flags))
            continue
        other = series.observations[other_index]
        base, ending = ((other.value, anchor.value) if direction == "trailing"
            else (anchor.value, other.value))
        lineage = (*anchor.source_lineage, *other.source_lineage)
        flags = tuple(sorted(set((*anchor.quality_flags, *other.quality_flags))))
        if base is None or ending is None:
            result, reason = None, "return_input_missing"
        elif base == 0:
            result, reason = None, "zero_return_base"
        elif method == "log" and (base <= 0 or ending <= 0):
            result, reason = None, "nonpositive_log_return_input"
        else:
            with localcontext() as context:
                context.prec = _PRECISION
                ratio = ending / base
                result = ratio - Decimal("1") if method == "simple" else ratio.ln()
            reason = None
        output.append(AnalyticsObservation(anchor.period_start, anchor.period_end, result, reason,
            lineage, flags))
    return AnalyticsSeries(f"{series.series_id}:{direction}_{method}_return:{horizon}", "fraction",
        series.frequency, tuple(output), series.lineage_digests,
        (*series.transformations, f"return:{direction}:{method}:horizon={horizon}"))


def forward_return_series(value: TimeSeries | AnalyticsSeries, *, horizon: int = 1,
    method: str = "simple") -> AnalyticsSeries:
    return return_series(value, horizon=horizon, direction="forward", method=method)


def _not_established(reason: str, **details: Any) -> dict[str, Any]:
    return {"status": "not_established", "reason": _text(reason, "reason"), "warnings": (reason,), **details}


def correlate_series(
    left: TimeSeries | AnalyticsSeries,
    right: TimeSeries | AnalyticsSeries,
    *,
    join: str = "inner",
) -> dict[str, Any]:
    """Return correlation only for complete, non-degenerate aligned pairs.

    The legacy default remains inner alignment.  Explicit outer alignment lets
    versioned callers count periods absent from either input as exclusions
    without changing the coefficient's complete-pair sample.
    """

    aligned = align_series((left, right), join=join)
    pairs = [(row["values"][0], row["values"][1]) for row in aligned["rows"]
        if row["values"][0] is not None and row["values"][1] is not None]
    excluded = len(aligned["rows"]) - len(pairs)
    if len(pairs) < 2:
        return _not_established("insufficient_complete_aligned_pairs", sample_size=len(pairs),
            excluded_count=excluded, input_lineage=aligned["input_lineage"])
    with localcontext() as context:
        context.prec = _PRECISION
        left_mean = sum((pair[0] for pair in pairs), Decimal("0")) / Decimal(len(pairs))
        right_mean = sum((pair[1] for pair in pairs), Decimal("0")) / Decimal(len(pairs))
        left_sum = sum(((pair[0] - left_mean) ** 2 for pair in pairs), Decimal("0"))
        right_sum = sum(((pair[1] - right_mean) ** 2 for pair in pairs), Decimal("0"))
        if left_sum == 0 or right_sum == 0:
            return _not_established("zero_variance", sample_size=len(pairs), excluded_count=excluded,
                input_lineage=aligned["input_lineage"])
        coefficient = sum(((a - left_mean) * (b - right_mean) for a, b in pairs),
            Decimal("0")) / (left_sum * right_sum).sqrt()
    return {"status": "established", "coefficient": coefficient, "sample_size": len(pairs),
        "excluded_count": excluded, "warnings": (), "input_lineage": aligned["input_lineage"]}


def _solve(matrix: list[list[Decimal]], rhs: list[Decimal]) -> tuple[Decimal, ...] | None:
    size = len(rhs)
    augmented = [list(row) + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if augmented[pivot][column] == 0:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [item / divisor for item in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                if factor:
                    augmented[row] = [item - factor * base for item, base in
                        zip(augmented[row], augmented[column], strict=True)]
    return tuple(row[-1] for row in augmented)


def _normal_decimal(value: Decimal) -> Decimal:
    nearest = value.to_integral_value()
    return nearest if abs(value - nearest) <= Decimal("1e-28") else value


def ordinary_least_squares(y: Sequence[Decimal | int | None], x: Sequence[Sequence[Decimal | int | None]],
    *, intercept: bool = True) -> dict[str, Any]:
    """Fit bounded Decimal OLS; insufficient or singular samples stay explicit."""

    if len(y) != len(x):
        raise ValidationError("OLS y and x must have equal row counts")
    if len(y) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("OLS exceeds the supported row limit")
    width = len(x[0]) if x else 0
    if not 1 <= width <= MAX_REGRESSION_COLUMNS or any(len(row) != width for row in x):
        raise ValidationError("OLS requires equal nonempty bounded feature rows")
    accepted: list[tuple[Decimal, list[Decimal]]] = []
    excluded = 0
    for index, (target, row) in enumerate(zip(y, x, strict=True)):
        if target is None or any(item is None for item in row):
            excluded += 1
        else:
            accepted.append((_decimal(target, f"y/{index}"), [_decimal(item, f"x/{index}") for item in row]))
    parameters = width + int(intercept)
    if len(accepted) <= parameters:
        return _not_established("insufficient_degrees_of_freedom", sample_size=len(accepted),
            excluded_count=excluded, parameter_count=parameters)
    with localcontext() as context:
        context.prec = _PRECISION
        design = [([Decimal("1")] if intercept else []) + row for _, row in accepted]
        targets = [target for target, _ in accepted]
        gram = [[sum((row[left] * row[right] for row in design), Decimal("0"))
            for right in range(parameters)] for left in range(parameters)]
        rhs = [sum((row[column] * target for row, target in zip(design, targets, strict=True)),
            Decimal("0")) for column in range(parameters)]
        coefficients = _solve(gram, rhs)
        if coefficients is None:
            return _not_established("singular_design", sample_size=len(accepted),
                excluded_count=excluded, parameter_count=parameters)
        coefficients = tuple(_normal_decimal(item) for item in coefficients)
        residuals = [target - sum((row[index] * coefficients[index]
            for index in range(parameters)), Decimal("0")) for row, target in zip(design, targets, strict=True)]
        rss = sum((item * item for item in residuals), Decimal("0"))
        mean = sum(targets, Decimal("0")) / Decimal(len(targets))
        tss = sum(((item - mean) ** 2 for item in targets), Decimal("0"))
    return {"status": "established", "coefficients": coefficients,
        "intercept": coefficients[0] if intercept else None,
        "feature_coefficients": coefficients[1:] if intercept else coefficients,
        "sample_size": len(accepted), "excluded_count": excluded, "parameter_count": parameters,
        "residual_sum_squares": rss, "r_squared": None if tss == 0 else Decimal("1") - rss / tss,
        "warnings": ("constant_target",) if tss == 0 else ()}


def rolling_regression(y: Sequence[Decimal | int | None], x: Sequence[Sequence[Decimal | int | None]],
    *, window: int, intercept: bool = True) -> tuple[dict[str, Any], ...]:
    """Apply the exact OLS contract to bounded contiguous windows."""

    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValidationError("window must be an integer of at least two")
    if len(y) != len(x):
        raise ValidationError("rolling inputs must have equal lengths")
    count = max(0, len(y) - window + 1)
    if count > MAX_ROLLING_ESTIMATES:
        raise ResourceLimitError("rolling regression exceeds the estimate limit")
    return tuple(ordinary_least_squares(y[index:index + window], x[index:index + window],
        intercept=intercept) for index in range(count))


def _complete(values: Sequence[Decimal | int | None], field: str) -> tuple[tuple[Decimal, ...], int]:
    if isinstance(values, (str, bytes)) or len(values) > MAX_OBSERVATIONS:
        raise ResourceLimitError(f"{field} exceeds the observation limit")
    known: list[Decimal] = []
    excluded = 0
    for index, value in enumerate(values):
        if value is None:
            excluded += 1
        else:
            known.append(_decimal(value, f"{field}/{index}"))
    return tuple(known), excluded


def stationarity_diagnostics(values: Sequence[Decimal | int | None]) -> dict[str, Any]:
    """Offer diagnostics only; never pretend that a unit-root decision exists."""

    sample, excluded = _complete(values, "stationarity")
    if excluded:
        return _not_established("missing_observations_prevent_contiguous_diagnostic",
            sample_size=len(sample), excluded_count=excluded, assessment="not_established")
    if len(sample) < 3:
        return _not_established("insufficient_sample", sample_size=len(sample), excluded_count=0,
            assessment="not_established")
    with localcontext() as context:
        context.prec = _PRECISION
        differences = tuple(sample[index] - sample[index - 1] for index in range(1, len(sample)))
        mean = sum(differences, Decimal("0")) / Decimal(len(differences))
        variance = sum(((item - mean) ** 2 for item in differences), Decimal("0")) / Decimal(len(differences) - 1)
    return {"status": "established", "assessment": "diagnostic_only", "sample_size": len(sample),
        "first_difference_mean": mean, "first_difference_sample_variance": variance,
        "warnings": ("no_unit_root_test_or_critical_values_available",)}


def event_study(returns: Sequence[Decimal | int | None], *, event_index: int, pre: int, post: int) -> dict[str, Any]:
    """Compute a bounded cumulative-return window supplied by the caller."""

    if any(isinstance(item, bool) or not isinstance(item, int) for item in (event_index, pre, post)):
        raise ValidationError("event index and windows must be integers")
    if pre < 0 or post < 0 or pre + post + 1 > MAX_EVENT_WINDOW:
        raise ResourceLimitError("event window exceeds the supported bound")
    if len(returns) > MAX_OBSERVATIONS:
        raise ResourceLimitError("event return input exceeds the observation limit")
    start, end = event_index - pre, event_index + post
    if not 0 <= event_index < len(returns) or start < 0 or end >= len(returns):
        return _not_established("event_window_outside_sample", sample_size=0, excluded_count=0)
    window = returns[start:end + 1]
    if any(item is None for item in window):
        return _not_established("missing_returns_in_event_window",
            sample_size=sum(item is not None for item in window), excluded_count=sum(item is None for item in window))
    cumulative = Decimal("1")
    with localcontext() as context:
        context.prec = _PRECISION
        for index, item in enumerate(window):
            value = _decimal(item, f"returns/{start + index}")
            if value <= Decimal("-1"):
                return _not_established("return_at_or_below_negative_one", sample_size=index, excluded_count=0)
            cumulative *= Decimal("1") + value
    return {"status": "established", "window_start": start, "window_end": end,
        "sample_size": len(window), "cumulative_return": cumulative - Decimal("1"), "warnings": ()}


def walk_forward_summary(predictions: Sequence[Decimal | int | None], actuals: Sequence[Decimal | int | None]) -> dict[str, Any]:
    """Summarize paired synthetic forecasts while reporting excluded pairs."""

    if len(predictions) != len(actuals):
        raise ValidationError("predictions and actuals must have equal lengths")
    if len(predictions) > MAX_OBSERVATIONS:
        raise ResourceLimitError("walk-forward input exceeds the observation limit")
    pairs: list[tuple[Decimal, Decimal]] = []
    excluded = 0
    for index, (prediction, actual) in enumerate(zip(predictions, actuals, strict=True)):
        if prediction is None or actual is None:
            excluded += 1
        else:
            pairs.append((_decimal(prediction, f"predictions/{index}"), _decimal(actual, f"actuals/{index}")))
    if len(pairs) < 2:
        return _not_established("insufficient_complete_forecast_pairs", sample_size=len(pairs),
            excluded_count=excluded)
    with localcontext() as context:
        context.prec = _PRECISION
        errors = tuple(prediction - actual for prediction, actual in pairs)
        mean = sum(errors, Decimal("0")) / Decimal(len(errors))
        mae = sum((abs(item) for item in errors), Decimal("0")) / Decimal(len(errors))
        mse = sum((item * item for item in errors), Decimal("0")) / Decimal(len(errors))
        rmse = mse.sqrt()
    return {"status": "established", "sample_size": len(pairs), "excluded_count": excluded,
        "mean_error": mean, "mean_absolute_error": mae, "root_mean_squared_error": rmse, "warnings": ()}


ANALYTIC_PRIMITIVES: Mapping[str, Callable[..., Any]] = MappingProxyType({
    "timeseries.describe": describe_series,
    "timeseries.resample": resample_series,
    "timeseries.align": align_series,
    "timeseries.returns": return_series,
    "market.forward_returns": forward_return_series,
    "timeseries.correlation": correlate_series,
    "econometrics.regression": ordinary_least_squares,
    "econometrics.rolling_regression": rolling_regression,
    "econometrics.stationarity": stationarity_diagnostics,
    "research.event_study": event_study,
    "research.walk_forward_summary": walk_forward_summary,
})


def analytic_primitive(name: str) -> Callable[..., Any]:
    """Resolve only server-owned primitive names."""

    try:
        return ANALYTIC_PRIMITIVES[name]
    except (KeyError, TypeError) as exc:
        raise ValidationError("Unknown analytics primitive") from exc


__all__ = [
    "ANALYTIC_PRIMITIVES", "AnalyticsObservation", "AnalyticsSeries", "MAX_EVENT_WINDOW",
    "MAX_INPUT_SERIES", "MAX_OBSERVATIONS", "MAX_REGRESSION_COLUMNS", "MAX_REGRESSION_ROWS",
    "MAX_RETURN_HORIZON", "MAX_ROLLING_ESTIMATES", "align_series", "analytic_primitive",
    "as_analytics_series", "correlate_series", "describe_series", "event_study",
    "forward_return_series", "ordinary_least_squares", "resample_series", "return_series",
    "rolling_regression", "stationarity_diagnostics", "walk_forward_summary",
]
