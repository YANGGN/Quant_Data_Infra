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

from quant_data.errors import ResourceLimitError, ValidationError


DECIMAL_PRECISION = 34
MAX_TECHNICAL_INDICATOR_OBSERVATIONS = 10_000

TECHNICAL_INDICATORS = (
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
    parameters: tuple[tuple[str, int | Decimal], ...]
    warnings: tuple[str, ...]
    components: tuple[TechnicalIndicatorComponent, ...]


_WINDOW_PARAMETERS = frozenset(
    {
        "sma",
        "ema",
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
        "accumulation_distribution",
    }
)
_VOLUME_INDICATORS = frozenset(
    {"on_balance_volume", "accumulation_distribution"}
)


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
    if not high or not low:
        return
    for index, (close_value, high_value, low_value) in enumerate(
        zip(close, high, low, strict=True)
    ):
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


def _checked_parameters(
    indicator: str,
    *,
    window: object,
    fast_window: object,
    slow_window: object,
    signal_window: object,
    standard_deviation_multiplier: object,
) -> tuple[tuple[str, int | Decimal], ...]:
    values = {
        "window": window,
        "fast_window": fast_window,
        "slow_window": slow_window,
        "signal_window": signal_window,
        "standard_deviation_multiplier": standard_deviation_multiplier,
    }
    required: tuple[str, ...]
    if indicator in _WINDOW_PARAMETERS:
        required = ("window",)
    elif indicator == _WINDOW_AND_MULTIPLIER:
        required = ("window", "standard_deviation_multiplier")
    elif indicator == _WINDOW_AND_SIGNAL:
        required = ("window", "signal_window")
    elif indicator == _MACD:
        required = ("fast_window", "slow_window", "signal_window")
    elif indicator in _NO_PARAMETERS:
        required = ()
    else:
        raise ValidationError("technical indicator is not supported")
    for name, value in values.items():
        if name not in required and value is not None:
            raise ValidationError(f"{name} is not applicable to {indicator}")
        if name in required and value is None:
            raise ValidationError(f"{indicator} requires {name}")

    checked: dict[str, int | Decimal] = {}
    if "window" in required:
        minimum = (
            2
            if indicator
            in {
                "rolling_standard_deviation",
                "rolling_z_score",
                "bollinger_bands",
            }
            else 1
        )
        checked["window"] = _checked_window(
            values["window"], field="window", minimum=minimum
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
    return tuple((name, checked[name]) for name in sorted(checked))


def _rolling_mean_and_standard_deviation(
    values: tuple[Decimal | None, ...], *, window: int
) -> tuple[tuple[TechnicalIndicatorPoint, ...], tuple[TechnicalIndicatorPoint, ...]]:
    """Return full-window means and sample deviations without skipping gaps."""

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
        if window == 1:
            mean_points.append(_value(mean))
            standard_deviation_points.append(
                _missing("sample_standard_deviation_requires_window_at_least_two")
            )
            continue
        numerator = rolling_square_sum - rolling_sum * mean
        variance = numerator / Decimal(window - 1)
        if variance < 0:
            # Decimal cancellation can make a mathematically nonnegative value
            # microscopically negative. Recompute the affected finite window
            # from centered differences instead of emitting an invalid square root.
            finite_entries = tuple(item for item in entries if item is not None)
            variance = sum(
                ((item - mean) * (item - mean) for item in finite_entries),
                Decimal("0"),
            ) / Decimal(window - 1)
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
    "MAX_TECHNICAL_INDICATOR_OBSERVATIONS",
    "TECHNICAL_INDICATORS",
    "TechnicalIndicatorComponent",
    "TechnicalIndicatorPoint",
    "TechnicalIndicatorResult",
    "calculate_technical_indicator",
]
