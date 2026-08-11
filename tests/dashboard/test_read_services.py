from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.dashboard.read_services import (
    GDP_QUERY_FIELDS,
    TABLE_QUERY_FIELDS,
    TABLE_VIEW_NAMES,
    Stage6DashboardReadService,
)
from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stage4 import run_clean_stage4_rebuild
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Stage6DashboardReadServiceTests(unittest.TestCase):
    """Exercise the dashboard reader only against a rebuilt four-store fixture cohort."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls.root = Path(cls.temporary.name)
        cls.work_root = cls.root / "stage4"
        run_clean_stage4_rebuild(project_root=PROJECT_ROOT, work_root=cls.work_root)
        cls.store_map = explicit_store_map(cls.work_root / "stage3" / "stage2" / "source")
        cls.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        cls.service = Stage6DashboardReadService(cls.store_map, cls.registry)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.before = mutation_fingerprint(self.store_map)

    def tearDown(self) -> None:
        self.assertEqual(dumps_strict(self.before), dumps_strict(mutation_fingerprint(self.store_map)))

    @staticmethod
    def _values(payload: dict[str, object]) -> list[Decimal | None]:
        selected = payload["selected"]
        assert isinstance(selected, dict)
        observations = selected["observations"]
        assert isinstance(observations, list)
        return [item["value"] for item in observations if isinstance(item, dict)]

    def test_gdp_modes_preserve_point_in_time_vintage_history_and_lineage(self) -> None:
        latest = self.service.gdp_vintages({})
        as_of = self.service.gdp_vintages(
            {"mode": "as_of", "as_of": "2026-05-29"}
        )
        first_release = self.service.gdp_vintages({"mode": "first_release"})
        before_first_release = self.service.gdp_vintages(
            {"mode": "as_of", "as_of": "2026-04-29"}
        )
        datetime_before_completion = self.service.gdp_vintages(
            {"mode": "as_of", "as_of": "2026-04-30T10:00:00-04:00"}
        )

        self.assertEqual(self._values(latest), [Decimal("2.2")])
        self.assertEqual(self._values(as_of), [Decimal("2.1")])
        self.assertEqual(self._values(first_release), [Decimal("1.8")])
        self.assertEqual(self._values(before_first_release), [])
        self.assertEqual(self._values(datetime_before_completion), [])
        self.assertEqual(latest["query"], {
            "series": "real-growth",
            "series_id": "macro.gdp.real_qoq_saar_pct",
            "mode": "latest",
            "as_of": None,
            "date_only_policy": "completed_date",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "limit": 25,
        })
        self.assertEqual(set(latest["comparison_trail"]), {"first_release", "as_of", "latest"})
        comparison = latest["comparison_trail"]
        assert isinstance(comparison, dict)
        self.assertEqual(
            [item["value"] for item in comparison["first_release"]["observations"]],
            [Decimal("1.8")],
        )
        self.assertEqual(
            [item["value"] for item in comparison["as_of"]["observations"]],
            [Decimal("2.1")],
        )
        self.assertEqual(
            [item["value"] for item in comparison["latest"]["observations"]],
            [Decimal("2.2")],
        )
        selected = latest["selected"]
        assert isinstance(selected, dict)
        self.assertIn("lineage_digest", selected)
        observation = selected["observations"][0]
        assert isinstance(observation, dict)
        for field in (
            "available_at",
            "available_precision",
            "captured_at",
            "captured_precision",
            "version_id",
            "evidence_id",
            "snapshot_id",
            "run_id",
        ):
            self.assertIn(field, observation)

    def test_gdp_query_contract_is_closed_and_bounded(self) -> None:
        self.assertEqual(GDP_QUERY_FIELDS, frozenset({"series", "mode", "as_of", "date_only_policy", "limit"}))
        invalid_queries = (
            {"relation": "macro_observation_versions"},
            {"series": "other"},
            {"mode": "current"},
            {"mode": "as_of"},
            {"mode": "latest", "as_of": "2026-05-29"},
            {"date_only_policy": "invented_timestamp"},
            {"limit": "0"},
            {"limit": "101"},
            {"limit": "1.5"},
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises((ValidationError, ResourceLimitError)):
                    self.service.gdp_vintages(query)

        nominal = self.service.gdp_vintages({"series": "nominal-gdp", "limit": "1"})
        self.assertEqual(self._values(nominal), [Decimal("30250.0")])
        self.assertFalse(nominal["truncated"])

    def test_table_inspector_exposes_only_reviewed_projection_columns(self) -> None:
        self.assertEqual(
            TABLE_QUERY_FIELDS,
            frozenset({"view", "search", "sort", "direction", "page", "limit"}),
        )
        self.assertEqual(
            TABLE_VIEW_NAMES,
            ("market-prices", "macro-observations", "company-filings", "news-items"),
        )
        for view in TABLE_VIEW_NAMES:
            with self.subTest(view=view):
                result = self.service.table_inspector({"view": view, "limit": "5"})
                self.assertEqual(result["surface"], "table-inspector")
                self.assertEqual(result["view"], view)
                self.assertIsInstance(result["label"], str)
                self.assertNotIn("relation", result)
                self.assertGreater(result["total"], 0)
                self.assertGreaterEqual(result["page_count"], 1)
                self.assertIn(result["sort"], result["available_sorts"])
                self.assertEqual(result["direction"], "desc")
                self.assertEqual(result["limit"], 5)
                self.assertEqual(result["search"], "")
                for row in result["rows"]:
                    self.assertEqual(tuple(row), tuple(result["columns"]))
                    self.assertFalse(
                        {"artifact_id", "snapshot_id", "run_id", "version_id", "payload", "body"}
                        & set(row)
                    )

    def test_table_bounds_per_view_sort_and_sql_injection_are_contained(self) -> None:
        invalid_queries = (
            {"view": "market-prices", "relation": "prices_daily"},
            {"view": "market-prices", "sql": "SELECT 1"},
            {"view": "market-prices", "sort": "published_at"},
            {"view": "news-items", "sort": "trade_date"},
            {"view": "market-prices", "sort": "trade_date; DELETE FROM prices_daily"},
            {"view": "market-prices", "direction": "desc; DELETE"},
            {"view": "market-prices", "page": "0"},
            {"view": "market-prices", "page": "101"},
            {"view": "market-prices", "limit": "0"},
            {"view": "market-prices", "limit": "101"},
            {"view": "market-prices", "search": "x" * 129},
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises((ValidationError, ResourceLimitError)):
                    self.service.table_inspector(query)

        injection = self.service.table_inspector(
            {"view": "market-prices", "search": "'); DROP TABLE prices_daily; --"}
        )
        self.assertEqual(injection["total"], 0)
        normal = self.service.table_inspector({"view": "market-prices"})
        self.assertGreater(normal["total"], 0)

    def test_unavailable_store_fails_closed_without_a_default_path(self) -> None:
        unavailable = StoreMap.four_explicit(
            market=self.store_map.market,
            macro=self.root / "missing-macro.sqlite",
            company=self.store_map.company,
            news=self.store_map.news,
        )
        service = Stage6DashboardReadService(unavailable, self.registry)
        with self.assertRaises(StoreUnavailableError):
            service.gdp_vintages({})
        with self.assertRaises(StoreUnavailableError):
            service.table_inspector({"view": "macro-observations"})
        self.assertGreater(
            service.table_inspector({"view": "market-prices"})["total"],
            0,
        )

    def test_all_dashboard_reads_avoid_writer_entry_points(self) -> None:
        with (
            patch(
                "quant_data.ingestion.IngestionCoordinator.execute",
                side_effect=AssertionError("dashboard reached ingestion"),
            ) as ingestion_writer,
            patch(
                "quant_data.migrations.initialize_all",
                side_effect=AssertionError("dashboard reached initialization"),
            ) as initializer,
            patch(
                "quant_data.migrations.migrate_store",
                side_effect=AssertionError("dashboard reached migration"),
            ) as migration_writer,
            patch(
                "quant_data.stores.writer_connection",
                side_effect=AssertionError("dashboard reached writer connection"),
            ) as connection_writer,
        ):
            self.service.gdp_vintages({})
            for view in TABLE_VIEW_NAMES:
                self.service.table_inspector({"view": view, "limit": "1"})

        ingestion_writer.assert_not_called()
        initializer.assert_not_called()
        migration_writer.assert_not_called()
        connection_writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
