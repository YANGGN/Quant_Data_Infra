from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    official_conditions_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_IDS = (
    "federal_reserve.macro.h41_history",
    "chicagofed.macro.nfci_history",
    "bis.macro.credit_conditions_history",
)
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
PRE_SOURCE_SHA256 = (
    "999ff3ef57e2c858139e2c1122e9d2409d53d66dd6f1cdcd7cb624e08542f969"
)
CURRENT_SOURCE_SHA256 = (
    "55285a106a56a3d664f83dd75cb71c43aa21f5e9637d0200704933a291732a78"
)


class OfficialConditionsRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_three_collectors_are_manual_credential_free_and_bound(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.71.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (45, 61, 68),
        )
        collectors = {
            str(item["id"]): item for item in registry.collectors
        }
        expected = {
            COLLECTOR_IDS[0]: (
                "macro.federal_reserve_h41_history",
                1,
                20_000,
                120,
            ),
            COLLECTOR_IDS[1]: (
                "macro.chicagofed_nfci_history",
                1,
                5_000,
                60,
            ),
            COLLECTOR_IDS[2]: (
                "macro.bis_credit_conditions_history",
                2,
                5_000,
                120,
            ),
        }
        for collector_id, values in expected.items():
            collector = collectors[collector_id]
            self.assertEqual(collector["handler"], values[0])
            self.assertEqual(collector["configuration_env"], [])
            self.assertTrue(collector["network"])
            self.assertEqual(
                collector["output_datasets"], list(DATASET_IDS)
            )
            self.assertEqual(
                collector["schedule_eligibility"],
                {"mode": "manual_only"},
            )
            self.assertEqual(
                collector["workload_bounds"],
                {
                    "max_bytes": 16_777_216,
                    "max_requests": values[1],
                    "max_rows": values[2],
                    "max_seconds": values[3],
                },
            )
            self.assertEqual(
                collector["retry_policy"]["max_attempts"], 1
            )
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            for collector_id in COLLECTOR_IDS:
                self.assertIn(
                    collector_id, datasets[dataset_id].collector_ids
                )
                self.assertEqual(
                    datasets[dataset_id].collector_ids.count(collector_id),
                    1,
                )
        self.assertFalse(
            any(
                step.collector_id in COLLECTOR_IDS
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_226_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = official_conditions_registry_profile(registry)

        self.assertEqual(historical.revision, "2.26.0")
        self.assertEqual(historical.source_sha256, PRE_SOURCE_SHA256)
        self.assertEqual(len(historical.collectors), 41)
        for collector_id in COLLECTOR_IDS:
            self.assertNotIn(
                collector_id,
                {str(item["id"]) for item in historical.collectors},
            )
        for dataset in historical.datasets:
            self.assertFalse(
                set(dataset.collector_ids).intersection(COLLECTOR_IDS)
            )
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
            PRE_SOURCE_SHA256,
        )

        collectors = tuple(
            {
                **dict(item),
                "workload_bounds": {
                    **dict(item["workload_bounds"]),
                    "max_rows": 19_999,
                },
            }
            if item["id"] == COLLECTOR_IDS[0]
            else item
            for item in registry.collectors
        )
        with self.assertRaises(RegistryError):
            official_conditions_registry_profile(
                replace(registry, collectors=collectors)
            )


if __name__ == "__main__":
    unittest.main()
