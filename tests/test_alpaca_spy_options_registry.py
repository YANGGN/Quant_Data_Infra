from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import alpaca_spy_options_registry_profile, load_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "alpaca.market.spy_option_surface"
DATASET_IDS = (
    "fixture.market.instruments",
    "fixture.market.option_capture_evidence",
    "fixture.market.options",
)
CURRENT_SOURCE_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)
PREVIOUS_SOURCE_SHA256 = (
    "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
)


class AlpacaSpyOptionsRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def test_collector_is_bounded_reciprocal_and_projects_to_exact_predecessor(self) -> None:
        current = self._registry()
        self.assertEqual(
            (current.schema_version, current.revision, current.source_sha256),
            ("1.9.0", "2.43.0", CURRENT_SOURCE_SHA256),
        )
        collector = next(item for item in current.collectors if item["id"] == COLLECTOR_ID)
        self.assertEqual(collector["handler"], "market.alpaca_spy_option_surface")
        self.assertEqual(
            collector["configuration_env"],
            ["ALPACA_API_KEY", "ALPACA_API_SECRET"],
        )
        self.assertEqual(collector["input_datasets"], ["market.stage10.instruments"])
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 8_388_608,
                "max_requests": 4,
                "max_rows": 10_000,
                "max_seconds": 120,
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

        previous = alpaca_spy_options_registry_profile(current)
        self.assertEqual(
            (previous.revision, previous.source_sha256),
            ("2.42.0", PREVIOUS_SOURCE_SHA256),
        )
        self.assertNotIn(COLLECTOR_ID, {str(item["id"]) for item in previous.collectors})
        previous_datasets = {item.id: item for item in previous.datasets}
        for dataset_id in DATASET_IDS:
            self.assertNotIn(COLLECTOR_ID, previous_datasets[dataset_id].collector_ids)
        payload = (
            json.dumps(previous.raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PREVIOUS_SOURCE_SHA256)
        self.assertIs(alpaca_spy_options_registry_profile(previous), previous)

    def test_current_delta_drift_fails_closed(self) -> None:
        current = self._registry()
        raw = json.loads(json.dumps(current.raw))
        collector = next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)
        collector["handler"] = "market.unreviewed"

        with self.assertRaises(RegistryError):
            alpaca_spy_options_registry_profile(replace(current, raw=raw))


if __name__ == "__main__":
    unittest.main()
