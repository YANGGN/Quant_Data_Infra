"""Focused independent vectors for the pure v2 econometric kernels."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import unittest

from quant_data.tool_platform.econometrics import (
    HC1,
    HC3,
    F_INFERENCE_BACKEND,
    KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD,
    MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD,
    NEWEY_WEST_HAC_BARTLETT,
    NORMAL_INFERENCE_BACKEND,
    STUDENT_T_INFERENCE_BACKEND,
    augmented_dickey_fuller,
    chow_break_test,
    fit_ols,
    kpss_level_stationarity,
    rolling_ols,
)


_OLS_X = tuple(
    (Decimal(value),)
    for value in ("-0.03", "-0.02", "-0.01", "0", "0.01", "0.02", "0.03")
)
_OLS_Y = tuple(
    Decimal(value)
    for value in ("-0.04", "-0.04", "-0.01", "0.01", "0.03", "0.04", "0.08")
)
_ROBUST_X = tuple(
    tuple(Decimal(value) for value in row)
    for row in (
        ("-1.3", "0.8"),
        ("-1.1", "-0.4"),
        ("-0.9", "1.1"),
        ("-0.7", "-1.2"),
        ("-0.5", "0.3"),
        ("-0.3", "-0.8"),
        ("-0.1", "1.5"),
        ("0.1", "-0.1"),
        ("0.3", "0.7"),
        ("0.5", "-1.4"),
        ("0.7", "0.5"),
        ("0.9", "-0.7"),
        ("1.1", "1.2"),
        ("1.3", "-0.3"),
    )
)
_ROBUST_Y = tuple(
    Decimal(value)
    for value in (
        "-0.440",
        "-0.130",
        "-0.070",
        "0.390",
        "0.050",
        "-0.160",
        "-0.470",
        "-0.030",
        "0.310",
        "0.820",
        "0.300",
        "0.020",
        "-0.450",
        "0.180",
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
_ADF_AMBIENT_CONTEXT_VALUES = tuple(
    Decimal(value)
    for value in (
        "0.0",
        "0.1257302210933933",
        "-0.10106877504425059",
        "0.6154741583940657",
        "0.2568278726885113",
        "-0.47227226424214847",
        "0.2450162121922166",
        "1.364481484814454",
        "1.2838986940287405",
        "-0.38680909965909605",
        "-1.3609040149196112",
        "-0.9592091068535936",
        "-0.1954516086406909",
        "-2.373277356256588",
        "-0.804627311265059",
        "-1.4445305314806332",
        "-1.0888449273881515",
        "-0.8130367433662236",
        "-0.5169955810313422",
        "0.2840118911774019",
        "1.1126207632032137",
        "0.1461120838124702",
        "1.4025307540375271",
        "-0.3189846066230677",
        "0.2727697774687774",
        "0.9708024969140329",
        "0.3336516756499897",
        "-0.6611384393655904",
        "-1.0849252081622103",
        "-0.7255360164802638",
        "0.04109896315097106",
        "-0.9994730407391511",
        "-0.45589218145341404",
        "-0.2717604834997729",
        "0.4737624102937483",
        "0.33160580283620733",
        "0.43722850210668174",
        "-0.5459002032046159",
        "-0.26436728904629503",
        "0.7187172812096623",
    )
)


class EconometricsV2KernelTests(unittest.TestCase):
    def assertDecimalClose(
        self, actual: Decimal | None, expected: str, tolerance: str = "2e-12"
    ) -> None:
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertLessEqual(abs(actual - Decimal(expected)), Decimal(tolerance))

    def test_ols_matches_frozen_independent_reference_with_inference(self) -> None:
        """Reference values were independently frozen from SciPy least squares and t tails."""

        result = fit_ols(_OLS_Y, _OLS_X)

        self.assertEqual(result.status, "established")
        self.assertIsNone(result.reason)
        self.assertEqual(result.sample_size, 7)
        self.assertEqual(result.excluded_count, 0)
        self.assertEqual(result.rank, 2)
        self.assertEqual(result.degrees_of_freedom_model, 1)
        self.assertEqual(result.degrees_of_freedom_residual, 5)
        self.assertEqual(
            result.inference_backend, STUDENT_T_INFERENCE_BACKEND
        )
        self.assertEqual(result.coefficient_names, ("intercept", "x_1"))
        intercept, slope = result.coefficients
        self.assertDecimalClose(intercept.estimate, "0.01")
        self.assertDecimalClose(slope.estimate, "2")
        self.assertDecimalClose(intercept.standard_error, "0.00338061701891407")
        self.assertDecimalClose(slope.standard_error, "0.169030850945703")
        self.assertDecimalClose(intercept.t_statistic, "2.95803989154981")
        self.assertDecimalClose(slope.t_statistic, "11.8321595661992")
        self.assertDecimalClose(intercept.p_value, "0.031590357432456")
        self.assertDecimalClose(slope.p_value, "0.0000759116567796678")
        self.assertDecimalClose(intercept.confidence_interval_lower, "0.00130984729793651")
        self.assertDecimalClose(intercept.confidence_interval_upper, "0.0186901527020635")
        self.assertDecimalClose(slope.confidence_interval_lower, "1.56549236489683")
        self.assertDecimalClose(slope.confidence_interval_upper, "2.43450763510317")
        self.assertDecimalClose(result.residual_sum_squares, "0.0004")
        self.assertDecimalClose(result.r_squared, "0.965517241379310")
        self.assertDecimalClose(result.adjusted_r_squared, "0.958620689655172")
        self.assertDecimalClose(result.root_mean_squared_error, "0.00894427190999916")
        self.assertDecimalClose(result.durbin_watson, "2.5")
        assert result.covariance is not None
        self.assertDecimalClose(result.covariance[0][0], "0.0000114285714285714")
        self.assertDecimalClose(result.covariance[1][1], "0.0285714285714286")
        self.assertEqual(result.residuals[0], Decimal("0.01"))
        self.assertEqual(result.residuals[-1], Decimal("0.01"))
        with self.assertRaises(FrozenInstanceError):
            result.status = "mutated"  # type: ignore[misc]

    def test_complete_case_ols_preserves_original_vector_positions(self) -> None:
        values = (Decimal("1"), None, Decimal("5"), Decimal("7"), Decimal("9"))
        features = (
            (Decimal("0"),),
            (Decimal("1"),),
            (Decimal("2"),),
            (Decimal("3"),),
            (Decimal("4"),),
        )

        result = fit_ols(values, features)

        self.assertEqual(result.status, "established")
        self.assertEqual(result.excluded_indices, (1,))
        self.assertEqual(result.complete_rows, (True, False, True, True, True))
        self.assertIsNone(result.fitted_values[1])
        self.assertIsNone(result.residuals[1])
        self.assertIn("complete_case_rows_dropped", result.warnings)

    def test_robust_covariances_and_diagnostics_match_independent_vectors(self) -> None:
        """Frozen values use independent NumPy algebra and SciPy distribution tails."""

        expected = {
            HC1: (
                0,
                (
                    ("0.0060050801963984881", "0.0017916247747367152", "-0.0012948938888785056"),
                    ("0.0017916247747367152", "0.0067086712999322694", "0.00082427507853504204"),
                    ("-0.0012948938888785056", "0.00082427507853504226", "0.0085033871675221345"),
                ),
                ("0.077492452512476898", "0.081906478986294301", "0.092213812238309156"),
                ("0.57073434720051064", "1.1772303919893863", "-2.7037386708969051"),
                ("0.56817973238334796", "0.23910356123960352", "0.0068564188576006359"),
            ),
            HC3: (
                0,
                (
                    ("0.0077298349776149834", "0.0025814345485228466", "-0.0018648332327561626"),
                    ("0.0025814345485228457", "0.0096134270556827179", "0.0011836560076144255"),
                    ("-0.0018648332327561624", "0.0011836560076144253", "0.012153968702440076"),
                ),
                ("0.087919480080440551", "0.098048085425890483", "0.11024503935524753"),
                ("0.50304669974401262", "0.98342355125727288", "-2.2615262473274753"),
                ("0.61493143925775118", "0.32539902382216557", "0.023726689246918283"),
            ),
            NEWEY_WEST_HAC_BARTLETT: (
                2,
                (
                    ("0.0076539628979610174", "0.0035895906624556261", "-0.00099233765120614655"),
                    ("0.0035895906624556274", "0.0080812360767412942", "0.00030338155841229842"),
                    ("-0.00099233765120614634", "0.00030338155841229842", "0.0017052386861961296"),
                ),
                ("0.087486929869329724", "0.089895695540672546", "0.041294535791023608"),
                ("0.50553384789857547", "1.0726074900869975", "-6.0376523276897833"),
                ("0.61318393112392455", "0.28344726271334286", "1.5637252503070262e-09"),
            ),
        }
        results = {}
        for method, (
            hac_lag,
            covariance,
            standard_errors,
            statistics,
            p_values,
        ) in expected.items():
            result = fit_ols(
                _ROBUST_Y,
                _ROBUST_X,
                covariance=method,
                hac_lag=hac_lag,
                diagnostic_lag=3,
            )
            results[method] = result
            self.assertEqual(result.status, "established")
            self.assertEqual(result.inference_distribution, "normal_asymptotic")
            self.assertEqual(result.inference_backend, NORMAL_INFERENCE_BACKEND)
            self.assertDecimalClose(result.residual_sum_squares, "0.9002181267742283")
            for coefficient, estimate, standard_error, statistic, p_value in zip(
                result.coefficients,
                ("0.044227604297675076", "0.096422796363505675", "-0.24932205013954276"),
                standard_errors,
                statistics,
                p_values,
                strict=True,
            ):
                self.assertDecimalClose(coefficient.estimate, estimate)
                self.assertDecimalClose(coefficient.standard_error, standard_error)
                self.assertDecimalClose(coefficient.t_statistic, statistic)
                self.assertDecimalClose(coefficient.p_value, p_value)
            assert result.covariance is not None
            for actual_row, expected_row in zip(
                result.covariance, covariance, strict=True
            ):
                for actual_value, expected_value in zip(
                    actual_row, expected_row, strict=True
                ):
                    self.assertDecimalClose(actual_value, expected_value)

        hc1 = results[HC1]
        hac_zero = fit_ols(
            _ROBUST_Y,
            _ROBUST_X,
            covariance=NEWEY_WEST_HAC_BARTLETT,
            hac_lag=0,
            diagnostic_lag=3,
        )
        self.assertEqual(hac_zero.covariance, hc1.covariance)
        diagnostics = {item.name: item for item in hc1.residual_diagnostics}
        self.assertEqual(
            tuple(diagnostics), ("ljung_box", "breusch_pagan", "jarque_bera")
        )
        for name, statistic, degrees, p_value in (
            ("ljung_box", "13.281650661833362", 3, "0.0040654684026667645"),
            ("breusch_pagan", "1.4894934745600836", 2, "0.47485454766646296"),
            ("jarque_bera", "1.1075654098693868", 2, "0.57477150203198413"),
        ):
            diagnostic = diagnostics[name]
            self.assertEqual(diagnostic.status, "established")
            self.assertEqual(diagnostic.degrees_of_freedom, degrees)
            self.assertDecimalClose(diagnostic.statistic, statistic)
            self.assertDecimalClose(diagnostic.p_value, p_value)

    def test_robust_inference_and_diagnostics_fail_closed_at_boundaries(self) -> None:
        perfect = fit_ols(
            (Decimal("1"),) * 16,
            tuple(
                (Decimal(value),)
                for value in (
                    "-1",
                    "-1",
                    "1",
                    "1",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                )
            ),
            covariance=HC1,
            diagnostic_lag=1,
        )
        self.assertEqual(perfect.status, "established")
        self.assertIsNone(perfect.coefficients[0].standard_error)
        self.assertIn("perfect_fit_inference_not_established", perfect.warnings)
        self.assertTrue(
            all(
                item.reason == "zero_residual_variance"
                for item in perfect.residual_diagnostics
            )
        )

        gapped_y = list(_ROBUST_Y)
        gapped_y[3] = None
        gapped = fit_ols(
            tuple(gapped_y),
            _ROBUST_X,
            covariance=NEWEY_WEST_HAC_BARTLETT,
            hac_lag=2,
            diagnostic_lag=3,
        )
        self.assertEqual(gapped.status, "established")
        self.assertIsNone(gapped.covariance)
        self.assertIsNone(gapped.coefficients[0].standard_error)
        self.assertIn("hac_requires_contiguous_complete_observations", gapped.warnings)
        ljung_box = next(
            item for item in gapped.residual_diagnostics if item.name == "ljung_box"
        )
        self.assertEqual(ljung_box.status, "not_established")
        self.assertEqual(
            ljung_box.reason, "missing_observations_break_residual_sequence"
        )

        overlagged = fit_ols(
            _ROBUST_Y[:8],
            _ROBUST_X[:8],
            covariance=NEWEY_WEST_HAC_BARTLETT,
            hac_lag=8,
            diagnostic_lag=8,
        )
        self.assertIsNone(overlagged.covariance)
        self.assertIn("hac_lag_exceeds_complete_sample", overlagged.warnings)
        ljung_box = next(
            item for item in overlagged.residual_diagnostics
            if item.name == "ljung_box"
        )
        self.assertEqual(
            ljung_box.reason, "insufficient_sample_for_fixed_ljung_box_lag"
        )

        unit_leverage = fit_ols(
            (
                Decimal("1"),
                Decimal("2"),
                Decimal("4"),
                Decimal("8"),
                Decimal("16"),
            ),
            (
                (Decimal("1"),),
                (Decimal("0"),),
                (Decimal("0"),),
                (Decimal("0"),),
                (Decimal("0"),),
            ),
            covariance=HC3,
        )
        self.assertEqual(unit_leverage.status, "established")
        self.assertIsNone(unit_leverage.covariance)
        self.assertIn(
            "hc3_unit_leverage_inference_not_established",
            unit_leverage.warnings,
        )

    def test_rolling_robust_windows_reuse_the_exact_standalone_kernel(self) -> None:
        windows = rolling_ols(
            _ROBUST_Y,
            _ROBUST_X,
            window=8,
            covariance=HC3,
            diagnostic_lag=1,
        )

        self.assertEqual(len(windows), 7)
        for window in windows:
            expected = fit_ols(
                _ROBUST_Y[window.start_index:window.end_index + 1],
                _ROBUST_X[window.start_index:window.end_index + 1],
                covariance=HC3,
                diagnostic_lag=1,
                missing="reject_incomplete_rows",
            )
            self.assertEqual(window.result, expected)

    def test_rolling_ols_matches_frozen_contiguous_windows(self) -> None:
        windows = rolling_ols(_OLS_Y, _OLS_X, window=5)

        self.assertEqual(len(windows), 3)
        self.assertEqual(
            tuple((window.start_index, window.end_index) for window in windows),
            ((0, 4), (1, 5), (2, 6)),
        )
        expected = (("0.009", "1.9"), ("0.006", "2"), ("0.009", "2.1"))
        for window, (intercept, slope) in zip(windows, expected, strict=True):
            self.assertEqual(window.result.status, "established")
            self.assertDecimalClose(window.result.coefficients[0].estimate, intercept)
            self.assertDecimalClose(window.result.coefficients[1].estimate, slope)

    def test_rolling_ols_rejects_incomplete_windows_without_shrinking_samples(self) -> None:
        values = (Decimal("1"), Decimal("3"), None, Decimal("7"), Decimal("9"), Decimal("11"))
        features = tuple((Decimal(index),) for index in range(len(values)))

        windows = rolling_ols(values, features, window=3)

        self.assertEqual(len(windows), 4)
        self.assertEqual(
            tuple(window.result.status for window in windows),
            ("not_established", "not_established", "not_established", "established"),
        )
        for window in windows[:3]:
            self.assertEqual(window.result.reason, "incomplete_rolling_window")
            self.assertEqual(window.result.sample_size, 2)
            self.assertEqual(window.result.excluded_count, 1)
            self.assertIn("fixed_window_sample_not_established", window.result.warnings)

    def test_rank_deficient_design_is_explicit(self) -> None:
        result = fit_ols(
            (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")),
            (
                (Decimal("0"), Decimal("0")),
                (Decimal("1"), Decimal("2")),
                (Decimal("2"), Decimal("4")),
                (Decimal("3"), Decimal("6")),
            ),
        )

        self.assertEqual(result.status, "not_established")
        self.assertEqual(result.reason, "rank_deficient_design")
        self.assertEqual(result.rank, 2)
        self.assertIsNotNone(result.rank_tolerance)
        self.assertIsNone(result.covariance)

    def test_insufficient_degrees_of_freedom_is_explicit(self) -> None:
        result = fit_ols(
            (Decimal("1"), Decimal("3")),
            ((Decimal("0"),), (Decimal("1"),)),
        )

        self.assertEqual(result.status, "not_established")
        self.assertEqual(result.reason, "insufficient_degrees_of_freedom")
        self.assertEqual(result.parameter_count, 2)
        self.assertEqual(result.sample_size, 2)

    def test_fixed_lag_adf_matches_independent_regression_and_mackinnon_critical_values(self) -> None:
        """The ADF coefficient/t statistic were independently frozen from SciPy OLS."""

        result = augmented_dickey_fuller(_ADF_VALUES, lag=1)

        self.assertEqual(result.status, "established")
        self.assertEqual(result.lag, 1)
        self.assertEqual(result.effective_sample_size, 18)
        self.assertIsNone(result.p_value)
        self.assertEqual(
            result.critical_value_method,
            MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD,
        )
        self.assertDecimalClose(result.adf_statistic, "-6.64775791282254")
        self.assertEqual(
            result.critical_values,
            (
                (Decimal("0.01"), Decimal("-3.859073285322359396433470507544582")),
                (Decimal("0.05"), Decimal("-3.042045692729766803840877914951989")),
                (Decimal("0.10"), Decimal("-2.660906419753086419753086419753087")),
            ),
        )
        self.assertEqual(
            result.rejections,
            ((Decimal("0.01"), True), (Decimal("0.05"), True), (Decimal("0.10"), True)),
        )
        self.assertEqual(result.decision, "reject_unit_root")
        assert result.regression is not None
        self.assertDecimalClose(result.regression.coefficients[1].estimate, "-1.58399641528393")
        self.assertDecimalClose(result.regression.coefficients[1].t_statistic, "-6.64775791282254")
        self.assertNotIn(
            "student_t_p_value_used_for_adf_decision",
            result.warnings,
        )

    def test_adf_critical_values_ignore_ambient_decimal_precision(self) -> None:
        with localcontext() as low_precision:
            low_precision.prec = 3
            low_result = augmented_dickey_fuller(
                _ADF_AMBIENT_CONTEXT_VALUES,
                lag=1,
                significance=Decimal("0.05"),
            )
        with localcontext() as high_precision:
            high_precision.prec = 50
            high_result = augmented_dickey_fuller(
                _ADF_AMBIENT_CONTEXT_VALUES,
                lag=1,
                significance=Decimal("0.05"),
            )

        self.assertEqual(low_result.adf_statistic, high_result.adf_statistic)
        self.assertEqual(low_result.critical_values, high_result.critical_values)
        self.assertEqual(low_result.rejections, high_result.rejections)
        self.assertEqual(low_result.decision, high_result.decision)
        self.assertEqual(low_result.decision, "fail_to_reject_unit_root")

    def test_adf_requires_contiguous_data_and_enough_degrees_of_freedom(self) -> None:
        missing = augmented_dickey_fuller((Decimal("1"), None, Decimal("3"), Decimal("4")))
        self.assertEqual(missing.status, "not_established")
        self.assertEqual(missing.reason, "missing_observations_prevent_contiguous_test")
        self.assertIsNone(missing.p_value)

        insufficient = augmented_dickey_fuller((Decimal("1"), Decimal("2"), Decimal("3")), lag=0)
        self.assertEqual(insufficient.status, "not_established")
        self.assertEqual(insufficient.reason, "insufficient_degrees_of_freedom")


    def test_fixed_lag_level_kpss_matches_independent_reference_vectors(self) -> None:
        result = kpss_level_stationarity(
            _ADF_VALUES,
            lag=2,
            significance=Decimal("0.05"),
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.sample_size, 20)
        self.assertEqual(result.lag, 2)
        self.assertEqual(
            result.critical_value_method,
            KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD,
        )
        self.assertDecimalClose(
            result.long_run_variance,
            "0.03374666666666666666666666666666667",
        )
        self.assertDecimalClose(result.partial_sum_squares, "2.644")
        self.assertDecimalClose(
            result.kpss_statistic,
            "0.1958711971552745951738443303032393",
        )
        self.assertIsNone(result.p_value)
        self.assertEqual(
            result.decision,
            "fail_to_reject_level_stationarity",
        )
        self.assertFalse(dict(result.rejections)[Decimal("0.05")])

        trending = kpss_level_stationarity(
            tuple(Decimal(index) for index in range(20)),
            lag=2,
            significance=Decimal("0.05"),
        )
        self.assertEqual(trending.status, "established")
        self.assertDecimalClose(
            trending.kpss_statistic,
            "0.770856619772595875891308537290422",
        )
        self.assertEqual(trending.decision, "reject_level_stationarity")
        self.assertTrue(dict(trending.rejections)[Decimal("0.05")])

    def test_kpss_is_precision_stable_and_fails_closed_at_boundaries(self) -> None:
        with localcontext() as context:
            context.prec = 8
            low_precision = kpss_level_stationarity(_ADF_VALUES, lag=2)
        with localcontext() as context:
            context.prec = 50
            high_precision = kpss_level_stationarity(_ADF_VALUES, lag=2)
        self.assertEqual(
            low_precision.kpss_statistic,
            high_precision.kpss_statistic,
        )
        self.assertEqual(low_precision.rejections, high_precision.rejections)

        cases = (
            (
                kpss_level_stationarity(
                    (Decimal("1"), None, Decimal("3")),
                    lag=1,
                ),
                "missing_observations_prevent_contiguous_test",
            ),
            (
                kpss_level_stationarity((Decimal("1"),)),
                "insufficient_observations_for_level_kpss",
            ),
            (
                kpss_level_stationarity(
                    (Decimal("2"), Decimal("2"), Decimal("2")),
                ),
                "zero_demeaned_variance",
            ),
            (
                kpss_level_stationarity(
                    (Decimal("1"), Decimal("2")),
                    lag=2,
                ),
                "kpss_lag_exceeds_complete_sample",
            ),
        )
        for result, reason in cases:
            self.assertEqual(result.status, "not_established")
            self.assertEqual(result.reason, reason)
            self.assertIsNone(result.kpss_statistic)
            self.assertIsNone(result.p_value)


    def test_fixed_break_chow_matches_independent_f_reference_vectors(self) -> None:
        predictors = tuple((Decimal(index),) for index in range(12))
        shifted = tuple(
            Decimal(value)
            for value in (
                "1.0", "3.2", "5.1", "7.4", "8.9", "11.0",
                "16.3", "18.8", "22.2", "24.6", "27.3", "29.5",
            )
        )
        result = chow_break_test(
            shifted,
            predictors,
            break_index=6,
            significance=Decimal("0.05"),
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.parameter_count, 2)
        self.assertEqual(result.numerator_degrees_of_freedom, 2)
        self.assertEqual(result.denominator_degrees_of_freedom, 8)
        self.assertEqual(result.inference_backend, F_INFERENCE_BACKEND)
        self.assertDecimalClose(
            result.pooled_residual_sum_squares,
            "11.565233100233103",
        )
        self.assertDecimalClose(
            result.pre_break_residual_sum_squares,
            "0.15485714285714308",
        )
        self.assertDecimalClose(
            result.post_break_residual_sum_squares,
            "0.4281904761904745",
        )
        self.assertDecimalClose(
            result.f_statistic,
            "75.34331757755497",
        )
        self.assertDecimalClose(
            result.p_value,
            "0.00000645949464775858",
            tolerance="2e-15",
        )
        self.assertEqual(result.decision, "reject_parameter_stability")
        self.assertEqual(result.pre_break_fit.status, "established")
        self.assertEqual(result.post_break_fit.status, "established")

        stable = tuple(
            Decimal(value)
            for value in (
                "1.0", "3.2", "5.1", "7.4", "8.9", "11.0",
                "13.3", "14.8", "17.2", "19.6", "21.3", "23.5",
            )
        )
        companion = chow_break_test(stable, predictors, break_index=6)
        self.assertDecimalClose(
            companion.f_statistic,
            "0.8286540114545575",
        )
        self.assertDecimalClose(
            companion.p_value,
            "0.4709075067740782",
        )
        self.assertEqual(
            companion.decision,
            "fail_to_reject_parameter_stability",
        )

    def test_fixed_break_chow_fails_closed_at_invalid_samples(self) -> None:
        predictors = tuple((Decimal(index),) for index in range(8))
        values = tuple(Decimal(index * 2 + 1) for index in range(8))

        outside = chow_break_test(values, predictors, break_index=8)
        self.assertEqual(outside.status, "not_established")
        self.assertEqual(outside.reason, "break_index_outside_sample")

        too_short = chow_break_test(values, predictors, break_index=2)
        self.assertEqual(too_short.status, "not_established")
        self.assertEqual(
            too_short.reason,
            "pre_break_insufficient_degrees_of_freedom",
        )

        missing = chow_break_test(
            (*values[:3], None, *values[4:]),
            predictors,
            break_index=4,
        )
        self.assertEqual(missing.status, "not_established")
        self.assertEqual(missing.reason, "missing_observations_rejected")

        rank_deficient = chow_break_test(
            values,
            tuple((Decimal("1"),) for _ in values),
            break_index=4,
        )
        self.assertEqual(rank_deficient.status, "not_established")
        self.assertEqual(
            rank_deficient.reason,
            "pooled_rank_deficient_design",
        )

        exact_segments = chow_break_test(
            tuple(
                Decimal(value)
                for value in ("1", "3", "5", "7", "14", "17", "20", "23")
            ),
            predictors,
            break_index=4,
        )
        self.assertEqual(exact_segments.status, "not_established")
        self.assertEqual(
            exact_segments.reason,
            "zero_unrestricted_residual_sum_squares",
        )




from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.tool_platform.econometrics import (
    engle_granger_cointegration,
    granger_causality,
    vector_autoregression,
)


_ENGLE_GRANGER_RETURNS_X = tuple(
    Decimal(value)
    for value in (
        "0", "0.01", "-0.01", "0.02", "0.01", "0.01", "-0.01", "0.02",
        "0.01", "-0.02", "0.01", "0.02", "0.01", "0.01", "-0.01", "0.02",
        "0.01", "0.02", "-0.01", "0.02", "0.01", "0.01", "0.02", "-0.01",
        "0.02", "0.01", "0.01", "0.02", "-0.01", "0.02",
    )
)
_ENGLE_GRANGER_RETURNS_Y = tuple(
    Decimal(value)
    for value in (
        "0", "0.006", "0", "0.019", "0.033", "0", "-0.002", "0.018",
        "0.034", "-0.049", "0.033", "0.017", "0.032", "0.001", "0",
        "0.019", "0.033", "0.017", "-0.002", "0.018", "0.034", "0.002",
        "0.05", "-0.034", "0.049", "0.001", "0.034", "0.019", "-0.001",
        "0.017",
    )
)
_ENGLE_GRANGER_CONTROL_Y = tuple(
    Decimal(value)
    for value in (
        "0", "-0.01", "0.02", "0.01", "-0.02", "0.01", "0.03", "-0.01",
        "0.02", "0.02", "-0.01", "0.02", "0.02", "-0.01", "0.03", "-0.01",
        "0.02", "0.02", "-0.01", "0.02", "0.02", "-0.01", "0.02", "0.01",
        "-0.02", "0.02", "0.01", "-0.02", "0.03", "0.01",
    )
)

_VAR_SERIES_0 = tuple(
    Decimal(value)
    for value in (
        "0.2", "0.18", "-0.016", "0.0352", "0.10056", "0.000168",
        "0.0600504", "-0.06198488", "0.031404536", "0.0294213608",
        "-0.05117359176", "0.084647922472", "-0.0146056232584",
        "0.02561831302248", "-0.082314506093256", "0.0453056481720232",
        "0.02359169445160696", "-0.042922491664517912",
        "0.0671232525006446264", "0.00013697575019338792",
        "0.040041092725058016376", "-0.0579876721824825950872",
        "0.04260369834525522147384", "0.042781109503576566442152",
        "-0.0671656671489270300673544", "0.06985029985532189097979368",
        "0.010955089956596567293938104", "0.0532865269869789701881814312",
        "-0.04401404190390630894354557064", "0.02679578742882810731693632881",
    )
)
_VAR_SERIES_1 = tuple(
    Decimal(value)
    for value in (
        "-0.1", "0.115", "0.22175", "0.0218375", "0.085379375",
        "0.06682084375", "0.0268480109375", "0.087754842734375",
        "-0.08074843731640625", "0.0265067462708984375",
        "0.071634843247724609375", "-0.05558884218406884765625",
        "0.1080535235551827880859375", "0.004598601119155697021484375",
        "0.04292521634889692425537109375", "-0.09923602609204336893615722656",
        "0.04370079442320887776596069336", "0.04097813888966813544149017334",
        "-0.04623958319242319133962745666", "0.09549486882744213460509313584",
        "-0.00600985340547508661672671604", "0.07253246546493054226541832099",
        "-0.04115640498887757025776541975", "0.04592404234624754568832264506",
        "-0.00215504633539803210209013874", "-0.02762957866043748358277377468",
        "0.06246536021191423643713118433", "-0.01507183348391435869086981552",
        "0.09152558956795353498723676264", "-0.03453053822633197885520454438",
    )
)


def _cumulative_levels(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    total = Decimal("0")
    levels: list[Decimal] = []
    for value in values:
        total += value
        levels.append(total)
    return tuple(levels)


class EconometricsV3KernelTests(unittest.TestCase):
    def assertDecimalClose(
        self, actual: Decimal | None, expected: str, tolerance: str = "3e-12"
    ) -> None:
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertLessEqual(abs(actual - Decimal(expected)), Decimal(tolerance))


    def test_engle_granger_matches_frozen_two_step_reference_vector(self) -> None:
        result = engle_granger_cointegration(
            (
                _cumulative_levels(_ENGLE_GRANGER_RETURNS_Y),
                _cumulative_levels(_ENGLE_GRANGER_RETURNS_X),
            ),
            lag=1,
            significance=Decimal("0.05"),
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.input_sample_size, 30)
        self.assertEqual(result.effective_sample_size, 28)
        self.assertEqual(result.first_stage_deterministic, "constant")
        self.assertEqual(result.residual_test_deterministic, "none")
        self.assertEqual(
            result.critical_value_method,
            "mackinnon_2010_tau_c_N2_finite_sample_polynomial",
        )
        self.assertIsNone(result.p_value)
        self.assertDecimalClose(
            result.cointegrating_intercept,
            "-0.0017157434402332361516",
            tolerance="5e-20",
        )
        self.assertDecimalClose(
            result.cointegrating_slope,
            "1.6957725947521865889",
            tolerance="5e-20",
        )
        self.assertDecimalClose(
            result.residual_adf_gamma,
            "-1.1450128731326673394",
            tolerance="5e-20",
        )
        self.assertDecimalClose(
            result.residual_adf_gamma_standard_error,
            "0.25041599185142551437",
            tolerance="5e-20",
        )
        self.assertDecimalClose(
            result.residual_adf_statistic,
            "-4.5724430962540751810",
            tolerance="5e-20",
        )
        assert result.residual_adf_regression is not None
        self.assertDecimalClose(
            result.residual_adf_regression.residual_sum_squares,
            "0.00004998254242385641048",
            tolerance="3e-23",
        )
        self.assertEqual(result.decision, "reject_no_cointegration")
        self.assertTrue(dict(result.rejections)[Decimal("0.05")])
        self.assertIn(
            "engle_granger_requires_i1_series_assumption_not_verified",
            result.warnings,
        )
        with self.assertRaises(FrozenInstanceError):
            result.status = "mutated"  # type: ignore[misc]

        control = engle_granger_cointegration(
            (
                _cumulative_levels(_ENGLE_GRANGER_CONTROL_Y),
                _cumulative_levels(_ENGLE_GRANGER_RETURNS_X),
            ),
            lag=1,
        )
        self.assertEqual(control.status, "established")
        self.assertDecimalClose(
            control.residual_adf_statistic,
            "-2.4501208430558570441",
            tolerance="5e-20",
        )
        self.assertEqual(control.decision, "fail_to_reject_no_cointegration")

    def test_engle_granger_fails_closed_for_missing_and_small_samples(self) -> None:
        missing_y = list(_cumulative_levels(_ENGLE_GRANGER_RETURNS_Y))
        missing_y[10] = None
        missing = engle_granger_cointegration(
            (tuple(missing_y), _cumulative_levels(_ENGLE_GRANGER_RETURNS_X)),
            lag=1,
        )
        self.assertEqual(missing.status, "not_established")
        self.assertEqual(
            missing.reason,
            "missing_observations_prevent_contiguous_test",
        )
        self.assertEqual(missing.excluded_count, 1)

        too_short = engle_granger_cointegration(
            (
                _cumulative_levels(_ENGLE_GRANGER_RETURNS_Y[:20]),
                _cumulative_levels(_ENGLE_GRANGER_RETURNS_X[:20]),
            ),
            lag=1,
        )
        self.assertEqual(too_short.status, "not_established")
        self.assertEqual(
            too_short.reason,
            "insufficient_observations_for_engle_granger",
        )

        with self.assertRaises(ResourceLimitError):
            engle_granger_cointegration(
                (
                    _cumulative_levels(_ENGLE_GRANGER_RETURNS_Y),
                    _cumulative_levels(_ENGLE_GRANGER_RETURNS_X),
                ),
                lag=5,
            )
        with self.assertRaises(ValidationError):
            engle_granger_cointegration(
                (_cumulative_levels(_ENGLE_GRANGER_RETURNS_Y),),
                lag=1,
            )

    def test_fixed_var_p1_matches_independent_frozen_reference(self) -> None:
        """Reference values were independently frozen with NumPy least squares."""

        result = vector_autoregression(
            (_VAR_SERIES_0, _VAR_SERIES_1),
            lag_order=1,
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.series_count, 2)
        self.assertEqual(result.effective_sample_size, 29)
        self.assertEqual(result.parameter_count, 3)
        self.assertEqual(result.degrees_of_freedom_residual, 26)
        self.assertEqual(
            result.coefficient_names,
            ("intercept", "lag_1_series_0", "lag_1_series_1"),
        )
        self.assertDecimalClose(result.intercepts[0], "0.023655096617003633")
        self.assertDecimalClose(result.intercepts[1], "0.006973395187042377")
        assert result.lag_coefficient_matrices is not None
        lag_one = result.lag_coefficient_matrices[0]
        self.assertDecimalClose(lag_one[0][0], "-0.11707598706218963")
        self.assertDecimalClose(lag_one[0][1], "-0.08408424657503215")
        self.assertDecimalClose(lag_one[1][0], "0.944874484902506")
        self.assertDecimalClose(lag_one[1][1], "0.06457538088069392")
        assert result.residual_covariance is not None
        self.assertDecimalClose(
            result.residual_covariance[0][0],
            "0.0035329419738617317",
        )
        self.assertDecimalClose(
            result.residual_covariance[0][1],
            "-0.0007153817939307785",
        )
        self.assertDecimalClose(
            result.residual_covariance[1][1],
            "0.0009457989949843621",
        )


    def test_fixed_var_p2_matches_independent_frozen_reference(self) -> None:
        """Reference values were independently frozen with NumPy least squares."""

        result = vector_autoregression(
            (_VAR_SERIES_0, _VAR_SERIES_1),
            lag_order=2,
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.effective_sample_size, 28)
        self.assertEqual(result.parameter_count, 5)
        self.assertEqual(result.degrees_of_freedom_residual, 23)
        assert result.lag_coefficient_matrices is not None
        self.assertEqual(len(result.lag_coefficient_matrices), 2)
        lag_one, lag_two = result.lag_coefficient_matrices
        self.assertDecimalClose(lag_one[0][0], "-0.41013422997028154")
        self.assertDecimalClose(lag_one[0][1], "-0.04250773837001167")
        self.assertDecimalClose(lag_one[1][0], "1.034884238851131")
        self.assertDecimalClose(lag_one[1][1], "-0.2881144719480779")
        self.assertDecimalClose(lag_two[0][0], "0.1444677859202853")
        self.assertDecimalClose(lag_two[0][1], "0.18319738689235296")
        self.assertDecimalClose(lag_two[1][0], "0.3861354120463534")
        self.assertDecimalClose(lag_two[1][1], "0.15779620952075593")
        assert result.residual_covariance is not None
        self.assertDecimalClose(
            result.residual_covariance[0][0],
            "0.0019779169291383285",
        )
        self.assertDecimalClose(
            result.residual_covariance[0][1],
            "-0.00016300168235395082",
        )
        self.assertDecimalClose(
            result.residual_covariance[1][1],
            "0.0005181768081487068",
        )

    def test_conditional_granger_matches_independent_f_reference(self) -> None:
        """The F statistic and tail were independently frozen with SciPy."""

        result = granger_causality(
            (_VAR_SERIES_0, _VAR_SERIES_1),
            lag_order=1,
            source_index=0,
            target_index=1,
            significance=Decimal("0.05"),
        )

        self.assertEqual(result.status, "established")
        self.assertEqual(result.restriction_count, 1)
        self.assertEqual(result.numerator_degrees_of_freedom, 1)
        self.assertEqual(result.denominator_degrees_of_freedom, 26)
        self.assertDecimalClose(
            result.restricted_residual_sum_squares,
            "0.11997260604561398",
        )
        self.assertDecimalClose(
            result.unrestricted_residual_sum_squares,
            "0.024590773869593414",
        )
        self.assertDecimalClose(result.f_statistic, "100.8478891200319")
        self.assertDecimalClose(
            result.p_value,
            "1.9415151100109854e-10",
            tolerance="5e-20",
        )
        self.assertEqual(result.decision, "reject_no_granger_causality")
        self.assertIn(
            "granger_is_predictive_precedence_not_structural_causality",
            result.warnings,
        )
        assert result.unrestricted_var is not None
        self.assertEqual(result.unrestricted_var.status, "established")

    def test_var_and_granger_fail_closed_on_invalid_boundaries(self) -> None:
        missing_series = list(_VAR_SERIES_1)
        missing_series[8] = None
        missing = vector_autoregression(
            (_VAR_SERIES_0, tuple(missing_series)),
            lag_order=1,
        )
        self.assertEqual(missing.status, "not_established")
        self.assertEqual(missing.reason, "missing_observations_prevent_contiguous_var")
        self.assertEqual(missing.excluded_count, 1)

        short = vector_autoregression(
            (_VAR_SERIES_0[:4], _VAR_SERIES_1[:4]),
            lag_order=1,
        )
        self.assertEqual(short.status, "not_established")
        self.assertEqual(short.reason, "insufficient_degrees_of_freedom")

        with self.assertRaises(ResourceLimitError):
            vector_autoregression(
                (_VAR_SERIES_0, _VAR_SERIES_1),
                lag_order=5,
            )
        with self.assertRaises(ValidationError):
            granger_causality(
                (_VAR_SERIES_0, _VAR_SERIES_1),
                lag_order=1,
                source_index=0,
                target_index=0,
            )
        with self.assertRaises(ValidationError):
            granger_causality(
                (_VAR_SERIES_0, _VAR_SERIES_1),
                lag_order=1,
                source_index=2,
                target_index=1,
            )


if __name__ == "__main__":
    unittest.main()
