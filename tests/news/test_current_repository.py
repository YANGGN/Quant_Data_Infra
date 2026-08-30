from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.migrations import initialize_all
from quant_data.news.current_repository import (
    CurrentNewsQuery,
    CurrentNewsRepository,
)
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _install_current_news_schema(store_map: StoreMap) -> None:
    """Temporary target tables until the reviewed forward migration is present."""

    with writer_connection(store_map, StoreRole.NEWS) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_attempts (
                attempt_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_outcomes (
                outcome_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_captures (
                capture_id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                provider_row_count INTEGER NOT NULL,
                accepted_row_count INTEGER NOT NULL,
                rejected_row_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_articles (
                article_id TEXT PRIMARY KEY,
                site TEXT NOT NULL,
                source_url TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_capture_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_article_versions (
                article_version_id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body_text TEXT NOT NULL,
                image_url TEXT,
                published_date_raw TEXT,
                published_normalized_at TEXT,
                published_precision TEXT NOT NULL,
                published_offset_status TEXT NOT NULL,
                version_sequence INTEGER NOT NULL,
                supersedes_article_version_id TEXT,
                capture_id TEXT NOT NULL,
                source_row INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fmp_stock_latest_current_capture_articles (
                capture_id TEXT NOT NULL,
                article_version_id TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                PRIMARY KEY (capture_id, source_row)
            );
            """
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _insert_capture(
    store_map: StoreMap,
    *,
    capture_id: str,
    captured_at: str,
) -> None:
    attempt_id = f"attempt-{capture_id}"
    outcome_id = f"outcome-{capture_id}"
    request_scope_sha256 = hashlib.sha256(
        f"{capture_id}:scope".encode("utf-8")
    ).hexdigest()
    response_bytes = b"[]"
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    semantic_identity = hashlib.sha256(
        f"{capture_id}:semantic".encode("utf-8")
    ).hexdigest()
    with writer_connection(store_map, StoreRole.NEWS) as connection:
        connection.execute(
            """
            INSERT INTO fmp_stock_latest_current_attempts (
                attempt_id, dataset_id, collector_id, profile_id, poll_slot,
                request_scope_json, request_scope_sha256,
                intent_recorded_at, intent_recorded_precision
            ) VALUES (
                ?, 'news.fmp.stock_latest_current_evidence',
                'fmp.news.stock_latest_current',
                'fmp.stock_latest.page0.limit1000.current.v1',
                ?, '{}', ?, ?, 'datetime'
            )
            """,
            (attempt_id, captured_at, request_scope_sha256, captured_at),
        )
        connection.execute(
            """
            INSERT INTO fmp_stock_latest_current_outcomes (
                outcome_id, attempt_id, outcome_kind, response_sha256,
                response_byte_count, http_status, content_type,
                provider_row_count, accepted_row_count, rejected_row_count,
                recorded_at, recorded_precision
            ) VALUES (
                ?, ?, 'succeeded', ?, ?, 200, 'application/json',
                4, 4, 0, ?, 'datetime'
            )
            """,
            (
                outcome_id,
                attempt_id,
                response_sha256,
                len(response_bytes),
                captured_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO fmp_stock_latest_current_captures (
                capture_id, dataset_id, attempt_id, outcome_id,
                request_scope_sha256, response_sha256, response_bytes,
                http_status, content_type, semantic_identity, completeness,
                captured_at, captured_precision, provider_row_count,
                accepted_row_count, rejected_row_count, normalization_version
            ) VALUES (
                ?, 'news.fmp.stock_latest_current_evidence', ?, ?, ?, ?, ?,
                200, 'application/json', ?, 'partial', ?, 'datetime',
                4, 4, 0, 'fmp.news.stock_latest.current.v1'
            )
            """,
            (
                capture_id,
                attempt_id,
                outcome_id,
                request_scope_sha256,
                response_sha256,
                response_bytes,
                semantic_identity,
                captured_at,
            ),
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _insert_article(
    store_map: StoreMap,
    *,
    article_id: str,
    version_id: str,
    capture_id: str,
    source_row: int,
    site: str,
    source_url: str,
    symbol: str,
    headline: str,
    body_text: str,
    raw: str | None,
    normalized: str | None,
    precision: str,
    offset_status: str,
    sequence: int = 1,
    supersedes: str | None = None,
) -> None:
    mutable_content_sha256 = hashlib.sha256(
        f"{headline}|{body_text}|{raw}|{normalized}".encode("utf-8")
    ).hexdigest()
    with writer_connection(store_map, StoreRole.NEWS) as connection:
        if sequence == 1:
            connection.execute(
                """
                INSERT INTO fmp_stock_latest_current_articles (
                    article_id, provider_namespace, site, source_url, symbol,
                    created_capture_id
                ) VALUES (?, 'fmp.news.stock_latest_current', ?, ?, ?, ?)
                """,
                (article_id, site, source_url, symbol, capture_id),
            )
        connection.execute(
            """
            INSERT INTO fmp_stock_latest_current_article_versions (
                article_version_id, article_id, mutable_content_sha256,
                title, body_text, image_url,
                published_date_raw, published_normalized_at, published_precision,
                published_offset_status, version_sequence,
                supersedes_article_version_id, capture_id, source_row
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                article_id,
                mutable_content_sha256,
                headline,
                body_text,
                raw,
                normalized,
                precision,
                offset_status,
                sequence,
                supersedes,
                capture_id,
                source_row,
            ),
        )
        connection.execute(
            """
            INSERT INTO fmp_stock_latest_current_capture_articles (
                capture_id, article_version_id, source_row
            ) VALUES (?, ?, ?)
            """,
            (capture_id, version_id, source_row),
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class CurrentNewsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _stores(Path(self.temporary.name))
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.store_map, registry)
        _install_current_news_schema(self.store_map)
        _insert_capture(
            self.store_map,
            capture_id="capture-1",
            captured_at="2026-08-20T10:00:00Z",
        )
        _insert_capture(
            self.store_map,
            capture_id="capture-2",
            captured_at="2026-08-20T12:00:00Z",
        )
        _insert_capture(
            self.store_map,
            capture_id="capture-3",
            captured_at="2026-08-21T01:00:00Z",
        )
        _insert_article(
            self.store_map,
            article_id="article-aapl",
            version_id="version-aapl-1",
            capture_id="capture-1",
            source_row=1,
            site="Market Wire",
            source_url="https://example.test/aapl",
            symbol="AAPL",
            headline="AAPL initial headline",
            body_text="private body text",
            raw="2026-08-20T08:00:00-04:00",
            normalized="2026-08-20T08:00:00.000000-04:00",
            precision="datetime_offset",
            offset_status="known",
        )
        _insert_article(
            self.store_map,
            article_id="article-aapl",
            version_id="version-aapl-2",
            capture_id="capture-2",
            source_row=1,
            site="Market Wire",
            source_url="https://example.test/aapl",
            symbol="AAPL",
            headline="AAPL corrected headline",
            body_text="new private body text",
            raw="2026-08-20",
            normalized=None,
            precision="date",
            offset_status="unknown",
            sequence=2,
            supersedes="version-aapl-1",
        )
        _insert_article(
            self.store_map,
            article_id="article-msft",
            version_id="version-msft-1",
            capture_id="capture-2",
            source_row=2,
            site="Global News",
            source_url="https://example.test/msft",
            symbol="MSFT",
            headline="MSFT late headline",
            body_text="private MSFT body",
            raw="2026-08-20T09:00:00+02:00",
            normalized="2026-08-20T09:00:00.000000+02:00",
            precision="datetime_offset",
            offset_status="known",
        )
        _insert_article(
            self.store_map,
            article_id="article-qqq",
            version_id="version-qqq-1",
            capture_id="capture-1",
            source_row=2,
            site="Market Wire",
            source_url="https://example.test/qqq",
            symbol="QQQ",
            headline="QQQ naive publication",
            body_text="private QQQ body",
            raw="2026-08-20T18:00:00",
            normalized=None,
            precision="datetime_naive",
            offset_status="unknown",
        )
        _insert_article(
            self.store_map,
            article_id="article-spy",
            version_id="version-spy-1",
            capture_id="capture-3",
            source_row=1,
            site="Index News",
            source_url="https://example.test/spy",
            symbol="SPY",
            headline="SPY publication missing",
            body_text="private SPY body",
            raw=None,
            normalized=None,
            precision="missing",
            offset_status="missing",
        )
        self.repository = CurrentNewsRepository(self.store_map)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_latest_search_orders_filters_and_omits_private_body(self) -> None:
        before = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)
        selection = self.repository.search(CurrentNewsQuery(limit=10))
        after = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)

        self.assertEqual(before, after)
        self.assertEqual(selection.total_selected_count, 4)
        self.assertFalse(selection.truncated)
        self.assertEqual(
            [record["article_id"] for record in selection.records],
            ["article-msft", "article-spy", "article-aapl", "article-qqq"],
        )
        aapl = next(
            record for record in selection.records if record["article_id"] == "article-aapl"
        )
        self.assertEqual(aapl["article_version_id"], "version-aapl-2")
        self.assertEqual(aapl["published_precision"], "date")
        self.assertNotIn("body_text", aapl)
        self.assertNotIn("image_url", aapl)

        filtered = self.repository.search(
            CurrentNewsQuery(query="GLOBAL", symbols=("msft",), limit=10)
        )
        self.assertEqual(
            [(record["symbol"], record["headline"]) for record in filtered.records],
            [("MSFT", "MSFT late headline")],
        )

    def test_as_of_uses_capture_availability_and_preserves_imprecision(self) -> None:
        selection = self.repository.search(
            CurrentNewsQuery(
                mode="as_of",
                as_of="2026-08-20T11:00:00Z",
                limit=10,
            )
        )
        self.assertEqual(
            [(record["article_id"], record["article_version_id"]) for record in selection.records],
            [("article-aapl", "version-aapl-1"), ("article-qqq", "version-qqq-1")],
        )
        qqq = selection.records[1]
        self.assertEqual(qqq["published_date_raw"], "2026-08-20T18:00:00")
        self.assertIsNone(qqq["published_normalized_at"])
        self.assertEqual(qqq["published_precision"], "datetime_naive")

    def test_provider_date_bounds_and_limit_are_inclusive_and_bounded(self) -> None:
        bounded = self.repository.search(
            CurrentNewsQuery(
                start_date="2026-08-20",
                end_date="2026-08-20",
                limit=2,
            )
        )
        self.assertTrue(bounded.truncated)
        self.assertEqual(bounded.total_selected_count, 3)
        self.assertEqual(len(bounded.records), 2)
        self.assertNotIn("article-spy", [row["article_id"] for row in bounded.records])

        with self.assertRaises(ValidationError):
            CurrentNewsQuery(mode="as_of", as_of=None)
        with self.assertRaises(ValidationError):
            CurrentNewsQuery(start_date="2026-08-21", end_date="2026-08-20")
        with self.assertRaises(ResourceLimitError):
            CurrentNewsQuery(limit=501)


if __name__ == "__main__":
    unittest.main()
