"""Pure deterministic Decimal kernels for common technical indicators.

This module deliberately has no dependency on stores, TimeSeries objects, or
public-tool adapters. It receives scalar OHLCV sequences, preserves every
input bar in every component, and makes warm-up and missing-input states
explicit at the point level.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Sequence

from quant_data.errors import ResourceLimitError, ValidationError


DECIMAL_PRECISION = 34
MAX_TECHNICAL_INDICATOR_OBSERVATIONS = 10_000
MAX_SUPERTREND_AI_FACTORS = 101
SUPERTREND_AI_KMEANS_ITERATION_CAP = 1_001

TECHNICAL_INDICATORS_V2 = (
    "sma",
    "ema",
    "rolling_standard_deviation",
    "rolling_z_score",
    "true_range",
    "average_true_range",
    "rate_of_change",
    "relative_strength_index",
    "macd",
    "bollinger_bands",
    "donchian_channels",
    "stochastic_oscillator",
    "average_directional_index",
    "on_balance_volume",
    "accumulation_distribution",
)
TECHNICAL_INDICATORS_V21 = (*TECHNICAL_INDICATORS_V2, "supertrend_ai")
TECHNICAL_INDICATORS_V22 = (
    *TECHNICAL_INDICATORS_V21,
    "swing_structure_forecast",
)
TECHNICAL_INDICATORS_V23 = (*TECHNICAL_INDICATORS_V22, "kdj")
TECHNICAL_INDICATORS_V24 = (*TECHNICAL_INDICATORS_V23, "williams_vix_fix")
TECHNICAL_INDICATORS_V25 = (*TECHNICAL_INDICATORS_V24, "wavetrend_crosses")
TECHNICAL_INDICATORS_V26 = (*TECHNICAL_INDICATORS_V25, "parabolic_sar")
TECHNICAL_INDICATORS_V27 = (
    *TECHNICAL_INDICATORS_V26,
    "rolling_regression_line",
)
TECHNICAL_INDICATORS = TECHNICAL_INDICATORS_V27


@dataclass(frozen=True, slots=True)
class TechnicalIndicatorPoint:
    """One output cell aligned to one input bar."""

    value: Decimal | None
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class TechnicalIndicatorComponent:
    """One named output series of a multi-component indicator."""

    name: str
    points: tuple[TechnicalIndicatorPoint, ...]


@dataclass(frozen=True, slots=True)
class TechnicalIndicatorResult:
    """The fully deterministic result of one indicator specification."""

    indicator: str
    parameters: tuple[tuple[str, int | Decimal | str], ...]
    warnings: tuple[str, ...]
    components: tuple[TechnicalIndicatorComponent, ...]


_WINDOW_PARAMETERS = frozenset(
    {
        "sma",
        "ema",
        "rolling_regression_line",
        "rolling_standard_deviation",
        "rolling_z_score",
        "average_true_range",
        "rate_of_change",
        "relative_strength_index",
        "donchian_channels",
        "average_directional_index",
    }
)
_WINDOW_AND_MULTIPLIER = "bollinger_bands"
_WINDOW_AND_SIGNAL = "stochastic_oscillator"
_MACD = "macd"
_SUPERTREND_AI = "supertrend_ai"
_SWING_STRUCTURE_FORECAST = "swing_structure_forecast"
_KDJ = "kdj"
_WILLIAMS_VIX_FIX = "williams_vix_fix"
_WAVETREND_CROSSES = "wavetrend_crosses"
_PARABOLIC_SAR = "parabolic_sar"
_ROLLING_REGRESSION_LINE = "rolling_regression_line"
_NO_PARAMETERS = frozenset(
    {
        "true_range",
        "on_balance_volume",
        "accumulation_distribution",
    }
)
_HIGH_LOW_INDICATORS = frozenset(
    {
        "true_range",
        "average_true_range",
        "donchian_channels",
        "stochastic_oscillator",
        "average_directional_index",
        _KDJ,
        _WAVETREND_CROSSES,
        _PARABOLIC_SAR,
        "accumulation_distribution",
        _SUPERTREND_AI,
        _SWING_STRUCTURE_FORECAST,
    }
)
_VOLUME_INDICATORS = frozenset(
    {"on_balance_volume", "accumulation_distribution"}
)
_LOW_ONLY_INDICATORS = frozenset({_WILLIAMS_VIX_FIX})


def _clean_decimal(value: Decimal) -> Decimal:
    """Use a canonical positive zero and the active deterministic context."""

    return Decimal("0") if value.is_zero() else +value


def _value(value: Decimal) -> TechnicalIndicatorPoint:
    return TechnicalIndicatorPoint(_clean_decimal(value), None)


def _missing(reason: str) -> TechnicalIndicatorPoint:
    return TechnicalIndicatorPoint(None, reason)


def _mean(values: tuple[Decimal, ...] | list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("mean requires at least one value")
    return _clean_decimal(sum(values, Decimal("0")) / Decimal(len(values)))


def _require_decimal_or_missing(value: object, field: str, index: int) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field}/{index} must be a finite Decimal or null")
    return value


def _checked_close(value: object) -> tuple[Decimal | None, ...]:
    if not isinstance(value, tuple):
        raise ValidationError("close must be a tuple of Decimal values or nulls")
    if not value:
        raise ValidationError("close must contain at least one bar")
    if len(value) > MAX_TECHNICAL_INDICATOR_OBSERVATIONS:
        raise ResourceLimitError("technical indicator input exceeds the observation limit")
    return tuple(
        _require_decimal_or_missing(item, "close", index)
        for index, item in enumerate(value)
    )


def _checked_optional_series(
    value: object,
    *,
    field: str,
    length: int,
    volume: bool = False,
) -> tuple[Decimal | None, ...]:
    if not isinstance(value, tuple):
        raise ValidationError(f"{field} must be a tuple of Decimal values or nulls")
    if not value:
        return ()
    if len(value) != length:
        raise ValidationError(f"{field} must have the same bar count as close")
    checked = tuple(
        _require_decimal_or_missing(item, field, index)
        for index, item in enumerate(value)
    )
    if volume:
        for index, item in enumerate(checked):
            if item is not None and item < 0:
                raise ValidationError(f"volume/{index} must be nonnegative")
    return checked


def _require_field(
    value: tuple[Decimal | None, ...], *, field: str, indicator: str
) -> tuple[Decimal | None, ...]:
    if not value:
        raise ValidationError(f"{indicator} requires a nonempty {field} series")
    return value


def _validate_ohlc(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
) -> None:
    if not high and not low:
        return
    for index, close_value in enumerate(close):
        high_value = high[index] if high else None
        low_value = low[index] if low else None
        if high_value is not None and low_value is not None and high_value < low_value:
            raise ValidationError(f"high/{index} must be greater than or equal to low/{index}")
        if close_value is None:
            continue
        if high_value is not None and high_value < close_value:
            raise ValidationError(
                f"high/{index} must be greater than or equal to close/{index}"
            )
        if low_value is not None and low_value > close_value:
            raise ValidationError(
                f"low/{index} must be less than or equal to close/{index}"
            )


def _checked_inputs(
    indicator: str,
    *,
    close: object,
    high: object,
    low: object,
    volume: object,
) -> tuple[
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
]:
    checked_close = _checked_close(close)
    checked_high = _checked_optional_series(
        high, field="high", length=len(checked_close)
    )
    checked_low = _checked_optional_series(
        low, field="low", length=len(checked_close)
    )
    checked_volume = _checked_optional_series(
        volume, field="volume", length=len(checked_close), volume=True
    )
    if indicator in _HIGH_LOW_INDICATORS:
        _require_field(checked_high, field="high", indicator=indicator)
        _require_field(checked_low, field="low", indicator=indicator)
    if indicator in _LOW_ONLY_INDICATORS:
        _require_field(checked_low, field="low", indicator=indicator)
    if indicator in _VOLUME_INDICATORS:
        _require_field(checked_volume, field="volume", indicator=indicator)
    _validate_ohlc(checked_close, checked_high, checked_low)
    return checked_close, checked_high, checked_low, checked_volume


def _checked_window(value: object, *, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= MAX_TECHNICAL_INDICATOR_OBSERVATIONS:
        raise ValidationError(f"{field} is outside the supported bound")
    return value


def _checked_multiplier(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("standard_deviation_multiplier must be a finite Decimal")
    if value <= 0 or value > Decimal("10"):
        raise ValidationError("standard_deviation_multiplier must be greater than zero and at most ten")
    return value



def _checked_finite_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    return value

def _checked_supertrend_decimal(
    value: object,
    *,
    field: str,
    minimum: Decimal,
    maximum: Decimal,
    minimum_inclusive: bool = True,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    below_minimum = value < minimum if minimum_inclusive else value <= minimum
    if below_minimum or value > maximum:
        comparator = "at least" if minimum_inclusive else "greater than"
        raise ValidationError(
            f"{field} must be {comparator} {minimum} and at most {maximum}"
        )
    return value


def _supertrend_factor_count(
    minimum_factor: Decimal,
    maximum_factor: Decimal,
    factor_step: Decimal,
) -> int:
    if minimum_factor > maximum_factor:
        raise ValidationError(
            "minimum_factor must be less than or equal to maximum_factor"
        )
    count = int((maximum_factor - minimum_factor) // factor_step) + 1
    if count < 3:
        raise ValidationError(
            "supertrend_ai requires at least three factor candidates"
        )
    if count > MAX_SUPERTREND_AI_FACTORS:
        raise ResourceLimitError(
            "supertrend_ai factor grid exceeds the supported limit"
        )
    return count


def _checked_parameters(
    indicator: str,
    *,
    window: object,
    fast_window: object,
    slow_window: object,
    signal_window: object,
    standard_deviation_multiplier: object,
    minimum_factor: object,
    maximum_factor: object,
    factor_step: object,
    performance_memory: object,
    cluster: object,
    sample_count: object,
    aggregation_method: object,
    percentile_window: object,
    percentile_high_factor: object,
    percentile_low_factor: object,
    start: object,
    increment: object,
    maximum: object,
) -> tuple[tuple[str, int | Decimal | str], ...]:
    values = {
        "window": window,
        "fast_window": fast_window,
        "slow_window": slow_window,
        "signal_window": signal_window,
        "standard_deviation_multiplier": standard_deviation_multiplier,
        "minimum_factor": minimum_factor,
        "maximum_factor": maximum_factor,
        "factor_step": factor_step,
        "performance_memory": performance_memory,
        "cluster": cluster,
        "sample_count": sample_count,
        "aggregation_method": aggregation_method,
        "percentile_window": percentile_window,
        "percentile_high_factor": percentile_high_factor,
        "percentile_low_factor": percentile_low_factor,
        "start": start,
        "increment": increment,
        "maximum": maximum,
    }
    required: tuple[str, ...]
    if indicator in _WINDOW_PARAMETERS:
        required = ("window",)
    elif indicator == _WINDOW_AND_MULTIPLIER:
        required = ("window", "standard_deviation_multiplier")
    elif indicator in {_WINDOW_AND_SIGNAL, _KDJ, _WAVETREND_CROSSES}:
        required = ("window", "signal_window")
    elif indicator == _MACD:
        required = ("fast_window", "slow_window", "signal_window")
    elif indicator == _SUPERTREND_AI:
        required = (
            "window",
            "minimum_factor",
            "maximum_factor",
            "factor_step",
            "performance_memory",
            "cluster",
        )
    elif indicator == _SWING_STRUCTURE_FORECAST:
        required = ("window", "sample_count", "aggregation_method")
    elif indicator == _WILLIAMS_VIX_FIX:
        required = (
            "window",
            "signal_window",
            "standard_deviation_multiplier",
            "percentile_window",
            "percentile_high_factor",
            "percentile_low_factor",
        )
    elif indicator == _PARABOLIC_SAR:
        required = ("start", "increment", "maximum")
    elif indicator in _NO_PARAMETERS:
        required = ()
    else:
        raise ValidationError("technical indicator is not supported")
    for name, value in values.items():
        if name not in required and value is not None:
            raise ValidationError(f"{name} is not applicable to {indicator}")
        if name in required and value is None:
            raise ValidationError(f"{indicator} requires {name}")

    checked: dict[str, int | Decimal | str] = {}
    if "window" in required:
        minimum = (
            10
            if indicator == _SWING_STRUCTURE_FORECAST
            else 2
            if indicator
            in {
                "rolling_standard_deviation",
                "rolling_z_score",
                "bollinger_bands",
                "rolling_regression_line",
            }
            else 1
        )
        checked["window"] = _checked_window(
            values["window"], field="window", minimum=minimum
        )
        if (
            indicator == _SWING_STRUCTURE_FORECAST
            and checked["window"] > 5_000
        ):
            raise ValidationError(
                "swing_structure_forecast window exceeds the Pine historical bound"
            )
    if "fast_window" in required:
        checked["fast_window"] = _checked_window(
            values["fast_window"], field="fast_window"
        )
        checked["slow_window"] = _checked_window(
            values["slow_window"], field="slow_window"
        )
        checked["signal_window"] = _checked_window(
            values["signal_window"], field="signal_window"
        )
        if checked["fast_window"] >= checked["slow_window"]:
            raise ValidationError("fast_window must be smaller than slow_window")
    elif "signal_window" in required:
        checked["signal_window"] = _checked_window(
            values["signal_window"], field="signal_window"
        )
    if "standard_deviation_multiplier" in required:
        checked["standard_deviation_multiplier"] = _checked_multiplier(
            values["standard_deviation_multiplier"]
        )
        if indicator == _WILLIAMS_VIX_FIX and not (
            Decimal("1")
            <= checked["standard_deviation_multiplier"]
            <= Decimal("5")
        ):
            raise ValidationError(
                "williams_vix_fix standard_deviation_multiplier must be between one and five"
            )
    if indicator == _SUPERTREND_AI:
        minimum_factor = _checked_supertrend_decimal(
            values["minimum_factor"],
            field="minimum_factor",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        )
        maximum_factor = _checked_supertrend_decimal(
            values["maximum_factor"],
            field="maximum_factor",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        )
        factor_step = _checked_supertrend_decimal(
            values["factor_step"],
            field="factor_step",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
            minimum_inclusive=False,
        )
        performance_memory = _checked_supertrend_decimal(
            values["performance_memory"],
            field="performance_memory",
            minimum=Decimal("2"),
            maximum=Decimal("10000"),
        )
        if not isinstance(values["cluster"], str) or values["cluster"] not in {
            "best",
            "average",
            "worst",
        }:
            raise ValidationError(
                "cluster must be one of best, average, or worst"
            )
        _supertrend_factor_count(
            minimum_factor, maximum_factor, factor_step
        )
        checked.update(
            {
                "minimum_factor": minimum_factor,
                "maximum_factor": maximum_factor,
                "factor_step": factor_step,
                "performance_memory": performance_memory,
                "cluster": values["cluster"],
            }
        )
    if indicator == _SWING_STRUCTURE_FORECAST:
        checked["sample_count"] = _checked_window(
            values["sample_count"], field="sample_count", minimum=3
        )
        if checked["sample_count"] > 20:
            raise ValidationError("sample_count must be at most twenty")
        if (
            not isinstance(values["aggregation_method"], str)
            or values["aggregation_method"]
            not in {"weighted", "average", "median"}
        ):
            raise ValidationError(
                "aggregation_method must be one of weighted, average, or median"
            )
        checked["aggregation_method"] = values["aggregation_method"]
    if indicator == _WILLIAMS_VIX_FIX:
        checked["percentile_window"] = _checked_window(
            values["percentile_window"], field="percentile_window"
        )
        checked["percentile_high_factor"] = _checked_supertrend_decimal(
            values["percentile_high_factor"],
            field="percentile_high_factor",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
            minimum_inclusive=False,
        )
        checked["percentile_low_factor"] = _checked_supertrend_decimal(
            values["percentile_low_factor"],
            field="percentile_low_factor",
            minimum=Decimal("1"),
            maximum=Decimal("10"),
        )
    if indicator == _PARABOLIC_SAR:
        for field in ("start", "increment", "maximum"):
            checked[field] = _checked_finite_decimal(
                values[field],
                field=field,
            )
    return tuple((name, checked[name]) for name in sorted(checked))


def _rolling_mean_and_standard_deviation(
    values: tuple[Decimal | None, ...], *, window: int, sample: bool = True
) -> tuple[tuple[TechnicalIndicatorPoint, ...], tuple[TechnicalIndicatorPoint, ...]]:
    """Return full-window means and deviations without skipping gaps."""

    mean_points: list[TechnicalIndicatorPoint] = []
    standard_deviation_points: list[TechnicalIndicatorPoint] = []
    entries: deque[Decimal | None] = deque()
    missing_count = 0
    rolling_sum = Decimal("0")
    rolling_square_sum = Decimal("0")
    for value in values:
        entries.append(value)
        if value is None:
            missing_count += 1
        else:
            rolling_sum += value
            rolling_square_sum += value * value
        if len(entries) > window:
            departed = entries.popleft()
            if departed is None:
                missing_count -= 1
            else:
                rolling_sum -= departed
                rolling_square_sum -= departed * departed
        if len(entries) < window:
            mean_points.append(_missing("insufficient_history"))
            standard_deviation_points.append(_missing("insufficient_history"))
            continue
        if missing_count:
            mean_points.append(_missing("rolling_window_input_missing"))
            standard_deviation_points.append(
                _missing("rolling_window_input_missing")
            )
            continue
        mean = _clean_decimal(rolling_sum / Decimal(window))
        if window == 1 and sample:
            mean_points.append(_value(mean))
            standard_deviation_points.append(
                _missing("sample_standard_deviation_requires_window_at_least_two")
            )
            continue
        denominator = Decimal(window - 1 if sample else window)
        numerator = rolling_square_sum - rolling_sum * mean
        variance = numerator / denominator
        if variance < 0:
            # Decimal cancellation can make a mathematically nonnegative value
            # microscopically negative. Recompute the affected finite window
            # from centered differences instead of emitting an invalid square root.
            finite_entries = tuple(item for item in entries if item is not None)
            variance = sum(
                ((item - mean) * (item - mean) for item in finite_entries),
                Decimal("0"),
            ) / denominator
        standard_deviation = _clean_decimal(variance.sqrt())
        mean_points.append(_value(mean))
        standard_deviation_points.append(_value(standard_deviation))
    return tuple(mean_points), tuple(standard_deviation_points)


def _ema_points(
    values: tuple[Decimal | None, ...], *, window: int
) -> tuple[TechnicalIndicatorPoint, ...]:
    alpha = Decimal("2") / Decimal(window + 1)
    seed: list[Decimal] = []
    ema: Decimal | None = None
    result: list[TechnicalIndicatorPoint] = []
    for value in values:
        if value is None:
            seed.clear()
            ema = None
            result.append(_missing("indicator_input_missing"))
            continue
        if ema is None:
            seed.append(value)
            if len(seed) < window:
                result.append(_missing("insufficient_history"))
                continue
            ema = _mean(seed)
            result.append(_value(ema))
            continue
        ema = _clean_decimal(ema + alpha * (value - ema))
        result.append(_value(ema))
    return tuple(result)


def _rolling_regression_line_points(
    values: tuple[Decimal | None, ...], *, window: int
) -> tuple[TechnicalIndicatorPoint, ...]:
    """Fit each complete close window and emit its fitted rightmost value."""

    count = Decimal(window)
    x_sum = Decimal(window * (window - 1) // 2)
    x_square_sum = Decimal(window * (window - 1) * (2 * window - 1) // 6)
    denominator = count * x_square_sum - x_sum * x_sum
    entries: deque[Decimal | None] = deque()
    missing_count = 0
    rolling_sum = Decimal("0")
    rolling_x_sum = Decimal("0")
    result: list[TechnicalIndicatorPoint] = []
    for value in values:
        entries.append(value)
        if value is None:
            missing_count += 1
        else:
            rolling_sum += value
            rolling_x_sum += Decimal(len(entries) - 1) * value
        if len(entries) > window:
            departed = entries.popleft()
            departed_value = departed if departed is not None else Decimal("0")
            rolling_x_sum -= rolling_sum - departed_value
            rolling_sum -= departed_value
            if departed is None:
                missing_count -= 1
        if len(entries) < window:
            result.append(_missing("insufficient_history"))
            continue
        if missing_count:
            result.append(_missing("rolling_window_input_missing"))
            continue
        slope = (count * rolling_x_sum - x_sum * rolling_sum) / denominator
        intercept = (rolling_sum - slope * x_sum) / count
        result.append(_value(intercept + slope * Decimal(window - 1)))
    return tuple(result)


def _true_range_points(
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    close: tuple[Decimal | None, ...],
) -> tuple[TechnicalIndicatorPoint, ...]:
    result: list[TechnicalIndicatorPoint] = []
    previous_close: Decimal | None = None
    for high_value, low_value, close_value in zip(high, low, close, strict=True):
        if high_value is None or low_value is None or close_value is None:
            previous_close = None
            result.append(_missing("indicator_input_missing"))
            continue
        if previous_close is None:
            current = high_value - low_value
        else:
            current = max(
                high_value - low_value,
                abs(high_value - previous_close),
                abs(low_value - previous_close),
            )
        previous_close = close_value
        result.append(_value(current))
    return tuple(result)


def _wilder_average(
    values: tuple[TechnicalIndicatorPoint, ...], *, window: int
) -> tuple[TechnicalIndicatorPoint, ...]:
    seed: list[Decimal] = []
    average: Decimal | None = None
    result: list[TechnicalIndicatorPoint] = []
    for point in values:
        if point.value is None:
            seed.clear()
            average = None
            result.append(_missing(point.missing_reason or "indicator_input_missing"))
            continue
        if average is None:
            seed.append(point.value)
            if len(seed) < window:
                result.append(_missing("insufficient_history"))
                continue
            average = _mean(seed)
            result.append(_value(average))
            continue
        average = _clean_decimal(
            (average * Decimal(window - 1) + point.value) / Decimal(window)
        )
        result.append(_value(average))
    return tuple(result)


def _rate_of_change_points(
    close: tuple[Decimal | None, ...], *, window: int
) -> tuple[TechnicalIndicatorPoint, ...]:
    result: list[TechnicalIndicatorPoint] = []
    for index, value in enumerate(close):
        if index < window:
            result.append(_missing("insufficient_history"))
            continue
        base = close[index - window]
        if value is None or base is None:
            result.append(_missing("lookback_input_missing"))
        elif base.is_zero():
            result.append(_missing("zero_lookback_close"))
        else:
            result.append(_value(Decimal("100") * (value / base - Decimal("1"))))
    return tuple(result)


def _relative_strength_index_points(
    close: tuple[Decimal | None, ...], *, window: int
) -> tuple[tuple[TechnicalIndicatorPoint, ...], tuple[str, ...]]:
    result: list[TechnicalIndicatorPoint] = []
    warnings: list[str] = []
    previous: Decimal | None = None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    average_gain: Decimal | None = None
    average_loss: Decimal | None = None
    for value in close:
        if value is None:
            previous = None
            gains.clear()
            losses.clear()
            average_gain = None
            average_loss = None
            result.append(_missing("indicator_input_missing"))
            continue
        if previous is None:
            previous = value
            result.append(_missing("insufficient_history"))
            continue
        change = value - previous
        previous = value
        gain = max(change, Decimal("0"))
        loss = max(-change, Decimal("0"))
        if average_gain is None or average_loss is None:
            gains.append(gain)
            losses.append(loss)
            if len(gains) < window:
                result.append(_missing("insufficient_history"))
                continue
            average_gain = _mean(gains)
            average_loss = _mean(losses)
        else:
            average_gain = _clean_decimal(
                (average_gain * Decimal(window - 1) + gain) / Decimal(window)
            )
            average_loss = _clean_decimal(
                (average_loss * Decimal(window - 1) + loss) / Decimal(window)
            )
        if average_loss.is_zero():
            if average_gain.is_zero():
                if "flat_rsi_window_defined_as_50" not in warnings:
                    warnings.append("flat_rsi_window_defined_as_50")
                result.append(_value(Decimal("50")))
            else:
                result.append(_value(Decimal("100")))
        elif average_gain.is_zero():
            result.append(_value(Decimal("0")))
        else:
            result.append(
                _value(
                    Decimal("100")
                    * average_gain
                    / (average_gain + average_loss)
                )
            )
    return tuple(result), tuple(warnings)


def _macd_components(
    close: tuple[Decimal | None, ...],
    *,
    fast_window: int,
    slow_window: int,
    signal_window: int,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    fast = _ema_points(close, window=fast_window)
    slow = _ema_points(close, window=slow_window)
    line: list[TechnicalIndicatorPoint] = []
    for close_value, fast_point, slow_point in zip(close, fast, slow, strict=True):
        if close_value is None:
            line.append(_missing("indicator_input_missing"))
        elif fast_point.value is None or slow_point.value is None:
            line.append(_missing("insufficient_history"))
        else:
            line.append(_value(fast_point.value - slow_point.value))

    signal: list[TechnicalIndicatorPoint] = []
    histogram: list[TechnicalIndicatorPoint] = []
    seed: list[Decimal] = []
    signal_value: Decimal | None = None
    alpha = Decimal("2") / Decimal(signal_window + 1)
    for close_value, line_point in zip(close, line, strict=True):
        if close_value is None:
            seed.clear()
            signal_value = None
            signal.append(_missing("indicator_input_missing"))
            histogram.append(_missing("indicator_input_missing"))
            continue
        if line_point.value is None:
            signal.append(_missing("insufficient_history"))
            histogram.append(_missing("insufficient_history"))
            continue
        if signal_value is None:
            seed.append(line_point.value)
            if len(seed) < signal_window:
                signal.append(_missing("insufficient_history"))
                histogram.append(_missing("insufficient_history"))
                continue
            signal_value = _mean(seed)
        else:
            signal_value = _clean_decimal(
                signal_value + alpha * (line_point.value - signal_value)
            )
        signal.append(_value(signal_value))
        histogram.append(_value(line_point.value - signal_value))
    return tuple(line), tuple(signal), tuple(histogram)


def _bollinger_components(
    close: tuple[Decimal | None, ...], *, window: int, multiplier: Decimal
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    means, standard_deviations = _rolling_mean_and_standard_deviation(
        close, window=window
    )
    upper: list[TechnicalIndicatorPoint] = []
    lower: list[TechnicalIndicatorPoint] = []
    for mean, standard_deviation in zip(means, standard_deviations, strict=True):
        if mean.value is None or standard_deviation.value is None:
            reason = mean.missing_reason or standard_deviation.missing_reason
            upper.append(_missing(reason or "rolling_window_input_missing"))
            lower.append(_missing(reason or "rolling_window_input_missing"))
        else:
            upper.append(_value(mean.value + multiplier * standard_deviation.value))
            lower.append(_value(mean.value - multiplier * standard_deviation.value))
    return means, tuple(upper), tuple(lower)


def _rolling_high_low(
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
) -> tuple[tuple[Decimal | None, Decimal | None, str | None], ...]:
    """Return rolling highest highs and lowest lows in linear time."""

    high_queue: deque[tuple[int, Decimal]] = deque()
    low_queue: deque[tuple[int, Decimal]] = deque()
    missing_count = 0
    result: list[tuple[Decimal | None, Decimal | None, str | None]] = []
    for index, (high_value, low_value) in enumerate(zip(high, low, strict=True)):
        if high_value is None or low_value is None:
            missing_count += 1
        if high_value is not None:
            while high_queue and high_queue[-1][1] <= high_value:
                high_queue.pop()
            high_queue.append((index, high_value))
        if low_value is not None:
            while low_queue and low_queue[-1][1] >= low_value:
                low_queue.pop()
            low_queue.append((index, low_value))
        expired = index - window
        if expired >= 0:
            old_high = high[expired]
            old_low = low[expired]
            if old_high is None or old_low is None:
                missing_count -= 1
            if high_queue and high_queue[0][0] == expired:
                high_queue.popleft()
            if low_queue and low_queue[0][0] == expired:
                low_queue.popleft()
        if index < window - 1:
            result.append((None, None, "insufficient_history"))
        elif missing_count:
            result.append((None, None, "rolling_window_input_missing"))
        else:
            result.append((high_queue[0][1], low_queue[0][1], None))
    return tuple(result)


def _donchian_components(
    high: tuple[Decimal | None, ...], low: tuple[Decimal | None, ...], *, window: int
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    upper: list[TechnicalIndicatorPoint] = []
    middle: list[TechnicalIndicatorPoint] = []
    lower: list[TechnicalIndicatorPoint] = []
    for highest, lowest, reason in _rolling_high_low(high, low, window=window):
        if reason is not None:
            upper.append(_missing(reason))
            middle.append(_missing(reason))
            lower.append(_missing(reason))
        else:
            assert highest is not None and lowest is not None
            upper.append(_value(highest))
            middle.append(_value((highest + lowest) / Decimal("2")))
            lower.append(_value(lowest))
    return tuple(upper), tuple(middle), tuple(lower)


def _stochastic_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    signal_window: int,
) -> tuple[tuple[TechnicalIndicatorPoint, ...], tuple[TechnicalIndicatorPoint, ...]]:
    percent_k: list[TechnicalIndicatorPoint] = []
    for close_value, (highest, lowest, reason) in zip(
        close, _rolling_high_low(high, low, window=window), strict=True
    ):
        if reason is not None:
            percent_k.append(_missing(reason))
        elif close_value is None:
            percent_k.append(_missing("rolling_window_input_missing"))
        else:
            assert highest is not None and lowest is not None
            price_range = highest - lowest
            if price_range.is_zero():
                percent_k.append(_missing("zero_price_range"))
            else:
                percent_k.append(
                    _value(
                        Decimal("100") * (close_value - lowest) / price_range
                    )
                )
    percent_d, _ = _rolling_mean_and_standard_deviation(
        tuple(point.value for point in percent_k), window=signal_window
    )
    return tuple(percent_k), percent_d


def _bcwsma_points(
    points: tuple[TechnicalIndicatorPoint, ...], *, window: int
) -> tuple[TechnicalIndicatorPoint, ...]:
    """Apply Pine's recursive BCWSMA with nz(previous) initialization."""

    previous = Decimal("0")
    result: list[TechnicalIndicatorPoint] = []
    denominator = Decimal(window)
    carry_weight = Decimal(window - 1)
    for point in points:
        if point.value is None:
            previous = Decimal("0")
            result.append(
                _missing(point.missing_reason or "indicator_input_missing")
            )
            continue
        previous = _clean_decimal(
            (point.value + carry_weight * previous) / denominator
        )
        result.append(_value(previous))
    return tuple(result)


def _kdj_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    signal_window: int,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    rsv, _ = _stochastic_components(
        close,
        high,
        low,
        window=window,
        signal_window=signal_window,
    )
    percent_k = _bcwsma_points(rsv, window=signal_window)
    percent_d = _bcwsma_points(percent_k, window=signal_window)
    percent_j = tuple(
        _missing(
            percent_k_point.missing_reason
            or percent_d_point.missing_reason
            or "indicator_input_missing"
        )
        if percent_k_point.value is None or percent_d_point.value is None
        else _value(
            Decimal("3") * percent_k_point.value
            - Decimal("2") * percent_d_point.value
        )
        for percent_k_point, percent_d_point in zip(
            percent_k, percent_d, strict=True
        )
    )
    return percent_k, percent_d, percent_j


def _williams_vix_fix_components(
    close: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    signal_window: int,
    multiplier: Decimal,
    percentile_window: int,
    percentile_high_factor: Decimal,
    percentile_low_factor: Decimal,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    williams_vix_fix: list[TechnicalIndicatorPoint] = []
    for low_value, (highest_close, _, reason) in zip(
        low,
        _rolling_high_low(close, close, window=window),
        strict=True,
    ):
        if reason is not None:
            williams_vix_fix.append(_missing(reason))
        elif low_value is None:
            williams_vix_fix.append(_missing("indicator_input_missing"))
        else:
            assert highest_close is not None
            if highest_close.is_zero():
                williams_vix_fix.append(_missing("zero_highest_close"))
            else:
                williams_vix_fix.append(
                    _value(
                        Decimal("100")
                        * (highest_close - low_value)
                        / highest_close
                    )
                )

    values = tuple(point.value for point in williams_vix_fix)
    middle_band, standard_deviation = _rolling_mean_and_standard_deviation(
        values,
        window=signal_window,
        sample=False,
    )
    upper_band = tuple(
        _missing(
            middle_point.missing_reason
            or deviation_point.missing_reason
            or "rolling_window_input_missing"
        )
        if middle_point.value is None or deviation_point.value is None
        else _value(
            middle_point.value + multiplier * deviation_point.value
        )
        for middle_point, deviation_point in zip(
            middle_band, standard_deviation, strict=True
        )
    )

    range_high: list[TechnicalIndicatorPoint] = []
    range_low: list[TechnicalIndicatorPoint] = []
    for highest, lowest, reason in _rolling_high_low(
        values, values, window=percentile_window
    ):
        if reason is not None:
            range_high.append(_missing(reason))
            range_low.append(_missing(reason))
        else:
            assert highest is not None and lowest is not None
            range_high.append(_value(highest * percentile_high_factor))
            range_low.append(_value(lowest * percentile_low_factor))
    return (
        tuple(williams_vix_fix),
        upper_band,
        tuple(range_high),
        tuple(range_low),
    )


def _wavetrend_crosses_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    signal_window: int,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    average_price = tuple(
        _missing("indicator_input_missing")
        if close_value is None or high_value is None or low_value is None
        else _value((high_value + low_value + close_value) / Decimal("3"))
        for close_value, high_value, low_value in zip(
            close, high, low, strict=True
        )
    )
    esa = _ema_points(
        tuple(point.value for point in average_price), window=window
    )
    absolute_deviation = tuple(
        _missing(
            price_point.missing_reason
            or esa_point.missing_reason
            or "indicator_input_missing"
        )
        if price_point.value is None or esa_point.value is None
        else _value(abs(price_point.value - esa_point.value))
        for price_point, esa_point in zip(average_price, esa, strict=True)
    )
    raw_smoothed_deviation = _ema_points(
        tuple(point.value for point in absolute_deviation), window=window
    )
    smoothed_deviation = tuple(
        _missing(source_point.missing_reason or "indicator_input_missing")
        if result_point.value is None and source_point.value is None
        else result_point
        for result_point, source_point in zip(
            raw_smoothed_deviation, absolute_deviation, strict=True
        )
    )
    channel_index: list[TechnicalIndicatorPoint] = []
    for price_point, esa_point, deviation_point in zip(
        average_price, esa, smoothed_deviation, strict=True
    ):
        if (
            price_point.value is None
            or esa_point.value is None
            or deviation_point.value is None
        ):
            channel_index.append(
                _missing(
                    price_point.missing_reason
                    or esa_point.missing_reason
                    or deviation_point.missing_reason
                    or "indicator_input_missing"
                )
            )
        elif deviation_point.value.is_zero():
            channel_index.append(_missing("zero_channel_deviation"))
        else:
            channel_index.append(
                _value(
                    (price_point.value - esa_point.value)
                    / (Decimal("0.015") * deviation_point.value)
                )
            )
    raw_wavetrend = _ema_points(
        tuple(point.value for point in channel_index),
        window=signal_window,
    )
    wavetrend = tuple(
        _missing(source_point.missing_reason or "indicator_input_missing")
        if result_point.value is None and source_point.value is None
        else result_point
        for result_point, source_point in zip(
            raw_wavetrend, channel_index, strict=True
        )
    )
    wavetrend_signal, _ = _rolling_mean_and_standard_deviation(
        tuple(point.value for point in wavetrend), window=4
    )
    difference: list[TechnicalIndicatorPoint] = []
    cross_signal: list[TechnicalIndicatorPoint] = []
    previous: tuple[Decimal, Decimal] | None = None
    for wave_point, signal_point in zip(
        wavetrend, wavetrend_signal, strict=True
    ):
        if wave_point.value is None or signal_point.value is None:
            reason = (
                wave_point.missing_reason
                or signal_point.missing_reason
                or "indicator_input_missing"
            )
            difference.append(_missing(reason))
            cross_signal.append(_missing(reason))
            previous = None
            continue
        difference.append(_value(wave_point.value - signal_point.value))
        if previous is None:
            cross_signal.append(_missing("insufficient_cross_history"))
        elif wave_point.value > signal_point.value and previous[0] <= previous[1]:
            cross_signal.append(_value(Decimal("1")))
        elif wave_point.value < signal_point.value and previous[0] >= previous[1]:
            cross_signal.append(_value(Decimal("-1")))
        else:
            cross_signal.append(_value(Decimal("0")))
        previous = (wave_point.value, signal_point.value)
    return wavetrend, wavetrend_signal, tuple(difference), tuple(cross_signal)


def _parabolic_sar_points(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    start: Decimal,
    increment: Decimal,
    maximum: Decimal,
) -> tuple[TechnicalIndicatorPoint, ...]:
    """Reproduce the causal state ordering of Pine\'s built-in ta.sar."""

    points: list[TechnicalIndicatorPoint] = []
    sar: Decimal | None = None
    extreme: Decimal | None = None
    acceleration: Decimal | None = None
    is_below: bool | None = None
    previous_close: Decimal | None = None
    previous_high: Decimal | None = None
    previous_low: Decimal | None = None
    second_previous_high: Decimal | None = None
    second_previous_low: Decimal | None = None
    segment_index = 0

    for close_value, high_value, low_value in zip(
        close, high, low, strict=True
    ):
        if close_value is None or high_value is None or low_value is None:
            points.append(_missing("indicator_input_missing"))
            sar = None
            extreme = None
            acceleration = None
            is_below = None
            previous_close = None
            previous_high = None
            previous_low = None
            second_previous_high = None
            second_previous_low = None
            segment_index = 0
            continue

        if segment_index == 0:
            points.append(_missing("insufficient_sar_history"))
            previous_close = close_value
            previous_high = high_value
            previous_low = low_value
            segment_index = 1
            continue

        first_trend_bar = False
        if segment_index == 1:
            assert (
                previous_close is not None
                and previous_high is not None
                and previous_low is not None
            )
            if close_value > previous_close:
                is_below = True
                extreme = high_value
                sar = previous_low
            else:
                is_below = False
                extreme = low_value
                sar = previous_high
            acceleration = start
            first_trend_bar = True

        assert (
            sar is not None
            and extreme is not None
            and acceleration is not None
            and is_below is not None
            and previous_high is not None
            and previous_low is not None
        )
        sar = _clean_decimal(sar + acceleration * (extreme - sar))

        if is_below:
            if sar > low_value:
                first_trend_bar = True
                is_below = False
                sar = max(high_value, extreme)
                extreme = low_value
                acceleration = start
        elif sar < high_value:
            first_trend_bar = True
            is_below = True
            sar = min(low_value, extreme)
            extreme = high_value
            acceleration = start

        if not first_trend_bar:
            if is_below and high_value > extreme:
                extreme = high_value
                acceleration = min(acceleration + increment, maximum)
            elif not is_below and low_value < extreme:
                extreme = low_value
                acceleration = min(acceleration + increment, maximum)

        if is_below:
            sar = min(sar, previous_low)
            if second_previous_low is not None:
                sar = min(sar, second_previous_low)
        else:
            sar = max(sar, previous_high)
            if second_previous_high is not None:
                sar = max(sar, second_previous_high)

        sar = _clean_decimal(sar)
        points.append(_value(sar))
        second_previous_high = previous_high
        second_previous_low = previous_low
        previous_close = close_value
        previous_high = high_value
        previous_low = low_value
        segment_index += 1

    return tuple(points)


def _di_and_dx(
    smoothed_true_range: Decimal,
    smoothed_plus_dm: Decimal,
    smoothed_minus_dm: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    if smoothed_true_range.is_zero():
        return Decimal("0"), Decimal("0"), Decimal("0")
    plus_di = _clean_decimal(
        Decimal("100") * smoothed_plus_dm / smoothed_true_range
    )
    minus_di = _clean_decimal(
        Decimal("100") * smoothed_minus_dm / smoothed_true_range
    )
    denominator = plus_di + minus_di
    if denominator.is_zero():
        dx = Decimal("0")
    else:
        dx = _clean_decimal(Decimal("100") * abs(plus_di - minus_di) / denominator)
    return plus_di, minus_di, dx


def _average_directional_index_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
]:
    plus_points: list[TechnicalIndicatorPoint] = []
    minus_points: list[TechnicalIndicatorPoint] = []
    adx_points: list[TechnicalIndicatorPoint] = []
    previous: tuple[Decimal, Decimal, Decimal] | None = None
    interval_count = 0
    raw_true_range_sum = Decimal("0")
    raw_plus_dm_sum = Decimal("0")
    raw_minus_dm_sum = Decimal("0")
    smoothed_true_range: Decimal | None = None
    smoothed_plus_dm: Decimal | None = None
    smoothed_minus_dm: Decimal | None = None
    dx_seed: list[Decimal] = []
    adx: Decimal | None = None

    for high_value, low_value, close_value in zip(high, low, close, strict=True):
        if high_value is None or low_value is None or close_value is None:
            previous = None
            interval_count = 0
            raw_true_range_sum = Decimal("0")
            raw_plus_dm_sum = Decimal("0")
            raw_minus_dm_sum = Decimal("0")
            smoothed_true_range = None
            smoothed_plus_dm = None
            smoothed_minus_dm = None
            dx_seed.clear()
            adx = None
            plus_points.append(_missing("indicator_input_missing"))
            minus_points.append(_missing("indicator_input_missing"))
            adx_points.append(_missing("indicator_input_missing"))
            continue

        current = (high_value, low_value, close_value)
        if previous is None:
            previous = current
            plus_points.append(_missing("insufficient_history"))
            minus_points.append(_missing("insufficient_history"))
            adx_points.append(_missing("insufficient_history"))
            continue

        previous_high, previous_low, previous_close = previous
        upward_move = high_value - previous_high
        downward_move = previous_low - low_value
        plus_dm = (
            upward_move
            if upward_move > downward_move and upward_move > Decimal("0")
            else Decimal("0")
        )
        minus_dm = (
            downward_move
            if downward_move > upward_move and downward_move > Decimal("0")
            else Decimal("0")
        )
        true_range = max(
            high_value - low_value,
            abs(high_value - previous_close),
            abs(low_value - previous_close),
        )
        interval_count += 1
        if smoothed_true_range is None:
            raw_true_range_sum += true_range
            raw_plus_dm_sum += plus_dm
            raw_minus_dm_sum += minus_dm
            if interval_count < window:
                plus_points.append(_missing("insufficient_history"))
                minus_points.append(_missing("insufficient_history"))
                adx_points.append(_missing("insufficient_history"))
                previous = current
                continue
            smoothed_true_range = _clean_decimal(raw_true_range_sum)
            smoothed_plus_dm = _clean_decimal(raw_plus_dm_sum)
            smoothed_minus_dm = _clean_decimal(raw_minus_dm_sum)
        else:
            assert smoothed_plus_dm is not None and smoothed_minus_dm is not None
            smoothed_true_range = _clean_decimal(
                smoothed_true_range
                - smoothed_true_range / Decimal(window)
                + true_range
            )
            smoothed_plus_dm = _clean_decimal(
                smoothed_plus_dm
                - smoothed_plus_dm / Decimal(window)
                + plus_dm
            )
            smoothed_minus_dm = _clean_decimal(
                smoothed_minus_dm
                - smoothed_minus_dm / Decimal(window)
                + minus_dm
            )

        assert smoothed_true_range is not None
        assert smoothed_plus_dm is not None and smoothed_minus_dm is not None
        plus_di, minus_di, dx = _di_and_dx(
            smoothed_true_range, smoothed_plus_dm, smoothed_minus_dm
        )
        plus_points.append(_value(plus_di))
        minus_points.append(_value(minus_di))
        if adx is None:
            dx_seed.append(dx)
            if len(dx_seed) < window:
                adx_points.append(_missing("insufficient_history"))
            else:
                adx = _mean(dx_seed)
                adx_points.append(_value(adx))
        else:
            adx = _clean_decimal(
                (adx * Decimal(window - 1) + dx) / Decimal(window)
            )
            adx_points.append(_value(adx))
        previous = current
    return tuple(plus_points), tuple(minus_points), tuple(adx_points)


@dataclass(slots=True)
class _SuperTrendCandidate:
    factor: Decimal
    upper: Decimal | None
    lower: Decimal | None
    output: Decimal | None = None
    performance: Decimal = Decimal("0")
    trend: int = 0


def _percentile_linear_interpolation(
    values: tuple[Decimal, ...], percentile: Decimal
) -> Decimal:
    ordered = tuple(sorted(values))
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = Decimal(len(ordered) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - Decimal(lower_index)
    return _clean_decimal(
        ordered[lower_index]
        + weight * (ordered[upper_index] - ordered[lower_index])
    )


def _cluster_supertrend_candidates(
    candidates: tuple[_SuperTrendCandidate, ...],
) -> tuple[
    tuple[tuple[Decimal, ...], ...],
    tuple[tuple[Decimal, ...], ...],
    bool,
]:
    performances = tuple(item.performance for item in candidates)
    centroids = (
        _percentile_linear_interpolation(
            performances, Decimal("0.25")
        ),
        _percentile_linear_interpolation(
            performances, Decimal("0.50")
        ),
        _percentile_linear_interpolation(
            performances, Decimal("0.75")
        ),
    )
    # With ordered centroids, one-dimensional Lloyd assignments are contiguous.
    # The finite partition bound also retains Pine's inclusive 0..1000 cap.
    partition_bound = (len(candidates) + 1) * (len(candidates) + 2) // 2
    iteration_limit = min(
        SUPERTREND_AI_KMEANS_ITERATION_CAP, partition_bound
    )
    factor_clusters: tuple[tuple[Decimal, ...], ...] = ((), (), ())
    performance_clusters: tuple[tuple[Decimal, ...], ...] = ((), (), ())
    for _ in range(iteration_limit):
        factor_lists: list[list[Decimal]] = [[], [], []]
        performance_lists: list[list[Decimal]] = [[], [], []]
        for candidate in candidates:
            cluster_index = min(
                range(3),
                key=lambda index: (
                    abs(candidate.performance - centroids[index]),
                    index,
                ),
            )
            factor_lists[cluster_index].append(candidate.factor)
            performance_lists[cluster_index].append(candidate.performance)
        factor_clusters = tuple(
            tuple(cluster) for cluster in factor_lists
        )
        performance_clusters = tuple(
            tuple(cluster) for cluster in performance_lists
        )
        new_centroids = tuple(
            _mean(cluster) if cluster else centroids[index]
            for index, cluster in enumerate(performance_lists)
        )
        if new_centroids == centroids:
            return factor_clusters, performance_clusters, True
        centroids = new_centroids
    return factor_clusters, performance_clusters, False


def _supertrend_ai_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    minimum_factor: Decimal,
    maximum_factor: Decimal,
    factor_step: Decimal,
    performance_memory: Decimal,
    cluster: str,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[str, ...],
]:
    """Adapt LuxAlgo's Pine v5 clustering SuperTrend to a causal series kernel.

    TradingView colors, labels, tables, and last-bar-relative calculation
    controls are presentation/runtime concerns. The tool processes every
    supplied bar causally; its declared input limit replaces ``maxData`` and
    the bounded convergence loop retains Pine's default 1,001 assignments.
    """

    factor_count = _supertrend_factor_count(
        minimum_factor, maximum_factor, factor_step
    )
    factors = tuple(
        _clean_decimal(minimum_factor + Decimal(index) * factor_step)
        for index in range(factor_count)
    )
    cluster_index = {"worst": 0, "average": 1, "best": 2}[cluster]
    performance_alpha = Decimal("2") / (performance_memory + Decimal("1"))
    denominator_length = int(performance_memory)
    denominator_alpha = Decimal("2") / Decimal(denominator_length + 1)

    trailing_stop_points: list[TechnicalIndicatorPoint] = []
    adaptive_average_points: list[TechnicalIndicatorPoint] = []
    trend_points: list[TechnicalIndicatorPoint] = []
    performance_index_points: list[TechnicalIndicatorPoint] = []
    target_factor_points: list[TechnicalIndicatorPoint] = []
    warnings = {
        "supertrend_ai_is_online_kmeans_parameter_selection",
        "supertrend_ai_tradingview_presentation_excluded",
        "supertrend_ai_input_limit_replaces_historical_bars_control",
    }

    candidates: tuple[_SuperTrendCandidate, ...] = ()
    atr_seed: list[Decimal] = []
    atr: Decimal | None = None
    denominator: Decimal | None = None
    previous_close: Decimal | None = None
    target_factor: Decimal | None = None
    adaptive_upper: Decimal | None = None
    adaptive_lower: Decimal | None = None
    adaptive_trend = 0
    previous_trailing_stop: Decimal | None = None
    adaptive_average: Decimal | None = None

    def append_missing(reason: str) -> None:
        for points in (
            trailing_stop_points,
            adaptive_average_points,
            trend_points,
            performance_index_points,
            target_factor_points,
        ):
            points.append(_missing(reason))

    for close_value, high_value, low_value in zip(
        close, high, low, strict=True
    ):
        if close_value is None or high_value is None or low_value is None:
            append_missing("indicator_input_missing")
            candidates = ()
            atr_seed = []
            atr = None
            denominator = None
            previous_close = None
            target_factor = None
            adaptive_upper = None
            adaptive_lower = None
            adaptive_trend = 0
            previous_trailing_stop = None
            adaptive_average = None
            continue

        midpoint = _clean_decimal((high_value + low_value) / Decimal("2"))
        if not candidates:
            candidates = tuple(
                _SuperTrendCandidate(
                    factor=factor,
                    upper=midpoint,
                    lower=midpoint,
                )
                for factor in factors
            )
            adaptive_upper = midpoint
            adaptive_lower = midpoint

        true_range = (
            high_value - low_value
            if previous_close is None
            else max(
                high_value - low_value,
                abs(high_value - previous_close),
                abs(low_value - previous_close),
            )
        )
        if atr is None:
            atr_seed.append(_clean_decimal(true_range))
            if len(atr_seed) == window:
                atr = _mean(atr_seed)
        else:
            atr = _clean_decimal(
                (atr * Decimal(window - 1) + true_range)
                / Decimal(window)
            )

        price_change = (
            Decimal("0")
            if previous_close is None
            else close_value - previous_close
        )
        for candidate in candidates:
            old_upper = candidate.upper
            old_lower = candidate.lower
            old_output = candidate.output
            if old_upper is not None and close_value > old_upper:
                candidate.trend = 1
            elif old_lower is not None and close_value < old_lower:
                candidate.trend = 0

            if atr is None:
                candidate.upper = None
                candidate.lower = None
            else:
                raw_upper = _clean_decimal(
                    midpoint + atr * candidate.factor
                )
                raw_lower = _clean_decimal(
                    midpoint - atr * candidate.factor
                )
                candidate.upper = (
                    min(raw_upper, old_upper)
                    if previous_close is not None
                    and old_upper is not None
                    and previous_close < old_upper
                    else raw_upper
                )
                candidate.lower = (
                    max(raw_lower, old_lower)
                    if previous_close is not None
                    and old_lower is not None
                    and previous_close > old_lower
                    else raw_lower
                )

            direction = Decimal("0")
            if previous_close is not None and old_output is not None:
                if previous_close > old_output:
                    direction = Decimal("1")
                elif previous_close < old_output:
                    direction = Decimal("-1")
            candidate.performance = _clean_decimal(
                candidate.performance
                + performance_alpha
                * (
                    price_change * direction
                    - candidate.performance
                )
            )
            candidate.output = (
                candidate.lower
                if candidate.trend == 1
                else candidate.upper
            )

        factor_clusters, performance_clusters, converged = (
            _cluster_supertrend_candidates(candidates)
        )
        if not converged:
            warnings.add("supertrend_ai_kmeans_iteration_cap_reached")
        selected_factors = factor_clusters[cluster_index]
        if selected_factors:
            target_factor = _mean(selected_factors)
        selected_performance = performance_clusters[cluster_index]
        performance_numerator = max(
            _mean(selected_performance)
            if selected_performance
            else Decimal("0"),
            Decimal("0"),
        )

        if previous_close is not None:
            absolute_change = abs(close_value - previous_close)
            denominator = (
                _clean_decimal(absolute_change)
                if denominator is None
                else _clean_decimal(
                    denominator
                    + denominator_alpha
                    * (absolute_change - denominator)
                )
            )
        performance_index = (
            None
            if denominator is None or denominator.is_zero()
            else _clean_decimal(performance_numerator / denominator)
        )

        old_adaptive_upper = adaptive_upper
        old_adaptive_lower = adaptive_lower
        trailing_stop: Decimal | None
        if atr is None or target_factor is None:
            adaptive_upper = None
            adaptive_lower = None
            trailing_stop = None
        else:
            raw_upper = _clean_decimal(
                midpoint + atr * target_factor
            )
            raw_lower = _clean_decimal(
                midpoint - atr * target_factor
            )
            adaptive_upper = (
                min(raw_upper, old_adaptive_upper)
                if previous_close is not None
                and old_adaptive_upper is not None
                and previous_close < old_adaptive_upper
                else raw_upper
            )
            adaptive_lower = (
                max(raw_lower, old_adaptive_lower)
                if previous_close is not None
                and old_adaptive_lower is not None
                and previous_close > old_adaptive_lower
                else raw_lower
            )
            if close_value > adaptive_upper:
                adaptive_trend = 1
            elif close_value < adaptive_lower:
                adaptive_trend = 0
            trailing_stop = (
                adaptive_lower
                if adaptive_trend == 1
                else adaptive_upper
            )

        if previous_trailing_stop is None and trailing_stop is not None:
            adaptive_average = trailing_stop
        elif trailing_stop is None:
            adaptive_average = None
        elif performance_index is None or adaptive_average is None:
            adaptive_average = None
        else:
            adaptive_average = _clean_decimal(
                adaptive_average
                + performance_index
                * (trailing_stop - adaptive_average)
            )

        trailing_reason = (
            "insufficient_history"
            if atr is None
            else "cluster_not_established"
            if target_factor is None
            else None
        )
        trailing_stop_points.append(
            _missing(trailing_reason)
            if trailing_stop is None
            else _value(trailing_stop)
        )
        adaptive_average_points.append(
            _value(adaptive_average)
            if adaptive_average is not None
            else _missing(
                trailing_reason or "performance_index_not_established"
            )
        )
        trend_points.append(
            _value(Decimal(adaptive_trend))
            if trailing_stop is not None
            else _missing(trailing_reason or "cluster_not_established")
        )
        performance_index_points.append(
            _value(performance_index)
            if performance_index is not None
            else _missing(
                "insufficient_history"
                if denominator is None
                else "zero_performance_denominator"
            )
        )
        target_factor_points.append(
            _value(target_factor)
            if target_factor is not None
            else _missing("cluster_not_established")
        )
        previous_trailing_stop = trailing_stop
        previous_close = close_value

    return (
        tuple(trailing_stop_points),
        tuple(adaptive_average_points),
        tuple(trend_points),
        tuple(performance_index_points),
        tuple(target_factor_points),
        tuple(sorted(warnings)),
    )


def _median_ignoring_missing(
    values: Sequence[Decimal | None],
) -> Decimal | None:
    finite = tuple(sorted(value for value in values if value is not None))
    if not finite:
        return None
    middle = len(finite) // 2
    if len(finite) % 2:
        return finite[middle]
    return _clean_decimal(
        (finite[middle - 1] + finite[middle]) / Decimal("2")
    )


def _swing_structure_forecast_components(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    *,
    window: int,
    sample_count: int,
    aggregation_method: str,
) -> tuple[
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[TechnicalIndicatorPoint, ...],
    tuple[str, ...],
]:
    """Causally unroll BOSWaves' Pine v6 latest-bar swing forecast.

    The source script's same-bar dependencies are retained: pivot confirmations
    are available to the completed-leg sample recorded on a direction change,
    and the low extreme wins when both rolling-extreme direction tests match.
    Chart objects and future x-coordinates are presentation state and are
    deliberately excluded.
    """

    confirmed_high_points: list[TechnicalIndicatorPoint] = []
    confirmed_low_points: list[TechnicalIndicatorPoint] = []
    direction_points: list[TechnicalIndicatorPoint] = []
    origin_points: list[TechnicalIndicatorPoint] = []
    target_points: list[TechnicalIndicatorPoint] = []
    percent_points: list[TechnicalIndicatorPoint] = []
    duration_points: list[TechnicalIndicatorPoint] = []
    standard_deviation_points: list[TechnicalIndicatorPoint] = []
    band_half_points: list[TechnicalIndicatorPoint] = []
    origin_age_points: list[TechnicalIndicatorPoint] = []

    maximums: deque[tuple[int, Decimal]] = deque()
    minimums: deque[tuple[int, Decimal]] = deque()
    percent_samples: deque[Decimal | None] = deque()
    duration_samples: deque[Decimal | None] = deque()
    segment_length = 0
    direction = False
    previous_high: Decimal | None = None
    previous_low: Decimal | None = None
    previous_rolling_high: Decimal | None = None
    previous_rolling_low: Decimal | None = None
    confirmed_high: Decimal | None = None
    confirmed_high_index: int | None = None
    confirmed_low: Decimal | None = None
    confirmed_low_index: int | None = None
    previous_close: Decimal | None = None
    atr_seed: list[Decimal] = []
    atr: Decimal | None = None
    atr_window = 200

    def append_forecast_missing(reason: str) -> None:
        for points in (
            origin_points,
            target_points,
            percent_points,
            duration_points,
            standard_deviation_points,
            band_half_points,
            origin_age_points,
        ):
            points.append(_missing(reason))

    for index, (close_value, high_value, low_value) in enumerate(
        zip(close, high, low, strict=True)
    ):
        if close_value is None or high_value is None or low_value is None:
            for points in (
                confirmed_high_points,
                confirmed_low_points,
                direction_points,
            ):
                points.append(_missing("indicator_input_missing"))
            append_forecast_missing("indicator_input_missing")
            maximums.clear()
            minimums.clear()
            percent_samples.clear()
            duration_samples.clear()
            segment_length = 0
            direction = False
            previous_high = None
            previous_low = None
            previous_rolling_high = None
            previous_rolling_low = None
            confirmed_high = None
            confirmed_high_index = None
            confirmed_low = None
            confirmed_low_index = None
            previous_close = None
            atr_seed.clear()
            atr = None
            continue

        segment_length += 1
        while maximums and maximums[-1][1] < high_value:
            maximums.pop()
        maximums.append((index, high_value))
        while minimums and minimums[-1][1] > low_value:
            minimums.pop()
        minimums.append((index, low_value))
        first_retained_index = index - window + 1
        while maximums and maximums[0][0] < first_retained_index:
            maximums.popleft()
        while minimums and minimums[0][0] < first_retained_index:
            minimums.popleft()
        rolling_high = (
            maximums[0][1] if segment_length >= window else None
        )
        rolling_low = minimums[0][1] if segment_length >= window else None

        true_range = (
            high_value - low_value
            if previous_close is None
            else max(
                high_value - low_value,
                abs(high_value - previous_close),
                abs(low_value - previous_close),
            )
        )
        if atr is None:
            atr_seed.append(_clean_decimal(true_range))
            if len(atr_seed) == atr_window:
                atr = _mean(atr_seed)
        else:
            atr = _clean_decimal(
                (atr * Decimal(atr_window - 1) + true_range)
                / Decimal(atr_window)
            )

        high_confirmed_now = (
            rolling_high is not None
            and previous_rolling_high is not None
            and previous_high is not None
            and previous_high == previous_rolling_high
            and high_value < rolling_high
        )
        if high_confirmed_now:
            confirmed_high = previous_high
            confirmed_high_index = index - 1
            confirmed_high_points.append(_value(confirmed_high))
        else:
            confirmed_high_points.append(
                _missing(
                    "insufficient_history"
                    if rolling_high is None
                    or previous_rolling_high is None
                    else "no_swing_high_confirmation"
                )
            )

        low_confirmed_now = (
            rolling_low is not None
            and previous_rolling_low is not None
            and previous_low is not None
            and previous_low == previous_rolling_low
            and low_value > rolling_low
        )
        if low_confirmed_now:
            confirmed_low = previous_low
            confirmed_low_index = index - 1
            confirmed_low_points.append(_value(confirmed_low))
        else:
            confirmed_low_points.append(
                _missing(
                    "insufficient_history"
                    if rolling_low is None
                    or previous_rolling_low is None
                    else "no_swing_low_confirmation"
                )
            )

        if rolling_high is None or rolling_low is None:
            direction_points.append(_missing("insufficient_history"))
            append_forecast_missing("insufficient_history")
            previous_high = high_value
            previous_low = low_value
            previous_rolling_high = rolling_high
            previous_rolling_low = rolling_low
            previous_close = close_value
            continue

        previous_direction = direction
        if high_value == rolling_high:
            direction = True
        if low_value == rolling_low:
            direction = False
        direction_points.append(
            _value(Decimal("1") if direction else Decimal("-1"))
        )

        if direction != previous_direction:
            percent: Decimal | None = None
            duration: Decimal | None = None
            if (
                confirmed_high is not None
                and confirmed_low is not None
                and confirmed_high_index is not None
                and confirmed_low_index is not None
            ):
                denominator = (
                    confirmed_low if not direction else confirmed_high
                )
                if not denominator.is_zero():
                    numerator = (
                        confirmed_high - confirmed_low
                        if not direction
                        else confirmed_low - confirmed_high
                    )
                    percent = _clean_decimal(
                        abs(numerator / denominator * Decimal("100"))
                    )
                duration = Decimal(
                    abs(confirmed_high_index - confirmed_low_index)
                )
            percent_samples.append(percent)
            duration_samples.append(duration)
            if len(percent_samples) > sample_count:
                percent_samples.popleft()
                duration_samples.popleft()

        if len(percent_samples) < 2:
            append_forecast_missing("insufficient_completed_swings")
        else:
            if aggregation_method == "weighted":
                if any(value is None for value in percent_samples):
                    forecast_percent = None
                else:
                    total_weight = Decimal(
                        len(percent_samples) * (len(percent_samples) + 1) // 2
                    )
                    forecast_percent = _clean_decimal(
                        sum(
                            (
                                value * Decimal(sample_index + 1)
                                for sample_index, value in enumerate(
                                    percent_samples
                                )
                                if value is not None
                            ),
                            Decimal("0"),
                        )
                        / total_weight
                    )
                if any(value is None for value in duration_samples):
                    forecast_duration = None
                else:
                    forecast_duration = _clean_decimal(
                        sum(
                            (
                                value * Decimal(sample_index + 1)
                                for sample_index, value in enumerate(
                                    duration_samples
                                )
                                if value is not None
                            ),
                            Decimal("0"),
                        )
                        / Decimal(
                            len(duration_samples)
                            * (len(duration_samples) + 1)
                            // 2
                        )
                    )
            elif aggregation_method == "median":
                forecast_percent = _median_ignoring_missing(percent_samples)
                forecast_duration = _median_ignoring_missing(
                    duration_samples
                )
            else:
                finite_percent = tuple(
                    value
                    for value in percent_samples
                    if value is not None
                )
                finite_duration = tuple(
                    value
                    for value in duration_samples
                    if value is not None
                )
                forecast_percent = (
                    _mean(finite_percent) if finite_percent else None
                )
                forecast_duration = (
                    _mean(finite_duration) if finite_duration else None
                )

            if (
                forecast_percent is None
                or any(value is None for value in percent_samples)
            ):
                standard_deviation = None
            else:
                variance = _clean_decimal(
                    sum(
                        (
                            (value - forecast_percent)
                            * (value - forecast_percent)
                            for value in percent_samples
                            if value is not None
                        ),
                        Decimal("0"),
                    )
                    / Decimal(len(percent_samples))
                )
                standard_deviation = _clean_decimal(variance.sqrt())

            origin = confirmed_high if not direction else confirmed_low
            origin_index = (
                confirmed_high_index
                if not direction
                else confirmed_low_index
            )
            target = (
                None
                if origin is None or forecast_percent is None
                else _clean_decimal(
                    origin
                    * (
                        Decimal("1") - forecast_percent / Decimal("100")
                        if not direction
                        else Decimal("1")
                        + forecast_percent / Decimal("100")
                    )
                )
            )
            band_half = (
                None
                if origin is None
                or standard_deviation is None
                or atr is None
                else _clean_decimal(
                    max(
                        origin * standard_deviation / Decimal("100"),
                        atr * Decimal("0.1"),
                    )
                )
            )

            origin_points.append(
                _value(origin)
                if origin is not None
                else _missing("forecast_origin_not_established")
            )
            target_points.append(
                _value(target)
                if target is not None
                else _missing(
                    "forecast_origin_not_established"
                    if origin is None
                    else "swing_percent_history_missing"
                )
            )
            percent_points.append(
                _value(forecast_percent)
                if forecast_percent is not None
                else _missing("swing_percent_history_missing")
            )
            duration_points.append(
                _value(forecast_duration)
                if forecast_duration is not None
                else _missing("swing_duration_history_missing")
            )
            standard_deviation_points.append(
                _value(standard_deviation)
                if standard_deviation is not None
                else _missing("swing_percent_history_missing")
            )
            band_half_points.append(
                _value(band_half)
                if band_half is not None
                else _missing(
                    "forecast_origin_not_established"
                    if origin is None
                    else "swing_percent_history_missing"
                    if standard_deviation is None
                    else "insufficient_atr_history"
                )
            )
            origin_age_points.append(
                _value(Decimal(index - origin_index))
                if origin_index is not None
                else _missing("forecast_origin_not_established")
            )

        previous_high = high_value
        previous_low = low_value
        previous_rolling_high = rolling_high
        previous_rolling_low = rolling_low
        previous_close = close_value

    return (
        tuple(confirmed_high_points),
        tuple(confirmed_low_points),
        tuple(direction_points),
        tuple(origin_points),
        tuple(target_points),
        tuple(percent_points),
        tuple(duration_points),
        tuple(standard_deviation_points),
        tuple(band_half_points),
        tuple(origin_age_points),
        (
            "swing_structure_forecast_forward_bar_visuals_excluded",
            "swing_structure_forecast_is_causal_unrolled_latest_bar_adaptation",
            "swing_structure_forecast_support_resistance_state_excluded",
            "swing_structure_forecast_tradingview_presentation_excluded",
        ),
    )


def _on_balance_volume_points(
    close: tuple[Decimal | None, ...], volume: tuple[Decimal | None, ...]
) -> tuple[TechnicalIndicatorPoint, ...]:
    result: list[TechnicalIndicatorPoint] = []
    previous_close: Decimal | None = None
    total = Decimal("0")
    for close_value, volume_value in zip(close, volume, strict=True):
        if close_value is None or volume_value is None:
            previous_close = None
            total = Decimal("0")
            result.append(_missing("indicator_input_missing"))
            continue
        if previous_close is None:
            total = Decimal("0")
        elif close_value > previous_close:
            total = _clean_decimal(total + volume_value)
        elif close_value < previous_close:
            total = _clean_decimal(total - volume_value)
        previous_close = close_value
        result.append(_value(total))
    return tuple(result)


def _accumulation_distribution_points(
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...],
    low: tuple[Decimal | None, ...],
    volume: tuple[Decimal | None, ...],
) -> tuple[TechnicalIndicatorPoint, ...]:
    result: list[TechnicalIndicatorPoint] = []
    cumulative = Decimal("0")
    in_segment = False
    for high_value, low_value, close_value, volume_value in zip(
        high, low, close, volume, strict=True
    ):
        if (
            high_value is None
            or low_value is None
            or close_value is None
            or volume_value is None
        ):
            cumulative = Decimal("0")
            in_segment = False
            result.append(_missing("indicator_input_missing"))
            continue
        if not in_segment:
            cumulative = Decimal("0")
            in_segment = True
        if high_value == low_value:
            money_flow_multiplier = Decimal("0")
        else:
            money_flow_multiplier = _clean_decimal(
                ((close_value - low_value) - (high_value - close_value))
                / (high_value - low_value)
            )
        cumulative = _clean_decimal(
            cumulative + money_flow_multiplier * volume_value
        )
        result.append(_value(cumulative))
    return tuple(result)


def calculate_technical_indicator(
    *,
    indicator: str,
    close: tuple[Decimal | None, ...],
    high: tuple[Decimal | None, ...] = (),
    low: tuple[Decimal | None, ...] = (),
    volume: tuple[Decimal | None, ...] = (),
    window: int | None = None,
    fast_window: int | None = None,
    slow_window: int | None = None,
    signal_window: int | None = None,
    standard_deviation_multiplier: Decimal | None = None,
    minimum_factor: Decimal | None = None,
    maximum_factor: Decimal | None = None,
    factor_step: Decimal | None = None,
    performance_memory: Decimal | None = None,
    cluster: str | None = None,
    sample_count: int | None = None,
    aggregation_method: str | None = None,
    percentile_window: int | None = None,
    percentile_high_factor: Decimal | None = None,
    percentile_low_factor: Decimal | None = None,
    start: Decimal | None = None,
    increment: Decimal | None = None,
    maximum: Decimal | None = None,
) -> TechnicalIndicatorResult:
    """Calculate exactly one finite, deterministic technical indicator.

    Required controls may not be omitted and controls irrelevant to the
    selected indicator are rejected. Extra valid OHLCV fields are accepted so
    a public adapter can pass one coherent input shape through this kernel.
    """

    if not isinstance(indicator, str) or indicator not in TECHNICAL_INDICATORS:
        raise ValidationError("technical indicator is not supported")
    parameters = _checked_parameters(
        indicator,
        window=window,
        fast_window=fast_window,
        slow_window=slow_window,
        signal_window=signal_window,
        standard_deviation_multiplier=standard_deviation_multiplier,
        minimum_factor=minimum_factor,
        maximum_factor=maximum_factor,
        factor_step=factor_step,
        performance_memory=performance_memory,
        cluster=cluster,
        sample_count=sample_count,
        aggregation_method=aggregation_method,
        percentile_window=percentile_window,
        percentile_high_factor=percentile_high_factor,
        percentile_low_factor=percentile_low_factor,
        start=start,
        increment=increment,
        maximum=maximum,
    )
    checked_close, checked_high, checked_low, checked_volume = _checked_inputs(
        indicator,
        close=close,
        high=high,
        low=low,
        volume=volume,
    )
    parameter_values = dict(parameters)

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        warnings: tuple[str, ...] = ()
        if indicator == "sma":
            means, _ = _rolling_mean_and_standard_deviation(
                checked_close, window=parameter_values["window"]
            )
            components = (TechnicalIndicatorComponent("sma", means),)
        elif indicator == _ROLLING_REGRESSION_LINE:
            components = (
                TechnicalIndicatorComponent(
                    "rolling_regression_line",
                    _rolling_regression_line_points(
                        checked_close, window=parameter_values["window"]
                    ),
                ),
            )
        elif indicator == "ema":
            components = (
                TechnicalIndicatorComponent(
                    "ema",
                    _ema_points(
                        checked_close, window=parameter_values["window"]
                    ),
                ),
            )
        elif indicator == "rolling_standard_deviation":
            _, standard_deviations = _rolling_mean_and_standard_deviation(
                checked_close, window=parameter_values["window"]
            )
            components = (
                TechnicalIndicatorComponent(
                    "rolling_standard_deviation", standard_deviations
                ),
            )
        elif indicator == "rolling_z_score":
            means, standard_deviations = _rolling_mean_and_standard_deviation(
                checked_close, window=parameter_values["window"]
            )
            z_scores: list[TechnicalIndicatorPoint] = []
            for close_value, mean, standard_deviation in zip(
                checked_close, means, standard_deviations, strict=True
            ):
                if mean.value is None or standard_deviation.value is None:
                    z_scores.append(
                        _missing(mean.missing_reason or "rolling_window_input_missing")
                    )
                elif close_value is None:
                    z_scores.append(_missing("rolling_window_input_missing"))
                elif standard_deviation.value.is_zero():
                    z_scores.append(_missing("zero_rolling_standard_deviation"))
                else:
                    z_scores.append(
                        _value((close_value - mean.value) / standard_deviation.value)
                    )
            components = (
                TechnicalIndicatorComponent("rolling_z_score", tuple(z_scores)),
            )
        elif indicator == "true_range":
            components = (
                TechnicalIndicatorComponent(
                    "true_range",
                    _true_range_points(checked_high, checked_low, checked_close),
                ),
            )
        elif indicator == "average_true_range":
            true_ranges = _true_range_points(checked_high, checked_low, checked_close)
            components = (
                TechnicalIndicatorComponent(
                    "average_true_range",
                    _wilder_average(
                        true_ranges, window=parameter_values["window"]
                    ),
                ),
            )
        elif indicator == "rate_of_change":
            components = (
                TechnicalIndicatorComponent(
                    "rate_of_change",
                    _rate_of_change_points(
                        checked_close, window=parameter_values["window"]
                    ),
                ),
            )
        elif indicator == "relative_strength_index":
            points, warnings = _relative_strength_index_points(
                checked_close, window=parameter_values["window"]
            )
            components = (
                TechnicalIndicatorComponent("relative_strength_index", points),
            )
        elif indicator == "macd":
            line, signal, histogram = _macd_components(
                checked_close,
                fast_window=parameter_values["fast_window"],
                slow_window=parameter_values["slow_window"],
                signal_window=parameter_values["signal_window"],
            )
            components = (
                TechnicalIndicatorComponent("macd_line", line),
                TechnicalIndicatorComponent("signal_line", signal),
                TechnicalIndicatorComponent("histogram", histogram),
            )
        elif indicator == "bollinger_bands":
            middle, upper, lower = _bollinger_components(
                checked_close,
                window=parameter_values["window"],
                multiplier=parameter_values["standard_deviation_multiplier"],
            )
            components = (
                TechnicalIndicatorComponent("middle_band", middle),
                TechnicalIndicatorComponent("upper_band", upper),
                TechnicalIndicatorComponent("lower_band", lower),
            )
        elif indicator == "donchian_channels":
            upper, middle, lower = _donchian_components(
                checked_high, checked_low, window=parameter_values["window"]
            )
            components = (
                TechnicalIndicatorComponent("upper_channel", upper),
                TechnicalIndicatorComponent("middle_channel", middle),
                TechnicalIndicatorComponent("lower_channel", lower),
            )
        elif indicator == "stochastic_oscillator":
            percent_k, percent_d = _stochastic_components(
                checked_close,
                checked_high,
                checked_low,
                window=parameter_values["window"],
                signal_window=parameter_values["signal_window"],
            )
            components = (
                TechnicalIndicatorComponent("percent_k", percent_k),
                TechnicalIndicatorComponent("percent_d", percent_d),
            )
        elif indicator == _KDJ:
            percent_k, percent_d, percent_j = _kdj_components(
                checked_close,
                checked_high,
                checked_low,
                window=parameter_values["window"],
                signal_window=parameter_values["signal_window"],
            )
            components = (
                TechnicalIndicatorComponent("percent_k", percent_k),
                TechnicalIndicatorComponent("percent_d", percent_d),
                TechnicalIndicatorComponent("percent_j", percent_j),
            )
            warnings = ("kdj_tradingview_presentation_excluded",)
        elif indicator == _WILLIAMS_VIX_FIX:
            (
                williams_vix_fix,
                upper_band,
                range_high,
                range_low,
            ) = _williams_vix_fix_components(
                checked_close,
                checked_low,
                window=int(parameter_values["window"]),
                signal_window=int(parameter_values["signal_window"]),
                multiplier=Decimal(
                    parameter_values["standard_deviation_multiplier"]
                ),
                percentile_window=int(
                    parameter_values["percentile_window"]
                ),
                percentile_high_factor=Decimal(
                    parameter_values["percentile_high_factor"]
                ),
                percentile_low_factor=Decimal(
                    parameter_values["percentile_low_factor"]
                ),
            )
            components = (
                TechnicalIndicatorComponent(
                    "williams_vix_fix", williams_vix_fix
                ),
                TechnicalIndicatorComponent("upper_band", upper_band),
                TechnicalIndicatorComponent("range_high", range_high),
                TechnicalIndicatorComponent("range_low", range_low),
            )
            warnings = (
                "williams_vix_fix_tradingview_presentation_excluded",
            )
        elif indicator == _WAVETREND_CROSSES:
            (
                wavetrend,
                wavetrend_signal,
                wavetrend_difference,
                wavetrend_cross_signal,
            ) = _wavetrend_crosses_components(
                checked_close,
                checked_high,
                checked_low,
                window=int(parameter_values["window"]),
                signal_window=int(parameter_values["signal_window"]),
            )
            components = (
                TechnicalIndicatorComponent("wavetrend", wavetrend),
                TechnicalIndicatorComponent(
                    "wavetrend_signal", wavetrend_signal
                ),
                TechnicalIndicatorComponent(
                    "wavetrend_difference", wavetrend_difference
                ),
                TechnicalIndicatorComponent(
                    "wavetrend_cross_signal", wavetrend_cross_signal
                ),
            )
            warnings = (
                "wavetrend_crosses_tradingview_presentation_excluded",
            )
        elif indicator == _PARABOLIC_SAR:
            components = (
                TechnicalIndicatorComponent(
                    "parabolic_sar",
                    _parabolic_sar_points(
                        checked_close,
                        checked_high,
                        checked_low,
                        start=Decimal(parameter_values["start"]),
                        increment=Decimal(parameter_values["increment"]),
                        maximum=Decimal(parameter_values["maximum"]),
                    ),
                ),
            )
            warnings = (
                "parabolic_sar_tradingview_presentation_excluded",
            )
        elif indicator == "average_directional_index":
            plus_di, minus_di, adx = _average_directional_index_components(
                checked_close,
                checked_high,
                checked_low,
                window=parameter_values["window"],
            )
            components = (
                TechnicalIndicatorComponent("plus_di", plus_di),
                TechnicalIndicatorComponent("minus_di", minus_di),
                TechnicalIndicatorComponent("adx", adx),
            )
        elif indicator == _SUPERTREND_AI:
            (
                trailing_stop,
                adaptive_average,
                trend,
                performance_index,
                target_factor,
                warnings,
            ) = _supertrend_ai_components(
                checked_close,
                checked_high,
                checked_low,
                window=int(parameter_values["window"]),
                minimum_factor=Decimal(
                    parameter_values["minimum_factor"]
                ),
                maximum_factor=Decimal(
                    parameter_values["maximum_factor"]
                ),
                factor_step=Decimal(parameter_values["factor_step"]),
                performance_memory=Decimal(
                    parameter_values["performance_memory"]
                ),
                cluster=str(parameter_values["cluster"]),
            )
            components = (
                TechnicalIndicatorComponent(
                    "trailing_stop", trailing_stop
                ),
                TechnicalIndicatorComponent(
                    "adaptive_moving_average", adaptive_average
                ),
                TechnicalIndicatorComponent("trend", trend),
                TechnicalIndicatorComponent(
                    "performance_index", performance_index
                ),
                TechnicalIndicatorComponent(
                    "target_factor", target_factor
                ),
            )
        elif indicator == _SWING_STRUCTURE_FORECAST:
            (
                confirmed_swing_high,
                confirmed_swing_low,
                swing_direction,
                forecast_origin,
                forecast_target,
                forecast_percent,
                forecast_duration_bars,
                forecast_standard_deviation,
                forecast_band_half,
                forecast_origin_age_bars,
                warnings,
            ) = _swing_structure_forecast_components(
                checked_close,
                checked_high,
                checked_low,
                window=int(parameter_values["window"]),
                sample_count=int(parameter_values["sample_count"]),
                aggregation_method=str(
                    parameter_values["aggregation_method"]
                ),
            )
            components = (
                TechnicalIndicatorComponent(
                    "confirmed_swing_high", confirmed_swing_high
                ),
                TechnicalIndicatorComponent(
                    "confirmed_swing_low", confirmed_swing_low
                ),
                TechnicalIndicatorComponent(
                    "swing_direction", swing_direction
                ),
                TechnicalIndicatorComponent(
                    "forecast_origin", forecast_origin
                ),
                TechnicalIndicatorComponent(
                    "forecast_target", forecast_target
                ),
                TechnicalIndicatorComponent(
                    "forecast_percent", forecast_percent
                ),
                TechnicalIndicatorComponent(
                    "forecast_duration_bars", forecast_duration_bars
                ),
                TechnicalIndicatorComponent(
                    "forecast_standard_deviation",
                    forecast_standard_deviation,
                ),
                TechnicalIndicatorComponent(
                    "forecast_band_half", forecast_band_half
                ),
                TechnicalIndicatorComponent(
                    "forecast_origin_age_bars",
                    forecast_origin_age_bars,
                ),
            )
        elif indicator == "on_balance_volume":
            components = (
                TechnicalIndicatorComponent(
                    "on_balance_volume",
                    _on_balance_volume_points(checked_close, checked_volume),
                ),
            )
        else:
            components = (
                TechnicalIndicatorComponent(
                    "accumulation_distribution",
                    _accumulation_distribution_points(
                        checked_close,
                        checked_high,
                        checked_low,
                        checked_volume,
                    ),
                ),
            )
    return TechnicalIndicatorResult(
        indicator=indicator,
        parameters=parameters,
        warnings=warnings,
        components=components,
    )


__all__ = [
    "DECIMAL_PRECISION",
    "MAX_SUPERTREND_AI_FACTORS",
    "MAX_TECHNICAL_INDICATOR_OBSERVATIONS",
    "SUPERTREND_AI_KMEANS_ITERATION_CAP",
    "TECHNICAL_INDICATORS",
    "TECHNICAL_INDICATORS_V2",
    "TECHNICAL_INDICATORS_V21",
    "TECHNICAL_INDICATORS_V22",
    "TECHNICAL_INDICATORS_V23",
    "TECHNICAL_INDICATORS_V24",
    "TECHNICAL_INDICATORS_V25",
    "TECHNICAL_INDICATORS_V26",
    "TECHNICAL_INDICATORS_V27",
    "TechnicalIndicatorComponent",
    "TechnicalIndicatorPoint",
    "TechnicalIndicatorResult",
    "calculate_technical_indicator",
]
