"""Independent frozen-vector and hostile-boundary tests for market transforms."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal
import unittest

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ValidationError
from quant_data.tool_platform.econometrics import CHI_SQUARE_INFERENCE_BACKEND
from quant_data.tool_platform.market_transform import (
    CHI_SQUARE_REFERENCE_DISTRIBUTION,
    autocorrelation,
    drawdown_episodes,
    ljung_box,
    partial_autocorrelation,
    rolling_statistic,
)


def _series(
    values: tuple[Decimal | None, ...], *, series_id: str = "stage10.trailing.fixture"
) -> TimeSeries:
    observations = []
    for index, value in enumerate(values):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        observations.append(
            Observation(
                period_start=day,
                period_end=day,
                value=value,
                missing_reason=None if value is not None else "source_missing",
                unit="fraction",
                value_representation="return",
                scale="1",
                vintage_at="vintage",
                available_at=None,
                available_precision="date",
                captured_at="capture",
                captured_precision="date",
                version_id=f"version-{index}",
                evidence_id=f"evidence-{index}",
                snapshot_id=f"snapshot-{index}",
                run_id=f"run-{index}",
            )
        )
    return TimeSeries(
        series_id=series_id,
        metadata={
            "unit": "fraction",
            "frequency": "daily",
            "return_direction": "trailing",
            "return_method": "simple",
        },
        observations=tuple(observations),
        warnings=(),
        audit={},
        provenance={},
    )


_REFERENCE_VALUES = tuple(
    Decimal(value)
    for value in ("-2", "-1", "0", "1", "3", "2", "4", "3", "5", "4")
)
_REFERENCE_ACF = (
    "1",
    "0.6153374233128834355828220858895706",
    "0.3901840490797546012269938650306748",
    "0.08527607361963190184049079754601227",
)
_REFERENCE_PACF = (
    "1",
    "0.6153374233128834355828220858895706",
    "0.01857845248414341104288532677202786",
    "-0.2604707422106824952445467147667822",
)


class MarketTransformKernelTests(unittest.TestCase):
    def assertDecimalClose(
        self, actual: Decimal | None, expected: str, tolerance: str = "2e-12"
    ) -> None:
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertLessEqual(abs(actual - Decimal(expected)), Decimal(tolerance))

    def test_rolling_statistics_are_anchored_and_never_skip_missing_inputs(self) -> None:
        source = _series(
            (
                Decimal("1"),
                Decimal("2"),
                Decimal("3"),
                Decimal("4"),
                None,
                Decimal("6"),
                Decimal("7"),
                Decimal("8"),
            )
        )
        mean = rolling_statistic(source, statistic="mean", window=3)
        standard_deviation = rolling_statistic(
            source, statistic="sample_standard_deviation", window=3
        )
        minimum = rolling_statistic(source, statistic="minimum", window=3)
        maximum = rolling_statistic(source, statistic="maximum", window=3)

        self.assertEqual(mean.status, "established")
        self.assertIsNone(mean.reason)
        self.assertEqual(mean.warnings, ())
        self.assertEqual(mean.established_count, 3)
        self.assertEqual(mean.not_established_count, 5)
        self.assertEqual(len(mean.points), len(source.observations))
        self.assertEqual(
            tuple(point.missing_reason for point in mean.points[:2]),
            ("insufficient_history", "insufficient_history"),
        )
        self.assertEqual(mean.points[2].window_start_index, 0)
        self.assertEqual(mean.points[2].value, Decimal("2"))
        self.assertEqual(mean.points[3].value, Decimal("3"))
        self.assertEqual(
            tuple(point.missing_reason for point in mean.points[4:7]),
            (
                "rolling_window_input_missing",
                "rolling_window_input_missing",
                "rolling_window_input_missing",
            ),
        )
        self.assertEqual(mean.points[-1].value, Decimal("7"))
        self.assertEqual(standard_deviation.points[2].value, Decimal("1"))
        self.assertEqual(minimum.points[2].value, Decimal("1"))
        self.assertEqual(maximum.points[2].value, Decimal("3"))
        with self.assertRaises(FrozenInstanceError):
            mean.points[2].value = Decimal("9")  # type: ignore[misc]

    def test_rolling_all_warmup_windows_are_not_established(self) -> None:
        result = rolling_statistic(
            _series((Decimal("1"), Decimal("2"))),
            statistic="mean",
            window=3,
        )

        self.assertEqual(result.status, "not_established")
        self.assertEqual(result.reason, "all_windows_insufficient_history")
        self.assertEqual(result.warnings, ("all_windows_insufficient_history",))
        self.assertNotEqual(result.reason, "empty_series")
        self.assertEqual(result.established_count, 0)
        self.assertEqual(result.not_established_count, 2)
        self.assertEqual(
            tuple(point.missing_reason for point in result.points),
            ("insufficient_history", "insufficient_history"),
        )

    def test_rolling_all_incomplete_windows_are_not_established(self) -> None:
        result = rolling_statistic(
            _series((None, None)), statistic="mean", window=1
        )

        self.assertEqual(result.status, "not_established")
        self.assertEqual(
            result.reason, "all_windows_rolling_window_input_missing"
        )
        self.assertEqual(
            result.warnings,
            ("all_windows_rolling_window_input_missing",),
        )
        self.assertNotEqual(result.reason, "empty_series")
        self.assertEqual(result.established_count, 0)
        self.assertEqual(result.not_established_count, 2)
        self.assertEqual(
            tuple(point.missing_reason for point in result.points),
            ("rolling_window_input_missing", "rolling_window_input_missing"),
        )

    def test_acf_pacf_and_ljung_box_match_frozen_independent_reference(self) -> None:
        """Reference values were frozen independently from NumPy and SciPy."""

        source = _series(_REFERENCE_VALUES)
        acf = autocorrelation(source, max_lag=3)
        pacf = partial_autocorrelation(source, max_lag=3)
        ljung = ljung_box(source, lag=2)

        self.assertEqual(acf.status, "established")
        self.assertEqual(acf.sample_size, 10)
        self.assertEqual(acf.centered_sum_squares, Decimal("48.9"))
        for actual, expected in zip(
            (point.value for point in acf.coefficients), _REFERENCE_ACF, strict=True
        ):
            self.assertDecimalClose(actual, expected)
        self.assertEqual(pacf.status, "established")
        for actual, expected in zip(
            (point.value for point in pacf.coefficients),
            _REFERENCE_PACF,
            strict=True,
        ):
            self.assertDecimalClose(actual, expected)
        self.assertDecimalClose(pacf.innovation_variance, "0.5790037740792778")
        self.assertEqual(ljung.status, "established")
        self.assertEqual(ljung.model_degrees_of_freedom, 0)
        self.assertEqual(ljung.degrees_of_freedom, 2)
        self.assertDecimalClose(ljung.statistic, "7.332189142735266")
        self.assertDecimalClose(ljung.p_value, "0.0255761610233446")
        self.assertEqual(
            ljung.reference_distribution, CHI_SQUARE_REFERENCE_DISTRIBUTION
        )
        self.assertEqual(ljung.inference_backend, CHI_SQUARE_INFERENCE_BACKEND)

    def test_terminal_missingness_is_counted_but_an_interior_gap_is_not_bridged(self) -> None:
        terminal = _series(
            (None, None, *_REFERENCE_VALUES, None),
            series_id="stage10.trailing.terminal-missing",
        )
        acf = autocorrelation(terminal, max_lag=2)
        ljung = ljung_box(terminal, lag=2)
        self.assertEqual(acf.status, "established")
        self.assertEqual(acf.trimmed_leading_count, 2)
        self.assertEqual(acf.trimmed_trailing_count, 1)
        self.assertEqual(acf.excluded_count, 3)
        self.assertEqual(acf.first_included_index, 2)
        self.assertEqual(acf.last_included_index, 11)
        self.assertEqual(ljung.status, "established")
        self.assertEqual(ljung.excluded_count, 3)

        interior = _series(
            (None, Decimal("0.01"), None, Decimal("0.02"), None),
            series_id="stage10.trailing.interior-missing",
        )
        for result in (
            autocorrelation(interior, max_lag=1),
            partial_autocorrelation(interior, max_lag=1),
            ljung_box(interior, lag=1),
            drawdown_episodes(interior, method="simple"),
        ):
            self.assertEqual(result.status, "not_established")
            self.assertEqual(result.reason, "interior_missing_observations")
            self.assertEqual(result.interior_missing_count, 1)

    def test_drawdown_episodes_preserve_earliest_high_water_mark_and_unrecovered_tail(self) -> None:
        source = _series(
            (
                Decimal("0.10"),
                Decimal("0"),
                Decimal("-0.10"),
                Decimal("0"),
                Decimal("0.12"),
                Decimal("-0.10"),
                Decimal("0.05"),
                Decimal("-0.20"),
            )
        )
        result = drawdown_episodes(source, method="simple")

        self.assertEqual(result.status, "established")
        self.assertEqual(result.initial_wealth, Decimal("1"))
        self.assertEqual(result.points[1].high_water_mark_index, 0)
        self.assertEqual(len(result.episodes), 2)
        recovered, unrecovered = result.episodes
        self.assertEqual(
            (
                recovered.peak_index,
                recovered.start_index,
                recovered.trough_index,
                recovered.recovery_index,
                recovered.recovered,
            ),
            (0, 2, 2, 4, True),
        )
        self.assertEqual(recovered.maximum_drawdown, Decimal("-0.10"))
        self.assertEqual(
            (
                unrecovered.peak_index,
                unrecovered.start_index,
                unrecovered.trough_index,
                unrecovered.recovery_index,
                unrecovered.recovered,
            ),
            (4, 5, 7, None, False),
        )
        self.assertEqual(unrecovered.maximum_drawdown, Decimal("-0.244000"))

    def test_log_drawdown_uses_log_compounding_and_simple_floor_is_rejected(self) -> None:
        log_returns = _series(
            (
                Decimal("0.095310179804324860043952123280765"),
                Decimal("-0.1053605156578263012275009808393128"),
            )
        )
        result = drawdown_episodes(log_returns, method="log")
        self.assertEqual(result.status, "established")
        self.assertDecimalClose(result.terminal_wealth, "0.99", "2e-30")
        self.assertEqual(result.episodes[0].peak_index, 0)
        self.assertFalse(result.episodes[0].recovered)
        with self.assertRaises(ValidationError):
            drawdown_episodes(_series((Decimal("-1"),)), method="simple")

    def test_hostile_parameter_boundaries_are_rejected_explicitly(self) -> None:
        source = _series((Decimal("1"), Decimal("2"), Decimal("3")))
        with self.assertRaises(ValidationError):
            rolling_statistic(source, statistic="median", window=2)
        with self.assertRaises(ValidationError):
            rolling_statistic(
                source, statistic="sample_standard_deviation", window=1
            )
        with self.assertRaises(ValidationError):
            rolling_statistic(source, statistic="mean", window=True)
        with self.assertRaises(ValidationError):
            autocorrelation(source, max_lag=True)
        with self.assertRaises(ValidationError):
            ljung_box(source, lag=0)
        unavailable = autocorrelation(source, max_lag=3)
        self.assertEqual(unavailable.status, "not_established")
        self.assertEqual(unavailable.reason, "insufficient_sample_for_requested_lag")


if __name__ == "__main__":
    unittest.main()
