"""Focused offline tests for NY Fed aggregate SOMA history."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ValidationError
from quant_data.macro.nyfed_soma_summary import (
    COMPONENT_MANIFEST,
    OUTPUT_DATASET_IDS,
    NyFedSomaPublisher,
    parse_nyfed_soma_summary,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/nyfed_soma_summary.json"
CAPTURED_AT = "2026-08-22T12:00:00Z"
START_DATE = "2003-07-09"
END_DATE = "2026-08-21"


def _body(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        indent=2 if pretty else None,
        sort_keys=pretty,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")


def _parse(body: bytes, *, captured_at: str = CAPTURED_AT):
    return parse_nyfed_soma_summary(
        body,
        captured_at=captured_at,
        start_date=START_DATE,
        end_date=END_DATE,
    )


class NyFedSomaParseTests(unittest.TestCase):
    def test_aggregate_summary_normalizes_declared_components(self) -> None:
        capture = _parse(FIXTURE.read_bytes())

        self.assertEqual(capture.provider_row_count, 2)
        self.assertEqual(
            (capture.releases[0].as_of_date, capture.releases[-1].as_of_date),
            ("2003-07-09", "2026-08-19"),
        )
        self.assertEqual(
            tuple(item.category for item in COMPONENT_MANIFEST),
            tuple(item.category for item in capture.releases[0].components),
        )
        early = {
            item.category: item for item in capture.releases[0].components
        }
        self.assertEqual(early["treasury_bills"].value_text, "239304992")
        self.assertIsNone(early["treasury_floating_rate_notes"].value_text)
        self.assertEqual(
            early["treasury_floating_rate_notes"].missing_reason,
            "source_missing",
        )
        self.assertEqual(
            capture.releases[-1].components[-1].value_text,
            "6368753087.4398",
        )

    def test_order_capture_time_and_json_format_are_nonsemantic(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        original = _parse(FIXTURE.read_bytes())
        equivalent = _parse(
            _body(
                {"soma": {"summary": list(reversed(raw["soma"]["summary"]))}},
                pretty=True,
            ),
            captured_at="2026-08-23T12:00:00Z",
        )

        self.assertEqual(
            tuple(item.semantic_identity for item in original.releases),
            tuple(item.semantic_identity for item in equivalent.releases),
        )
        self.assertNotEqual(original.response_sha256, equivalent.response_sha256)
        self.assertNotEqual(original.captured_at, equivalent.captured_at)

    def test_security_level_or_invalid_values_fail_closed(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        security_shaped = json.loads(json.dumps(raw))
        security_shaped["soma"]["summary"][0]["cusip"] = "91282ABC1"
        with self.assertRaisesRegex(ValidationError, "aggregate summary"):
            _parse(_body(security_shaped))

        negative = json.loads(json.dumps(raw))
        negative["soma"]["summary"][0]["bills"] = "-1"
        with self.assertRaisesRegex(ValidationError, "non-negative"):
            _parse(_body(negative))


class NyFedSomaPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        data = self.root / "data"
        data.mkdir(parents=True)
        self.stores = StoreMap.four_explicit(
            market=data / "market.sqlite",
            macro=data / "macro.sqlite",
            company=data / "company.sqlite",
            news=data / "news.sqlite",
        )
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.MACRO,
            applied_at="2026-08-22T11:00:00Z",
        )
        self.publisher = NyFedSomaPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _counts(self) -> tuple[int, ...]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
                for name in (
                    "ingestion_runs",
                    "ingestion_artifacts",
                    "soma_source_artifacts",
                    "soma_snapshots",
                    "soma_summary_components",
                )
            )

    def test_publish_and_semantic_replay(self) -> None:
        report = self.publisher.publish(_parse(FIXTURE.read_bytes()))

        self.assertEqual(
            (
                report.published_releases,
                report.unchanged_releases,
                report.written_components,
            ),
            (2, 0, 18),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ingestion_run_outputs"
                ).fetchone()[0],
                2 * len(OUTPUT_DATASET_IDS),
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT missing_reason
                    FROM soma_summary_components
                    WHERE as_of_date='2003-07-09'
                      AND category='treasury_floating_rate_notes'
                    """
                ).fetchone()[0],
                "source_missing",
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        """
                        SELECT value_text, unit
                        FROM soma_summary_components
                        WHERE as_of_date='2026-08-19'
                          AND category='total'
                        """
                    ).fetchone()
                ),
                ("6368753087.4398", "thousands_usd"),
            )
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )

        before = self._counts()
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        replay = self.publisher.publish(
            _parse(
                _body(
                    {"soma": {"summary": list(reversed(raw["soma"]["summary"]))}},
                    pretty=True,
                ),
                captured_at="2026-08-23T12:00:00Z",
            )
        )
        self.assertEqual(
            (
                replay.published_releases,
                replay.unchanged_releases,
                replay.written_components,
            ),
            (0, 2, 0),
        )
        self.assertEqual(self._counts(), before)

    def test_changed_week_appends_one_summary_version(self) -> None:
        self.publisher.publish(_parse(FIXTURE.read_bytes()))
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["soma"]["summary"][-1]["total"] = "6368753087440.80"

        report = self.publisher.publish(
            _parse(
                _body(raw),
                captured_at="2026-08-23T12:00:00Z",
            )
        )

        self.assertEqual(
            (
                report.published_releases,
                report.unchanged_releases,
                report.written_components,
            ),
            (1, 1, 9),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM soma_snapshots"
                ).fetchone()[0],
                3,
            )


if __name__ == "__main__":
    unittest.main()
