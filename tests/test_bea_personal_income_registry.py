from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    bea_personal_income_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "bea.macro.personal_income_history"
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
CURRENT_SOURCE_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)
PRE_EXTENSION_SOURCE_SHA256 = (
    "9ed2affcaa84c6420c7650c10a02361fad2bd892b33a5df2797e033e300ef86d"
)


class BeaPersonalIncomeRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_private_bounded_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.43.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (40, 53, 50),
        )
        collector = next(
            item
            for item in registry.collectors
            if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(
            collector["handler"], "macro.bea_personal_income_history"
        )
        self.assertEqual(collector["configuration_env"], ["BEA_API_KEY"])
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
            self.assertEqual(
                datasets[dataset_id].collector_ids[-1], COLLECTOR_ID
            )
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

    def test_revision_234_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = bea_personal_income_registry_profile(registry)

        self.assertEqual(historical.revision, "2.34.0")
        self.assertEqual(
            historical.source_sha256, PRE_EXTENSION_SOURCE_SHA256
        )
        self.assertEqual(len(historical.collectors), 49)
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
        datasets = {item.id: item for item in historical.datasets}
        for dataset_id in DATASET_IDS:
            self.assertNotIn(
                COLLECTOR_ID, datasets[dataset_id].collector_ids
            )

        first = DATASET_IDS[0]
        drifted = replace(
            registry,
            datasets=tuple(
                replace(item, collector_ids=item.collector_ids[:-1])
                if item.id == first
                else item
                for item in registry.datasets
            ),
        )
        with self.assertRaises(RegistryError):
            bea_personal_income_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
