"""Pure deterministic transforms and diagnostics for Stage 10 return series.

This module owns no store, public-tool, or Stage 10 selection logic. Callers
validate the trailing-return contract before invoking these kernels; the
kernels preserve the supplied immutable TimeSeries identity and expose only
typed immutable results that an adapter can render.

All arithmetic under this module's control runs at Decimal precision 34. The
only binary64 boundary is the established, explicitly labelled chi-square
survival backend reused from econometrics for Ljung--Box.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from quant_data.contracts import TimeSeries
from quant_data.errors import ResourceLimitError, ValidationError

from .econometrics import CHI_SQUARE_INFERENCE_BACKEND, _chi_square_survival


DECIMAL_PRECISION = 34
MAX_TRANSFORM_OBSERVATIONS = 10_000
MAX_ROLLING_WINDOW = 10_000
MAX_AUTOCORRELATION_LAG = 252
CHI_SQUARE_REFERENCE_DISTRIBUTION = "chi_square_asymptotic"

_ROLLING_STATISTICS = frozenset(
    {"mean", "sample_standard_deviation", "minimum", "maximum"}
)


@dataclass(frozen=True, slots=True)
class RollingStatisticPoint:
    index: int
    period_start: str
    period_end: str
    window_start_index: int | None
    window_end_index: int
    value: Decimal | None
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class RollingStatisticResult:
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    statistic: str
    window: int
    input_series_id: str
    input_lineage_digest: str
    input_count: int
    established_count: int
    not_established_count: int
    points: tuple[RollingStatisticPoint, ...]


@dataclass(frozen=True, slots=True)
class AutocorrelationPoint:
    lag: int
    value: Decimal | None
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class AutocorrelationResult:
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_series_id: str
    input_lineage_digest: str
    max_lag: int
    sample_size: int
    excluded_count: int
    trimmed_leading_count: int
    trimmed_trailing_count: int
    interior_missing_count: int
    first_included_index: int | None
    last_included_index: int | None
    mean: Decimal | None
    centered_sum_squares: Decimal | None
    coefficients: tuple[AutocorrelationPoint, ...]


@dataclass(frozen=True, slots=True)
class PartialAutocorrelationResult:
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_series_id: str
    input_lineage_digest: str
    max_lag: int
    sample_size: int
    excluded_count: int
    trimmed_leading_count: int
    trimmed_trailing_count: int
    interior_missing_count: int
    first_included_index: int | None
    last_included_index: int | None
    mean: Decimal | None
    centered_sum_squares: Decimal | None
    innovation_variance: Decimal | None
    autocorrelations: tuple[AutocorrelationPoint, ...]
    coefficients: tuple[AutocorrelationPoint, ...]


@dataclass(frozen=True, slots=True)
class LjungBoxResult:
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_series_id: str
    input_lineage_digest: str
    lag: int
    model_degrees_of_freedom: int
    degrees_of_freedom: int | None
    sample_size: int
    excluded_count: int
    trimmed_leading_count: int
    trimmed_trailing_count: int
    interior_missing_count: int
    first_included_index: int | None
    last_included_index: int | None
    statistic: Decimal | None
    p_value: Decimal | None
    reference_distribution: str
    inference_backend: str
    autocorrelations: tuple[AutocorrelationPoint, ...]


@dataclass(frozen=True, slots=True)
class DrawdownPoint:
    index: int
    period_start: str
    period_end: str
    wealth: Decimal
    high_water_mark: Decimal
    high_water_mark_index: int | None
    drawdown: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownEpisode:
    peak_index: int | None
    peak_wealth: Decimal
    start_index: int
    trough_index: int
    trough_wealth: Decimal
    maximum_drawdown: Decimal
    recovery_index: int | None
    recovery_wealth: Decimal | None
    recovered: bool


@dataclass(frozen=True, slots=True)
class DrawdownResult:
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_series_id: str
    input_lineage_digest: str
    method: str
    initial_wealth: Decimal
    sample_size: int
    excluded_count: int
    trimmed_leading_count: int
    trimmed_trailing_count: int
    interior_missing_count: int
    first_included_index: int | None
    last_included_index: int | None
    terminal_wealth: Decimal | None
    terminal_high_water_mark: Decimal | None
    terminal_high_water_mark_index: int | None
    points: tuple[DrawdownPoint, ...]
    episodes: tuple[DrawdownEpisode, ...]


@dataclass(frozen=True, slots=True)
class _PreparedSample:
    values: tuple[Decimal, ...]
    first_index: int | None
    last_index: int | None
    trimmed_leading_count: int
    trimmed_trailing_count: int
    interior_missing_count: int

    @property
    def excluded_count(self) -> int:
        return self.trimmed_leading_count + self.trimmed_trailing_count


def _clean_decimal(value: Decimal) -> Decimal:
    return Decimal("0") if value.is_zero() else +value


def _warnings(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _validate_series(value: TimeSeries) -> TimeSeries:
    if not isinstance(value, TimeSeries):
        raise ValidationError("market transform requires a typed TimeSeries")
    value.validate_lineage()
    if len(value.observations) > MAX_TRANSFORM_OBSERVATIONS:
        raise ResourceLimitError("market transform input exceeds the observation limit")
    return value


def _validate_window(value: object, *, statistic: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("rolling window must be an integer")
    if not 1 <= value <= MAX_ROLLING_WINDOW:
        raise ValidationError("rolling window is outside the supported bound")
    if statistic == "sample_standard_deviation" and value < 2:
        raise ValidationError(
            "sample standard deviation requires a rolling window of at least two"
        )
    return value


def _validate_max_lag(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("max_lag must be an integer")
    if not 0 <= value <= MAX_AUTOCORRELATION_LAG:
        raise ValidationError("max_lag is outside the supported bound")
    return value


def _validate_ljung_box_lag(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("lag must be an integer")
    if not 1 <= value <= MAX_AUTOCORRELATION_LAG:
        raise ValidationError("lag is outside the supported bound")
    return value


def _prepared_sample(series: TimeSeries) -> _PreparedSample:
    observations = series.observations
    first = next(
        (
            index
            for index, observation in enumerate(observations)
            if observation.value is not None
        ),
        None,
    )
    if first is None:
        return _PreparedSample((), None, None, len(observations), 0, 0)
    last = next(
        index
        for index in range(len(observations) - 1, -1, -1)
        if observations[index].value is not None
    )
    interior_missing = sum(
        observations[index].value is None for index in range(first, last + 1)
    )
    values: tuple[Decimal, ...] = ()
    if not interior_missing:
        values = tuple(
            observations[index].value
            for index in range(first, last + 1)
            if observations[index].value is not None
        )
    return _PreparedSample(
        values,
        first,
        last,
        first,
        len(observations) - last - 1,
        interior_missing,
    )


def _missing_coefficients(
    max_lag: int, reason: str
) -> tuple[AutocorrelationPoint, ...]:
    return tuple(
        AutocorrelationPoint(lag, None, reason)
        for lag in range(max_lag + 1)
    )


def _terminal_missing_warning(sample: _PreparedSample) -> tuple[str, ...]:
    return (
        ("leading_trailing_missing_observations_trimmed",)
        if sample.excluded_count
        else ()
    )


def _not_established_acf(
    series: TimeSeries,
    *,
    max_lag: int,
    sample: _PreparedSample,
    reason: str,
) -> AutocorrelationResult:
    return AutocorrelationResult(
        status="not_established",
        reason=reason,
        warnings=_warnings(reason, *_terminal_missing_warning(sample)),
        input_series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        max_lag=max_lag,
        sample_size=len(sample.values),
        excluded_count=sample.excluded_count,
        trimmed_leading_count=sample.trimmed_leading_count,
        trimmed_trailing_count=sample.trimmed_trailing_count,
        interior_missing_count=sample.interior_missing_count,
        first_included_index=sample.first_index,
        last_included_index=sample.last_index,
        mean=None,
        centered_sum_squares=None,
        coefficients=_missing_coefficients(max_lag, reason),
    )


def _autocorrelation_from_prepared(
    series: TimeSeries, *, max_lag: int, sample: _PreparedSample
) -> AutocorrelationResult:
    if sample.interior_missing_count:
        return _not_established_acf(
            series,
            max_lag=max_lag,
            sample=sample,
            reason="interior_missing_observations",
        )
    count = len(sample.values)
    if count < 2:
        return _not_established_acf(
            series,
            max_lag=max_lag,
            sample=sample,
            reason="insufficient_sample_for_autocorrelation",
        )
    if max_lag >= count:
        return _not_established_acf(
            series,
            max_lag=max_lag,
            sample=sample,
            reason="insufficient_sample_for_requested_lag",
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        mean = sum(sample.values, Decimal("0")) / Decimal(count)
        centered = tuple(value - mean for value in sample.values)
        denominator = sum((value * value for value in centered), Decimal("0"))
        if denominator <= 0:
            return _not_established_acf(
                series,
                max_lag=max_lag,
                sample=sample,
                reason="zero_variance",
            )
        coefficients = [AutocorrelationPoint(0, Decimal("1"), None)]
        for lag in range(1, max_lag + 1):
            numerator = sum(
                (
                    centered[index] * centered[index - lag]
                    for index in range(lag, count)
                ),
                Decimal("0"),
            )
            coefficients.append(
                AutocorrelationPoint(
                    lag,
                    _clean_decimal(numerator / denominator),
                    None,
                )
            )
        return AutocorrelationResult(
            status="established",
            reason=None,
            warnings=_terminal_missing_warning(sample),
            input_series_id=series.series_id,
            input_lineage_digest=series.lineage_digest,
            max_lag=max_lag,
            sample_size=count,
            excluded_count=sample.excluded_count,
            trimmed_leading_count=sample.trimmed_leading_count,
            trimmed_trailing_count=sample.trimmed_trailing_count,
            interior_missing_count=0,
            first_included_index=sample.first_index,
            last_included_index=sample.last_index,
            mean=_clean_decimal(mean),
            centered_sum_squares=_clean_decimal(denominator),
            coefficients=tuple(coefficients),
        )



def rolling_statistic(
    series: TimeSeries, *, statistic: str, window: int
) -> RollingStatisticResult:
    """Return one fixed-window statistic anchored to every input observation."""

    series = _validate_series(series)
    if statistic not in _ROLLING_STATISTICS:
        raise ValidationError("unsupported rolling statistic")
    window = _validate_window(window, statistic=statistic)
    points: list[RollingStatisticPoint] = []
    for index, anchor in enumerate(series.observations):
        if index + 1 < window:
            points.append(
                RollingStatisticPoint(
                    index, anchor.period_start, anchor.period_end, None, index,
                    None, "insufficient_history",
                )
            )
            continue
        window_start = index - window + 1
        raw_values = tuple(
            observation.value
            for observation in series.observations[window_start : index + 1]
        )
        if any(value is None for value in raw_values):
            points.append(
                RollingStatisticPoint(
                    index, anchor.period_start, anchor.period_end, window_start,
                    index, None, "rolling_window_input_missing",
                )
            )
            continue
        values = tuple(value for value in raw_values if value is not None)
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            if statistic == "mean":
                calculated = sum(values, Decimal("0")) / Decimal(window)
            elif statistic == "sample_standard_deviation":
                mean = sum(values, Decimal("0")) / Decimal(window)
                variance = sum(
                    ((value - mean) ** 2 for value in values), Decimal("0")
                ) / Decimal(window - 1)
                calculated = variance.sqrt()
            elif statistic == "minimum":
                calculated = min(values)
            else:
                calculated = max(values)
        points.append(
            RollingStatisticPoint(
                index, anchor.period_start, anchor.period_end, window_start,
                index, _clean_decimal(calculated), None,
            )
        )
    established_count = sum(point.value is not None for point in points)
    not_established_count = len(points) - established_count
    if established_count:
        status = "established"
        reason = None
        warnings: tuple[str, ...] = ()
    elif not points:
        status = "not_established"
        reason = "empty_series"
        warnings = ("empty_series",)
    else:
        missing_reasons = tuple(
            sorted(
                {
                    point.missing_reason
                    for point in points
                    if point.missing_reason is not None
                }
            )
        )
        status = "not_established"
        if len(missing_reasons) == 1:
            reason = f"all_windows_{missing_reasons[0]}"
            warnings = (reason,)
        else:
            reason = "all_windows_not_established"
            warnings = (reason, *missing_reasons)
    return RollingStatisticResult(
        status=status,
        reason=reason,
        warnings=warnings,
        statistic=statistic,
        window=window,
        input_series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        input_count=len(series.observations),
        established_count=established_count,
        not_established_count=not_established_count,
        points=tuple(points),
    )


def autocorrelation(
    series: TimeSeries, *, max_lag: int
) -> AutocorrelationResult:
    """Calculate mean-centred ACF values from lag zero through max_lag."""

    series = _validate_series(series)
    max_lag = _validate_max_lag(max_lag)
    return _autocorrelation_from_prepared(
        series, max_lag=max_lag, sample=_prepared_sample(series)
    )


def _not_established_pacf(
    acf: AutocorrelationResult,
    *,
    reason: str,
    innovation_variance: Decimal | None,
) -> PartialAutocorrelationResult:
    return PartialAutocorrelationResult(
        status="not_established",
        reason=reason,
        warnings=_warnings(reason, *acf.warnings),
        input_series_id=acf.input_series_id,
        input_lineage_digest=acf.input_lineage_digest,
        max_lag=acf.max_lag,
        sample_size=acf.sample_size,
        excluded_count=acf.excluded_count,
        trimmed_leading_count=acf.trimmed_leading_count,
        trimmed_trailing_count=acf.trimmed_trailing_count,
        interior_missing_count=acf.interior_missing_count,
        first_included_index=acf.first_included_index,
        last_included_index=acf.last_included_index,
        mean=acf.mean,
        centered_sum_squares=acf.centered_sum_squares,
        innovation_variance=innovation_variance,
        autocorrelations=acf.coefficients,
        coefficients=_missing_coefficients(acf.max_lag, reason),
    )


def partial_autocorrelation(
    series: TimeSeries, *, max_lag: int
) -> PartialAutocorrelationResult:
    """Calculate PACF by Durbin--Levinson recursion over this module's ACF."""

    acf = autocorrelation(series, max_lag=max_lag)
    if acf.status != "established":
        return _not_established_pacf(
            acf,
            reason=acf.reason or "autocorrelation_not_established",
            innovation_variance=None,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        raw = tuple(point.value for point in acf.coefficients)
        if any(value is None for value in raw):
            raise ValidationError("autocorrelation result is internally inconsistent")
        values = tuple(value for value in raw if value is not None)
        coefficients = [AutocorrelationPoint(0, Decimal("1"), None)]
        prior: tuple[Decimal, ...] = ()
        innovation_variance = Decimal("1")
        for lag in range(1, max_lag + 1):
            numerator = values[lag] - sum(
                (
                    prior[index - 1] * values[lag - index]
                    for index in range(1, lag)
                ),
                Decimal("0"),
            )
            if innovation_variance <= 0:
                return _not_established_pacf(
                    acf,
                    reason="nonpositive_innovation_variance",
                    innovation_variance=_clean_decimal(innovation_variance),
                )
            last_coefficient = _clean_decimal(numerator / innovation_variance)
            current = tuple(
                _clean_decimal(
                    prior[index] - last_coefficient * prior[lag - index - 2]
                )
                for index in range(lag - 1)
            ) + (last_coefficient,)
            coefficients.append(AutocorrelationPoint(lag, last_coefficient, None))
            innovation_variance = _clean_decimal(
                innovation_variance
                * (Decimal("1") - last_coefficient * last_coefficient)
            )
            if innovation_variance <= 0:
                return _not_established_pacf(
                    acf,
                    reason="nonpositive_innovation_variance",
                    innovation_variance=innovation_variance,
                )
            prior = current
        return PartialAutocorrelationResult(
            status="established",
            reason=None,
            warnings=acf.warnings,
            input_series_id=acf.input_series_id,
            input_lineage_digest=acf.input_lineage_digest,
            max_lag=acf.max_lag,
            sample_size=acf.sample_size,
            excluded_count=acf.excluded_count,
            trimmed_leading_count=acf.trimmed_leading_count,
            trimmed_trailing_count=acf.trimmed_trailing_count,
            interior_missing_count=0,
            first_included_index=acf.first_included_index,
            last_included_index=acf.last_included_index,
            mean=acf.mean,
            centered_sum_squares=acf.centered_sum_squares,
            innovation_variance=innovation_variance,
            autocorrelations=acf.coefficients,
            coefficients=tuple(coefficients),
        )



def _not_established_ljung_box(
    series: TimeSeries,
    *,
    lag: int,
    sample: _PreparedSample,
    reason: str,
    autocorrelations: tuple[AutocorrelationPoint, ...],
) -> LjungBoxResult:
    return LjungBoxResult(
        status="not_established",
        reason=reason,
        warnings=_warnings(reason, *_terminal_missing_warning(sample)),
        input_series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        lag=lag,
        model_degrees_of_freedom=0,
        degrees_of_freedom=None,
        sample_size=len(sample.values),
        excluded_count=sample.excluded_count,
        trimmed_leading_count=sample.trimmed_leading_count,
        trimmed_trailing_count=sample.trimmed_trailing_count,
        interior_missing_count=sample.interior_missing_count,
        first_included_index=sample.first_index,
        last_included_index=sample.last_index,
        statistic=None,
        p_value=None,
        reference_distribution=CHI_SQUARE_REFERENCE_DISTRIBUTION,
        inference_backend=CHI_SQUARE_INFERENCE_BACKEND,
        autocorrelations=autocorrelations,
    )


def ljung_box(series: TimeSeries, *, lag: int) -> LjungBoxResult:
    """Run fixed-lag Ljung--Box with model degrees of freedom fixed at zero."""

    series = _validate_series(series)
    lag = _validate_ljung_box_lag(lag)
    sample = _prepared_sample(series)
    if sample.interior_missing_count:
        return _not_established_ljung_box(
            series,
            lag=lag,
            sample=sample,
            reason="interior_missing_observations",
            autocorrelations=_missing_coefficients(
                lag, "interior_missing_observations"
            ),
        )
    if len(sample.values) < max(8, 2 * lag + 1):
        return _not_established_ljung_box(
            series,
            lag=lag,
            sample=sample,
            reason="insufficient_sample_for_fixed_ljung_box_lag",
            autocorrelations=_missing_coefficients(
                lag, "insufficient_sample_for_fixed_ljung_box_lag"
            ),
        )
    acf = _autocorrelation_from_prepared(series, max_lag=lag, sample=sample)
    if acf.status != "established":
        return _not_established_ljung_box(
            series,
            lag=lag,
            sample=sample,
            reason=acf.reason or "autocorrelation_not_established",
            autocorrelations=acf.coefficients,
        )
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        statistic = Decimal(len(sample.values) * (len(sample.values) + 2)) * sum(
            (
                (coefficient.value * coefficient.value)
                / Decimal(len(sample.values) - coefficient.lag)
                for coefficient in acf.coefficients[1:]
                if coefficient.value is not None
            ),
            Decimal("0"),
        )
        statistic = _clean_decimal(statistic)
    return LjungBoxResult(
        status="established",
        reason=None,
        warnings=_warnings("asymptotic_chi_square_reference", *acf.warnings),
        input_series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        lag=lag,
        model_degrees_of_freedom=0,
        degrees_of_freedom=lag,
        sample_size=len(sample.values),
        excluded_count=sample.excluded_count,
        trimmed_leading_count=sample.trimmed_leading_count,
        trimmed_trailing_count=sample.trimmed_trailing_count,
        interior_missing_count=0,
        first_included_index=sample.first_index,
        last_included_index=sample.last_index,
        statistic=statistic,
        p_value=_chi_square_survival(statistic, lag),
        reference_distribution=CHI_SQUARE_REFERENCE_DISTRIBUTION,
        inference_backend=CHI_SQUARE_INFERENCE_BACKEND,
        autocorrelations=acf.coefficients,
    )


def _not_established_drawdown(
    series: TimeSeries,
    *,
    method: str,
    sample: _PreparedSample,
    reason: str,
) -> DrawdownResult:
    return DrawdownResult(
        status="not_established",
        reason=reason,
        warnings=_warnings(reason, *_terminal_missing_warning(sample)),
        input_series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        method=method,
        initial_wealth=Decimal("1"),
        sample_size=len(sample.values),
        excluded_count=sample.excluded_count,
        trimmed_leading_count=sample.trimmed_leading_count,
        trimmed_trailing_count=sample.trimmed_trailing_count,
        interior_missing_count=sample.interior_missing_count,
        first_included_index=sample.first_index,
        last_included_index=sample.last_index,
        terminal_wealth=None,
        terminal_high_water_mark=None,
        terminal_high_water_mark_index=None,
        points=(),
        episodes=(),
    )


def drawdown_episodes(series: TimeSeries, *, method: str) -> DrawdownResult:
    """Compound from wealth one and identify drawdown episodes.

    High-water-mark ties retain the earliest peak index. An active episode
    recovers at the first later observation whose wealth is at or above its
    original peak; an unrecovered episode remains in the output with a null
    recovery index.
    """

    series = _validate_series(series)
    if method not in {"simple", "log"}:
        raise ValidationError("drawdown method must be simple or log")
    if method == "simple" and any(
        observation.value is not None and observation.value <= Decimal("-1")
        for observation in series.observations
    ):
        raise ValidationError(
            "simple returns at or below negative one are invalid"
        )
    sample = _prepared_sample(series)
    if sample.interior_missing_count:
        return _not_established_drawdown(
            series,
            method=method,
            sample=sample,
            reason="interior_missing_observations",
        )
    if not sample.values:
        return _not_established_drawdown(
            series,
            method=method,
            sample=sample,
            reason="insufficient_nonmissing_returns",
        )

    assert sample.first_index is not None
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        wealth = Decimal("1")
        high_water_mark = Decimal("1")
        high_water_mark_index: int | None = None
        points: list[DrawdownPoint] = []
        episodes: list[DrawdownEpisode] = []
        active_peak_index: int | None = None
        active_peak_wealth: Decimal | None = None
        active_start_index: int | None = None
        active_trough_index: int | None = None
        active_trough_wealth: Decimal | None = None

        for offset, value in enumerate(sample.values):
            index = sample.first_index + offset
            anchor = series.observations[index]
            if method == "simple":
                wealth = _clean_decimal(wealth * (Decimal("1") + value))
            else:
                wealth = _clean_decimal(wealth * value.exp())

            if active_peak_wealth is not None:
                assert active_start_index is not None
                assert active_trough_index is not None
                assert active_trough_wealth is not None
                if wealth < active_trough_wealth:
                    active_trough_index = index
                    active_trough_wealth = wealth
                if wealth >= active_peak_wealth:
                    episodes.append(
                        DrawdownEpisode(
                            peak_index=active_peak_index,
                            peak_wealth=active_peak_wealth,
                            start_index=active_start_index,
                            trough_index=active_trough_index,
                            trough_wealth=active_trough_wealth,
                            maximum_drawdown=_clean_decimal(
                                active_trough_wealth / active_peak_wealth
                                - Decimal("1")
                            ),
                            recovery_index=index,
                            recovery_wealth=wealth,
                            recovered=True,
                        )
                    )
                    active_peak_index = None
                    active_peak_wealth = None
                    active_start_index = None
                    active_trough_index = None
                    active_trough_wealth = None

            if active_peak_wealth is None:
                if wealth < high_water_mark:
                    active_peak_index = high_water_mark_index
                    active_peak_wealth = high_water_mark
                    active_start_index = index
                    active_trough_index = index
                    active_trough_wealth = wealth
                elif wealth > high_water_mark:
                    high_water_mark = wealth
                    high_water_mark_index = index

            points.append(
                DrawdownPoint(
                    index=index,
                    period_start=anchor.period_start,
                    period_end=anchor.period_end,
                    wealth=wealth,
                    high_water_mark=high_water_mark,
                    high_water_mark_index=high_water_mark_index,
                    drawdown=_clean_decimal(
                        wealth / high_water_mark - Decimal("1")
                    ),
                )
            )

        if active_peak_wealth is not None:
            assert active_start_index is not None
            assert active_trough_index is not None
            assert active_trough_wealth is not None
            episodes.append(
                DrawdownEpisode(
                    peak_index=active_peak_index,
                    peak_wealth=active_peak_wealth,
                    start_index=active_start_index,
                    trough_index=active_trough_index,
                    trough_wealth=active_trough_wealth,
                    maximum_drawdown=_clean_decimal(
                        active_trough_wealth / active_peak_wealth - Decimal("1")
                    ),
                    recovery_index=None,
                    recovery_wealth=None,
                    recovered=False,
                )
            )

        return DrawdownResult(
            status="established",
            reason=None,
            warnings=_terminal_missing_warning(sample),
            input_series_id=series.series_id,
            input_lineage_digest=series.lineage_digest,
            method=method,
            initial_wealth=Decimal("1"),
            sample_size=len(sample.values),
            excluded_count=sample.excluded_count,
            trimmed_leading_count=sample.trimmed_leading_count,
            trimmed_trailing_count=sample.trimmed_trailing_count,
            interior_missing_count=0,
            first_included_index=sample.first_index,
            last_included_index=sample.last_index,
            terminal_wealth=wealth,
            terminal_high_water_mark=high_water_mark,
            terminal_high_water_mark_index=high_water_mark_index,
            points=tuple(points),
            episodes=tuple(episodes),
        )


__all__ = [
    "AutocorrelationPoint",
    "AutocorrelationResult",
    "CHI_SQUARE_INFERENCE_BACKEND",
    "CHI_SQUARE_REFERENCE_DISTRIBUTION",
    "DECIMAL_PRECISION",
    "DrawdownEpisode",
    "DrawdownPoint",
    "DrawdownResult",
    "LjungBoxResult",
    "MAX_AUTOCORRELATION_LAG",
    "MAX_ROLLING_WINDOW",
    "MAX_TRANSFORM_OBSERVATIONS",
    "PartialAutocorrelationResult",
    "RollingStatisticPoint",
    "RollingStatisticResult",
    "autocorrelation",
    "drawdown_episodes",
    "ljung_box",
    "partial_autocorrelation",
    "rolling_statistic",
]
