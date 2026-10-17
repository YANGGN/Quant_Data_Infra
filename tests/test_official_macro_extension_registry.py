from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    bls_price_wage_productivity_registry_profile,
    load_registry,
    official_macro_extension_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_IDS = (
    "treasury_fiscal_data.macro.tga_closing_balance_history",
    "eia.macro.natural_gas_storage_history",
    "nber.macro.us_recession_history",
)
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
CURRENT_SOURCE_SHA256 = (
    "d7a5ba0a556abc9faa6d726e162969d4c11f0a5da614865eff23318d16ca55ff"
)
PRE_EXTENSION_SOURCE_SHA256 = (
    "131b4f24b2e8d7d9dfa7fedb5de37cf16455aa5a0ee91d0aae69315fd0ac2aef"
)


class OfficialMacroExtensionRegistryTests(unittest.TestCase):
    def _registry(self):
        current = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        return bls_price_wage_productivity_registry_profile(current)

    def test_three_collectors_are_manual_bounded_and_unjobbed(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.29.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (38, 51, 48),
        )
        collectors = {str(item["id"]): item for item in registry.collectors}
        expected = {
            COLLECTOR_IDS[0]: (
                "macro.treasury_fiscal_tga_history",
                [],
                10_000,
                ["captured_at", "http_headers", "source_row_order"],
            ),
            COLLECTOR_IDS[1]: (
                "macro.eia_natural_gas_storage_history",
                ["EIA_API_KEY"],
                5_000,
                ["api_key", "captured_at", "http_headers", "source_row_order"],
            ),
            COLLECTOR_IDS[2]: (
                "macro.nber_us_recession_history",
                [],
                5_000,
                ["captured_at", "http_headers", "source_row_order"],
            ),
        }
        for collector_id, values in expected.items():
            collector = collectors[collector_id]
            self.assertEqual(collector["handler"], values[0])
            self.assertEqual(collector["configuration_env"], values[1])
            self.assertTrue(collector["network"])
            self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
            self.assertEqual(
                collector["schedule_eligibility"], {"mode": "manual_only"}
            )
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(
                collector["workload_bounds"],
                {
                    "max_bytes": 16_777_216,
                    "max_requests": 1,
                    "max_rows": values[2],
                    "max_seconds": 60,
                },
            )
            self.assertEqual(
                collector["semantic_identity"]["excludes"], values[3]
            )

        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            self.assertEqual(
                datasets[dataset_id].collector_ids[-3:], COLLECTOR_IDS
            )
            for collector_id in COLLECTOR_IDS:
                self.assertEqual(
                    datasets[dataset_id].collector_ids.count(collector_id), 1
                )
        self.assertFalse(
            any(
                step.collector_id in COLLECTOR_IDS
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_228_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = self._registry()
        historical = official_macro_extension_registry_profile(registry)

        self.assertEqual(historical.revision, "2.28.0")
        self.assertEqual(
            historical.source_sha256, PRE_EXTENSION_SOURCE_SHA256
        )
        self.assertEqual(len(historical.collectors), 45)
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
            hashlib.sha256(payload).hexdigest(), PRE_EXTENSION_SOURCE_SHA256
        )
        self.assertFalse(
            set(COLLECTOR_IDS).intersection(
                str(item["id"]) for item in historical.collectors
            )
        )

        first = DATASET_IDS[0]
        drifted = replace(
            registry,
            datasets=tuple(
                replace(item, collector_ids=item.collector_ids[:-1])
                if item.id == first
                else item
                for item in registry.datasets
            ),
        )
        with self.assertRaises(RegistryError):
            official_macro_extension_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
