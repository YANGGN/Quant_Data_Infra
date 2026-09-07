from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    nyfed_soma_summary_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "nyfed.macro.soma_summary_history"
DATASET_IDS = (
    "fixture.macro.soma_evidence",
    "fixture.macro.soma_summary",
)
PRE_SOMA_SOURCE_SHA256 = (
    "a6fa1cc606182743ddcb8d6b0cc06b236e3dc5ba4891d71627bf5d1454a8c863"
)
CURRENT_SOURCE_SHA256 = (
    "174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05"
)


class NyFedSomaRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_exact_manual_credential_free_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.74.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (48, 64, 70),
        )
        collector = next(
            item for item in registry.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(collector["handler"], "macro.nyfed_soma_summary_history")
        self.assertEqual(collector["configuration_env"], [])
        self.assertTrue(collector["network"])
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["schedule_eligibility"],
            {"mode": "manual_only"},
        )
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            self.assertEqual(datasets[dataset_id].collector_ids[-1], COLLECTOR_ID)
            self.assertEqual(datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1)
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_225_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = nyfed_soma_summary_registry_profile(registry)

        self.assertEqual(historical.revision, "2.25.0")
        self.assertEqual(historical.source_sha256, PRE_SOMA_SOURCE_SHA256)
        self.assertEqual(len(historical.collectors), 40)
        self.assertNotIn(
            COLLECTOR_ID,
            {item["id"] for item in historical.collectors},
        )
        for dataset in historical.datasets:
            self.assertNotIn(COLLECTOR_ID, dataset.collector_ids)
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
            PRE_SOMA_SOURCE_SHA256,
        )

        collectors = tuple(
            {
                **dict(item),
                "workload_bounds": {
                    **dict(item["workload_bounds"]),
                    "max_rows": 19_999,
                },
            }
            if item["id"] == COLLECTOR_ID
            else item
            for item in registry.collectors
        )
        with self.assertRaises(RegistryError):
            nyfed_soma_summary_registry_profile(
                replace(registry, collectors=collectors)
            )


if __name__ == "__main__":
    unittest.main()
