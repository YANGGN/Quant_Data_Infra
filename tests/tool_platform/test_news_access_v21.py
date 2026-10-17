from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
from quant_data.news.current_multi_source import (
    CapturedCurrentMultiSourceResponse,
    CurrentMultiSourceCredentials,
    CurrentMultiSourceImporter,
    CurrentMultiSourceRequest,
)
from quant_data.news.fmp_stock_latest_current import (
    CapturedFmpStockLatestCurrentResponse,
    FmpStockLatestCurrentImporter,
    FmpStockLatestCurrentRequest,
)
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, writer_connection
from quant_data.tool_platform.arguments import CurrentNewsSearchArgumentsV21
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.news_access import TOOL_NAME
from quant_data.tool_platform.operations import invoke_operation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FMP_CAPTURED_AT = datetime(2026, 8, 30, 15, 32, tzinfo=timezone.utc)
FED_CAPTURED_AT = FMP_CAPTURED_AT + timedelta(hours=1)


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(record: object) -> dict[str, object]:
    return {field.name: field.value for field in getattr(record, "fields")}


class _FmpTransport:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def get(self, **_: object) -> CapturedFmpStockLatestCurrentResponse:
        return CapturedFmpStockLatestCurrentResponse(
            status=200,
            content_type="application/json",
            body=self._body,
        )


class _OfficialFeedTransport:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def get(self, **_: object) -> CapturedCurrentMultiSourceResponse:
        return CapturedCurrentMultiSourceResponse(
            status=200,
            content_type="application/rss+xml",
            body=self._body,
        )


class NewsAccessV21Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _stores(Path(self.temporary.name))
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.store_map, self.registry)
        self.registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        self._seed_current_news()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_current_news(self) -> None:
        fmp_body = dumps_strict(
            [
                {
                    "symbol": "AAPL",
                    "title": "AAPL stock-latest headline",
                    "text": "FMP_PRIVATE_BODY_AND_RAW_EVIDENCE",
                    "site": "FMP Wire",
                    "url": "https://example.test/fmp/aapl",
                    "image": "https://example.test/fmp/aapl.jpg",
                    "publishedDate": "2026-08-30T08:00:00Z",
                }
            ]
        ).encode("utf-8")
        FmpStockLatestCurrentImporter(
            self.store_map,
            self.registry,
            clock=lambda: FMP_CAPTURED_AT,
        ).run_once(
            api_key="offline-fmp-key",
            request=FmpStockLatestCurrentRequest(FMP_CAPTURED_AT),
            transport=_FmpTransport(fmp_body),
        )

        fed_body = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<rss><channel><item>"
            "<guid>fed-release-1</guid>"
            "<title>Federal Reserve release</title>"
            "<link>https://example.test/fed/release-1</link>"
            "<content>PRIVATE_RSS_ARTICLE_BODY</content>"
            "<pubDate>Sat, 30 Aug 2026 10:00:00 -0400</pubDate>"
            "</item></channel></rss>"
        ).encode("utf-8")
        CurrentMultiSourceImporter(
            self.store_map,
            self.registry,
            clock=lambda: FED_CAPTURED_AT,
        ).run_once(
            request=CurrentMultiSourceRequest(
                feed_id="fed_press",
                poll_slot=FED_CAPTURED_AT,
            ),
            credentials=CurrentMultiSourceCredentials(),
            transport=_OfficialFeedTransport(fed_body),
        )
        with writer_connection(self.store_map, StoreRole.NEWS) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.store_map,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="current-news-v2-1-test",
            api_version="1.0",
            tool_name=TOOL_NAME,
            tool_version="2.1.0",
            operation_graph_id="tool_platform.news.search.v2_1",
            operation_version="2.1.0",
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
    def _arguments(
        *,
        source_ids: tuple[str, ...] = (),
        limit: int = 100,
    ) -> CurrentNewsSearchArgumentsV21:
        return CurrentNewsSearchArgumentsV21(
            query="",
            symbols=(),
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            start_date=None,
            end_date=None,
            limit=limit,
            source_ids=source_ids,
        )

    def _invoke(
        self,
        *,
        source_ids: tuple[str, ...] = (),
        limit: int = 100,
    ):
        return invoke_operation(
            TOOL_NAME,
            self._arguments(source_ids=source_ids, limit=limit),
            self._context(),
            self.registry,
        )

    def test_merges_exact_source_filters_and_keeps_private_evidence_out(self) -> None:
        merged = self._invoke()
        self.assertEqual(merged.status, "ok")
        self.assertEqual(
            [_fields(record)["feed_id"] for record in merged.records],
            ["fed_press", "fmp_stock_latest"],
        )
        expected_fields = {
            "article_id",
            "article_version_id",
            "available_at",
            "capture_id",
            "feed_id",
            "headline",
            "provider",
            "published_date_raw",
            "published_normalized_at",
            "published_offset_status",
            "published_precision",
            "source_name",
            "source_row",
            "source_url",
            "summary",
            "symbol",
            "symbols",
            "version_sequence",
        }
        self.assertEqual(set(_fields(merged.records[0])), expected_fields)
        self.assertNotIn("FMP_PRIVATE_BODY_AND_RAW_EVIDENCE", repr(merged))
        self.assertNotIn("PRIVATE_RSS_ARTICLE_BODY", repr(merged))
        for record in merged.records:
            fields = _fields(record)
            self.assertNotIn("body_text", fields)
            self.assertNotIn("image_url", fields)
            self.assertNotIn("response_bytes", fields)

        fmp_only = self._invoke(source_ids=("fmp_stock_latest",))
        self.assertEqual(
            [_fields(record)["feed_id"] for record in fmp_only.records],
            ["fmp_stock_latest"],
        )
        self.assertEqual(
            {(item.dataset_id, item.semantic_id) for item in fmp_only.lineage},
            {
                (
                    "news.fmp.stock_latest_current_evidence",
                    _fields(fmp_only.records[0])["capture_id"],
                ),
                (
                    "news.fmp.stock_latest_current_articles",
                    _fields(fmp_only.records[0])["article_version_id"],
                ),
            },
        )

        fed_only = self._invoke(source_ids=("fed_press",))
        self.assertEqual(
            [_fields(record)["feed_id"] for record in fed_only.records],
            ["fed_press"],
        )
        self.assertEqual(
            {(item.dataset_id, item.semantic_id) for item in fed_only.lineage},
            {
                (
                    "news.current_multi_source_evidence",
                    _fields(fed_only.records[0])["capture_id"],
                ),
                (
                    "news.current_multi_source_articles",
                    _fields(fed_only.records[0])["article_version_id"],
                ),
            },
        )

    def test_global_limit_is_deterministic_and_reports_complete_lineage(self) -> None:
        first = self._invoke(limit=1)
        second = self._invoke(limit=1)

        self.assertEqual(first, second)
        self.assertEqual(len(first.records), 1)
        self.assertEqual(_fields(first.records[0])["feed_id"], "fed_press")
        self.assertTrue(first.truncation.applied)
        self.assertTrue(first.truncation.has_more)
        self.assertEqual(first.truncation.total_known_count, 2)
        fields = _fields(first.records[0])
        self.assertEqual(
            {(item.dataset_id, item.semantic_id) for item in first.lineage},
            {
                ("news.current_multi_source_evidence", fields["capture_id"]),
                (
                    "news.current_multi_source_articles",
                    fields["article_version_id"],
                ),
            },
        )

    def test_rejects_unsupported_source_id_before_selection(self) -> None:
        with self.assertRaises(ValidationError):
            self._invoke(source_ids=("unapproved_news_feed",))
