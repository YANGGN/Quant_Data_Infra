from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry, stage8_registry_profile, stage9_registry_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.market.daily_price_backfill"
DATASET_IDS = (
    "market.fmp.daily_price_evidence",
    "market.fmp.instruments",
    "market.fmp.daily_prices",
)
MIGRATION_ID = "market:0009_fmp_daily_price_backfill"
STAGE8_SOURCE_SHA256 = (
    "f4f565db7638ef026859af3f3b68b967ca54a80d1e09027d3afd1260cf53c10b"
)


class Stage9RegistryTests(unittest.TestCase):
    def _raw(self) -> dict[str, object]:
        value = loads_strict(REGISTRY_PATH.read_bytes(), max_bytes=16 * 1024 * 1024)
        self.assertIsInstance(value, dict)
        return value

    def _assert_rejected(self, mutate) -> None:
        raw = self._raw()
        mutate(raw)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "registry.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_canonical_fmp_backfill_contract_is_closed_and_isolated(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        registry = stage9_registry_profile(registry)
        self.assertEqual((registry.schema_version, registry.registry_version), ("1.5.0", "2.7.0"))
        self.assertEqual((len(registry.migrations), len(registry.datasets), len(registry.collectors)), (31, 36, 20))

        collector = next(item for item in registry.collectors if item["id"] == COLLECTOR_ID)
        self.assertEqual(collector["handler"], "market.fmp_daily_price")
        self.assertIs(collector["network"], True)
        self.assertEqual(tuple(collector["input_datasets"]), ())
        self.assertEqual(tuple(collector["output_datasets"]), DATASET_IDS)
        self.assertEqual(tuple(collector["configuration_env"]), ("FMP_API_KEY",))
        self.assertEqual(
            dict(collector["workload_bounds"]),
            {"max_bytes": 262144, "max_requests": 1, "max_rows": 22, "max_seconds": 30},
        )
        self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})

        declarations = tuple(
            next(declaration for declaration in registry.datasets if declaration.id == item)
            for item in DATASET_IDS
        )
        self.assertEqual(tuple(item.store for item in declarations), ("market", "market", "market"))
        self.assertEqual(tuple(item.collector_ids for item in declarations), ((COLLECTOR_ID,),) * 3)
        for declaration in declarations:
            self.assertFalse(declaration.tool_ids)
            self.assertFalse(declaration.dashboard_ids)
            self.assertFalse(declaration.export_ids)
        relations = {relation for item in declarations for relation in item.relations}
        self.assertEqual(
            relations,
            {
                "fmp_instrument_identities",
                "fmp_daily_price_captures",
                "fmp_daily_price_versions",
                "fmp_daily_prices",
            },
        )

        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual(migration.store, "market")
        self.assertEqual(migration.ordinal, 9)
        self.assertEqual(
            migration.sha256,
            "f2e664888449cb3d1885f1199d1a22996709cb87f4f3ac005bd8f91c0cc62ba0",
        )
        self.assertEqual(registry.store("market").migration_order[-1], MIGRATION_ID)

    def test_stage8_projection_is_exact_and_source_pinned(self) -> None:
        current = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        historical = stage8_registry_profile(current)
        self.assertEqual((historical.schema_version, historical.registry_version), ("1.4.0", "2.6.0"))
        self.assertEqual((len(historical.migrations), len(historical.datasets), len(historical.collectors)), (30, 33, 19))
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertTrue(set(DATASET_IDS).isdisjoint(item.id for item in historical.datasets))
        self.assertNotIn(COLLECTOR_ID, {str(item["id"]) for item in historical.collectors})
        payload = (json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STAGE8_SOURCE_SHA256)
        self.assertEqual(historical.source_sha256, STAGE8_SOURCE_SHA256)

    def test_hostile_live_collector_or_migration_drift_fails_closed(self) -> None:
        def collector(raw):
            return next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)

        mutations = (
            lambda raw: collector(raw).__setitem__("network", False),
            lambda raw: collector(raw).__setitem__("handler", "market.other"),
            lambda raw: collector(raw).__setitem__("configuration_env", ["FMP_API_KEY", "EXTRA"]),
            lambda raw: collector(raw)["workload_bounds"].__setitem__("max_requests", 2),
            lambda raw: collector(raw)["retry_policy"].__setitem__("max_attempts", 2),
            lambda raw: collector(raw)["output_datasets"].pop(),
            lambda raw: next(item for item in raw["migrations"] if item["id"] == MIGRATION_ID).__setitem__("sha256", "0" * 64),
            lambda raw: next(item for item in raw["datasets"] if item["id"] == DATASET_IDS[2])["physical"].__setitem__("relations", ["prices_daily"]),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._assert_rejected(mutate)


if __name__ == "__main__":
    unittest.main()
