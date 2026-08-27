from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
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
