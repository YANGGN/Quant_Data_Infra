from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    current_news_registry_profile,
    data_status_options_registry_profile,
    macro_database_expansion_registry_profile,
    load_registry,
    multi_source_current_news_registry_profile,
    news_research_registry_profile,
    option_raw_evidence_registry_profile,
    technical_indicators_v27_registry_profile,
)
from quant_data.tool_platform.generate import generated_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_REGISTRY_SHA256 = (
    "2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f"
)
CURRENT_CATALOG_SHA256 = (
    "fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28"
)
REGISTRY_267_SHA256 = (
    "a80b0e06db95968c9fd49cd3d90054b709c57895993a28b512ba2550e162f325"
)
REGISTRY_266_SHA256 = (
    "f7b8c402ce4abce5d024f7fcdc8debde97e25324739f037ef209312bb4d070f3"
)
CATALOG_225_SHA256 = (
    "e35b136e3e47a6211a85d62c75baf6ecd52b9246938a30549ace5c19e0c39700"
)
REGISTRY_265_SHA256 = (
    "c22d9ada8be3c3c7f9538c902bac3ef3467b9fdfa43c23fd7aa1c88200d58614"
)
CATALOG_224_SHA256 = (
    "6a4f7e8ce223658617512928b860f5cf5bde85e01f075070771fa019e882ed46"
)
REGISTRY_263_SHA256 = (
    "06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72"
)
REGISTRY_264_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
CATALOG_223_SHA256 = (
    "05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d"
)
REGISTRY_262_SHA256 = (
    "59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2"
)
CATALOG_222_SHA256 = (
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
        self.assertEqual(registry.registry_version, "2.69.0")
        self.assertEqual(registry.source_sha256, CURRENT_REGISTRY_SHA256)
        self.assertEqual(
            registry.raw["tool_version_schema_catalog"]["sha256"],
            CURRENT_CATALOG_SHA256,
        )

        registry_266 = data_status_options_registry_profile(registry)
        self.assertEqual(registry_266.registry_version, "2.66.0")
        self.assertEqual(registry_266.source_sha256, REGISTRY_266_SHA256)
        self.assertEqual(
            registry_266.raw["tool_version_schema_catalog"]["sha256"],
            CATALOG_225_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(registry_266.raw)).hexdigest(),
            REGISTRY_266_SHA256,
        )

        registry_265 = news_research_registry_profile(registry)
        self.assertEqual(registry_265.registry_version, "2.65.0")
        self.assertEqual(registry_265.source_sha256, REGISTRY_265_SHA256)
        self.assertEqual(
            registry_265.raw["tool_version_schema_catalog"]["sha256"],
            CATALOG_224_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(registry_265.raw)).hexdigest(),
            REGISTRY_265_SHA256,
        )

        registry_264 = option_raw_evidence_registry_profile(registry)
        self.assertEqual(registry_264.registry_version, "2.64.0")
        self.assertEqual(registry_264.source_sha256, REGISTRY_264_SHA256)
        self.assertEqual(
            registry_264.raw["tool_version_schema_catalog"]["sha256"],
            CATALOG_224_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(registry_264.raw)).hexdigest(),
            REGISTRY_264_SHA256,
        )
        self.assertIs(option_raw_evidence_registry_profile(registry_264), registry_264)

        registry_263 = multi_source_current_news_registry_profile(registry)
        self.assertEqual(registry_263.registry_version, "2.63.0")
        self.assertEqual(registry_263.source_sha256, REGISTRY_263_SHA256)
        self.assertEqual(
            registry_263.raw["tool_version_schema_catalog"]["sha256"],
            CATALOG_223_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(registry_263.raw)).hexdigest(),
            REGISTRY_263_SHA256,
        )

        registry_262 = current_news_registry_profile(registry)
        self.assertEqual(registry_262.registry_version, "2.62.0")
        self.assertEqual(registry_262.source_sha256, REGISTRY_262_SHA256)
        self.assertEqual(
            registry_262.raw["tool_version_schema_catalog"]["sha256"],
            CATALOG_222_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(_render(registry_262.raw)).hexdigest(),
            REGISTRY_262_SHA256,
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
        predecessor = macro_database_expansion_registry_profile(registry)
        self.assertEqual(
            (predecessor.registry_version, predecessor.source_sha256),
            ("2.67.0", REGISTRY_267_SHA256),
        )
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
