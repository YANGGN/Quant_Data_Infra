from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal, localcontext

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.market_cross_sectional import (
    TOOL_NAME,
    invoke_stage10_cross_sectional_analytics_v21,
)
from tests.tool_platform.test_market_cross_sectional_performance_v2 import (
    MarketCrossSectionalPerformanceV2Tests,
    _diagnostic,
    _fields,
)


_CAPTURED_AT = "2026-08-20T00:00:00Z"


class MarketCrossSectionalAnalyticsV21Tests(unittest.TestCase):
    """Exercise the additive analytics kernel against the Stage 10 fixture."""

    def setUp(self) -> None:
        self.fixture = MarketCrossSectionalPerformanceV2Tests(methodName="runTest")
        self.fixture.setUp()
        self.stores = self.fixture.stores
        self.registry = self.fixture.registry
        self._append_history()
        self.baseline = mutation_fingerprint(self.stores)

    def tearDown(self) -> None:
        self.assertEqual(
            self.baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        self.fixture.temporary.cleanup()

    def _append_history(self) -> None:
        additions = {
            "AAA": ((3, "2026-01-04", "99"), (4, "2026-01-05", "108.9"), (5, "2026-01-06", "108.9")),
            "BBB": ((3, "2026-01-04", "60.5"), (4, "2026-01-05", "60.5"), (5, "2026-01-06", "66.55")),
            "CCC": ((3, "2026-01-04", "99"), (4, "2026-01-05", "99"), (5, "2026-01-06", "108.9")),
            "CUT": ((4, "2026-01-05", "13.2"), (5, "2026-01-06", "14.52")),
            "ZERO": ((3, "2026-01-04", "11"),),
        }
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            for ticker, rows in additions.items():
                instrument_id = f"cross-section-{ticker.lower()}"
                capture_id = f"cross-section-capture-{ticker.lower()}"
                artifact_id = f"cross-section-artifact-{ticker.lower()}"
                snapshot_id = f"cross-section-snapshot-{ticker.lower()}"
                run_id = f"cross-section-capture-run-{ticker.lower()}"
                for source_row, trade_date, close in rows:
                    version_id = f"cross-section-analytics-v21-{ticker.lower()}-{source_row}"
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_price_versions(
                            version_id, instrument_id, trade_date, provider,
                            price_variant, currency_segment, open_value,
                            high_value, low_value, close_value, volume,
                            available_at, available_precision, captured_at,
                            captured_precision, correction_sequence,
                            supersedes_version_id, capture_id, artifact_id,
                            snapshot_id, run_id, source_row
                        ) VALUES (
                            ?, ?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native',
                            ?, ?, ?, ?, 1, ?, 'datetime', ?, 'datetime', 1, NULL,
                            ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            version_id,
                            instrument_id,
                            trade_date,
                            close,
                            close,
                            close,
                            close,
                            _CAPTURED_AT,
                            _CAPTURED_AT,
                            capture_id,
                            artifact_id,
                            snapshot_id,
                            run_id,
                            source_row,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_prices(
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', 'fmp_full_eod_v1',
                                  'provider_native', ?)
                        """,
                        (instrument_id, trade_date, version_id),
                    )

    def _context(self):
        return replace(
            self.fixture._context(),
            request_id="market-cross-sectional-analytics-v21-test",
            tool_version="2.1.0",
            operation_graph_id="tool_platform.market.cross_sectional_performance.v2_1",
            operation_version="2.1.0",
        )

    def _call(
        self,
        *,
        tickers: tuple[str, ...],
        benchmark_ticker: str,
        window: int,
        start_date: str | None = None,
        end_date: str | None = None,
        mode: str = "latest",
        as_of: str | None = None,
        date_only_policy: str = "completed_date",
        limit: int = 10,
    ):
        return invoke_stage10_cross_sectional_analytics_v21(
            TOOL_NAME,
            {
                "tickers": tickers,
                "benchmark_ticker": benchmark_ticker,
                "window": window,
                "start_date": start_date,
                "end_date": end_date,
                "mode": mode,
                "as_of": as_of,
                "date_only_policy": date_only_policy,
                "limit": limit,
            },
            self._context(),
            self.registry,
        )

    def test_breadth_relative_return_volatility_and_beta(self) -> None:
        result = self._call(
            tickers=("bbb", " aaa ", "ccc"),
            benchmark_ticker="aaa",
            window=3,
            limit=5,
        )
        rows = _fields(result)
        by_ticker = {str(row["ticker"]): row for row in rows}
        self.assertEqual(tuple(row["ticker"] for row in rows), ("BBB", "AAA", "CCC"))
        self.assertEqual(by_ticker["BBB"]["rank"], 1)
        self.assertEqual(by_ticker["AAA"]["rank"], 2)
        self.assertEqual(by_ticker["CCC"]["rank"], 3)
        self.assertEqual(
            by_ticker["BBB"]["benchmark_relative_cumulative_return"],
            Decimal("0.242"),
        )
        self.assertEqual(
            by_ticker["BBB"]["benchmark_relative_cumulative_return_status"],
            "computed",
        )
        self.assertEqual(by_ticker["AAA"]["rolling_beta"], Decimal("1"))
        self.assertEqual(by_ticker["BBB"]["rolling_beta"], Decimal("-0.5"))
        self.assertEqual(by_ticker["BBB"]["rolling_beta_status"], "computed")
        self.assertEqual(by_ticker["BBB"]["rolling_beta_observation_count"], 3)
        with localcontext() as decimal_context:
            decimal_context.prec = 34
            expected_volatility = Decimal("0.1") * Decimal(252).sqrt()
        self.assertEqual(by_ticker["AAA"]["realized_volatility"], expected_volatility)
        self.assertEqual(by_ticker["AAA"]["realized_volatility_status"], "computed")
        diagnostic = _diagnostic(result)
        self.assertEqual(diagnostic["breadth_eligible_count"], 3)
        self.assertEqual(diagnostic["breadth_positive_count"], 3)
        self.assertEqual(diagnostic["breadth_negative_count"], 0)
        self.assertEqual(diagnostic["breadth_unchanged_count"], 0)
        self.assertEqual(diagnostic["breadth_positive_fraction"], Decimal("1"))
        self.assertEqual(diagnostic["benchmark_ticker"], "AAA")
        self.assertEqual(diagnostic["window"], 3)
        self.assertEqual(len(result.lineage), 3)

    def test_explicit_metric_statuses_for_short_history_and_zero_variance(self) -> None:
        result = self._call(
            tickers=("AAA", "CUT", "ZERO", "TINY"),
            benchmark_ticker="CUT",
            window=2,
            limit=6,
        )
        by_ticker = {str(row["ticker"]): row for row in _fields(result)}
        self.assertEqual(by_ticker["CUT"]["realized_volatility"], Decimal("0"))
        self.assertEqual(by_ticker["CUT"]["realized_volatility_status"], "zero_variance")
        self.assertIsNone(by_ticker["AAA"]["rolling_beta"])
        self.assertEqual(by_ticker["AAA"]["rolling_beta_status"], "zero_variance")
        self.assertIsNone(by_ticker["ZERO"]["realized_volatility"])
        self.assertEqual(by_ticker["ZERO"]["realized_volatility_status"], "zero_base_price")
        self.assertIsNone(by_ticker["TINY"]["realized_volatility"])
        self.assertEqual(by_ticker["TINY"]["realized_volatility_status"], "insufficient_history")
        self.assertEqual(by_ticker["TINY"]["rolling_beta_status"], "insufficient_history")
        self.assertEqual(
            by_ticker["ZERO"]["benchmark_relative_cumulative_return_status"],
            "source_zero_base_price",
        )

    def test_validation_and_as_of_lineage_are_preserved(self) -> None:
        with self.assertRaises(ValidationError):
            self._call(
                tickers=("AAA", "BBB"),
                benchmark_ticker="MISSING",
                window=2,
            )
        with self.assertRaises(ResourceLimitError):
            self._call(
                tickers=("AAA", "BBB"),
                benchmark_ticker="AAA",
                window=1,
            )
        result = self._call(
            tickers=("AAA", "BBB"),
            benchmark_ticker="AAA",
            window=2,
            mode="as_of",
            as_of="2026-08-21T00:00:00Z",
            limit=5,
        )
        diagnostic = _diagnostic(result)
        self.assertEqual(diagnostic["point_in_time_status"], "safe")
        self.assertEqual(diagnostic["actual_mode"], "as_of")
        self.assertEqual(diagnostic["cutoff"], "2026-08-21T00:00:00Z")
        self.assertIn(
            "point_in_time_safe_only_for_retained_local_captures",
            {warning.code for warning in result.warnings},
        )
        self.assertEqual(len(result.lineage), 2)


if __name__ == "__main__":
    unittest.main()
