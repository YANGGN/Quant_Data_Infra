from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import (
    PUBLIC_TOOL_NAMES,
    fmp_wholesale_calendar_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0016_fmp_calendar_wholesale_evidence"
DATASET_ID = "macro.fmp.economic_calendar_evidence"
COLLECTOR_ID = "fmp.macro.us_economic_calendar_wholesale"
MIGRATION_SHA256 = "78dc02d34c0489c3f1fe4b7847870a18955606b1f47a3309ed8464ee9f3bbb4d"
PRE_WHOLESALE_SOURCE_SHA256 = (
    "3c1caec6eedfb7e179d8a1f8e291ef6e8971da5f4539946ae76691ac53f22647"
)


class FmpWholesaleCalendarRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def test_private_wholesale_declaration_and_exact_preimage_projection(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.21.0")
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (38, 51, 37),
        )
        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual(
            (
                migration.store,
                migration.ordinal,
                migration.dependencies,
                migration.sha256,
                migration.reconstruction_state,
            ),
            (
                "macro",
                16,
                ("macro:0015_live_macro_history_extension",),
                MIGRATION_SHA256,
                "fixture_validated",
            ),
        )
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(registry.store("macro").migration_order[-1], MIGRATION_ID)

        dataset = next(item for item in registry.datasets if item.id == DATASET_ID)
        self.assertEqual(dataset.store, "macro")
        self.assertEqual(dataset.layer, "evidence")
        self.assertEqual(
            dataset.relations,
            ("fmp_economic_calendar_captures", "fmp_economic_calendar_rows"),
        )
        self.assertEqual(dataset.collector_ids, (COLLECTOR_ID,))
        self.assertFalse(dataset.tool_ids)
        self.assertFalse(dataset.dashboard_ids)
        self.assertFalse(dataset.export_ids)

        collector = next(item for item in registry.collectors if item["id"] == COLLECTOR_ID)
        self.assertEqual(collector["handler"], "macro.fmp_us_economic_calendar_wholesale")
        self.assertTrue(collector["network"])
        self.assertEqual(collector["configuration_env"], ["FMP_API_KEY"])
        self.assertEqual(collector["output_datasets"], [DATASET_ID])
        self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        self.assertEqual(
            collector["semantic_identity"]["includes"],
            ["request_scope", "normalization_version", "normalized_complete_batch"],
        )
        self.assertEqual(tuple(item["id"] for item in registry.tools), PUBLIC_TOOL_NAMES)

        historical = fmp_wholesale_calendar_registry_profile(registry)
        self.assertEqual(historical.revision, "2.20.0")
        self.assertEqual(historical.source_sha256, PRE_WHOLESALE_SOURCE_SHA256)
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertNotIn(DATASET_ID, {item.id for item in historical.datasets})
        self.assertNotIn(COLLECTOR_ID, {str(item["id"]) for item in historical.collectors})
        self.assertEqual(
            tuple(item["id"] for item in historical.tools),
            tuple(item["id"] for item in registry.tools),
        )
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PRE_WHOLESALE_SOURCE_SHA256)
        self.assertIs(fmp_wholesale_calendar_registry_profile(historical), historical)

    def test_new_wholesale_delta_drift_fails_closed(self) -> None:
        registry = self._registry()
        raw = json.loads(json.dumps(registry.raw))
        collector = next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)
        collector["handler"] = "macro.unreviewed"

        with self.assertRaises(RegistryError):
            fmp_wholesale_calendar_registry_profile(replace(registry, raw=raw))


if __name__ == "__main__":
    unittest.main()
