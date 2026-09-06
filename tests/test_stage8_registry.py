from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import ExportDeclaration, load_registry, stage8_registry_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
EXPORT_ID = "atlas.fixture_snapshot"
EXPORT_KEYS = frozenset(
    {
        "id",
        "version",
        "lifecycle",
        "owner",
        "description",
        "semantic_dataset_id",
        "kind",
        "format",
        "datasets",
        "query_contract",
        "schema_contract",
        "chunking",
        "freshness",
        "source_mode",
        "consistency",
        "staging",
        "optional_dependency",
        "benchmark",
        "consumers",
        "provenance",
    }
)
PROJECTIONS = (
    (
        "market-prices",
        "market.daily_prices",
        "market",
        "fixture.market.daily_prices",
        ("prices_daily", "prices_daily_versions"),
        5000,
    ),
    (
        "gdp-vintages",
        "macro.gdp_vintages",
        "macro",
        "fixture.macro.gdp_vintages",
        ("gdp_vintages",),
        1000,
    ),
    (
        "company-issuers",
        "company.issuers",
        "company",
        "fixture.company.issuers",
        ("company_issuers", "company_issuer_versions"),
        1000,
    ),
    (
        "news-items",
        "news.items",
        "news",
        "fixture.news.items",
        ("news_items", "news_item_versions"),
        5000,
    ),
)
FORBIDDEN_PUBLIC_FIELDS = frozenset(
    {
        "body",
        "summary",
        "source_url",
        "run_id",
        "artifact_id",
        "snapshot_id",
        "database_path",
        "filesystem_path",
        "credential",
        "sql",
    }
)


class Stage8RegistryTests(unittest.TestCase):
    def _load_mutation(self, mutate) -> None:
        raw = loads_strict(REGISTRY_PATH.read_bytes(), max_bytes=16 * 1024 * 1024)
        mutate(raw)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "registry.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_canonical_atlas_export_is_exact_bounded_and_immutable(self) -> None:
        source_before = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        registry = stage8_registry_profile(
            load_registry(
                REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        self.assertEqual(registry.schema_version, "1.4.0")
        self.assertEqual(registry.registry_version, "2.6.0")
        self.assertEqual(tuple(item.id for item in registry.exports), (EXPORT_ID,))
        declaration = registry.export(EXPORT_ID)
        self.assertIsInstance(declaration, ExportDeclaration)
        self.assertEqual(declaration.version, "1.0.0")
        self.assertEqual(
            declaration.datasets,
            (
                "fixture.market.daily_prices",
                "fixture.macro.gdp_vintages",
                "fixture.company.issuers",
                "fixture.news.items",
            ),
        )
        self.assertEqual(
            declaration.source_stores,
            ("market", "macro", "company", "news"),
        )
        with self.assertRaises(RegistryError):
            registry.export("atlas.unregistered")

        raw = registry.raw["exports"][0]
        self.assertEqual(frozenset(raw), EXPORT_KEYS)
        self.assertEqual(
            raw["lifecycle"],
            {
                "mode": "manual_only",
                "fixture_only": True,
                "network": False,
                "hosting": False,
                "status": "fixture_validated",
            },
        )
        self.assertEqual(raw["kind"], "atlas_snapshot")
        self.assertEqual(raw["format"], "json")
        self.assertEqual(raw["source_mode"], "online_backup")
        self.assertIsNone(raw["optional_dependency"])
        self.assertEqual(
            raw["consistency"],
            {
                "complete_receipts": "required",
                "receipt_chain": "complete_baseline_plus_validated_deltas",
                "partial_scope": "forbidden",
                "cross_store_atomic": False,
                "source_copies": "online_backup_per_store",
                "read_access": "query_only",
            },
        )
        self.assertEqual(
            raw["staging"],
            {
                "root": "host_selected",
                "child": "unique_generated",
                "revision": "immutable",
                "promotion": "atomic_pointer",
                "cleanup": "exact_child_only",
                "validation": "complete_before_promote",
            },
        )
        self.assertEqual(
            raw["benchmark"],
            {
                "decision": "not_required_json_atlas_snapshot",
                "parquet": "not_adopted",
                "duckdb": "not_adopted",
            },
        )
        self.assertEqual(
            raw["consumers"],
            [
                {
                    "id": "quant_data_atlas",
                    "mode": "static_read_only",
                    "hosting": False,
                }
            ],
        )

        query = raw["query_contract"]
        self.assertEqual(
            query["cutoff"],
            {
                "source": "export_start",
                "precision": "datetime",
                "timezone": "aware_utc",
                "availability": "at_or_before",
                "date_only_policy": "completed_date",
                "vintage_mode": "as_of",
            },
        )
        self.assertEqual(
            query["bounds"],
            {
                "max_total_rows": 12000,
                "max_bytes": 8 * 1024 * 1024,
                "max_runtime_seconds": 30,
            },
        )
        self.assertEqual(
            declaration.chunking,
            {
                "version": "1.0.0",
                "strategy": "ordered_rows",
                "max_rows": 250,
                "max_bytes": 262144,
                "empty_policy": "omit",
            },
        )

        schemas = {
            schema["id"]: schema for schema in raw["schema_contract"]["schemas"]
        }
        for projection, expected in zip(query["projections"], PROJECTIONS):
            with self.subTest(projection=expected[0]):
                projection_id, operation_id, store, dataset, relations, max_rows = expected
                self.assertEqual(projection["id"], projection_id)
                self.assertEqual(projection["operation_id"], operation_id)
                self.assertEqual(projection["store"], store)
                self.assertEqual(projection["dataset"], dataset)
                self.assertEqual(tuple(projection["relations"]), relations)
                self.assertEqual(projection["bounds"], {"max_rows": max_rows})
                self.assertEqual(projection["order_by"], [
                    {"field": item["field"], "direction": "asc"}
                    for item in projection["order_by"]
                ])
                self.assertTrue(set(projection["row_identity"]).issubset(projection["fields"]))
                self.assertFalse(set(projection["fields"]).intersection(FORBIDDEN_PUBLIC_FIELDS))
                schema = schemas[projection["schema_id"]]
                self.assertEqual(
                    projection["fields"],
                    [item["name"] for item in schema["fields"]],
                )
                self.assertEqual(projection["row_identity"], schema["row_identity"])
                self.assertEqual(projection["order_by"], schema["order_by"])
                self.assertEqual(
                    schema["constraints"],
                    {
                        "additional_properties": False,
                        "finite_numbers": "reject",
                        "row_identity_unique": True,
                        "total_order": True,
                    },
                )

        schema = raw["schema_contract"]
        digest_material = {
            "id": schema["id"],
            "version": schema["version"],
            "serialization": schema["serialization"],
            "schemas": schema["schemas"],
        }
        self.assertEqual(
            schema["sha256"],
            hashlib.sha256(dumps_strict(digest_material).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            {
                dataset.id
                for dataset in registry.datasets
                if EXPORT_ID in dataset.export_ids
            },
            set(declaration.datasets),
        )

        with self.assertRaises(TypeError):
            declaration.lifecycle["network"] = True
        with self.assertRaises(TypeError):
            declaration.schema_contract["schemas"][0]["fields"][0]["name"] = "body"
        registry.raw["exports"][0]["lifecycle"]["network"] = True
        self.assertFalse(declaration.lifecycle["network"])
        self.assertEqual(
            source_before,
            hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(),
        )

    def test_hostile_export_declarations_fail_closed(self) -> None:
        cases = (
            lambda raw: raw["exports"][0].__setitem__("unexpected", True),
            lambda raw: raw["exports"][0]["lifecycle"].__setitem__("network", True),
            lambda raw: raw["exports"][0].__setitem__("optional_dependency", "duckdb"),
            lambda raw: raw["exports"][0].__setitem__("source_mode", "read_only_copy"),
            lambda raw: raw["exports"][0]["datasets"].reverse(),
            lambda raw: raw["exports"][0]["query_contract"]["projections"][0][
                "relations"
            ].__setitem__(0, "sqlite_master"),
            lambda raw: raw["exports"][0]["query_contract"]["projections"][3][
                "fields"
            ].append("body"),
            lambda raw: raw["exports"][0]["query_contract"]["projections"][0][
                "order_by"
            ].__setitem__(0, {"field": "close", "direction": "desc"}),
            lambda raw: raw["exports"][0]["schema_contract"].__setitem__(
                "sha256", "0" * 64
            ),
            lambda raw: raw["exports"][0]["staging"].__setitem__("root", "/tmp"),
            lambda raw: raw["exports"][0]["consumers"][0].__setitem__(
                "hosting", True
            ),
            lambda raw: raw["datasets"][2]["export_ids"].clear(),
            lambda raw: raw["datasets"][0]["export_ids"].append(EXPORT_ID),
        )
        source_before = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                self._load_mutation(mutate)
                self.assertEqual(
                    source_before,
                    hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
