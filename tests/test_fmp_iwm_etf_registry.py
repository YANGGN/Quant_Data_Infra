from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    fmp_iwm_etf_daily_history_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.market.iwm_etf_daily_history"
DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.instruments",
    "market.stage10.universes",
    "market.stage10.daily_prices",
)
CURRENT_SOURCE_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
PREVIOUS_SOURCE_SHA256 = (
    "6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1"
)


class FmpIwmEtfRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_current_collector_is_exact_reciprocal_and_projects_to_249(self) -> None:
        current = self._registry()
        self.assertEqual(
            (current.schema_version, current.revision, current.source_sha256),
            ("1.9.0", "2.64.0", CURRENT_SOURCE_SHA256),
        )
        self.assertEqual(
            (len(current.migrations), len(current.datasets), len(current.collectors)),
            (43, 58, 58),
        )
        collector = next(
            item for item in current.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(collector["version"], "1.0.0")
        self.assertEqual(collector["handler"], "market.fmp_iwm_etf_daily_history")
        self.assertTrue(collector["network"])
        self.assertEqual(collector["configuration_env"], ["FMP_API_KEY"])
        self.assertEqual(
            collector["input_datasets"],
            ["market.stage10.instruments", "market.stage10.universes"],
        )
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["semantic_identity"],
            {
                "excludes": [
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                ],
                "includes": [
                    "scope_manifest_sha256",
                    "base_scope_manifest_sha256",
                    "successor_membership_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_history_sha256",
                ],
            },
        )
        self.assertEqual(
            collector["mutation_policy"],
            {
                "mode": "append_successor_universe_and_price_versions",
                "unchanged": "zero_persistent_writes",
            },
        )
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 16_777_216,
                "max_requests": 1,
                "max_rows": 30_000,
                "max_seconds": 45,
            },
        )
        self.assertEqual(
            collector["retry_policy"],
            {
                "backoff": "none_single_attempt",
                "honor_retry_after": False,
                "max_attempts": 1,
                "transient_classes": [],
            },
        )
        self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})
        self.assertEqual(
            collector["physical_locks"], "derived_from_output_store_paths"
        )
        datasets = {item.id: item for item in current.datasets}
        for dataset_id in DATASET_IDS:
            self.assertEqual(datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1)
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in current.jobs
                for step in job.steps
            )
        )

        previous = fmp_iwm_etf_daily_history_registry_profile(current)
        self.assertEqual(
            (previous.revision, previous.source_sha256),
            ("2.49.0", PREVIOUS_SOURCE_SHA256),
        )
        self.assertEqual(
            (len(previous.migrations), len(previous.datasets), len(previous.collectors)),
            (40, 53, 54),
        )
        self.assertNotIn(COLLECTOR_ID, {str(item["id"]) for item in previous.collectors})
        previous_datasets = {item.id: item for item in previous.datasets}
        for dataset_id in DATASET_IDS:
            self.assertNotIn(COLLECTOR_ID, previous_datasets[dataset_id].collector_ids)
        payload = (
            json.dumps(previous.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PREVIOUS_SOURCE_SHA256)
        self.assertIs(fmp_iwm_etf_daily_history_registry_profile(previous), previous)

    def test_current_delta_drift_fails_closed(self) -> None:
        current = self._registry()
        raw = json.loads(json.dumps(current.raw))
        collector = next(
            item for item in raw["collectors"] if item["id"] == COLLECTOR_ID
        )
        collector["handler"] = "market.unreviewed"
        with self.assertRaises(RegistryError):
            fmp_iwm_etf_daily_history_registry_profile(replace(current, raw=raw))

        raw = json.loads(json.dumps(current.raw))
        dataset = next(
            item
            for item in raw["datasets"]
            if item["id"] == "market.stage10.daily_prices"
        )
        dataset["collector_ids"].remove(COLLECTOR_ID)
        with self.assertRaises(RegistryError):
            fmp_iwm_etf_daily_history_registry_profile(replace(current, raw=raw))

        raw = json.loads(json.dumps(current.raw))
        collector = next(
            item for item in raw["collectors"] if item["id"] == COLLECTOR_ID
        )
        collector["workload_bounds"]["max_rows"] = 30_001
        with self.assertRaises(RegistryError):
            fmp_iwm_etf_daily_history_registry_profile(replace(current, raw=raw))


if __name__ == "__main__":
    unittest.main()
