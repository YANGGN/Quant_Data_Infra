from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import Fixture, FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.news.stage4_repository import NewsStage4Query, NewsStage4Repository
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class _FixtureManifest:
    """Tiny adapter for deliberately malformed fixture rejection cases only."""

    def __init__(self, fixtures: tuple[Fixture, ...]) -> None:
        self._fixtures = {fixture.id: fixture for fixture in fixtures}

    def get(self, fixture_id: str) -> Fixture:
        try:
            return self._fixtures[fixture_id]
        except KeyError as exc:
            raise ValidationError("Unknown fixture ID") from exc


class Stage4NewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self._temporary.name)
        self.store_map = _temporary_store_map(root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(
            MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self.importer = NewsStage4FixtureImporter(self.store_map, self.manifest)
        self.repository = NewsStage4Repository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _query(
        *,
        source_item_id: str | None = None,
        as_of: str | None = None,
        include_retracted: bool = False,
    ) -> NewsStage4Query:
        return NewsStage4Query(
            source_name="fixture_wire",
            source_item_id=source_item_id,
            as_of=as_of,
            limit=100,
            include_retracted=include_retracted,
        )

    def _import(self, *fixture_ids: str) -> None:
        for fixture_id in fixture_ids:
            receipt = self.importer.import_fixture(fixture_id)
            self.assertEqual(receipt.outcome, "succeeded")
            self.assertGreater(receipt.written_count, 0)

    def _mutated_fixture(
        self,
        fixture_id: str,
        *,
        source_id: str,
        mutate: Any,
    ) -> Fixture:
        source = self.manifest.get(source_id)
        payload = json.loads(source.bytes)
        mutate(payload)
        content = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        return replace(
            source,
            id=fixture_id,
            bytes=content,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
            expected_semantic_identity="0" * 64,
        )

    def test_initial_evidence_and_exact_replay_are_total_no_write(self) -> None:
        before = mutation_fingerprint(self.store_map)
        receipt = self.importer.import_fixture("news_initial")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertGreater(receipt.written_count, 0)
        after_initial = mutation_fingerprint(self.store_map)
        self.assertNotEqual(before["sha256"], after_initial["sha256"])

        replay = self.importer.import_fixture("news_initial")
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(after_initial, mutation_fingerprint(self.store_map))

        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_source_artifacts").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_item_versions").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_item_versions_fts").fetchone()[0],
                1,
            )

    def test_changed_content_appends_and_later_capture_respects_as_of(self) -> None:
        self._import(
            "news_initial",
            "news_content_correction",
            "news_later_capture",
        )

        latest = self.repository.get_items(self._query(source_item_id="nw-100"))
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["version_sequence"], 3)
        self.assertEqual(
            latest[0]["headline"],
            "Northstar expands platform with point-in-time controls",
        )
        self.assertEqual(latest[0]["available_at"], "2026-06-10T16:00:00Z")
        self.assertEqual(
            latest[0]["labels"]["topics"][0]["topic"],
            "data_governance",
        )

        before_correction = self.repository.get_items(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-03T15:59:59Z",
            )
        )
        self.assertEqual(before_correction[0]["version_sequence"], 1)
        at_correction = self.repository.get_items(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-03T16:00:00Z",
            )
        )
        self.assertEqual(at_correction[0]["version_sequence"], 2)
        before_later_capture = self.repository.get_items(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-09T23:00:00Z",
            )
        )
        self.assertEqual(before_later_capture[0]["version_sequence"], 2)

        latest_search = self.repository.search(
            self._query(source_item_id="nw-100"),
            "Northstar",
        )
        self.assertEqual([item["version_sequence"] for item in latest_search], [3])
        historical_search = self.repository.search(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-05T00:00:00Z",
            ),
            "Northstar",
        )
        self.assertEqual([item["version_sequence"] for item in historical_search], [2])

        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            rows = connection.execute(
                """
                SELECT version_sequence, supersedes_news_item_version_id
                FROM news_item_versions
                ORDER BY version_sequence
                """
            ).fetchall()
        self.assertEqual([row["version_sequence"] for row in rows], [1, 2, 3])
        self.assertIsNone(rows[0]["supersedes_news_item_version_id"])
        self.assertIsNotNone(rows[1]["supersedes_news_item_version_id"])
        self.assertIsNotNone(rows[2]["supersedes_news_item_version_id"])

    def test_retraction_and_explicit_missing_content_remain_auditable(self) -> None:
        self._import(
            "news_initial",
            "news_content_correction",
            "news_later_capture",
            "news_retraction",
            "news_missing_content",
        )
        latest = self.repository.get_items(self._query())
        self.assertEqual([item["source_item_id"] for item in latest], ["nw-200"])
        missing = latest[0]
        self.assertEqual(missing["content_state"], "missing")
        self.assertEqual(missing["content_missing_reason"], "provider_metadata_only")
        self.assertEqual(
            missing["labels"]["symbols"][0]["association_state"],
            "missing",
        )
        self.assertEqual(
            missing["labels"]["topics"][0]["missing_reason"],
            "not_classified",
        )

        historical = self.repository.get_items(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-15T00:00:00Z",
            )
        )
        self.assertEqual(historical[0]["version_sequence"], 3)
        self.assertEqual(historical[0]["item_state"], "active")

        retracted = self.repository.get_items(
            self._query(source_item_id="nw-100", include_retracted=True)
        )
        self.assertEqual(retracted[0]["version_sequence"], 4)
        self.assertEqual(retracted[0]["item_state"], "retracted")
        self.assertEqual(retracted[0]["content_state"], "redacted")
        self.assertEqual(retracted[0]["retraction_reason"], "publisher_retraction")
        self.assertEqual(
            self.repository.search(self._query(source_item_id="nw-100"), "Northstar"),
            (),
        )

        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_item_versions").fetchone()[0],
                5,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM news_item_versions_fts").fetchone()[0],
                3,
            )

    def test_malformed_content_and_ambiguous_labels_fail_before_any_write(self) -> None:
        malformed_content = self._mutated_fixture(
            "malformed_content",
            source_id="news_initial",
            mutate=lambda payload: payload["items"][0]["content"].update(
                {"headline": None, "body": None, "summary": None}
            ),
        )
        ambiguous_labels = self._mutated_fixture(
            "ambiguous_labels",
            source_id="news_initial",
            mutate=lambda payload: payload["items"][0]["labels"]["topics"].append(
                {
                    "state": "present",
                    "topic": "technology",
                    "confidence": "0.70",
                    "missing_reason": None,
                }
            ),
        )
        importer = NewsStage4FixtureImporter(
            self.store_map,
            _FixtureManifest((malformed_content, ambiguous_labels)),
        )
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            importer.import_fixture("malformed_content")
        self.assertEqual(before, mutation_fingerprint(self.store_map))
        with self.assertRaises(ValidationError):
            importer.import_fixture("ambiguous_labels")
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_read_and_safe_search_rejections_do_not_mutate(self) -> None:
        self._import(
            "news_initial",
            "news_content_correction",
            "news_later_capture",
            "news_retraction",
            "news_missing_content",
        )
        before = mutation_fingerprint(self.store_map)
        self.repository.get_items(self._query())
        self.repository.get_items(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-15T00:00:00Z",
            )
        )
        self.repository.search(
            self._query(
                source_item_id="nw-100",
                as_of="2026-06-15T00:00:00Z",
            ),
            "Northstar",
        )
        with self.assertRaises(ValidationError):
            self.repository.search(self._query(), "Northstar*")
        self.assertEqual(before, mutation_fingerprint(self.store_map))


if __name__ == "__main__":
    unittest.main()
