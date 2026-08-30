from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from quant_data.errors import ValidationError
from quant_data.news.current_repository import CurrentNewsSelection
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.arguments import CurrentNewsSearchArgumentsV2
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.news_access import (
    TOOL_NAME,
    invoke_news_search_v2,
)

from quant_data.tool_platform.operations import invoke_operation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(record: object) -> dict[str, object]:
    return {field.name: field.value for field in getattr(record, "fields")}


class NewsAccessV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _stores(Path(self.temporary.name))
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.store_map,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="current-news-v2-test",
            api_version="1.0",
            tool_name=TOOL_NAME,
            tool_version="2.0.0",
            operation_graph_id="tool_platform.news.search.v2",
            operation_version="2.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
        )

    @staticmethod
    def _selection(*, truncated: bool = False) -> CurrentNewsSelection:
        records = (
            {
                "article_id": "article-1",
                "article_version_id": "version-1",
                "available_at": "2026-08-21T12:00:00Z",
                "capture_id": "capture-1",
                "headline": "AAPL headline",
                "published_date_raw": "2026-08-21T08:00:00-04:00",
                "published_normalized_at": "2026-08-21T08:00:00.000000-04:00",
                "published_precision": "datetime_offset",
                "published_offset_status": "known",
                "source_name": "Market Wire",
                "source_row": 1,
                "source_url": "https://example.test/aapl",
                "symbol": "AAPL",
                "version_sequence": 1,
            },
        )
        return CurrentNewsSelection(
            records=records,
            total_selected_count=2 if truncated else 1,
            truncated=truncated,
            migration_ids=("news:0001_foundation", "news:0006_current"),
            receipt_sha256="a" * 64,
        )

    def _arguments(self, **changes: object) -> CurrentNewsSearchArgumentsV2:
        values: dict[str, object] = {
            "query": "",
            "symbols": (),
            "mode": "latest",
            "as_of": None,
            "date_only_policy": "completed_date",
            "start_date": None,
            "end_date": None,
            "limit": 100,
        }
        values.update(changes)
        return CurrentNewsSearchArgumentsV2(**values)

    def test_projects_headline_metadata_and_complete_lineage(self) -> None:
        selection = self._selection()
        with mock.patch(
            "quant_data.tool_platform.news_access.CurrentNewsRepository"
        ) as repository_type:
            repository_type.return_value.search.return_value = selection
            result = invoke_news_search_v2(
                TOOL_NAME,
                self._arguments(
                    query="aapl",
                    symbols=("AAPL",),
                    mode="as_of",
                    as_of="2026-08-22T00:00:00Z",
                ),
                self._context(),
                self.registry,
            )

        repository_type.assert_called_once_with(self.store_map, self.registry)
        query = repository_type.return_value.search.call_args.args[0]
        self.assertEqual(query.query, "aapl")
        self.assertEqual(query.symbols, ("AAPL",))
        self.assertEqual(query.mode, "as_of")
        self.assertEqual(query.as_of, "2026-08-22T00:00:00Z")
        self.assertEqual(result.status, "ok")
        fields = _fields(result.records[0])
        self.assertEqual(
            set(fields),
            {
                "article_id",
                "article_version_id",
                "available_at",
                "capture_id",
                "headline",
                "published_date_raw",
                "published_normalized_at",
                "published_precision",
                "source_name",
                "source_row",
                "source_url",
                "symbol",
                "version_sequence",
            },
        )
        self.assertNotIn("body_text", fields)
        self.assertNotIn("image_url", fields)
        self.assertEqual(
            {(item.dataset_id, item.semantic_id) for item in result.lineage},
            {
                ("news.fmp.stock_latest_current_evidence", "capture-1"),
                ("news.fmp.stock_latest_current_articles", "version-1"),
            },
        )
        metrics = {item.name: item.value for item in result.diagnostics[0].metrics}
        self.assertEqual(metrics["availability_basis"], "local_capture")
        self.assertEqual(metrics["migration_ids"], '["news:0001_foundation","news:0006_current"]')
        self.assertEqual(metrics["receipt_sha256"], "a" * 64)

    def test_empty_result_is_honest_and_truncation_is_explicit(self) -> None:
        empty = CurrentNewsSelection(
            records=(),
            total_selected_count=0,
            truncated=False,
            migration_ids=("news:0006_current",),
            receipt_sha256="b" * 64,
        )
        with mock.patch(
            "quant_data.tool_platform.news_access.CurrentNewsRepository"
        ) as repository_type:
            repository_type.return_value.search.return_value = empty
            result = invoke_news_search_v2(
                TOOL_NAME,
                self._arguments(),
                self._context(),
                self.registry,
            )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.records, ())
        self.assertEqual(result.lineage, ())
        self.assertFalse(result.truncation.applied)

        with mock.patch(
            "quant_data.tool_platform.news_access.CurrentNewsRepository"
        ) as repository_type:
            repository_type.return_value.search.return_value = self._selection(
                truncated=True
            )
            truncated = invoke_news_search_v2(
                TOOL_NAME,
                self._arguments(limit=1),
                self._context(),
                self.registry,
            )
        self.assertTrue(truncated.truncation.applied)
        self.assertTrue(truncated.truncation.has_more)
        self.assertEqual(truncated.truncation.total_known_count, 2)

    def test_closed_dispatch_routes_news_search_v2(self) -> None:
        with mock.patch(
            "quant_data.tool_platform.news_access.CurrentNewsRepository"
        ) as repository_type:
            repository_type.return_value.search.return_value = self._selection()
            result = invoke_operation(
                TOOL_NAME,
                self._arguments(limit=1),
                self._context(),
                self.registry,
            )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.tool, TOOL_NAME)
        repository_type.return_value.search.assert_called_once()

    def test_rejects_wrong_typed_arguments(self) -> None:

        with self.assertRaises(ValidationError):
            invoke_news_search_v2(
                TOOL_NAME,
                {"query": "AAPL"},
                self._context(),
                self.registry,
            )


if __name__ == "__main__":
    unittest.main()
