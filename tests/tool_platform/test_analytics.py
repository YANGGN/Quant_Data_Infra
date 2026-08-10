"""Focused offline tests for pure Stage 5 analytics primitives."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
import unittest

from quant_data.errors import ValidationError
from quant_data.tool_platform.analytics import (
    ANALYTIC_PRIMITIVES,
    AnalyticsObservation,
    AnalyticsSeries,
    align_series,
    analytic_primitive,
    correlate_series,
    describe_series,
    event_study,
    forward_return_series,
    ordinary_least_squares,
    resample_series,
    return_series,
    rolling_regression,
    stationarity_diagnostics,
    walk_forward_summary,
)


def _series(values: tuple[Decimal | None, ...], *, series_id: str = "fixture.series", unit: str = "points") -> AnalyticsSeries:
    observations = []
    for index, value in enumerate(values, start=1):
        day = f"2026-01-{index:02d}"
        observations.append(
            AnalyticsObservation(
                day,
                day,
                value,
                None if value is not None else "source_missing",
                (f"version-{index}", f"evidence-{index}", f"snapshot-{index}"),
            )
        )
    return AnalyticsSeries(
        series_id,
        unit,
        "daily",
        tuple(observations),
        (f"lineage-{series_id}",),
    )


def _daily_calendar_series(
    start: date,
    end: date,
    *,
    omitted: tuple[date, ...] = (),
    missing: tuple[date, ...] = (),
    series_id: str = "calendar.daily",
) -> AnalyticsSeries:
    omitted_dates = set(omitted)
    missing_dates = set(missing)
    observations = []
    current = start
    index = 1
    while current <= end:
        if current not in omitted_dates:
            value = None if current in missing_dates else Decimal("2")
            observations.append(
                AnalyticsObservation(
                    current.isoformat(),
                    current.isoformat(),
                    value,
                    None if value is not None else "source_missing",
                    (f"version-{index}", f"evidence-{index}", f"snapshot-{index}"),
                )
            )
            index += 1
        current = date.fromordinal(current.toordinal() + 1)
    return AnalyticsSeries(
        series_id,
        "points",
        "daily",
        tuple(observations),
        (f"lineage-{series_id}",),
    )


def _period_series(
    frequency: str,
    periods: tuple[tuple[str, str, Decimal | None], ...],
    *,
    series_id: str,
) -> AnalyticsSeries:
    observations = tuple(
        AnalyticsObservation(
            start,
            end,
            value,
            None if value is not None else "source_missing",
            (f"version-{index}", f"evidence-{index}", f"snapshot-{index}"),
        )
        for index, (start, end, value) in enumerate(periods, start=1)
    )
    return AnalyticsSeries(
        series_id,
        "points",
        frequency,
        observations,
        (f"lineage-{series_id}",),
    )


class AnalyticsPrimitiveTests(unittest.TestCase):
    def test_observation_contract_is_frozen_and_requires_explicit_missingness(self) -> None:
        observation = AnalyticsObservation("2026-01-01", "2026-01-01", Decimal("1"), None)
        with self.assertRaises(FrozenInstanceError):
            observation.value = Decimal("2")  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            AnalyticsObservation("2026-01-01", "2026-01-01", None, None)
        with self.assertRaises(ValidationError):
            AnalyticsObservation("2026-01-01", "2026-01-01", Decimal("1"), "missing")

    def test_describe_is_decimal_safe_and_does_not_drop_missing(self) -> None:
        result = describe_series(_series((Decimal("1"), None, Decimal("3"))))
        self.assertEqual(result["status"], "established")
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["mean"], Decimal("2"))
        self.assertEqual(result["sample_variance"], Decimal("2"))
        missing_only = describe_series(_series((None,)))
        self.assertEqual(missing_only["status"], "not_established")
        self.assertIn("all_observations_missing", missing_only["warnings"])

    def test_resample_requires_complete_daily_calendar_coverage(self) -> None:
        lone_midmonth = _daily_calendar_series(
            date(2026, 1, 15),
            date(2026, 1, 15),
            series_id="lone.midmonth",
        )
        lone_result = resample_series(lone_midmonth, target_frequency="monthly")
        self.assertIsNone(lone_result.observations[0].value)
        self.assertEqual(
            lone_result.observations[0].missing_reason,
            "resample_incomplete_bucket",
        )
        complete = _daily_calendar_series(
            date(2026, 1, 1),
            date(2026, 1, 31),
            series_id="complete.january",
        )
        complete_result = resample_series(complete, target_frequency="monthly")
        self.assertEqual(complete_result.observations[0].value, Decimal("2"))
        self.assertEqual(complete_result.observations[0].period_start, "2026-01-01")
        missing_day = _daily_calendar_series(
            date(2026, 1, 1),
            date(2026, 1, 31),
            omitted=(date(2026, 1, 16),),
            series_id="missing.january.day",
        )
        incomplete_result = resample_series(missing_day, target_frequency="monthly")
        self.assertIsNone(incomplete_result.observations[0].value)
        self.assertEqual(
            incomplete_result.observations[0].missing_reason,
            "resample_incomplete_bucket",
        )
        with self.assertRaises(ValidationError):
            resample_series(complete_result, target_frequency="daily")

    def test_resample_accepts_exact_weekly_and_monthly_coverage(self) -> None:
        weekly = _period_series(
            "weekly",
            (
                ("2026-01-01", "2026-01-07", Decimal("1")),
                ("2026-01-08", "2026-01-14", Decimal("2")),
                ("2026-01-15", "2026-01-21", Decimal("3")),
                ("2026-01-22", "2026-01-28", Decimal("4")),
                ("2026-01-29", "2026-01-31", Decimal("5")),
            ),
            series_id="complete.weekly.january",
        )
        weekly_result = resample_series(weekly, target_frequency="monthly", method="sum")
        self.assertEqual(weekly_result.observations[0].value, Decimal("15"))
        self.assertEqual(weekly_result.observations[0].period_end, "2026-01-31")
        monthly = _period_series(
            "monthly",
            (
                ("2026-01-01", "2026-01-31", Decimal("1")),
                ("2026-02-01", "2026-02-28", Decimal("2")),
                ("2026-03-01", "2026-03-31", Decimal("3")),
            ),
            series_id="complete.monthly.quarter",
        )
        quarterly_result = resample_series(monthly, target_frequency="quarterly", method="sum")
        self.assertEqual(quarterly_result.observations[0].value, Decimal("6"))
        self.assertEqual(quarterly_result.observations[0].period_end, "2026-03-31")

    def test_align_is_explicit_and_rejects_semantic_mismatch(self) -> None:
        left = _series((Decimal("1"), Decimal("2")), series_id="left")
        right = _series((Decimal("3"),), series_id="right")
        outer = align_series((left, right), join="outer")
        self.assertEqual(len(outer["rows"]), 2)
        self.assertEqual(outer["rows"][1]["missing_reasons"][1], "absent_from_series")
        with self.assertRaises(ValidationError):
            align_series((left, _series((Decimal("1"),), series_id="other", unit="USD")))

    def test_return_targets_are_explicit_about_unavailable_and_missing_inputs(self) -> None:
        source = _series((Decimal("100"), Decimal("110"), None, Decimal("121")))
        trailing = return_series(source)
        self.assertEqual(trailing.observations[0].missing_reason, "insufficient_history")
        self.assertEqual(trailing.observations[1].value, Decimal("0.1"))
        self.assertEqual(trailing.observations[2].missing_reason, "return_input_missing")
        forward = forward_return_series(source)
        self.assertEqual(forward.observations[0].value, Decimal("0.1"))
        self.assertEqual(forward.observations[-1].missing_reason, "insufficient_forward_horizon")

    def test_correlation_and_ols_are_honest_about_insufficient_or_singular_data(self) -> None:
        left = _series((Decimal("1"), Decimal("2"), Decimal("3")), series_id="left")
        right = _series((Decimal("2"), Decimal("4"), Decimal("6")), series_id="right")
        correlation = correlate_series(left, right)
        self.assertEqual(correlation["status"], "established")
        self.assertEqual(correlation["coefficient"], Decimal("1"))
        fitted = ordinary_least_squares(
            (1, 3, 5, 7),
            ((0,), (1,), (2,), (3,)),
        )
        self.assertEqual(fitted["status"], "established")
        self.assertEqual(fitted["coefficients"], (Decimal("1"), Decimal("2")))
        singular = ordinary_least_squares((1, 2, 3), ((1,), (1,), (1,)))
        self.assertEqual(singular["status"], "not_established")
        rolling = rolling_regression((1, 3, 5, 7), ((0,), (1,), (2,), (3,)), window=3)
        self.assertEqual(len(rolling), 2)
        self.assertTrue(all(item["status"] == "established" for item in rolling))

    def test_diagnostics_event_and_walk_forward_do_not_fabricate_results(self) -> None:
        missing = stationarity_diagnostics((Decimal("1"), None, Decimal("3")))
        self.assertEqual(missing["status"], "not_established")
        diagnostics = stationarity_diagnostics((1, 2, 3, 4))
        self.assertEqual(diagnostics["assessment"], "diagnostic_only")
        event = event_study((Decimal("0.1"), Decimal("0.2"), Decimal("-0.1")), event_index=1, pre=1, post=1)
        self.assertEqual(event["status"], "established")
        self.assertEqual(event["cumulative_return"], Decimal("0.188"))
        missing_event = event_study((Decimal("0.1"), None, Decimal("0.2")), event_index=1, pre=1, post=1)
        self.assertEqual(missing_event["status"], "not_established")
        summary = walk_forward_summary((1, 2, None), (2, 2, 3))
        self.assertEqual(summary["status"], "established")
        self.assertEqual(summary["excluded_count"], 1)

    def test_closed_mapping_rejects_unknown_names(self) -> None:
        self.assertIs(analytic_primitive("timeseries.describe"), ANALYTIC_PRIMITIVES["timeseries.describe"])
        with self.assertRaises(TypeError):
            ANALYTIC_PRIMITIVES["unexpected"] = describe_series  # type: ignore[index]
        with self.assertRaises(ValidationError):
            analytic_primitive("arbitrary.import.path")


if __name__ == "__main__":
    unittest.main()
