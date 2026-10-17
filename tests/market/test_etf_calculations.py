from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from quant_data.errors import ValidationError
from quant_data.market.etf_calculations import (
    CALENDAR_COVERAGE_END,
    CALENDAR_COVERAGE_START,
    CALENDAR_ID,
    ETF_SYMBOLS,
    FEATURE_NAMES,
    calculate_features,
    completed_month_end,
    month_end,
    sessions,
)


class EtfCalculationTests(unittest.TestCase):
    endpoint = date(2025, 3, 31)

    @staticmethod
    def _prices_through(endpoint: date) -> dict[date, Decimal]:
        return {
            day: Decimal("100")
            for day in sessions(CALENDAR_COVERAGE_START, endpoint)
        }

    @staticmethod
    def _lag_end(endpoint: date, lag: int) -> date:
        ordinal = endpoint.year * 12 + endpoint.month - 1 - lag
        result = month_end(ordinal // 12, ordinal % 12 + 1)
        assert result is not None
        return result

    def test_public_universe_features_and_calendar_coverage(self) -> None:
        self.assertEqual(
            ETF_SYMBOLS,
            (
                "SPY",
                "QQQ",
                "DIA",
                "IWM",
                "VEA",
                "VWO",
                "IEF",
                "TLT",
                "TIP",
                "LQD",
                "HYG",
                "GLD",
                "SLV",
                "PDBC",
                "XLB",
                "XLC",
                "XLE",
                "XLF",
                "XLI",
                "XLK",
                "XLP",
                "XLRE",
                "XLU",
                "XLV",
                "XLY",
            ),
        )
        self.assertEqual(
            FEATURE_NAMES,
            (
                "split_adjusted_price",
                "ten_month_sma",
                "return_12_to_1_month",
                "return_3_month",
                "return_6_month",
                "return_12_month",
                "realized_volatility_21",
                "realized_volatility_63",
                "realized_volatility_252",
            ),
        )
        self.assertEqual(CALENDAR_ID, "US_cash_equity_2024_2026")
        self.assertEqual(CALENDAR_COVERAGE_START, date(2024, 1, 1))
        self.assertEqual(CALENDAR_COVERAGE_END, date(2026, 12, 31))

    def test_calendar_uses_observed_holidays_and_validates_bounds(self) -> None:
        march_sessions = sessions(date(2024, 3, 1), date(2024, 3, 31))
        self.assertNotIn(date(2024, 3, 29), march_sessions)
        self.assertEqual(month_end(2024, 3), date(2024, 3, 28))
        self.assertNotIn(
            date(2025, 1, 9),
            sessions(date(2025, 1, 1), date(2025, 1, 31)),
        )
        self.assertEqual(month_end(2026, 11), date(2026, 11, 30))
        self.assertIsNone(month_end(2023, 12))
        self.assertIsNone(month_end(2027, 1))
        with self.assertRaises(ValidationError):
            sessions(date(2023, 12, 29), date(2024, 1, 2))
        with self.assertRaises(ValidationError):
            sessions(date(2024, 1, 3), date(2024, 1, 2))

    def test_completed_month_end_honors_dst_and_early_close_boundaries(self) -> None:
        self.assertEqual(
            completed_month_end(datetime(2024, 3, 28, 19, 59, tzinfo=timezone.utc)),
            date(2024, 2, 29),
        )
        self.assertEqual(
            completed_month_end(datetime(2024, 3, 28, 20, 0, tzinfo=timezone.utc)),
            date(2024, 3, 28),
        )
        self.assertEqual(
            completed_month_end(datetime(2024, 11, 29, 17, 59, tzinfo=timezone.utc)),
            date(2024, 10, 31),
        )
        self.assertEqual(
            completed_month_end(datetime(2024, 11, 29, 18, 0, tzinfo=timezone.utc)),
            date(2024, 11, 29),
        )
        self.assertIsNone(
            completed_month_end(datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc))
        )
        self.assertIsNone(
            completed_month_end(datetime(1, 1, 1, tzinfo=timezone.utc))
        )
        with self.assertRaises(ValidationError):
            completed_month_end(datetime(2024, 3, 28, 16, 0))

    def test_unestablished_or_dividend_adjusted_prices_fail_closed(self) -> None:
        for adjustment_status in (
            "not_established",
            "dividend_adjusted",
            "split_adjusted_price_excluding_distributions",
        ):
            values, reasons = calculate_features(
                {},
                self.endpoint,
                adjustment_status=adjustment_status,
            )
            self.assertEqual(set(values), set(FEATURE_NAMES))
            self.assertEqual(set(reasons), set(FEATURE_NAMES))
            self.assertTrue(all(value is None for value in values.values()))
            self.assertEqual(
                set(reasons.values()),
                {"adjustment_basis_not_established"},
            )

    def test_complete_constant_history_has_all_features_and_zero_volatility(self) -> None:
        values, reasons = calculate_features(
            self._prices_through(self.endpoint),
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertEqual(set(values), set(FEATURE_NAMES))
        self.assertEqual(set(reasons), set(FEATURE_NAMES))
        for name in FEATURE_NAMES[:2]:
            self.assertEqual(values[name], Decimal("100"))
            self.assertEqual(reasons[name], "computed")
        for name in FEATURE_NAMES[2:6]:
            self.assertEqual(values[name], Decimal(0))
            self.assertEqual(reasons[name], "computed")
        for name in FEATURE_NAMES[6:]:
            self.assertEqual(values[name], Decimal(0))
            self.assertEqual(reasons[name], "zero_variance")

    def test_nonconstant_monthly_endpoints_use_exact_lags_and_ignore_future(self) -> None:
        endpoint = date(2026, 1, 30)
        prices = {
            self._lag_end(endpoint, lag): Decimal(220 - 10 * lag)
            for lag in range(13)
        }
        values, _ = calculate_features(
            prices, endpoint, adjustment_status="split_adjusted_excluding_distributions",
        )
        expected = {
            "split_adjusted_price": Decimal("220"),
            "ten_month_sma": Decimal("175"),
            "return_12_to_1_month": Decimal("1.1"),
            "return_3_month": Decimal("0.157894736842105263157894736842105"),
            "return_6_month": Decimal("0.375"),
            "return_12_month": Decimal("1.2"),
        }
        self.assertEqual({name: values[name] for name in expected}, expected)
        prices[date(2026, 2, 27)] = Decimal("9999")
        self.assertEqual(calculate_features(
            prices, endpoint, adjustment_status="split_adjusted_excluding_distributions",
        )[0], values)
        prices[endpoint] = Decimal("330")
        self.assertEqual(calculate_features(
            prices, endpoint, adjustment_status="split_adjusted_excluding_distributions",
        )[0]["return_12_to_1_month"], Decimal("1.1"))

    def test_realized_volatility_uses_sample_returns_and_sqrt_252(self) -> None:
        prices = self._prices_through(self.endpoint)
        trailing = sessions(CALENDAR_COVERAGE_START, self.endpoint)[-22:]
        prices[trailing[0]] = Decimal("100")
        prices[trailing[1]] = Decimal("101")
        for day in trailing[2:]:
            prices[day] = Decimal("101")
        values, reasons = calculate_features(
            prices,
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        expected_21 = Decimal("0.03464101615137754587054892683011743")
        self.assertEqual(values["realized_volatility_21"], expected_21)
        self.assertEqual(values["realized_volatility_63"], Decimal("0.02000000000000000000000000000000001"))
        self.assertEqual(values["realized_volatility_252"], Decimal("0.009999999999999999999999999999999994"))
        self.assertEqual(reasons["realized_volatility_21"], "computed")

    def test_missing_and_invalid_inputs_do_not_fill_or_skip_sessions(self) -> None:
        prices = self._prices_through(self.endpoint)
        prices.pop(self.endpoint)
        values, reasons = calculate_features(
            prices,
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertIsNone(values["split_adjusted_price"])
        self.assertEqual(reasons["split_adjusted_price"], "missing_endpoint_price")
        self.assertIsNone(values["ten_month_sma"])
        self.assertEqual(reasons["ten_month_sma"], "missing_endpoint_price")
        self.assertEqual(values["return_12_to_1_month"], Decimal(0))
        self.assertEqual(reasons["return_12_to_1_month"], "computed")

        prices = self._prices_through(self.endpoint)
        prices.pop(sessions(CALENDAR_COVERAGE_START, self.endpoint)[-10])
        values, reasons = calculate_features(
            prices,
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertIsNone(values["realized_volatility_21"])
        self.assertEqual(reasons["realized_volatility_21"], "missing_daily_session")

        prices = self._prices_through(self.endpoint)
        prices[self._lag_end(self.endpoint, 3)] = Decimal("NaN")
        values, reasons = calculate_features(
            prices,
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertIsNone(values["return_3_month"])
        self.assertEqual(reasons["return_3_month"], "invalid_price")

    def test_minimum_history_and_missing_monthly_endpoint_are_explicit(self) -> None:
        short_endpoint = date(2024, 1, 31)
        short_prices = self._prices_through(short_endpoint)
        values, reasons = calculate_features(
            short_prices,
            short_endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertIsNone(values["realized_volatility_21"])
        self.assertEqual(reasons["realized_volatility_21"], "insufficient_history")
        self.assertIsNone(values["ten_month_sma"])
        self.assertEqual(reasons["ten_month_sma"], "insufficient_history")

        prices = self._prices_through(self.endpoint)
        prices.pop(self._lag_end(self.endpoint, 3))
        values, reasons = calculate_features(
            prices,
            self.endpoint,
            adjustment_status="split_adjusted_excluding_distributions",
        )
        self.assertIsNone(values["return_3_month"])
        self.assertEqual(reasons["return_3_month"], "missing_monthly_endpoint")


if __name__ == "__main__":
    unittest.main()
