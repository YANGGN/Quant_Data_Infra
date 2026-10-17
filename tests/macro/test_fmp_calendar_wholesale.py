"""Focused offline tests for compact FMP calendar raw evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.macro.fmp_calendar_wholesale import (
    LEGACY_WHOLESALE_DATASET_ID,
    MAX_RESPONSE_BYTES,
    NORMALIZATION_VERSION,
    PERSISTENCE_CODE_VERSION,
    WHOLESALE_COLLECTOR_ID,
    WHOLESALE_DATASET_ID,
    WHOLESALE_EVENT_DATASET_ID,
    FmpWholesaleCalendarPublisher,
    parse_fmp_us_calendar_wholesale,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import (
    Registry,
    fmp_calendar_incremental_registry_profile,
    load_registry,
)
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_MIGRATION_TIME = "2026-08-24T18:00:00Z"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fixture_registry() -> Registry:
    return load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})


def _row(
    event: str,
    date: str,
    *,
    country: str = "US",
    currency: object = "USD",
    unit: object = "%",
    previous: object = "0.5",
    estimate: object = "1.0",
    actual: object = "1.5",
    **extra: object,
) -> dict[str, object]:
    return {
        "date": date,
        "country": country,
        "event": event,
        "currency": currency,
        "previous": previous,
        "estimate": estimate,
        "actual": actual,
        "unit": unit,
        **extra,
    }


def _body(rows: list[dict[str, object]], *, pretty: bool = False, sort: bool = False) -> bytes:
    if pretty:
        return json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=sort).encode("utf-8")
    return json.dumps(
        rows, ensure_ascii=False, separators=(",", ":"), sort_keys=sort
    ).encode("utf-8")


class FmpWholesaleCalendarParseTests(unittest.TestCase):
    def test_raw_rows_survive_unknown_labels_non_us_rows_and_format_reordering(self) -> None:
        rows = [
            _row(
                "GDP Experimental Alias",
                "2024-04-25 12:30:00",
                provider_extension={"source": "fixture", "revision": 1},
                changePercentage="12.5",
            ),
            _row(
                "Canadian Unreviewed Event",
                "2024-04-25 12:30:00",
                country="CA",
                currency="CAD",
                unit="points",
                provider_extension=["keep", "all", "objects"],
            ),
        ]
        original = parse_fmp_us_calendar_wholesale(
            _body(rows),
            captured_at="2026-08-18T18:00:00Z",
            start_date="2024-04-01",
            end_date="2024-06-30",
        )
        reordered_objects = [dict(reversed(tuple(item.items()))) for item in reversed(rows)]
        reformatted = parse_fmp_us_calendar_wholesale(
            _body(reordered_objects, pretty=True, sort=True),
            captured_at="2026-08-19T18:00:00Z",
            start_date="2024-04-01",
            end_date="2024-06-30",
        )

        self.assertEqual(original.semantic_identity, reformatted.semantic_identity)
        self.assertNotEqual(original.response_sha256, reformatted.response_sha256)
        self.assertEqual(len(original.rows), 2)
        self.assertEqual(original.rows[0].event_name, "GDP Experimental Alias")
        self.assertEqual(original.rows[1].country, "CA")
        self.assertEqual(
            json.loads(original.rows[1].raw_row_json)["provider_extension"],
            ["keep", "all", "objects"],
        )

    def test_empty_array_and_scope_are_valid_but_malformed_rows_are_not(self) -> None:
        empty = parse_fmp_us_calendar_wholesale(
            b"[]",
            captured_at="2026-08-18T18:00:00Z",
            start_date="2024-04-01",
            end_date="2024-06-30",
        )
        self.assertEqual(empty.rows, ())
        self.assertEqual(
            empty.semantic_identity,
            parse_fmp_us_calendar_wholesale(
                b"[ ]",
                captured_at="2026-08-19T18:00:00Z",
                start_date="2024-04-01",
                end_date="2024-06-30",
            ).semantic_identity,
        )
        self.assertNotEqual(
            empty.semantic_identity,
            parse_fmp_us_calendar_wholesale(
                b"[]",
                captured_at="2026-08-18T18:00:00Z",
                start_date="2024-07-01",
                end_date="2024-09-30",
            ).semantic_identity,
        )
        with self.assertRaisesRegex(ValidationError, "row event is invalid"):
            parse_fmp_us_calendar_wholesale(
                _body([{"date": "2024-04-25", "country": "US"}]),
                captured_at="2026-08-18T18:00:00Z",
                start_date="2024-04-01",
                end_date="2024-06-30",
            )
        with self.assertRaisesRegex(ValidationError, "must be an array"):
            parse_fmp_us_calendar_wholesale(
                b"{}",
                captured_at="2026-08-18T18:00:00Z",
                start_date="2024-04-01",
                end_date="2024-06-30",
            )
        with self.assertRaises(ResourceLimitError):
            parse_fmp_us_calendar_wholesale(
                b" " * (MAX_RESPONSE_BYTES + 1),
                captured_at="2026-08-18T18:00:00Z",
                start_date="2024-04-01",
                end_date="2024-06-30",
            )


class FmpWholesaleCalendarPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.stores = _stores(self.root)
        self.registry = _fixture_registry()
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.MACRO,
            applied_at=_MIGRATION_TIME,
        )
        self.publisher = FmpWholesaleCalendarPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )

    def test_publisher_requires_exact_incremental_migration(self) -> None:
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        legacy_stores = _stores(legacy_root)
        migrate_and_register_store(
            legacy_stores,
            fmp_calendar_incremental_registry_profile(self.registry),
            StoreRole.MACRO,
            applied_at=_MIGRATION_TIME,
        )

        with self.assertRaisesRegex(
            ConflictError,
            "migration 0018 is not applied",
        ):
            FmpWholesaleCalendarPublisher(
                macro_store=legacy_stores.macro,
                project_root=legacy_root,
                registry=self.registry,
            )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _capture(
        rows: list[dict[str, object]],
        *,
        captured_at: str = "2026-08-18T18:00:00Z",
        pretty: bool = False,
        sort: bool = False,
        start_date: str = "2024-04-01",
        end_date: str = "2024-06-30",
    ):
        return parse_fmp_us_calendar_wholesale(
            _body(rows, pretty=pretty, sort=sort),
            captured_at=captured_at,
            start_date=start_date,
            end_date=end_date,
        )

    def _counts(self) -> dict[str, int]:
        relations = (
            "fmp_economic_calendar_fetch_receipts",
            "fmp_economic_calendar_raw_events",
            "fmp_economic_calendar_raw_event_versions",
            "fmp_economic_calendar_latest_response_cache",
            "ingestion_runs",
            "ingestion_artifacts",
            "ingestion_snapshots",
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return {
                relation: int(
                    connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
                )
                for relation in relations
            }

    def _cache_row(self):
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return connection.execute(
                """
                SELECT receipt_id, response_bytes, response_sha256,
                       semantic_identity, source_semantic_identity, captured_at
                FROM fmp_economic_calendar_latest_response_cache
                """
            ).fetchone()

    def test_first_material_batch_writes_receipt_event_versions_cache_and_manifest(self) -> None:
        capture = self._capture(
            [
                _row(
                    "GDP Future Alias",
                    "2024-04-25 12:30:00",
                    provider_extension={"nested": [1, 2, 3]},
                    change="0.2",
                    impact="High",
                    changePercentage="20",
                ),
                _row(
                    "Unexpected Canadian Event",
                    "2024-04-25 12:30:00",
                    country="CA",
                    currency="CAD",
                    unit="points",
                    provider_extension=True,
                ),
            ]
        )
        report = self.publisher.publish(capture)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.semantic_identity, capture.semantic_identity)
        self.assertEqual(report.written_captures, 1)
        self.assertEqual(report.written_rows, 2)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            receipt = connection.execute(
                """
                SELECT receipt_id, response_sha256, semantic_identity,
                       source_semantic_identity, row_count, changed_event_count,
                       receipt_manifest_sha256, receipt_manifest_byte_count,
                       normalization_version, persistence_version
                FROM fmp_economic_calendar_fetch_receipts
                """
            ).fetchone()
            artifact = connection.execute(
                """
                SELECT content_sha256, byte_count, dataset_id, normalization_version,
                       source_reference
                FROM ingestion_artifacts WHERE artifact_id=?
                """,
                (report.artifact_id,),
            ).fetchone()
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        cache = self._cache_row()
        self.assertEqual(self._counts(), {
            "fmp_economic_calendar_fetch_receipts": 1,
            "fmp_economic_calendar_raw_events": 2,
            "fmp_economic_calendar_raw_event_versions": 2,
            "fmp_economic_calendar_latest_response_cache": 1,
            "ingestion_runs": 1,
            "ingestion_artifacts": 1,
            "ingestion_snapshots": 1,
        })
        self.assertEqual(receipt["receipt_id"], report.capture_id)
        self.assertEqual(receipt["response_sha256"], capture.response_sha256)
        self.assertEqual(receipt["source_semantic_identity"], capture.semantic_identity)
        self.assertNotEqual(receipt["semantic_identity"], capture.semantic_identity)
        self.assertEqual(
            tuple(receipt)[4:],
            (2, 2, receipt["receipt_manifest_sha256"], receipt["receipt_manifest_byte_count"], NORMALIZATION_VERSION, PERSISTENCE_CODE_VERSION),
        )
        self.assertEqual(bytes(cache["response_bytes"]), capture.response_bytes)
        self.assertEqual(cache["response_sha256"], capture.response_sha256)
        self.assertEqual(cache["source_semantic_identity"], capture.semantic_identity)
        self.assertEqual(cache["semantic_identity"], receipt["semantic_identity"])
        self.assertEqual(cache["receipt_id"], receipt["receipt_id"])
        self.assertEqual(
            tuple(artifact),
            (
                receipt["receipt_manifest_sha256"],
                receipt["receipt_manifest_byte_count"],
                WHOLESALE_DATASET_ID,
                PERSISTENCE_CODE_VERSION,
                "fmp/economic-calendar/us-wholesale-receipt.json",
            ),
        )
        self.assertEqual(self.publisher.completed_windows(), (("2024-04-01", "2024-06-30"),))
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), capture
        )

    def test_exact_semantic_replay_is_a_total_no_write_and_keeps_original_cache(self) -> None:
        rows = [
            _row("Unreviewed One", "2024-04-25 12:30:00", marker="one"),
            _row("Unreviewed Two", "2024-05-15 12:30:00", marker="two"),
        ]
        original = self._capture(rows)
        first = self.publisher.publish(original)
        reformatted = self._capture(
            [dict(reversed(tuple(item.items()))) for item in reversed(rows)],
            captured_at="2026-08-19T18:00:00Z",
            pretty=True,
            sort=True,
        )
        self.assertEqual(original.semantic_identity, reformatted.semantic_identity)
        self.assertNotEqual(original.response_sha256, reformatted.response_sha256)
        before = self._counts()
        cached = self._cache_row()
        replay = self.publisher.publish(reformatted)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.run_id, None)
        self.assertEqual(replay.artifact_id, None)
        self.assertEqual(replay.snapshot_id, None)
        self.assertEqual(replay.written_captures, 0)
        self.assertEqual(replay.written_rows, 0)
        self.assertEqual(replay.capture_id, first.capture_id)
        self.assertEqual(self._counts(), before)
        self.assertEqual(dict(self._cache_row()), dict(cached))
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), original
        )


    def test_one_changed_row_appends_only_one_event_version_and_replaces_cache(self) -> None:
        first = self._capture(
            [
                _row("Unchanged", "2024-04-25 12:30:00", provider_note="same"),
                _row("Changed", "2024-05-15 12:30:00", provider_note="old"),
            ]
        )
        second = self._capture(
            [
                _row("Unchanged", "2024-04-25 12:30:00", provider_note="same"),
                _row("Changed", "2024-05-15 12:30:00", provider_note="new"),
            ],
            captured_at="2026-08-19T18:00:00Z",
        )
        first_report = self.publisher.publish(first)
        report = self.publisher.publish(second)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_captures, 1)
        self.assertEqual(report.written_rows, 1)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            receipt = connection.execute(
                """
                SELECT changed_event_count FROM fmp_economic_calendar_fetch_receipts
                WHERE receipt_id=?
                """,
                (report.capture_id,),
            ).fetchone()
            versions = connection.execute(
                """
                SELECT version.correction_sequence, version.row_sha256,
                       version.supersedes_event_version_id
                FROM fmp_economic_calendar_raw_event_versions AS version
                JOIN fmp_economic_calendar_raw_events AS event
                  ON event.event_id = version.event_id
                WHERE event.event_name='Changed'
                ORDER BY version.correction_sequence
                """
            ).fetchall()
        cache = self._cache_row()
        self.assertEqual(receipt["changed_event_count"], 1)
        self.assertEqual(self._counts()["fmp_economic_calendar_fetch_receipts"], 2)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_events"], 2)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_event_versions"], 3)
        self.assertEqual(self._counts()["fmp_economic_calendar_latest_response_cache"], 1)
        self.assertEqual([row["correction_sequence"] for row in versions], [1, 2])
        self.assertIsNone(versions[0]["supersedes_event_version_id"])
        self.assertIsNotNone(versions[1]["supersedes_event_version_id"])
        self.assertEqual(bytes(cache["response_bytes"]), second.response_bytes)
        self.assertEqual(cache["receipt_id"], report.capture_id)
        self.assertNotEqual(first_report.capture_id, report.capture_id)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), second
        )

    def test_identical_duplicate_event_identity_collapses_to_one_version(self) -> None:
        duplicate = _row(
            "Duplicate Provider Item",
            "2024-04-25 12:30:00",
            provider_note="same-object",
        )
        capture = self._capture([duplicate, dict(duplicate)])
        report = self.publisher.publish(capture)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_rows, 1)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            receipt = connection.execute(
                """
                SELECT row_count, changed_event_count
                FROM fmp_economic_calendar_fetch_receipts
                WHERE receipt_id=?
                """,
                (report.capture_id,),
            ).fetchone()
        self.assertEqual(tuple(receipt), (2, 1))
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_events"], 1)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_event_versions"], 1)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), capture
        )

    def test_conflicting_duplicate_event_identity_fails_before_a_transaction(self) -> None:
        capture = self._capture(
            [
                _row("Conflicting Duplicate", "2024-04-25 12:30:00", provider_note="old"),
                _row("Conflicting Duplicate", "2024-04-25 12:30:00", provider_note="new"),
            ]
        )
        with self.assertRaisesRegex(ConflictError, "conflicting event identities"):
            self.publisher.publish(capture)
        self.assertEqual(self._counts(), {
            "fmp_economic_calendar_fetch_receipts": 0,
            "fmp_economic_calendar_raw_events": 0,
            "fmp_economic_calendar_raw_event_versions": 0,
            "fmp_economic_calendar_latest_response_cache": 0,
            "ingestion_runs": 0,
            "ingestion_artifacts": 0,
            "ingestion_snapshots": 0,
        })

    def test_a_to_b_to_a_appends_a_new_version_instead_of_replaying_history(self) -> None:
        first = self._capture(
            [_row("Revised Event", "2024-04-25 12:30:00", provider_note="A")]
        )
        second = self._capture(
            [_row("Revised Event", "2024-04-25 12:30:00", provider_note="B")],
            captured_at="2026-08-19T18:00:00Z",
        )
        third = self._capture(
            [_row("Revised Event", "2024-04-25 12:30:00", provider_note="A")],
            captured_at="2026-08-20T18:00:00Z",
        )
        first_report = self.publisher.publish(first)
        second_report = self.publisher.publish(second)
        third_report = self.publisher.publish(third)
        self.assertEqual(
            (first_report.outcome, second_report.outcome, third_report.outcome),
            ("published", "published", "published"),
        )
        self.assertEqual(third_report.written_rows, 1)
        self.assertEqual(first.semantic_identity, third.semantic_identity)
        self.assertNotEqual(first_report.capture_id, third_report.capture_id)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            receipt_rows = connection.execute(
                """
                SELECT semantic_identity, source_semantic_identity
                FROM fmp_economic_calendar_fetch_receipts
                ORDER BY captured_at
                """
            ).fetchall()
            version_rows = connection.execute(
                """
                SELECT correction_sequence, row_sha256
                FROM fmp_economic_calendar_raw_event_versions
                ORDER BY correction_sequence
                """
            ).fetchall()
        self.assertEqual(self._counts()["fmp_economic_calendar_fetch_receipts"], 3)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_event_versions"], 3)
        self.assertEqual(len({row["semantic_identity"] for row in receipt_rows}), 3)
        self.assertEqual(receipt_rows[0]["source_semantic_identity"], receipt_rows[2]["source_semantic_identity"])
        self.assertEqual([row["correction_sequence"] for row in version_rows], [1, 2, 3])
        self.assertEqual(version_rows[0]["row_sha256"], version_rows[2]["row_sha256"])
        self.assertEqual(bytes(self._cache_row()["response_bytes"]), third.response_bytes)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), third
        )

    def test_empty_material_batch_writes_a_receipt_and_cache_without_versions(self) -> None:
        capture = self._capture([])
        report = self.publisher.publish(capture)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_captures, 1)
        self.assertEqual(report.written_rows, 0)
        self.assertEqual(self._counts()["fmp_economic_calendar_fetch_receipts"], 1)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_events"], 0)
        self.assertEqual(self._counts()["fmp_economic_calendar_raw_event_versions"], 0)
        self.assertEqual(bytes(self._cache_row()["response_bytes"]), b"[]")
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), capture
        )

    def test_publisher_reparses_capture_before_a_transaction(self) -> None:
        capture = self._capture([_row("Unreviewed", "2024-04-25 12:30:00")])
        with self.assertRaisesRegex(ValidationError, "capture binding"):
            self.publisher.publish(replace(capture, response_sha256="0" * 64))
        self.assertEqual(self._counts()["fmp_economic_calendar_fetch_receipts"], 0)
        self.assertEqual(self._counts()["ingestion_runs"], 0)


if __name__ == "__main__":
    unittest.main()
