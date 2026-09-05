from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.migrations import migrate_and_register_store
from quant_data.news.tool_repository import (
    CURRENT_NEWS_TOOL_SOURCE_IDS,
    CurrentNewsToolRepository,
)
from quant_data.registry import (
    DatasetDeclaration,
    MigrationDeclaration,
    Registry,
    StoreDeclaration,
)
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


def _digest(value: str | bytes) -> str:
    encoded = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fixture_registry() -> Registry:
    """Use only the news declarations, independent of changing tool metadata."""

    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    store = next(item for item in raw["stores"] if item["id"] == "news")

    def dataset(value: dict[str, object]) -> DatasetDeclaration:
        physical = dict(value["physical"])
        return DatasetDeclaration(
            id=str(value["id"]),
            version=str(value["version"]),
            store=str(value["store"]),
            layer=str(value["layer"]),
            relations=tuple(str(item["name"]) for item in physical["relations"]),
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

    return Registry(
        schema_id=str(raw["schema_id"]),
        schema_version=str(raw["schema_version"]),
        registry_version="current-news-tool-repository-test",
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
        migrations=tuple(
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
        ),
        datasets=tuple(
            dataset(item) for item in raw["datasets"] if item["store"] == "news"
        ),
        collectors=(),
        jobs=(),
        exports=(),
        tools=(),
        tool_version_policies=(),
        dashboard=(),
        raw={},
    )


def _insert_fmp_success(
    connection: object,
    *,
    suffix: str,
    captured_at: str,
    provider_rows: int,
    accepted_rows: int,
    rejected_rows: int,
) -> tuple[str, str]:
    attempt_id = f"fmp-attempt-{suffix}"
    outcome_id = f"fmp-outcome-{suffix}"
    capture_id = f"fmp-capture-{suffix}"
    response = b"[private-fmp-response]"
    scope_sha = _digest(f"fmp-scope-{suffix}")
    response_sha = _digest(response)
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_attempts (
            attempt_id, dataset_id, collector_id, profile_id, poll_slot,
            request_scope_json, request_scope_sha256,
            intent_recorded_at, intent_recorded_precision
        ) VALUES (?, 'news.fmp.stock_latest_current_evidence',
                  'fmp.news.stock_latest_current',
                  'fmp.stock_latest.page0.limit1000.current.v1', ?, '{}', ?, ?,
                  'datetime')
        """,
        (attempt_id, captured_at, scope_sha, captured_at),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_outcomes (
            outcome_id, attempt_id, outcome_kind, response_sha256,
            response_byte_count, http_status, content_type,
            provider_row_count, accepted_row_count, rejected_row_count,
            recorded_at, recorded_precision
        ) VALUES (?, ?, 'succeeded', ?, ?, 200, 'application/json', ?, ?, ?, ?,
                  'datetime')
        """,
        (
            outcome_id,
            attempt_id,
            response_sha,
            len(response),
            provider_rows,
            accepted_rows,
            rejected_rows,
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
        ) VALUES (?, 'news.fmp.stock_latest_current_evidence', ?, ?, ?, ?, ?,
                  200, 'application/json', ?, 'partial', ?, 'datetime', ?, ?, ?,
                  'fmp.news.stock_latest.current.v1')
        """,
        (
            capture_id,
            attempt_id,
            outcome_id,
            scope_sha,
            response_sha,
            response,
            _digest(f"fmp-semantic-{suffix}"),
            captured_at,
            provider_rows,
            accepted_rows,
            rejected_rows,
        ),
    )
    return capture_id, outcome_id


def _insert_fmp_failure(connection: object, *, captured_at: str) -> None:
    attempt_id = "fmp-attempt-failure"
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_attempts (
            attempt_id, dataset_id, collector_id, profile_id, poll_slot,
            request_scope_json, request_scope_sha256,
            intent_recorded_at, intent_recorded_precision
        ) VALUES (?, 'news.fmp.stock_latest_current_evidence',
                  'fmp.news.stock_latest_current',
                  'fmp.stock_latest.page0.limit1000.current.v1', ?, '{}', ?, ?,
                  'datetime')
        """,
        (attempt_id, captured_at, _digest("fmp-failure-scope"), captured_at),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_outcomes (
            outcome_id, attempt_id, outcome_kind, recorded_at, recorded_precision
        ) VALUES ('fmp-outcome-failure', ?, 'request_failed', ?, 'datetime')
        """,
        (attempt_id, captured_at),
    )


def _insert_fmp_versions(
    connection: object,
    *,
    first_capture_id: str,
    corrected_capture_id: str,
    repeated_capture_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_articles (
            article_id, provider_namespace, site, source_url, symbol,
            created_capture_id
        ) VALUES ('article-fmp', 'fmp.news.stock_latest_current', 'FMP Wire',
                  'https://example.test/fmp/article', 'AAPL', ?)
        """,
        (first_capture_id,),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_article_versions (
            article_version_id, article_id, mutable_content_sha256, title,
            body_text, image_url, published_date_raw, published_normalized_at,
            published_precision, published_offset_status, version_sequence,
            supersedes_article_version_id, capture_id, source_row
        ) VALUES ('fmp-version-1', 'article-fmp', ?, 'Initial FMP headline',
                  'private FMP body text', 'https://example.test/private-image.png',
                  '2026-08-30T09:00:00Z', '2026-08-30T09:00:00Z',
                  'datetime_offset', 'known', 1, NULL, ?, 1)
        """,
        (_digest("fmp-version-1"), first_capture_id),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_capture_articles (
            capture_id, article_version_id, source_row
        ) VALUES (?, 'fmp-version-1', 1)
        """,
        (first_capture_id,),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_article_versions (
            article_version_id, article_id, mutable_content_sha256, title,
            body_text, image_url, published_date_raw, published_normalized_at,
            published_precision, published_offset_status, version_sequence,
            supersedes_article_version_id, capture_id, source_row
        ) VALUES ('fmp-version-2', 'article-fmp', ?, 'Corrected FMP headline',
                  'new private FMP body text', 'https://example.test/new-image.png',
                  '2026-08-30', NULL, 'date', 'unknown', 2, 'fmp-version-1', ?, 1)
        """,
        (_digest("fmp-version-2"), corrected_capture_id),
    )
    connection.execute(
        """
        INSERT INTO fmp_stock_latest_current_capture_articles (
            capture_id, article_version_id, source_row
        ) VALUES (?, 'fmp-version-2', 1), (?, 'fmp-version-2', 1)
        """,
        (corrected_capture_id, repeated_capture_id),
    )


def _insert_multi_source_version(connection: object) -> None:
    response = b"<private-fed-rss-response />"
    scope_sha = _digest("fed-scope")
    response_sha = _digest(response)
    connection.execute(
        """
        INSERT INTO current_multi_source_attempts (
            attempt_id, dataset_id, collector_id, feed_id, profile_id, poll_slot,
            request_scope_json, request_scope_sha256,
            intent_recorded_at, intent_recorded_precision
        ) VALUES ('fed-attempt-1', 'news.current_multi_source_evidence',
                  'news.current_multi_source', 'fed_press',
                  'rss.fed.press.current.v1', '2026-08-30T11:00:00Z', '{}', ?,
                  '2026-08-30T11:00:00Z', 'datetime')
        """,
        (scope_sha,),
    )
    connection.execute(
        """
        INSERT INTO current_multi_source_outcomes (
            outcome_id, attempt_id, outcome_kind, response_sha256,
            response_byte_count, http_status, content_type,
            provider_row_count, accepted_row_count, rejected_row_count,
            recorded_at, recorded_precision
        ) VALUES ('fed-outcome-1', 'fed-attempt-1', 'succeeded', ?, ?, 200,
                  'application/rss+xml', 2, 2, 0, '2026-08-30T11:00:00Z',
                  'datetime')
        """,
        (response_sha, len(response)),
    )
    connection.execute(
        """
        INSERT INTO current_multi_source_captures (
            capture_id, dataset_id, attempt_id, outcome_id, feed_id,
            request_scope_sha256, response_sha256, response_bytes, http_status,
            content_type, semantic_identity, completeness, captured_at,
            captured_precision, provider_row_count, accepted_row_count,
            rejected_row_count, normalization_version
        ) VALUES ('fed-capture-1', 'news.current_multi_source_evidence',
                  'fed-attempt-1', 'fed-outcome-1', 'fed_press', ?, ?, ?, 200,
                  'application/rss+xml', ?, 'partial', '2026-08-30T11:00:00Z',
                  'datetime', 2, 2, 0, 'news.current_multi_source.v1')
        """,
        (scope_sha, response_sha, response, _digest("fed-semantic")),
    )
    connection.execute(
        """
        INSERT INTO current_multi_source_articles (
            article_id, feed_id, provider, source_item_key, created_capture_id
        ) VALUES ('article-fed', 'fed_press', 'fed', 'id:fed-1', 'fed-capture-1')
        """
    )
    connection.execute(
        """
        INSERT INTO current_multi_source_article_versions (
            article_version_id, article_id, mutable_content_sha256, title, summary,
            site, source_url, published_date_raw, published_normalized_at,
            published_precision, published_offset_status, version_sequence,
            supersedes_article_version_id, capture_id, source_row
        ) VALUES ('fed-version-1', 'article-fed', ?, 'Federal safe headline',
                  'Federal safe summary', 'Federal Reserve',
                  'https://example.test/fed/article', '2026-08-30T08:00:00Z',
                  '2026-08-30T08:00:00Z', 'datetime_offset', 'known', 1, NULL,
                  'fed-capture-1', 1)
        """,
        (_digest("fed-version-1"),),
    )
    connection.execute(
        """
        INSERT INTO current_multi_source_capture_articles (
            capture_id, article_version_id, source_row
        ) VALUES ('fed-capture-1', 'fed-version-1', 1)
        """
    )
    connection.executemany(
        """
        INSERT INTO current_multi_source_article_symbols (
            article_version_id, provider_symbol
        ) VALUES ('fed-version-1', ?)
        """,
        (("SPY",), ("TLT",)),
    )


class CurrentNewsToolRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _stores(Path(self.temporary.name))
        self.registry = _fixture_registry()
        migrate_and_register_store(
            self.store_map,
            self.registry,
            StoreRole.NEWS,
            applied_at="2026-08-30T08:00:00Z",
        )
        with writer_connection(self.store_map, StoreRole.NEWS) as connection:
            capture_1, _ = _insert_fmp_success(
                connection,
                suffix="1",
                captured_at="2026-08-30T09:00:00Z",
                provider_rows=4,
                accepted_rows=3,
                rejected_rows=1,
            )
            capture_2, _ = _insert_fmp_success(
                connection,
                suffix="2",
                captured_at="2026-08-30T09:30:00Z",
                provider_rows=4,
                accepted_rows=4,
                rejected_rows=0,
            )
            capture_3, _ = _insert_fmp_success(
                connection,
                suffix="3",
                captured_at="2026-08-30T09:40:00Z",
                provider_rows=4,
                accepted_rows=3,
                rejected_rows=1,
            )
            _insert_fmp_versions(
                connection,
                first_capture_id=capture_1,
                corrected_capture_id=capture_2,
                repeated_capture_id=capture_3,
            )
            _insert_fmp_failure(
                connection,
                captured_at="2026-08-30T10:00:00Z",
            )
            _insert_multi_source_version(connection)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.repository = CurrentNewsToolRepository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_source_status_is_fixed_safe_and_distinguishes_capture(self) -> None:
        before = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)
        statuses = self.repository.source_status()
        filtered = self.repository.source_status(("fed_press", "fmp_stock_latest"))
        after = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)

        self.assertEqual(before, after)
        self.assertEqual(
            tuple(item["source_id"] for item in statuses),
            CURRENT_NEWS_TOOL_SOURCE_IDS,
        )
        self.assertEqual(
            tuple(item["source_id"] for item in filtered),
            ("fmp_stock_latest", "fed_press"),
        )
        fmp = next(item for item in statuses if item["source_id"] == "fmp_stock_latest")
        self.assertEqual(fmp["status"], "request_failed")
        self.assertFalse(fmp["no_retained_outcome"])
        self.assertEqual(fmp["outcome_count"], 4)
        self.assertEqual(fmp["capture_count"], 3)
        self.assertEqual(fmp["article_count"], 1)
        self.assertEqual(fmp["article_version_count"], 2)
        self.assertEqual(fmp["accepted_row_count_total"], 10)
        self.assertEqual(fmp["rejected_row_count_total"], 2)
        self.assertEqual(fmp["latest_outcome"]["outcome_id"], "fmp-outcome-failure")
        self.assertEqual(fmp["latest_outcome"]["recorded_at"], "2026-08-30T10:00:00Z")
        self.assertEqual(
            fmp["latest_successful_capture"]["capture_id"],
            "fmp-capture-3",
        )
        self.assertEqual(
            fmp["latest_successful_capture"]["accepted_row_count"],
            3,
        )

        empty = next(item for item in statuses if item["source_id"] == "fmp_general")
        self.assertEqual(empty["status"], "no_retained_outcome")
        self.assertTrue(empty["no_retained_outcome"])
        self.assertIsNone(empty["latest_outcome"])
        self.assertIsNone(empty["latest_successful_capture"])
        self.assertEqual(empty["article_count"], 0)

    def test_history_and_single_version_project_only_safe_lineage(self) -> None:
        before = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)
        history = self.repository.item_history("article-fmp", 10)
        single = self.repository.article_version("fmp-version-2")
        generic_history = self.repository.item_history("article-fed", 10)
        generic = self.repository.article_version("fed-version-1")
        after = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)

        self.assertEqual(before, after)
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(history["source_id"], "fmp_stock_latest")
        self.assertEqual(history["total_version_count"], 2)
        self.assertFalse(history["truncated"])
        versions = history["versions"]
        self.assertEqual(
            tuple(item["article_version_id"] for item in versions),
            ("fmp-version-1", "fmp-version-2"),
        )
        second = versions[1]
        self.assertEqual(second["feed_id"], "fmp_stock_latest")
        self.assertEqual(second["provider"], "fmp")
        self.assertEqual(second["summary"], "")
        self.assertEqual(second["symbols"], ("AAPL",))
        self.assertEqual(second["available_at"], "2026-08-30T09:30:00Z")
        self.assertEqual(second["published_normalized_at"], None)
        self.assertEqual(second["published_precision"], "date")
        self.assertEqual(second["capture_id"], "fmp-capture-2")
        self.assertEqual(
            second["capture_membership_ids"],
            ("fmp-capture-2", "fmp-capture-3"),
        )
        self.assertEqual(second["supersedes_article_version_id"], "fmp-version-1")
        self.assertEqual(single, second)

        rendered = dumps_strict(history)
        self.assertNotIn("body_text", second)
        self.assertNotIn("image_url", second)
        self.assertNotIn("private FMP body text", rendered)
        self.assertNotIn("private-image.png", rendered)
        self.assertNotIn("private-fmp-response", rendered)

        self.assertIsNotNone(generic)
        assert generic is not None
        self.assertIsNotNone(generic_history)
        assert generic_history is not None
        self.assertEqual(generic_history["versions"], (generic,))
        self.assertEqual(generic["feed_id"], "fed_press")
        self.assertEqual(generic["provider"], "fed")
        self.assertEqual(generic["summary"], "Federal safe summary")
        self.assertEqual(generic["symbols"], ("SPY", "TLT"))
        self.assertEqual(generic["capture_membership_ids"], ("fed-capture-1",))

    def test_bounds_identifiers_and_dataset_registration_are_enforced(self) -> None:
        truncated = self.repository.item_history("article-fmp", 1)
        self.assertIsNotNone(truncated)
        assert truncated is not None
        self.assertTrue(truncated["truncated"])
        self.assertEqual(truncated["total_version_count"], 2)
        self.assertEqual(
            tuple(item["article_version_id"] for item in truncated["versions"]),
            ("fmp-version-1",),
        )
        self.assertIsNone(self.repository.item_history("missing-article", 1))
        self.assertIsNone(self.repository.article_version("missing-version"))

        with self.assertRaises(ValidationError):
            self.repository.source_status(("unknown_source",))
        with self.assertRaises(ValidationError):
            self.repository.source_status(("fed_press", "fed_press"))
        with self.assertRaises(ValidationError):
            self.repository.source_status(["fed_press"])  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            self.repository.article_version("   ")
        with self.assertRaises(ResourceLimitError):
            self.repository.item_history("article-fmp", 501)
        with self.assertRaises(ResourceLimitError):
            self.repository.item_history("article-fmp", 0)
        with self.assertRaises(ValidationError):
            CurrentNewsToolRepository(
                self.store_map,
                replace(self.registry, datasets=()),
            )


if __name__ == "__main__":
    unittest.main()
