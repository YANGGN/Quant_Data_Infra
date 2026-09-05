from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from quant_data.registry import load_registry, macro_vintage_registry_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0013_live_gdp_cpi_vintages"
MIGRATION_SHA256 = "9eab5c35c6377e6f22a927dbc4602e982ff3c8ae6095e782de546a05de58f5bc"
PRE_VINTAGE_SOURCE_SHA256 = (
    "b6cfa9db720f4cb878127eae001e6bc1d59e0c7421a8c65277b24ab8fe36adb6"
)


class MacroVintageRegistryTests(unittest.TestCase):
    def test_canonical_declarations_and_migration_are_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(
            (registry.schema_version, registry.registry_version),
            ("1.9.0", "2.70.0"),
        )
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (44, 59, 66),
        )

        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal), ("macro", 13))
        self.assertEqual(migration.dependencies, ("macro:0012_stage11_bea_eia_live_history",))
        self.assertEqual(migration.sha256, MIGRATION_SHA256)
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(registry.store("macro").migration_order[-6], MIGRATION_ID)

        datasets = {item.id: item for item in registry.datasets}
        self.assertEqual(
            datasets["macro.official_vintages_evidence"].relations,
            ("macro_live_vintage_captures",),
        )
        self.assertEqual(
            datasets["macro.official_vintages"].relations,
            (
                "macro_live_vintage_series",
                "macro_live_vintage_releases",
                "macro_live_vintage_observation_versions",
                "macro_live_vintage_observations",
                "macro_live_vintage_capture_membership",
                "macro_live_vintage_gdp_provenance",
            ),
        )
        collectors = {str(item["id"]): item for item in registry.collectors}
        self.assertEqual(
            set(collectors) & {
                "bea.macro.live_gdp_vintages",
                "bls.macro.live_cpi_vintages",
            },
            {
                "bea.macro.live_gdp_vintages",
                "bls.macro.live_cpi_vintages",
            },
        )
        for collector_id in (
            "bea.macro.live_gdp_vintages",
            "bls.macro.live_cpi_vintages",
        ):
            collector = collectors[collector_id]
            self.assertTrue(collector["network"])
            self.assertEqual(collector["configuration_env"], [])
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})

    def test_pre_vintage_registry_projection_remains_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        historical = macro_vintage_registry_profile(registry)
        self.assertEqual(
            (historical.schema_version, historical.registry_version),
            ("1.8.0", "2.15.0"),
        )
        self.assertEqual(historical.source_sha256, PRE_VINTAGE_SOURCE_SHA256)
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertNotIn(
            "macro.official_vintages",
            {item.id for item in historical.datasets},
        )
        self.assertNotIn(
            "bea.macro.live_gdp_vintages",
            {str(item["id"]) for item in historical.collectors},
        )


if __name__ == "__main__":
    unittest.main()
