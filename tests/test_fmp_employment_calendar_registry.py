from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import (
    fmp_employment_release_calendar_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.macro.employment_release_calendar_refresh"
DATASET_ID = "fixture.macro.economic_calendar"
PRE_EMPLOYMENT_CALENDAR_SOURCE_SHA256 = (
    "f76a61027045c4408c60e57dacfcb5a17c617de4099395b1c6a62790e4019c4e"
)
EXPECTED_COLLECTOR = {
    "configuration_env": ["FMP_API_KEY"],
    "handler": "macro.fmp_employment_release_calendar_refresh",
    "id": COLLECTOR_ID,
    "input_datasets": [],
    "mutation_policy": {
        "mode": "append_versions_and_snapshot_membership",
        "unchanged": "zero_persistent_writes",
    },
    "network": True,
    "output_datasets": [DATASET_ID],
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
        ],
        "includes": [
            "request_scope",
            "normalization_version",
            "normalized_complete_batch",
        ],
    },
    "version": "1.0.0",
    "workload_bounds": {
        "max_bytes": 1_048_576,
        "max_requests": 1,
        "max_rows": 2_000,
        "max_seconds": 60,
    },
}


class FmpEmploymentCalendarRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_canonical_employment_calendar_collector_is_exact_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.47.0")
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (40, 53, 53),
        )
        collector = next(
            item for item in registry.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(dict(collector), EXPECTED_COLLECTOR)
        calendar = next(item for item in registry.datasets if item.id == DATASET_ID)
        self.assertEqual(
            calendar.collector_ids,
            (
                "fixture.macro.calendar_import",
                "fmp.macro.gdp_cpi_release_calendar_history",
                COLLECTOR_ID,
            ),
        )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_219_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()

        historical = fmp_employment_release_calendar_registry_profile(registry)
        self.assertEqual(historical.revision, "2.19.0")
        self.assertEqual(
            historical.source_sha256,
            PRE_EMPLOYMENT_CALENDAR_SOURCE_SHA256,
        )
        self.assertNotIn(COLLECTOR_ID, {item["id"] for item in historical.collectors})
        calendar = next(item for item in historical.datasets if item.id == DATASET_ID)
        self.assertEqual(
            calendar.collector_ids,
            (
                "fixture.macro.calendar_import",
                "fmp.macro.gdp_cpi_release_calendar_history",
            ),
        )
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            PRE_EMPLOYMENT_CALENDAR_SOURCE_SHA256,
        )

        raw = json.loads(json.dumps(registry.raw))
        fixture_collector = next(
            item
            for item in raw["collectors"]
            if item["id"] == "fixture.macro.calendar_import"
        )
        fixture_collector["version"] = "1.0.1"
        drifted = replace(registry, raw=raw)
        with self.assertRaises(RegistryError):
            fmp_employment_release_calendar_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
