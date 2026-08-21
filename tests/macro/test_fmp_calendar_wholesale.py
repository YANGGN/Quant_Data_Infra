"""Fixture-only tests for wholesale FMP calendar raw evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.macro.fmp_calendar_wholesale import (
    MAX_RESPONSE_BYTES,
    WHOLESALE_COLLECTOR_ID,
    WHOLESALE_DATASET_ID,
    FmpWholesaleCalendarPublisher,
    parse_fmp_us_calendar_wholesale,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import MigrationDeclaration, Registry, load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MIGRATION_RESOURCE = (
    PROJECT_ROOT / "quant_data/migrations/macro/0016_fmp_calendar_wholesale_evidence.sql"
)
MIGRATION_ID = "macro:0016_fmp_calendar_wholesale_evidence"
_MIGRATION_TIME = "2026-08-18T18:00:00Z"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fixture_registry() -> Registry:
    """Use the real successor when available, otherwise a local test overlay."""

    registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
    present = (
        any(item.id == MIGRATION_ID for item in registry.migrations)
        and any(item.id == WHOLESALE_DATASET_ID for item in registry.datasets)
        and any(
            str(item.get("id")) == WHOLESALE_COLLECTOR_ID
            for item in registry.collectors
        )
    )
    if present:
        return registry

    template = next(
        item
        for item in registry.datasets
        if item.id == "fixture.macro.economic_calendar"
    )
    dataset = replace(
        template,
        id=WHOLESALE_DATASET_ID,
        version="1.0.0",
        store="macro",
        layer="evidence",
        relations=(
            "fmp_economic_calendar_captures",
            "fmp_economic_calendar_rows",
        ),
        collector_ids=(WHOLESALE_COLLECTOR_ID,),
        tool_ids=(),
        dashboard_ids=(),
        export_ids=(),
    )
    migration = MigrationDeclaration(
        id=MIGRATION_ID,
        store="macro",
        ordinal=16,
        resource="quant_data/migrations/macro/0016_fmp_calendar_wholesale_evidence.sql",
        sha256=hashlib.sha256(MIGRATION_RESOURCE.read_bytes()).hexdigest(),
        semantic_scope="add immutable wholesale FMP calendar evidence",
        dependencies=("macro:0015_live_macro_history_extension",),
        reconstruction_state="fixture_validated",
    )
    stores = tuple(
        replace(
            item,
            migration_order=(
                item.migration_order
                if item.id != "macro" or MIGRATION_ID in item.migration_order
                else item.migration_order + (MIGRATION_ID,)
            ),
        )
        for item in registry.stores
    )
    return replace(
        registry,
        registry_version="test-wholesale-evidence",
        stores=stores,
        migrations=registry.migrations + (migration,),
        datasets=registry.datasets + (dataset,),
        collectors=registry.collectors
        + ({"id": WHOLESALE_COLLECTOR_ID, "output_datasets": [WHOLESALE_DATASET_ID]},),
    )


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

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _capture(
        rows: list[dict[str, object]],
        *,
        captured_at: str = "2026-08-18T18:00:00Z",
        pretty: bool = False,
        sort: bool = False,
    ):
        return parse_fmp_us_calendar_wholesale(
            _body(rows, pretty=pretty, sort=sort),
            captured_at=captured_at,
            start_date="2024-04-01",
            end_date="2024-06-30",
        )

    def test_publish_retains_exact_bytes_all_rows_lineage_and_replay(self) -> None:
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
        self.assertEqual(report.written_captures, 1)
        self.assertEqual(report.written_rows, 2)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            stored = connection.execute(
                """
                SELECT response_bytes, response_sha256, semantic_identity, row_count,
                       request_country, request_start_date, request_end_date
                FROM fmp_economic_calendar_captures
                """
            ).fetchone()
            rows = connection.execute(
                """
                SELECT source_row, country, event_name, raw_row_json, change_json,
                       impact_json, change_percentage_json
                FROM fmp_economic_calendar_rows
                ORDER BY source_row
                """
            ).fetchall()
            artifact = connection.execute(
                """
                SELECT content_sha256, byte_count, dataset_id
                FROM ingestion_artifacts
                WHERE artifact_id=?
                """,
                (report.artifact_id,),
            ).fetchone()
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        self.assertEqual(bytes(stored["response_bytes"]), capture.response_bytes)
        self.assertEqual(stored["response_sha256"], capture.response_sha256)
        self.assertEqual(stored["semantic_identity"], capture.semantic_identity)
        self.assertEqual(tuple(stored)[3:], (2, "US", "2024-04-01", "2024-06-30"))
        self.assertEqual(rows[0]["raw_row_json"], capture.rows[0].raw_row_json)
        self.assertEqual(rows[1]["country"], "CA")
        self.assertEqual(rows[1]["event_name"], "Unexpected Canadian Event")
        self.assertEqual(rows[0]["change_json"], '"0.2"')
        self.assertEqual(rows[0]["impact_json"], '"High"')
        self.assertEqual(rows[0]["change_percentage_json"], '"20"')
        self.assertEqual(
            tuple(artifact),
            (capture.response_sha256, len(capture.response_bytes), WHOLESALE_DATASET_ID),
        )
        self.assertEqual(self.publisher.completed_windows(), (("2024-04-01", "2024-06-30"),))
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"),
            capture,
        )

    def test_row_reorder_and_whitespace_are_total_no_write_replays(self) -> None:
        rows = [
            _row("Unreviewed One", "2024-04-25 12:30:00", marker="one"),
            _row("Unreviewed Two", "2024-05-15 12:30:00", marker="two"),
        ]
        original = self._capture(rows)
        published = self.publisher.publish(original)
        reordered = self._capture(
            [dict(reversed(tuple(item.items()))) for item in reversed(rows)],
            captured_at="2026-08-19T18:00:00Z",
            pretty=True,
            sort=True,
        )
        self.assertEqual(original.semantic_identity, reordered.semantic_identity)
        self.assertNotEqual(original.response_sha256, reordered.response_sha256)
        before = self._db_counts()
        replay = self.publisher.publish(reordered)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.run_id, None)
        self.assertEqual(replay.written_captures, 0)
        self.assertEqual(replay.written_rows, 0)
        self.assertEqual(self._db_counts(), before)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"),
            original,
        )
        self.assertEqual(published.capture_id, replay.capture_id)

    def test_changed_unknown_row_creates_a_new_immutable_capture(self) -> None:
        first = self._capture(
            [_row("Not Yet Reviewed", "2024-04-25 12:30:00", provider_note="old")]
        )
        second = self._capture(
            [_row("Not Yet Reviewed", "2024-04-25 12:30:00", provider_note="new")],
            captured_at="2026-08-19T18:00:00Z",
        )
        self.assertNotEqual(first.semantic_identity, second.semantic_identity)
        self.publisher.publish(first)
        report = self.publisher.publish(second)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(self._db_counts()[0], 2)
        self.assertEqual(self._db_counts()[1], 2)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"),
            second,
        )
        self.assertEqual(self.publisher.completed_windows(), (("2024-04-01", "2024-06-30"),))

    def test_empty_wholesale_capture_and_immutability_guards(self) -> None:
        capture = self._capture([])
        report = self.publisher.publish(capture)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_rows, 0)
        self.assertEqual(
            self.publisher.load_latest_window("2024-04-01", "2024-06-30"), capture
        )
        connection = sqlite3.connect(self.stores.macro)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE fmp_economic_calendar_captures SET row_count=1"
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM fmp_economic_calendar_captures"
                )
            connection.rollback()
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_publisher_reparses_its_capture_before_a_transaction(self) -> None:
        capture = self._capture([_row("Unreviewed", "2024-04-25 12:30:00")])
        with self.assertRaisesRegex(ValidationError, "capture binding"):
            self.publisher.publish(replace(capture, response_sha256="0" * 64))
        self.assertEqual(self._db_counts(), (0, 0, 0, 0))

    def _db_counts(self) -> tuple[int, int, int, int]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in (
                    "fmp_economic_calendar_captures",
                    "fmp_economic_calendar_rows",
                    "ingestion_runs",
                    "ingestion_artifacts",
                )
            )


if __name__ == "__main__":
    unittest.main()
