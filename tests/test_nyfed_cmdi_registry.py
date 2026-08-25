from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    nyfed_cmdi_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "nyfed.macro.cmdi_history"
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
PRE_CMDI_SOURCE_SHA256 = (
    "9be40d07a7984b223303174a079b19a2a57fa7fcb5a56678482533dc53e4f8ff"
)
CURRENT_SOURCE_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)


class NyFedCmdiRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_manual_credential_free_and_bound(self) -> None:
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
            collector,
            {
                "configuration_env": [],
                "handler": "macro.nyfed_cmdi_history",
                "id": COLLECTOR_ID,
                "input_datasets": [],
                "mutation_policy": {
                    "mode": "append_versions_and_snapshot_membership",
                    "unchanged": "zero_persistent_writes",
                },
                "network": True,
                "output_datasets": list(DATASET_IDS),
                "physical_locks": "derived_from_output_store_paths",
                "retry_policy": {
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                    "max_attempts": 1,
                    "transient_classes": [],
                },
                "schedule_eligibility": {"mode": "manual_only"},
                "semantic_identity": {
                    "excludes": [
                        "captured_at",
                        "http_headers",
                        "source_row_order",
                    ],
                    "includes": [
                        "request_scope",
                        "normalization_version",
                        "normalized_observations",
                    ],
                },
                "version": "1.0.0",
                "workload_bounds": {
                    "max_bytes": 16_777_216,
                    "max_requests": 1,
                    "max_rows": 5_000,
                    "max_seconds": 60,
                },
            },
        )
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

    def test_revision_227_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = nyfed_cmdi_registry_profile(registry)

        self.assertEqual(historical.revision, "2.27.0")
        self.assertEqual(historical.source_sha256, PRE_CMDI_SOURCE_SHA256)
        self.assertEqual(len(historical.collectors), 44)
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
            PRE_CMDI_SOURCE_SHA256,
        )
        drifted = replace(
            registry,
            collectors=tuple(
                {
                    **dict(item),
                    "workload_bounds": {
                        **dict(item["workload_bounds"]),
                        "max_rows": 4_999,
                    },
                }
                if item["id"] == COLLECTOR_ID
                else item
                for item in registry.collectors
            ),
        )
        with self.assertRaises(RegistryError):
            nyfed_cmdi_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
