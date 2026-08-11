from __future__ import annotations

import unittest
from pathlib import Path

from quant_data.registry import (
    load_registry,
    stage2_registry_profile,
    stage3_registry_profile,
    stage4_registry_profile,
    stage5_registry_profile,
    stage6_registry_profile,
    stage7_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage3RegistryProfileTests(unittest.TestCase):
    def test_additive_registry_reproduces_the_stage3_contract(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / "config" / "system_registry.json",
            project_root=PROJECT_ROOT,
        )

        stage3 = stage3_registry_profile(registry)

        self.assertEqual(stage3.registry_version, "2.1.0")
        self.assertEqual(stage3.raw["registry_version"], "2.1.0")
        self.assertEqual(len(stage3.migrations), 21)
        self.assertEqual(len(stage3.datasets), 19)
        self.assertEqual(len(stage3.collectors), 14)
        self.assertEqual(len(stage3.tools), 2)
        self.assertEqual(
            {
                store.id: len(store.migration_order)
                for store in stage3.stores
            },
            {
                "company": 2,
                "macro": 11,
                "market": 6,
                "news": 2,
            },
        )
        self.assertEqual(
            {item.id for item in stage3.migrations},
            {item["id"] for item in stage3.raw["migrations"]},
        )
        self.assertEqual(
            {item.id for item in stage3.datasets},
            {item["id"] for item in stage3.raw["datasets"]},
        )

    def test_canonical_registry_matches_the_stage4_contract(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / "config" / "system_registry.json",
            project_root=PROJECT_ROOT,
        )

        stage4 = stage4_registry_profile(registry)

        self.assertEqual(stage4.registry_version, "2.2.0")
        self.assertEqual(stage4.raw["registry_version"], "2.2.0")
        self.assertEqual(len(stage4.migrations), 30)
        self.assertEqual(len(stage4.datasets), 33)
        self.assertEqual(len(stage4.collectors), 19)
        self.assertEqual(len(stage4.tools), 2)
        self.assertEqual(stage4.raw["jobs"], [])
        self.assertEqual(stage4.raw["exports"], [])
        self.assertEqual(
            {
                store.id: len(store.migration_order)
                for store in stage4.stores
            },
            {
                "company": 7,
                "macro": 11,
                "market": 8,
                "news": 4,
            },
        )
        self.assertEqual(
            {item.id for item in stage4.migrations},
            {item["id"] for item in stage4.raw["migrations"]},
        )
        self.assertEqual(
            {item.id for item in stage4.datasets},
            {item["id"] for item in stage4.raw["datasets"]},
        )

    def test_all_pre_stage7_profiles_keep_jobs_empty(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / "config" / "system_registry.json",
            project_root=PROJECT_ROOT,
        )
        profiles = (
            (stage2_registry_profile(registry), "1.0.0", "2.0.0"),
            (stage3_registry_profile(registry), "1.0.0", "2.1.0"),
            (stage4_registry_profile(registry), "1.0.0", "2.2.0"),
            (stage5_registry_profile(registry), "1.1.0", "2.3.0"),
            (stage6_registry_profile(registry), "1.2.0", "2.4.0"),
        )
        for profile, schema_version, registry_version in profiles:
            with self.subTest(registry_version=registry_version):
                self.assertEqual(profile.schema_version, schema_version)
                self.assertEqual(profile.registry_version, registry_version)
                self.assertEqual(profile.jobs, ())
                self.assertEqual(profile.exports, ())
                self.assertEqual(profile.raw["jobs"], [])
                self.assertEqual(profile.raw["exports"], [])
                self.assertTrue(
                    all(not dataset.export_ids for dataset in profile.datasets)
                )
                self.assertTrue(
                    all(
                        not dataset["export_ids"]
                        for dataset in profile.raw["datasets"]
                    )
                )

        stage7 = stage7_registry_profile(registry)
        self.assertEqual(stage7.schema_version, "1.3.0")
        self.assertEqual(stage7.registry_version, "2.5.0")
        self.assertEqual(len(stage7.jobs), 8)
        self.assertEqual(stage7.exports, ())
        self.assertEqual(stage7.raw["exports"], [])
        self.assertTrue(all(not dataset.export_ids for dataset in stage7.datasets))
        self.assertTrue(
            all(not dataset["export_ids"] for dataset in stage7.raw["datasets"])
        )
