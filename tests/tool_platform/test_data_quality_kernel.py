from __future__ import annotations

import dataclasses
from dataclasses import replace
from decimal import Decimal
import unittest

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.tool_platform.data_quality import audit_time_series_quality
from quant_data.tool_platform.market_statistics import (
    stage10_market_return_example_series,
)


def _rebuild(
    source: TimeSeries,
    *,
    observations: tuple[Observation, ...] | None = None,
    series_id: str | None = None,
    truncated: bool | None = None,
    audit_updates: dict[str, object] | None = None,
) -> TimeSeries:
    payload = source.to_primitive()
    audit = payload["audit"]
    assert isinstance(audit, dict)
    if audit_updates:
        audit.update(audit_updates)
    return TimeSeries(
        series_id=series_id or source.series_id,
        metadata=payload["metadata"],
        observations=observations if observations is not None else source.observations,
        warnings=tuple(payload["warnings"]),
        audit=audit,
        provenance=payload["provenance"],
        truncated=source.truncated if truncated is None else truncated,
    )


def _observation(
    source: Observation,
    *,
    day: str,
    ordinal: int,
    value: Decimal | None,
    missing_reason: str | None,
    available_at: str | None = "2026-01-10T00:00:00Z",
    available_precision: str = "datetime",
) -> Observation:
    return replace(
        source,
        period_start=day,
        period_end=day,
        value=value,
        missing_reason=missing_reason,
        available_at=available_at,
        available_precision=available_precision,
        version_id=f"version:{ordinal}",
        evidence_id=f"evidence:{ordinal}",
        snapshot_id=f"snapshot:{ordinal}",
        run_id=f"run:{ordinal}",
        quality_flags=(f"quality:{ordinal % 2}",),
    )


class DataQualityKernelTests(unittest.TestCase):
    def test_reports_duplicate_periods_and_out_of_order_rows_without_rejection(self) -> None:
        base = stage10_market_return_example_series("AAPL")
        latest = base.observations[1]
        earliest = base.observations[0]
        series = _rebuild(
            base,
            observations=(latest, earliest, replace(earliest)),
        )

        summary = audit_time_series_quality((series,))[0]

        self.assertEqual(summary.observation_count, 3)
        self.assertEqual(summary.first_period, ("2026-08-10", "2026-08-10"))
        self.assertEqual(summary.last_period, ("2026-08-11", "2026-08-11"))
        self.assertEqual(summary.duplicate_period_count, 1)
        self.assertEqual(summary.duplicate_version_id_count, 1)
        self.assertEqual(summary.duplicate_evidence_id_count, 1)
        self.assertEqual(summary.duplicate_snapshot_id_count, 1)
        self.assertEqual(summary.duplicate_run_id_count, 1)
        self.assertEqual(summary.out_of_order_count, 2)
        self.assertEqual(summary.distinct_version_id_count, 2)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            summary.observation_count = 1  # type: ignore[misc]

    def test_distinguishes_explicit_missing_observations_from_absent_calendar_rows(self) -> None:
        base = stage10_market_return_example_series("GAPS")
        observations = (
            _observation(
                base.observations[0],
                day="2026-01-01",
                ordinal=0,
                value=None,
                missing_reason="source_missing",
            ),
            _observation(
                base.observations[1],
                day="2026-01-04",
                ordinal=1,
                value=Decimal("2"),
                missing_reason=None,
            ),
        )
        series = _rebuild(
            base,
            observations=observations,
            audit_updates={
                "requested_start_date": "2026-01-01",
                "requested_end_date": "2026-01-04",
            },
        )

        summary = audit_time_series_quality((series,))[0]
        primitive = summary.to_primitive()

        self.assertEqual(summary.explicit_missing_count, 1)
        self.assertEqual(summary.missing_reasons, (("source_missing", 1),))
        self.assertEqual(summary.calendar_discontinuity_count, 1)
        self.assertEqual(summary.calendar_discontinuity_span_days, 2)
        self.assertEqual(
            summary.calendar_discontinuities[0].to_primitive(),
            {
                "previous_period_end": "2026-01-01",
                "next_period_start": "2026-01-04",
                "calendar_span_start": "2026-01-02",
                "calendar_span_end": "2026-01-03",
                "calendar_day_count": 2,
            },
        )
        self.assertEqual(summary.coverage_status, "incomplete_or_not_established")
        self.assertIn(
            "trading_session_coverage_not_established",
            summary.coverage_notes,
        )
        missingness = primitive["missingness"]
        self.assertEqual(missingness["explicit_missing_count"], 1)
        self.assertIsNone(missingness["absent_row_count"])
        self.assertEqual(
            missingness["absent_row_count_status"],
            "not_inferable_from_one_series",
        )
        self.assertEqual(
            primitive["calendar_discontinuities"]["basis"],
            "calendar_days_not_trading_sessions",
        )

    def test_retains_truncation_and_requested_source_range(self) -> None:
        base = stage10_market_return_example_series("TRUNC")
        series = _rebuild(
            base,
            truncated=True,
            audit_updates={"truncated": True},
        )

        summary = audit_time_series_quality((series,))[0]

        self.assertTrue(summary.truncated)
        self.assertEqual(summary.requested_source_range, ("2026-08-10", "2026-08-12"))
        self.assertEqual(summary.coverage_status, "truncated")
        self.assertIn("input_truncated", summary.coverage_notes)
        self.assertIn("requested_source_range_declared", summary.coverage_notes)

    def test_classifies_availability_and_preserves_temporal_metadata(self) -> None:
        base = stage10_market_return_example_series("AVAIL")
        observations = (
            _observation(
                base.observations[0],
                day="2026-01-01",
                ordinal=0,
                value=None,
                missing_reason="source_missing",
                available_at="2026-01-01",
                available_precision="date",
            ),
            _observation(
                base.observations[1],
                day="2026-01-02",
                ordinal=1,
                value=Decimal("1"),
                missing_reason=None,
                available_at="2026-01-02T00:00:00Z",
                available_precision="datetime",
            ),
            _observation(
                base.observations[1],
                day="2026-01-03",
                ordinal=2,
                value=Decimal("2"),
                missing_reason=None,
                available_at=None,
                available_precision="unknown",
            ),
            _observation(
                base.observations[1],
                day="2026-01-04",
                ordinal=3,
                value=Decimal("3"),
                missing_reason=None,
                available_at="not-a-time",
                available_precision="datetime",
            ),
            _observation(
                base.observations[1],
                day="2026-01-05",
                ordinal=4,
                value=Decimal("4"),
                missing_reason=None,
                available_at=None,
                available_precision="datetime",
            ),
        )
        series = _rebuild(
            base,
            observations=observations,
            audit_updates={
                "cutoff": "2026-01-06",
                "cutoff_precision": "date",
                "date_only_policy": "calendar_date_inclusive",
                "point_in_time_status": "safe",
                "point_in_time_scope": "retained_local_captures",
                "unsafe_reasons": ["date_only_policy_recorded"],
            },
        )

        summary = audit_time_series_quality((series,))[0]

        self.assertEqual(summary.availability_counts.date, 1)
        self.assertEqual(summary.availability_counts.datetime, 1)
        self.assertEqual(summary.availability_counts.unknown, 1)
        self.assertEqual(summary.availability_counts.invalid, 1)
        self.assertEqual(summary.availability_counts.missing, 1)
        self.assertEqual(summary.availability_basis, "local_capture")
        self.assertEqual(summary.cutoff, "2026-01-06")
        self.assertEqual(summary.cutoff_precision, "date")
        self.assertEqual(summary.date_only_policy, "calendar_date_inclusive")
        self.assertEqual(summary.point_in_time_status, "safe")
        self.assertEqual(summary.point_in_time_scope, "retained_local_captures")
        self.assertEqual(summary.unsafe_reasons, ("date_only_policy_recorded",))

    def test_accepts_offset_aware_capture_timestamps(self) -> None:
        base = stage10_market_return_example_series("CAPTURE_TIME")
        for captured_at in (
            "2026-08-10T00:00:00Z",
            "2026-08-10T00:00:00+00:00",
            "2026-08-10T00:00:00-04:00",
        ):
            with self.subTest(captured_at=captured_at):
                series = _rebuild(
                    base,
                    observations=(
                        replace(base.observations[0], captured_at=captured_at),
                        *base.observations[1:],
                    ),
                )
                self.assertEqual(
                    audit_time_series_quality((series,))[0].observation_count,
                    len(base.observations),
                )

        invalid = _rebuild(
            base,
            observations=(
                replace(
                    base.observations[0],
                    captured_at="2026-08-10T00:00:00",
                ),
                *base.observations[1:],
            ),
        )
        with self.assertRaisesRegex(ValidationError, "capture timing"):
            audit_time_series_quality((invalid,))

    def test_rejects_tampered_lineage_before_reporting(self) -> None:
        series = stage10_market_return_example_series("TAMPER")
        object.__setattr__(series, "lineage_digest", "0" * 64)

        with self.assertRaises(ValidationError):
            audit_time_series_quality((series,))

    def test_orders_summaries_and_bounds_calendar_list_deterministically(self) -> None:
        alpha = stage10_market_return_example_series("ALPHA")
        zeta = stage10_market_return_example_series("ZETA")
        gap_observations = (
            _observation(
                alpha.observations[0],
                day="2026-01-01",
                ordinal=0,
                value=None,
                missing_reason="source_missing",
            ),
            _observation(
                alpha.observations[1],
                day="2026-01-04",
                ordinal=1,
                value=Decimal("1"),
                missing_reason=None,
            ),
            _observation(
                alpha.observations[2],
                day="2026-01-07",
                ordinal=2,
                value=Decimal("2"),
                missing_reason=None,
            ),
        )
        alpha = _rebuild(alpha, observations=gap_observations)

        first = audit_time_series_quality((zeta, alpha), discontinuity_limit=1)
        second = audit_time_series_quality((alpha, zeta), discontinuity_limit=1)

        self.assertEqual(
            [item.series_id for item in first],
            ["example:stage10_return:alpha", "example:stage10_return:zeta"],
        )
        self.assertEqual(
            dumps_strict([item.to_primitive() for item in first]),
            dumps_strict([item.to_primitive() for item in second]),
        )
        summary = first[0]
        self.assertEqual(summary.calendar_discontinuity_count, 2)
        self.assertEqual(summary.calendar_discontinuity_span_days, 4)
        self.assertEqual(len(summary.calendar_discontinuities), 1)
        self.assertTrue(summary.calendar_discontinuities_truncated)
        self.assertEqual(
            summary.calendar_discontinuities[0].calendar_span_start,
            "2026-01-02",
        )
        self.assertEqual(
            summary.to_primitive()["calendar_discontinuities"]["items_truncated"],
            True,
        )


if __name__ == "__main__":
    unittest.main()
