"""Frozen vectors and hostile boundaries for pure general-statistics kernels."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import unittest

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.tool_platform.general_statistics import (
    BOOTSTRAP_METHOD,
    BOOTSTRAP_PRNG,
    TYPE_7_QUANTILE_METHOD,
    bootstrap_confidence_interval,
    covariance_correlation_matrix,
    distribution_diagnostics,
    principal_component_analysis,
    type7_quantile,
)


def _values(*values: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in values)


def _rows(*rows: tuple[str, ...]) -> tuple[tuple[Decimal, ...], ...]:
    return tuple(_values(*row) for row in rows)


_DISTRIBUTION_VECTOR = _values("1", "2", "3", "4")
_COVARIANCE_VECTOR = _rows(
    ("2", "1"),
    ("4", "3"),
    ("6", "2"),
    ("8", "4"),
)
_PCA_DIAGONAL_VECTOR = _rows(
    ("-2", "0"),
    ("2", "0"),
    ("0", "-1"),
    ("0", "1"),
)
_PCA_NONDIAGONAL_VECTOR = _rows(
    ("-2", "-2", "-1"),
    ("-1", "0", "2"),
    ("1", "1", "0"),
    ("2", "3", "-1"),
)


class GeneralStatisticsKernelTests(unittest.TestCase):
    def assertDecimalClose(
        self, actual: Decimal | None, expected: str, tolerance: str = "2e-42"
    ) -> None:
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertLessEqual(abs(actual - Decimal(expected)), Decimal(tolerance))

    def test_distribution_matches_hand_derived_population_moment_vector(self) -> None:
        """The frozen vector is hand-derived from the type-7/moment definitions."""

        result = distribution_diagnostics(
            _DISTRIBUTION_VECTOR,
            quantile_probabilities=_values("0.25", "0.5", "0.75"),
        )

        self.assertEqual(result.status, "established")
        self.assertIsNone(result.reason)
        self.assertEqual(result.quantile_method, TYPE_7_QUANTILE_METHOD)
        self.assertEqual(result.sample_size, 4)
        self.assertEqual(result.mean, Decimal("2.5"))
        self.assertEqual(result.median, Decimal("2.5"))
        self.assertEqual(result.raw_median_absolute_deviation, Decimal("1"))
        self.assertEqual(result.population_variance, Decimal("1.25"))
        self.assertEqual(result.population_skewness, Decimal("0"))
        self.assertEqual(result.population_excess_kurtosis, Decimal("-1.36"))
        self.assertDecimalClose(result.jarque_bera_statistic, "0.30826666666666666666666666666666666666666666666667")
        self.assertEqual(
            tuple((item.probability, item.value) for item in result.quantiles),
            (
                (Decimal("0.25"), Decimal("1.75")),
                (Decimal("0.5"), Decimal("2.5")),
                (Decimal("0.75"), Decimal("3.25")),
            ),
        )

    def test_type7_quantile_and_distribution_ignore_ambient_decimal_context(self) -> None:
        baseline = distribution_diagnostics(_DISTRIBUTION_VECTOR)
        with localcontext() as context:
            context.prec = 9
            self.assertEqual(type7_quantile(_DISTRIBUTION_VECTOR, Decimal("0.25")), Decimal("1.75"))
            self.assertEqual(distribution_diagnostics(_DISTRIBUTION_VECTOR), baseline)

    def test_distribution_has_explicit_insufficient_and_zero_variance_states(self) -> None:
        empty = distribution_diagnostics(())
        singleton = distribution_diagnostics(_values("7"))
        constant = distribution_diagnostics(_values("7", "7", "7"))

        self.assertEqual((empty.status, empty.reason, empty.median), ("not_established", "insufficient_sample", None))
        self.assertEqual((singleton.status, singleton.reason, singleton.median), ("not_established", "insufficient_sample", Decimal("7")))
        self.assertEqual((constant.status, constant.reason), ("not_established", "zero_variance"))
        self.assertEqual(constant.population_variance, Decimal("0"))
        self.assertIsNone(constant.population_skewness)
        self.assertIsNone(constant.jarque_bera_statistic)

    def test_distribution_rejects_non_decimal_and_non_finite_inputs(self) -> None:
        with self.assertRaises(ValidationError):
            distribution_diagnostics((Decimal("1"), 2))  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            distribution_diagnostics((Decimal("1"), 2.0))  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            distribution_diagnostics((Decimal("NaN"),))
        with self.assertRaises(ValidationError):
            type7_quantile(_DISTRIBUTION_VECTOR, Decimal("1.01"))

    def test_bootstrap_is_seeded_deterministic_and_uses_frozen_percentile_bounds(self) -> None:
        first = bootstrap_confidence_interval(
            _DISTRIBUTION_VECTOR,
            statistic="mean",
            seed=12345,
            replicates=200,
            confidence_level=Decimal("0.90"),
        )
        second = bootstrap_confidence_interval(
            _DISTRIBUTION_VECTOR,
            statistic="mean",
            seed=12345,
            replicates=200,
            confidence_level=Decimal("0.90"),
        )
        changed_seed = bootstrap_confidence_interval(
            _DISTRIBUTION_VECTOR,
            statistic="mean",
            seed=54321,
            replicates=200,
            confidence_level=Decimal("0.90"),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.status, "established")
        self.assertEqual(first.method, BOOTSTRAP_METHOD)
        self.assertEqual(first.prng, BOOTSTRAP_PRNG)
        self.assertEqual(first.quantile_method, TYPE_7_QUANTILE_METHOD)
        self.assertEqual(first.point_estimate, Decimal("2.5"))
        self.assertEqual(first.lower_bound, Decimal("1.7375"))
        self.assertEqual(first.upper_bound, Decimal("3.2625"))
        self.assertNotEqual(
            (first.lower_bound, first.upper_bound),
            (changed_seed.lower_bound, changed_seed.upper_bound),
        )

    def test_bootstrap_reports_boundaries_and_rejects_invalid_controls(self) -> None:
        empty = bootstrap_confidence_interval(
            (), statistic="median", seed=0, replicates=10
        )
        self.assertEqual((empty.status, empty.reason), ("not_established", "insufficient_sample"))
        with self.assertRaises(ValidationError):
            bootstrap_confidence_interval(_DISTRIBUTION_VECTOR, statistic="mean", seed=True)
        with self.assertRaises(ValidationError):
            bootstrap_confidence_interval(_DISTRIBUTION_VECTOR, statistic="mean", seed=-1)
        with self.assertRaises(ValidationError):
            bootstrap_confidence_interval(_DISTRIBUTION_VECTOR, statistic="mean", seed=1, replicates=0)
        with self.assertRaises(ResourceLimitError):
            bootstrap_confidence_interval(_DISTRIBUTION_VECTOR, statistic="mean", seed=1, replicates=10_001)
        with self.assertRaises(ValidationError):
            bootstrap_confidence_interval(_DISTRIBUTION_VECTOR, statistic="mean", seed=1, confidence_level=Decimal("1"))

    def test_covariance_and_correlation_match_frozen_n_minus_one_vector(self) -> None:
        """Reference values are hand-derived with means (5, 2.5) and n-1=3."""

        result = covariance_correlation_matrix(_COVARIANCE_VECTOR)

        self.assertEqual(result.status, "established")
        self.assertEqual(result.sample_denominator, 3)
        self.assertEqual(result.means, (Decimal("5"), Decimal("2.5")))
        assert result.covariance is not None
        assert result.correlation is not None
        self.assertDecimalClose(result.covariance[0][0], "6.6666666666666666666666666666666666666666666666667")
        self.assertDecimalClose(result.covariance[0][1], "2.6666666666666666666666666666666666666666666666667")
        self.assertDecimalClose(result.covariance[1][1], "1.6666666666666666666666666666666666666666666666667")
        self.assertEqual(result.correlation[0][0], Decimal("1"))
        self.assertEqual(result.correlation[1][1], Decimal("1"))
        self.assertDecimalClose(result.correlation[0][1], "0.8")
        self.assertEqual(result.covariance[0][1], result.covariance[1][0])
        self.assertEqual(result.correlation[0][1], result.correlation[1][0])

    def test_covariance_explicitly_handles_insufficient_and_zero_variance_rows(self) -> None:
        insufficient = covariance_correlation_matrix(_rows(("1", "2")))
        zero_variance = covariance_correlation_matrix(
            _rows(("1", "2"), ("1", "3"), ("1", "4"))
        )

        self.assertEqual((insufficient.status, insufficient.reason), ("not_established", "insufficient_sample"))
        self.assertEqual((zero_variance.status, zero_variance.reason), ("not_established", "zero_variance"))
        self.assertEqual(zero_variance.zero_variance_indices, (0,))
        self.assertIsNotNone(zero_variance.covariance)
        self.assertIsNone(zero_variance.correlation)
        with self.assertRaises(ValidationError):
            covariance_correlation_matrix(((Decimal("1"), None), (Decimal("2"), Decimal("3"))))  # type: ignore[arg-type]

    def test_covariance_pca_has_descending_order_canonical_signs_and_scores(self) -> None:
        result = principal_component_analysis(_PCA_DIAGONAL_VECTOR)

        self.assertEqual(result.status, "established")
        self.assertEqual(result.method, "covariance")
        self.assertEqual(result.component_count, 2)
        self.assertEqual(result.eigenvectors, ((Decimal("1"), Decimal("0")), (Decimal("0"), Decimal("1"))))
        assert result.eigenvalues is not None
        assert result.explained_variance_ratios is not None
        assert result.scores is not None
        self.assertDecimalClose(result.eigenvalues[0], "2.6666666666666666666666666666666666666666666666667")
        self.assertDecimalClose(result.eigenvalues[1], "0.66666666666666666666666666666666666666666666666667")
        self.assertDecimalClose(result.explained_variance_ratios[0], "0.8")
        self.assertDecimalClose(result.explained_variance_ratios[1], "0.2")
        self.assertEqual(
            result.scores,
            (
                (Decimal("-2"), Decimal("0")),
                (Decimal("2"), Decimal("0")),
                (Decimal("0"), Decimal("-1")),
                (Decimal("0"), Decimal("1")),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            result.status = "mutated"  # type: ignore[misc]

    def test_correlation_pca_tie_order_and_rotated_component_sign_are_deterministic(self) -> None:
        tied = principal_component_analysis(_PCA_DIAGONAL_VECTOR, method="correlation")
        rotated = principal_component_analysis(
            _rows(("-2", "-2"), ("-1", "-1"), ("1", "1"), ("2", "2"))
        )

        self.assertEqual(tied.status, "established")
        self.assertEqual(tied.eigenvalues, (Decimal("1"), Decimal("1")))
        self.assertEqual(tied.eigenvectors, ((Decimal("1"), Decimal("0")), (Decimal("0"), Decimal("1"))))
        self.assertEqual(rotated.status, "established")
        assert rotated.eigenvectors is not None
        self.assertGreater(rotated.eigenvectors[0][0], Decimal("0"))
        self.assertGreater(rotated.eigenvectors[0][1], Decimal("0"))
        self.assertDecimalClose(rotated.eigenvectors[0][0], "0.70710678118654752440084436210484903928483593768847")
        self.assertDecimalClose(rotated.eigenvectors[0][1], "0.70710678118654752440084436210484903928483593768847")

    def test_pca_has_explicit_zero_variance_and_nonconvergence_states(self) -> None:
        zero_variance = principal_component_analysis(
            _rows(("1", "2"), ("1", "3"), ("1", "4"))
        )
        nonconverged = principal_component_analysis(
            _PCA_NONDIAGONAL_VECTOR, max_rotations=1
        )
        one_feature = principal_component_analysis(
            _rows(("1",), ("2",), ("3",))
        )

        self.assertEqual((zero_variance.status, zero_variance.reason), ("not_established", "zero_variance"))
        self.assertEqual(zero_variance.zero_variance_indices, (0,))
        self.assertEqual((nonconverged.status, nonconverged.reason), ("not_established", "jacobi_nonconvergence"))
        self.assertEqual(one_feature.status, "established")
        self.assertEqual(one_feature.eigenvectors, ((Decimal("1"),),))


if __name__ == "__main__":
    unittest.main()
