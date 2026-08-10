"""Focused Stage 5 tests for typed analytical adapter outputs."""
from __future__ import annotations

from decimal import Decimal
import unittest

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ValidationError
from quant_data.tool_platform.analytic_adapter import invoke_analytic


def _source_series(series_id: str = "fixture.adapter.source") -> TimeSeries:
    timing = (
        ("2026-01-01", Decimal("100"), "2026-01-02", "date", "2026-01-02", "date"),
        (
            "2026-01-02",
            Decimal("110"),
            "2026-01-02T12:00:00Z",
            "datetime",
            "2026-01-02T12:00:00Z",
            "datetime",
        ),
        ("2026-01-03", Decimal("121"), "2026-01-04", "date", "2026-01-04", "date"),
    )
    observations = tuple(
        Observation(
            period_start=period,
            period_end=period,
            value=value,
            missing_reason=None,
            unit="points",
            value_representation="level",
            scale="1",
            vintage_at=f"fixture-vintage-{index}",
            available_at=available_at,
            available_precision=available_precision,
            captured_at=captured_at,
            captured_precision=captured_precision,
            version_id=f"fixture-version-{index}",
            evidence_id=f"fixture-evidence-{index}",
            snapshot_id=f"fixture-snapshot-{index}",
            run_id=f"fixture-run-{index}",
            dimensions={},
            quality_flags=("fixture_quality",),
        )
        for index, (
            period,
            value,
            available_at,
            available_precision,
            captured_at,
            captured_precision,
        ) in enumerate(timing, start=1)
    )
    return TimeSeries(
        series_id=series_id,
        metadata={
            "provider": "fixture",
            "frequency": "daily",
            "unit": "points",
            "value_representation": "level",
            "scale": "1",
        },
        observations=observations,
        warnings=("fixture_source",),
        audit={
            "mode": "latest",
            "cutoff": None,
            "cutoff_precision": None,
            "date_only_policy": "completed_date",
            "availability_basis": "source_evidenced",
            "period_range_rule": "period_start",
            "requested_start_date": "2026-01-01",
            "requested_end_date": "2026-01-03",
            "limit": 10_000,
            "selected_count": len(observations),
        },
        provenance={
            "dataset_id": "fixture.analytics.adapter",
            "store_role": "macro",
            "registry_revision": "2.3.0",
            "store_receipt": {
                "migration_ids": ["macro:0001"],
                "sha256": "a" * 64,
            },
        },
    )


def _return_arguments(series: TimeSeries) -> dict[str, object]:
    return {
        "series": series,
        "parameters": ({"name": "operation", "value": "returns"},),
        "limit": 10_000,
    }


class AnalyticAdapterTransformTests(unittest.TestCase):
    def test_parameters_fail_closed_without_host_coercion(self) -> None:
        source = _source_series()
        cases = (
            (
                "timeseries.transform",
                {
                    "series": source,
                    "parameters": (
                        {"name": "operation", "value": "returns"},
                        {"name": "horizon", "value": "1"},
                    ),
                    "limit": 10_000,
                },
            ),
            (
                "econometrics.rolling_regression",
                {
                    "series": (source, _source_series("fixture.adapter.predictor")),
                    "parameters": ({"name": "window", "value": True},),
                    "limit": 10_000,
                },
            ),
            (
                "research.event_study",
                {
                    "series": source,
                    "parameters": ({"name": "event_index", "value": "1"},),
                    "limit": 10_000,
                },
            ),
        )
        for name, arguments in cases:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                invoke_analytic(name, arguments)

    def test_transform_emits_full_series_with_conservative_timing_and_detached_source(self) -> None:
        source = _source_series()
        source_before = source.to_primitive()
        detached_source = source.to_primitive()
        detached_source["metadata"]["provider"] = "tampered-detached-copy"
        self.assertEqual(source.metadata["provider"], "fixture")

        result = invoke_analytic("timeseries.transform", _return_arguments(source))

        self.assertEqual(source.to_primitive(), source_before)
        self.assertEqual(len(result.records), 3)
        self.assertEqual(len(result.series), 1)
        transformed = result.series[0]
        transformed.validate_lineage()
        self.assertEqual(transformed.metadata["unit"], "fraction")
        self.assertEqual(transformed.metadata["frequency"], "daily")
        self.assertEqual(transformed.audit["selected_count"], 3)
        self.assertEqual(
            (transformed.observations[0].period_start, transformed.observations[0].period_end),
            ("2026-01-01", "2026-01-01"),
        )
        self.assertIsNone(transformed.observations[0].value)
        self.assertEqual(
            transformed.observations[0].missing_reason,
            "insufficient_history",
        )
        self.assertEqual(transformed.observations[1].value, Decimal("0.1"))
        self.assertEqual(
            (
                transformed.observations[1].available_at,
                transformed.observations[1].available_precision,
            ),
            ("2026-01-02", "date"),
        )
        self.assertEqual(
            (
                transformed.observations[1].captured_at,
                transformed.observations[1].captured_precision,
            ),
            ("2026-01-02", "date"),
        )
        self.assertTrue(transformed.observations[1].vintage_at.startswith("derived:"))
        self.assertIn(
            f"source_lineage:{source.lineage_digest}",
            transformed.observations[1].quality_flags,
        )
        self.assertIn(
            "transformation:return:trailing:simple:horizon=1",
            transformed.warnings,
        )
        payload = result.to_primitive()["series"][0]
        self.assertEqual(payload["lineage_digest"], transformed.lineage_digest)
        self.assertEqual(payload["observations"][1]["value"], Decimal("0.1"))

    def test_transform_output_is_composable_and_rejects_lineage_tampering(self) -> None:
        first = invoke_analytic("timeseries.transform", _return_arguments(_source_series())).series[0]
        second = invoke_analytic("timeseries.transform", _return_arguments(first)).series[0]

        second.validate_lineage()
        self.assertEqual(second.metadata["unit"], "fraction")
        self.assertEqual(second.metadata["frequency"], "daily")
        self.assertIn(
            f"source_lineage:{first.lineage_digest}",
            second.observations[1].quality_flags,
        )

        object.__setattr__(first, "lineage_digest", "0" * 64)
        with self.assertRaises(ValidationError):
            invoke_analytic("timeseries.transform", _return_arguments(first))

    def test_research_panel_uses_declared_research_envelope(self) -> None:
        result = invoke_analytic(
            "research.point_in_time_panel",
            {
                "series": (_source_series("fixture.adapter.left"), _source_series("fixture.adapter.right")),
                "parameters": (),
                "limit": 10_000,
            },
        )

        self.assertEqual(result.research_contract.status, "declared")
        self.assertIsNotNone(result.research_contract.contract)
        assert result.research_contract.contract is not None
        self.assertEqual(result.research_contract.contract.point_in_time_status, "not_established")


if __name__ == "__main__":
    unittest.main()
