"""Offline cache and import-boundary regressions for the system registry."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from quant_data import registry
from quant_data.errors import RegistryError


ROOT = Path(__file__).resolve().parents[1]


class SchemaCatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="registry-catalog-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.resource = self.root / "catalog.json"
        with registry._SCHEMA_CATALOG_CACHE_LOCK:
            registry._SCHEMA_CATALOG_CACHE.clear()
        self.addCleanup(registry._SCHEMA_CATALOG_CACHE.clear)
        self.catalog = {
            "schema_id": "fixture.catalog",
            "schema_version": "1.0.0",
            "dialect": registry.SCHEMA_DIALECT,
            "contracts": [{
                "id": "fixture.contract", "tool": "fixture.tool", "direction": "input",
                "schema": {
                    "$id": "fixture.contract", "$schema": registry.SCHEMA_DIALECT,
                    "type": "object", "additionalProperties": False,
                    "required": ["value"],
                    "properties": {"value": {"type": "string", "enum": ["original"]}},
                },
            }],
        }
        self.raw = {"tool_schema_catalog": {
            "schema_id": "fixture.catalog", "schema_version": "1.0.0",
            "resource": "catalog.json", "sha256": "",
        }}
        self.write_catalog()

    def write_catalog(self):
        payload = json.dumps(self.catalog, sort_keys=True).encode()
        self.resource.write_bytes(payload)
        self.raw["tool_schema_catalog"]["sha256"] = hashlib.sha256(payload).hexdigest()

    def load(self, **overrides):
        kwargs = {
            "expected_id": "fixture.catalog", "expected_version": "1.0.0",
            "expected_count": 1, "allowed_tool_names": ("fixture.tool",),
        }
        kwargs.update(overrides)
        return registry._load_tool_schema_catalog(self.raw, self.root, **kwargs)

    def test_warm_reuse_skips_schema_validation_but_detaches_nested_values(self):
        with patch.object(registry, "_validate_strict_schema", wraps=registry._validate_strict_schema) as validate:
            first = self.load()
            calls = validate.call_count
            self.assertGreater(calls, 0)
            expected = copy.deepcopy(first)
            first["fixture.contract"]["required"].clear()
            first["fixture.contract"]["properties"]["value"]["enum"].append("mutation")
            second = self.load()
            self.assertEqual(second, expected)
            self.assertEqual(validate.call_count, calls)
            self.assertIsNot(first, second)

    def test_warm_cache_rechecks_bytes_even_when_size_and_mtime_match(self):
        self.load()
        before = self.resource.stat()
        self.resource.write_bytes(self.resource.read_bytes().replace(b"original", b"modified"))
        os.utime(self.resource, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.resource.stat().st_size, before.st_size)
        with self.assertRaisesRegex(RegistryError, "checksum mismatch"):
            self.load()

    def test_new_valid_bytes_get_new_schema_and_invalid_bytes_fail_with_updated_hash(self):
        self.load()
        schema = self.catalog["contracts"][0]["schema"]
        schema["properties"]["value"]["enum"] = ["changed"]
        self.write_catalog()
        self.assertEqual(self.load()["fixture.contract"]["properties"]["value"]["enum"], ["changed"])
        schema["properties"]["value"]["type"] = "unsupported"
        self.write_catalog()
        for _ in range(2):
            with self.assertRaisesRegex(RegistryError, "Unsupported or missing schema type"):
                self.load()
        self.assertEqual(len(registry._SCHEMA_CATALOG_CACHE), 2)

    def test_validation_expectations_are_part_of_cache_identity(self):
        self.load()
        with self.assertRaisesRegex(RegistryError, "identity or size drifted"):
            self.load(expected_count=2)
        with self.assertRaisesRegex(RegistryError, "metadata is invalid"):
            self.load(allowed_tool_names=("another.tool",))
        with self.assertRaisesRegex(RegistryError, "catalog metadata is invalid"):
            self.load(expected_id="another.catalog")
        with self.assertRaisesRegex(RegistryError, "catalog metadata is invalid"):
            self.load(expected_version="2.0.0")

    def test_warm_cache_rechecks_containment_and_file_existence(self):
        self.load()
        with tempfile.TemporaryDirectory(prefix="registry-outside-test-") as other:
            outside = Path(other) / "catalog.json"
            outside.write_bytes(self.resource.read_bytes())
            self.resource.unlink()
            self.resource.symlink_to(outside)
            with self.assertRaisesRegex(RegistryError, "inside the project"):
                self.load()
        self.resource.unlink()
        with self.assertRaises(FileNotFoundError):
            self.load()

    def test_warm_cache_rejects_traversal_and_invalid_declaration(self):
        self.load()
        self.raw["tool_schema_catalog"]["resource"] = "../catalog.json"
        with self.assertRaisesRegex(RegistryError, "without traversal"):
            self.load()
        self.raw["tool_schema_catalog"]["resource"] = "catalog.json"
        self.raw["tool_schema_catalog"]["extra"] = True
        with self.assertRaisesRegex(RegistryError, "Unknown fields"):
            self.load()

    def test_cache_is_bounded_and_evicts_least_recently_used_bundle(self):
        for i in range(registry._SCHEMA_CATALOG_CACHE_LIMIT + 1):
            self.catalog["contracts"][0]["schema"]["properties"]["value"]["enum"] = [str(i)]
            self.write_catalog()
            self.load()
        self.assertEqual(len(registry._SCHEMA_CATALOG_CACHE), registry._SCHEMA_CATALOG_CACHE_LIMIT)
        self.catalog["contracts"][0]["schema"]["properties"]["value"]["enum"] = ["0"]
        self.write_catalog()
        with patch.object(registry, "_validate_strict_schema", wraps=registry._validate_strict_schema) as validate:
            self.load()
            self.assertGreater(validate.call_count, 0)

    def test_concurrent_readers_get_independent_contracts(self):
        # Threads read only this explicit temporary resource; no store or lock paths.
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.load(), range(8)))
        for item in results:
            self.assertEqual(item, results[0])
        results[0]["fixture.contract"]["required"].clear()
        for item in results[1:]:
            self.assertEqual(item["fixture.contract"]["required"], ["value"])
        self.assertEqual(self.load()["fixture.contract"]["required"], ["value"])


class RegistryCompatibilityImportTests(unittest.TestCase):
    def test_current_load_does_not_import_historical_projections(self):
        script = """
import sys
from pathlib import Path
from quant_data.registry import load_registry
assert 'quant_data.registry_compat' not in sys.modules
load_registry('config/system_registry.json', project_root=Path.cwd())
assert 'quant_data.registry_compat' not in sys.modules
from quant_data.registry import stage2_registry_profile
from quant_data.registry_compat import stage2_registry_profile as direct
assert stage2_registry_profile is direct
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True,
            text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_exports_and_discovery_resolve_to_historical_module(self):
        from quant_data import registry_compat

        for name in registry._COMPATIBILITY_EXPORTS:
            with self.subTest(name=name):
                self.assertIn(name, dir(registry))
                self.assertIs(getattr(registry, name), getattr(registry_compat, name))
                if not name.startswith("_"):
                    self.assertIn(name, registry.__all__)
        with self.assertRaises(AttributeError):
            getattr(registry, "unknown_registry_profile")


if __name__ == "__main__":
    unittest.main()
