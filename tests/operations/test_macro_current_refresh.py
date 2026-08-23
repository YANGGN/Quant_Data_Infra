from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
import unittest
from unittest.mock import patch

from quant_data.errors import StoreUnavailableError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.operations import macro_current_refresh as operation


FIXED_NOW = datetime(2026, 8, 20, 0, 30, tzinfo=timezone.utc)
EXPECTED_SOURCES = (
    "fmp_treasury_curve",
    "nyfed_overnight_rates",
    "nyfed_repo_facilities",
    "nyfed_soma",
    "federal_reserve_h41",
    "chicagofed_financial_conditions",
    "bis_credit_conditions",
    "nyfed_cmdi",
    "treasury_tga",
    "eia_natural_gas_storage",
    "nber_us_recession",
    "bls_price_wage_productivity",
)


@dataclass(frozen=True, slots=True)
class _Outcome:
    outcome: str


@dataclass(frozen=True, slots=True)
class _SomaOutcome:
    published_releases: int
    unchanged_releases: int


class MacroCurrentRefreshTests(unittest.TestCase):
    def _collectors(
        self, *, failure: str | None = None
    ) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
        calls: list[tuple[str, dict[str, object]]] = []
        collectors: dict[str, object] = {}

        for source in EXPECTED_SOURCES:
            def collector(*, _source: str = source, **kwargs: object) -> object:
                calls.append((_source, dict(kwargs)))
                if _source == failure:
                    raise StoreUnavailableError("fixture credential must not leak")
                if _source == "nyfed_soma":
                    return _SomaOutcome(published_releases=1, unchanged_releases=0)
                return _Outcome("unchanged")

            collectors[source] = collector
        return collectors, calls

    def test_all_fixed_collectors_run_once_in_order_with_bounded_scopes(self) -> None:
        collectors, calls = self._collectors()

        report = operation.run_macro_current_refresh(
            collectors=collectors,
            utcnow=lambda: FIXED_NOW,
        )

        self.assertEqual(
            calls,
            [
                ("fmp_treasury_curve", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("nyfed_overnight_rates", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("nyfed_repo_facilities", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("nyfed_soma", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("federal_reserve_h41", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("chicagofed_financial_conditions", {"start_date": "1971-01-08", "end_date": "2026-09-30"}),
                ("bis_credit_conditions", {"start_period": "1961-Q1", "end_period": "2026-Q3"}),
                ("nyfed_cmdi", {"start_date": "2005-01-07", "end_date": "2026-09-30"}),
                ("treasury_tga", {"start_date": "2026-05-18", "end_date": "2026-09-30"}),
                ("eia_natural_gas_storage", {}),
                ("nber_us_recession", {}),
                ("bls_price_wage_productivity", {}),
            ],
        )
        self.assertEqual(report.local_date, "2026-08-19")
        self.assertEqual(len(report.steps), 12)
        self.assertEqual(sum(step.request_cap for step in report.steps), 13)
        self.assertEqual(
            [step.request_cap for step in report.steps],
            [1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1],
        )
        self.assertEqual(operation.REQUEST_CAP, 13)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.steps[3].outcome, "published")
        self.assertEqual(report.mapping()["outcome"], "succeeded")
        self.assertEqual(
            loads_strict(dumps_strict(report.mapping())), report.mapping()
        )

    def test_planned_scopes_are_stable_on_consecutive_local_dates(self) -> None:
        first_collectors, first_calls = self._collectors()
        second_collectors, second_calls = self._collectors()

        operation.run_macro_current_refresh(
            collectors=first_collectors,
            utcnow=lambda: FIXED_NOW,
        )
        operation.run_macro_current_refresh(
            collectors=second_collectors,
            utcnow=lambda: datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(first_calls, second_calls)

    def test_one_failure_is_sanitized_and_does_not_stop_later_sources(self) -> None:
        collectors, calls = self._collectors(failure="nyfed_repo_facilities")

        report = operation.run_macro_current_refresh(
            collectors=collectors,
            utcnow=lambda: FIXED_NOW,
        )

        self.assertEqual([source for source, _ in calls], list(EXPECTED_SOURCES))
        self.assertEqual(report.exit_code, 69)
        self.assertEqual(report.mapping()["outcome"], "partial")
        self.assertEqual(report.mapping()["failed_steps"], 1)
        failed = report.steps[2]
        self.assertEqual(
            (failed.source, failed.outcome, failed.error, failed.exit_code),
            ("nyfed_repo_facilities", "failed", "store_unavailable", 69),
        )
        rendered = dumps_strict(report.mapping())
        self.assertNotIn("fixture credential", rendered)
        self.assertEqual(report.steps[-1].source, "bls_price_wage_productivity")
        self.assertEqual(report.steps[-1].outcome, "unchanged")

    def test_cli_prints_strict_summary_and_rejects_arguments(self) -> None:
        collectors, _ = self._collectors()
        report = operation.run_macro_current_refresh(
            collectors=collectors,
            utcnow=lambda: FIXED_NOW,
        )
        output = StringIO()
        with patch.object(
            operation, "refresh_macro_current_live", return_value=report
        ) as refresh:
            with redirect_stdout(output):
                self.assertEqual(operation.main([]), 0)
        refresh.assert_called_once_with()
        self.assertEqual(loads_strict(output.getvalue()), report.mapping())

        error = StringIO()
        with patch.object(operation, "refresh_macro_current_live") as refresh:
            with redirect_stderr(error):
                self.assertEqual(operation.main(["--history"]), 2)
        refresh.assert_not_called()
        self.assertEqual(
            loads_strict(error.getvalue()), {"error": "invalid_arguments"}
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
