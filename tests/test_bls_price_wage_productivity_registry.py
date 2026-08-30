from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    bls_price_wage_productivity_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "bls.macro.price_wage_productivity_history"
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
CURRENT_SOURCE_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
PRE_EXTENSION_SOURCE_SHA256 = (
    "d7a5ba0a556abc9faa6d726e162969d4c11f0a5da614865eff23318d16ca55ff"
)


class BlsPriceWageProductivityRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_manual_bounded_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.64.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (43, 58, 58),
        )
        collector = next(
            item
            for item in registry.collectors
            if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(
            collector["handler"],
            "macro.bls_price_wage_productivity_history",
        )
        self.assertEqual(collector["configuration_env"], [])
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["schedule_eligibility"], {"mode": "manual_only"}
        )
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 16_777_216,
                "max_requests": 1,
                "max_rows": 5_000,
                "max_seconds": 60,
            },
        )
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            self.assertIn(COLLECTOR_ID, datasets[dataset_id].collector_ids)
            self.assertEqual(
                datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1
            )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_229_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = bls_price_wage_productivity_registry_profile(registry)

        self.assertEqual(historical.revision, "2.29.0")
        self.assertEqual(historical.source_sha256, PRE_EXTENSION_SOURCE_SHA256)
        self.assertEqual(len(historical.collectors), 48)
        payload = (
            json.dumps(
                historical.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            PRE_EXTENSION_SOURCE_SHA256,
        )
        self.assertNotIn(
            COLLECTOR_ID,
            {str(item["id"]) for item in historical.collectors},
        )

        first = DATASET_IDS[0]
        drifted = replace(
            registry,
            datasets=tuple(
                replace(
                    item,
                    collector_ids=tuple(
                        value
                        for value in item.collector_ids
                        if value != COLLECTOR_ID
                    ),
                )
                if item.id == first
                else item
                for item in registry.datasets
            ),
        )
        with self.assertRaises(RegistryError):
            bls_price_wage_productivity_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
