from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import (
    fmp_treasury_yield_curve_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.macro.treasury_yield_curve_history"
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
    "fixture.macro.treasury_yield_curves",
)
PRE_TREASURY_SOURCE_SHA256 = (
    "b16724532234c3c1082326bdd2f0957c8518508849bcc5d090728fcfd2e3a8cd"
)
CURRENT_SOURCE_SHA256 = (
    "174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05"
)
EXPECTED_COLLECTOR = {
    "configuration_env": ["FMP_API_KEY"],
    "handler": "macro.fmp_treasury_yield_curve_history",
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
            "api_key",
            "captured_at",
            "http_headers",
            "source_row_order",
            "unknown_provider_fields",
        ],
        "includes": [
            "request_scope",
            "normalization_version",
            "normalized_12_tenor_batch",
        ],
    },
    "version": "1.0.0",
    "workload_bounds": {
        "max_bytes": 16_777_216,
        "max_requests": 1,
        "max_rows": 20_000,
        "max_seconds": 60,
    },
}


class FmpTreasuryCurveRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_canonical_treasury_collector_is_exact_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.74.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (48, 64, 70),
        )
        collector = next(
            item for item in registry.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(dict(collector), EXPECTED_COLLECTOR)
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            self.assertIn(COLLECTOR_ID, datasets[dataset_id].collector_ids)
            self.assertEqual(datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1)
        self.assertEqual(
            datasets["fixture.macro.treasury_yield_curves"].collector_ids[-1],
            COLLECTOR_ID,
        )
        self.assertEqual(
            registry.store("macro").migration_order[-3],
            "macro:0016_fmp_calendar_wholesale_evidence",
        )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_221_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()

        historical = fmp_treasury_yield_curve_registry_profile(registry)
        self.assertEqual(historical.revision, "2.21.0")
        self.assertEqual(historical.source_sha256, PRE_TREASURY_SOURCE_SHA256)
        self.assertNotIn(COLLECTOR_ID, {item["id"] for item in historical.collectors})
        for dataset in historical.datasets:
            self.assertNotIn(COLLECTOR_ID, dataset.collector_ids)
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            PRE_TREASURY_SOURCE_SHA256,
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
            fmp_treasury_yield_curve_registry_profile(
                replace(registry, collectors=collectors)
            )


if __name__ == "__main__":
    unittest.main()
