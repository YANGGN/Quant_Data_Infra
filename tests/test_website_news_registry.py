from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry import load_registry, website_source_registry_profile, analyst_history_registry_profile
from quant_data.tool_platform.generate import generated_bytes

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_SHA256 = "55285a106a56a3d664f83dd75cb71c43aa21f5e9637d0200704933a291732a78"


class WebsiteNewsRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(ROOT / "config/system_registry.json",
                                      project_root=ROOT, environment={})

    def test_projection_preserves_exact_fmp_research_predecessor(self):
        prior = website_source_registry_profile(self.registry)
        self.assertEqual(prior.registry_version, "2.71.0")
        payload = (json.dumps(prior.raw, ensure_ascii=True, indent=2, sort_keys=True)+"\n").encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PREDECESSOR_SHA256)
        self.assertEqual(prior.source_sha256, PREDECESSOR_SHA256)

    def test_predecessor_regenerates_exact_successor_without_other_changes(self):
        prior = website_source_registry_profile(self.registry)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root/"config").mkdir()
            (root/"config/system_registry.json").write_text(
                json.dumps(prior.raw, ensure_ascii=True, indent=2, sort_keys=True)+"\n")
            registry, _, catalog = generated_bytes(root)
        expected = analyst_history_registry_profile(self.registry)
        self.assertEqual(registry, (json.dumps(expected.raw, ensure_ascii=True, indent=1, sort_keys=True)+"\n").encode())
        self.assertEqual(catalog, (ROOT/"quant_data/generated/tool_contract_schemas_v2.json").read_bytes())

    def test_projection_rejects_parsed_migration_and_source_binding_tampering(self):
        changed = replace(self.registry, migrations=tuple(
            replace(m, sha256="0"*64) if m.id=="news:0009_website_source_extension" else m
            for m in self.registry.migrations))
        with self.assertRaises(RegistryError):
            website_source_registry_profile(changed)
        changed = replace(self.registry, collectors=tuple(
            {**c, "handler":"wrong.handler"} if c["id"]=="news.current_multi_source" else c
            for c in self.registry.collectors))
        with self.assertRaises(RegistryError):
            website_source_registry_profile(changed)
