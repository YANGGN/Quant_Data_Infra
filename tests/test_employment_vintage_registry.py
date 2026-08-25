from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from quant_data.registry import employment_vintage_registry_profile, load_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0014_live_employment_vintages"
MIGRATION_SHA256 = "79d1ed0a0ee7e059109bcd3238f1c7ab79148924b69c8eda282b09c0af245ee8"
PRE_EMPLOYMENT_SOURCE_SHA256 = (
    "8da35a5a21ecd097d62fe625bdb096ecf3f2eba62413346957372ea81b9d7992"
)
COLLECTOR_IDS = (
    "philadelphia_fed.macro.live_employment_vintages",
    "bls.macro.live_employment_current",
)


class EmploymentVintageRegistryTests(unittest.TestCase):
    def test_canonical_employment_declarations_are_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(
            (registry.schema_version, registry.registry_version),
            ("1.9.0", "2.43.0"),
        )
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (40, 53, 50),
        )

        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal), ("macro", 14))
        self.assertEqual(migration.dependencies, ("macro:0013_live_gdp_cpi_vintages",))
        self.assertEqual(migration.sha256, MIGRATION_SHA256)
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(registry.store("macro").migration_order[-5], MIGRATION_ID)

        datasets = {item.id: item for item in registry.datasets}
        expected_dataset_collectors = (
            "bea.macro.live_gdp_vintages",
            "bls.macro.live_cpi_vintages",
            *COLLECTOR_IDS,
            "bls.macro.cpi_current_history",
            "philadelphia_fed.macro.gdp_cpi_vintage_history",
        )
        for dataset_id in (
            "macro.official_vintages_evidence",
            "macro.official_vintages",
        ):
            self.assertEqual(
                datasets[dataset_id].collector_ids,
                expected_dataset_collectors,
            )

        collectors = {str(item["id"]): item for item in registry.collectors}
        historical = collectors[COLLECTOR_IDS[0]]
        current = collectors[COLLECTOR_IDS[1]]
        self.assertEqual(historical["handler"], "macro.live_employment_vintages")
        self.assertEqual(current["handler"], "macro.live_employment_current")
        self.assertEqual(historical["workload_bounds"]["max_requests"], 2)
        self.assertEqual(historical["workload_bounds"]["max_rows"], 500_000)
        self.assertEqual(current["workload_bounds"]["max_requests"], 1)
        for collector in (historical, current):
            self.assertTrue(collector["network"])
            self.assertEqual(collector["configuration_env"], [])
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})

    def test_pre_employment_projection_is_byte_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        historical = employment_vintage_registry_profile(registry)
        self.assertEqual(
            (historical.schema_version, historical.registry_version),
            ("1.8.0", "2.16.0"),
        )
        self.assertEqual(historical.source_sha256, PRE_EMPLOYMENT_SOURCE_SHA256)
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertFalse(
            set(COLLECTOR_IDS).intersection(
                {str(item["id"]) for item in historical.collectors}
            )
        )
        for dataset_id in (
            "macro.official_vintages_evidence",
            "macro.official_vintages",
        ):
            dataset = next(item for item in historical.datasets if item.id == dataset_id)
            self.assertEqual(
                dataset.collector_ids,
                (
                    "bea.macro.live_gdp_vintages",
                    "bls.macro.live_cpi_vintages",
                ),
            )
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), historical.source_sha256)


if __name__ == "__main__":
    unittest.main()
