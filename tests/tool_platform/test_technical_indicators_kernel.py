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


_SWING_TRIANGLE = _values(
    "100",
    "101",
    "102",
    "103",
    "104",
    "105",
    "106",
    "107",
    "108",
    "109",
    "108",
    "107",
    "106",
    "105",
    "104",
    "103",
    "102",
    "101",
    "100",
    "99",
)


def _swing_ohlc(
    close: tuple[Decimal | None, ...],
    *,
    high_offset: Decimal = Decimal("1"),
    low_offset: Decimal = Decimal("1"),
) -> tuple[
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
]:
    return (
        close,
        tuple(
            None if value is None else value + high_offset for value in close
        ),
        tuple(
            None if value is None else value - low_offset for value in close
        ),
    )


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

    def test_rolling_regression_line_is_causal_and_recovers_after_gap(self) -> None:
        prefix = calculate_technical_indicator(
            indicator="rolling_regression_line",
            close=_values("1", "2", "4", "5"),
            window=3,
        )
        full = calculate_technical_indicator(
            indicator="rolling_regression_line",
            close=_values("1", "2", "4", "5", "100"),
            window=3,
        )
        prefix_points = _component_values(prefix, "rolling_regression_line")
        full_points = _component_values(full, "rolling_regression_line")
        self.assertEqual(
            tuple(point.missing_reason for point in prefix_points[:2]),
            ("insufficient_history", "insufficient_history"),
        )
        self.assertDecimalClose(
            prefix_points[2].value,
            "3.833333333333333333333333333333333",
        )
        self.assertDecimalClose(
            prefix_points[3].value,
            "5.166666666666666666666666666666667",
        )
        self.assertEqual(
            tuple(point.value for point in full_points[:4]),
            tuple(point.value for point in prefix_points),
        )

        gap = calculate_technical_indicator(
            indicator="rolling_regression_line",
            close=_values("1", "2", None, "4", "6", "8"),
            window=2,
        )
        gap_points = _component_values(gap, "rolling_regression_line")
        self.assertEqual(
            tuple(point.missing_reason for point in gap_points),
            (
                "insufficient_history",
                None,
                "rolling_window_input_missing",
                "rolling_window_input_missing",
                None,
                None,
            ),
        )
        self.assertEqual(
            tuple(point.value for point in gap_points[-2:]),
            (Decimal("6"), Decimal("8")),
        )

    def test_supertrend_ai_matches_clustering_vector_and_is_causal(self) -> None:
        kwargs = {
            "indicator": "supertrend_ai",
            "close": _values("10", "13", "15", "20"),
            "high": _values("11", "14", "16", "21"),
            "low": _values("9", "12", "14", "19"),
            "window": 1,
            "minimum_factor": Decimal("1"),
            "maximum_factor": Decimal("3"),
            "factor_step": Decimal("1"),
            "performance_memory": Decimal("2"),
            "cluster": "best",
        }
        result = calculate_technical_indicator(**kwargs)
        self.assertEqual(
            tuple(component.name for component in result.components),
            (
                "trailing_stop",
                "adaptive_moving_average",
                "trend",
                "performance_index",
                "target_factor",
            ),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "trailing_stop")
            ),
            (None, None, Decimal("18"), Decimal("14")),
        )
        with localcontext() as context:
            context.prec = 34
            expected_ama = Decimal("538") / Decimal("37")
            expected_performance = (
                None,
                Decimal("0"),
                Decimal("2") / Decimal("7"),
                Decimal("32") / Decimal("37"),
            )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(
                    result, "adaptive_moving_average"
                )
            ),
            (None, None, Decimal("18"), expected_ama),
        )
        self.assertEqual(
            tuple(point.value for point in _component_values(result, "trend")),
            (None, None, Decimal("0"), Decimal("1")),
        )
        performance_points = _component_values(
            result, "performance_index"
        )
        self.assertEqual(
            tuple(point.value for point in performance_points[:2]),
            expected_performance[:2],
        )
        self.assertDecimalClose(
            performance_points[2].value, str(expected_performance[2])
        )
        self.assertDecimalClose(
            performance_points[3].value, str(expected_performance[3])
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "target_factor")
            ),
            (None, None, Decimal("1"), Decimal("1")),
        )
        self.assertIn(
            "supertrend_ai_input_limit_replaces_historical_bars_control",
            result.warnings,
        )

        prefix_kwargs = {
            **kwargs,
            "close": kwargs["close"][:3],
            "high": kwargs["high"][:3],
            "low": kwargs["low"][:3],
        }
        prefix = calculate_technical_indicator(**prefix_kwargs)
        for full_component, prefix_component in zip(
            result.components, prefix.components, strict=True
        ):
            self.assertEqual(full_component.points[:3], prefix_component.points)

        update_order = calculate_technical_indicator(
            indicator="supertrend_ai",
            close=_values("8", "8", "8", "9", "10"),
            high=_values("9", "9", "9", "10", "11"),
            low=_values("7", "7", "7", "8", "9"),
            window=1,
            minimum_factor=Decimal("0"),
            maximum_factor=Decimal("2"),
            factor_step=Decimal("1"),
            performance_memory=Decimal("2"),
            cluster="best",
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(update_order, "trend")
            ),
            (None, None, None, Decimal("0"), Decimal("0")),
        )

    def test_kdj_matches_bcwsma_vector_and_restarts_after_flat_range(self) -> None:
        result = calculate_technical_indicator(
            indicator="kdj",
            close=_values("7", "8.5", "9", "10.5", "12"),
            high=_values("10", "12", "14", "16", "18"),
            low=_values("4", "5", "6", "8", "10"),
            window=3,
            signal_window=2,
        )
        self.assertEqual(
            tuple(component.name for component in result.components),
            ("percent_k", "percent_d", "percent_j"),
        )
        self.assertEqual(
            tuple(
                point.missing_reason
                for point in _component_values(result, "percent_k")[:2]
            ),
            ("insufficient_history", "insufficient_history"),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "percent_k")[2:]
            ),
            (Decimal("25"), Decimal("37.5"), Decimal("43.75")),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "percent_d")[2:]
            ),
            (Decimal("12.5"), Decimal("25"), Decimal("34.375")),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "percent_j")[2:]
            ),
            (Decimal("50"), Decimal("62.5"), Decimal("62.5")),
        )
        self.assertEqual(
            result.warnings,
            ("kdj_tradingview_presentation_excluded",),
        )

        restarted = calculate_technical_indicator(
            indicator="kdj",
            close=_values("5", "5", "6"),
            high=_values("5", "5", "8"),
            low=_values("5", "5", "4"),
            window=2,
            signal_window=2,
        )
        self.assertEqual(
            _component_values(restarted, "percent_k")[1].missing_reason,
            "zero_price_range",
        )
        self.assertEqual(
            (
                _component_values(restarted, "percent_k")[2].value,
                _component_values(restarted, "percent_d")[2].value,
                _component_values(restarted, "percent_j")[2].value,
            ),
            (Decimal("25"), Decimal("12.5"), Decimal("50")),
        )


    def test_williams_vix_fix_matches_pine_threshold_vector(self) -> None:
        result = calculate_technical_indicator(
            indicator="williams_vix_fix",
            close=_values("100", "100", "100", "100", "100", "100"),
            low=_values("100", "90", "80", "70", "60", "50"),
            window=2,
            signal_window=2,
            standard_deviation_multiplier=Decimal("2"),
            percentile_window=3,
            percentile_high_factor=Decimal("0.85"),
            percentile_low_factor=Decimal("1.01"),
        )
        self.assertEqual(
            tuple(component.name for component in result.components),
            (
                "williams_vix_fix",
                "upper_band",
                "range_high",
                "range_low",
            ),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(
                    result, "williams_vix_fix"
                )
            ),
            (None, Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "upper_band")
            ),
            (None, None, Decimal("25"), Decimal("35"), Decimal("45"), Decimal("55")),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "range_high")
            ),
            (None, None, None, Decimal("25.5"), Decimal("34"), Decimal("42.5")),
        )
        self.assertEqual(
            tuple(
                point.value
                for point in _component_values(result, "range_low")
            ),
            (None, None, None, Decimal("10.1"), Decimal("20.2"), Decimal("30.3")),
        )
        self.assertEqual(
            result.warnings,
            ("williams_vix_fix_tradingview_presentation_excluded",),
        )

        zero_denominator = calculate_technical_indicator(
            indicator="williams_vix_fix",
            close=_values("0", "0"),
            low=_values("0", "0"),
            window=2,
            signal_window=1,
            standard_deviation_multiplier=Decimal("2"),
            percentile_window=1,
            percentile_high_factor=Decimal("0.85"),
            percentile_low_factor=Decimal("1.01"),
        )
        self.assertEqual(
            _component_values(
                zero_denominator, "williams_vix_fix"
            )[1].missing_reason,
            "zero_highest_close",
        )

    def test_wavetrend_crosses_matches_pine_vector_and_flat_channel(
        self,
    ) -> None:
        average_prices = _values(
            "1", "2", "3", "4", "5", "6", "7", "6", "5", "4", "3",
            "4", "5", "6", "7", "6", "5", "4", "3", "4", "5",
        )
        result = calculate_technical_indicator(
            indicator="wavetrend_crosses",
            close=average_prices,
            high=tuple(
                None if value is None else value + Decimal("1")
                for value in average_prices
            ),
            low=tuple(
                None if value is None else value - Decimal("1")
                for value in average_prices
            ),
            window=2,
            signal_window=2,
        )
        self.assertEqual(
            tuple(component.name for component in result.components),
            (
                "wavetrend",
                "wavetrend_signal",
                "wavetrend_difference",
                "wavetrend_cross_signal",
            ),
        )
        wavetrend = _component_values(result, "wavetrend")
        self.assertEqual(
            tuple(point.missing_reason for point in wavetrend[:3]),
            ("insufficient_history",) * 3,
        )
        self.assertEqual(
            wavetrend[3].value,
            Decimal("66.66666666666666666666666666666665"),
        )
        cross_signal = _component_values(result, "wavetrend_cross_signal")
        self.assertEqual(
            cross_signal[6].missing_reason,
            "insufficient_cross_history",
        )
        self.assertEqual(
            tuple(
                (index, point.value)
                for index, point in enumerate(cross_signal)
                if point.value not in {None, Decimal("0")}
            ),
            ((11, Decimal("1")), (15, Decimal("-1")), (19, Decimal("1"))),
        )
        self.assertEqual(
            result.warnings,
            ("wavetrend_crosses_tradingview_presentation_excluded",),
        )

        flat = _values(*("5",) * 8)
        flat_result = calculate_technical_indicator(
            indicator="wavetrend_crosses",
            close=flat,
            high=flat,
            low=flat,
            window=2,
            signal_window=2,
        )
        self.assertEqual(
            _component_values(flat_result, "wavetrend")[2].missing_reason,
            "zero_channel_deviation",
        )

    def test_parabolic_sar_matches_pine_initialization_and_reversals(self) -> None:
        close = _values("9", "10", "11", "12", "10", "8", "7", "12")
        high = _values("10", "11", "12", "13", "12", "10", "9", "13")
        low = _values("8", "9", "10", "11", "9", "7", "6", "11")
        result = calculate_technical_indicator(
            indicator="parabolic_sar",
            close=close,
            high=high,
            low=low,
            start=Decimal("0.02"),
            increment=Decimal("0.02"),
            maximum=Decimal("0.2"),
        )
        self.assertEqual(
            tuple(component.name for component in result.components),
            ("parabolic_sar",),
        )
        points = _component_values(result, "parabolic_sar")
        self.assertEqual(points[0].missing_reason, "insufficient_sar_history")
        self.assertEqual(
            tuple(point.value for point in points),
            (
                None,
                Decimal("8"),
                Decimal("8"),
                Decimal("8.16"),
                Decimal("8.4504"),
                Decimal("13"),
                Decimal("12.88"),
                Decimal("6"),
            ),
        )
        self.assertEqual(
            result.parameters,
            (
                ("increment", Decimal("0.02")),
                ("maximum", Decimal("0.2")),
                ("start", Decimal("0.02")),
            ),
        )
        self.assertEqual(
            result.warnings,
            ("parabolic_sar_tradingview_presentation_excluded",),
        )

        capped = calculate_technical_indicator(
            indicator="parabolic_sar",
            close=close,
            high=high,
            low=low,
            start=Decimal("0.02"),
            increment=Decimal("0.02"),
            maximum=Decimal("0.04"),
        )
        self.assertEqual(
            _component_values(capped, "parabolic_sar")[4].value,
            Decimal("8.3536"),
        )

        restarted = calculate_technical_indicator(
            indicator="parabolic_sar",
            close=_values("9", "10", None, "7", "8"),
            high=_values("10", "11", None, "8", "9"),
            low=_values("8", "9", None, "6", "7"),
            start=Decimal("0.02"),
            increment=Decimal("0.02"),
            maximum=Decimal("0.2"),
        )
        restarted_points = _component_values(restarted, "parabolic_sar")
        self.assertEqual(
            tuple(point.missing_reason for point in restarted_points),
            (
                "insufficient_sar_history",
                None,
                "indicator_input_missing",
                "insufficient_sar_history",
                None,
            ),
        )
        self.assertEqual(restarted_points[4].value, Decimal("6"))


    def test_swing_structure_forecast_matches_frozen_triangular_vector(self) -> None:
        close, high, low = _swing_ohlc(_SWING_TRIANGLE * 3)
        result = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=close,
            high=high,
            low=low,
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )

        self.assertEqual(
            tuple(component.name for component in result.components),
            (
                "confirmed_swing_high",
                "confirmed_swing_low",
                "swing_direction",
                "forecast_origin",
                "forecast_target",
                "forecast_percent",
                "forecast_duration_bars",
                "forecast_standard_deviation",
                "forecast_band_half",
                "forecast_origin_age_bars",
            ),
        )
        highs = _component_values(result, "confirmed_swing_high")
        lows = _component_values(result, "confirmed_swing_low")
        direction = _component_values(result, "swing_direction")
        percent = _component_values(result, "forecast_percent")
        target = _component_values(result, "forecast_target")
        duration = _component_values(result, "forecast_duration_bars")
        standard_deviation = _component_values(
            result, "forecast_standard_deviation"
        )
        band_half = _component_values(result, "forecast_band_half")
        origin_age = _component_values(result, "forecast_origin_age_bars")

        self.assertEqual(
            tuple(point.missing_reason for point in direction[:9]),
            ("insufficient_history",) * 9,
        )
        self.assertEqual(highs[10].value, Decimal("110"))
        self.assertEqual(lows[20].value, Decimal("98"))
        self.assertEqual(
            (direction[9].value, direction[14].value, direction[24].value),
            (Decimal("1"), Decimal("-1"), Decimal("1")),
        )
        self.assertEqual(percent[43].missing_reason, "swing_percent_history_missing")
        self.assertDecimalClose(percent[44].value, "11.35435992578849721706864564007421")
        self.assertDecimalClose(target[44].value, "109.1272727272727272727272727272727")
        self.assertEqual(duration[44].value, Decimal("10"))
        self.assertDecimalClose(
            standard_deviation[44].value,
            "0.6297054823182612462196758697779744",
        )
        self.assertEqual(band_half[44].missing_reason, "insufficient_atr_history")
        self.assertEqual(origin_age[44].value, Decimal("5"))
        self.assertEqual(
            result.warnings,
            (
                "swing_structure_forecast_forward_bar_visuals_excluded",
                "swing_structure_forecast_is_causal_unrolled_latest_bar_adaptation",
                "swing_structure_forecast_support_resistance_state_excluded",
                "swing_structure_forecast_tradingview_presentation_excluded",
            ),
        )

        tied_close = _values(*("5",) * 10)
        tied = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=tied_close,
            high=_values(*("10",) * 10),
            low=_values(*("0",) * 10),
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )
        self.assertEqual(
            _component_values(tied, "swing_direction")[-1].value,
            Decimal("-1"),
        )

    def test_swing_structure_forecast_aggregation_and_atr_warmup_are_exact(self) -> None:
        close, high, low = _swing_ohlc(_SWING_TRIANGLE * 7)
        results = {
            method: calculate_technical_indicator(
                indicator="swing_structure_forecast",
                close=close,
                high=high,
                low=low,
                window=10,
                sample_count=4,
                aggregation_method=method,
            )
            for method in ("weighted", "average", "median")
        }
        with localcontext() as context:
            context.prec = 34
            rising_percent = Decimal("120") / Decimal("11")
            falling_percent = Decimal("600") / Decimal("49")
            expected_weighted = (
                Decimal("6") * rising_percent
                + Decimal("4") * falling_percent
            ) / Decimal("10")
            expected_even_median = (rising_percent + falling_percent) / Decimal("2")
        forecast_percent = {
            method: _component_values(result, "forecast_percent")[124].value
            for method, result in results.items()
        }
        self.assertDecimalClose(forecast_percent["weighted"], str(expected_weighted))
        self.assertDecimalClose(forecast_percent["average"], str(expected_even_median))
        self.assertDecimalClose(forecast_percent["median"], str(expected_even_median))
        self.assertNotEqual(forecast_percent["weighted"], forecast_percent["average"])
        self.assertNotEqual(forecast_percent["median"], rising_percent)
        self.assertNotEqual(forecast_percent["median"], falling_percent)

        wide_close = tuple(
            value + Decimal("9900") for value in _SWING_TRIANGLE
        )
        atr_close, atr_high, atr_low = _swing_ohlc(
            wide_close * 11,
            high_offset=Decimal("100"),
            low_offset=Decimal("100"),
        )
        atr_result = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=atr_close,
            high=atr_high,
            low=atr_low,
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )
        atr_band = _component_values(atr_result, "forecast_band_half")
        atr_origin = _component_values(atr_result, "forecast_origin")
        atr_standard_deviation = _component_values(
            atr_result, "forecast_standard_deviation"
        )
        self.assertEqual(atr_band[198].missing_reason, "insufficient_atr_history")
        self.assertEqual(atr_band[199].value, Decimal("20.0"))
        assert atr_origin[199].value is not None
        assert atr_standard_deviation[199].value is not None
        self.assertLess(
            atr_origin[199].value
            * atr_standard_deviation[199].value
            / Decimal("100"),
            Decimal("20"),
        )

    def test_swing_structure_forecast_resets_on_missing_input_and_is_prefix_invariant(self) -> None:
        full_close, full_high, full_low = _swing_ohlc(_SWING_TRIANGLE * 7)
        full = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=full_close,
            high=full_high,
            low=full_low,
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )
        prefix_close, prefix_high, prefix_low = _swing_ohlc(_SWING_TRIANGLE * 5)
        prefix = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=prefix_close,
            high=prefix_high,
            low=prefix_low,
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )
        for full_component, prefix_component in zip(
            full.components, prefix.components, strict=True
        ):
            self.assertEqual(
                full_component.points[: len(prefix_close)], prefix_component.points
            )

        gap_close = _SWING_TRIANGLE * 3 + (None,) + _SWING_TRIANGLE
        close, high, low = _swing_ohlc(gap_close)
        reset = calculate_technical_indicator(
            indicator="swing_structure_forecast",
            close=close,
            high=high,
            low=low,
            window=10,
            sample_count=3,
            aggregation_method="weighted",
        )
        for component in reset.components:
            self.assertEqual(component.points[60].missing_reason, "indicator_input_missing")
        reset_direction = _component_values(reset, "swing_direction")
        reset_percent = _component_values(reset, "forecast_percent")
        self.assertEqual(reset_direction[69].missing_reason, "insufficient_history")
        self.assertEqual(reset_direction[70].value, Decimal("1"))
        self.assertEqual(
            reset_percent[70].missing_reason, "insufficient_completed_swings"
        )

    def test_swing_structure_forecast_parameters_and_ohlc_are_strict(self) -> None:
        close, high, low = _swing_ohlc(_SWING_TRIANGLE)
        valid = {
            "indicator": "swing_structure_forecast",
            "close": close,
            "high": high,
            "low": low,
            "window": 10,
            "sample_count": 3,
            "aggregation_method": "weighted",
        }
        invalid = (
            {**valid, "window": 9},
            {**valid, "window": 5_001},
            {**valid, "sample_count": 2},
            {**valid, "sample_count": 21},
            {**valid, "aggregation_method": "mode"},
            {**valid, "minimum_factor": Decimal("1")},
            {key: value for key, value in valid.items() if key != "high"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValidationError):
                    calculate_technical_indicator(**arguments)

    def test_invalid_parameters_inputs_and_ohlc_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(indicator="rolling_regression_line", close=_values("1", "2"), window=1)
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
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(
                indicator="supertrend_ai",
                close=_values("1"),
                high=_values("2"),
                low=_values("0"),
                window=1,
            )
        with self.assertRaises(ValidationError):
            calculate_technical_indicator(
                indicator="supertrend_ai",
                close=_values("1"),
                high=_values("2"),
                low=_values("0"),
                window=1,
                minimum_factor=Decimal("3"),
                maximum_factor=Decimal("1"),
                factor_step=Decimal("1"),
                performance_memory=Decimal("2"),
                cluster="best",
            )
        with self.assertRaises(ResourceLimitError):
            calculate_technical_indicator(
                indicator="supertrend_ai",
                close=_values("1"),
                high=_values("2"),
                low=_values("0"),
                window=1,
                minimum_factor=Decimal("0"),
                maximum_factor=Decimal("100"),
                factor_step=Decimal("0.5"),
                performance_memory=Decimal("2"),
                cluster="best",
            )
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
