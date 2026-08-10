from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_data.contracts import TimeSeries
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.registry import load_registry
from quant_data.stage4 import run_clean_stage4_rebuild
from quant_data.stores import StoreMap, stable_id
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.domain_operations import (
    DOMAIN_OPERATION_NAMES,
    invoke_domain_operation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _source_store_map(work_root: Path) -> StoreMap:
    source = work_root / "stage3" / "stage2" / "source"
    return StoreMap.four_explicit(
        market=source / "market.sqlite",
        macro=source / "macro.sqlite",
        company=source / "company.sqlite",
        news=source / "news.sqlite",
    )


def _spy_id() -> str:
    return stable_id(
        "instrument",
        "fixture.market.instruments",
        "fixture.market.instrument.spy.v1",
    )


def _research_series() -> TimeSeries:
    return TimeSeries(
        series_id="fixture.research.input",
        metadata={"frequency": "monthly", "unit": "index"},
        observations=(),
        warnings=(),
        audit={"mode": "latest"},
        provenance={
            "dataset_id": "fixture.macro.stage3_catalog",
            "store_role": "macro",
        },
    )


class DomainOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls._root = Path(cls._temporary.name)
        cls._work_root = cls._root / "stage4"
        run_clean_stage4_rebuild(
            project_root=PROJECT_ROOT,
            work_root=cls._work_root,
        )
        cls.stores = _source_store_map(cls._work_root)
        cls.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        cls._baseline = mutation_fingerprint(cls.stores)
        cls._registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def tearDown(self) -> None:
        self.assertEqual(self._baseline["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def _context(self, name: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self._registry_sha256,
            request_id="domain-operation-test",
            api_version="1.0",
            tool_name=name,
            tool_version="1.0.0",
            operation_graph_id=f"tool_platform.{name}",
            operation_version="1.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
        )

    def _call(self, name: str, arguments: dict[str, Any]):
        return invoke_domain_operation(
            name,
            arguments,
            self._context(name),
            self.registry,
        )

    @staticmethod
    def _query(
        identifiers: list[str],
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return {
            "identifiers": identifiers,
            "mode": mode,
            "as_of": as_of,
            "start_date": None,
            "end_date": None,
            "parameters": [],
            "limit": limit,
        }

    def test_closed_domain_inventory_is_exact(self) -> None:
        expected = {
            "macro.search_series",
            "macro.describe_series",
            "macro.release_surprises",
            "macro.revision_analysis",
            "macro.align_us_recessions",
            "macro.standardize_surprises",
            "macro.get_liquidity_snapshot",
            "macro.get_liquidity_impulse",
            "macro.get_credit_conditions",
            "macro.regime_snapshot",
            "company.search_issuers",
            "company.search_filings",
            "company.get_fundamentals",
            "company.get_corporate_actions",
            "company.get_share_count_history",
            "company.get_earnings_calendar",
            "company.get_consensus_history",
            "company.get_guidance_history",
            "company.get_estimate_revisions",
            "company.get_earnings_setup",
            "energy.get_electricity_retail_sales",
            "energy.get_weekly_fundamentals",
            "market.search_instruments",
            "market.get_returns",
            "market.get_forward_returns",
            "market.technical_indicators",
            "market.cross_sectional_performance",
            "rates.get_funding_conditions",
            "rates.get_repo_facility_usage",
            "rates.curve_analytics",
            "options.search_captures",
            "options.search_contracts",
            "options.get_surface_snapshot",
            "options.surface_diagnostics",
            "options.screen_contracts",
            "options.strategy_scenario",
            "news.search",
            "research.liquidity_credit_state",
        }
        self.assertEqual(DOMAIN_OPERATION_NAMES, expected)
        self.assertNotIn("macro.get_series", DOMAIN_OPERATION_NAMES)
        self.assertNotIn("macro.get_intraday_releases", DOMAIN_OPERATION_NAMES)
        self.assertNotIn("timeseries.transform", DOMAIN_OPERATION_NAMES)

    def test_established_fixture_facts_are_scalar_lineaged_and_read_only(self) -> None:
        results = (
            self._call(
                "macro.align_us_recessions",
                self._query([], limit=100),
            ),
            self._call(
                "company.get_fundamentals",
                self._query(["0001000001"], limit=100),
            ),
            self._call(
                "news.search",
                {
                    "query": "Northstar",
                    "as_of": "2026-06-05T00:00:00Z",
                    "limit": 100,
                },
            ),
            self._call(
                "options.get_surface_snapshot",
                self._query([_spy_id()], limit=100),
            ),
        )
        for result in results:
            with self.subTest(tool=result.tool):
                self.assertEqual(result.status, "ok")
                self.assertTrue(result.records)
                self.assertTrue(result.lineage)
                primitive = result.to_primitive()
                serialized = dumps_strict(primitive)
                self.assertNotIn(str(self._root), serialized)
                self.assertNotIn("sqlite", serialized.lower())
                for record in result.records:
                    for field in record.fields:
                        self.assertNotIsInstance(field.value, float)

        options = results[-1]
        option_values = {
            field.value
            for record in options.records
            for field in record.fields
            if field.name in {"last_price", "implied_volatility", "delta"}
            and field.value is not None
        }
        self.assertTrue(any(isinstance(value, Decimal) for value in option_values))

    def test_point_in_time_and_fixed_feed_are_preserved(self) -> None:
        historical = self._call(
            "news.search",
            {
                "query": "Northstar",
                "as_of": "2026-06-05T00:00:00Z",
                "limit": 100,
            },
        )
        self.assertEqual(historical.status, "ok")
        self.assertTrue(historical.records)
        self.assertTrue(
            all(lineage.store_role == "news" for lineage in historical.lineage)
        )

        options = self._call(
            "options.get_surface_snapshot",
            {
                **self._query([_spy_id()], limit=100),
                "parameters": [{"name": "resolved_feed", "value": "untrusted"}],
            },
        )
        self.assertEqual(options.status, "ok")
        resolved = {
            field.value
            for record in options.records
            for field in record.fields
            if field.name == "resolved_feed"
        }
        self.assertEqual(resolved, {"fixture_options"})

    def test_unestablished_composites_are_honest_and_deterministic(self) -> None:
        arguments = self._query(["fixture.market.instrument.spy.v1"], limit=25)
        result = self._call("market.get_returns", arguments)
        self.assertEqual(result.status, "not_established")
        self.assertFalse(result.records)
        self.assertEqual(result.diagnostics[0].code, "fixture_semantics_not_established")
        self.assertEqual(result.truncation.returned_count, 0)

        research = self._call(
            "research.liquidity_credit_state",
            {
                "series": [_research_series()],
                "parameters": [],
                "limit": 25,
                "as_of": None,
                "unsafe_ok": False,
            },
        )
        self.assertEqual(research.status, "not_established")
        self.assertEqual(research.research_contract.status, "declared")
        contract = research.research_contract.contract
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.sample["input_series_count"], 1)
        self.assertEqual(contract.sample["input_observation_count"], 0)
        self.assertEqual(len(contract.input_lineage), 1)
        self.assertEqual(research.lineage, contract.input_lineage)

    def test_unknown_operation_is_fail_closed(self) -> None:
        with self.assertRaises(LookupError):
            self._call("macro.dynamic_import", {"limit": 1})


if __name__ == "__main__":
    unittest.main()
