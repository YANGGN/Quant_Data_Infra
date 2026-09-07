from __future__ import annotations

import hashlib
import copy
import json
import unittest
import tempfile
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage11_retry import (
    STAGE11_MAX_REQUEST_ATTEMPTS,
    STAGE11_RETRY_BACKOFF_SECONDS,
)
from quant_data.registry import (
    load_registry,
    stage10_registry_profile,
    stage11_registry_profile,
    stage12_registry_profile,
    stage12b_registry_profile,
    stage12c_registry_profile,
    stage12d_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
MIGRATION_ID = "macro:0012_stage11_bea_eia_live_history"
MIGRATION_SHA256 = "4f29eed2d73fcb1aa6f3c519a3360c132658cf152341261a18d4332e140fd0a5"
STAGE10_SOURCE_SHA256 = "c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9"
STAGE11_SOURCE_SHA256 = "7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56"
STAGE12_SOURCE_SHA256 = "f65f7d039b73037012c6183c34501027f4298376ab94125e856e9dd79c6d234d"
STAGE12A_SOURCE_SHA256 = "80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e"
STAGE12B_COLLECTOR_ID = "market.stage12b.fmp_daily_incremental_fixture"
STAGE11_COLLECTOR_IDS = (
    "bea.macro.stage11_nipa_history",
    "eia.macro.stage11_electricity_retail_history",
    "eia.macro.stage11_petroleum_weekly_stock_history",
)
CANONICAL_RETRY = {
    "backoff": "deterministic_1s_then_2s_no_jitter",
    "honor_retry_after": False,
    "max_attempts": 3,
    "transient_classes": ["connection", "timeout", "http_429", "http_5xx"],
}
HISTORICAL_RETRY = {
    "backoff": "none_operator_resume",
    "honor_retry_after": False,
    "max_attempts": 1,
    "transient_classes": ["http_429", "http_500", "transport"],
}


class Stage11RegistryTests(unittest.TestCase):
    def test_canonical_stage11_and_frozen_stage10_projection(self) -> None:
        registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        self.assertEqual((registry.schema_version, registry.registry_version), ("1.9.0", "2.74.0"))
        self.assertEqual((len(registry.migrations), len(registry.datasets), len(registry.collectors)), (48, 64, 70))
        self.assertEqual(registry.store("market").default_path, "data/market.sqlite")
        self.assertEqual(registry.store("macro").default_path, "data/macro.sqlite")
        self.assertEqual(registry.store("company").default_path, "data/company.sqlite")
        self.assertEqual(registry.store("news").default_path, "data/news.sqlite")
        stage12d = stage12d_registry_profile(registry)
        self.assertEqual(
            (stage12d.schema_version, stage12d.registry_version),
            ("1.8.0", "2.14.0"),
        )
        self.assertEqual(stage12d.source_sha256, "c24398b35bc9fe9553dd85346dd3879abd6b802e1dd255ffb186d4ed9e1ea769")
        self.assertEqual(stage12d.store("macro").default_path, "data/macro_data.sqlite")
        self.assertEqual(stage12d.store("company").default_path, "data/company_data.sqlite")
        self.assertEqual(stage12d.store("news").default_path, "data/news_data.sqlite")
        stage12d_payload = (
            json.dumps(stage12d.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(stage12d_payload).hexdigest(), stage12d.source_sha256)
        pre_stage12 = stage12_registry_profile(registry)
        self.assertEqual(
            (pre_stage12.schema_version, pre_stage12.registry_version),
            ("1.8.0", "2.11.0"),
        )
        self.assertEqual(
            pre_stage12.store("market").default_path, "data/market_data.sqlite"
        )
        self.assertEqual(
            next(item for item in pre_stage12.raw["stores"] if item["id"] == "market")["default_path"],
            "data/market_data.sqlite",
        )
        self.assertEqual(pre_stage12.source_sha256, STAGE12_SOURCE_SHA256)
        pre_stage12_payload = (
            json.dumps(
                pre_stage12.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(pre_stage12_payload).hexdigest(),
            STAGE12_SOURCE_SHA256,
        )
        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal, migration.sha256), ("macro", 12, MIGRATION_SHA256))
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertIn(MIGRATION_ID, registry.store("macro").migration_order)
        canonical_collectors = {
            str(item["id"]): item for item in registry.collectors
            if item["id"] in STAGE11_COLLECTOR_IDS
        }
        self.assertEqual(tuple(canonical_collectors), STAGE11_COLLECTOR_IDS)
        self.assertEqual(STAGE11_MAX_REQUEST_ATTEMPTS, 3)
        self.assertEqual(STAGE11_RETRY_BACKOFF_SECONDS, (1.0, 2.0))
        for collector in canonical_collectors.values():
            self.assertEqual(dict(collector["retry_policy"]), CANONICAL_RETRY)

        stage11 = stage11_registry_profile(registry)
        self.assertEqual((stage11.schema_version, stage11.registry_version), ("1.7.0", "2.9.0"))
        self.assertEqual(stage11.store("macro").migration_order[-1], MIGRATION_ID)
        self.assertEqual(stage11.source_sha256, STAGE11_SOURCE_SHA256)
        self.assertEqual(stage11.raw["registry_version"], "2.9.0")
        payload = (
            json.dumps(stage11.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STAGE11_SOURCE_SHA256)
        historical_collectors = {
            str(item["id"]): item for item in stage11.collectors
            if item["id"] in STAGE11_COLLECTOR_IDS
        }
        for collector in historical_collectors.values():
            self.assertEqual(dict(collector["retry_policy"]), HISTORICAL_RETRY)

        stage10 = stage10_registry_profile(registry)
        self.assertEqual((stage10.schema_version, stage10.registry_version), ("1.6.0", "2.8.0"))
        self.assertEqual((len(stage10.migrations), len(stage10.datasets), len(stage10.collectors)), (32, 40, 22))
        self.assertEqual(stage10.source_sha256, STAGE10_SOURCE_SHA256)
        self.assertNotIn(MIGRATION_ID, {item.id for item in stage10.migrations})

    def test_stage12b_fixture_projection_is_exact_and_idempotent(self) -> None:
        registry = stage12c_registry_profile(
            load_registry(
                REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        collector = next(
            item for item in registry.collectors if item["id"] == STAGE12B_COLLECTOR_ID
        )
        self.assertEqual(
            dict(collector),
            {
                "configuration_env": [],
                "handler": "market.stage12b_fmp_daily_incremental_fixture",
                "id": STAGE12B_COLLECTOR_ID,
                "input_datasets": [
                    "market.stage10.instruments",
                    "market.stage10.daily_prices",
                ],
                "mutation_policy": {
                    "mode": "append_versions_and_move_current_projection",
                    "unchanged": "zero_persistent_writes",
                },
                "network": False,
                "output_datasets": [
                    "market.stage10.source_evidence",
                    "market.stage10.daily_prices",
                ],
                "physical_locks": "derived_from_output_store_paths",
                "retry_policy": {
                    "backoff": "none",
                    "honor_retry_after": False,
                    "max_attempts": 1,
                    "transient_classes": [],
                },
                "schedule_eligibility": {"mode": "manual_only"},
                "semantic_identity": {
                    "excludes": ["captured_at", "source_row_order"],
                    "includes": [
                        "request_scope",
                        "normalization_version",
                        "normalized_complete_batch",
                    ],
                },
                "version": "1.0.0",
                "workload_bounds": {
                    "max_bytes": 65_536,
                    "max_requests": 1,
                    "max_rows": 5,
                    "max_seconds": 30,
                },
            },
        )
        datasets = {item.id: item for item in registry.datasets}
        self.assertEqual(
            datasets["market.stage10.source_evidence"].collector_ids,
            (
                "fmp.market.stage10_universe_capture",
                "fmp.market.stage10_daily_history",
                STAGE12B_COLLECTOR_ID,
            ),
        )
        self.assertEqual(
            datasets["market.stage10.daily_prices"].collector_ids,
            ("fmp.market.stage10_daily_history", STAGE12B_COLLECTOR_ID),
        )

        stage12a = stage12b_registry_profile(registry)
        self.assertEqual(
            (stage12a.schema_version, stage12a.registry_version),
            ("1.8.0", "2.12.0"),
        )
        self.assertEqual(stage12a.source_sha256, STAGE12A_SOURCE_SHA256)
        self.assertEqual(stage12a.store("market").default_path, "data/market.sqlite")
        payload = (
            json.dumps(stage12a.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STAGE12A_SOURCE_SHA256)
        self.assertNotIn(
            STAGE12B_COLLECTOR_ID,
            {str(item["id"]) for item in stage12a.collectors},
        )
        self.assertTrue(
            all(
                STAGE12B_COLLECTOR_ID not in item.collector_ids
                for item in stage12a.datasets
            )
        )
        self.assertIs(stage12b_registry_profile(stage12a), stage12a)

    def test_stage12b_fixture_declaration_rejects_adversarial_drift(self) -> None:
        source = loads_strict(REGISTRY_PATH.read_bytes(), max_bytes=16 * 1024 * 1024)
        self.assertIsInstance(source, dict)

        def collector(raw):
            return next(
                item
                for item in raw["collectors"]
                if item["id"] == STAGE12B_COLLECTOR_ID
            )

        def dataset(raw, dataset_id):
            return next(item for item in raw["datasets"] if item["id"] == dataset_id)

        cases = (
            lambda raw: collector(raw).__setitem__("handler", "market.other"),
            lambda raw: collector(raw).__setitem__("network", True),
            lambda raw: collector(raw).__setitem__("configuration_env", ["FMP_API_KEY"]),
            lambda raw: collector(raw).__setitem__("schedule_eligibility", {"mode": "scheduled"}),
            lambda raw: collector(raw).__setitem__("physical_locks", "job_name"),
            lambda raw: collector(raw)["workload_bounds"].__setitem__("max_requests", 2),
            lambda raw: collector(raw)["workload_bounds"].__setitem__("max_rows", 6),
            lambda raw: collector(raw)["retry_policy"].__setitem__("max_attempts", 2),
            lambda raw: collector(raw)["output_datasets"].pop(),
            lambda raw: dataset(raw, "market.stage10.source_evidence")["collector_ids"].pop(),
            lambda raw: dataset(raw, "market.stage10.daily_prices")["collector_ids"].pop(),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(dir="/tmp") as directory:
                raw = copy.deepcopy(source)
                mutation(raw)
                path = Path(directory) / "registry.json"
                path.write_text(dumps_strict(raw), encoding="utf-8")
                with self.assertRaises(RegistryError):
                    load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_canonical_stage11_retry_policy_is_closed(self) -> None:
        source = loads_strict(REGISTRY_PATH.read_bytes(), max_bytes=16 * 1024 * 1024)
        self.assertIsInstance(source, dict)
        cases = (
            ("classes", ["connection", "http_429", "http_5xx"]),
            ("attempts", 2),
            ("backoff", "none_operator_resume"),
            ("retry_after", True),
        )
        for name, replacement in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir="/tmp") as directory:
                raw = copy.deepcopy(source)
                collector = next(
                    item for item in raw["collectors"]
                    if item["id"] == STAGE11_COLLECTOR_IDS[0]
                )
                if name == "classes":
                    collector["retry_policy"]["transient_classes"] = replacement
                elif name == "attempts":
                    collector["retry_policy"]["max_attempts"] = replacement
                elif name == "backoff":
                    collector["retry_policy"]["backoff"] = replacement
                else:
                    collector["retry_policy"]["honor_retry_after"] = replacement
                path = Path(directory) / "registry.json"
                path.write_text(dumps_strict(raw), encoding="utf-8")
                with self.assertRaises(RegistryError):
                    load_registry(path, project_root=PROJECT_ROOT, environment={})


if __name__ == "__main__":
    unittest.main()
