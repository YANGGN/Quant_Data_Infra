"""End-to-end coverage for the explicit Stage 10 research-analytic successors."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from quant_data.boundary import ToolDispatcher
from quant_data.errors import ValidationError
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.tool_platform.market_statistics import (
    stage10_market_return_example_series,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXECUTABLE_EXAMPLE_TOOLS = (
    "research.point_in_time_panel",
    "research.event_study",
    "alpha.signal_diagnostics",
    "research.walk_forward_backtest",
    "research.robustness_suite",
    "forecast.evaluate",
)


class Stage10ResearchAnalyticsV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_generated_examples_execute_without_store_access(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            dispatcher = ToolDispatcher(
                explicit_store_map(root / "stores"),
                self.registry,
            )
            for name in EXECUTABLE_EXAMPLE_TOOLS:
                with self.subTest(name=name):
                    declaration = self.registry.tool(name, "2.0.0")
                    result = dispatcher.call(
                        name,
                        dict(declaration["examples"][0]),
                        tool_version="2.0.0",
                    )
                    self.assertEqual(result["tool"], name)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["series"], [])
            self.assertFalse(any(root.iterdir()))

    def test_multiple_testing_accepts_lineage_valid_p_values(self) -> None:
        source = stage10_market_return_example_series("PVALUES")
        values = (None, Decimal("0.05"), Decimal("0.5"))
        missing = ("insufficient_history", None, None)
        p_values = replace(
            source,
            observations=tuple(
                replace(
                    observation,
                    value=values[index],
                    missing_reason=missing[index],
                )
                for index, observation in enumerate(source.observations)
            ),
            lineage_digest="",
        )

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            dispatcher = ToolDispatcher(
                explicit_store_map(root / "stores"),
                self.registry,
            )
            declaration = self.registry.tool("stats.multiple_testing", "2.0.0")
            arguments = dict(declaration["examples"][0])
            arguments["series"] = [p_values.to_primitive()]
            result = dispatcher.call(
                "stats.multiple_testing",
                arguments,
                tool_version="2.0.0",
            )
            self.assertFalse(any(root.iterdir()))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool"], "stats.multiple_testing")
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["series"], [])

    def test_frozen_v1_still_rejects_stage10_successor_input(self) -> None:
        name = "research.point_in_time_panel"
        arguments = dict(self.registry.tool(name, "2.0.0")["examples"][0])
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            dispatcher = ToolDispatcher(
                explicit_store_map(root / "stores"),
                self.registry,
            )
            with self.assertRaises(ValidationError):
                dispatcher.call(name, arguments, tool_version="1.0.0")
            self.assertFalse(any(root.iterdir()))


if __name__ == "__main__":
    unittest.main()
