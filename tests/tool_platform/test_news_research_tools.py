"""Focused offline routes for the compact retained-news tool suite."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage12_incremental import (
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
)
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
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
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.arguments import (
    CurrentNewsSearchArgumentsV22,
    NewsAnalysisArgumentsV1,
    NewsAttentionArgumentsV1,
    NewsEventImpactArgumentsV1,
    NewsItemHistoryArgumentsV1,
    NewsSourceStatusArgumentsV1,
    NewsStoryClusterArgumentsV1,
)
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.operations import invoke_operation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STAGE12A_SCOPE = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
_STAGE12B_SCOPE = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
_PRICE_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "stage12b" / "daily_price_complete.json"
_NEWS_CAPTURED_AT = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
_MARKET_CAPTURED_AT = "2026-08-15T01:00:00Z"
_AAPL_INSTRUMENT_ID = "news-tools-aapl"


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


class NewsResearchToolRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(
            self.stores,
            self.registry,
            applied_at=_MARKET_CAPTURED_AT,
        )
        self._seed_price_series()
        self._seed_news()
        with writer_connection(self.stores, StoreRole.NEWS) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.baseline = mutation_fingerprint(self.stores)
        self.registry_sha256 = hashlib.sha256(
            (PROJECT_ROOT / CANONICAL_REGISTRY_PATH).read_bytes()
        ).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(
            self.baseline["sha256"], mutation_fingerprint(self.stores)["sha256"]
        )
        self.temporary.cleanup()

    def _seed_price_series(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "news_tools_market_seed",
                    "market.stage10.instruments",
                    "a" * 64,
                    "fixture.seed",
                    "{}",
                    _MARKET_CAPTURED_AT,
                    "fixture",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type,
                    display_name, exchange_code, currency_segment,
                    first_trade_date, identity_seed_sha256, captured_at,
                    captured_precision, run_id
                ) VALUES (?, 'fmp', 'AAPL', 'equity', 'AAPL fixture', 'XNAS',
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                (
                    _AAPL_INSTRUMENT_ID,
                    "b" * 64,
                    _MARKET_CAPTURED_AT,
                    "news_tools_market_seed",
                ),
            )
        collector = Stage12BIncrementalCollector(
            fixture_root=self.root,
            market_store=self.stores.market,
            scope=load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE),
            stage12a_scope=load_stage12_market_v1_scope(_STAGE12A_SCOPE),
            stage12a_scope_source=_STAGE12A_SCOPE,
        )
        fixture_rows = loads_strict(_PRICE_FIXTURE.read_bytes())
        self.assertIsInstance(fixture_rows, list)
        price_body = dumps_strict(
            [
                {
                    "symbol": "AAPL",
                    "date": "2026-08-08",
                    "open": 99,
                    "high": 101,
                    "low": 98,
                    "close": 100,
                    "volume": 999,
                    "change": 1,
                    "changePercent": 1,
                    "vwap": 100,
                },
                *fixture_rows,
            ]
        ).encode("utf-8")
        prepared = collector.prepare(
            Stage12BFixtureRequest(
                symbol="AAPL",
                from_date="2026-08-08",
                to_date="2026-08-12",
                session_dates=(
                    "2026-08-08",
                    "2026-08-10",
                    "2026-08-11",
                    "2026-08-12",
                ),
                captured_at=_MARKET_CAPTURED_AT,
            ),
            Stage12BFixtureResponse(
                status=200,
                media_type="application/json; charset=utf-8",
                body=price_body,
                elapsed_seconds=1,
            ),
        )
        collector.publish(prepared)

    def _seed_news(self) -> None:
        fmp_body = dumps_strict(
            [
                {
                    "symbol": "AAPL",
                    "title": "AAPL beats earnings and raises guidance",
                    "text": "PRIVATE_FMP_BODY",
                    "site": "FMP Wire",
                    "url": "https://example.test/fmp/aapl-results",
                    "image": "https://example.test/fmp/aapl-results.jpg",
                    "publishedDate": "2026-08-08T08:00:00Z",
                },
                {
                    "symbol": "MSFT",
                    "title": "MSFT launches a new product",
                    "text": "PRIVATE_FMP_BODY",
                    "site": "FMP Wire",
                    "url": "https://example.test/fmp/msft-product",
                    "image": "https://example.test/fmp/msft-product.jpg",
                    "publishedDate": "2026-08-08T09:00:00Z",
                },
            ]
        ).encode("utf-8")
        FmpStockLatestCurrentImporter(
            self.stores,
            self.registry,
            clock=lambda: _NEWS_CAPTURED_AT,
        ).run_once(
            api_key="offline-fmp-key",
            request=FmpStockLatestCurrentRequest(_NEWS_CAPTURED_AT),
            transport=_FmpTransport(fmp_body),
        )
        fed_body = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<rss><channel><item><guid>fed-release-1</guid>"
            "<title>Federal Reserve interest rate decision</title>"
            "<link>https://example.test/fed/release-1</link>"
            "<content>PRIVATE_OFFICIAL_BODY</content>"
            "<pubDate>Sat, 09 Aug 2026 10:00:00 -0400</pubDate>"
            "</item></channel></rss>"
        ).encode("utf-8")
        CurrentMultiSourceImporter(
            self.stores,
            self.registry,
            clock=lambda: _NEWS_CAPTURED_AT,
        ).run_once(
            request=CurrentMultiSourceRequest(
                feed_id="fed_press",
                poll_slot=_NEWS_CAPTURED_AT,
            ),
            credentials=CurrentMultiSourceCredentials(),
            transport=_OfficialFeedTransport(fed_body),
        )

    def _context(self, name: str, version: str) -> ToolExecutionContext:
        graph_suffix = {"2.2.0": "v2_2", "1.0.0": "v1"}[version]
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id=f"news-research-{name}-{version}",
            api_version="1.0",
            tool_name=name,
            tool_version=version,
            operation_graph_id=f"tool_platform.{name}.{graph_suffix}",
            operation_version=version,
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
        )

    def _call(self, name: str, arguments: object, *, version: str = "1.0.0"):
        return invoke_operation(
            name,
            arguments,
            self._context(name, version),
            self.registry,
        )

    @staticmethod
    def _analysis_arguments() -> NewsAnalysisArgumentsV1:
        return NewsAnalysisArgumentsV1(
            query="",
            symbols=(),
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            start_date=None,
            end_date=None,
            limit=100,
            source_ids=(),
        )

    @staticmethod
    def _v22_arguments(*, cursor: str | None = None, query: str = "") -> CurrentNewsSearchArgumentsV22:
        return CurrentNewsSearchArgumentsV22(
            query=query,
            symbols=(),
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            start_date=None,
            end_date=None,
            limit=2,
            source_ids=(),
            cursor=cursor,
        )

    def test_v22_keyset_pagination_has_no_duplicates_and_binds_query(self) -> None:
        first = self._call(
            "news.search", self._v22_arguments(), version="2.2.0"
        )
        self.assertEqual(first.status, "ok")
        self.assertEqual(len(first.records), 2)
        self.assertTrue(first.truncation.has_more)
        self.assertIsNotNone(first.truncation.next_cursor)

        second = self._call(
            "news.search",
            replace(
                self._v22_arguments(), cursor=first.truncation.next_cursor
            ),
            version="2.2.0",
        )
        self.assertEqual(second.status, "ok")
        self.assertFalse(second.truncation.has_more)
        version_ids = [
            _fields(record)["article_version_id"]
            for record in (*first.records, *second.records)
        ]
        self.assertEqual(len(version_ids), 3)
        self.assertEqual(len(set(version_ids)), len(version_ids))

        with self.assertRaises(ValidationError):
            self._call(
                "news.search",
                self._v22_arguments(
                    cursor=first.truncation.next_cursor, query="different"
                ),
                version="2.2.0",
            )

    def test_all_news_research_routes_return_bounded_public_results(self) -> None:
        search = self._call(
            "news.search",
            replace(self._v22_arguments(), limit=3),
            version="2.2.0",
        )
        selected = tuple(_fields(record) for record in search.records)
        aapl = next(row for row in selected if row["symbol"] == "AAPL")
        fed = next(row for row in selected if row["feed_id"] == "fed_press")

        status = self._call(
            "news.get_source_status", NewsSourceStatusArgumentsV1(source_ids=())
        )
        self.assertEqual(status.status, "ok")
        self.assertEqual(len(status.records), 8)
        status_rows = {_fields(record)["source_id"]: _fields(record) for record in status.records}
        self.assertEqual(status_rows["fmp_stock_latest"]["capture_count"], 1)
        self.assertEqual(status_rows["fed_press"]["capture_count"], 1)

        history = self._call(
            "news.get_item_history",
            NewsItemHistoryArgumentsV1(article_id=aapl["article_id"], limit=10),
        )
        self.assertEqual(history.status, "ok")
        self.assertEqual(len(history.records), 1)
        self.assertEqual(
            _fields(history.records[0])["article_version_id"],
            aapl["article_version_id"],
        )

        analysis = self._analysis_arguments()
        routes = (
            (
                "news.story_clusters",
                NewsStoryClusterArgumentsV1(**dict(analysis), window_hours=24),
                "candidate",
            ),
            ("news.entity_coverage", analysis, "provider_symbol_only"),
            (
                "news.attention_metrics",
                NewsAttentionArgumentsV1(
                    **dict(analysis), bucket="day", baseline_periods=2
                ),
                "insufficient_history",
            ),
            ("news.classify_events", analysis, "candidate"),
            ("news.headline_sentiment", analysis, "lexicon_descriptive"),
        )
        for name, arguments, expected_marker in routes:
            with self.subTest(name=name):
                result = self._call(name, arguments)
                self.assertEqual(result.status, "ok")
                self.assertGreaterEqual(len(result.records), 1)
                self.assertIn(expected_marker, repr(result))
                self.assertNotIn("PRIVATE_FMP_BODY", repr(result))
                self.assertNotIn("PRIVATE_OFFICIAL_BODY", repr(result))

        established = self._call(
            "research.news_event_impact",
            NewsEventImpactArgumentsV1(
                article_version_id=aapl["article_version_id"],
                instrument_id=_AAPL_INSTRUMENT_ID,
                pre_observations=0,
                post_observations=0,
            ),
        )
        self.assertEqual(established.status, "ok")
        impact = _fields(established.records[0])
        self.assertEqual(impact["analysis_status"], "established_retrospective")
        self.assertFalse(impact["causal_interpretation"])

        not_established = self._call(
            "research.news_event_impact",
            NewsEventImpactArgumentsV1(
                article_version_id=fed["article_version_id"],
                instrument_id=_AAPL_INSTRUMENT_ID,
                pre_observations=0,
                post_observations=0,
            ),
        )
        self.assertEqual(not_established.status, "not_established")
        self.assertEqual(
            not_established.diagnostics[0].code,
            "news_symbol_does_not_match_instrument",
        )


if __name__ == "__main__":
    unittest.main()
