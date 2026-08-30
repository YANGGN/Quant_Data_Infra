from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.migrations import migrate_and_register_store
from quant_data.news.current_multi_source import (
    CURRENT_MULTI_SOURCE_MAX_BYTES,
    CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS,
    CapturedCurrentMultiSourceResponse,
    CurrentMultiSourceCredentials,
    CurrentMultiSourceImporter,
    CurrentMultiSourceRequest,
    FMP_GENERAL_URL,
    FMP_PRESS_RELEASES_URL,
)
from quant_data.news.current_multi_source_repository import (
    CurrentMultiSourceNewsRepository,
)
from quant_data.news.current_repository import CurrentNewsQuery
from quant_data.registry import (
    DatasetDeclaration,
    MigrationDeclaration,
    Registry,
    StoreDeclaration,
)
from quant_data.stores import StoreMap, StoreRole, writer_connection


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
        return DatasetDeclaration(
            id=str(value["id"]),
            version=str(value["version"]),
            store=str(value["store"]),
            layer=str(value["layer"]),
            relations=tuple(
                str(item["name"]) for item in physical["relations"]
            ),
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
        registry_version="current-multi-source-repository-test",
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

class _Transport:
    def __init__(self, *, url: str, body: bytes) -> None:
        self.url = url
        self.body = body

    def get(self, **kwargs: object) -> CapturedCurrentMultiSourceResponse:
        if kwargs["url"] != self.url:
            raise AssertionError("fixed news URL drifted")
        if kwargs["timeout_seconds"] != CURRENT_MULTI_SOURCE_TIMEOUT_SECONDS:
            raise AssertionError("news timeout drifted")
        if kwargs["max_bytes"] != CURRENT_MULTI_SOURCE_MAX_BYTES:
            raise AssertionError("news byte bound drifted")
        return CapturedCurrentMultiSourceResponse(
            200,
            "application/json",
            self.body,
        )


class CurrentMultiSourceNewsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.stores = _stores(Path(self.temporary.name))
        self.registry = _fixture_registry()
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.NEWS,
            applied_at="2026-08-30T15:00:00Z",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish(
        self,
        *,
        feed_id: str,
        url: str,
        article_id: str,
        headline: str,
        clock: datetime,
    ) -> None:
        body = dumps_strict(
            [
                {
                    "id": article_id,
                    "title": headline,
                    "text": f"{headline} summary",
                    "site": "Example",
                    "url": f"https://example.test/{article_id}",
                    "publishedDate": "2026-08-30T10:00:00Z",
                    "symbol": "AAPL",
                }
            ]
        ).encode("utf-8")
        importer = CurrentMultiSourceImporter(
            self.stores,
            self.registry,
            clock=lambda: clock,
        )
        importer.run_once(
            request=CurrentMultiSourceRequest(
                feed_id=feed_id,
                poll_slot=clock,
            ),
            credentials=CurrentMultiSourceCredentials(fmp_api_key="offline-key"),
            transport=_Transport(url=url, body=body),
        )
        with writer_connection(self.stores, StoreRole.NEWS) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def test_source_filter_precedes_limit_and_total_count(self) -> None:
        self._publish(
            feed_id="fmp_press_releases",
            url=FMP_PRESS_RELEASES_URL,
            article_id="press-1",
            headline="Press release",
            clock=FIXED_TIME,
        )
        self._publish(
            feed_id="fmp_general",
            url=FMP_GENERAL_URL,
            article_id="general-1",
            headline="General news",
            clock=FIXED_TIME + timedelta(hours=1),
        )
        repository = CurrentMultiSourceNewsRepository(
            self.stores,
            self.registry,
        )
        query = CurrentNewsQuery(limit=1)

        filtered = repository.search(
            query,
            source_ids=("fmp_press_releases",),
        )
        unfiltered = repository.search(query)

        self.assertEqual(filtered.total_selected_count, 1)
        self.assertFalse(filtered.truncated)
        self.assertEqual(
            tuple(record["feed_id"] for record in filtered.records),
            ("fmp_press_releases",),
        )
        self.assertEqual(unfiltered.total_selected_count, 2)
        self.assertTrue(unfiltered.truncated)
        self.assertNotEqual(filtered.receipt_sha256, unfiltered.receipt_sha256)

    def test_source_filter_validates_the_fixed_feed_catalog(self) -> None:
        repository = CurrentMultiSourceNewsRepository(
            self.stores,
            self.registry,
        )
        query = CurrentNewsQuery()

        with self.assertRaises(ValidationError):
            repository.search(query, source_ids=("not_a_fixed_feed",))
        with self.assertRaises(ValidationError):
            repository.search(
                query,
                source_ids=("fmp_general", "fmp_general"),
            )
        with self.assertRaises(ValidationError):
            repository.search(query, source_ids=["fmp_general"])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
