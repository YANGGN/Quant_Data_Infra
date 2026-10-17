from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.operations import current_news_refresh as operation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime(2026, 8, 30, 14, 37, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Outcome:
    outcome: str


class CurrentNewsRefreshTests(unittest.TestCase):
    def _collectors(
        self,
        *,
        failed_source: str | None = None,
    ) -> tuple[dict[str, object], list[str]]:
        calls: list[str] = []
        collectors: dict[str, object] = {}
        for source in operation.SOURCE_IDS:
            def collector(*, _source: str = source) -> object:
                calls.append(_source)
                if _source == failed_source:
                    raise StoreUnavailableError("fixture token must not leak")
                return _Outcome("unchanged")

            collectors[source] = collector
        return collectors, calls

    @staticmethod
    def _credential_reader(
        missing: set[str] | None = None,
        calls: list[str] | None = None,
    ):
        blocked = set() if missing is None else set(missing)

        def reader(*, project_root, name, environment):
            del project_root, environment
            if calls is not None:
                calls.append(name)
            if name in blocked:
                raise ValidationError("fixture secret is unavailable")
            return "offline-value"

        return reader

    def test_runs_each_fixed_source_once_in_order(self) -> None:
        collectors, calls = self._collectors()
        credential_calls: list[str] = []

        report = operation.run_current_news_refresh(
            collectors=collectors,
            environment={
                "FMP_API_KEY": "offline",
                "ALPACA_API_KEY": "offline",
                "ALPACA_API_SECRET": "offline",
            },
            utcnow=lambda: FIXED_NOW,
            credential_reader=self._credential_reader(calls=credential_calls),
        )

        self.assertEqual(calls, list(operation.SOURCE_IDS))
        self.assertEqual(
            credential_calls,
            ["FMP_API_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"],
        )
        self.assertEqual(report.poll_slot, "2026-08-30T14:00:00Z")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.mapping()["request_cap"], 10)
        self.assertEqual(report.mapping()["outcome"], "succeeded")
        self.assertEqual(
            loads_strict(dumps_strict(report.mapping())),
            report.mapping(),
        )

    def test_missing_fmp_credential_is_unavailable_and_rss_still_runs(self) -> None:
        collectors, calls = self._collectors()

        report = operation.run_current_news_refresh(
            collectors=collectors,
            environment={"ALPACA_API_KEY": "offline", "ALPACA_API_SECRET": "offline"},
            utcnow=lambda: FIXED_NOW,
            credential_reader=self._credential_reader({"FMP_API_KEY"}),
        )

        self.assertEqual(
            calls,
            ["fed_press", "ecb_press", "bea_news", "eia_press", "alpaca_benzinga", "finviz", "financialjuice"],
        )
        unavailable = [step.source for step in report.steps if step.outcome == "unavailable"]
        self.assertEqual(
            unavailable,
            ["fmp_stock_latest", "fmp_press_releases", "fmp_general"],
        )
        self.assertEqual(report.exit_code, 78)
        rendered = dumps_strict(report.mapping())
        self.assertNotIn("fixture secret", rendered)
        self.assertNotIn("offline", rendered)

    def test_independent_failure_does_not_stop_later_sources(self) -> None:
        collectors, calls = self._collectors(failed_source="fmp_general")

        report = operation.run_current_news_refresh(
            collectors=collectors,
            environment={
                "FMP_API_KEY": "offline",
                "ALPACA_API_KEY": "offline",
                "ALPACA_API_SECRET": "offline",
            },
            utcnow=lambda: FIXED_NOW,
            credential_reader=self._credential_reader(),
        )

        self.assertEqual(calls, list(operation.SOURCE_IDS))
        failed = report.steps[2]
        self.assertEqual(
            (failed.source, failed.outcome, failed.error, failed.exit_code),
            ("fmp_general", "failed", "store_unavailable", 69),
        )
        self.assertEqual(report.exit_code, 69)
        self.assertEqual(report.mapping()["outcome"], "partial")

    def test_alpaca_batches_continue_after_a_batch_failure(self) -> None:
        batches = (("AAPL", "MSFT"), ("SPY",), ("QQQ",))
        calls: list[tuple[str, ...]] = []

        def invoke(batch: tuple[str, ...]) -> object:
            calls.append(batch)
            if batch == ("SPY",):
                raise StoreUnavailableError("fixture API key must not leak")
            return _Outcome("succeeded")

        result = operation._run_alpaca_batches(batches, invoke)

        self.assertEqual(calls, list(batches))
        self.assertEqual(
            (
                result.request_cap,
                result.attempted_requests,
                result.successful_requests,
                result.failed_requests,
                result.exit_code,
                result.error,
            ),
            (3, 3, 2, 1, 69, "store_unavailable"),
        )

    def test_cli_is_zero_argument_and_sanitized(self) -> None:
        collectors, _ = self._collectors()
        report = operation.run_current_news_refresh(
            collectors=collectors,
            environment={
                "FMP_API_KEY": "offline",
                "ALPACA_API_KEY": "offline",
                "ALPACA_API_SECRET": "offline",
            },
            utcnow=lambda: FIXED_NOW,
            credential_reader=self._credential_reader(),
        )
        output = StringIO()
        with patch.object(operation, "refresh_current_news_live", return_value=report) as refresh:
            with redirect_stdout(output):
                self.assertEqual(operation.main([]), 0)
        refresh.assert_called_once_with()
        self.assertEqual(loads_strict(output.getvalue()), report.mapping())

        error = StringIO()
        with patch.object(operation, "refresh_current_news_live") as refresh:
            with redirect_stderr(error):
                self.assertEqual(operation.main(["--ticker", "AAPL"]), 2)
        refresh.assert_not_called()
        self.assertEqual(loads_strict(error.getvalue()), {"error": "invalid_arguments"})

    def test_direct_script_bootstraps_project_before_argument_validation(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "refresh_current_news.py"),
                "--unexpected",
            ],
            cwd="/tmp",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(loads_strict(result.stderr), {"error": "invalid_arguments"})

    def test_invalid_collector_set_rejects_before_credential_resolution(self) -> None:
        with self.assertRaises(ValidationError):
            operation.run_current_news_refresh(
                collectors={},
                environment={},
                utcnow=lambda: FIXED_NOW,
                credential_reader=self._credential_reader(),
            )


if __name__ == "__main__":
    unittest.main()
