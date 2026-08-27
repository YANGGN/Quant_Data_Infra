from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import (
    PUBLIC_TOOL_NAMES,
    fmp_calendar_incremental_registry_profile,
    fmp_wholesale_calendar_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0016_fmp_calendar_wholesale_evidence"
DATASET_ID = "macro.fmp.economic_calendar_evidence"
COLLECTOR_ID = "fmp.macro.us_economic_calendar_wholesale"
MIGRATION_SHA256 = "78dc02d34c0489c3f1fe4b7847870a18955606b1f47a3309ed8464ee9f3bbb4d"
INCREMENTAL_MIGRATION_ID = "macro:0018_fmp_calendar_incremental_events"
INCREMENTAL_MIGRATION_RESOURCE = (
    "quant_data/migrations/macro/0018_fmp_calendar_incremental_events.sql"
)
INCREMENTAL_MIGRATION_SHA256 = (
    "078cfd62e7cd7a314e6b828b19414023f60fa81c760c89a31b3398f1b7e41a3e"
)
INCREMENTAL_EVIDENCE_DATASET_ID = (
    "macro.fmp.economic_calendar_incremental_evidence"
)
INCREMENTAL_EVENT_DATASET_ID = "macro.fmp.economic_calendar_incremental_events"
CURRENT_SOURCE_SHA256 = (
    "6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1"
)
PRE_INCREMENTAL_SOURCE_SHA256 = (
    "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
)
PRE_WHOLESALE_SOURCE_SHA256 = (
    "3c1caec6eedfb7e179d8a1f8e291ef6e8971da5f4539946ae76691ac53f22647"
)


class FmpWholesaleCalendarRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})

    def test_private_wholesale_declaration_and_exact_preimage_projection(self) -> None:
        current = self._registry()
        self.assertEqual(current.revision, "2.49.0")
        self.assertEqual(
            (len(current.migrations), len(current.datasets), len(current.collectors)),
            (40, 53, 54),
        )
        registry = fmp_calendar_incremental_registry_profile(current)

        self.assertEqual(registry.revision, "2.39.0")
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (39, 51, 50),
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
        self.assertEqual(registry.store("macro").migration_order[-2], MIGRATION_ID)

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

    def test_incremental_calendar_declaration_and_exact_2_39_projection(self) -> None:
        current = self._registry()
        self.assertEqual(
            (
                current.schema_version,
                current.revision,
                current.source_sha256,
            ),
            ("1.9.0", "2.49.0", CURRENT_SOURCE_SHA256),
        )
        self.assertEqual(
            (len(current.migrations), len(current.datasets), len(current.collectors)),
            (40, 53, 54),
        )

        migration = next(
            item
            for item in current.migrations
            if item.id == INCREMENTAL_MIGRATION_ID
        )
        self.assertEqual(
            (
                migration.store,
                migration.ordinal,
                migration.resource,
                migration.dependencies,
                migration.sha256,
                migration.reconstruction_state,
            ),
            (
                "macro",
                18,
                INCREMENTAL_MIGRATION_RESOURCE,
                ("macro:0017_live_gdi_vintages",),
                INCREMENTAL_MIGRATION_SHA256,
                "fixture_validated",
            ),
        )
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            INCREMENTAL_MIGRATION_SHA256,
        )
        self.assertEqual(
            current.store("macro").migration_order[-3:],
            (
                MIGRATION_ID,
                "macro:0017_live_gdi_vintages",
                INCREMENTAL_MIGRATION_ID,
            ),
        )
        self.assertEqual(
            next(item for item in current.datasets if item.id == DATASET_ID).collector_ids,
            (),
        )

        compact_datasets = {
            item.id: (
                item.store,
                item.layer,
                item.revision_policy,
                item.relations,
                item.collector_ids,
            )
            for item in current.datasets
            if item.id
            in {INCREMENTAL_EVIDENCE_DATASET_ID, INCREMENTAL_EVENT_DATASET_ID}
        }
        self.assertEqual(
            compact_datasets,
            {
                INCREMENTAL_EVIDENCE_DATASET_ID: (
                    "macro",
                    "evidence",
                    "current_state_capture",
                    (
                        "fmp_economic_calendar_fetch_receipts",
                        "fmp_economic_calendar_latest_response_cache",
                    ),
                    (COLLECTOR_ID,),
                ),
                INCREMENTAL_EVENT_DATASET_ID: (
                    "macro",
                    "canonical",
                    "append_version",
                    (
                        "fmp_economic_calendar_raw_events",
                        "fmp_economic_calendar_raw_event_versions",
                    ),
                    (COLLECTOR_ID,),
                ),
            },
        )
        collector = next(
            item for item in current.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(
            collector["output_datasets"],
            [INCREMENTAL_EVIDENCE_DATASET_ID, INCREMENTAL_EVENT_DATASET_ID],
        )
        self.assertEqual(
            collector["mutation_policy"],
            {
                "mode": "append_event_versions_and_replace_latest_cache",
                "unchanged": "zero_persistent_writes",
            },
        )
        self.assertEqual(
            collector["semantic_identity"],
            {
                "includes": [
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                    "predecessor_receipt_id",
                ],
                "excludes": [
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                ],
            },
        )

        projected = fmp_calendar_incremental_registry_profile(current)
        self.assertEqual(
            (
                projected.revision,
                projected.source_sha256,
                len(projected.migrations),
                len(projected.datasets),
                len(projected.collectors),
            ),
            ("2.39.0", PRE_INCREMENTAL_SOURCE_SHA256, 39, 51, 50),
        )
        self.assertNotIn(
            INCREMENTAL_MIGRATION_ID,
            {item.id for item in projected.migrations},
        )
        self.assertTrue(
            {
                INCREMENTAL_EVIDENCE_DATASET_ID,
                INCREMENTAL_EVENT_DATASET_ID,
            }.isdisjoint({item.id for item in projected.datasets})
        )
        self.assertEqual(
            projected.store("macro").migration_order[-2:],
            (MIGRATION_ID, "macro:0017_live_gdi_vintages"),
        )
        self.assertIs(
            fmp_calendar_incremental_registry_profile(projected),
            projected,
        )

    def test_new_wholesale_delta_drift_fails_closed(self) -> None:
        registry = self._registry()
        raw = json.loads(json.dumps(registry.raw))
        collector = next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)
        collector["handler"] = "macro.unreviewed"

        with self.assertRaises(RegistryError):
            fmp_wholesale_calendar_registry_profile(replace(registry, raw=raw))


if __name__ == "__main__":
    unittest.main()
