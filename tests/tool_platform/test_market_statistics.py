from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.market_statistics import (
    stage10_market_return_example_series,
    validate_stage10_return_inputs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _fields(record: dict[str, object]) -> dict[str, object]:
    return {
        item["name"]: item["value"]
        for item in record["fields"]  # type: ignore[index]
    }


def _metrics(result: dict[str, object]) -> dict[str, object]:
    diagnostics = result["diagnostics"]  # type: ignore[index]
    return {
        item["name"]: item["value"]
        for item in diagnostics[0]["metrics"]  # type: ignore[index]
    }


def _series_from_payload(
    payload: dict[str, object], observations: tuple[Observation, ...]
) -> TimeSeries:
    return TimeSeries(
        series_id=payload["series_id"],  # type: ignore[arg-type]
        metadata=payload["metadata"],  # type: ignore[arg-type]
        observations=observations,
        warnings=tuple(payload["warnings"]),  # type: ignore[arg-type]
        audit=payload["audit"],  # type: ignore[arg-type]
        provenance=payload["provenance"],  # type: ignore[arg-type]
        truncated=payload["truncated"],  # type: ignore[arg-type]
    )


class Stage10MarketStatisticsTests(unittest.TestCase):
    """Offline boundary coverage for composable Stage 10 return statistics."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = StoreMap.four_explicit(
            market=self.root / "market.sqlite",
            macro=self.root / "macro.sqlite",
            company=self.root / "company.sqlite",
            news=self.root / "news.sqlite",
        )
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.dispatcher = ToolDispatcher(self.stores, self.registry)
        self.aapl = stage10_market_return_example_series("AAPL")
        self.msft = stage10_market_return_example_series("MSFT")
        self.offset_msft = self._offset_msft()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return self.dispatcher.call(name, arguments, tool_version="2.0.0")

    def _offset_msft(self) -> TimeSeries:
        """Make a valid independently sourced series with a one-day offset.

        The source-selection receipt is updated as part of the fixture so the
        test exercises outer alignment, not an intentionally invalid lineage.
        """

        days = ("2026-08-11", "2026-08-12", "2026-08-13")
        values = (Decimal("3"), Decimal("-3"), Decimal("7"))
        observations = tuple(
            replace(
                observation,
                period_start=days[index],
                period_end=days[index],
                value=values[index],
                missing_reason=None,
            )
            for index, observation in enumerate(self.msft.observations)
        )
        payload = self.msft.to_primitive()
        audit = payload["audit"]
        audit["missing_count"] = 0
        audit["selected_count"] = len(observations)
        audit["requested_end_date"] = days[-1]
        audit["source_selected_count"] = len(observations)
        derivation = payload["provenance"]["derivation"]
        source_selection = derivation["source_selection"]
        for source, observation in zip(
            source_selection["observations"], observations, strict=True
        ):
            source["period_start"] = observation.period_start
            source["period_end"] = observation.period_end
        source_selection["selected_count"] = len(observations)
        derivation["source_selection_sha256"] = hashlib.sha256(
            dumps_strict(source_selection).encode("utf-8")
        ).hexdigest()
        return _series_from_payload(payload, observations)

    def _incompatible_method(self) -> TimeSeries:
        payload = self.msft.to_primitive()
        payload["metadata"]["return_method"] = "log"
        payload["audit"]["return_method"] = "log"
        return _series_from_payload(payload, self.msft.observations)

    def _truncated(self) -> TimeSeries:
        payload = self.msft.to_primitive()
        payload["truncated"] = True
        payload["audit"]["truncated"] = True
        return _series_from_payload(payload, self.msft.observations)

    def test_describe_reports_exact_statistics_and_http_matches_direct(self) -> None:
        arguments = {"series": self.aapl.to_primitive(), "limit": 100}
        direct = self._call("timeseries.describe", arguments)
        fields = _fields(direct["records"][0])  # type: ignore[index]
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(
            {
                name: fields[name]
                for name in (
                    "count",
                    "nonmissing_count",
                    "missing_count",
                    "mean",
                    "sample_variance",
                )
            },
            {
                "count": 3,
                "nonmissing_count": 2,
                "missing_count": 1,
                "mean": Decimal("0"),
                "sample_variance": Decimal("2"),
            },
        )
        metrics = _metrics(direct)
        self.assertEqual(metrics["mode"], "latest")
        self.assertIsNone(metrics["cutoff"])
        self.assertIsNone(metrics["cutoff_precision"])
        self.assertEqual(metrics["date_only_policy"], "completed_date")
        self.assertEqual(metrics["availability_basis"], "local_capture")
        self.assertEqual(metrics["point_in_time_status"], "not_applicable")
        self.assertEqual(metrics["point_in_time_scope"], "current_stored_knowledge")
        self.assertEqual(metrics["return_target_status"], "historical_transform")
        self.assertEqual(metrics["registry_revision"], "2.33.0")
        self.assertEqual(metrics["input_series_id_0"], self.aapl.series_id)
        self.assertEqual(
            metrics["input_lineage_digest_0"], self.aapl.lineage_digest
        )

        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "timeseries.describe",
                    "tool_version": "2.0.0",
                    "arguments": arguments,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status, 200)
        envelope = loads_strict(response.body)
        self.assertEqual(dumps_strict(envelope["result"]), dumps_strict(direct))
        self.assertEqual(envelope["receipt"]["logical_stores"], [])
        self.assertFalse(any(self.root.iterdir()))

    def test_align_preserves_inner_and_outer_missingness(self) -> None:
        arguments = {"series": (self.aapl, self.offset_msft), "limit": 100}
        inner = self._call(
            "timeseries.align", {**arguments, "join": "inner"}
        )
        outer = self._call(
            "timeseries.align", {**arguments, "join": "outer"}
        )

        inner_rows = [_fields(record) for record in inner["records"]]  # type: ignore[index]
        self.assertEqual(
            [row["period_end"] for row in inner_rows],
            ["2026-08-11", "2026-08-12"],
        )
        self.assertEqual(
            [(row["value_0"], row["value_1"]) for row in inner_rows],
            [(Decimal("1"), Decimal("3")), (Decimal("-1"), Decimal("-3"))],
        )
        self.assertEqual(
            [
                (row["missing_reason_0"], row["missing_reason_1"])
                for row in inner_rows
            ],
            [(None, None), (None, None)],
        )

        outer_rows = [_fields(record) for record in outer["records"]]  # type: ignore[index]
        self.assertEqual(
            [row["period_end"] for row in outer_rows],
            ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"],
        )
        self.assertEqual(
            (outer_rows[0]["missing_reason_0"], outer_rows[0]["missing_reason_1"]),
            ("insufficient_history", "absent_from_series"),
        )
        self.assertEqual(
            (outer_rows[-1]["missing_reason_0"], outer_rows[-1]["missing_reason_1"]),
            ("absent_from_series", None),
        )
        self.assertEqual(_metrics(outer)["missing_cell_count"], 3)
        outer_metrics = _metrics(outer)
        self.assertEqual(outer_metrics["input_series_id_0"], self.aapl.series_id)
        self.assertEqual(
            outer_metrics["input_lineage_digest_0"], self.aapl.lineage_digest
        )
        self.assertEqual(
            outer_metrics["input_series_id_1"], self.offset_msft.series_id
        )
        self.assertEqual(
            outer_metrics["input_lineage_digest_1"],
            self.offset_msft.lineage_digest,
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_correlation_uses_outer_complete_pairs_and_counts_exclusions(self) -> None:
        result = self._call(
            "timeseries.correlation",
            {"series": (self.aapl, self.offset_msft), "limit": 100},
        )
        fields = _fields(result["records"][0])  # type: ignore[index]
        metrics = _metrics(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(fields["coefficient"], Decimal("1"))
        self.assertEqual(fields["sample_size"], 2)
        self.assertEqual(fields["excluded_count"], 2)
        self.assertEqual(metrics["alignment_policy"], "outer_complete_pairs")
        self.assertEqual(metrics["sample_size"], 2)
        self.assertEqual(metrics["excluded_count"], 2)
        self.assertFalse(any(self.root.iterdir()))

    def test_typed_core_rejects_constant_drift_and_invalid_observation_time(self) -> None:
        drifted = self.aapl.to_primitive()
        drifted["metadata"]["frequency"] = "weekly"
        with self.assertRaises(ValidationError):
            validate_stage10_return_inputs(
                (_series_from_payload(drifted, self.aapl.observations),)
            )

        malformed_observations = tuple(
            replace(
                observation,
                available_at="not-a-time",
                captured_at="not-a-time",
            )
            if index == 1
            else observation
            for index, observation in enumerate(self.aapl.observations)
        )
        malformed = _series_from_payload(
            self.aapl.to_primitive(), malformed_observations
        )
        with self.assertRaises(ValidationError):
            validate_stage10_return_inputs((malformed,))

        invented_precision_observations = tuple(
            replace(
                observation,
                available_precision="invented",
                captured_precision="invented",
            )
            if index == 1
            else observation
            for index, observation in enumerate(self.aapl.observations)
        )
        invented_precision = _series_from_payload(
            self.aapl.to_primitive(), invented_precision_observations
        )
        with self.assertRaises(ValidationError):
            validate_stage10_return_inputs((invented_precision,))

        public_null_availability = self.aapl.to_primitive()
        public_null_availability["observations"][1]["available_at"] = None
        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.describe",
                {"series": public_null_availability, "limit": 100},
            )

        public_invented_precision = self.aapl.to_primitive()
        public_invented_precision["observations"][1][
            "available_precision"
        ] = "invented"
        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.describe",
                {"series": public_invented_precision, "limit": 100},
            )

    def test_as_of_contract_reapplies_cutoff_to_identity_and_observations(self) -> None:
        payload = self.aapl.to_primitive()
        payload["provenance"]["store_receipt"][
            "selected_instrument_identity"
        ]["captured_at"] = "2026-08-13T01:00:00Z"
        payload["audit"].update(
            {
                "mode": "as_of",
                "requested_mode": "as_of",
                "actual_mode": "as_of",
                "cutoff": "2026-08-14T12:00:00Z",
                "cutoff_precision": "datetime",
                "point_in_time_status": "safe",
                "point_in_time_scope": "retained_local_captures",
            }
        )
        unavailable = _series_from_payload(payload, self.aapl.observations)
        with self.assertRaises(ValidationError):
            validate_stage10_return_inputs((unavailable,))

        payload["audit"]["cutoff"] = "2026-08-16T12:00:00Z"
        available = _series_from_payload(payload, self.aapl.observations)
        reports = validate_stage10_return_inputs((available,))
        self.assertEqual(reports[0].point_in_time_status, "safe")

    def test_twenty_series_boundary_keeps_diagnostics_within_contract(self) -> None:
        series = tuple(
            stage10_market_return_example_series(f"S{index}")
            for index in range(20)
        )
        result = self._call(
            "timeseries.align",
            {"series": series, "join": "inner", "limit": 100},
        )
        metrics = _metrics(result)
        self.assertLessEqual(len(metrics), 100)
        for index, value in enumerate(series):
            self.assertEqual(metrics[f"input_series_id_{index}"], value.series_id)
            self.assertEqual(
                metrics[f"input_lineage_digest_{index}"], value.lineage_digest
            )

    def test_incompatible_tampered_truncated_and_over_limit_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.align",
                {
                    "series": (self.aapl, self._incompatible_method()),
                    "join": "inner",
                    "limit": 100,
                },
            )

        contradictory = self.aapl.to_primitive()
        contradictory["audit"]["requested_mode"] = "as_of"
        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.describe",
                {
                    "series": _series_from_payload(
                        contradictory, self.aapl.observations
                    ),
                    "limit": 100,
                },
            )

        tampered = self.aapl.to_primitive()
        tampered["lineage_digest"] = "0" * 64
        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.describe", {"series": tampered, "limit": 100}
            )

        with self.assertRaises(ValidationError):
            self._call(
                "timeseries.correlation",
                {"series": (self.aapl, self._truncated()), "limit": 100},
            )

        with self.assertRaises(ResourceLimitError):
            self._call(
                "timeseries.align",
                {
                    "series": (self.aapl, self.offset_msft),
                    "join": "outer",
                    "limit": 1,
                },
            )
        self.assertFalse(any(self.root.iterdir()))

    def test_statistics_never_open_a_store_and_v1_describe_remains_default(self) -> None:
        with patch.object(
            sqlite3,
            "connect",
            side_effect=AssertionError("composable statistics must not open stores"),
        ) as connection:
            self._call(
                "timeseries.describe", {"series": self.aapl, "limit": 100}
            )
            self._call(
                "timeseries.align",
                {
                    "series": (self.aapl, self.offset_msft),
                    "join": "outer",
                    "limit": 100,
                },
            )
            self._call(
                "timeseries.correlation",
                {"series": (self.aapl, self.offset_msft), "limit": 100},
            )
        connection.assert_not_called()
        self.assertFalse(any(self.root.iterdir()))

        legacy_arguments = self.registry.tool("timeseries.describe")["examples"][0]
        omitted = self.dispatcher.call("timeseries.describe", legacy_arguments)
        explicit = self.dispatcher.call(
            "timeseries.describe", legacy_arguments, tool_version="1.0.0"
        )
        self.assertEqual(dumps_strict(omitted), dumps_strict(explicit))


if __name__ == "__main__":
    unittest.main()
