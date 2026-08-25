"""Offline public-boundary coverage for the Stage 10 econometrics v2 slice."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.market_statistics import (
    stage10_market_return_example_series,
)
from tests.tool_platform.test_econometrics_v2_kernel import (
    _ENGLE_GRANGER_RETURNS_X,
    _ENGLE_GRANGER_RETURNS_Y,
    _VAR_SERIES_0,
    _VAR_SERIES_1,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"

_OLS_X = tuple(
    Decimal(value)
    for value in ("-0.03", "-0.02", "-0.01", "0", "0.01", "0.02", "0.03")
)
_OLS_Y = tuple(
    Decimal(value)
    for value in ("-0.04", "-0.04", "-0.01", "0.01", "0.03", "0.04", "0.08")
)
_ROBUST_X1 = tuple(
    Decimal(value)
    for value in (
        "-1.3", "-1.1", "-0.9", "-0.7", "-0.5", "-0.3", "-0.1",
        "0.1", "0.3", "0.5", "0.7", "0.9", "1.1", "1.3",
    )
)
_ROBUST_X2 = tuple(
    Decimal(value)
    for value in (
        "0.8", "-0.4", "1.1", "-1.2", "0.3", "-0.8", "1.5",
        "-0.1", "0.7", "-1.4", "0.5", "-0.7", "1.2", "-0.3",
    )
)
_ROBUST_Y = tuple(
    Decimal(value)
    for value in (
        "-0.440", "-0.130", "-0.070", "0.390", "0.050", "-0.160",
        "-0.470", "-0.030", "0.310", "0.820", "0.300", "0.020",
        "-0.450", "0.180",
    )
)
_ADF_VALUES = tuple(
    Decimal(value)
    for value in (
        "0",
        "0.5",
        "0.1",
        "-0.2",
        "0.1",
        "0.4",
        "0.2",
        "-0.1",
        "0.3",
        "0.2",
        "-0.3",
        "-0.2",
        "0.1",
        "0.4",
        "0.2",
        "-0.1",
        "0",
        "0.3",
        "-0.1",
        "-0.2",
    )
)


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


def _diagnostic_metrics(
    result: dict[str, object], code: str
) -> dict[str, object]:
    diagnostic = next(
        item
        for item in result["diagnostics"]  # type: ignore[index]
        if item["code"] == code
    )
    return {
        item["name"]: item["value"]
        for item in diagnostic["metrics"]
    }


def _series(symbol: str, values: tuple[Decimal | None, ...]) -> TimeSeries:
    base = stage10_market_return_example_series(symbol)
    payload = base.to_primitive()
    start = date(2026, 7, 1)
    days = tuple(
        (start + timedelta(days=index)).isoformat() for index in range(len(values))
    )
    observations: list[Observation] = []
    source_observations: list[dict[str, str]] = []
    for index, (day, value) in enumerate(zip(days, values, strict=True)):
        template = base.observations[index % len(base.observations)]
        observations.append(
            replace(
                template,
                period_start=day,
                period_end=day,
                value=value,
                missing_reason=None if value is not None else "fixture_missing",
                vintage_at=f"fixture-vintage:{symbol}:{index}",
                version_id=f"fixture-version:{symbol}:{index}",
                evidence_id=f"fixture-evidence:{symbol}:{index}",
                snapshot_id=f"fixture-snapshot:{symbol}:{index}",
                run_id=f"fixture-run:{symbol}:{index}",
            )
        )
        source_observations.append(
            {
                "period_start": day,
                "period_end": day,
                "version_id": f"source-version:{symbol}:{index}",
                "evidence_id": f"source-evidence:{symbol}:{index}",
                "snapshot_id": f"source-snapshot:{symbol}:{index}",
                "run_id": f"source-run:{symbol}:{index}",
            }
        )
    audit = payload["audit"]
    audit.update(
        {
            "requested_start_date": (
                days[0] if days else audit["requested_start_date"]
            ),
            "requested_end_date": (
                days[-1] if days else audit["requested_end_date"]
            ),
            "limit": max(1, len(values)),
            "selected_count": len(values),
            "missing_count": sum(value is None for value in values),
            "source_selection_limit": max(1, len(values)),
            "source_selected_count": len(values),
        }
    )
    derivation = payload["provenance"]["derivation"]
    source_selection = derivation["source_selection"]
    source_selection["selected_count"] = len(values)
    source_selection["observations"] = source_observations
    derivation["source_selection_sha256"] = hashlib.sha256(
        dumps_strict(source_selection).encode("utf-8")
    ).hexdigest()
    return TimeSeries(
        series_id=payload["series_id"],
        metadata=payload["metadata"],
        observations=tuple(observations),
        warnings=tuple(payload["warnings"]),
        audit=audit,
        provenance=payload["provenance"],
        truncated=False,
    )


def _simple_returns_from_log(
    values: tuple[Decimal, ...],
) -> tuple[Decimal, ...]:
    with localcontext() as context:
        context.prec = 34
        return tuple(+(value.exp() - Decimal("1")) for value in values)


def _log_returns(series: TimeSeries) -> TimeSeries:
    payload = series.to_primitive()
    payload["metadata"]["return_method"] = "log"
    payload["audit"]["return_method"] = "log"
    observations = tuple(
        replace(
            observation,
            quality_flags=tuple(
                flag.replace(
                    "transformation:return:trailing:simple:",
                    "transformation:return:trailing:log:",
                )
                for flag in observation.quality_flags
            ),
        )
        for observation in series.observations
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


def _forward(series: TimeSeries) -> TimeSeries:
    payload = series.to_primitive()
    payload["metadata"]["return_direction"] = "forward"
    payload["metadata"]["return_target_status"] = "outcome_label"
    payload["audit"]["return_direction"] = "forward"
    observations = tuple(
        replace(
            item,
            quality_flags=tuple(
                flag.replace(
                    "transformation:return:trailing:",
                    "transformation:return:forward:",
                )
                for flag in item.quality_flags
            ),
        )
        for item in series.observations
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


def _truncated(series: TimeSeries) -> TimeSeries:
    payload = series.to_primitive()
    payload["truncated"] = True
    payload["audit"]["truncated"] = True
    return TimeSeries(
        series_id=payload["series_id"],
        metadata=payload["metadata"],
        observations=series.observations,
        warnings=tuple(payload["warnings"]),
        audit=payload["audit"],
        provenance=payload["provenance"],
        truncated=True,
    )


class Stage10MarketEconometricsTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return self.dispatcher.call(name, arguments, tool_version="2.0.0")

    def _call_v21(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        return self.dispatcher.call(name, arguments, tool_version="2.1.0")

    def _call_v3(
        self, arguments: dict[str, object]
    ) -> dict[str, object]:
        return self.dispatcher.call(
            "econometrics.regression", arguments, tool_version="3.0.0"
        )

    def assertDecimalClose(
        self, actual: object, expected: str, tolerance: str = "2e-12"
    ) -> None:
        self.assertIsInstance(actual, Decimal)
        assert isinstance(actual, Decimal)
        self.assertLessEqual(
            abs(actual - Decimal(expected)), Decimal(tolerance)
        )

    def test_ols_exposes_frozen_coefficients_inference_and_research_contract(self) -> None:
        dependent = _series("DEPENDENT", _OLS_Y)
        predictor = _series("PREDICTOR", _OLS_X)
        arguments = {
            "series": [dependent, predictor],
            "intercept": True,
            "covariance": "classical_homoskedastic",
            "confidence_level": "0.95",
            "limit": 100,
        }

        first = self._call("econometrics.regression", arguments)
        second = self._call("econometrics.regression", arguments)
        records = [
            _fields(record) for record in first["records"]  # type: ignore[index]
        ]
        by_term = {record["term"]: record for record in records}
        self.assertEqual(first["status"], "ok")
        self.assertDecimalClose(by_term["intercept"]["estimate"], "0.01")
        self.assertDecimalClose(by_term["x_1"]["estimate"], "2")
        self.assertDecimalClose(
            by_term["x_1"]["p_value"], "0.0000759116567796678"
        )
        self.assertEqual(
            {matrix["name"] for matrix in first["matrices"]},  # type: ignore[index]
            {"ols_observations", "ols_predictors", "ols_classical_covariance"},
        )
        research = first["research_contract"]  # type: ignore[index]
        self.assertEqual(research["status"], "declared")
        self.assertEqual(
            research["contract"]["execution_policy"],
            "caller_supplied_in_memory_read_only_no_store",
        )
        self.assertEqual(
            research["contract"]["analysis_id"],
            second["research_contract"]["contract"]["analysis_id"],  # type: ignore[index]
        )
        self.assertEqual(_metrics(first)["sample_size"], 7)
        self.assertFalse(any(self.root.iterdir()))

    def test_rolling_returns_every_window_and_each_inference_matrix(self) -> None:
        result = self._call(
            "econometrics.rolling_regression",
            {
                "series": [_series("ROLLY", _OLS_Y), _series("ROLLX", _OLS_X)],
                "window": 5,
                "intercept": True,
                "covariance": "classical_homoskedastic",
                "confidence_level": "0.95",
                "limit": 100,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["records"]), 3)  # type: ignore[arg-type]
        self.assertEqual(len(result["matrices"]), 6)  # type: ignore[arg-type]
        estimate = next(
            matrix
            for matrix in result["matrices"]  # type: ignore[index]
            if matrix["name"] == "rolling_coefficient_estimate"
        )
        for actual, expected in zip(
            (row[1] for row in estimate["values"]),
            ("1.9", "2", "2.1"),
            strict=True,
        ):
            self.assertDecimalClose(actual, expected)
        self.assertEqual(_metrics(result)["window_count"], 3)
        self.assertFalse(any(self.root.iterdir()))

    def test_v21_robust_regression_matches_frozen_public_contract_and_http(self) -> None:
        arguments = {
            "series": [
                _series("ROBUSTY", _ROBUST_Y).to_primitive(),
                _series("ROBUSTX1", _ROBUST_X1).to_primitive(),
                _series("ROBUSTX2", _ROBUST_X2).to_primitive(),
            ],
            "intercept": True,
            "covariance": "hc1",
            "hac_lag": 0,
            "diagnostic_lag": 3,
            "confidence_level": "0.95",
            "limit": 100,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call_v21("econometrics.regression", arguments)
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.regression",
                        "tool_version": "2.1.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        self.assertEqual(response.status, 200)
        envelope = loads_strict(response.body)
        self.assertEqual(dumps_strict(envelope["result"]), dumps_strict(direct))
        self.assertEqual(envelope["receipt"]["logical_stores"], [])
        self.assertEqual(direct["status"], "ok")
        records = {
            fields["term"]: fields
            for fields in (
                _fields(record)
                for record in direct["records"]  # type: ignore[index]
            )
        }
        self.assertDecimalClose(
            records["x_2"]["standard_error"], "0.092213812238309156"
        )
        self.assertDecimalClose(
            records["x_2"]["standardized_statistic"], "-2.7037386708969051"
        )
        self.assertDecimalClose(
            records["x_2"]["p_value"], "0.0068564188576006359"
        )
        self.assertEqual(records["x_2"]["statistic_kind"], "z")
        self.assertNotIn("t_statistic", records["x_2"])
        self.assertEqual(
            [item["code"] for item in direct["diagnostics"]],  # type: ignore[index]
            [
                "stage10_ols_v2_1",
                "stage10_ljung_box_v2_1",
                "stage10_breusch_pagan_v2_1",
                "stage10_jarque_bera_v2_1",
            ],
        )
        for code, statistic, p_value in (
            (
                "stage10_ljung_box_v2_1",
                "13.281650661833362",
                "0.0040654684026667645",
            ),
            (
                "stage10_breusch_pagan_v2_1",
                "1.4894934745600836",
                "0.47485454766646296",
            ),
            (
                "stage10_jarque_bera_v2_1",
                "1.1075654098693868",
                "0.57477150203198413",
            ),
        ):
            diagnostic = _diagnostic_metrics(direct, code)
            self.assertEqual(diagnostic["status"], "established")
            self.assertDecimalClose(diagnostic["statistic"], statistic)
            self.assertDecimalClose(diagnostic["p_value"], p_value)
        self.assertEqual(
            {matrix["name"] for matrix in direct["matrices"]},  # type: ignore[index]
            {"ols_observations", "ols_predictors", "ols_hc1_covariance"},
        )
        self.assertEqual(
            direct["research_contract"]["contract"]["model_version"],  # type: ignore[index]
            "2.1.0",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_v21_rolling_windows_match_standalone_v21_fits(self) -> None:
        arguments = {
            "series": [
                _series("ROLLROBUSTY", _ROBUST_Y),
                _series("ROLLROBUSTX1", _ROBUST_X1),
                _series("ROLLROBUSTX2", _ROBUST_X2),
            ],
            "window": 8,
            "intercept": True,
            "covariance": "hc3",
            "hac_lag": 0,
            "diagnostic_lag": 1,
            "confidence_level": "0.95",
            "limit": 100,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            result = self._call_v21(
                "econometrics.rolling_regression", arguments
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["records"]), 7)  # type: ignore[arg-type]
        self.assertEqual(len(result["matrices"]), 6)  # type: ignore[arg-type]
        first_window = _fields(result["records"][0])  # type: ignore[index]
        self.assertEqual(first_window["standardized_statistic_kind"], "z")
        for name in ("ljung_box", "breusch_pagan", "jarque_bera"):
            self.assertEqual(first_window[f"{name}_status"], "established")
        estimate_matrix = next(
            matrix
            for matrix in result["matrices"]  # type: ignore[index]
            if matrix["name"] == "rolling_coefficient_estimate"
        )
        for index, actual in enumerate(estimate_matrix["values"]):
            standalone = self._call_v21(
                "econometrics.regression",
                {
                    "series": [
                        _series("WINDOWY", _ROBUST_Y[index:index + 8]),
                        _series("WINDOWX1", _ROBUST_X1[index:index + 8]),
                        _series("WINDOWX2", _ROBUST_X2[index:index + 8]),
                    ],
                    "intercept": True,
                    "covariance": "hc3",
                    "hac_lag": 0,
                    "diagnostic_lag": 1,
                    "confidence_level": "0.95",
                    "limit": 100,
                },
            )
            expected = tuple(
                _fields(record)["estimate"]
                for record in standalone["records"]  # type: ignore[index]
            )
            self.assertEqual(tuple(actual), expected)
        self.assertEqual(
            result["research_contract"]["contract"]["model_version"],  # type: ignore[index]
            "2.1.0",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_adf_exposes_mackinnon_decision_without_student_t_p_value(self) -> None:
        result = self._call(
            "econometrics.stationarity",
            {
                "series": _series("ADF", _ADF_VALUES),
                "deterministic": "constant",
                "lag": 1,
                "significance": "0.05",
                "limit": 100,
            },
        )

        fields = _fields(result["records"][0])  # type: ignore[index]
        self.assertEqual(result["status"], "ok")
        self.assertDecimalClose(fields["adf_statistic"], "-6.64775791282254")
        self.assertIsNone(fields["p_value"])
        self.assertEqual(
            fields["p_value_status"],
            "not_provided_use_mackinnon_critical_values",
        )
        self.assertEqual(fields["decision"], "reject_unit_root")
        self.assertTrue(fields["reject_0_05"])
        self.assertEqual(_metrics(result)["p_value_status"], "not_provided")
        self.assertFalse(any(self.root.iterdir()))

    def test_v21_stationarity_adds_kpss_and_joint_decision_without_store_access(self) -> None:
        series = _series("KPSS", _ADF_VALUES).to_primitive()
        arguments = {
            "series": series,
            "deterministic": "constant",
            "adf_lag": 1,
            "kpss_lag": 2,
            "significance": "0.05",
            "limit": 100,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call_v21("econometrics.stationarity", arguments)
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.stationarity",
                        "tool_version": "2.1.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        self.assertEqual(direct["status"], "ok")
        self.assertEqual(
            [record["record_type"] for record in direct["records"]],
            [
                "stage10_adf_test",
                "stage10_kpss_test_v2_1",
                "stage10_stationarity_joint_v2_1",
            ],
        )
        kpss = _fields(direct["records"][1])
        joint = _fields(direct["records"][2])
        self.assertDecimalClose(
            kpss["kpss_statistic"],
            "0.1958711971552745951738443303032393",
        )
        self.assertIsNone(kpss["p_value"])
        self.assertEqual(
            kpss["decision"],
            "fail_to_reject_level_stationarity",
        )
        self.assertEqual(
            joint["interpretation"],
            "evidence_consistent_with_level_stationarity",
        )
        self.assertEqual(
            direct["research_contract"]["contract"]["model_version"],
            "2.1.0",
        )
        self.assertEqual(response.status, 200)
        envelope = loads_strict(response.body)
        self.assertEqual(dumps_strict(envelope["result"]), dumps_strict(direct))
        self.assertEqual(envelope["receipt"]["logical_stores"], [])

        legacy = self._call(
            "econometrics.stationarity",
            {
                "series": series,
                "deterministic": "constant",
                "lag": 1,
                "significance": "0.05",
                "limit": 100,
            },
        )
        self.assertEqual(len(legacy["records"]), 1)
        self.assertEqual(
            legacy["records"][0]["record_type"],
            "stage10_adf_test",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_v21_stationarity_joint_interpretation_covers_all_four_states(
        self,
    ) -> None:
        cases = (
            (
                "fail_to_reject_unit_root",
                "fail_to_reject_level_stationarity",
                "inconclusive",
                (
                    "0.304716253356925",
                    "0.727581373608291",
                    "0.0719559718934004",
                    "1.19839620105433",
                    "-0.309530163458363",
                    "0.264409748854661",
                    "-0.693360725705325",
                    "0.14932194834125",
                    "-0.518694376185106",
                    "-1.85929209649913",
                    "-1.09111604635628",
                    "1.29383311690554",
                ),
            ),
            (
                "fail_to_reject_unit_root",
                "reject_level_stationarity",
                "evidence_consistent_with_nonstationarity",
                (
                    "0.674330764166835",
                    "0.717824439756825",
                    "0.717203060612123",
                    "0.127904154335628",
                    "1.39970402677616",
                    "-0.664317578997619",
                    "-0.466273179540201",
                    "0.778604977935051",
                    "-0.243925359663211",
                    "-0.702852278464782",
                    "-0.19258921885559",
                    "-1.05370263618657",
                ),
            ),
            (
                "reject_unit_root",
                "fail_to_reject_level_stationarity",
                "evidence_consistent_with_level_stationarity",
                (
                    "-1.42452946720558",
                    "0.410089464858384",
                    "-0.88073036185325",
                    "0.654582260430857",
                    "0.116851682597922",
                    "-0.211777238528841",
                    "-0.384726359476992",
                    "-0.991303707224419",
                    "0.356678706406677",
                    "1.28125768618595",
                    "-1.07190281900086",
                    "0.23542762822271",
                ),
            ),
            (
                "reject_unit_root",
                "reject_level_stationarity",
                "conflicting_evidence",
                (
                    "-1.51936282160355",
                    "-0.871132979042617",
                    "-0.867822939798483",
                    "-0.389533271877871",
                    "-0.605354651136429",
                    "-0.973227215383505",
                    "0.0669409994394906",
                    "-1.44966384149699",
                    "1.67959751683235",
                    "-1.18224914604917",
                    "-0.237456478675683",
                    "1.68606099454952",
                ),
            ),
        )

        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            for adf_decision, kpss_decision, interpretation, raw in cases:
                with self.subTest(interpretation=interpretation):
                    result = self._call_v21(
                        "econometrics.stationarity",
                        {
                            "series": _series(
                                f"JOINT-{interpretation}",
                                tuple(Decimal(value) for value in raw),
                            ),
                            "deterministic": "constant",
                            "adf_lag": 0,
                            "kpss_lag": 2,
                            "significance": "0.05",
                            "limit": 12,
                        },
                    )
                    adf = _fields(result["records"][0])
                    kpss = _fields(result["records"][1])
                    joint = _fields(result["records"][2])
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(adf["decision"], adf_decision)
                    self.assertEqual(kpss["decision"], kpss_decision)
                    self.assertEqual(joint["interpretation"], interpretation)
        self.assertFalse(any(self.root.iterdir()))

    def test_v21_stationarity_preserves_complete_v20_result_bytes(self) -> None:
        series = _series("ADF", _ADF_VALUES)
        legacy_arguments = {
            "series": series,
            "deterministic": "constant",
            "lag": 1,
            "significance": "0.05",
            "limit": 100,
        }
        before = self._call("econometrics.stationarity", legacy_arguments)
        self._call_v21(
            "econometrics.stationarity",
            {
                "series": series,
                "deterministic": "constant",
                "adf_lag": 1,
                "kpss_lag": 2,
                "significance": "0.05",
                "limit": 100,
            },
        )
        after = self._call("econometrics.stationarity", legacy_arguments)

        self.assertEqual(dumps_strict(after), dumps_strict(before))
        self.assertEqual(
            hashlib.sha256(dumps_strict(before).encode("utf-8")).hexdigest(),
            "ea7a2cf64888755ed749c18f3bd549f1f6f41005223cd01fa1f28b2c77b502c0",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_small_stationarity_limit_returns_typed_failure_direct_and_http(
        self,
    ) -> None:
        arguments = {
            "series": _series("SMALLKPSS", (Decimal("0"),)).to_primitive(),
            "deterministic": "constant",
            "adf_lag": 0,
            "kpss_lag": 0,
            "significance": "0.05",
            "limit": 1,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call_v21("econometrics.stationarity", arguments)
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.stationarity",
                        "tool_version": "2.1.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        self.assertEqual(direct["status"], "not_established")
        self.assertEqual(len(direct["records"]), 3)
        self.assertEqual(direct["truncation"]["limit"], 3)
        self.assertEqual(direct["truncation"]["returned_count"], 3)
        parameters = {
            item["name"]: item["value"]
            for item in direct["research_contract"]["contract"]["parameters"]
        }
        self.assertEqual(
            parameters["observation_limit"],
            1,
        )
        self.assertIn(
            "result_record_limit_raised_for_fixed_analytical_report",
            {item["code"] for item in direct["warnings"]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            dumps_strict(loads_strict(response.body)["result"]),
            dumps_strict(direct),
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_fixed_break_chow_public_contract_matches_http_without_store_access(self) -> None:
        predictor_values = tuple(Decimal(index) for index in range(12))
        dependent_values = tuple(
            Decimal(value)
            for value in (
                "1.0", "3.2", "5.1", "7.4", "8.9", "11.0",
                "16.3", "18.8", "22.2", "24.6", "27.3", "29.5",
            )
        )
        arguments = {
            "series": [
                _series("BREAKY", dependent_values).to_primitive(),
                _series("BREAKX", predictor_values).to_primitive(),
            ],
            "intercept": True,
            "break_index": 6,
            "significance": "0.05",
            "limit": 100,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call(
                "econometrics.structural_breaks",
                arguments,
            )
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.structural_breaks",
                        "tool_version": "2.0.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        summary = _fields(direct["records"][0])
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(len(direct["records"]), 4)
        self.assertEqual(
            [record["record_type"] for record in direct["records"]],
            [
                "stage10_chow_test_v2",
                "stage10_chow_ols_fit_v2",
                "stage10_chow_ols_fit_v2",
                "stage10_chow_ols_fit_v2",
            ],
        )
        self.assertEqual(
            summary["break_index_semantics"],
            "zero_based_first_post_break_row",
        )
        self.assertEqual(summary["pre_break_end_period"], "2026-07-06")
        self.assertEqual(summary["post_break_start_period"], "2026-07-07")
        self.assertDecimalClose(
            summary["f_statistic"],
            "75.34331757755497",
        )
        self.assertDecimalClose(
            summary["p_value"],
            "0.00000645949464775858",
            tolerance="2e-15",
        )
        self.assertEqual(summary["decision"], "reject_parameter_stability")
        self.assertEqual(
            summary["reference_distribution"],
            "fisher_snedecor_f_exact_finite_sample",
        )
        self.assertEqual(
            [matrix["name"] for matrix in direct["matrices"]],
            ["chow_coefficient_estimates"],
        )
        coefficient_matrix = direct["matrices"][0]
        self.assertEqual(
            coefficient_matrix["column_labels"],
            ["pooled", "pre_break", "post_break"],
        )
        self.assertDecimalClose(
            coefficient_matrix["values"][1][1],
            "1.98285714285714",
        )
        warning_codes = {item["code"] for item in direct["warnings"]}
        self.assertIn(
            "chow_exact_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
            warning_codes,
        )
        self.assertEqual(
            direct["research_contract"]["contract"]["model_id"],
            "chow_pooled_vs_segmented_ols_fixed_break",
        )
        uncertainty = {
            item["name"]: item["value"]
            for item in direct["research_contract"]["contract"]["uncertainty"]
        }
        self.assertEqual(
            uncertainty["assumption"],
            "gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
        )
        self.assertEqual(response.status, 200)
        envelope = loads_strict(response.body)
        self.assertEqual(dumps_strict(envelope["result"]), dumps_strict(direct))
        self.assertEqual(envelope["receipt"]["logical_stores"], [])
        self.assertFalse(any(self.root.iterdir()))

    def test_small_structural_break_limit_returns_typed_failure_direct_and_http(
        self,
    ) -> None:
        arguments = {
            "series": [
                _series(
                    "SMALLBREAKY",
                    tuple(Decimal(value) for value in ("1", "2", "4")),
                ).to_primitive(),
                _series(
                    "SMALLBREAKX",
                    tuple(Decimal(value) for value in ("0", "1", "2")),
                ).to_primitive(),
            ],
            "intercept": True,
            "break_index": 1,
            "significance": "0.05",
            "limit": 3,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call(
                "econometrics.structural_breaks",
                arguments,
            )
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.structural_breaks",
                        "tool_version": "2.0.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        summary = _fields(direct["records"][0])
        self.assertEqual(direct["status"], "not_established")
        self.assertEqual(
            summary["reason"],
            "pre_break_insufficient_degrees_of_freedom",
        )
        self.assertEqual(
            summary["reference_distribution"],
            "fisher_snedecor_f_exact_finite_sample",
        )
        self.assertEqual(len(direct["records"]), 4)
        self.assertEqual(direct["truncation"]["limit"], 4)
        self.assertEqual(direct["truncation"]["returned_count"], 4)
        parameters = {
            item["name"]: item["value"]
            for item in direct["research_contract"]["contract"]["parameters"]
        }
        self.assertEqual(
            parameters["observation_limit"],
            3,
        )
        warning_codes = {item["code"] for item in direct["warnings"]}
        self.assertIn(
            "result_record_limit_raised_for_fixed_analytical_report",
            warning_codes,
        )
        self.assertIn(
            "chow_exact_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
            warning_codes,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            dumps_strict(loads_strict(response.body)["result"]),
            dumps_strict(direct),
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_fixed_break_chow_rejects_incomplete_aligned_rows(self) -> None:
        predictor_values = tuple(Decimal(index) for index in range(8))
        dependent_values = tuple(Decimal(index * 2 + 1) for index in range(8))
        result = self._call(
            "econometrics.structural_breaks",
            {
                "series": [
                    _series(
                        "BREAKMISSINGY",
                        (*dependent_values[:3], None, *dependent_values[4:]),
                    ),
                    _series("BREAKMISSINGX", predictor_values),
                ],
                "intercept": True,
                "break_index": 4,
                "significance": "0.05",
                "limit": 100,
            },
        )

        self.assertEqual(result["status"], "not_established")
        self.assertEqual(
            _fields(result["records"][0])["reason"],
            "missing_observations_rejected",
        )
        self.assertEqual(result["matrices"], [])
        self.assertEqual(
            result["exclusions"][0]["code"],
            "structural_break_not_established",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_http_matches_direct_and_never_opens_sqlite(self) -> None:
        arguments = {
            "series": [
                _series("HTTPY", _OLS_Y).to_primitive(),
                _series("HTTPX", _OLS_X).to_primitive(),
            ],
            "intercept": True,
            "covariance": "classical_homoskedastic",
            "confidence_level": "0.95",
            "limit": 100,
        }
        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call("econometrics.regression", arguments)
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.regression",
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

    def test_valid_empty_inputs_are_honestly_not_established(self) -> None:
        common = {
            "series": [_series("EMPTYY", ()), _series("EMPTYX", ())],
            "intercept": True,
            "covariance": "classical_homoskedastic",
            "confidence_level": "0.95",
            "limit": 100,
        }

        regression = self._call("econometrics.regression", common)
        rolling = self._call(
            "econometrics.rolling_regression",
            {**common, "window": 5},
        )

        self.assertEqual(regression["status"], "not_established")
        self.assertEqual(
            _fields(regression["records"][0])["reason"],  # type: ignore[index]
            "no_aligned_observations",
        )
        self.assertEqual(_metrics(regression)["sample_size"], 0)
        self.assertEqual(rolling["status"], "not_established")
        self.assertEqual(
            _fields(rolling["records"][0])["reason"],  # type: ignore[index]
            "no_aligned_observations",
        )
        self.assertEqual(_metrics(rolling)["window_count"], 0)
        self.assertIn(
            "no_aligned_observations",
            {item["code"] for item in rolling["warnings"]},  # type: ignore[index]
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_forward_truncated_and_incomplete_rolling_samples_fail_closed(
        self,
    ) -> None:
        y = _series("HOSTILEY", _OLS_Y)
        x = _series("HOSTILEX", _OLS_X)
        common = {
            "intercept": True,
            "covariance": "classical_homoskedastic",
            "confidence_level": "0.95",
            "limit": 100,
        }
        with self.assertRaises(ValidationError):
            self._call(
                "econometrics.regression",
                {**common, "series": [_forward(y), _forward(x)]},
            )
        with self.assertRaises(ValidationError):
            self._call(
                "econometrics.regression",
                {**common, "series": [y, _truncated(x)]},
            )

        missing_values = list(_OLS_Y)
        missing_values[2] = None
        rolling = self._call(
            "econometrics.rolling_regression",
            {
                **common,
                "series": [
                    _series("MISSINGY", tuple(missing_values)),
                    _series("MISSINGX", _OLS_X),
                ],
                "window": 5,
            },
        )
        window_statuses = [
            _fields(record)["status"]
            for record in rolling["records"]  # type: ignore[index]
        ]
        self.assertSequenceEqual(
            window_statuses,
            ("not_established", "not_established", "not_established"),
        )
        self.assertEqual(rolling["status"], "not_established")
        self.assertFalse(any(self.root.iterdir()))


    def test_v3_engle_granger_matches_frozen_public_reference(self) -> None:
        arguments = {
            "series": [
                _series(
                    "COINTY",
                    _simple_returns_from_log(_ENGLE_GRANGER_RETURNS_Y),
                ),
                _series(
                    "COINTX",
                    _simple_returns_from_log(_ENGLE_GRANGER_RETURNS_X),
                ),
            ],
            "analysis": "engle_granger_cointegration",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": -1,
            "target_index": -1,
            "limit": 30,
        }

        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            result = self._call_v3(arguments)
            log_result = self._call_v3(
                {
                    **arguments,
                    "series": [
                        _log_returns(_series("LOGCOINTY", _ENGLE_GRANGER_RETURNS_Y)),
                        _log_returns(_series("LOGCOINTX", _ENGLE_GRANGER_RETURNS_X)),
                    ],
                }
            )

        self.assertEqual(result["status"], "ok")
        first_stage = _fields(result["records"][0])  # type: ignore[index]
        residual_adf = _fields(result["records"][1])  # type: ignore[index]
        log_first_stage = _fields(
            log_result["records"][0]  # type: ignore[index]
        )
        log_residual_adf = _fields(
            log_result["records"][1]  # type: ignore[index]
        )
        self.assertDecimalClose(
            log_first_stage["slope"],
            "1.6957725947521865889",
        )
        self.assertDecimalClose(
            log_residual_adf["residual_adf_statistic"],
            "-4.5724430962540751810",
        )
        self.assertDecimalClose(
            first_stage["intercept"],
            "-0.0017157434402332361516",
        )
        self.assertDecimalClose(
            first_stage["slope"],
            "1.6957725947521865889",
        )
        self.assertDecimalClose(
            residual_adf["residual_adf_statistic"],
            "-4.5724430962540751810",
        )
        self.assertEqual(
            residual_adf["decision"],
            "reject_no_cointegration",
        )
        self.assertIsNone(residual_adf["p_value"])
        self.assertEqual(
            residual_adf["p_value_status"],
            "not_provided_use_mackinnon_2010_cointegration_critical_values",
        )
        self.assertEqual(
            result["matrices"][0]["name"],  # type: ignore[index]
            "engle_granger_normalized_log_levels",
        )
        self.assertEqual(
            len(result["matrices"][0]["values"]),  # type: ignore[index]
            30,
        )
        self.assertIn(
            "engle_granger_requires_i1_series_assumption_not_verified",
            {item["code"] for item in result["warnings"]},  # type: ignore[index]
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_v3_var_matches_frozen_public_coefficient_matrices(self) -> None:
        result = self._call_v3(
            {
                "series": [
                    _series("VAR0", _VAR_SERIES_0),
                    _series("VAR1", _VAR_SERIES_1),
                ],
                "analysis": "vector_autoregression",
                "deterministic": "constant",
                "lag_order": 1,
                "significance": "0.05",
                "source_index": -1,
                "target_index": -1,
                "limit": 30,
            }
        )

        self.assertEqual(result["status"], "ok")
        equations = [
            _fields(record)
            for record in result["records"]  # type: ignore[index]
        ]
        self.assertDecimalClose(
            equations[0]["intercept"],
            "0.023655096617003633",
        )
        self.assertDecimalClose(
            equations[1]["intercept"],
            "0.006973395187042377",
        )
        matrices = {
            matrix["name"]: matrix
            for matrix in result["matrices"]  # type: ignore[index]
        }
        lag_one = matrices["var_lag_1_coefficients"]["values"]
        self.assertDecimalClose(lag_one[0][0], "-0.11707598706218963")
        self.assertDecimalClose(lag_one[0][1], "-0.08408424657503215")
        self.assertDecimalClose(lag_one[1][0], "0.944874484902506")
        self.assertDecimalClose(lag_one[1][1], "0.06457538088069392")
        covariance = matrices["var_residual_covariance"]["values"]
        self.assertDecimalClose(
            covariance[0][1],
            "-0.0007153817939307785",
        )
        self.assertEqual(_metrics(result)["effective_sample_size"], 29)
        self.assertFalse(any(self.root.iterdir()))

    def test_v3_granger_matches_frozen_reference_http_and_no_store(
        self,
    ) -> None:
        arguments = {
            "series": [
                _series("CAUSE", _VAR_SERIES_0).to_primitive(),
                _series("TARGET", _VAR_SERIES_1).to_primitive(),
            ],
            "analysis": "granger_causality",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": 0,
            "target_index": 1,
            "limit": 30,
        }

        with patch("sqlite3.connect", side_effect=AssertionError("store open")):
            direct = self._call_v3(arguments)
            response = Stage1Application(self.stores, self.registry).handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "econometrics.regression",
                        "tool_version": "3.0.0",
                        "arguments": arguments,
                    }
                ).encode("utf-8"),
            )

        self.assertEqual(response.status, 200)
        envelope = loads_strict(response.body)
        self.assertEqual(dumps_strict(envelope["result"]), dumps_strict(direct))
        self.assertEqual(envelope["receipt"]["logical_stores"], [])
        self.assertEqual(direct["status"], "ok")
        summary = _fields(direct["records"][0])  # type: ignore[index]
        self.assertDecimalClose(
            summary["f_statistic"],
            "100.8478891200319",
        )
        self.assertDecimalClose(
            summary["p_value"],
            "1.9415151100109854e-10",
            tolerance="5e-20",
        )
        self.assertEqual(
            summary["decision"],
            "reject_no_granger_causality",
        )
        self.assertEqual(summary["source_index"], 0)
        self.assertEqual(summary["target_index"], 1)
        self.assertEqual(len(direct["records"]), 3)  # type: ignore[arg-type]
        self.assertIn(
            "granger_is_predictive_precedence_not_structural_causality",
            {item["code"] for item in direct["warnings"]},  # type: ignore[index]
        )
        self.assertIn(
            "exact_granger_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
            {item["code"] for item in direct["warnings"]},  # type: ignore[index]
        )
        uncertainty = {
            item["name"]: item["value"]
            for item in direct["research_contract"]["contract"]["uncertainty"]
        }
        self.assertEqual(
            uncertainty["assumption"],
            "gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_v3_missingness_edges_and_invalid_price_factor_fail_closed(
        self,
    ) -> None:
        interior_missing = list(_VAR_SERIES_1)
        interior_missing[8] = None
        common = {
            "analysis": "vector_autoregression",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": -1,
            "target_index": -1,
            "limit": 30,
        }
        missing = self._call_v3(
            {
                **common,
                "series": [
                    _series("MISSING0", _VAR_SERIES_0),
                    _series("MISSING1", tuple(interior_missing)),
                ],
            }
        )
        self.assertEqual(missing["status"], "not_established")
        self.assertEqual(
            _fields(missing["records"][0])["reason"],  # type: ignore[index]
            "missing_observations_prevent_contiguous_var",
        )
        self.assertEqual(missing["matrices"], [])

        interior_eg_missing = list(
            _simple_returns_from_log(_ENGLE_GRANGER_RETURNS_Y)
        )
        interior_eg_missing[10] = None
        missing_eg = self._call_v3(
            {
                "series": [
                    _series("MISSINGCOINTY", tuple(interior_eg_missing)),
                    _series(
                        "MISSINGCOINTX",
                        _simple_returns_from_log(_ENGLE_GRANGER_RETURNS_X),
                    ),
                ],
                "analysis": "engle_granger_cointegration",
                "deterministic": "constant",
                "lag_order": 1,
                "significance": "0.05",
                "source_index": -1,
                "target_index": -1,
                "limit": 30,
            }
        )
        self.assertEqual(missing_eg["status"], "not_established")
        self.assertEqual(missing_eg["matrices"], [])
        self.assertIn(
            "missing_observations_prevent_contiguous_test",
            {item["reason"] for item in missing_eg["exclusions"]},
        )

        trimmed = self._call_v3(
            {
                **common,
                "series": [
                    _series("EDGE0", (None, *_VAR_SERIES_0[1:])),
                    _series("EDGE1", (None, *_VAR_SERIES_1[1:])),
                ],
            }
        )
        self.assertEqual(trimmed["status"], "ok")
        self.assertIn(
            "common_incomplete_edge_rows_trimmed",
            {
                item["code"]
                for item in trimmed["exclusions"]  # type: ignore[index]
            },
        )
        self.assertEqual(
            _metrics(trimmed)["common_edge_excluded_count"],
            1,
        )

        invalid_returns = list(_simple_returns_from_log(_ENGLE_GRANGER_RETURNS_Y))
        invalid_returns[10] = Decimal("-1")
        invalid_factor = self._call_v3(
            {
                "series": [
                    _series("BADFACTOR", tuple(invalid_returns)),
                    _series(
                        "FACTORX",
                        _simple_returns_from_log(_ENGLE_GRANGER_RETURNS_X),
                    ),
                ],
                "analysis": "engle_granger_cointegration",
                "deterministic": "constant",
                "lag_order": 1,
                "significance": "0.05",
                "source_index": -1,
                "target_index": -1,
                "limit": 30,
            }
        )
        self.assertEqual(invalid_factor["status"], "not_established")
        self.assertEqual(
            _fields(invalid_factor["records"][0])["reason"],  # type: ignore[index]
            "nonpositive_simple_return_price_factor",
        )
        self.assertFalse(any(self.root.iterdir()))

if __name__ == "__main__":
    unittest.main()
