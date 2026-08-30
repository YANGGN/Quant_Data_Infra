from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    current_news_registry_profile,
    load_registry,
    technical_indicators_v27_registry_profile,
)
from quant_data.tool_platform.generate import generated_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_REGISTRY_SHA256 = (
    "06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72"
)
CURRENT_CATALOG_SHA256 = (
    "05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d"
)
PREDECESSOR_REGISTRY_SHA256 = (
    "59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2"
)
PREDECESSOR_CATALOG_SHA256 = (
    "18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1"
)
REGISTRY_261_SHA256 = (
    "0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea"
)


def _render(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


class CurrentNewsVersioningTests(unittest.TestCase):
    def test_current_registry_projects_to_exact_predecessors(self) -> None:
        registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(registry.registry_version, "2.63.0")
        self.assertEqual(registry.source_sha256, CURRENT_REGISTRY_SHA256)
        self.assertEqual(
            registry.raw["tool_version_schema_catalog"]["sha256"],
            CURRENT_CATALOG_SHA256,
        )

        predecessor = current_news_registry_profile(registry)
        self.assertEqual(predecessor.registry_version, "2.62.0")
        self.assertEqual(predecessor.source_sha256, PREDECESSOR_REGISTRY_SHA256)
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"]["sha256"],
            PREDECESSOR_CATALOG_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(predecessor.raw)).hexdigest(),
            PREDECESSOR_REGISTRY_SHA256,
        )

        registry_261 = technical_indicators_v27_registry_profile(registry)
        self.assertEqual(registry_261.registry_version, "2.61.0")
        self.assertEqual(registry_261.source_sha256, REGISTRY_261_SHA256)

    def test_exact_predecessor_regenerates_current_bytes(self) -> None:
        current_registry_bytes = (
            PROJECT_ROOT / "config" / "system_registry.json"
        ).read_bytes()
        current_catalog_bytes = (
            PROJECT_ROOT
            / "quant_data"
            / "generated"
            / "tool_contract_schemas_v2.json"
        ).read_bytes()
        registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        predecessor = current_news_registry_profile(registry)
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config = temporary_root / "config"
            config.mkdir()
            (config / "system_registry.json").write_bytes(
                _render(predecessor.raw)
            )
            generated_registry, _, generated_catalog = generated_bytes(
                temporary_root
            )
        self.assertEqual(generated_registry, current_registry_bytes)
        self.assertEqual(generated_catalog, current_catalog_bytes)


if __name__ == "__main__":
    unittest.main()
