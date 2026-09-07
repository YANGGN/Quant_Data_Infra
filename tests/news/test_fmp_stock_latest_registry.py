from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry, stage11_registry_profile, website_source_registry_profile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.news.stock_latest"
MIGRATION_ID = "news:0005_fmp_stock_latest"
DATASET_IDS = (
    "news.fmp.stock_latest_evidence",
    "news.fmp.stock_latest_articles",
)
MIGRATION_SHA256 = "f2565061f5b908255838be875c05112b8bf91033295b87a5b955f9de2d142217"


class FmpStockLatestRegistryTests(unittest.TestCase):
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

    def test_private_one_request_declarations_are_closed(self) -> None:
        registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        collector = next(item for item in registry.collectors if item["id"] == COLLECTOR_ID)
        self.assertTrue(collector["network"])
        self.assertEqual(tuple(collector["configuration_env"]), ("FMP_API_KEY",))
        self.assertEqual(tuple(collector["input_datasets"]), ())
        self.assertEqual(tuple(collector["output_datasets"]), DATASET_IDS)
        self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})
        self.assertEqual(
            dict(collector["workload_bounds"]),
            {
                "max_bytes": 64 * 1024 * 1024,
                "max_requests": 1,
                "max_rows": 1000,
                "max_seconds": 60,
            },
        )
        self.assertEqual(
            dict(collector["retry_policy"]),
            {
                "backoff": "none_single_attempt",
                "honor_retry_after": False,
                "max_attempts": 1,
                "transient_classes": [],
            },
        )
        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual(
            (migration.store, migration.ordinal, migration.sha256, migration.reconstruction_state),
            ("news", 5, MIGRATION_SHA256, "fixture_validated"),
        )
        self.assertEqual(
            registry.store("news").migration_order[-5:],
            (
                "news:0005_fmp_stock_latest",
                "news:0006_fmp_stock_latest_current",
                "news:0007_current_multi_source",
                "news:0008_adopt_fmp_news_legacy",
                "news:0009_website_source_extension",
            ),
        )
        self.assertEqual(
            website_source_registry_profile(registry).store("news").migration_order,
            registry.store("news").migration_order[:-1],
        )
        datasets = {item.id: item for item in registry.datasets if item.id in DATASET_IDS}
        self.assertEqual(tuple(datasets), DATASET_IDS)
        self.assertEqual(
            datasets[DATASET_IDS[0]].relations,
            (
                "fmp_stock_latest_attempts",
                "fmp_stock_latest_outcomes",
                "fmp_stock_latest_captures",
            ),
        )
        self.assertEqual(
            datasets[DATASET_IDS[1]].relations,
            (
                "fmp_stock_latest_articles",
                "fmp_stock_latest_article_versions",
                "fmp_stock_latest_capture_articles",
            ),
        )
        for dataset in datasets.values():
            self.assertEqual(dataset.store, "news")
            self.assertEqual(dataset.tool_ids, ("data.get_dataset_status",))
            self.assertFalse(dataset.dashboard_ids)
            self.assertFalse(dataset.export_ids)

        stage11 = stage11_registry_profile(registry)
        self.assertNotIn(MIGRATION_ID, {item.id for item in stage11.migrations})
        self.assertTrue(set(DATASET_IDS).isdisjoint(item.id for item in stage11.datasets))
        self.assertNotIn(COLLECTOR_ID, {str(item["id"]) for item in stage11.collectors})

    def test_collector_drift_fails_closed(self) -> None:
        def collector(raw):
            return next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)

        mutations = (
            lambda raw: collector(raw)["workload_bounds"].__setitem__("max_requests", 2),
            lambda raw: collector(raw)["workload_bounds"].__setitem__(
                "max_bytes", 64 * 1024 * 1024 - 1
            ),
            lambda raw: collector(raw)["retry_policy"].__setitem__("max_attempts", 2),
            lambda raw: collector(raw)["schedule_eligibility"].__setitem__(
                "mode", "scheduled"
            ),
            lambda raw: collector(raw).__setitem__("network", False),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._assert_rejected(mutate)


if __name__ == "__main__":
    unittest.main()
