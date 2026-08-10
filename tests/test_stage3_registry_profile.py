from __future__ import annotations

import unittest
from pathlib import Path

from quant_data.registry import (
    load_registry,
    stage3_registry_profile,
    stage4_registry_profile,
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
