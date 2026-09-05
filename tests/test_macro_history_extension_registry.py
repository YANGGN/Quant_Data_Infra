from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from quant_data.registry import (
    load_registry,
    macro_history_extension_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0015_live_macro_history_extension"
MIGRATION_SHA256 = "31153005b135f6bb03d0c228488bee73b6c50051afaaf4b7f2a9252f957158b9"
PRE_HISTORY_SOURCE_SHA256 = (
    "6040e45faa3ddbe522a97921a36e3a1f5ded34b3a4da7dd0ee32d52929e1fcb2"
)
COLLECTOR_IDS = (
    "bls.macro.cpi_current_history",
    "philadelphia_fed.macro.gdp_cpi_vintage_history",
)


class MacroHistoryExtensionRegistryTests(unittest.TestCase):
    def test_canonical_history_extension_declarations_are_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(
            (registry.schema_version, registry.registry_version),
            ("1.9.0", "2.69.0"),
        )
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (44, 59, 66),
        )

        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal), ("macro", 15))
        self.assertEqual(migration.dependencies, ("macro:0014_live_employment_vintages",))
        self.assertEqual(migration.sha256, MIGRATION_SHA256)
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(registry.store("macro").migration_order[-4], MIGRATION_ID)

        expected_dataset_collectors = (
            "bea.macro.live_gdp_vintages",
            "bls.macro.live_cpi_vintages",
            "philadelphia_fed.macro.live_employment_vintages",
            "bls.macro.live_employment_current",
            *COLLECTOR_IDS,
        )
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in (
            "macro.official_vintages_evidence",
            "macro.official_vintages",
        ):
            self.assertEqual(
                datasets[dataset_id].collector_ids,
                expected_dataset_collectors,
            )

        collectors = {str(item["id"]): item for item in registry.collectors}
        bls = collectors[COLLECTOR_IDS[0]]
        philly = collectors[COLLECTOR_IDS[1]]
        self.assertEqual(bls["handler"], "macro.cpi_current_history")
        self.assertEqual(philly["handler"], "macro.gdp_cpi_vintage_history")
        self.assertEqual(bls["workload_bounds"]["max_requests"], 7)
        self.assertEqual(bls["workload_bounds"]["max_rows"], 1_344)
        self.assertEqual(philly["workload_bounds"]["max_requests"], 4)
        for collector in (bls, philly):
            self.assertTrue(collector["network"])
            self.assertEqual(collector["configuration_env"], [])
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})

    def test_pre_history_projection_is_byte_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        historical = macro_history_extension_registry_profile(registry)
        self.assertEqual(
            (historical.schema_version, historical.registry_version),
            ("1.8.0", "2.17.0"),
        )
        self.assertEqual(historical.source_sha256, PRE_HISTORY_SOURCE_SHA256)
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertFalse(
            set(COLLECTOR_IDS).intersection(
                {str(item["id"]) for item in historical.collectors}
            )
        )
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), historical.source_sha256)


if __name__ == "__main__":
    unittest.main()
