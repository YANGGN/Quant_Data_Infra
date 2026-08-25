from __future__ import annotations

import tempfile
import hashlib
import json
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry, stage9_registry_profile, stage10_registry_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_IDS = (
    "fmp.market.stage10_universe_capture",
    "fmp.market.stage10_daily_history",
)
DATASET_IDS = (
    "market.stage10.source_evidence",
    "market.stage10.instruments",
    "market.stage10.universes",
    "market.stage10.daily_prices",
)
MIGRATION_ID = "market:0010_stage10_market_history"
MIGRATION_SHA256 = "a7703655f6fe8089431eacdc78600582d6f5582589c0c38b531a2b93c7dc041a"
STAGE9_SOURCE_SHA256 = "46ff0f92f92380c203aacaf54e511ed219f3bc43edaaba0fb5a6fbe29b95a145"


class Stage10RegistryTests(unittest.TestCase):
    def _raw(self) -> dict[str, object]:
        value = loads_strict(REGISTRY_PATH.read_bytes())
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

    def test_canonical_stage10_contract_is_closed_and_nonproduction_only(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual((registry.schema_version, registry.registry_version), ("1.9.0", "2.43.0"))
        registry = stage10_registry_profile(registry)
        self.assertEqual((registry.schema_version, registry.registry_version), ("1.6.0", "2.8.0"))
        self.assertEqual(registry.store("market").default_path, "data/market_data.sqlite")
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (32, 40, 22),
        )

        collectors = {
            str(item["id"]): item
            for item in registry.collectors
            if item["id"] in COLLECTOR_IDS
        }
        self.assertEqual(tuple(collectors), COLLECTOR_IDS)
        for collector in collectors.values():
            self.assertIs(collector["network"], True)
            self.assertEqual(tuple(collector["configuration_env"]), ("FMP_API_KEY",))
            self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(collector["retry_policy"]["backoff"], "none_operator_resume")

        self.assertEqual(
            dict(collectors[COLLECTOR_IDS[0]]["workload_bounds"]),
            {
                "max_bytes": 4_194_304,
                "max_requests": 3,
                "max_rows": 600,
                "max_seconds": 135,
            },
        )
        self.assertEqual(
            dict(collectors[COLLECTOR_IDS[1]]["workload_bounds"]),
            {
                "max_bytes": 16_777_216,
                "max_requests": 1,
                "max_rows": 30_000,
                "max_seconds": 45,
            },
        )

        datasets = tuple(
            next(item for item in registry.datasets if item.id == dataset_id)
            for dataset_id in DATASET_IDS
        )
        self.assertEqual(tuple(item.store for item in datasets), ("market",) * 4)
        for declaration in datasets:
            self.assertFalse(declaration.tool_ids)
            self.assertFalse(declaration.dashboard_ids)
            self.assertFalse(declaration.export_ids)
        self.assertEqual(
            {relation for declaration in datasets for relation in declaration.relations},
            {
                "stage10_instruments",
                "stage10_universe_captures",
                "stage10_scope_snapshots",
                "stage10_universes",
                "stage10_universe_snapshots",
                "stage10_universe_snapshot_members",
                "stage10_daily_price_captures",
                "stage10_daily_price_versions",
                "stage10_daily_prices",
            },
        )
        universe = next(item for item in datasets if item.id == "market.stage10.universes")
        self.assertIn("russell_excluded", universe.quality_contract["rules"])
        prices = next(item for item in datasets if item.id == "market.stage10.daily_prices")
        self.assertIn(
            "no_membership_history_inference",
            prices.quality_contract["rules"],
        )

        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal), ("market", 10))
        self.assertEqual(migration.sha256, MIGRATION_SHA256)
        self.assertEqual(registry.store("market").migration_order[-1], MIGRATION_ID)
        self.assertEqual(tuple(item.id for item in registry.exports), ("atlas.fixture_snapshot",))
        self.assertEqual(len(registry.jobs), 8)

    def test_stage9_projection_is_exact_and_source_pinned(self) -> None:
        current = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        historical = stage9_registry_profile(current)
        self.assertEqual(
            (historical.schema_version, historical.registry_version),
            ("1.5.0", "2.7.0"),
        )
        self.assertEqual(
            (
                len(historical.migrations),
                len(historical.datasets),
                len(historical.collectors),
            ),
            (31, 36, 20),
        )
        self.assertNotIn(MIGRATION_ID, {item.id for item in historical.migrations})
        self.assertTrue(
            set(DATASET_IDS).isdisjoint(item.id for item in historical.datasets)
        )
        self.assertTrue(
            set(COLLECTOR_IDS).isdisjoint(
                str(item["id"]) for item in historical.collectors
            )
        )
        self.assertEqual(historical.source_sha256, STAGE9_SOURCE_SHA256)
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STAGE9_SOURCE_SHA256)
        self.assertEqual(
            historical.store("market").migration_order[-1],
            "market:0009_fmp_daily_price_backfill",
        )

    def test_hostile_stage10_drift_fails_closed(self) -> None:
        def collector(raw, collector_id=COLLECTOR_IDS[1]):
            return next(
                item for item in raw["collectors"] if item["id"] == collector_id
            )

        mutations = (
            lambda raw: collector(raw).__setitem__("network", False),
            lambda raw: collector(raw).__setitem__("handler", "market.other"),
            lambda raw: collector(raw).__setitem__(
                "configuration_env", ["FMP_API_KEY", "OTHER_KEY"]
            ),
            lambda raw: collector(raw)["workload_bounds"].__setitem__(
                "max_requests", 2
            ),
            lambda raw: collector(raw)["retry_policy"].__setitem__("max_attempts", 2),
            lambda raw: collector(raw)["output_datasets"].pop(),
            lambda raw: next(
                item for item in raw["migrations"] if item["id"] == MIGRATION_ID
            ).__setitem__("sha256", "0" * 64),
            lambda raw: next(
                item for item in raw["datasets"] if item["id"] == DATASET_IDS[3]
            )["physical"].__setitem__("relations", ["prices_daily"]),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._assert_rejected(mutate)


if __name__ == "__main__":
    unittest.main()
