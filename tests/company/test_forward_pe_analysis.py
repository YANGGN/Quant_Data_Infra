"""Known arithmetic and causal-window checks for the P/E analysis primitive."""
from math import isclose
from statistics import fmean, pstdev
import unittest

from quant_data.company.forward_pe_analysis import rolling_statistics


def days(values):
    return [[f"2026-01-{i+1:02d}", 10, 1, value, "available", "w"] for i, value in enumerate(values)]


class RollingPeTests(unittest.TestCase):
    def calculate(self, values, **options):
        return rolling_statistics(days(values), window_sessions=options.pop("window_sessions", 5), min_observations=options.pop("min_observations", 5), **options)

    def test_known_winsorized_and_raw_population_scores(self):
        result = self.calculate([1, 2, 3, 4, 100], winsor_tail_pct=5)[-1]
        expected = [1.2, 2, 3, 4, 80.8]
        self.assertAlmostEqual(result["lower_bound"], 1.2)
        self.assertAlmostEqual(result["upper_bound"], 80.8)
        self.assertAlmostEqual(result["rolling_mean"], fmean(expected))
        self.assertAlmostEqual(result["rolling_std"], pstdev(expected))
        self.assertAlmostEqual(result["z_score"], (80.8-fmean(expected))/pstdev(expected))
        self.assertAlmostEqual(result["raw_z_score"], (100-22)/pstdev([1,2,3,4,100]))
        self.assertEqual(result["raw_pe"], 100)
        self.assertTrue(result["was_clipped"])
        self.assertEqual(result["clipped_count"], 2)

    def test_future_extremes_cannot_change_prior_scores(self):
        base = [1, 2, 3, 4, 5, 6, 7]
        prior = self.calculate(base)
        after = self.calculate(base + [1000000000, -1000000000])
        self.assertEqual(prior, after[:len(prior)])
        self.assertEqual(prior[-1]["window_start"], "2026-01-03")

    def test_missing_values_occupy_sessions_without_fill(self):
        result = self.calculate([1, 2, None, 4, 5, None, 7], min_observations=4)
        self.assertEqual(result[2]["z_score_reason"], "missing_pe")
        self.assertIsNone(result[5]["winsorized_pe"])
        self.assertEqual(result[4]["valid_count"], 4)
        self.assertIsNotNone(result[4]["z_score"])
        self.assertEqual(result[6]["valid_count"], 3)
        self.assertEqual(result[6]["z_score_reason"], "insufficient_history")

    def test_no_caps_preserves_negative_values_and_matches_raw_score(self):
        result = self.calculate([-3, -2, -1, 1, 2], winsor_tail_pct=0)[-1]
        self.assertEqual(result["negative_count"], 3)
        self.assertEqual(result["winsorized_pe"], 2)
        self.assertEqual(result["raw_z_score"], result["z_score"])
        self.assertIsNone(result["lower_bound"])
        self.assertFalse(result["was_clipped"])

    def test_constant_or_warmup_windows_never_become_zero_scores(self):
        result = self.calculate([7, 7, 7, 7, 7])
        self.assertEqual(result[0]["z_score_reason"], "insufficient_history")
        self.assertEqual(result[-1]["z_score_reason"], "zero_variance")
        self.assertIsNone(result[-1]["z_score"])

    def test_extreme_finite_values_have_finite_scores(self):
        result = self.calculate([-1e300, -2e299, 0, 2e299, 1e300])[-1]
        self.assertTrue(isclose(result["z_score"], 1.541713, abs_tol=.1))

    def test_input_values_are_unchanged_and_ordering_is_checked(self):
        from quant_data.errors import ValidationError
        source = days([1,2,3,4,100]); before = [r[:] for r in source]
        rolling_statistics(source, window_sessions=5, min_observations=5)
        self.assertEqual(source,before)
        with self.assertRaises(ValidationError):
            rolling_statistics(source[::-1],window_sessions=5,min_observations=5)
