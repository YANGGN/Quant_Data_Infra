from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import ToolDispatcher
from quant_data.contracts import TimeSeries
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.schema import validate_schema
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreMap
from quant_data.temporal import TemporalValue
from quant_data.tool_platform.arguments import (
    Stage10TechnicalIndicatorArgumentsV2,
    Stage10TechnicalIndicatorArgumentsV22,
    Stage10TechnicalIndicatorArgumentsV25,
    Stage10TechnicalIndicatorArgumentsV26,
    Stage10TechnicalIndicatorArgumentsV27,
)
from quant_data.tool_platform.context import (
    CancellationToken,
    CapabilitySet,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.technical_indicator_adapter import (
    _cumulative_available_times,
    _example_series,
    invoke_stage10_technical_indicator,
    stage10_technical_indicator_example,
    stage10_technical_indicator_input_series_schema,
    stage10_technical_indicator_output_series_schema,
    stage10_technical_indicator_output_series_schema_v21,
    stage10_technical_indicator_output_series_schema_v22,
    stage10_technical_indicator_output_series_schema_v23,
    stage10_technical_indicator_output_series_schema_v27,
    stage10_technical_indicator_output_series_schema_v25,
    stage10_technical_indicator_output_series_schema_v26,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TechnicalIndicatorAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.context = ToolExecutionContext(
            store_map=StoreMap.four_explicit(
                market=root / "market.sqlite",
                macro=root / "macro.sqlite",
                company=root / "company.sqlite",
                news=root / "news.sqlite",
            ),
            registry_revision="2.48.0",
            registry_sha256="a" * 64,
            request_id="indicator-test",
            api_version="1.0",
            tool_name="market.technical_indicators",
            tool_version="2.0.0",
            operation_graph_id=(
                "tool_platform.market.technical_indicators.v2"
            ),
            operation_version="2.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                Decimal("0"), "2026-08-26T00:00:00Z"
            ),
        )
        self.close = _example_series()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _price(
        self, role: str, values: tuple[str, ...]
    ) -> TimeSeries:
        observations = tuple(
            replace(
                observation,
                value=Decimal(value),
                dimensions={
                    **dict(observation.dimensions),
                    "observation_field": role,
                },
            )
            for observation, value in zip(
                self.close.observations, values, strict=True
            )
        )
        return TimeSeries(
            series_id=f"example:stage10_price:aapl:{role}",
            metadata={
                **dict(self.close.metadata),
                "observation_field": role,
            },
            observations=observations,
            warnings=self.close.warnings,
            audit={**dict(self.close.audit), "observation_field": role},
            provenance=dict(self.close.provenance),
            truncated=False,
        )

    def _swing_price(
        self, role: str, values: tuple[int, ...]
    ) -> TimeSeries:
        """Build an aligned, contract-valid synthetic daily price series."""

        start = date(2026, 1, 1)
        end = start + timedelta(days=len(values) - 1)
        observations = tuple(
            replace(
                self.close.observations[index % len(self.close.observations)],
                period_start=(start + timedelta(days=index)).isoformat(),
                period_end=(start + timedelta(days=index)).isoformat(),
                value=Decimal(value),
                missing_reason=None,
                version_id=f"example-swing-version-{index}",
                evidence_id=f"example-swing-evidence-{index}",
                snapshot_id=f"example-swing-snapshot-{index}",
                run_id=f"example-swing-run-{index}",
                dimensions={
                    **dict(
                        self.close.observations[
                            index % len(self.close.observations)
                        ].dimensions
                    ),
                    "observation_field": role,
                },
            )
            for index, value in enumerate(values)
        )
        return TimeSeries(
            series_id=f"example:stage10_swing:aapl:{role}",
            metadata={
                **dict(self.close.metadata),
                "observation_field": role,
            },
            observations=observations,
            warnings=self.close.warnings,
            audit={
                **dict(self.close.audit),
                "requested_start_date": start.isoformat(),
                "requested_end_date": end.isoformat(),
                "observation_field": role,
                "limit": len(values),
                "selected_count": len(values),
                "missing_count": 0,
            },
            provenance=dict(self.close.provenance),
            truncated=False,
        )

    def _volume(self, values: tuple[str, ...]) -> TimeSeries:
        observations = tuple(
            replace(
                observation,
                value=Decimal(value),
                unit="provider_native_volume",
                value_representation="volume",
                dimensions={
                    "instrument_id": self.close.metadata["instrument_id"],
                    "provider": "fmp",
                    "data_variant": "fmp_full_eod_v1",
                    "observation_field": "volume",
                },
            )
            for observation, value in zip(
                self.close.observations, values, strict=True
            )
        )
        return TimeSeries(
            series_id="example:stage10_volume:aapl",
            metadata={
                "instrument_id": self.close.metadata["instrument_id"],
                "provider_symbol": "AAPL",
                "asset_type": "equity",
                "display_name": "AAPL example",
                "exchange_code": "XNAS",
                "provider": "fmp",
                "frequency": "daily",
                "unit": "provider_native_volume",
                "value_representation": "volume",
                "scale": "1",
                "data_variant": "fmp_full_eod_v1",
                "observation_field": "volume",
                "availability_basis": "local_capture",
                "volume_unit_status": "provider_native_not_normalized",
                "volume_adjustment_status": "not_established",
                "session_calendar_status": "not_established",
            },
            observations=observations,
            warnings=(
                "local_capture_availability",
                "session_calendar_not_established",
                "volume_adjustment_semantics_not_established",
                "volume_unit_semantics_provider_native",
            ),
            audit={
                "mode": "latest",
                "requested_mode": "latest",
                "actual_mode": "latest",
                "cutoff": None,
                "cutoff_precision": None,
                "date_only_policy": "completed_date",
                "availability_basis": "local_capture",
                "period_range_rule": "trade_date",
                "requested_start_date": "2026-08-10",
                "requested_end_date": "2026-08-14",
                "provider": "fmp",
                "data_variant": "fmp_full_eod_v1",
                "observation_field": "volume",
                "volume_unit_status": "provider_native_not_normalized",
                "volume_adjustment_status": "not_established",
                "session_calendar_status": "not_established",
                "point_in_time_status": "not_applicable",
                "point_in_time_scope": "current_stored_knowledge",
                "unsafe_reasons": [],
                "limit": 5,
                "selected_count": 5,
                "missing_count": 0,
                "truncated": False,
            },
            provenance=dict(self.close.provenance),
            truncated=False,
        )

    def _arguments(
        self,
        series: tuple[TimeSeries, ...],
        indicator: str,
        *,
        window: int | None = None,
        fast_window: int | None = None,
        slow_window: int | None = None,
        signal_window: int | None = None,
        multiplier: Decimal | None = None,
        limit: int = 5,
    ) -> Stage10TechnicalIndicatorArgumentsV2:
        return Stage10TechnicalIndicatorArgumentsV2(
            series=series,
            indicator=indicator,
            window=window,
            fast_window=fast_window,
            slow_window=slow_window,
            signal_window=signal_window,
            standard_deviation_multiplier=multiplier,
            limit=limit,
        )

    def _invoke(
        self, arguments: Stage10TechnicalIndicatorArgumentsV2
    ):
        return invoke_stage10_technical_indicator(
            "market.technical_indicators", arguments, self.context
        )

    def _swing_arguments(
        self,
        series: tuple[TimeSeries, ...],
        *,
        limit: int,
    ) -> Stage10TechnicalIndicatorArgumentsV22:
        return Stage10TechnicalIndicatorArgumentsV22(
            series=series,
            indicator="swing_structure_forecast",
            window=10,
            fast_window=None,
            slow_window=None,
            signal_window=None,
            standard_deviation_multiplier=None,
            minimum_factor=None,
            maximum_factor=None,
            factor_step=None,
            performance_memory=None,
            cluster=None,
            sample_count=3,
            aggregation_method="average",
            limit=limit,
        )

    def _invoke_v22(
        self, arguments: Stage10TechnicalIndicatorArgumentsV22
    ):
        return invoke_stage10_technical_indicator(
            "market.technical_indicators",
            arguments,
            replace(
                self.context,
                tool_version="2.2.0",
                operation_graph_id=(
                    "tool_platform.market.technical_indicators.v2_2"
                ),
                operation_version="2.2.0",
            ),
        )

    def test_public_example_and_price_volume_union_validate(self) -> None:
        input_schema = stage10_technical_indicator_input_series_schema()
        validate_schema(stage10_technical_indicator_example(), input_schema)
        validate_schema(self.close.to_primitive(), input_schema)
        validate_schema(
            self._volume(("1000", "1100", "900", "1200", "1300"))
            .to_primitive(),
            input_schema,
        )

    def test_sma_is_deterministic_aligned_and_schema_valid(self) -> None:
        arguments = self._arguments((self.close,), "sma", window=3)
        first = self._invoke(arguments)
        second = self._invoke(arguments)
        self.assertEqual(first, second)
        self.assertEqual(len(first.series), 1)
        values = tuple(item.value for item in first.series[0].observations)
        self.assertEqual(
            values,
            (
                None,
                None,
                Decimal("101"),
                Decimal("101.3333333333333333333333333333333"),
                Decimal("102"),
            ),
        )
        self.assertEqual(
            tuple(
                item.missing_reason
                for item in first.series[0].observations[:2]
            ),
            ("insufficient_history", "insufficient_history"),
        )
        validate_schema(
            first.series[0].to_primitive(),
            stage10_technical_indicator_output_series_schema(),
            code="invalid_output",
        )
        self.assertFalse(any(Path(self.temporary.name).iterdir()))

    def test_multi_component_and_volume_indicators(self) -> None:
        macd = self._invoke(
            self._arguments(
                (self.close,),
                "macd",
                fast_window=2,
                slow_window=3,
                signal_window=2,
            )
        )
        self.assertEqual(
            tuple(item.metadata["component"] for item in macd.series),
            ("macd_line", "signal_line", "histogram"),
        )
        high = self._price(
            "high", ("101", "102", "103", "102", "104")
        )
        low = self._price(
            "low", ("99", "100", "101", "100", "102")
        )
        volume = self._volume(
            ("1000", "1100", "900", "1200", "1300")
        )
        ad = self._invoke(
            self._arguments(
                (high, low, self.close, volume),
                "accumulation_distribution",
            )
        )
        obv = self._invoke(
            self._arguments(
                (self.close, volume), "on_balance_volume"
            )
        )
        self.assertEqual(ad.series[0].metadata["unit"], "provider_native_volume")
        self.assertEqual(
            tuple(item.value for item in obv.series[0].observations),
            (
                Decimal("0"),
                Decimal("1100"),
                Decimal("2000"),
                Decimal("800"),
                Decimal("2100"),
            ),
        )

    def test_availability_grid_is_linear_and_reused_by_components(self) -> None:
        high = self._price(
            "high", ("101", "102", "103", "102", "104")
        )
        low = self._price(
            "low", ("99", "100", "101", "100", "102")
        )
        ordered = (("high", high), ("low", low), ("close", self.close))
        original_parse = TemporalValue.parse
        parse_calls = 0

        def counted_parse(value, *, pointer):
            nonlocal parse_calls
            parse_calls += 1
            return original_parse(value, pointer=pointer)

        with patch(
            "quant_data.tool_platform.technical_indicator_adapter."
            "TemporalValue.parse",
            side_effect=counted_parse,
        ):
            available_times = _cumulative_available_times(ordered)
        self.assertEqual(parse_calls, 15)
        self.assertEqual(
            available_times,
            tuple(
                observation.available_at
                for observation in self.close.observations
            ),
        )

        with patch(
            "quant_data.tool_platform.technical_indicator_adapter."
            "_cumulative_available_times",
            wraps=_cumulative_available_times,
        ) as cumulative:
            self._invoke(
                self._arguments(
                    (self.close,),
                    "macd",
                    fast_window=2,
                    slow_window=3,
                    signal_window=2,
                )
            )
        cumulative.assert_called_once()

    def test_public_multi_output_budget_accepts_required_and_full_ohlc(
        self,
    ) -> None:
        root = Path(self.temporary.name)
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        dispatcher = ToolDispatcher(
            explicit_store_map(root / "public-stores"), registry
        )
        opened = self._price(
            "open", ("100", "100", "101", "102", "102")
        )
        high = self._price(
            "high", ("101", "102", "103", "103", "104")
        )
        low = self._price(
            "low", ("99", "99", "100", "100", "101")
        )
        common = {
            "standard_deviation_multiplier": None,
            "limit": 5,
        }
        adx = dispatcher.call(
            "market.technical_indicators",
            {
                **common,
                "series": [
                    high.to_primitive(),
                    low.to_primitive(),
                    self.close.to_primitive(),
                ],
                "indicator": "average_directional_index",
                "window": 3,
                "fast_window": None,
                "slow_window": None,
                "signal_window": None,
            },
            tool_version="2.0.0",
        )
        macd = dispatcher.call(
            "market.technical_indicators",
            {
                **common,
                "series": [
                    opened.to_primitive(),
                    high.to_primitive(),
                    low.to_primitive(),
                    self.close.to_primitive(),
                ],
                "indicator": "macd",
                "window": None,
                "fast_window": 2,
                "slow_window": 3,
                "signal_window": 2,
            },
            tool_version="2.0.0",
        )
        self.assertEqual(len(adx["series"]), 3)
        self.assertEqual(
            tuple(item["metadata"]["component"] for item in adx["series"]),
            ("plus_di", "minus_di", "adx"),
        )
        self.assertEqual(len(macd["series"]), 3)
        self.assertEqual(
            tuple(item["metadata"]["component"] for item in macd["series"]),
            ("macd_line", "signal_line", "histogram"),
        )
        self.assertFalse(any(root.iterdir()))

    def test_supertrend_ai_v21_public_dispatch_is_typed_and_store_free(
        self,
    ) -> None:
        root = Path(self.temporary.name)
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        dispatcher = ToolDispatcher(
            explicit_store_map(root / "public-supertrend-stores"),
            registry,
        )
        high = self._price(
            "high", ("101", "102", "103", "102", "104")
        )
        low = self._price(
            "low", ("99", "100", "101", "100", "102")
        )
        result = dispatcher.call(
            "market.technical_indicators",
            {
                "series": [
                    high.to_primitive(),
                    low.to_primitive(),
                    self.close.to_primitive(),
                ],
                "indicator": "supertrend_ai",
                "window": 1,
                "fast_window": None,
                "slow_window": None,
                "signal_window": None,
                "standard_deviation_multiplier": None,
                "minimum_factor": 1,
                "maximum_factor": 3,
                "factor_step": 1,
                "performance_memory": 2,
                "cluster": "best",
                "limit": 5,
            },
            tool_version="2.1.0",
        )
        self.assertEqual(
            tuple(item["metadata"]["component"] for item in result["series"]),
            (
                "trailing_stop",
                "adaptive_moving_average",
                "trend",
                "performance_index",
                "target_factor",
            ),
        )
        self.assertEqual(
            tuple(
                item["metadata"]["transformation_version"]
                for item in result["series"]
            ),
            ("2.1.0",) * 5,
        )
        output_schema = stage10_technical_indicator_output_series_schema_v21()
        for series in result["series"]:
            validate_schema(
                series, output_schema, code="invalid_output"
            )
        self.assertFalse(any(root.iterdir()))

    def test_kdj_v23_public_dispatch_is_typed_and_store_free(self) -> None:
        root = Path(self.temporary.name)
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        dispatcher = ToolDispatcher(
            explicit_store_map(root / "public-kdj-stores"),
            registry,
        )
        close = self._price("close", ("7", "8.5", "9", "10.5", "12"))
        high = self._price("high", ("10", "12", "14", "16", "18"))
        low = self._price("low", ("4", "5", "6", "8", "10"))
        result = dispatcher.call(
            "market.technical_indicators",
            {
                "series": [
                    high.to_primitive(),
                    low.to_primitive(),
                    close.to_primitive(),
                ],
                "indicator": "kdj",
                "window": 3,
                "fast_window": None,
                "slow_window": None,
                "signal_window": 2,
                "standard_deviation_multiplier": None,
                "minimum_factor": None,
                "maximum_factor": None,
                "factor_step": None,
                "performance_memory": None,
                "cluster": None,
                "sample_count": None,
                "aggregation_method": None,
                "limit": 5,
            },
            tool_version="2.3.0",
        )
        self.assertEqual(
            tuple(item["metadata"]["component"] for item in result["series"]),
            ("percent_k", "percent_d", "percent_j"),
        )
        self.assertEqual(
            tuple(
                item["metadata"]["transformation_version"]
                for item in result["series"]
            ),
            ("2.3.0",) * 3,
        )
        self.assertEqual(
            tuple(
                item["observations"][2]["value"]
                for item in result["series"]
            ),
            (Decimal("25"), Decimal("12.5"), Decimal("50")),
        )
        output_schema = stage10_technical_indicator_output_series_schema_v23()
        for series in result["series"]:
            validate_schema(series, output_schema, code="invalid_output")
            self.assertIn(
                "kdj_tradingview_presentation_excluded",
                series["warnings"],
            )
        self.assertFalse(any(root.iterdir()))

    def test_wavetrend_v25_direct_typed_dispatch_is_store_free(self) -> None:
        values = (
            1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3,
            4, 5, 6, 7, 6, 5, 4, 3, 4, 5,
        )
        close = self._swing_price("close", values)
        high = self._swing_price(
            "high", tuple(value + 1 for value in values)
        )
        low = self._swing_price(
            "low", tuple(value - 1 for value in values)
        )
        arguments = Stage10TechnicalIndicatorArgumentsV25(
            series=(high, low, close),
            indicator="wavetrend_crosses",
            window=2,
            fast_window=None,
            slow_window=None,
            signal_window=2,
            standard_deviation_multiplier=None,
            minimum_factor=None,
            maximum_factor=None,
            factor_step=None,
            performance_memory=None,
            cluster=None,
            sample_count=None,
            aggregation_method=None,
            percentile_window=None,
            percentile_high_factor=None,
            percentile_low_factor=None,
            limit=len(values),
        )
        result = invoke_stage10_technical_indicator(
            "market.technical_indicators",
            arguments,
            replace(
                self.context,
                tool_version="2.5.0",
                operation_graph_id=(
                    "tool_platform.market.technical_indicators.v2_5"
                ),
                operation_version="2.5.0",
            ),
        )
        self.assertEqual(
            tuple(series.metadata["component"] for series in result.series),
            (
                "wavetrend",
                "wavetrend_signal",
                "wavetrend_difference",
                "wavetrend_cross_signal",
            ),
        )
        self.assertEqual(
            tuple(series.metadata["unit"] for series in result.series),
            ("dimensionless",) * 4,
        )
        self.assertEqual(
            tuple(
                series.metadata["value_representation"]
                for series in result.series
            ),
            ("ratio", "ratio", "ratio", "regime"),
        )
        self.assertEqual(
            tuple(
                series.metadata["transformation_version"]
                for series in result.series
            ),
            ("2.5.0",) * 4,
        )
        components = {
            str(series.metadata["component"]): series
            for series in result.series
        }
        self.assertEqual(
            tuple(
                (index, observation.value)
                for index, observation in enumerate(
                    components["wavetrend_cross_signal"].observations
                )
                if observation.value not in {None, Decimal("0")}
            ),
            ((11, Decimal("1")), (15, Decimal("-1")), (19, Decimal("1"))),
        )
        self.assertIn(
            "wavetrend_crosses_tradingview_presentation_excluded",
            components["wavetrend"].warnings,
        )
        output_schema = stage10_technical_indicator_output_series_schema_v25()
        for series in result.series:
            validate_schema(
                series.to_primitive(), output_schema, code="invalid_output"
            )
        self.assertFalse(any(Path(self.temporary.name).iterdir()))


    def test_rolling_regression_line_v27_direct_typed_dispatch_is_store_free(
        self,
    ) -> None:
        arguments = Stage10TechnicalIndicatorArgumentsV27(
            series=(self.close,),
            indicator="rolling_regression_line",
            window=3,
            fast_window=None,
            slow_window=None,
            signal_window=None,
            standard_deviation_multiplier=None,
            minimum_factor=None,
            maximum_factor=None,
            factor_step=None,
            performance_memory=None,
            cluster=None,
            sample_count=None,
            aggregation_method=None,
            percentile_window=None,
            percentile_high_factor=None,
            percentile_low_factor=None,
            start=None,
            increment=None,
            maximum=None,
            limit=5,
        )
        result = invoke_stage10_technical_indicator(
            "market.technical_indicators",
            arguments,
            replace(
                self.context,
                tool_version="2.7.0",
                operation_graph_id=(
                    "tool_platform.market.technical_indicators.v2_7"
                ),
                operation_version="2.7.0",
            ),
        )
        self.assertEqual(len(result.series), 1)
        series = result.series[0]
        self.assertEqual(series.metadata["component"], "rolling_regression_line")
        self.assertEqual(series.metadata["value_representation"], "price")
        self.assertEqual(series.metadata["transformation_version"], "2.7.0")
        self.assertEqual(
            tuple(observation.value for observation in series.observations[:3]),
            (None, None, Decimal("102")),
        )
        validate_schema(
            series.to_primitive(),
            stage10_technical_indicator_output_series_schema_v27(),
            code="invalid_output",
        )
        self.assertFalse(any(Path(self.temporary.name).iterdir()))

    def test_parabolic_sar_v26_direct_typed_dispatch_is_store_free(
        self,
    ) -> None:
        close = self._swing_price(
            "close", (9, 10, 11, 12, 10, 8, 7, 12)
        )
        high = self._swing_price(
            "high", (10, 11, 12, 13, 12, 10, 9, 13)
        )
        low = self._swing_price(
            "low", (8, 9, 10, 11, 9, 7, 6, 11)
        )
        arguments = Stage10TechnicalIndicatorArgumentsV26(
            series=(high, low, close),
            indicator="parabolic_sar",
            window=None,
            fast_window=None,
            slow_window=None,
            signal_window=None,
            standard_deviation_multiplier=None,
            minimum_factor=None,
            maximum_factor=None,
            factor_step=None,
            performance_memory=None,
            cluster=None,
            sample_count=None,
            aggregation_method=None,
            percentile_window=None,
            percentile_high_factor=None,
            percentile_low_factor=None,
            start=Decimal("0.02"),
            increment=Decimal("0.02"),
            maximum=Decimal("0.2"),
            limit=8,
        )
        result = invoke_stage10_technical_indicator(
            "market.technical_indicators",
            arguments,
            replace(
                self.context,
                tool_version="2.6.0",
                operation_graph_id=(
                    "tool_platform.market.technical_indicators.v2_6"
                ),
                operation_version="2.6.0",
            ),
        )
        self.assertEqual(len(result.series), 1)
        series = result.series[0]
        self.assertEqual(series.metadata["component"], "parabolic_sar")
        self.assertEqual(series.metadata["value_representation"], "price")
        self.assertEqual(series.metadata["transformation_version"], "2.6.0")
        self.assertEqual(
            tuple(observation.value for observation in series.observations),
            (
                None,
                Decimal("8"),
                Decimal("8"),
                Decimal("8.16"),
                Decimal("8.4504"),
                Decimal("13"),
                Decimal("12.88"),
                Decimal("6"),
            ),
        )
        self.assertIn(
            "parabolic_sar_tradingview_presentation_excluded",
            series.warnings,
        )
        validate_schema(
            series.to_primitive(),
            stage10_technical_indicator_output_series_schema_v26(),
            code="invalid_output",
        )
        self.assertFalse(any(Path(self.temporary.name).iterdir()))

    def test_swing_structure_forecast_v22_direct_typed_dispatch_is_causal(
        self,
    ) -> None:
        values = (
            *range(100, 110),
            *range(108, 99, -1),
            *range(101, 111),
            *range(109, 99, -1),
            *range(101, 111),
            *range(109, 99, -1),
            *range(101, 111),
        )
        close = self._swing_price("close", values)
        high = self._swing_price(
            "high", tuple(value + 1 for value in values)
        )
        low = self._swing_price(
            "low", tuple(value - 1 for value in values)
        )
        result = self._invoke_v22(
            self._swing_arguments(
                (close, high, low), limit=len(values)
            )
        )

        expected_contracts = (
            ("confirmed_swing_high", "provider_native_currency", "price", "1"),
            ("confirmed_swing_low", "provider_native_currency", "price", "1"),
            ("swing_direction", "dimensionless", "regime", "1"),
            ("forecast_origin", "provider_native_currency", "price", "1"),
            ("forecast_target", "provider_native_currency", "price", "1"),
            ("forecast_percent", "percent", "percentage", "100"),
            ("forecast_duration_bars", "dimensionless", "bar_count", "1"),
            (
                "forecast_standard_deviation",
                "percent",
                "percentage",
                "100",
            ),
            ("forecast_band_half", "provider_native_currency", "price", "1"),
            (
                "forecast_origin_age_bars",
                "dimensionless",
                "bar_count",
                "1",
            ),
        )
        self.assertEqual(len(result.series), 10)
        self.assertEqual(
            tuple(
                (
                    series.metadata["component"],
                    series.metadata["unit"],
                    series.metadata["value_representation"],
                    series.metadata["scale"],
                )
                for series in result.series
            ),
            expected_contracts,
        )
        output_schema = stage10_technical_indicator_output_series_schema_v22()
        source_grid = tuple(
            (observation.period_start, observation.period_end)
            for observation in close.observations
        )
        for series in result.series:
            self.assertEqual(len(series.observations), len(values))
            self.assertEqual(
                tuple(
                    (observation.period_start, observation.period_end)
                    for observation in series.observations
                ),
                source_grid,
            )
            self.assertEqual(
                series.metadata["transformation"],
                "technical_indicator_v2_2",
            )
            self.assertEqual(series.metadata["transformation_version"], "2.2.0")
            self.assertEqual(
                series.metadata["source_observation_fields"],
                ("high", "low", "close"),
            )
            validate_schema(
                series.to_primitive(), output_schema, code="invalid_output"
            )

        components = {
            str(series.metadata["component"]): series
            for series in result.series
        }
        self.assertEqual(
            components["forecast_target"].observations[0].missing_reason,
            "insufficient_history",
        )
        self.assertEqual(
            components["forecast_target"].observations[9].missing_reason,
            "insufficient_completed_swings",
        )
        self.assertEqual(
            components["confirmed_swing_high"].observations[10].value,
            Decimal("110"),
        )
        self.assertEqual(
            components["confirmed_swing_low"].observations[19].value,
            Decimal("99"),
        )
        self.assertTrue(
            any(
                observation.value is not None
                for observation in components["forecast_target"].observations
            )
        )
        self.assertEqual(
            components["forecast_band_half"].observations[43].missing_reason,
            "insufficient_atr_history",
        )

        prefix_count = 49
        prefix_result = self._invoke_v22(
            self._swing_arguments(
                (
                    self._swing_price("close", values[:prefix_count]),
                    self._swing_price(
                        "high",
                        tuple(
                            value + 1 for value in values[:prefix_count]
                        ),
                    ),
                    self._swing_price(
                        "low",
                        tuple(
                            value - 1 for value in values[:prefix_count]
                        ),
                    ),
                ),
                limit=prefix_count,
            )
        )
        for full, prefix in zip(result.series, prefix_result.series, strict=True):
            self.assertEqual(
                tuple(
                    (observation.value, observation.missing_reason)
                    for observation in full.observations[:prefix_count]
                ),
                tuple(
                    (observation.value, observation.missing_reason)
                    for observation in prefix.observations
                ),
            )

        excluded_presentation_warnings = {
            "swing_structure_forecast_forward_bar_visuals_excluded",
            "swing_structure_forecast_support_resistance_state_excluded",
            "swing_structure_forecast_tradingview_presentation_excluded",
        }
        self.assertTrue(
            excluded_presentation_warnings.issubset(
                set(result.series[0].warnings)
            )
        )
        self.assertIn(
            "swing_structure_forecast_is_causal_unrolled_latest_bar_adaptation",
            result.series[0].warnings,
        )
        self.assertFalse(any(Path(self.temporary.name).iterdir()))

    def test_swing_structure_forecast_v22_output_budget_is_store_free(
        self,
    ) -> None:
        values = tuple(range(100, 110))
        arguments = self._swing_arguments(
            (
                self._swing_price("close", values),
                self._swing_price(
                    "high", tuple(value + 1 for value in values)
                ),
                self._swing_price(
                    "low", tuple(value - 1 for value in values)
                ),
            ),
            limit=len(values),
        )
        with patch(
            "quant_data.tool_platform.technical_indicator_adapter."
            "MAX_OUTPUT_VALUES",
            99,
        ):
            with self.assertRaisesRegex(
                ResourceLimitError, "output exceeds the value limit"
            ):
                self._invoke_v22(arguments)
        self.assertFalse(any(Path(self.temporary.name).iterdir()))

    def test_required_roles_grid_identity_truncation_and_limit_fail_closed(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValidationError, "requires high"):
            self._invoke(
                self._arguments(
                    (self.close,), "average_true_range", window=3
                )
            )
        mismatched = TimeSeries(
            series_id="mismatched",
            metadata=dict(self.close.metadata),
            observations=(
                replace(
                    self.close.observations[0],
                    version_id="different-version",
                ),
                *self.close.observations[1:],
            ),
            warnings=self.close.warnings,
            audit=dict(self.close.audit),
            provenance=dict(self.close.provenance),
            truncated=False,
        )
        high = self._price(
            "high", ("101", "102", "103", "102", "104")
        )
        low = self._price(
            "low", ("99", "100", "101", "100", "102")
        )
        with self.assertRaisesRegex(ValidationError, "one Stage 10 selection"):
            self._invoke(
                self._arguments(
                    (high, low, mismatched),
                    "average_true_range",
                    window=3,
                )
            )
        truncated = replace(self.close, truncated=True, lineage_digest="")
        with self.assertRaisesRegex(ValidationError, "truncated"):
            self._invoke(
                self._arguments((truncated,), "sma", window=3)
            )
        with self.assertRaises(ResourceLimitError):
            self._invoke(
                self._arguments(
                    (self.close,), "sma", window=3, limit=4
                )
            )


if __name__ == "__main__":
    unittest.main()
