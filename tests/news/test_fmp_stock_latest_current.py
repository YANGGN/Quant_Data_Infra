from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.errors import ConflictError, StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.migrations import migrate_and_register_store
from quant_data.news.fmp_stock_latest_current import (
    CapturedFmpStockLatestCurrentResponse,
    FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
    FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_LIMIT,
    FMP_STOCK_LATEST_CURRENT_MAX_BYTES,
    FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
    FMP_STOCK_LATEST_CURRENT_PATH,
    FmpStockLatestCurrentImporter,
    FmpStockLatestCurrentRequest,
)
from quant_data.registry import (
    DatasetDeclaration,
    MigrationDeclaration,
    StoreDeclaration,
    load_registry,
)
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
MIGRATION_PATH = PROJECT_ROOT / "quant_data/migrations/news/0006_fmp_stock_latest_current.sql"
FIXED_TIME = datetime(2026, 8, 29, 15, 32, tzinfo=timezone.utc)


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _dataset(
    dataset_id: str,
    *,
    layer: str,
    relations: tuple[str, ...],
) -> DatasetDeclaration:
    return DatasetDeclaration(
        id=dataset_id,
        version="1.0.0",
        store=StoreRole.NEWS.value,
        layer=layer,
        relations=relations,
        physical={"relations": relations},
        identity={"provider": "fmp", "dataset": dataset_id},
        temporal={"availability_basis": "local_capture"},
        revision_policy="append_only",
        freshness={"cadence": "hourly"},
        quality_contract={"mode": "validated_rows"},
        collector_ids=(FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,),
        tool_ids=(),
        dashboard_ids=(),
        export_ids=(),
        active=True,
    )


def _successor_registry():
    base = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
    if any(item.id == FMP_STOCK_LATEST_CURRENT_MIGRATION_ID for item in base.migrations):
        return base
    resource = "quant_data/migrations/news/0006_fmp_stock_latest_current.sql"
    migration = MigrationDeclaration(
        id=FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
        store=StoreRole.NEWS.value,
        ordinal=6,
        resource=resource,
        sha256=hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest(),
        semantic_scope="repeatable current FMP stock-latest news page",
        dependencies=("news:0005_fmp_stock_latest",),
        reconstruction_state="fixture_validated",
    )
    evidence = _dataset(
        FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
        layer="evidence",
        relations=(
            "fmp_stock_latest_current_attempts",
            "fmp_stock_latest_current_outcomes",
            "fmp_stock_latest_current_captures",
        ),
    )
    articles = _dataset(
        FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
        layer="canonical",
        relations=(
            "fmp_stock_latest_current_articles",
            "fmp_stock_latest_current_article_versions",
            "fmp_stock_latest_current_capture_articles",
        ),
    )
    collectors = (
        *base.collectors,
        {
            "id": FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID,
            "handler": "news.fmp_stock_latest_current",
            "network": True,
            "configuration_env": ["FMP_API_KEY"],
            "output_datasets": [
                FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
                FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
            ],
        },
    )
    stores = tuple(
        replace(
            item,
            migration_order=(*item.migration_order, FMP_STOCK_LATEST_CURRENT_MIGRATION_ID),
        )
        if item.id == StoreRole.NEWS.value
        else item
        for item in base.stores
    )
    return replace(
        base,
        registry_version="test-current-news",
        stores=stores,
        migrations=(*base.migrations, migration),
        datasets=(*base.datasets, evidence, articles),
        collectors=collectors,
    )


def _body(*rows: object) -> bytes:
    return dumps_strict(list(rows)).encode("utf-8")


def _row(
    *,
    title: str = "A title",
    text: object = "body",
    site: object = "example.test",
    image: object = "https://example.test/image.jpg",
    published: object = "2026-08-29T15:00:00Z",
) -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "title": title,
        "text": text,
        "site": site,
        "url": "https://example.test/news/aapl",
        "image": image,
        "publishedDate": published,
    }


class _Transport:
    def __init__(self, bodies: list[bytes], news_path: Path) -> None:
        self.bodies = bodies
        self.news_path = news_path
        self.calls = 0
        self.lock_was_available = False

    def get(self, **kwargs: object) -> CapturedFmpStockLatestCurrentResponse:
        self.calls += 1
        if kwargs != {
            "path": FMP_STOCK_LATEST_CURRENT_PATH,
            "query": {"page": "0", "limit": "1000"},
            "headers": {"apikey": "offline-key", "Accept": "application/json"},
            "timeout_seconds": 60,
            "max_bytes": FMP_STOCK_LATEST_CURRENT_MAX_BYTES,
        }:
            raise AssertionError("FMP current stock-latest request drifted")
        with StoreWriteLock(self.news_path, timeout_seconds=0):
            self.lock_was_available = True
        return CapturedFmpStockLatestCurrentResponse(
            200,
            "application/json; charset=utf-8",
            self.bodies[min(self.calls - 1, len(self.bodies) - 1)],
        )


class _RaisingTransport:
    def __init__(self, news_path: Path) -> None:
        self.news_path = news_path
        self.calls = 0
        self.lock_was_available = False

    def get(self, **kwargs: object) -> CapturedFmpStockLatestCurrentResponse:
        del kwargs
        self.calls += 1
        with StoreWriteLock(self.news_path, timeout_seconds=0):
            self.lock_was_available = True
        raise RuntimeError("offline-key must not escape")


class FmpStockLatestCurrentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = _stores(self.root)
        self.registry = _successor_registry()
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.NEWS,
            applied_at="2026-08-29T15:00:00Z",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _importer(self, clock: datetime = FIXED_TIME) -> FmpStockLatestCurrentImporter:
        return FmpStockLatestCurrentImporter(
            self.stores,
            self.registry,
            clock=lambda: clock,
        )

    def test_accepts_usable_rows_derives_site_and_retains_partial_counts(self) -> None:
        body = _body(
            _row(text=None, site=None, image="not-a-url", published=None),
            {"symbol": "", "title": "bad", "url": "https://example.test/bad"},
        )
        transport = _Transport([body], self.stores.news)
        receipt = self._importer().run_once(api_key="offline-key", transport=transport)

        self.assertEqual(
            (
                receipt.outcome,
                receipt.poll_slot,
                receipt.provider_row_count,
                receipt.accepted_row_count,
                receipt.rejected_row_count,
            ),
            ("succeeded", "2026-08-29T15:00:00Z", 2, 1, 1),
        )
        self.assertEqual(transport.calls, 1)
        self.assertTrue(transport.lock_was_available)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            capture = connection.execute(
                """
                SELECT response_bytes, response_sha256, completeness,
                       provider_row_count, accepted_row_count, rejected_row_count
                FROM fmp_stock_latest_current_captures
                """
            ).fetchone()
            version = connection.execute(
                """
                SELECT site, body_text, image_url, published_date_raw,
                       published_precision, published_offset_status
                FROM fmp_stock_latest_current_articles AS article
                JOIN fmp_stock_latest_current_article_versions AS version
                  ON version.article_id=article.article_id
                """
            ).fetchone()
        self.assertEqual(bytes(capture["response_bytes"]), body)
        self.assertEqual(capture["response_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(
            tuple(capture[2:]),
            ("partial", 2, 1, 1),
        )
        self.assertEqual(
            tuple(version),
            ("example.test", "", None, None, "missing", "missing"),
        )

    def test_same_hour_is_permanently_single_attempt_with_no_second_request(self) -> None:
        transport = _Transport([_body(_row())], self.stores.news)
        importer = self._importer()
        first = importer.run_once(api_key="offline-key", transport=transport)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            before = tuple(
                connection.execute(
                    """
                    SELECT count(*) FROM fmp_stock_latest_current_attempts
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_outcomes
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_captures
                    """
                )
            )
        with self.assertRaises(ConflictError):
            importer.run_once(api_key="offline-key", transport=transport)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            after = tuple(
                connection.execute(
                    """
                    SELECT count(*) FROM fmp_stock_latest_current_attempts
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_outcomes
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_captures
                    """
                )
            )
        self.assertEqual(first.outcome, "succeeded")
        self.assertEqual(before, after)
        self.assertEqual(transport.calls, 1)

    def test_next_hour_retains_a_new_capture_without_duplicating_unchanged_version(self) -> None:
        body = _body(_row())
        first_transport = _Transport([body], self.stores.news)
        second_transport = _Transport([body], self.stores.news)
        self._importer(FIXED_TIME).run_once(api_key="offline-key", transport=first_transport)
        self._importer(FIXED_TIME + timedelta(hours=1)).run_once(
            api_key="offline-key",
            transport=second_transport,
        )
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            counts = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT count(*) FROM fmp_stock_latest_current_captures
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_articles
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_article_versions
                    UNION ALL SELECT count(*) FROM fmp_stock_latest_current_capture_articles
                    """
                )
            )
        self.assertEqual(counts, (2, 1, 1, 2))

    def test_changed_article_content_appends_one_version(self) -> None:
        first = _Transport([_body(_row(title="first"))], self.stores.news)
        second = _Transport([_body(_row(title="corrected"))], self.stores.news)
        self._importer(FIXED_TIME).run_once(api_key="offline-key", transport=first)
        self._importer(FIXED_TIME + timedelta(hours=1)).run_once(
            api_key="offline-key",
            transport=second,
        )
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            versions = tuple(
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT version_sequence, title, supersedes_article_version_id IS NOT NULL
                    FROM fmp_stock_latest_current_article_versions
                    ORDER BY version_sequence
                    """
                )
            )
        self.assertEqual(versions, ((1, "first", 0), (2, "corrected", 1)))

    def test_nonempty_page_with_no_usable_rows_is_terminal_and_counts_rejections(self) -> None:
        transport = _Transport([_body({"symbol": "AAPL", "title": "", "url": "bad"})], self.stores.news)
        with self.assertRaises(ValidationError):
            self._importer().run_once(api_key="offline-key", transport=transport)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            outcome = connection.execute(
                """
                SELECT outcome_kind, provider_row_count, accepted_row_count, rejected_row_count
                FROM fmp_stock_latest_current_outcomes
                """
            ).fetchone()
            capture_count = connection.execute(
                "SELECT count(*) FROM fmp_stock_latest_current_captures"
            ).fetchone()[0]
        self.assertEqual(tuple(outcome), ("response_rejected", 1, 0, 1))
        self.assertEqual(capture_count, 0)
        with self.assertRaises(ConflictError):
            self._importer().run_once(api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 1)

    def test_transport_failure_records_one_terminal_outcome_and_sanitizes_secret(self) -> None:
        transport = _RaisingTransport(self.stores.news)
        with self.assertRaises(StoreUnavailableError) as rejected:
            self._importer().run_once(api_key="offline-key", transport=transport)
        self.assertNotIn("offline-key", str(rejected.exception))
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)
        self.assertTrue(transport.lock_was_available)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT outcome_kind FROM fmp_stock_latest_current_outcomes"
                ).fetchone()[0],
                "request_failed",
            )
        with self.assertRaises(ConflictError):
            self._importer().run_once(api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 1)

    def test_invalid_credential_creates_no_intent_or_request(self) -> None:
        transport = _Transport([_body(_row())], self.stores.news)
        with self.assertRaises(ValidationError):
            self._importer().run_once(api_key="invalid key", transport=transport)
        self.assertEqual(transport.calls, 0)
        with read_connection(self.stores, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM fmp_stock_latest_current_attempts"
                ).fetchone()[0],
                0,
            )

    def test_registry_preflight_rejects_missing_current_collector(self) -> None:
        invalid = replace(
            self.registry,
            collectors=tuple(
                item
                for item in self.registry.collectors
                if item.get("id") != FMP_STOCK_LATEST_CURRENT_COLLECTOR_ID
            ),
        )
        with self.assertRaises(ValidationError):
            FmpStockLatestCurrentImporter(self.stores, invalid, clock=lambda: FIXED_TIME)

    def test_response_bound_is_closed(self) -> None:
        with self.assertRaises(Exception):
            CapturedFmpStockLatestCurrentResponse(
                200,
                "application/json",
                b"x" * (FMP_STOCK_LATEST_CURRENT_MAX_BYTES + 1),
            )


if __name__ == "__main__":
    unittest.main()
