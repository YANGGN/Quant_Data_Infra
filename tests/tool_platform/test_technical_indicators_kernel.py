"""Frozen vectors and hostile boundaries for technical-indicator kernels."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import unittest

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.tool_platform.technical_indicators import (
    MAX_TECHNICAL_INDICATOR_OBSERVATIONS,
    TechnicalIndicatorPoint,
    calculate_technical_indicator,
)


def _values(*items: str | None) -> tuple[Decimal | None, ...]:
    return tuple(None if item is None else Decimal(item) for item in items)


def _component_values(result: object, name: str) -> tuple[TechnicalIndicatorPoint, ...]:
    components = getattr(result, "components")
    return next(component.points for component in components if component.name == name)


class TechnicalIndicatorKernelTests(unittest.TestCase):
    def assertDecimalClose(
        self, actual: Decimal | None, expected: str, tolerance: str = "2e-31"
    ) -> None:
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertLessEqual(abs(actual - Decimal(expected)), Decimal(tolerance))

    def test_sma_ema_standard_deviation_and_z_score_match_fixed_vectors(self) -> None:
        close = _values("1", "2", "3", "4")
        sma = calculate_technical_indicator(indicator="sma", close=close, window=3)
        ema = calculate_technical_indicator(indicator="ema", close=close, window=3)
        standard_deviation = calculate_technical_indicator(
            indicator="rolling_standard_deviation", close=close, window=3
        )
        z_score = calculate_technical_indicator(
            indicator="rolling_z_score", close=close, window=3
        )

        self.assertEqual(
            tuple(point.missing_reason for point in _component_values(sma, "sma")[:2]),
            ("insufficient_history", "insufficient_history"),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(sma, "sma")),
            (None, None, Decimal("2"), Decimal("3")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(ema, "ema")),
            (None, None, Decimal("2"), Decimal("3")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(
                standard_deviation, "rolling_standard_deviation"
            )),
            (None, None, Decimal("1"), Decimal("1")),
        )
        z_points = _component_values(z_score, "rolling_z_score")
        self.assertDecimalClose(z_points[2].value, "1")
        self.assertDecimalClose(z_points[3].value, "1")

    def test_true_range_atr_roc_and_rsi_match_fixed_wilder_vectors(self) -> None:
        close = _values("9", "10", "11", "10", "9")
        high = _values("10", "11", "12", "11", "10")
        low = _values("8", "9", "10", "9", "8")
        true_range = calculate_technical_indicator(
            indicator="true_range", close=close, high=high, low=low
        )
        atr = calculate_technical_indicator(
            indicator="average_true_range", close=close, high=high, low=low, window=2
        )
        roc = calculate_technical_indicator(
            indicator="rate_of_change", close=_values("100", "110", "121"), window=1
        )
        rsi = calculate_technical_indicator(
            indicator="relative_strength_index", close=_values("1", "2", "3", "2", "1"), window=2
        )

        self.assertEqual(
            tuple(point.value for point in _component_values(true_range, "true_range")),
            (Decimal("2"), Decimal("2"), Decimal("2"), Decimal("2"), Decimal("2")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(atr, "average_true_range")),
            (None, Decimal("2"), Decimal("2"), Decimal("2"), Decimal("2")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(roc, "rate_of_change")),
            (None, Decimal("10"), Decimal("10")),
        )
        rsi_points = _component_values(rsi, "relative_strength_index")
        self.assertEqual(
            tuple(point.value for point in rsi_points[:2]), (None, None)
        )
        self.assertEqual(rsi_points[2].value, Decimal("100"))
        self.assertEqual(rsi_points[3].value, Decimal("50"))
        self.assertEqual(rsi_points[4].value, Decimal("25"))

    def test_macd_bollinger_and_donchian_match_fixed_vectors(self) -> None:
        close = _values("1", "2", "3", "4", "5")
        macd = calculate_technical_indicator(
            indicator="macd", close=close, fast_window=2, slow_window=3, signal_window=2
        )
        bands = calculate_technical_indicator(
            indicator="bollinger_bands",
            close=_values("1", "2", "3"),
            window=3,
            standard_deviation_multiplier=Decimal("2"),
        )
        channels = calculate_technical_indicator(
            indicator="donchian_channels",
            close=_values("1", "2", "3"),
            high=_values("2", "3", "4"),
            low=_values("0", "1", "2"),
            window=2,
        )

        self.assertEqual(
            tuple(point.value for point in _component_values(macd, "macd_line")),
            (None, None, Decimal("0.5"), Decimal("0.5"), Decimal("0.5")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(macd, "signal_line")),
            (None, None, None, Decimal("0.5"), Decimal("0.5")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(macd, "histogram")),
            (None, None, None, Decimal("0"), Decimal("0")),
        )
        self.assertEqual(_component_values(bands, "middle_band")[-1].value, Decimal("2"))
        self.assertEqual(_component_values(bands, "upper_band")[-1].value, Decimal("4"))
        self.assertEqual(_component_values(bands, "lower_band")[-1].value, Decimal("0"))
        self.assertEqual(
            tuple(point.value for point in _component_values(channels, "upper_channel")),
            (None, Decimal("3"), Decimal("4")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(channels, "middle_channel")),
            (None, Decimal("1.5"), Decimal("2.5")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(channels, "lower_channel")),
            (None, Decimal("0"), Decimal("1")),
        )

    def test_stochastic_adx_obv_and_accumulation_distribution_match_fixed_vectors(self) -> None:
        stochastic = calculate_technical_indicator(
            indicator="stochastic_oscillator",
            close=_values("1", "2", "3", "4"),
            high=_values("2", "3", "4", "5"),
            low=_values("0", "1", "2", "3"),
            window=2,
            signal_window=2,
        )
        adx = calculate_technical_indicator(
            indicator="average_directional_index",
            close=_values("10", "10", "10", "10"),
            high=_values("10", "10", "10", "10"),
            low=_values("10", "10", "10", "10"),
            window=2,
        )
        trending_adx = calculate_technical_indicator(
            indicator="average_directional_index",
            close=_values("1", "2", "3", "4"),
            high=_values("2", "3", "4", "5"),
            low=_values("0", "1", "2", "3"),
            window=2,
        )
        obv = calculate_technical_indicator(
            indicator="on_balance_volume",
            close=_values("1", "2", "2", "1"),
            volume=_values("10", "10", "10", "10"),
        )
        accumulation_distribution = calculate_technical_indicator(
            indicator="accumulation_distribution",
            close=_values("2", "1", "3"),
            high=_values("2", "3", "4"),
            low=_values("0", "1", "2"),
            volume=_values("10", "10", "10"),
        )

        k = _component_values(stochastic, "percent_k")
        d = _component_values(stochastic, "percent_d")
        self.assertIsNone(k[0].value)
        for point in k[1:]:
            self.assertDecimalClose(point.value, "66.66666666666666666666666666666667")
        self.assertIsNone(d[0].value)
        self.assertIsNone(d[1].value)
        for point in d[2:]:
            self.assertDecimalClose(point.value, "66.66666666666666666666666666666667")
        self.assertEqual(
            tuple(point.value for point in _component_values(adx, "plus_di")),
            (None, None, Decimal("0"), Decimal("0")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(adx, "minus_di")),
            (None, None, Decimal("0"), Decimal("0")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(adx, "adx")),
            (None, None, None, Decimal("0")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(trending_adx, "plus_di")),
            (None, None, Decimal("50"), Decimal("50")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(trending_adx, "minus_di")),
            (None, None, Decimal("0"), Decimal("0")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(trending_adx, "adx")),
            (None, None, None, Decimal("100")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(obv, "on_balance_volume")),
            (Decimal("0"), Decimal("10"), Decimal("10"), Decimal("0")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(
                accumulation_distribution, "accumulation_distribution"
            )),
            (Decimal("10"), Decimal("0"), Decimal("0")),
        )

    def test_zero_denominators_and_flat_rsi_have_explicit_states(self) -> None:
        z_score = calculate_technical_indicator(
            indicator="rolling_z_score", close=_values("2", "2"), window=2
        )
        roc = calculate_technical_indicator(
            indicator="rate_of_change", close=_values("0", "1"), window=1
        )
        stochastic = calculate_technical_indicator(
            indicator="stochastic_oscillator",
            close=_values("1"),
            high=_values("1"),
            low=_values("1"),
            window=1,
            signal_window=1,
        )
        rsi = calculate_technical_indicator(
            indicator="relative_strength_index", close=_values("1", "1", "1"), window=2
        )

        self.assertEqual(
            _component_values(z_score, "rolling_z_score")[-1].missing_reason,
            "zero_rolling_standard_deviation",
        )
        self.assertEqual(
            _component_values(roc, "rate_of_change")[-1].missing_reason,
            "zero_lookback_close",
        )
        self.assertEqual(
            _component_values(stochastic, "percent_k")[0].missing_reason,
            "zero_price_range",
        )
        self.assertEqual(_component_values(rsi, "relative_strength_index")[-1].value, Decimal("50"))
        self.assertEqual(rsi.warnings, ("flat_rsi_window_defined_as_50",))

    def test_recursive_indicators_reset_and_rewarm_after_gaps(self) -> None:
        ema = calculate_technical_indicator(
            indicator="ema", close=_values("1", "2", None, "4", "5"), window=2
        )
        atr = calculate_technical_indicator(
            indicator="average_true_range",
            close=_values("1", "2", None, "4", "5"),
            high=_values("2", "3", None, "5", "6"),
            low=_values("0", "1", None, "3", "4"),
            window=2,
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(ema, "ema")),
            (None, Decimal("1.5"), None, None, Decimal("4.5")),
        )
        self.assertEqual(
            tuple(point.missing_reason for point in _component_values(ema, "ema")),
            (
                "insufficient_history",
                None,
                "indicator_input_missing",
                "insufficient_history",
                None,
            ),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(atr, "average_true_range")),
            (None, Decimal("2"), None, None, Decimal("2")),
        )

    def test_rolling_missingness_is_never_skipped_and_window_one_is_safe(self) -> None:
        sma = calculate_technical_indicator(
            indicator="sma", close=_values("1", None, "3", "4"), window=2
        )
        one = calculate_technical_indicator(
            indicator="sma", close=_values("-0", "3"), window=1
        )
        stochastic = calculate_technical_indicator(
            indicator="stochastic_oscillator",
            close=_values("1", "2"),
            high=_values("2", "3"),
            low=_values("0", "1"),
            window=1,
            signal_window=1,
        )
        self.assertEqual(
            tuple(point.missing_reason for point in _component_values(sma, "sma")),
            ("insufficient_history", "rolling_window_input_missing", "rolling_window_input_missing", None),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(one, "sma")),
            (Decimal("0"), Decimal("3")),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(stochastic, "percent_d")),
            (Decimal("50"), Decimal("50")),
        )

    def test_invalid_parameters_inputs_and_ohlc_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="unknown", close=_values("1"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="sma", close=_values("1"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="sma", close=_values("1"), window=1, signal_window=1)
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="rolling_standard_deviation", close=_values("1"), window=1)
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="macd", close=_values("1", "2"), fast_window=2, slow_window=2, signal_window=1)
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="bollinger_bands", close=_values("1", "2"), window=2, standard_deviation_multiplier=Decimal("0"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="true_range", close=_values("1"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="sma", close=[Decimal("1")], window=1)  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="sma", close=_values("1"), high=_values("2", "3"), window=1)
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="on_balance_volume", close=_values("1"), volume=_values("-1"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="true_range", close=_values("2"), high=_values("1"), low=_values("0"))
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="true_range", close=_values("1"), high=_values("0"), low=_values("2"))
        with self.assertRaises(ResourceLimitError):
            calculate_technical_indicator(
                indicator="sma",
                close=tuple(Decimal("1") for _ in range(MAX_TECHNICAL_INDICATOR_OBSERVATIONS + 1)),
                window=1,
            )

    def test_decimal_context_isolation_frozen_results_and_component_alignment(self) -> None:
        kwargs = {
            "indicator": "bollinger_bands",
            "close": _values("1.1", "2.2", "3.3", "4.4"),
            "window": 3,
            "standard_deviation_multiplier": Decimal("2"),
        }
        baseline = calculate_technical_indicator(**kwargs)
        with localcontext() as context:
            context.prec = 7
            self.assertEqual(calculate_technical_indicator(**kwargs), baseline)
        with self.assertRaises(FrozenInstanceError):
            baseline.components[0].points[0].value = Decimal("1")  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            baseline.components[0].name = "changed"  # type: ignore[misc]
        self.assertEqual(
            tuple(component.name for component in baseline.components),
            ("middle_band", "upper_band", "lower_band"),
        )
        self.assertTrue(all(len(component.points) == 4 for component in baseline.components))
