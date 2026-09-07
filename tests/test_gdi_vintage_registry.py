from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from quant_data.registry import (
    gdi_vintage_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_ID = "macro:0017_live_gdi_vintages"
MIGRATION_SHA256 = (
    "e92da1620b2da10e15d76ee9a3817fc081d0363f6cd7b87653f2340a8f756e71"
)
CURRENT_SOURCE_SHA256 = (
    "174c4b23a1bbfccd3188d8dd944a64023dd0dad0e08fdd7cf381015a8b498b05"
)
PRE_GDI_SOURCE_SHA256 = (
    "4945c54e695b093112e6e7425dc214e5288396cf309d23ccf1aba33e386ee1e6"
)


class GdiVintageRegistryTests(unittest.TestCase):
    def test_additive_migration_is_current_and_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

        self.assertEqual(registry.revision, "2.74.0")
        self.assertEqual(registry.source_sha256, CURRENT_SOURCE_SHA256)
        self.assertEqual(
            (
                len(registry.migrations),
                len(registry.datasets),
                len(registry.collectors),
            ),
            (48, 64, 70),
        )
        migration = next(
            item for item in registry.migrations if item.id == MIGRATION_ID
        )
        self.assertEqual((migration.store, migration.ordinal), ("macro", 17))
        self.assertEqual(
            migration.dependencies,
            ("macro:0016_fmp_calendar_wholesale_evidence",),
        )
        self.assertEqual(migration.sha256, MIGRATION_SHA256)
        self.assertEqual(
            hashlib.sha256(
                (PROJECT_ROOT / migration.resource).read_bytes()
            ).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(
            registry.store("macro").migration_order[-2],
            MIGRATION_ID,
        )

    def test_revision_230_projection_is_byte_exact(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

        historical = gdi_vintage_registry_profile(registry)

        self.assertEqual(historical.revision, "2.30.0")
        self.assertEqual(historical.source_sha256, PRE_GDI_SOURCE_SHA256)
        self.assertNotIn(
            MIGRATION_ID,
            {item.id for item in historical.migrations},
        )
        self.assertNotIn(
            MIGRATION_ID,
            historical.store("macro").migration_order,
        )


if __name__ == "__main__":
    unittest.main()
