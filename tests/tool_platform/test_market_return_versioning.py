from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.errors import RegistryError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    alpaca_spy_options_registry_profile,
    econometrics_model_suite_v3_registry_profile,
    bea_personal_income_registry_profile,
    fmp_calendar_incremental_registry_profile,
    econometrics_v2_registry_profile,
    load_registry,
    market_price_series_v1_registry_profile,
    market_available_ticker_v1_registry_profile,
    market_return_v2_registry_profile,
    robust_econometrics_v21_registry_profile,
    stationarity_v21_registry_profile,
    structural_breaks_v2_registry_profile,
    timeseries_analysis_v2_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.schema import validate_schema
from quant_data.tool_platform.arguments import parse_arguments
from quant_data.tool_platform.generate import generate, generated_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
V1_CATALOG = (
    PROJECT_ROOT / "quant_data" / "generated" / "tool_contract_schemas_v1.json"
)
V2_CATALOG = (
    PROJECT_ROOT / "quant_data" / "generated" / "tool_contract_schemas_v2.json"
)
V1_CATALOG_SHA256 = (
    "a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87"
)
V2_CATALOG_SHA256 = (
    "554873c79f58abbee6f4fbf83e4a6767153c9ff920651825d8cac3bd6075905f"
)
CURRENT_REGISTRY_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)
PRE_ALPACA_SPY_OPTIONS_REGISTRY_SHA256 = (
    "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
)
PRE_AVAILABLE_TICKER_REGISTRY_SHA256 = (
    "835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3"
)
PRE_AVAILABLE_TICKER_CATALOG_SHA256 = (
    "0f89da921b37d210c596646b3c4f47e4dbe27c2fe2579d8772c6db2bed312398"
)
PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256 = (
    "72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7"
)
PRE_MARKET_PRICE_SERIES_CATALOG_SHA256 = (
    "2c9424a0ff9cda9130d559c2dacb6cef55ff9ff8537191285219d608411b7f24"
)
PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SHA256 = (
    "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
)
PRE_ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SHA256 = (
    "3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547"
)
PRE_ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SHA256 = (
    "e0650e5b76ec9a851220c2395c34f9172f1380b5a5c9b2bc681056c655bdeb5b"
)
PRE_STRUCTURAL_BREAKS_V2_REGISTRY_SHA256 = (
    "2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b"
)
PRE_STRUCTURAL_BREAKS_V2_CATALOG_SHA256 = (
    "529d904d46003c53785926730279dc4c37bf09b7d15f99c366122c416c4af26e"
)
PRE_STATIONARITY_V21_REGISTRY_SHA256 = (
    "5c9702d8ca4c2f896083471cc60adecfd3a43c72d45694d04688ab07e6330035"
)
PRE_STATIONARITY_V21_CATALOG_SHA256 = (
    "7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9"
)

PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256 = (
    "a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e"
)
PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SHA256 = (
    "5250c18b70066de734cb2a4715ec20825f4a587892ba70728065dde82a4a239c"
)
PRE_ECONOMETRICS_V2_REGISTRY_SHA256 = (
    "eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb"
)
PRE_ECONOMETRICS_V2_CATALOG_SHA256 = (
    "3ae30774d87c31217204da2240a56124c2e732a14f9fb6e38a57e3d6d7341b79"
)
PRE_TIMESERIES_V2_REGISTRY_SHA256 = (
    "4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d"
)
PRE_TIMESERIES_V2_CATALOG_SHA256 = (
    "2697d9abc49880a156b7d607d59aa6903f518fbd16d8d764adc0b8c0b257415b"
)
PREDECESSOR_REGISTRY_SHA256 = (
    "d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0"
)


class MarketReturnVersioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_generator_accepts_only_current_or_exact_direct_predecessor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            registry_path = root / CANONICAL_REGISTRY_PATH
            registry_path.parent.mkdir(parents=True)

            stale = bea_personal_income_registry_profile(self.registry)
            self.assertEqual(stale.revision, "2.34.0")
            registry_path.write_text(
                json.dumps(
                    stale.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "exact reviewed registry source",
            ):
                generated_bytes(root)

            relabeled = dict(stale.raw)
            relabeled["registry_version"] = "2.38.0"
            registry_path.write_text(
                json.dumps(
                    relabeled,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "exact reviewed registry source",
            ):
                generated_bytes(root)

            incremental_predecessor = (
                fmp_calendar_incremental_registry_profile(self.registry)
            )
            self.assertEqual(incremental_predecessor.revision, "2.39.0")
            self.assertEqual(
                incremental_predecessor.source_sha256,
                PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SHA256,
            )
            predecessor = econometrics_model_suite_v3_registry_profile(self.registry)
            self.assertEqual(predecessor.revision, "2.38.0")
            registry_path.write_text(
                json.dumps(
                    predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, _, _ = generated_bytes(root)
            regenerated = json.loads(registry_bytes)
            self.assertEqual(regenerated["registry_version"], "2.39.0")
            self.assertIn(
                "bea.macro.personal_income_history",
                {item["id"] for item in regenerated["collectors"]},
            )

    def test_current_registry_has_nine_versioned_tools_and_thirteen_variants(
        self,
    ) -> None:
        self.assertEqual(
            (self.registry.schema_version, self.registry.registry_version),
            ("1.9.0", "2.43.0"),
        )
        self.assertEqual(self.registry.source_sha256, CURRENT_REGISTRY_SHA256)
        predecessor = market_price_series_v1_registry_profile(self.registry)
        alpaca_predecessor = alpaca_spy_options_registry_profile(self.registry)
        self.assertEqual(
            (
                alpaca_predecessor.registry_version,
                alpaca_predecessor.source_sha256,
            ),
            ("2.42.0", PRE_ALPACA_SPY_OPTIONS_REGISTRY_SHA256),
        )
        self.assertIs(alpaca_spy_options_registry_profile(alpaca_predecessor), alpaca_predecessor)
        ticker_predecessor = (
            market_available_ticker_v1_registry_profile(self.registry)
        )
        self.assertEqual(ticker_predecessor.registry_version, "2.41.0")
        self.assertEqual(
            ticker_predecessor.source_sha256,
            PRE_AVAILABLE_TICKER_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(item["id"] for item in ticker_predecessor.tools),
            tuple(
                item["id"]
                for item in self.registry.tools
                if item["id"] != "market.get_available_ticker"
            ),
        )
        self.assertEqual(
            ticker_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.7.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_AVAILABLE_TICKER_CATALOG_SHA256,
            },
        )
        ticker_predecessor_payload = (
            json.dumps(
                ticker_predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(ticker_predecessor_payload).hexdigest(),
            PRE_AVAILABLE_TICKER_REGISTRY_SHA256,
        )
        self.assertIs(
            market_available_ticker_v1_registry_profile(ticker_predecessor),
            ticker_predecessor,
        )
        self.assertEqual(predecessor.registry_version, "2.40.0")
        self.assertEqual(
            predecessor.source_sha256,
            PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(item["id"] for item in predecessor.tools),
            tuple(
                item["id"]
                for item in self.registry.tools
                if item["id"] not in {
                    "market.get_available_ticker",
                    "market.get_price_series",
                }
            ),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.6.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_MARKET_PRICE_SERIES_CATALOG_SHA256,
            },
        )
        predecessor_payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(predecessor_payload).hexdigest(),
            PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256,
        )
        self.assertIs(
            market_price_series_v1_registry_profile(predecessor),
            predecessor,
        )
        drifted_datasets = replace(
            self.registry,
            datasets=tuple(
                replace(
                    item,
                    tool_ids=tuple(
                        tool_id
                        for tool_id in item.tool_ids
                        if tool_id != "market.get_price_series"
                    ),
                )
                if item.id == "market.stage10.daily_prices"
                else item
                for item in self.registry.datasets
            ),
        )
        with self.assertRaises(RegistryError):
            market_price_series_v1_registry_profile(drifted_datasets)
        self.assertEqual(
            tuple(policy["tool"] for policy in self.registry.tool_version_policies),
            (
                "market.get_returns",
                "market.get_forward_returns",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "econometrics.structural_breaks",
            ),
        )
        for name in ("market.get_returns", "market.get_forward_returns"):
            with self.subTest(name=name):
                self.assertEqual(self.registry.tool(name)["version"], "1.0.0")
                self.assertEqual(
                    self.registry.tool(name, "1.0.0")["version"], "1.0.0"
                )
                v2 = self.registry.tool(name, "2.0.0")
                self.assertEqual(v2["operation_graph_id"], f"tool_platform.{name}.v2")
                self.assertEqual(
                    v2["datasets"],
                    [
                        "market.stage10.daily_prices",
                        "market.stage10.source_evidence",
                        "market.stage10.instruments",
                    ],
                )
                with self.assertRaises(RegistryError):
                    self.registry.tool(name, "3.0.0")
        for name in (
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
            "econometrics.structural_breaks",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.registry.tool(name)["version"], "1.0.0")
                self.assertEqual(
                    self.registry.tool(name, "2.0.0")["datasets"], []
                )
                self.assertEqual(
                    self.registry.tool(name, "2.0.0")["operation_graph_id"],
                    f"tool_platform.{name}.v2",
                )
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in self.registry.tool_version_policies
            ),
            13,
        )
        for name in (
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
        ):
            with self.subTest(name=name, version="2.1.0"):
                declaration = self.registry.tool(name, "2.1.0")
                self.assertEqual(declaration["datasets"], [])
                self.assertEqual(
                    declaration["operation_graph_id"],
                    f"tool_platform.{name}.v2_1",
                )
                self.assertTrue(
                    declaration["input_schema_id"].endswith(":2.1.0")
                )
                self.assertTrue(
                    declaration["output_schema_id"].endswith(":2.1.0")
                )
        regression_v3 = self.registry.tool(
            "econometrics.regression",
            "3.0.0",
        )
        self.assertEqual(
            regression_v3["operation_graph_id"],
            "tool_platform.econometrics.regression.v3",
        )
        self.assertTrue(regression_v3["input_schema_id"].endswith(":3.0.0"))
        self.assertTrue(regression_v3["output_schema_id"].endswith(":3.0.0"))
        self.assertEqual(regression_v3["datasets"], [])
        with self.assertRaises(RegistryError):
            self.registry.tool(
                "econometrics.structural_breaks",
                "2.1.0",
            )

    def test_generated_v1_is_frozen_and_v2_catalog_is_separate(self) -> None:
        generate(PROJECT_ROOT, check=True)
        self.assertEqual(hashlib.sha256(V1_CATALOG.read_bytes()).hexdigest(), V1_CATALOG_SHA256)
        self.assertEqual(hashlib.sha256(V2_CATALOG.read_bytes()).hexdigest(), V2_CATALOG_SHA256)
        v1 = loads_strict(V1_CATALOG.read_bytes())
        v2 = loads_strict(V2_CATALOG.read_bytes())
        self.assertEqual(len(v1["contracts"]), 114)
        self.assertEqual(len(v2["contracts"]), 30)
        self.assertEqual(
            {item["tool"] for item in v2["contracts"]},
            {
                "market.get_price_series",
                "market.get_returns",
                "market.get_available_ticker",
                "market.get_forward_returns",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "econometrics.structural_breaks",
            },
        )
        self.assertEqual(
            sum(item["id"].endswith(":1.0.0") for item in v2["contracts"]),
            4,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":1.0.0")
            },
            {"market.get_available_ticker", "market.get_price_series"},
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.0.0") for item in v2["contracts"]),
            18,
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.1.0") for item in v2["contracts"]),
            6,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.1.0")
            },
            {
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
            },
        )

        self.assertEqual(
            sum(item["id"].endswith(":3.0.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":3.0.0")
            },
            {"econometrics.regression"},
        )

    def test_manifest_exposes_59_names_and_marks_only_versioned_v1_variants_deprecated(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            dispatcher = ToolDispatcher(
                explicit_store_map(Path(temporary) / "stores"), self.registry
            )
            manifest = dispatcher.manifest()
        self.assertEqual(len(manifest["tools"]), 59)
        self.assertEqual(len({item["name"] for item in manifest["tools"]}), 59)
        price_series = next(
            item
            for item in manifest["tools"]
            if item["name"] == "market.get_price_series"
        )
        self.assertEqual(price_series["version"], "1.0.0")
        self.assertEqual(price_series["lifecycle"], "experimental")
        self.assertEqual(
            price_series["compatibility"]["status"],
            "additive_native_v1",
        )
        self.assertEqual(
            price_series["input_schema"]["required"],
            ["ticker", "mode", "as_of", "date_only_policy", "limit"],
        )
        versioned = {
            "market.get_returns",
            "market.get_forward_returns",
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
            "econometrics.structural_breaks",
        }
        for name in versioned:
            tool = next(item for item in manifest["tools"] if item["name"] == name)
            self.assertEqual(tool["version"], "1.0.0")
            self.assertEqual(tool["lifecycle"], "deprecated")
            expected_versions = ["1.0.0", "2.0.0"]
            if name in {
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
            }:
                expected_versions.append("2.1.0")
            if name == "econometrics.regression":
                expected_versions.append("3.0.0")
            self.assertEqual(
                [item["version"] for item in tool["versions"]],
                expected_versions,
            )
            self.assertEqual(tool["versions"][0]["lifecycle"], "deprecated")
            self.assertTrue(
                all(
                    item["lifecycle"] == "experimental"
                    for item in tool["versions"][1:]
                )
            )
            self.assertEqual(
                tool["deprecation"]["removal"],
                {"status": "not_scheduled", "milestone": None},
            )
        self.assertEqual(
            {
                item["name"]
                for item in manifest["tools"]
                if item["lifecycle"] == "deprecated"
            },
            versioned,
        )

    def test_new_econometric_projections_restore_exact_predecessors(self) -> None:
        pre_model_suite = econometrics_model_suite_v3_registry_profile(
            self.registry
        )
        self.assertEqual(pre_model_suite.registry_version, "2.38.0")
        self.assertEqual(
            pre_model_suite.source_sha256,
            PRE_ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_model_suite.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.5.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SHA256,
            },
        )
        with self.assertRaises(RegistryError):
            pre_model_suite.tool("econometrics.regression", "3.0.0")
        self.assertEqual(
            pre_model_suite.tool("econometrics.regression", "2.1.0")["version"],
            "2.1.0",
        )

        pre_structural = structural_breaks_v2_registry_profile(
            self.registry
        )
        self.assertEqual(pre_structural.registry_version, "2.37.0")
        self.assertEqual(
            pre_structural.source_sha256,
            PRE_STRUCTURAL_BREAKS_V2_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_structural.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.4.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_STRUCTURAL_BREAKS_V2_CATALOG_SHA256,
            },
        )
        self.assertNotIn(
            "econometrics.structural_breaks",
            {
                policy["tool"]
                for policy in pre_structural.tool_version_policies
            },
        )
        self.assertEqual(
            pre_structural.tool(
                "econometrics.stationarity",
                "2.1.0",
            )["version"],
            "2.1.0",
        )

        pre_stationarity = stationarity_v21_registry_profile(self.registry)
        self.assertEqual(pre_stationarity.registry_version, "2.36.0")
        self.assertEqual(
            pre_stationarity.source_sha256,
            PRE_STATIONARITY_V21_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_stationarity.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.3.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_STATIONARITY_V21_CATALOG_SHA256,
            },
        )
        with self.assertRaises(RegistryError):
            pre_stationarity.tool(
                "econometrics.stationarity",
                "2.1.0",
            )


    def test_robust_v21_projection_restores_exact_235_registry(self) -> None:
        predecessor = robust_econometrics_v21_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.schema_version, predecessor.registry_version),
            ("1.9.0", "2.35.0"),
        )
        self.assertEqual(
            predecessor.source_sha256,
            PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256,
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256,
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.2.0",
                "sha256": PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SHA256,
            },
        )
        self.assertEqual(
            (
                len(predecessor.migrations),
                len(predecessor.datasets),
                len(predecessor.collectors),
            ),
            (39, 51, 50),
        )
        self.assertTrue(
            all(
                tuple(
                    variant["version"]
                    for variant in policy["variants"]
                )
                == ("2.0.0",)
                for policy in predecessor.tool_version_policies
            )
        )
        self.assertIs(
            robust_econometrics_v21_registry_profile(predecessor),
            predecessor,
        )

        drifted_variants = replace(
            self.registry,
            tool_version_policies=self.registry.tool_version_policies[:-1],
        )
        with self.assertRaises(RegistryError):
            robust_econometrics_v21_registry_profile(drifted_variants)
        drifted_catalog = replace(
            self.registry,
            raw={
                **self.registry.raw,
                "tool_version_schema_catalog": {
                    **self.registry.raw["tool_version_schema_catalog"],
                    "schema_version": "drifted",
                },
            },
        )
        with self.assertRaises(RegistryError):
            robust_econometrics_v21_registry_profile(drifted_catalog)

    def test_projection_restores_exact_233_then_232_then_231_registries(self) -> None:
        pre_econometrics = econometrics_v2_registry_profile(self.registry)
        self.assertEqual(
            (pre_econometrics.schema_version, pre_econometrics.registry_version),
            ("1.9.0", "2.33.0"),
        )
        self.assertEqual(
            pre_econometrics.source_sha256,
            PRE_ECONOMETRICS_V2_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(
                policy["tool"]
                for policy in pre_econometrics.tool_version_policies
            ),
            (
                "market.get_returns",
                "market.get_forward_returns",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
            ),
        )
        self.assertEqual(
            pre_econometrics.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.1.0",
                "sha256": PRE_ECONOMETRICS_V2_CATALOG_SHA256,
            },
        )
        self.assertIs(
            econometrics_v2_registry_profile(pre_econometrics),
            pre_econometrics,
        )

        pre_timeseries = timeseries_analysis_v2_registry_profile(self.registry)
        self.assertEqual(
            (pre_timeseries.schema_version, pre_timeseries.registry_version),
            ("1.9.0", "2.32.0"),
        )
        self.assertEqual(
            pre_timeseries.source_sha256, PRE_TIMESERIES_V2_REGISTRY_SHA256
        )
        self.assertEqual(
            tuple(
                policy["tool"]
                for policy in pre_timeseries.tool_version_policies
            ),
            ("market.get_returns", "market.get_forward_returns"),
        )
        self.assertEqual(
            pre_timeseries.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.0.0",
                "sha256": PRE_TIMESERIES_V2_CATALOG_SHA256,
            },
        )
        self.assertIs(
            timeseries_analysis_v2_registry_profile(pre_timeseries),
            pre_timeseries,
        )

        predecessor = market_return_v2_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.schema_version, predecessor.registry_version),
            ("1.8.0", "2.31.0"),
        )
        self.assertEqual(predecessor.source_sha256, PREDECESSOR_REGISTRY_SHA256)
        self.assertEqual(predecessor.tool_version_policies, ())
        self.assertNotIn("tool_versions", predecessor.raw)
        self.assertNotIn("tool_version_schema_catalog", predecessor.raw)
        for dataset_id in (
            "market.stage10.daily_prices",
            "market.stage10.source_evidence",
            "market.stage10.instruments",
        ):
            self.assertEqual(
                next(item for item in predecessor.datasets if item.id == dataset_id).tool_ids,
                (),
            )
        self.assertIs(market_return_v2_registry_profile(predecessor), predecessor)

        drifted = replace(
            self.registry,
            tool_version_policies=self.registry.tool_version_policies[:-1],
        )
        with self.assertRaises(RegistryError):
            timeseries_analysis_v2_registry_profile(drifted)

    def test_v2_schema_and_cross_field_validation_run_without_a_reader(self) -> None:
        declaration = self.registry.tool("market.get_returns", "2.0.0")
        example = dict(declaration["examples"][0])
        for field, value in (
            ("identifier_kind", "ticker"),
            ("mode", "first_release"),
            ("date_only_policy", "guess"),
            ("method", "arithmetic"),
            ("horizon", 0),
            ("limit", 10001),
        ):
            with self.subTest(field=field):
                invalid = {**example, field: value}
                with self.assertRaises(ValidationError):
                    validate_schema(invalid, declaration["input_schema"])

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_return_v2",
                {**example, "mode": "as_of", "as_of": None},
                lambda value: value,
            )
        for name in (
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
        ):
            with self.subTest(name=name):
                declaration = self.registry.tool(name, "2.0.0")
                for example in declaration["examples"]:
                    validate_schema(example, declaration["input_schema"])
        for name in (
            "econometrics.regression",
            "econometrics.rolling_regression",
        ):
            with self.subTest(name=name, version="2.1.0"):
                robust_declaration = self.registry.tool(name, "2.1.0")
                for robust_example in robust_declaration["examples"]:
                    validate_schema(
                        robust_example,
                        robust_declaration["input_schema"],
                    )

        regression_declaration = self.registry.tool(
            "econometrics.regression", "2.1.0"
        )
        regression_example = dict(regression_declaration["examples"][0])
        with self.assertRaises(ValidationError):
            validate_schema(
                {**regression_example, "diagnostic_lag": 19},
                regression_declaration["input_schema"],
            )

        decoder_calls = 0

        def decode(_: object) -> object:
            nonlocal decoder_calls
            decoder_calls += 1
            return object()

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_regression_v2_1",
                {
                    **regression_example,
                    "covariance": "hc3",
                    "hac_lag": 1,
                },
                decode,
            )
        rolling_declaration = self.registry.tool(
            "econometrics.rolling_regression", "2.1.0"
        )
        rolling_example = dict(rolling_declaration["examples"][0])
        for invalid in (
            {
                **rolling_example,
                "window": 8,
                "diagnostic_lag": 4,
            },
            {
                **rolling_example,
                "covariance": "newey_west_hac_bartlett",
                "window": 8,
                "hac_lag": 8,
                "diagnostic_lag": 1,
            },
        ):
            with self.assertRaises(ValidationError):
                parse_arguments(
                    "stage10_market_rolling_regression_v2_1",
                    invalid,
                    decode,
                )
        self.assertEqual(decoder_calls, 0)

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_return_v2",
                {
                    **example,
                    "start_date": "2026-08-12",
                    "end_date": "2026-08-10",
                },
                lambda value: value,
            )

    def test_http_unknown_version_is_sanitized_before_store_access(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            stores = explicit_store_map(Path(temporary) / "stores")
            application = Stage1Application(stores, self.registry)
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "market.get_returns",
                        "tool_version": "3.0.0",
                        "arguments": {},
                    }
                ).encode("utf-8"),
            )
            malformed = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "market.get_returns",
                        "tool_version": "2.0.0 ",
                        "arguments": {},
                    }
                ).encode("utf-8"),
            )
            non_string = tuple(
                application.handle(
                    "POST",
                    "/api/agent-tools/call",
                    headers={"Content-Type": "application/json"},
                    body=dumps_strict(
                        {
                            "api_version": "1.0",
                            "tool": "market.get_returns",
                            "tool_version": value,
                            "arguments": {},
                        }
                    ).encode("utf-8"),
                )
                for value in (None, 7, {})
            )
            self.assertFalse(any(Path(temporary).iterdir()))
        payload = loads_strict(response.body)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "unsupported_tool_version")
        self.assertEqual(payload["receipt"]["tool_version"], "3.0.0")
        self.assertEqual(payload["receipt"]["operation_graph_id"], None)
        malformed_payload = loads_strict(malformed.body)
        self.assertEqual(malformed.status, 400)
        self.assertEqual(malformed_payload["error"]["code"], "invalid_request")
        self.assertEqual(
            malformed_payload["receipt"],
            {"registry_revision": "2.43.0"},
        )
        for response in non_string:
            with self.subTest(body=response.body):
                payload = loads_strict(response.body)
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertEqual(
                    payload["receipt"],
                    {"registry_revision": "2.43.0"},
                )


if __name__ == "__main__":
    unittest.main()
