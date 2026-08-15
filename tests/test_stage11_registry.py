from __future__ import annotations

import hashlib
import copy
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
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
MIGRATION_ID = "macro:0012_stage11_bea_eia_live_history"
MIGRATION_SHA256 = "4f29eed2d73fcb1aa6f3c519a3360c132658cf152341261a18d4332e140fd0a5"
STAGE10_SOURCE_SHA256 = "c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9"
STAGE11_SOURCE_SHA256 = "7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56"
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
        self.assertEqual((registry.schema_version, registry.registry_version), ("1.7.0", "2.10.0"))
        self.assertEqual((len(registry.migrations), len(registry.datasets), len(registry.collectors)), (33, 46, 25))
        migration = next(item for item in registry.migrations if item.id == MIGRATION_ID)
        self.assertEqual((migration.store, migration.ordinal, migration.sha256), ("macro", 12, MIGRATION_SHA256))
        self.assertEqual(
            hashlib.sha256((PROJECT_ROOT / migration.resource).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(registry.store("macro").migration_order[-1], MIGRATION_ID)
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
        self.assertEqual(stage11.source_sha256, STAGE11_SOURCE_SHA256)
        self.assertEqual(stage11.raw["registry_version"], "2.9.0")
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

    def test_canonical_stage11_retry_policy_is_closed(self) -> None:
        source = loads_strict(REGISTRY_PATH.read_bytes())
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
