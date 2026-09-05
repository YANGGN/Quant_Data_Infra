from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    alpaca_etf_options_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "alpaca.market.etf_option_surface_grid"
CORE_DATASET_IDS = (
    "fixture.market.instruments",
    "fixture.market.option_capture_evidence",
    "fixture.market.options",
)
RAW_EVIDENCE_DATASET_ID = "market.alpaca.option_raw_evidence"
CURRENT_DATASET_IDS = (*CORE_DATASET_IDS, RAW_EVIDENCE_DATASET_ID)
CURRENT_SOURCE_SHA256 = (
    "4c2de9ef1ac49a4c23ab326000878fa66629caa1f8a0bcb65d4c089d827e9ac3"
)
PREVIOUS_SOURCE_SHA256 = (
    "3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524"
)


class AlpacaEtfOptionsRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )

    def test_manual_collector_is_exact_reciprocal_and_projects_to_248(self) -> None:
        current = self._registry()
        self.assertEqual(
            (current.schema_version, current.revision, current.source_sha256),
            ("1.9.0", "2.70.0", CURRENT_SOURCE_SHA256),
        )
        collector = next(
            item for item in current.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(
            collector["handler"], "market.alpaca_etf_option_surface_grid"
        )
        self.assertEqual(
            collector["configuration_env"],
            ["ALPACA_API_KEY", "ALPACA_API_SECRET"],
        )
        self.assertEqual(
            collector["input_datasets"], ["market.stage10.instruments"]
        )
        self.assertEqual(collector["output_datasets"], list(CURRENT_DATASET_IDS))
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 268_435_456,
                "max_requests": 362,
                "max_rows": 900_016,
                "max_seconds": 900,
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
        self.assertEqual(
            collector["schedule_eligibility"], {"mode": "manual_only"}
        )
        datasets = {item.id: item for item in current.datasets}
        for dataset_id in CURRENT_DATASET_IDS:
            self.assertEqual(
                datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1
            )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in current.jobs
                for step in job.steps
            )
        )

        previous = alpaca_etf_options_registry_profile(current)
        self.assertEqual(
            (previous.revision, previous.source_sha256),
            ("2.48.0", PREVIOUS_SOURCE_SHA256),
        )
        self.assertNotIn(
            COLLECTOR_ID,
            {str(item["id"]) for item in previous.collectors},
        )
        previous_datasets = {item.id: item for item in previous.datasets}
        for dataset_id in CORE_DATASET_IDS:
            self.assertNotIn(
                COLLECTOR_ID, previous_datasets[dataset_id].collector_ids
            )
        payload = (
            json.dumps(
                previous.raw, ensure_ascii=True, indent=2, sort_keys=True
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(), PREVIOUS_SOURCE_SHA256
        )
        self.assertIs(alpaca_etf_options_registry_profile(previous), previous)

    def test_current_delta_drift_fails_closed(self) -> None:
        current = self._registry()
        raw = json.loads(json.dumps(current.raw))
        collector = next(
            item for item in raw["collectors"] if item["id"] == COLLECTOR_ID
        )
        collector["handler"] = "market.unreviewed"
        with self.assertRaises(RegistryError):
            alpaca_etf_options_registry_profile(replace(current, raw=raw))

        raw = json.loads(json.dumps(current.raw))
        dataset = next(
            item
            for item in raw["datasets"]
            if item["id"] == "fixture.market.options"
        )
        dataset["collector_ids"].remove(COLLECTOR_ID)
        with self.assertRaises(RegistryError):
            alpaca_etf_options_registry_profile(replace(current, raw=raw))


if __name__ == "__main__":
    unittest.main()
