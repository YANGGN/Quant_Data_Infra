from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import loads_strict
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.market_cross_sectional import (
    TOOL_NAME,
    invoke_stage10_cross_sectional_performance,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CAPTURED_AT = "2026-08-20T00:00:00Z"


def _fields(result: object) -> tuple[dict[str, object], ...]:
    return tuple(
        {field.name: field.value for field in record.fields}
        for record in getattr(result, "records")
    )


def _diagnostic(result: object) -> dict[str, object]:
    return {
        item.name: item.value
        for item in getattr(result, "diagnostics")[0].metrics
    }


def _digest(value: int) -> str:
    return f"{value:064x}"


class MarketCrossSectionalPerformanceV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at=_CAPTURED_AT)
        self._seed_prices()
        self.baseline = mutation_fingerprint(self.stores)
        self.registry_sha256 = hashlib.sha256(
            (PROJECT_ROOT / CANONICAL_REGISTRY_PATH).read_bytes()
        ).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(
            self.baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        self.temporary.cleanup()

    def _insert_run(
        self,
        connection: object,
        *,
        run_id: str,
        dataset_id: str,
        semantic_identity: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ingestion_runs(
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, code_version
            ) VALUES (?, ?, ?, 'fixture.seed', '{}', 'running', ?, 'fixture')
            """,
            (run_id, dataset_id, semantic_identity, _CAPTURED_AT),
        )

    def _seed_prices(self) -> None:
        fixtures = (
            ("AAA", "equity", (100, 110)),
            ("BBB", "equity", (50, 55)),
            ("CCC", "etf", (100, 90)),
            ("CUT", "equity", (10, 11, 12)),
            ("TINY", "equity", (5,)),
            ("ZERO", "equity", (0, 10)),
        )
        dates = ("2026-01-02", "2026-01-03", "2026-01-04")
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            identity_run_id = "cross-section-identity-run"
            self._insert_run(
                connection,
                run_id=identity_run_id,
                dataset_id="market.stage10.instruments",
                semantic_identity=_digest(1),
            )
            for index, (ticker, asset_type, closes) in enumerate(fixtures, start=1):
                instrument_id = f"cross-section-{ticker.lower()}"
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id, provider, provider_symbol, asset_type,
                        display_name, exchange_code, currency_segment,
                        first_trade_date, identity_seed_sha256, captured_at,
                        captured_precision, run_id
                    ) VALUES (?, 'fmp', ?, ?, ?, 'XNAS', 'provider_native', NULL,
                              ?, ?, 'datetime', ?)
                    """,
                    (
                        instrument_id,
                        ticker,
                        asset_type,
                        f"{ticker} fixture",
                        _digest(10 + index),
                        _CAPTURED_AT,
                        identity_run_id,
                    ),
                )
                run_id = f"cross-section-capture-run-{ticker.lower()}"
                capture_id = f"cross-section-capture-{ticker.lower()}"
                artifact_id = f"cross-section-artifact-{ticker.lower()}"
                snapshot_id = f"cross-section-snapshot-{ticker.lower()}"
                self._insert_run(
                    connection,
                    run_id=run_id,
                    dataset_id="market.stage10.source_evidence",
                    semantic_identity=_digest(100 + index),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_captures(
                        capture_id, dataset_id, provider, instrument_id,
                        provider_symbol, endpoint_path, scope_manifest_sha256,
                        request_scope_json, request_scope_sha256, response_sha256,
                        response_bytes, http_status, content_type,
                        semantic_identity, completeness, artifact_id, snapshot_id,
                        captured_at, captured_precision, earliest_trade_date,
                        latest_trade_date, row_count, normalization_version, run_id
                    ) VALUES (
                        ?, 'market.stage10.source_evidence', 'fmp', ?, ?,
                        '/stable/historical-price-eod/full', ?, '{}', ?, ?, ?,
                        200, 'application/json', ?, 'complete', ?, ?, ?,
                        'datetime', ?, ?, ?, 'stage10.fmp.daily_price.v1', ?
                    )
                    """,
                    (
                        capture_id,
                        instrument_id,
                        ticker,
                        _digest(200 + index),
                        _digest(300 + index),
                        _digest(400 + index),
                        f'{{"symbol":"{ticker}"}}'.encode("utf-8"),
                        _digest(500 + index),
                        artifact_id,
                        snapshot_id,
                        _CAPTURED_AT,
                        dates[0],
                        dates[len(closes) - 1],
                        len(closes),
                        run_id,
                    ),
                )
                for source_row, close in enumerate(closes, start=1):
                    version_id = f"cross-section-version-{ticker.lower()}-{source_row}"
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
                            dates[source_row - 1],
                            str(close),
                            str(close),
                            str(close),
                            str(close),
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
                        (instrument_id, dates[source_row - 1], version_id),
                    )



    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="market-cross-sectional-v2-test",
            api_version="1.0",
            tool_name=TOOL_NAME,
            tool_version="2.0.0",
            operation_graph_id=(
                "tool_platform.market.cross_sectional_performance.v2"
            ),
            operation_version="2.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(Decimal("0"), _CAPTURED_AT),
        )

    @staticmethod
    def _arguments(
        *,
        tickers: tuple[str, ...],
        start_date: str | None = None,
        end_date: str | None = None,
        mode: str = "latest",
        as_of: str | None = None,
        date_only_policy: str = "completed_date",
        limit: int = 2,
    ) -> dict[str, object]:
        return {
            "tickers": tickers,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": date_only_policy,
            "limit": limit,
        }

    def _call(self, **kwargs: object):
        return invoke_stage10_cross_sectional_performance(
            TOOL_NAME,
            self._arguments(**kwargs),
            self._context(),
            self.registry,
        )

    def test_actual_close_reader_ranks_complete_coverage_and_retains_exclusions(
        self,
    ) -> None:
        result = self._call(
            tickers=("bbb", " aaa ", "ccc", "tiny", "zero", "cut", "missing"),
        )
        rows = _fields(result)
        by_ticker = {str(row["ticker"]): row for row in rows}
        self.assertEqual(
            tuple(row["ticker"] for row in rows),
            ("AAA", "BBB", "CCC", "CUT", "MISSING", "TINY", "ZERO"),
        )
        self.assertEqual(
            (
                by_ticker["AAA"]["rank"],
                by_ticker["BBB"]["rank"],
                by_ticker["CCC"]["rank"],
            ),
            (1, 2, 3),
        )
        self.assertEqual(
            (
                by_ticker["AAA"]["percentile"],
                by_ticker["BBB"]["percentile"],
                by_ticker["CCC"]["percentile"],
            ),
            (Decimal("1"), Decimal("0.5"), Decimal("0")),
        )
        self.assertEqual(
            (
                by_ticker["AAA"]["cumulative_return"],
                by_ticker["BBB"]["cumulative_return"],
                by_ticker["CCC"]["cumulative_return"],
            ),
            (Decimal("0.1"), Decimal("0.1"), Decimal("-0.1")),
        )
        self.assertEqual(by_ticker["CUT"]["coverage_status"], "truncated")
        self.assertEqual(by_ticker["CUT"]["cumulative_return"], Decimal("0.1"))
        self.assertFalse(by_ticker["CUT"]["ranking_eligible"])
        self.assertEqual(
            by_ticker["TINY"]["coverage_status"], "insufficient_observations"
        )
        self.assertEqual(by_ticker["ZERO"]["coverage_status"], "zero_base_price")
        self.assertEqual(by_ticker["MISSING"]["coverage_status"], "unavailable")
        self.assertEqual(by_ticker["MISSING"]["source_lineage_digest"], None)
        self.assertEqual(len(result.lineage), 6)
        self.assertEqual(
            {item.dataset_id for item in result.lineage},
            {"market.stage10.daily_prices"},
        )
        self.assertFalse(result.truncation.applied)
        self.assertEqual(result.truncation.returned_count, 7)
        self.assertEqual(
            tuple(item.code for item in result.warnings),
            (
                "adjustment_and_total_return_semantics_not_established",
                "insufficient_source_observations_excluded_from_ranking",
                "local_capture_availability",
                "market_instrument_unavailable",
                "observed_row_horizon",
                "result_truncated",
                "session_calendar_not_established",
                "source_series_truncated_excluded_from_ranking",
                "zero_base_price_excluded_from_ranking",
            ),
        )
        diagnostic = _diagnostic(result)
        self.assertEqual(diagnostic["actual_mode"], "latest")
        self.assertEqual(diagnostic["availability_basis"], "local_capture")
        self.assertIsNone(diagnostic["cutoff"])
        self.assertIsNone(diagnostic["cutoff_precision"])
        self.assertEqual(diagnostic["date_only_policy"], "completed_date")
        self.assertEqual(diagnostic["point_in_time_status"], "not_applicable")
        self.assertEqual(diagnostic["requested_start_date"], None)
        self.assertEqual(diagnostic["requested_end_date"], None)
        self.assertEqual(diagnostic["selected_instrument_count"], 6)
        self.assertEqual(diagnostic["selected_observation_count"], 11)
        self.assertEqual(diagnostic["source_series_warning_count"], 5)
        self.assertEqual(diagnostic["source_total_observation_bound"], 21)
        self.assertEqual(diagnostic["ranked_count"], 3)
        self.assertEqual(
            {
                item["instrument_id"]
                for item in loads_strict(
                    str(diagnostic["selected_instrument_identities"])
                )
            },
            {
                "cross-section-aaa",
                "cross-section-bbb",
                "cross-section-ccc",
                "cross-section-cut",
                "cross-section-tiny",
                "cross-section-zero",
            },
        )
        self.assertEqual(
            self.baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    def test_optional_dates_and_as_of_reuse_the_close_reader_temporal_contract(
        self,
    ) -> None:
        result = self._call(
            tickers=("AAA", "BBB"),
            start_date="2026-01-02",
            end_date="2026-01-03",
            mode="as_of",
            as_of="2026-08-21T00:00:00Z",
        )
        rows = _fields(result)
        self.assertEqual(tuple(row["ticker"] for row in rows), ("AAA", "BBB"))
        self.assertEqual(
            tuple(row["cumulative_return"] for row in rows),
            (Decimal("0.1"), Decimal("0.1")),
        )
        self.assertEqual(
            tuple(item.code for item in result.warnings),
            (
                "adjustment_and_total_return_semantics_not_established",
                "local_capture_availability",
                "observed_row_horizon",
                "point_in_time_safe_only_for_retained_local_captures",
                "session_calendar_not_established",
            ),
        )
        diagnostic = _diagnostic(result)
        self.assertEqual(diagnostic["mode"], "as_of")
        self.assertEqual(diagnostic["requested_mode"], "as_of")
        self.assertEqual(diagnostic["actual_mode"], "as_of")
        self.assertEqual(diagnostic["cutoff"], "2026-08-21T00:00:00Z")
        self.assertEqual(diagnostic["cutoff_precision"], "datetime")
        self.assertEqual(diagnostic["date_only_policy"], "completed_date")
        self.assertEqual(diagnostic["availability_basis"], "local_capture")
        self.assertEqual(diagnostic["point_in_time_status"], "safe")
        self.assertEqual(diagnostic["requested_start_date"], "2026-01-02")
        self.assertEqual(diagnostic["requested_end_date"], "2026-01-03")
        self.assertEqual(diagnostic["selected_instrument_count"], 2)
        self.assertEqual(diagnostic["selected_observation_count"], 4)
        self.assertEqual(loads_strict(str(diagnostic["unsafe_reasons"])), [])
        self.assertEqual(diagnostic["source_total_observation_bound"], 400_002)

    def test_empty_as_of_selection_retains_its_temporal_diagnostic(self) -> None:
        result = self._call(
            tickers=("AAA", "BBB"),
            start_date="2026-02-01",
            end_date="2026-02-02",
            mode="as_of",
            as_of="2026-08-21T00:00:00Z",
        )
        self.assertEqual(
            tuple(row["observation_count"] for row in _fields(result)),
            (0, 0),
        )
        diagnostic = _diagnostic(result)
        self.assertEqual(diagnostic["point_in_time_status"], "safe")
        self.assertEqual(diagnostic["selected_instrument_count"], 2)
        self.assertEqual(diagnostic["selected_observation_count"], 0)
        self.assertEqual(loads_strict(str(diagnostic["unsafe_reasons"])), [])

    def test_adapter_rejects_normalized_duplicates_and_bounds_as_of_work(self) -> None:
        with self.assertRaises(ValidationError):
            self._call(tickers=("AAA", " aaa "))
        with self.assertRaises(ResourceLimitError):
            self._call(tickers=("AAA", "BBB"), limit=1)
        with self.assertRaises(ResourceLimitError):
            self._call(
                tickers=tuple(f"S{index:02d}" for index in range(25)),
                mode="as_of",
                as_of="2026-08-21T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
