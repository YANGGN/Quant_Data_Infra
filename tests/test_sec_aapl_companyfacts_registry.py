from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    sec_aapl_companyfacts_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "sec.company.aapl_fundamentals"
DATASET_IDS = (
    "fixture.company.sec_evidence",
    "fixture.company.issuers",
    "fixture.company.filings",
    "fixture.company.fundamentals",
    "fixture.company.filing_issuer_membership",
)
CURRENT_SOURCE_SHA256 = (
    "174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05"
)
PREVIOUS_SOURCE_SHA256 = (
    "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
)


class SecAaplCompanyFactsRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_bounded_reciprocal_and_projects_exactly(self) -> None:
        current = self._registry()
        self.assertEqual(
            (current.schema_version, current.revision, current.source_sha256),
            ("1.9.0", "2.74.0", CURRENT_SOURCE_SHA256),
        )
        collector = next(
            item for item in current.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(collector["handler"], "company.sec_aapl_fundamentals")
        self.assertEqual(
            collector["configuration_env"],
            ["SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL"],
        )
        self.assertEqual(collector["input_datasets"], [])
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 16_777_216,
                "max_requests": 2,
                "max_rows": 10_000,
                "max_seconds": 120,
            },
        )
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        self.assertEqual(
            collector["schedule_eligibility"], {"mode": "manual_only"}
        )
        datasets = {item.id: item for item in current.datasets}
        for dataset_id in DATASET_IDS:
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

        previous = sec_aapl_companyfacts_registry_profile(current)
        self.assertEqual(
            (previous.revision, previous.source_sha256),
            ("2.43.0", PREVIOUS_SOURCE_SHA256),
        )
        self.assertNotIn(
            COLLECTOR_ID, {str(item["id"]) for item in previous.collectors}
        )
        previous_datasets = {item.id: item for item in previous.datasets}
        for dataset_id in DATASET_IDS:
            self.assertNotIn(
                COLLECTOR_ID, previous_datasets[dataset_id].collector_ids
            )
        payload = (
            json.dumps(previous.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PREVIOUS_SOURCE_SHA256)
        self.assertIs(sec_aapl_companyfacts_registry_profile(previous), previous)

    def test_current_delta_drift_fails_closed(self) -> None:
        current = self._registry()
        raw = json.loads(json.dumps(current.raw))
        collector = next(
            item for item in raw["collectors"] if item["id"] == COLLECTOR_ID
        )
        collector["handler"] = "company.unreviewed"
        with self.assertRaises(RegistryError):
            sec_aapl_companyfacts_registry_profile(replace(current, raw=raw))


if __name__ == "__main__":
    unittest.main()
