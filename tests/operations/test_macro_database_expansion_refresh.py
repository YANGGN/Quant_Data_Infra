from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
import unittest
from unittest.mock import patch

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.operations import macro_database_expansion_refresh as operation


AS_OF = "2026-09-06"
SERIES_BREAK = "SBN2024"
EXPECTED_SOURCES = (
    "cftc_tff_futures_only",
    "cftc_disaggregated_futures_only",
    "treasury_securities_auctions",
    "nyfed_primary_dealer_statistics",
    "federal_reserve_h8",
    "federal_reserve_sloos",
)


@dataclass(frozen=True, slots=True)
class _Outcome:
    outcome: str


@dataclass(frozen=True, slots=True)
class _PublishedCount:
    published: int
    unchanged: int


class MacroDatabaseExpansionRefreshTests(unittest.TestCase):
    def _collectors(
        self, *, failure: str | None = None
    ) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
        calls: list[tuple[str, dict[str, object]]] = []
        collectors: dict[str, object] = {}

        for source in EXPECTED_SOURCES:
            def collector(*, _source: str = source, **kwargs: object) -> object:
                calls.append((_source, dict(kwargs)))
                if _source == failure:
                    raise StoreUnavailableError("fixture secret must not leak")
                if _source == "nyfed_primary_dealer_statistics":
                    return _PublishedCount(published=1, unchanged=0)
                return _Outcome("unchanged")

            collectors[source] = collector
        return collectors, calls

    def test_all_six_sources_run_once_in_order_with_exact_bounded_scopes(self) -> None:
        collectors, calls = self._collectors()

        report = operation.run_macro_database_expansion_refresh(
            as_of=AS_OF,
            series_break=SERIES_BREAK,
            collectors=collectors,
        )

        self.assertEqual(
            calls,
            [
                (
                    "cftc_tff_futures_only",
                    {
                        "report_family": "tff_futures_only",
                        "start_date": "2026-08-15",
                        "end_date": "2026-09-04",
                    },
                ),
                (
                    "cftc_disaggregated_futures_only",
                    {
                        "report_family": "disaggregated_futures_only",
                        "start_date": "2026-08-15",
                        "end_date": "2026-09-04",
                    },
                ),
                (
                    "treasury_securities_auctions",
                    {"start_date": "2026-07-22", "end_date": "2026-09-04"},
                ),
                (
                    "nyfed_primary_dealer_statistics",
                    {
                        "series_break": "SBN2024",
                        "start_date": "2026-07-22",
                        "end_date": "2026-09-04",
                    },
                ),
                (
                    "federal_reserve_h8",
                    {
                        "source_key": "federal_reserve_h8",
                        "start_date": "2026-07-22",
                        "end_date": "2026-09-04",
                    },
                ),
                (
                    "federal_reserve_sloos",
                    {
                        "source_key": "federal_reserve_sloos",
                        "start_date": "2025-08-30",
                        "end_date": "2026-09-04",
                    },
                ),
            ],
        )
        self.assertEqual(
            (report.as_of.isoformat(), report.anchor_date.isoformat()),
            (AS_OF, "2026-09-04"),
        )
        self.assertEqual(
            [step.request_cap for step in report.steps],
            [8, 8, 1, 33, 7, 6],
        )
        self.assertEqual(sum(step.request_cap for step in report.steps), 63)
        self.assertEqual(operation.REQUEST_CAP, 63)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.steps[3].outcome, "published")
        self.assertEqual(report.mapping()["outcome"], "succeeded")
        self.assertEqual(
            loads_strict(dumps_strict(report.mapping())),
            report.mapping(),
        )

    def test_anchor_is_latest_friday_on_or_before_as_of(self) -> None:
        friday = operation.build_macro_database_expansion_refresh_plan(
            as_of="2026-09-04",
            series_break=SERIES_BREAK,
        )
        saturday = operation.build_macro_database_expansion_refresh_plan(
            as_of="2026-09-05",
            series_break=SERIES_BREAK,
        )
        thursday = operation.build_macro_database_expansion_refresh_plan(
            as_of="2026-09-03",
            series_break=SERIES_BREAK,
        )

        self.assertEqual(friday.anchor_date.isoformat(), "2026-09-04")
        self.assertEqual(saturday.anchor_date.isoformat(), "2026-09-04")
        self.assertEqual(thursday.anchor_date.isoformat(), "2026-08-28")
        self.assertEqual(sum(call.request_cap for call in friday.calls), 63)

    def test_invalid_preflight_does_not_load_live_wrappers(self) -> None:
        with patch.object(operation, "_live_collectors") as loaders:
            with self.assertRaises(ValidationError):
                operation.refresh_macro_database_expansion_live(
                    as_of="2026-09-06T00:00:00Z",
                    series_break=SERIES_BREAK,
                )
            with self.assertRaises(ValidationError):
                operation.refresh_macro_database_expansion_live(
                    as_of=AS_OF,
                    series_break="latest",
                )
        loaders.assert_not_called()

    def test_first_failure_is_sanitized_and_stops_later_sources(self) -> None:
        collectors, calls = self._collectors(
            failure="treasury_securities_auctions"
        )

        report = operation.run_macro_database_expansion_refresh(
            as_of=AS_OF,
            series_break=SERIES_BREAK,
            collectors=collectors,
        )

        self.assertEqual(
            [source for source, _ in calls],
            [
                "cftc_tff_futures_only",
                "cftc_disaggregated_futures_only",
                "treasury_securities_auctions",
            ],
        )
        self.assertEqual(report.exit_code, 69)
        self.assertEqual(report.mapping()["outcome"], "failed")
        self.assertEqual(report.mapping()["failed_steps"], 1)
        failed = report.steps[-1]
        self.assertEqual(
            (failed.source, failed.outcome, failed.error, failed.exit_code),
            ("treasury_securities_auctions", "failed", "store_unavailable", 69),
        )
        self.assertNotIn("fixture secret", dumps_strict(report.mapping()))
        self.assertEqual(len(report.steps), 3)

    def test_collector_bindings_are_preflighted_before_any_call(self) -> None:
        calls: list[str] = []

        def collector(**kwargs: object) -> _Outcome:
            del kwargs
            calls.append("called")
            return _Outcome("unchanged")

        bad_collectors = {source: collector for source in EXPECTED_SOURCES}
        del bad_collectors["federal_reserve_sloos"]
        with self.assertRaises(ValidationError):
            operation.run_macro_database_expansion_refresh(
                as_of=AS_OF,
                series_break=SERIES_BREAK,
                collectors=bad_collectors,
            )
        self.assertEqual(calls, [])

    def test_cli_is_strict_sanitized_and_has_no_scope_switches(self) -> None:
        collectors, _ = self._collectors()
        report = operation.run_macro_database_expansion_refresh(
            as_of=AS_OF,
            series_break=SERIES_BREAK,
            collectors=collectors,
        )
        output = StringIO()
        with patch.object(
            operation,
            "refresh_macro_database_expansion_live",
            return_value=report,
        ) as refresh:
            with redirect_stdout(output):
                self.assertEqual(
                    operation.main(
                        ["--as-of", AS_OF, "--series-break", SERIES_BREAK]
                    ),
                    0,
                )
        refresh.assert_called_once_with(as_of=AS_OF, series_break=SERIES_BREAK)
        self.assertEqual(loads_strict(output.getvalue()), report.mapping())

        error = StringIO()
        with patch.object(
            operation, "refresh_macro_database_expansion_live"
        ) as refresh:
            with redirect_stderr(error):
                self.assertEqual(
                    operation.main(
                        [
                            "--as-of",
                            AS_OF,
                            "--series-break",
                            SERIES_BREAK,
                            "--retry",
                            "2",
                        ]
                    ),
                    64,
                )
        refresh.assert_not_called()
        self.assertEqual(
            loads_strict(error.getvalue()),
            {
                "contract": "quant_data.macro_database_expansion_refresh_error",
                "version": "1.0.0",
                "error": "invalid_request",
                "exit_code": 64,
            },
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
