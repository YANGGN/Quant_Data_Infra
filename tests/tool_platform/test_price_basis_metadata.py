"""Public composition and frozen-contract checks for close metadata successors."""
from __future__ import annotations
import copy
import hashlib
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.boundary import ToolDispatcher
from quant_data.contracts import TimeSeries
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.registry import price_basis_registry_profile
from quant_data.schema import validate_schema
from quant_data.tool_platform.price_basis import BASIS, BINDING, DOCUMENTATION
from quant_data.tool_platform.price_basis_versions import PRICE_BASIS_VERSIONS
from quant_data.tool_platform.market_statistics import stage10_market_statistic_series_schema
from quant_data.tool_platform.technical_indicator_adapter import stage10_technical_indicator_input_series_schema
from tests.tool_platform import test_market_cross_sectional_performance_v2 as fixtures

ROOT = Path(__file__).resolve().parents[2]


class PriceBasisMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MarketCrossSectionalPerformanceV2Tests(methodName="runTest")
        cls.fixture.setUp()
        cls.registry = cls.fixture.registry
        cls.dispatcher = ToolDispatcher(cls.fixture.stores, cls.registry)

    @classmethod
    def tearDownClass(cls):
        try:
            assert cls.fixture.baseline["sha256"] == mutation_fingerprint(cls.fixture.stores)["sha256"]
        finally:
            cls.fixture.temporary.cleanup()

    def _prices(self, version="2.0.0", **extra):
        return self.dispatcher.call("market.get_price_series", {
            "ticker": "AAA", "start_date": "2026-01-02", "end_date": "2026-01-03",
            "mode": "latest", "as_of": None, "date_only_policy": "completed_date",
            "limit": 100, **extra,
        }, tool_version=version)

    def _returns(self, version="2.1.0", name="market.get_returns"):
        return self.dispatcher.call(name, {
            "identifier": "AAA", "identifier_kind": "provider_symbol",
            "start_date": "2026-01-02", "end_date": "2026-01-03",
            "mode": "latest", "as_of": None, "date_only_policy": "completed_date",
            "method": "simple", "horizon": 1, "limit": 100,
        }, tool_version=version)

    def test_price_and_return_values_are_unchanged_and_compose(self):
        old = self._prices("1.0.0")
        new = self._prices()
        for before, after in zip(old["series"], new["series"], strict=True):
            self.assertEqual([o["value"] for o in before["observations"]],
                             [o["value"] for o in after["observations"]])
            expected = BASIS if after["metadata"]["observation_field"] == "close" else "not_established"
            self.assertEqual(after["metadata"]["adjustment_status"], expected)
            self.assertFalse(after["metadata"]["price_adjustment_applied_by_tool"])
            self.assertIsNone(after["metadata"]["source_publication_time"])
        for name in ("market.get_returns", "market.get_forward_returns"):
            before = self._returns("2.0.0", name)["series"][0]
            after = self._returns(name=name)["series"][0]
            self.assertEqual([o["value"] for o in before["observations"]],
                             [o["value"] for o in after["observations"]])
            self.assertEqual(after["metadata"]["adjustment_status"], BASIS)
            self.assertTrue(any(o["missing_reason"] is not None for o in after["observations"]))
        close = new["series"][-1]
        indicator_arguments = copy.deepcopy(
            self.registry.tool("market.technical_indicators", "2.8.0")["examples"][0]
        )
        indicator_arguments.update(series=[close], indicator="sma", window=2, limit=100)
        indicator = self.dispatcher.call("market.technical_indicators",
                                        indicator_arguments, tool_version="2.8.0")
        self.assertEqual(indicator["series"][0]["observations"][-1]["value"], 105)
        self.assertEqual(indicator["series"][0]["metadata"]["adjustment_status"], BASIS)
        with self.assertRaises(ValidationError):
            self.dispatcher.call("market.technical_indicators",
                                 indicator_arguments, tool_version="2.7.0")
        result = self.dispatcher.call("timeseries.describe", {
            "series": self._returns()["series"][0], "limit": 100,
        }, tool_version="2.1.0")
        self.assertEqual(result["status"], "ok")

    def test_cutoff_empty_bounds_and_frozen_predecessor(self):
        with self.assertRaises(ValidationError):
            self._prices(mode="as_of", as_of="2026-08-19T00:00:00Z")
        historical = self._prices(mode="as_of", as_of="2026-08-20T00:00:00Z")
        self.assertEqual(historical["series"][-1]["metadata"]["adjustment_status"], BASIS)
        self.assertEqual(historical["series"][-1]["audit"]["point_in_time_scope"], "retained_local_captures")
        empty = self._prices(start_date="2026-01-05", end_date="2026-01-06")
        self.assertEqual(empty["series"][-1]["metadata"]["adjustment_status"], "not_established")
        self.assertIsNone(empty["series"][-1]["metadata"]["source_adjustment_binding"])
        prior = price_basis_registry_profile(self.registry)
        self.assertEqual(prior.source_sha256, "7c729d2d80d84faa121ef33986d68590bebf7b0018ade76d641c00214b783f0c")
        for old in prior.tools:
            self.assertEqual(old, self.registry.tool(old["id"]))
        for policy in prior.tool_version_policies:
            for old in policy["variants"]:
                self.assertEqual(old, self.registry.tool(old["id"], old["version"]))
        self.assertEqual(hashlib.sha256((ROOT / "quant_data/generated/tool_contract_schemas_v1.json").read_bytes()).hexdigest(),
                         "a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87")

    def _bound_example(self, value):
        if isinstance(value, dict):
            if value.get("contract") == "quant_data.timeseries":
                schema = (stage10_market_statistic_series_schema()
                          if value["metadata"]["value_representation"] == "return"
                          else stage10_technical_indicator_input_series_schema())
                source = self.dispatcher._timeseries_from_public(value, schema=schema)
                if source.metadata.get("observation_field") != "close":
                    return value
                metadata = {
                    **source.metadata, "adjustment_status": BASIS,
                    "source_price_field": "close", "price_adjustment_applied_by_tool": False,
                    "source_adjustment_binding": BINDING, "source_adjustment_documentation": DOCUMENTATION,
                    "source_capture_count": 1, "adjustment_vintage_status": "single_provider_capture",
                    "source_publication_time": None,
                }
                # This caller-supplied vector is interpreted as p-values by the
                # multiple-testing tool; signed-return examples are invalid there.
                observations = tuple(
                    replace(item, quality_flags=tuple(sorted({
                        flag for flag in item.quality_flags
                        if not flag.startswith(("adjustment_status:", "adjustment_semantics:"))
                    } | {"adjustment_status:" + BASIS})))
                    for item in source.observations
                )
                if getattr(self, "_example_tool", None) == "stats.multiple_testing":
                    from decimal import Decimal
                    observations = tuple(replace(item, value=Decimal("0.05")) if item.value is not None else item
                                         for item in observations)
                warnings = tuple(code for code in source.warnings if code not in {
                    "adjustment_and_total_return_semantics_not_established",
                    "raw_price_adjustment_semantics_not_established",
                })
                return replace(source, metadata=metadata, observations=observations,
                               warnings=warnings, lineage_digest="").to_primitive()
            return {key: self._bound_example(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._bound_example(item) for item in value]
        return value

    def test_all_store_free_successors_accept_price_basis_examples(self):
        exercised = []
        for name, (_, version) in PRICE_BASIS_VERSIONS.items():
            declaration = self.registry.tool(name, version)
            if declaration["stores"]:
                continue
            for original in declaration["examples"]:
                with self.subTest(tool=name, version=version):
                    self._example_tool = name
                    arguments = self._bound_example(copy.deepcopy(original))
                    result = self.dispatcher.call(name, arguments, tool_version=version)
                    validate_schema(result, declaration["output_schema"])
                    exercised.append(name)
        self.assertEqual(set(exercised), {
            name for name, (_, version) in PRICE_BASIS_VERSIONS.items()
            if not self.registry.tool(name, version)["stores"]
        })

    def test_cross_sectional_successor_exposes_source_binding(self):
        name = "market.cross_sectional_performance"
        arguments = copy.deepcopy(self.registry.tool(name, "2.2.0")["examples"][0])
        arguments.update(tickers=["AAA", "CCC"], start_date="2026-01-02",
                         end_date="2026-01-03", mode="latest", as_of=None,
                         benchmark_ticker="AAA", window=2, limit=100)
        old = self.dispatcher.call(name, arguments, tool_version="2.1.0")
        new = self.dispatcher.call(name, arguments, tool_version="2.2.0")
        for before, after in zip(old["records"], new["records"], strict=True):
            old_fields = {item["name"]: item["value"] for item in before["fields"]}
            new_fields = {item["name"]: item["value"] for item in after["fields"]}
            self.assertEqual(new_fields["source_adjustment_status"], BASIS)
            self.assertEqual({k: new_fields[k] for k in old_fields}, old_fields | {
                "source_lineage_digest": new_fields["source_lineage_digest"]
            })

    def test_news_event_successor_preserves_retrospective_outcome(self):
        from tests.tool_platform import test_news_research_tools as news_fixtures
        fixture = news_fixtures.NewsResearchToolRoutesTests(methodName="runTest")
        fixture.setUp()
        try:
            search = fixture._call("news.search", replace(fixture._v22_arguments(), limit=3),
                                   version="2.2.0")
            article = next(news_fixtures._fields(row) for row in search.records
                           if news_fixtures._fields(row)["symbol"] == "AAPL")
            dispatcher = ToolDispatcher(fixture.stores, fixture.registry)
            args = {"article_version_id": article["article_version_id"],
                    "instrument_id": news_fixtures._AAPL_INSTRUMENT_ID,
                    "pre_observations": 0, "post_observations": 0}
            old = dispatcher.call("research.news_event_impact", args, tool_version="1.0.0")
            new = dispatcher.call("research.news_event_impact", args, tool_version="2.0.0")
            old_fields = {item["name"]: item["value"] for item in old["records"][0]["fields"]}
            new_fields = {item["name"]: item["value"] for item in new["records"][0]["fields"]}
            self.assertEqual(new_fields["source_adjustment_status"], BASIS)
            self.assertEqual({k: new_fields[k] for k in old_fields}, old_fields)
            self.assertFalse(new_fields["causal_interpretation"])
            self.assertIsNone(new_fields["source_publication_time"])
        finally:
            fixture.tearDown()

    def test_successors_reject_incomplete_or_contradictory_basis(self):
        from quant_data.tool_platform.price_basis import BASIS_FIELDS, extend_price_basis_schema
        from quant_data.tool_platform.market_prices import stage10_market_price_series_schema
        close = self.dispatcher._timeseries_from_public(
            self._prices()["series"][-1],
            schema=extend_price_basis_schema(stage10_market_price_series_schema()),
        )
        base_args = copy.deepcopy(self.registry.tool("market.technical_indicators", "2.8.0")["examples"][0])
        base_args.update(indicator="sma", window=2, limit=100)
        invalid = [
            replace(close, metadata={k: v for k, v in close.metadata.items() if k not in BASIS_FIELDS},
                    lineage_digest=""),
            replace(close, warnings=(*close.warnings, "adjustment_and_total_return_semantics_not_established"),
                    lineage_digest=""),
            replace(close, observations=tuple(
                replace(item, quality_flags=(*item.quality_flags, "adjustment_status:not_established"))
                for item in close.observations), lineage_digest=""),
        ]
        for series in invalid:
            with self.subTest(metadata=series.metadata), self.assertRaises(ValidationError):
                self.dispatcher.call("market.technical_indicators",
                                     {**base_args, "series": [series]}, tool_version="2.8.0")
        from quant_data.tool_platform.market_statistics import stage10_market_return_example_series
        with self.assertRaisesRegex(ValidationError, "consistent price adjustment basis"):
            self.dispatcher.call("timeseries.correlation", {
                "series": [self._returns()["series"][0], stage10_market_return_example_series("BBB")],
                "limit": 100,
            }, tool_version="2.1.0")

    def test_multi_price_indicator_scopes_source_adjustment_flags(self):
        args = copy.deepcopy(self.registry.tool("market.technical_indicators", "2.8.0")["examples"][0])
        args.update(series=self._prices()["series"], indicator="average_true_range", window=2, limit=100)
        result = self.dispatcher.call("market.technical_indicators", args, tool_version="2.8.0")
        output = result["series"][0]
        self.assertEqual(output["metadata"]["adjustment_status"], "not_established")
        self.assertEqual(output["metadata"]["source_close_adjustment_status"], BASIS)
        for observation in output["observations"]:
            flags = observation["quality_flags"]
            self.assertEqual([flag for flag in flags if flag.startswith("adjustment_status:")],
                             ["adjustment_status:not_established"])
            self.assertIn("source_adjustment_status:close:" + BASIS, flags)

    def test_invalid_split_only_claim_is_rejected(self):
        from quant_data.tool_platform.price_basis import expected_price_metadata
        close = self._prices()["series"][-1]
        metadata = dict(close["metadata"])
        for changes in (
            {"source_price_field": "adjClose"},
            {"source_adjustment_binding": None},
            {"source_capture_count": 0},
            {"price_adjustment_applied_by_tool": True},
            {"source_publication_time": "2026-01-02T00:00:00Z"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                expected_price_metadata({**metadata, **changes}, {"adjustment_status": "not_established"})
