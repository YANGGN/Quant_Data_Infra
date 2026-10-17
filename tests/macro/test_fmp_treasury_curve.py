"""Offline tests for the bounded FMP Treasury curve publisher."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.macro.fmp_treasury_curve import (
    COLLECTOR_ID,
    MAX_RESPONSE_ROWS,
    OUTPUT_DATASET_IDS,
    TENOR_MANIFEST,
    FmpTreasuryCurvePublisher,
    parse_fmp_treasury_curve,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
BASE_FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/fmp_treasury_curve_base.json"
CORRECTION_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/fmp_treasury_curve_correction.json"
)
CAPTURED_AT = "2026-08-21T14:00:00Z"
CORRECTED_AT = "2026-08-21T15:00:00Z"
START_DATE = "2025-01-01"
END_DATE = "2025-01-31"


def _body(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _parse(body: bytes, *, captured_at: str = CAPTURED_AT):
    return parse_fmp_treasury_curve(
        body,
        captured_at=captured_at,
        start_date=START_DATE,
        end_date=END_DATE,
    )


class FmpTreasuryCurveParseTests(unittest.TestCase):
    def test_frozen_manifest_null_and_unknown_extension(self) -> None:
        capture = _parse(BASE_FIXTURE.read_bytes())

        self.assertEqual(len(TENOR_MANIFEST), 12)
        self.assertEqual(len(capture.observations), 24)
        self.assertEqual(
            tuple(item.series_id for item in TENOR_MANIFEST),
            (
                "macro.treasury.par_yield.1m",
                "macro.treasury.par_yield.2m",
                "macro.treasury.par_yield.3m",
                "macro.treasury.par_yield.6m",
                "macro.treasury.par_yield.1y",
                "macro.treasury.par_yield.2y",
                "macro.treasury.par_yield.3y",
                "macro.treasury.par_yield.5y",
                "macro.treasury.par_yield.7y",
                "macro.treasury.par_yield.10y",
                "macro.treasury.par_yield.20y",
                "macro.treasury.par_yield.30y",
            ),
        )
        self.assertNotIn(
            "month4", {item.provider_field for item in capture.observations}
        )
        missing = next(
            item
            for item in capture.observations
            if item.curve_date == "2025-01-03" and item.tenor == "30Y"
        )
        self.assertIsNone(missing.value_text)
        self.assertEqual(missing.missing_reason, "source_null")
        self.assertEqual(capture.observations[0].curve_date, "2025-01-02")
        self.assertEqual(capture.observations[0].source_row, 1)
        self.assertEqual(capture.observations[-1].source_row, 24)
        self.assertEqual(capture.captured_at, "2026-08-21T14:00:00.000000Z")

    def test_order_format_capture_time_and_unknown_fields_are_nonsemantic(self) -> None:
        original_rows = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))
        original = _parse(BASE_FIXTURE.read_bytes())
        altered = []
        for row in reversed(original_rows):
            changed = dict(reversed(tuple(row.items())))
            changed["month4"] = -12345
            changed["newProviderField"] = {"ignored": True}
            altered.append(changed)
        equivalent = _parse(
            _body(altered, pretty=True),
            captured_at="2026-08-22T14:00:00+00:00",
        )

        self.assertEqual(original.semantic_identity, equivalent.semantic_identity)
        self.assertNotEqual(original.response_sha256, equivalent.response_sha256)
        self.assertNotEqual(original.captured_at, equivalent.captured_at)
        self.assertEqual(
            tuple(
                (
                    item.curve_date,
                    item.provider_field,
                    item.value_text,
                    item.missing_reason,
                )
                for item in original.observations
            ),
            tuple(
                (
                    item.curve_date,
                    item.provider_field,
                    item.value_text,
                    item.missing_reason,
                )
                for item in equivalent.observations
            ),
        )

    def test_invalid_rows_and_bounds_fail_closed(self) -> None:
        row = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))[0]

        missing = dict(row)
        missing.pop("month6")
        with self.assertRaisesRegex(ValidationError, "missing a declared field"):
            _parse(_body([missing]))

        with self.assertRaisesRegex(ValidationError, "must be unique"):
            _parse(_body([row, row]))

        outside = dict(row, date="2024-12-31")
        with self.assertRaisesRegex(ValidationError, "outside"):
            _parse(_body([outside]))

        boolean = dict(row, year10=True)
        with self.assertRaisesRegex(ValidationError, "finite decimal"):
            _parse(_body([boolean]))

        with self.assertRaises(ValidationError):
            _parse(_body([dict(row, year10=float("nan"))]))

        with self.assertRaises(ResourceLimitError):
            _parse(b'[{"date":"2025-01-03","month1":1e100000}]')

        excessive = b"[" + b",".join([b"{}"] * (MAX_RESPONSE_ROWS + 1)) + b"]"
        with self.assertRaises(ResourceLimitError):
            _parse(excessive)

        with self.assertRaisesRegex(ValidationError, "request window"):
            parse_fmp_treasury_curve(
                b"[]",
                captured_at=CAPTURED_AT,
                start_date="2024-01-01",
                end_date="2025-01-01",
            )


class FmpTreasuryCurvePublicationTests(unittest.TestCase):
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
            applied_at="2026-08-21T13:00:00Z",
        )
        self.publisher = FmpTreasuryCurvePublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _counts(self) -> tuple[int, ...]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            relations = (
                "ingestion_runs",
                "ingestion_artifacts",
                "macro_source_artifacts",
                "macro_source_snapshots",
                "macro_observation_versions",
                "macro_snapshot_observation_membership",
                "treasury_yield_curves",
                "treasury_yield_curve_versions",
            )
            return tuple(
                int(
                    connection.execute(
                        f"SELECT count(*) FROM {relation}"
                    ).fetchone()[0]
                )
                for relation in relations
            )

    def test_initial_publish_writes_generic_and_specialized_lineage(self) -> None:
        capture = _parse(BASE_FIXTURE.read_bytes())
        report = self.publisher.publish(capture)

        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_curves, 2)
        self.assertEqual(report.written_curve_versions, 24)
        self.assertEqual(report.written_observation_versions, 24)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM macro_series WHERE provider='fmp'"
                ).fetchone()[0],
                12,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM treasury_yield_curves WHERE provider='fmp'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT count(*)
                    FROM macro_observation_versions AS version
                    JOIN macro_series AS series ON series.series_id=version.series_id
                    WHERE series.provider='fmp'
                    """
                ).fetchone()[0],
                24,
            )
            missing = connection.execute(
                """
                SELECT yield_value, missing_reason, curve_date, available_at,
                       available_precision, captured_precision
                FROM treasury_yield_curve_versions
                WHERE provider='fmp' AND curve_date='2025-01-03' AND tenor='30Y'
                """
            ).fetchone()
            release = connection.execute(
                """
                SELECT vintage_at, vintage_precision, available_precision
                FROM macro_releases
                WHERE series_id='macro.treasury.par_yield.30y'
                  AND source_vintage_identity='fmp:treasury:par_yield:2025-01-03'
                """
            ).fetchone()
            output_count = connection.execute(
                "SELECT count(*) FROM ingestion_run_outputs WHERE run_id=?",
                (report.run_id,),
            ).fetchone()[0]
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        self.assertEqual(
            tuple(missing),
            (
                None,
                "source_null",
                "2025-01-03",
                "2026-08-21T14:00:00.000000Z",
                "datetime",
                "datetime",
            ),
        )
        self.assertEqual(tuple(release), ("2025-01-03", "date", "datetime"))
        self.assertEqual(output_count, len(OUTPUT_DATASET_IDS))

    def test_semantic_replay_is_a_total_no_write(self) -> None:
        rows = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))
        original = _parse(BASE_FIXTURE.read_bytes())
        self.publisher.publish(original)
        before = self._counts()
        for row in rows:
            row["month4"] = "provider-only-change"
        replay_capture = _parse(
            _body(list(reversed(rows)), pretty=True),
            captured_at="2026-08-22T14:00:00Z",
        )

        replay = self.publisher.publish(replay_capture)

        self.assertEqual(replay.outcome, "unchanged")
        self.assertIsNone(replay.run_id)
        self.assertIsNone(replay.artifact_id)
        self.assertIsNone(replay.snapshot_id)
        self.assertEqual(
            (
                replay.written_curves,
                replay.written_curve_versions,
                replay.written_observation_versions,
            ),
            (0, 0, 0),
        )
        self.assertEqual(self._counts(), before)

    def test_one_changed_tenor_appends_only_its_two_version_chains(self) -> None:
        self.publisher.publish(_parse(BASE_FIXTURE.read_bytes()))
        correction = self.publisher.publish(
            _parse(CORRECTION_FIXTURE.read_bytes(), captured_at=CORRECTED_AT)
        )

        self.assertEqual(correction.outcome, "published")
        self.assertEqual(
            (
                correction.written_curves,
                correction.written_curve_versions,
                correction.written_observation_versions,
            ),
            (0, 1, 1),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            curve_versions = connection.execute(
                """
                SELECT correction_sequence, yield_value,
                       supersedes_curve_version_id, available_at
                FROM treasury_yield_curve_versions
                WHERE provider='fmp' AND curve_date='2025-01-02'
                  AND tenor='10Y'
                ORDER BY correction_sequence
                """
            ).fetchall()
            generic_versions = connection.execute(
                """
                SELECT correction_sequence, value_text, supersedes_version_id
                FROM macro_observation_versions
                WHERE series_id='macro.treasury.par_yield.10y'
                  AND period_start='2025-01-02'
                ORDER BY correction_sequence
                """
            ).fetchall()
            unchanged_curve_count = connection.execute(
                """
                SELECT count(*) FROM treasury_yield_curve_versions
                WHERE provider='fmp' AND curve_date='2025-01-02' AND tenor='2Y'
                """
            ).fetchone()[0]
            unchanged_generic_count = connection.execute(
                """
                SELECT count(*) FROM macro_observation_versions
                WHERE series_id='macro.treasury.par_yield.2y'
                  AND period_start='2025-01-02'
                """
            ).fetchone()[0]
            correction_memberships = connection.execute(
                """
                SELECT count(*) FROM macro_snapshot_observation_membership
                WHERE snapshot_id=?
                """,
                (correction.snapshot_id,),
            ).fetchone()[0]
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(len(curve_versions), 2)
        self.assertEqual(tuple(curve_versions[0])[:2], (1, "4.57"))
        self.assertEqual(tuple(curve_versions[1])[:2], (2, "4.58"))
        self.assertIsNotNone(curve_versions[1]["supersedes_curve_version_id"])
        self.assertEqual(
            curve_versions[1]["available_at"],
            "2026-08-21T15:00:00.000000Z",
        )
        self.assertEqual(len(generic_versions), 2)
        self.assertEqual(tuple(generic_versions[1])[:2], (2, "4.58"))
        self.assertIsNotNone(generic_versions[1]["supersedes_version_id"])
        self.assertEqual(unchanged_curve_count, 1)
        self.assertEqual(unchanged_generic_count, 1)
        self.assertEqual(correction_memberships, 24)

    def test_empty_capture_is_valid_complete_evidence(self) -> None:
        capture = _parse(b"[]")
        report = self.publisher.publish(capture)

        self.assertEqual(report.outcome, "published")
        self.assertEqual(
            (
                report.written_curves,
                report.written_curve_versions,
                report.written_observation_versions,
            ),
            (0, 0, 0),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            snapshot = connection.execute(
                """
                SELECT row_count, completeness, validation_state
                FROM macro_source_snapshots
                WHERE snapshot_id=?
                """,
                (report.snapshot_id,),
            ).fetchone()
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM macro_series WHERE provider='fmp'"
                ).fetchone()[0],
                12,
            )
        self.assertEqual(tuple(snapshot), (0, "complete", "validated"))

    def test_capture_registry_and_path_tampering_fail_before_publication(self) -> None:
        capture = _parse(BASE_FIXTURE.read_bytes())
        with self.assertRaisesRegex(ValidationError, "capture binding"):
            self.publisher.publish(
                replace(capture, response_sha256="0" * 64)
            )
        self.assertEqual(self._counts(), (0, 0, 0, 0, 0, 0, 0, 0))

        collectors = tuple(
            {
                **dict(item),
                "output_datasets": list(reversed(OUTPUT_DATASET_IDS)),
            }
            if item["id"] == COLLECTOR_ID
            else item
            for item in self.registry.collectors
        )
        with self.assertRaisesRegex(ValidationError, "collector binding"):
            FmpTreasuryCurvePublisher(
                macro_store=self.stores.macro,
                project_root=self.root,
                registry=replace(self.registry, collectors=collectors),
            )

        other = self.root / "other.sqlite"
        other.write_bytes(b"not a store")
        with self.assertRaisesRegex(ValidationError, "target binding"):
            FmpTreasuryCurvePublisher(
                macro_store=other,
                project_root=self.root,
                registry=self.registry,
            )


if __name__ == "__main__":
    unittest.main()
