from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.errors import (
    ConflictError,
    MigrationError,
    ResourceLimitError,
    ValidationError,
)
from quant_data.json_codec import dumps_strict
from quant_data.migrations import migrate_and_register_store
from quant_data.news.current_multi_source import (
    ALPACA_NEWS_URL,
    BEA_NEWS_RSS_URL,
    CURRENT_MULTI_SOURCE_MAX_BYTES,
    CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
    LEGACY_FMP_NEWS_DATASET_ID,
    LEGACY_FMP_NEWS_MIGRATION_ID,
    CapturedCurrentMultiSourceResponse,
    CurrentMultiSourceCredentials,
    CurrentMultiSourceImporter,
    CurrentMultiSourceRequest,
    ECB_PRESS_RSS_URL,
    EIA_PRESS_RSS_URL,
    FED_PRESS_RSS_URL,
    FMP_PRESS_RELEASES_URL,
)
from quant_data.registry import (
    DatasetDeclaration,
    MigrationDeclaration,
    Registry,
    StoreDeclaration,
)
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXED_TIME = datetime(2026, 8, 30, 15, 32, tzinfo=timezone.utc)


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )




def _fixture_registry() -> Registry:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    store = next(item for item in raw["stores"] if item["id"] == "news")

    def dataset(value: dict[str, object]) -> DatasetDeclaration:
        physical = dict(value["physical"])
        relations = tuple(
            str(item["name"]) for item in physical["relations"]
        )
        return DatasetDeclaration(
            id=str(value["id"]),
            version=str(value["version"]),
            store=str(value["store"]),
            layer=str(value["layer"]),
            relations=relations,
            physical=physical,
            identity=dict(value["identity"]),
            temporal=dict(value["temporal"]),
            revision_policy=str(value["revision_policy"]),
            freshness=dict(value["freshness"]),
            quality_contract=dict(value["quality_contract"]),
            collector_ids=tuple(value["collector_ids"]),
            tool_ids=tuple(value["tool_ids"]),
            dashboard_ids=tuple(value["dashboard_ids"]),
            export_ids=tuple(value["export_ids"]),
            active=bool(value["active"]),
        )

    migrations = tuple(
        MigrationDeclaration(
            id=str(item["id"]),
            store=str(item["store"]),
            ordinal=int(item["ordinal"]),
            resource=str(item["resource"]),
            sha256=str(item["sha256"]),
            semantic_scope=str(item["semantic_scope"]),
            dependencies=tuple(item["dependencies"]),
            reconstruction_state=str(item["reconstruction_state"]),
        )
        for item in raw["migrations"]
        if item["store"] == "news"
    )
    return Registry(
        schema_id=str(raw["schema_id"]),
        schema_version=str(raw["schema_version"]),
        registry_version="current-multi-source-test",
        status=str(raw["status"]),
        project_root=PROJECT_ROOT,
        source_path=REGISTRY_PATH,
        stores=(
            StoreDeclaration(
                id=str(store["id"]),
                default_path=str(store["default_path"]),
                path_env=str(store["path_env"]),
                anchor_relation=str(store["anchor_relation"]),
                control_tables=tuple(store["control_tables"]),
                migration_order=tuple(store["migration_order"]),
                write_coordination=dict(store["write_coordination"]),
                backup=dict(store["backup"]),
            ),
        ),
        migrations=migrations,
        datasets=tuple(
            dataset(item)
            for item in raw["datasets"]
            if item["store"] == "news"
        ),
        collectors=tuple(dict(item) for item in raw["collectors"]),
        jobs=(),
        exports=(),
        tools=(),
        tool_version_policies=(),
        dashboard=(),
        raw={},
    )

def _json(value: object) -> bytes:
    return dumps_strict(value).encode("utf-8")


def _rss(*, guid: str, title: str, link: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<rss><channel><item>"
        f"<guid>{guid}</guid><title>{title}</title>"
        f"<link>{link}</link>"
        "<content>PRIVATE_RSS_ARTICLE_BODY</content>"
        "<pubDate>Sat, 30 Aug 2026 10:00:00 -0400</pubDate>"
        "</item></channel></rss>"
    ).encode("utf-8")


class _Transport:
    def __init__(
        self,
        *,
        expected: dict[str, object],
        response: CapturedCurrentMultiSourceResponse,
        news_path: Path,
    ) -> None:
        self.expected = expected
        self.response = response
        self.news_path = news_path
        self.calls = 0
        self.lock_was_available = False

    def get(self, **kwargs: object) -> CapturedCurrentMultiSourceResponse:
        self.calls += 1
        if kwargs != self.expected:
            raise AssertionError(f"fixed feed request drifted: {kwargs!r}")
        with StoreWriteLock(self.news_path, timeout_seconds=0):
            self.lock_was_available = True
        return self.response


class CurrentMultiSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = _stores(self.root)
        with sqlite3.connect(self.stores.news) as connection:
            connection.executescript(
                """
                CREATE TABLE fmp_news_articles (
                    symbol TEXT NOT NULL,
                    published_date TEXT,
                    title TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    url TEXT NOT NULL,
                    site TEXT NOT NULL,
                    image_url TEXT,
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (symbol, url)
                ) STRICT;
                INSERT INTO fmp_news_articles
                    (symbol, published_date, title, body_text, url, site,
                     image_url, fetched_at, raw_json)
                VALUES
                    ('AAPL', '2026-08-29', 'Legacy headline', 'private body',
                     'https://example.test/legacy', 'example.test', NULL,
                     '2026-08-30T14:00:00Z', '{}');
                """
            )
        self.registry = _fixture_registry()
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.NEWS,
            applied_at="2026-08-30T15:00:00Z",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _importer(self, clock: datetime = FIXED_TIME) -> CurrentMultiSourceImporter:
        return CurrentMultiSourceImporter(
            self.stores,
            self.registry,
            clock=lambda: clock,
        )

    def test_legacy_fmp_table_is_preserved_as_inactive_private_evidence(self) -> None:
        migration = next(
            item
            for item in self.registry.migrations
            if item.id == LEGACY_FMP_NEWS_MIGRATION_ID
        )
        legacy = next(
            item
            for item in self.registry.datasets
            if item.id == LEGACY_FMP_NEWS_DATASET_ID
        )
        self.assertEqual((migration.store, migration.ordinal), ("news", 8))
        self.assertFalse(legacy.active)
        self.assertEqual(
            (
                legacy.collector_ids,
                legacy.tool_ids,
                legacy.dashboard_ids,
                legacy.export_ids,
            ),
            ((), (), (), ()),
        )
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            row = connection.execute(
                "SELECT title, body_text FROM fmp_news_articles"
            ).fetchone()
            registered = connection.execute(
                "SELECT active FROM dataset_registry WHERE dataset_id=?",
                (LEGACY_FMP_NEWS_DATASET_ID,),
            ).fetchone()
        self.assertEqual(tuple(row), ("Legacy headline", "private body"))
        self.assertEqual(registered[0], 0)

    def test_incompatible_legacy_table_fails_before_dataset_registration(self) -> None:
        bad_root = self.root / "bad"
        bad_root.mkdir()
        stores = _stores(bad_root)
        with sqlite3.connect(stores.news) as connection:
            connection.execute(
                "CREATE TABLE fmp_news_articles (wrong_column TEXT) STRICT"
            )
        with self.assertRaises(MigrationError):
            migrate_and_register_store(
                stores,
                self.registry,
                StoreRole.NEWS,
                applied_at="2026-08-30T15:00:00Z",
            )
        with sqlite3.connect(stores.news) as connection:
            registered = connection.execute(
                "SELECT count(*) FROM dataset_registry WHERE dataset_id=?",
                (LEGACY_FMP_NEWS_DATASET_ID,),
            ).fetchone()[0]
        self.assertEqual(registered, 0)

    def _run(
        self,
        *,
        feed_id: str,
        body: bytes,
        content_type: str,
        expected: dict[str, object],
        credentials: CurrentMultiSourceCredentials,
        symbols: tuple[str, ...] | None = None,
        clock: datetime = FIXED_TIME,
    ):
        transport = _Transport(
            expected=expected,
            response=CapturedCurrentMultiSourceResponse(200, content_type, body),
            news_path=self.stores.news,
        )
        receipt = self._importer(clock).run_once(
            request=CurrentMultiSourceRequest(
                feed_id=feed_id,
                poll_slot=clock,
                symbols=symbols,
            ),
            credentials=credentials,
            transport=transport,
        )
        self.assertEqual(transport.calls, 1)
        self.assertTrue(transport.lock_was_available)
        return receipt

    def test_fmp_press_release_stores_private_evidence_and_symbol(self) -> None:
        body = _json(
            [
                {
                    "id": "fmp-press-1",
                    "title": "Issuer announces results",
                    "text": "PRIVATE_FMP_TEXT_BODY",
                    "body": "PRIVATE_FMP_BODY",
                    "site": "Issuer",
                    "url": "https://example.test/releases/one",
                    "publishedDate": "2026-08-30T10:00:00Z",
                    "symbol": "AAPL",
                }
            ]
        )
        receipt = self._run(
            feed_id="fmp_press_releases",
            body=body,
            content_type="application/json; charset=utf-8",
            expected={
                "url": FMP_PRESS_RELEASES_URL,
                "query": {"page": "0", "limit": "1000"},
                "headers": {
                    "apikey": "offline-fmp-key",
                    "Accept": "application/json",
                },
                "timeout_seconds": CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
                "max_bytes": CURRENT_MULTI_SOURCE_MAX_BYTES,
            },
            credentials=CurrentMultiSourceCredentials(fmp_api_key="offline-fmp-key"),
        )

        self.assertEqual(
            (
                receipt.feed_id,
                receipt.poll_slot,
                receipt.outcome,
                receipt.provider_row_count,
                receipt.accepted_row_count,
                receipt.rejected_row_count,
            ),
            (
                "fmp_press_releases",
                "2026-08-30T15:00:00Z",
                "succeeded",
                1,
                1,
                0,
            ),
        )
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            capture = connection.execute(
                """
                SELECT response_bytes, feed_id
                FROM current_multi_source_captures
                """
            ).fetchone()
            symbol = connection.execute(
                "SELECT provider_symbol FROM current_multi_source_article_symbols"
            ).fetchone()[0]
            summary = connection.execute(
                "SELECT summary FROM current_multi_source_article_versions"
            ).fetchone()[0]
        self.assertEqual(bytes(capture["response_bytes"]), body)
        self.assertEqual(capture["feed_id"], "fmp_press_releases")
        self.assertEqual(symbol, "AAPL")
        self.assertEqual(summary, "")

    def test_each_official_feed_has_a_fixed_rss_request(self) -> None:
        feeds = (
            ("fed_press", FED_PRESS_RSS_URL),
            ("ecb_press", ECB_PRESS_RSS_URL),
            ("bea_news", BEA_NEWS_RSS_URL),
            ("eia_press", EIA_PRESS_RSS_URL),
        )
        for offset, (feed_id, url) in enumerate(feeds):
            with self.subTest(feed_id=feed_id):
                clock = FIXED_TIME + timedelta(hours=offset)
                receipt = self._run(
                    feed_id=feed_id,
                    body=_rss(
                        guid=f"{feed_id}-1",
                        title=f"{feed_id} release",
                        link=f"https://example.test/{feed_id}",
                    ),
                    content_type="application/rss+xml",
                    expected={
                        "url": url,
                        "query": {},
                        "headers": {
                            "Accept": (
                                "application/rss+xml, application/atom+xml, "
                                "application/xml, text/xml"
                            )
                        },
                        "timeout_seconds": CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
                        "max_bytes": CURRENT_MULTI_SOURCE_MAX_BYTES,
                    },
                    credentials=CurrentMultiSourceCredentials(),
                    clock=clock,
                )
                self.assertEqual(receipt.outcome, "succeeded")


        with read_connection(self.stores, StoreRole.NEWS) as connection:
            summaries = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT summary FROM current_multi_source_article_versions"
                )
            )
        self.assertEqual(summaries, ("", "", "", ""))
    def test_alpaca_uses_a_bounded_sorted_company_batch_and_numeric_id(self) -> None:
        body = _json(
            {
                "news": [
                    {
                        "id": 101,
                        "headline": "A company headline",
                        "summary": "Short summary",
                        "source": "Benzinga",
                        "url": "https://example.test/alpaca/101",
                        "updated_at": "2026-08-30T11:00:00Z",
                        "symbols": ["MSFT", "AAPL"],
                    }
                ]
            }
        )
        receipt = self._run(
            feed_id="alpaca_benzinga",
            body=body,
            content_type="application/json",
            expected={
                "url": ALPACA_NEWS_URL,
                "query": {
                    "symbols": "AAPL,MSFT",
                    "limit": "50",
                    "include_content": "false",
                    "sort": "desc",
                },
                "headers": {
                    "APCA-API-KEY-ID": "offline-alpaca-key",
                    "APCA-API-SECRET-KEY": "offline-alpaca-secret",
                    "Accept": "application/json",
                },
                "timeout_seconds": CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
                "max_bytes": CURRENT_MULTI_SOURCE_MAX_BYTES,
            },
            credentials=CurrentMultiSourceCredentials(
                alpaca_api_key="offline-alpaca-key",
                alpaca_api_secret="offline-alpaca-secret",
            ),
            symbols=("msft", "AAPL"),
        )
        self.assertEqual(receipt.accepted_row_count, 1)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            symbols = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT provider_symbol
                    FROM current_multi_source_article_symbols
                    ORDER BY provider_symbol
                    """
                )
            )
            source_key = connection.execute(
                "SELECT source_item_key FROM current_multi_source_articles"
            ).fetchone()[0]
        self.assertEqual(symbols, ("AAPL", "MSFT"))
        self.assertEqual(source_key, "id:101")

    def test_unchanged_next_hour_keeps_one_article_and_version(self) -> None:
        body = _json(
            [
                {
                    "id": "same-1",
                    "title": "Same article",
                    "text": "Same body",
                    "site": "Issuer",
                    "url": "https://example.test/releases/same",
                    "publishedDate": "2026-08-30T10:00:00Z",
                    "symbol": "AAPL",
                }
            ]
        )
        expected = {
            "url": FMP_PRESS_RELEASES_URL,
            "query": {"page": "0", "limit": "1000"},
            "headers": {
                "apikey": "offline-fmp-key",
                "Accept": "application/json",
            },
            "timeout_seconds": CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
            "max_bytes": CURRENT_MULTI_SOURCE_MAX_BYTES,
        }
        credentials = CurrentMultiSourceCredentials(fmp_api_key="offline-fmp-key")
        self._run(
            feed_id="fmp_press_releases",
            body=body,
            content_type="application/json",
            expected=expected,
            credentials=credentials,
        )
        self._run(
            feed_id="fmp_press_releases",
            body=body,
            content_type="application/json",
            expected=expected,
            credentials=credentials,
            clock=FIXED_TIME + timedelta(hours=1),
        )
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            counts = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT count(*) FROM current_multi_source_captures
                    UNION ALL SELECT count(*) FROM current_multi_source_articles
                    UNION ALL SELECT count(*) FROM current_multi_source_article_versions
                    UNION ALL SELECT count(*) FROM current_multi_source_capture_articles
                    """
                )
            )
        self.assertEqual(counts, (2, 1, 1, 2))

    def test_rejected_response_records_terminal_outcome_without_capture(self) -> None:
        transport = _Transport(
            expected={
                "url": FMP_PRESS_RELEASES_URL,
                "query": {"page": "0", "limit": "1000"},
                "headers": {
                    "apikey": "offline-fmp-key",
                    "Accept": "application/json",
                },
                "timeout_seconds": CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
                "max_bytes": CURRENT_MULTI_SOURCE_MAX_BYTES,
            },
            response=CapturedCurrentMultiSourceResponse(
                200, "text/plain", b"not a JSON response"
            ),
            news_path=self.stores.news,
        )
        with self.assertRaises(ValidationError):
            self._importer().run_once(
                request=CurrentMultiSourceRequest(
                    feed_id="fmp_press_releases",
                    poll_slot=FIXED_TIME,
                ),
                credentials=CurrentMultiSourceCredentials(
                    fmp_api_key="offline-fmp-key"
                ),
                transport=transport,
            )
        self.assertTrue(transport.lock_was_available)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            outcome = connection.execute(
                "SELECT outcome_kind FROM current_multi_source_outcomes"
            ).fetchone()[0]
            captures = connection.execute(
                "SELECT count(*) FROM current_multi_source_captures"
            ).fetchone()[0]
        self.assertEqual(outcome, "response_rejected")
        self.assertEqual(captures, 0)
        with self.assertRaises(ConflictError):
            self._importer().run_once(
                request=CurrentMultiSourceRequest(
                    feed_id="fmp_press_releases",
                    poll_slot=FIXED_TIME,
                ),
                credentials=CurrentMultiSourceCredentials(
                    fmp_api_key="offline-fmp-key"
                ),
                transport=transport,
            )
        self.assertEqual(transport.calls, 1)

    def test_request_rejects_non_alpaca_symbols_and_large_alpaca_batch(self) -> None:
        with self.assertRaises(ValidationError):
            CurrentMultiSourceRequest(
                feed_id="fed_press",
                poll_slot=FIXED_TIME,
                symbols=("AAPL",),
            )
        with self.assertRaises(ResourceLimitError):
            CurrentMultiSourceRequest(
                feed_id="alpaca_benzinga",
                poll_slot=FIXED_TIME,
                symbols=tuple(f"S{index}" for index in range(51)),
            )


if __name__ == "__main__":
    unittest.main()
