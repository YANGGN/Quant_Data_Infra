from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    PUBLIC_TOOL_NAMES,
    load_registry,
    stage2_registry_profile,
    stage3_registry_profile,
    stage4_registry_profile,
    stage5_registry_profile,
    stage6_registry_profile,
    stage7_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


class RegistryIdentifierTests(unittest.TestCase):
    def _load_mutation(self, mutate):
        raw = loads_strict(REGISTRY_PATH.read_bytes())
        mutate(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            return load_registry(path, project_root=PROJECT_ROOT, environment={})

    def _assert_identifier_error(self, mutate, pointer: str) -> None:
        with self.assertRaises(RegistryError) as raised:
            self._load_mutation(mutate)
        self.assertEqual(len(raised.exception.issues), 1)
        issue = raised.exception.issues[0]
        self.assertEqual(issue.pointer, pointer)
        self.assertEqual(issue.rule, "identifier")

    @staticmethod
    def _replace_migration_id(raw, replacement: str) -> None:
        original = raw["migrations"][0]["id"]
        for store in raw["stores"]:
            store["migration_order"] = [
                replacement if item == original else item
                for item in store["migration_order"]
            ]
        for migration in raw["migrations"]:
            if migration["id"] == original:
                migration["id"] = replacement
            migration["dependencies"] = [
                replacement if item == original else item
                for item in migration["dependencies"]
            ]

    @staticmethod
    def _replace_dataset_id(raw, replacement: str) -> None:
        original = raw["datasets"][0]["id"]
        for dataset in raw["datasets"]:
            if dataset["id"] == original:
                dataset["id"] = replacement
        for collector in raw["collectors"]:
            for field in ("input_datasets", "output_datasets"):
                collector[field] = [
                    replacement if item == original else item
                    for item in collector[field]
                ]
        for tool in raw["tools"]:
            tool["datasets"] = [
                replacement if item == original else item for item in tool["datasets"]
            ]
        for exposure in raw["dashboard"]:
            exposure["datasets"] = [
                replacement if item == original else item
                for item in exposure["datasets"]
            ]

    @staticmethod
    def _replace_collector_id(raw, replacement: str) -> None:
        original = raw["collectors"][0]["id"]
        for collector in raw["collectors"]:
            if collector["id"] == original:
                collector["id"] = replacement
        for dataset in raw["datasets"]:
            dataset["collector_ids"] = [
                replacement if item == original else item
                for item in dataset["collector_ids"]
            ]

    def test_canonical_registry_inventory_remains_valid(self) -> None:
        registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

        self.assertEqual(len(registry.stores), 4)
        self.assertEqual(registry.schema_version, "1.5.0")
        self.assertEqual(registry.registry_version, "2.7.0")
        self.assertEqual(tuple(item.id for item in registry.exports), ("atlas.fixture_snapshot",))
        self.assertEqual(
            tuple(item["id"] for item in registry.dashboard),
            ("stage1.overview", "stage6.gdp_vintages",
             "stage6.table_inspector", "stage6.agent_tools"),
        )
        self.assertEqual(len(registry.migrations), 31)
        self.assertEqual(len(registry.datasets), 36)
        self.assertEqual(len(registry.collectors), 20)
        self.assertEqual(len(registry.jobs), 8)
        stage5 = stage5_registry_profile(registry)
        self.assertEqual(stage5.schema_version, "1.1.0")
        self.assertEqual(stage5.registry_version, "2.3.0")
        self.assertEqual(
            stage5.source_sha256,
            "56c2e8c97623c23596c92b177ee5e0aa89ecf164c37094216142cc82fc78cd9a",
        )
        self.assertEqual(len(stage5.dashboard), 1)
        self.assertEqual(stage5.dashboard[0]["id"], "stage1.overview")
        self.assertEqual(
            stage5.dashboard[0]["api_routes"],
            [
                "/api/health",
                "/api/price-series",
                "/api/agent-tools",
                "/api/agent-tools/call",
            ],
        )
        self.assertFalse(
            any(
                dashboard_id.startswith("stage6.")
                for dataset in stage5.datasets
                for dashboard_id in dataset.dashboard_ids
            )
        )
        self.assertEqual(stage5.jobs, ())
        self.assertEqual(stage5.raw["jobs"], [])
        stage6 = stage6_registry_profile(registry)
        self.assertEqual(stage6.schema_version, "1.2.0")
        self.assertEqual(stage6.registry_version, "2.4.0")
        self.assertEqual(stage6.jobs, ())
        self.assertEqual(stage6.exports, ())
        self.assertEqual(stage6.raw["jobs"], [])
        stage7 = stage7_registry_profile(registry)
        self.assertEqual(stage7.schema_version, "1.3.0")
        self.assertEqual(stage7.registry_version, "2.5.0")
        self.assertEqual(stage7.exports, ())
        self.assertEqual(
            stage7.source_sha256,
            "643f4fd9a21f2b8b2701b0a408cddb63ae198175bc2cd61ce1ed637a9419a52c",
        )
        stage4 = stage4_registry_profile(registry)
        self.assertEqual(stage4.registry_version, "2.2.0")
        self.assertEqual(len(stage4.migrations), 30)
        self.assertEqual(len(stage4.datasets), 33)
        self.assertEqual(len(stage4.collectors), 19)
        stage3 = stage3_registry_profile(registry)
        self.assertEqual(stage3.registry_version, "2.1.0")
        self.assertEqual(len(stage3.migrations), 21)
        self.assertEqual(len(stage3.datasets), 19)
        self.assertEqual(len(stage3.collectors), 14)
        stage2 = stage2_registry_profile(registry)
        self.assertEqual(stage2.registry_version, "2.0.0")
        self.assertEqual(len(stage2.migrations), 10)
        self.assertEqual(len(stage2.datasets), 5)
        self.assertEqual(len(stage2.collectors), 2)
        self.assertEqual(
            tuple(registry.raw["compatibility_target"]["reserved_tool_names"]),
            PUBLIC_TOOL_NAMES,
        )
        self.assertEqual(len(PUBLIC_TOOL_NAMES), 57)
        self.assertEqual([tool["id"] for tool in registry.tools], list(PUBLIC_TOOL_NAMES))
        self.assertEqual([tool["id"] for tool in stage4.tools], ["macro.get_series", "timeseries.describe"])


    def test_dashboard_contract_rejects_unsafe_or_inconsistent_declarations(self) -> None:
        cases = (
            lambda raw: raw["dashboard"][1].__setitem__("route", "/"),
            lambda raw: raw["dashboard"][1].__setitem__(
                "api_routes", ["/api/health"]
            ),
            lambda raw: raw["dashboard"][2]["relations"].__setitem__(
                0, "sqlite_master"
            ),
            lambda raw: raw["dashboard"][1]["filters"].__setitem__(
                "additionalProperties", True
            ),
            lambda raw: raw["dashboard"][1]["filters"]["properties"].__setitem__(
                "sql", {"type": "string"}
            ),
            lambda raw: raw["dashboard"][2]["pagination"].__setitem__(
                "max_limit", 101
            ),
            lambda raw: raw["dashboard"][2]["filters"]["properties"].pop(
                "search"
            ),
            lambda raw: raw["dashboard"][2]["sort_fields"].clear(),
            lambda raw: raw["dashboard"][1]["pagination"].__setitem__(
                "default_limit", 24
            ),
            lambda raw: raw["datasets"][0]["dashboard_ids"].clear(),
        )
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(RegistryError):
                    self._load_mutation(mutate)

    def test_hostile_declaration_ids_fail_after_reciprocal_rewrites(self) -> None:
        cases = (
            (
                "migration",
                "../../migration",
                self._replace_migration_id,
                "/stores/0/migration_order/0",
            ),
            (
                "dataset",
                "../../dataset",
                self._replace_dataset_id,
                "/datasets/0/id",
            ),
            (
                "collector",
                "../../collector",
                self._replace_collector_id,
                "/datasets/0/collector_ids/0",
            ),
        )

        for kind, hostile_id, replace, pointer in cases:
            with self.subTest(kind=kind):
                self._assert_identifier_error(
                    lambda raw, replace=replace, hostile_id=hostile_id: replace(
                        raw, hostile_id
                    ),
                    pointer,
                )

    def test_non_normalized_stable_ids_are_rejected(self) -> None:
        invalid_ids = (
            "Market:0001_foundation",
            "market:0001 foundation",
            ".market:0001",
            "market:0001_",
            "market..0001",
            "market/0001",
            r"market\0001",
        )

        for invalid_id in invalid_ids:
            with self.subTest(invalid_id=invalid_id):
                self._assert_identifier_error(
                    lambda raw, invalid_id=invalid_id: raw["migrations"][0].__setitem__(
                        "id", invalid_id
                    ),
                    "/migrations/0/id",
                )

    def test_identifier_cross_reference_arrays_are_validated(self) -> None:
        cases = (
            (
                "/stores/0/migration_order/0",
                lambda raw: raw["stores"][0]["migration_order"].__setitem__(
                    0, "../../migration"
                ),
            ),
            (
                "/migrations/1/dependencies/0",
                lambda raw: raw["migrations"][1]["dependencies"].__setitem__(
                    0, "../../migration"
                ),
            ),
            (
                "/datasets/0/collector_ids/0",
                lambda raw: raw["datasets"][0]["collector_ids"].__setitem__(
                    0, "../../collector"
                ),
            ),
            (
                "/tools/0/datasets/0",
                lambda raw: raw["tools"][0]["datasets"].__setitem__(
                    0, "../../dataset"
                ),
            ),
            (
                "/collectors/0/output_datasets/0",
                lambda raw: raw["collectors"][0]["output_datasets"].__setitem__(
                    0, "../../dataset"
                ),
            ),
            (
                "/dashboard/0/tools/0",
                lambda raw: raw["dashboard"][0]["tools"].__setitem__(
                    0, "../../tool"
                ),
            ),
            (
                "/presentation_order/tools/0",
                lambda raw: raw["presentation_order"]["tools"].__setitem__(
                    0, "../../tool"
                ),
            ),
        )

        for pointer, mutate in cases:
            with self.subTest(pointer=pointer):
                self._assert_identifier_error(mutate, pointer)

    def test_each_supported_separator_is_accepted_when_references_match(self) -> None:
        valid_ids = (
            "market:0001_foundation",
            "market.0001.foundation",
            "market_0001_foundation",
            "market-0001-foundation",
        )

        for valid_id in valid_ids:
            with self.subTest(valid_id=valid_id):
                registry = self._load_mutation(
                    lambda raw, valid_id=valid_id: self._replace_migration_id(
                        raw, valid_id
                    )
                )
                self.assertEqual(registry.migrations[0].id, valid_id)


if __name__ == "__main__":
    unittest.main()
