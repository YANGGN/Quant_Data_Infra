"""Offline public-boundary coverage for the Step 2--4 analysis foundation."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import ToolDispatcher
from quant_data.contracts import TimeSeries
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from tests.tool_platform.test_market_econometrics import _series


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _fields(record: dict[str, object]) -> dict[str, object]:
    return {
        item["name"]: item["value"]
        for item in record["fields"]  # type: ignore[index]
    }


def _metrics(result: dict[str, object]) -> dict[str, object]:
    return {
        item["name"]: item["value"]
        for item in result["diagnostics"][0]["metrics"]  # type: ignore[index]
    }


def _rebuild(
    base: TimeSeries,
    *,
    observations: tuple[object, ...] | None = None,
    truncated: bool | None = None,
) -> TimeSeries:
    payload = base.to_primitive()
    selected = (
        base.observations
        if observations is None
        else tuple(observations)
    )
    chosen_truncated = base.truncated if truncated is None else truncated
    payload["audit"]["selected_count"] = len(selected)
    payload["audit"]["missing_count"] = sum(
        item.value is None for item in selected
    )
    payload["audit"]["truncated"] = chosen_truncated
    return TimeSeries(
        series_id=payload["series_id"],
        metadata=payload["metadata"],
        observations=selected,
        warnings=tuple(payload["warnings"]),
        audit=payload["audit"],
        provenance=payload["provenance"],
        truncated=chosen_truncated,
    )


def _quality_as_of_series(
    base: TimeSeries,
    *,
    identity_captured_at: str,
    observation_captured_at: str,
) -> TimeSeries:
    """Make a typed retained-capture fixture without touching a store."""

    payload = base.to_primitive()
    payload["audit"].update(
        {
            "mode": "as_of",
            "requested_mode": "as_of",
            "actual_mode": "as_of",
            "cutoff": "2026-07-02T00:00:00Z",
            "cutoff_precision": "datetime",
            "point_in_time_status": "safe",
            "point_in_time_scope": "retained_local_captures",
            "unsafe_reasons": [],
        }
    )
    identity = payload["provenance"]["store_receipt"][
        "selected_instrument_identity"
    ]
    identity["captured_at"] = identity_captured_at
    identity["captured_precision"] = "datetime"
    observations = tuple(
        replace(
            item,
            available_at=observation_captured_at,
            available_precision="datetime",
            captured_at=observation_captured_at,
            captured_precision="datetime",
        )
        for item in base.observations
    )
    return TimeSeries(
        series_id=payload["series_id"],
        metadata=payload["metadata"],
        observations=observations,
        warnings=tuple(payload["warnings"]),
        audit=payload["audit"],
        provenance=payload["provenance"],
        truncated=False,
    )


def _quality_offset_capture_series(base: TimeSeries, captured_at: str) -> TimeSeries:
    payload = base.to_primitive()
    identity = payload["provenance"]["store_receipt"][
        "selected_instrument_identity"
    ]
    identity["captured_at"] = captured_at
    identity["captured_precision"] = "datetime"
    observations = tuple(
        replace(
            item,
            available_at=captured_at,
            available_precision="datetime",
            captured_at=captured_at,
            captured_precision="datetime",
        )
        for item in base.observations
    )
    return TimeSeries(
        series_id=payload["series_id"],
        metadata=payload["metadata"],
        observations=observations,
        warnings=tuple(payload["warnings"]),
        audit=payload["audit"],
        provenance=payload["provenance"],
        truncated=False,
    )


class MarketAnalysisAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        stores = StoreMap(
            market=root / "market.sqlite",
            macro=root / "macro.sqlite",
            company=root / "company.sqlite",
            news=root / "news.sqlite",
        )
        registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )
        self.dispatcher = ToolDispatcher(stores, registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _call(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        version: str | None = None,
    ) -> dict[str, object]:
        return self.dispatcher.call(
            name, arguments, tool_version=version
        )

    def test_quality_reports_duplicates_order_truncation_and_calendar_basis(
        self,
    ) -> None:
        base = _series(
            "QUALITY",
            (
                Decimal("0.01"),
                None,
                Decimal("0.03"),
                Decimal("0.04"),
            ),
        )
        first, second, third, fourth = base.observations
        observations = (
            first,
            replace(
                second,
                period_start=first.period_start,
                period_end=first.period_end,
                version_id=first.version_id,
                evidence_id=first.evidence_id,
            ),
            fourth,
            third,
        )
        series = _rebuild(base, observations=observations, truncated=True)
        result = self._call(
            "data.quality_audit",
            {"series": [series.to_primitive()], "limit": 20},
            version="2.0.0",
        )
        summary = _fields(result["records"][0])  # type: ignore[index]
        metrics = _metrics(result)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["coverage_status"], "truncated")
        self.assertEqual(summary["duplicate_period_count"], 1)
        self.assertEqual(summary["duplicate_version_id_count"], 1)
        self.assertEqual(summary["out_of_order_count"], 1)
        self.assertEqual(metrics["explicit_missing_count"], 1)
        gap_records = [
            item
            for item in result["records"]  # type: ignore[index]
            if item["record_type"] == "stage10_quality_calendar_discontinuity"
        ]
        for record in gap_records:
            self.assertEqual(
                _fields(record)["basis"],
                "calendar_days_not_trading_sessions",
            )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_transform_routes_are_typed_and_interior_gaps_fail_closed(
        self,
    ) -> None:
        values = tuple(
            Decimal(value)
            for value in (
                "0.01", "-0.02", "0.03", "0.01", "-0.01", "0.02",
                "0.04", "-0.03", "0.02", "0.01", "-0.02", "0.03",
            )
        )
        series = _series("TRANSFORM", values).to_primitive()
        cases = (
            (
                "rolling_statistic",
                {
                    "rolling_statistic": "mean",
                    "window": 3,
                    "max_lag": None,
                    "ljung_box_lag": None,
                },
            ),
            (
                "autocorrelation",
                {
                    "rolling_statistic": None,
                    "window": None,
                    "max_lag": 2,
                    "ljung_box_lag": None,
                },
            ),
            (
                "partial_autocorrelation",
                {
                    "rolling_statistic": None,
                    "window": None,
                    "max_lag": 2,
                    "ljung_box_lag": None,
                },
            ),
            (
                "ljung_box",
                {
                    "rolling_statistic": None,
                    "window": None,
                    "max_lag": None,
                    "ljung_box_lag": 2,
                },
            ),
            (
                "drawdown_episodes",
                {
                    "rolling_statistic": None,
                    "window": None,
                    "max_lag": None,
                    "ljung_box_lag": None,
                },
            ),
        )
        for operation, parameters in cases:
            with self.subTest(operation=operation):
                result = self._call(
                    "timeseries.transform",
                    {
                        "series": series,
                        "operation": operation,
                        **parameters,
                        "limit": 20,
                    },
                    version="2.0.0",
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(_metrics(result)["operation"], operation)

        gap = _series(
            "TRANSFORM_GAP",
            (Decimal("0.01"), None, Decimal("0.02"), Decimal("0.03")),
        )
        result = self._call(
            "timeseries.transform",
            {
                "series": gap.to_primitive(),
                "operation": "autocorrelation",
                "rolling_statistic": None,
                "window": None,
                "max_lag": 1,
                "ljung_box_lag": None,
                "limit": 10,
            },
            version="2.0.0",
        )
        self.assertEqual(result["status"], "not_established")
        self.assertEqual(_metrics(result)["interior_missing_count"], 1)

    def test_rolling_window_larger_than_input_is_not_established(self) -> None:
        result = self._call(
            "timeseries.transform",
            {
                "series": _series(
                    "SHORT_ROLLING",
                    (Decimal("0.01"), Decimal("0.02"), Decimal("0.03")),
                ).to_primitive(),
                "operation": "rolling_statistic",
                "rolling_statistic": "mean",
                "window": 4,
                "max_lag": None,
                "ljung_box_lag": None,
                "limit": 10,
            },
            version="2.0.0",
        )

        metrics = _metrics(result)
        self.assertEqual(result["status"], "not_established")
        self.assertEqual(metrics["status"], "not_established")
        self.assertEqual(metrics["established_count"], 0)

    def test_quality_rejects_a_tampered_source_receipt(self) -> None:
        base = _series(
            "TAMPER", (Decimal("0.01"), Decimal("0.02"), Decimal("0.03"))
        )
        payload = base.to_primitive()
        payload["provenance"]["derivation"]["source_selection"][
            "series_id"
        ] = "tampered"
        tampered = TimeSeries(
            series_id=payload["series_id"],
            metadata=payload["metadata"],
            observations=base.observations,
            warnings=tuple(payload["warnings"]),
            audit=payload["audit"],
            provenance=payload["provenance"],
            truncated=False,
        )
        with self.assertRaisesRegex(Exception, "source receipt"):
            self._call(
                "data.quality_audit",
                {"series": [tampered.to_primitive()], "limit": 10},
                version="2.0.0",
            )

    def test_quality_rejects_post_cutoff_retained_evidence_without_store_access(
        self,
    ) -> None:
        base = _series(
            "QUALITY_AS_OF",
            (Decimal("0.01"), Decimal("0.02"), Decimal("0.03")),
        )
        cases = (
            (
                "identity",
                _quality_as_of_series(
                    base,
                    identity_captured_at="2026-07-03T00:00:00Z",
                    observation_captured_at="2026-07-01T00:00:00Z",
                ),
                "identity is unavailable at the cutoff",
            ),
            (
                "observation",
                _quality_as_of_series(
                    base,
                    identity_captured_at="2026-07-01T00:00:00Z",
                    observation_captured_at="2026-07-03T00:00:00Z",
                ),
                "observation is unavailable at the cutoff",
            ),
        )
        for subject, series, message in cases:
            with self.subTest(subject=subject):
                with patch("sqlite3.connect", side_effect=AssertionError("store open")):
                    with self.assertRaisesRegex(Exception, message):
                        self._call(
                            "data.quality_audit",
                            {"series": [series.to_primitive()], "limit": 10},
                            version="2.0.0",
                        )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_quality_accepts_offset_capture_timestamps_without_store_access(
        self,
    ) -> None:
        series = _quality_offset_capture_series(
            _series(
                "QUALITY_OFFSET",
                (Decimal("0.01"), Decimal("0.02"), Decimal("0.03")),
            ),
            "2026-07-01T00:00:00+05:30",
        )
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            result = self._call(
                "data.quality_audit",
                {"series": [series.to_primitive()], "limit": 10},
                version="2.0.0",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_distribution_and_seeded_bootstrap_are_deterministic(
        self,
    ) -> None:
        series = _series(
            "SCALAR",
            (
                None,
                Decimal("0.01"),
                Decimal("0.02"),
                Decimal("0.03"),
                Decimal("0.05"),
                None,
            ),
        ).to_primitive()
        distribution = self._call(
            "stats.distribution_diagnostics",
            {"series": series, "limit": 10},
        )
        self.assertEqual(distribution["status"], "ok")
        self.assertEqual(_metrics(distribution)["trimmed_leading_count"], 1)
        self.assertEqual(_metrics(distribution)["trimmed_trailing_count"], 1)

        arguments = {
            "series": series,
            "statistic": "mean",
            "seed": 1729,
            "replicates": 250,
            "confidence_level": "0.95",
            "limit": 10,
        }
        first = self._call("stats.bootstrap_confidence_interval", arguments)
        second = self._call("stats.bootstrap_confidence_interval", arguments)
        self.assertEqual(first, second)
        fields = _fields(first["records"][0])  # type: ignore[index]
        self.assertEqual(fields["seed"], 1729)
        self.assertEqual(fields["prng"], "splitmix64_v1")
        self.assertEqual(fields["quantile_method"], "hyndman_fan_type_7")

        gap = _series(
            "SCALAR_GAP",
            (Decimal("0.01"), None, Decimal("0.03")),
        )
        failed = self._call(
            "stats.distribution_diagnostics",
            {"series": gap.to_primitive(), "limit": 10},
        )
        self.assertEqual(failed["status"], "not_established")
        self.assertEqual(_fields(failed["records"][0])["reason"],
                         "interior_missing_observations")  # type: ignore[index]

    def test_covariance_and_pca_share_one_joint_complete_sample(self) -> None:
        left = _series(
            "LEFT",
            (
                Decimal("0.01"),
                Decimal("0.02"),
                None,
                Decimal("0.04"),
                Decimal("0.06"),
            ),
        )
        right = _series(
            "RIGHT",
            (
                Decimal("0.03"),
                Decimal("0.01"),
                Decimal("0.02"),
                Decimal("0.05"),
                Decimal("0.08"),
            ),
        )
        supplied = [left.to_primitive(), right.to_primitive()]
        covariance = self._call(
            "stats.covariance_matrix",
            {"series": supplied, "limit": 10},
        )
        self.assertEqual(covariance["status"], "ok")
        self.assertEqual(
            {item["name"] for item in covariance["matrices"]},  # type: ignore[index]
            {"sample_covariance", "sample_correlation"},
        )
        self.assertEqual(_metrics(covariance)["joint_complete_sample_size"], 4)
        self.assertEqual(_metrics(covariance)["joint_incomplete_row_count"], 1)
        self.assertEqual(_metrics(covariance)["explicit_missing_cell_count"], 1)

        pca = self._call(
            "stats.principal_components",
            {
                "series": supplied,
                "basis": "correlation",
                "components": 2,
                "include_scores": True,
                "limit": 10,
            },
        )
        self.assertEqual(pca["status"], "ok")
        matrices = {
            item["name"]: item for item in pca["matrices"]  # type: ignore[index]
        }
        self.assertIn("pca_eigenvectors", matrices)
        self.assertIn("pca_scores", matrices)
        for row in matrices["pca_eigenvectors"]["values"]:
            pivot = max(
                range(len(row)), key=lambda index: (abs(row[index]), -index)
            )
            self.assertGreaterEqual(row[pivot], Decimal("0"))
        eigenvalues = [
            _fields(item)["eigenvalue"]
            for item in pca["records"]  # type: ignore[index]
            if item["record_type"] == "stage10_pca_component"
        ]
        self.assertEqual(eigenvalues, sorted(eigenvalues, reverse=True))
        metrics = _metrics(pca)
        self.assertEqual(
            metrics["eigenvector_sign_rule"],
            "largest_absolute_loading_positive_ties_lowest_feature_index",
        )

    def test_non_quality_tools_reject_truncated_input(self) -> None:
        base = _series(
            "TRUNCATED", (Decimal("0.01"), Decimal("0.02"), Decimal("0.03"))
        )
        truncated = _rebuild(base, truncated=True)
        with self.assertRaisesRegex(Exception, "truncated"):
            self._call(
                "stats.distribution_diagnostics",
                {"series": truncated.to_primitive(), "limit": 10},
            )


if __name__ == "__main__":
    unittest.main()
