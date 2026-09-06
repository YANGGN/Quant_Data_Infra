from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    macro_database_expansion_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_IDS = (
    "cftc.macro.tff_futures_only_history",
    "cftc.macro.disaggregated_futures_only_history",
    "treasury_fiscal_data.macro.securities_auctions_history",
    "nyfed.macro.primary_dealer_statistics_history",
    "federal_reserve.macro.h8_history",
    "federal_reserve.macro.sloos_history",
)
DATASET_IDS = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
CURRENT_SOURCE_SHA256 = (
    "55285a106a56a3d664f83dd75cb71c43aa21f5e9637d0200704933a291732a78"
)
PRE_EXPANSION_SOURCE_SHA256 = (
    "a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325"
)
SPECS = {
    COLLECTOR_IDS[0]: (
        "macro.cftc_tff_futures_only_history",
        8,
        4_000,
        16_777_216,
        480,
    ),
    COLLECTOR_IDS[1]: (
        "macro.cftc_disaggregated_futures_only_history",
        8,
        4_000,
        16_777_216,
        480,
    ),
    COLLECTOR_IDS[2]: (
        "macro.treasury_securities_auctions_history",
        1,
        1_000,
        16_777_216,
        60,
    ),
    COLLECTOR_IDS[3]: (
        "macro.nyfed_primary_dealer_statistics_history",
        33,
        100_000,
        67_108_864,
        180,
    ),
    COLLECTOR_IDS[4]: (
        "macro.federal_reserve_h8_history",
        7,
        20_000,
        16_777_216,
        420,
    ),
    COLLECTOR_IDS[5]: (
        "macro.federal_reserve_sloos_history",
        6,
        20_000,
        16_777_216,
        360,
    ),
}


class MacroDatabaseExpansionRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collectors_are_manual_credential_free_and_reciprocal(self) -> None:
        registry = self._registry()

        self.assertEqual(registry.revision, "2.71.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (45, 61, 68),
        )
        collectors = {
            str(item["id"]): item for item in registry.collectors
        }
        self.assertEqual(set(COLLECTOR_IDS), set(collectors).intersection(COLLECTOR_IDS))
        for collector_id, (
            handler,
            max_requests,
            max_rows,
            max_bytes,
            max_seconds,
        ) in SPECS.items():
            collector = collectors[collector_id]
            self.assertEqual(collector["handler"], handler)
            self.assertEqual(collector["configuration_env"], [])
            self.assertTrue(collector["network"])
            self.assertEqual(collector["input_datasets"], [])
            self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
            self.assertEqual(
                collector["physical_locks"],
                "derived_from_output_store_paths",
            )
            self.assertEqual(
                collector["schedule_eligibility"],
                {"mode": "manual_only"},
            )
            self.assertEqual(
                collector["workload_bounds"],
                {
                    "max_requests": max_requests,
                    "max_rows": max_rows,
                    "max_bytes": max_bytes,
                    "max_seconds": max_seconds,
                },
            )
            self.assertEqual(
                collector["retry_policy"],
                {
                    "transient_classes": [],
                    "max_attempts": 1,
                    "backoff": "none_single_attempt",
                    "honor_retry_after": False,
                },
            )
        datasets = {item.id: item for item in registry.datasets}
        for dataset_id in DATASET_IDS:
            self.assertEqual(
                datasets[dataset_id].collector_ids[-len(COLLECTOR_IDS) :],
                COLLECTOR_IDS,
            )
            for collector_id in COLLECTOR_IDS:
                self.assertEqual(
                    datasets[dataset_id].collector_ids.count(collector_id),
                    1,
                )
        self.assertFalse(
            any(
                step.collector_id in COLLECTOR_IDS
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_projection_restores_exact_267_and_rejects_drift(self) -> None:
        registry = self._registry()
        historical = macro_database_expansion_registry_profile(registry)

        self.assertEqual(historical.revision, "2.67.0")
        self.assertEqual(historical.source_sha256, PRE_EXPANSION_SOURCE_SHA256)
        self.assertEqual(len(historical.collectors), 58)
        self.assertTrue(
            set(COLLECTOR_IDS).isdisjoint(
                {str(item["id"]) for item in historical.collectors}
            )
        )
        self.assertTrue(
            all(
                set(COLLECTOR_IDS).isdisjoint(dataset.collector_ids)
                for dataset in historical.datasets
            )
        )
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
            PRE_EXPANSION_SOURCE_SHA256,
        )

        drifted = replace(
            registry,
            collectors=tuple(
                {
                    **dict(item),
                    "workload_bounds": {
                        **dict(item["workload_bounds"]),
                        "max_rows": 3_999,
                    },
                }
                if item["id"] == COLLECTOR_IDS[0]
                else item
                for item in registry.collectors
            ),
        )
        with self.assertRaises(RegistryError):
            macro_database_expansion_registry_profile(drifted)


if __name__ == "__main__":
    unittest.main()
