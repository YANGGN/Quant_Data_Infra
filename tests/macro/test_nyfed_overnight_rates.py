"""Offline tests for the bounded NY Fed overnight-rate publisher."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.macro.nyfed_overnight_rates import (
    MAX_RESPONSE_ROWS,
    OUTPUT_DATASET_IDS,
    RATE_MANIFEST,
    NyFedOvernightRatesPublisher,
    parse_nyfed_overnight_rates,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
BASE_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/nyfed_overnight_rates_base.json"
)
CORRECTION_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/nyfed_overnight_rates_correction.json"
)
CAPTURED_AT = "2026-08-21T14:00:00Z"
CORRECTED_AT = "2026-08-21T15:00:00Z"
START_DATE = "2026-08-20"
END_DATE = "2026-08-21"


def _body(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _parse(body: bytes, *, captured_at: str = CAPTURED_AT):
    return parse_nyfed_overnight_rates(
        body,
        captured_at=captured_at,
        start_date=START_DATE,
        end_date=END_DATE,
    )


class NyFedOvernightRatesParseTests(unittest.TestCase):
    def test_manifest_headline_values_null_and_unsupported_type(self) -> None:
        capture = _parse(BASE_FIXTURE.read_bytes())

        self.assertEqual(
            tuple(item.code for item in RATE_MANIFEST),
            (
                "EFFR",
                "OBFR",
                "TGCR",
                "BGCR",
                "SOFR",
                "SOFR_PERCENTILE_1",
                "SOFR_PERCENTILE_25",
                "SOFR_PERCENTILE_75",
                "SOFR_PERCENTILE_99",
                "SOFR_VOLUME",
                "SOFR_INDEX",
                "SOFR_AVERAGE_30D",
                "SOFR_AVERAGE_90D",
                "SOFR_AVERAGE_180D",
            ),
        )
        self.assertEqual(
            tuple(item.series_id for item in RATE_MANIFEST),
            (
                "macro.nyfed.effr",
                "macro.nyfed.obfr",
                "macro.nyfed.tgcr",
                "macro.nyfed.bgcr",
                "macro.nyfed.sofr",
                "macro.nyfed.sofr_percentile_1",
                "macro.nyfed.sofr_percentile_25",
                "macro.nyfed.sofr_percentile_75",
                "macro.nyfed.sofr_percentile_99",
                "macro.nyfed.sofr_volume",
                "macro.nyfed.sofr_index",
                "macro.nyfed.sofr_average_30d",
                "macro.nyfed.sofr_average_90d",
                "macro.nyfed.sofr_average_180d",
            ),
        )
        self.assertEqual(len(capture.observations), 24)
        observations = {item.rate_code: item for item in capture.observations}
        self.assertEqual(observations["SOFR_PERCENTILE_1"].value_text, "3.6")
        self.assertEqual(observations["SOFR_VOLUME"].value_text, "1842")
        self.assertEqual(observations["SOFR_INDEX"].value_text, "1.082927")
        self.assertEqual(observations["SOFR_AVERAGE_180D"].value_text, "3.59")
        missing = next(
            item
            for item in capture.observations
            if item.effective_date == "2026-08-20" and item.rate_code == "SOFR"
        )
        self.assertIsNone(missing.value_text)
        self.assertEqual(missing.missing_reason, "source_null")
        self.assertEqual(capture.observations[0].rate_code, "EFFR")
        self.assertEqual(capture.observations[-1].rate_code, "SOFR_VOLUME")
        self.assertEqual(capture.captured_at, "2026-08-21T14:00:00.000000Z")

    def test_order_format_capture_time_and_unknown_fields_are_nonsemantic(self) -> None:
        rows = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))["refRates"]
        original = _parse(BASE_FIXTURE.read_bytes())
        altered = []
        for row in reversed(rows):
            changed = dict(reversed(tuple(row.items())))
            changed["futureField"] = {"ignored": True}
            altered.append(changed)
        equivalent = _parse(
            _body({"refRates": altered}, pretty=True),
            captured_at="2026-08-22T14:00:00+00:00",
        )

        self.assertEqual(original.semantic_identity, equivalent.semantic_identity)
        self.assertNotEqual(original.response_sha256, equivalent.response_sha256)
        self.assertNotEqual(original.captured_at, equivalent.captured_at)
        self.assertEqual(
            tuple(
                (
                    item.effective_date,
                    item.rate_code,
                    item.value_text,
                    item.missing_reason,
                )
                for item in original.observations
            ),
            tuple(
                (
                    item.effective_date,
                    item.rate_code,
                    item.value_text,
                    item.missing_reason,
                )
                for item in equivalent.observations
            ),
        )

    def test_invalid_supported_rows_and_bounds_fail_closed(self) -> None:
        row = {
            "effectiveDate": "2026-08-20",
            "type": "EFFR",
            "percentRate": 3.63,
        }
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            _parse(_body({"refRates": [row, row]}))
        with self.assertRaisesRegex(ValidationError, "missing a declared field"):
            _parse(_body({"refRates": [{k: v for k, v in row.items() if k != "percentRate"}]}))
        with self.assertRaisesRegex(ValidationError, "outside"):
            _parse(_body({"refRates": [{**row, "effectiveDate": "2026-08-19"}]}))
        with self.assertRaisesRegex(ValidationError, "finite decimal"):
            _parse(_body({"refRates": [{**row, "percentRate": True}]}))
        with self.assertRaisesRegex(ValidationError, "finite decimal"):
            _parse(_body({"refRates": [{**row, "percentRate": "NA"}]}))
        supplemental_absent = _parse(
            _body(
                {
                    "refRates": [
                        {
                            "effectiveDate": "2026-08-20",
                            "type": "SOFR",
                            "percentRate": 3.63,
                        }
                    ]
                }
            )
        )
        self.assertEqual(
            tuple(item.rate_code for item in supplemental_absent.observations),
            ("SOFR",),
        )
        for marker in ("NA", "N/A"):
            with self.subTest(marker=marker):
                supplemental_missing = _parse(
                    _body(
                        {
                            "refRates": [
                                {
                                    "effectiveDate": "2026-08-20",
                                    "type": "SOFR",
                                    "percentRate": 3.63,
                                    "percentPercentile1": marker,
                                }
                            ]
                        }
                    )
                )
                missing = supplemental_missing.observations[1]
                self.assertEqual(
                    (missing.rate_code, missing.value_text, missing.missing_reason),
                    ("SOFR_PERCENTILE_1", None, "source_missing"),
                )
        documented_headline = _parse(
            _body(
                {
                    "refRates": [
                        {
                            "effectiveDate": "2026-08-20",
                            "type": "EFFR",
                            "percent": 3.63,
                        }
                    ]
                }
            )
        )
        self.assertEqual(documented_headline.observations[0].value_text, "3.63")
        with self.assertRaisesRegex(ValidationError, "contain refRates"):
            _parse(b"[]")
        excessive = {"refRates": [{"type": "UNKNOWN"}] * (MAX_RESPONSE_ROWS + 1)}
        with self.assertRaises(ResourceLimitError):
            _parse(_body(excessive))


class NyFedOvernightRatesPublicationTests(unittest.TestCase):
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
        self.publisher = NyFedOvernightRatesPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _counts(self) -> tuple[int, ...]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in (
                    "ingestion_runs",
                    "ingestion_artifacts",
                    "macro_source_artifacts",
                    "macro_source_snapshots",
                    "macro_observation_versions",
                    "macro_snapshot_observation_membership",
                )
            )

    def test_publish_replay_and_one_rate_correction(self) -> None:
        original = _parse(BASE_FIXTURE.read_bytes())
        report = self.publisher.publish(original)

        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.written_series, 14)
        self.assertEqual(report.written_observation_versions, 24)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM macro_series WHERE provider='nyfed'"
                ).fetchone()[0],
                14,
            )
            metadata = connection.execute(
                """
                SELECT unit, value_representation
                FROM macro_series
                WHERE series_id='macro.nyfed.sofr_volume'
                """
            ).fetchone()
            index_metadata = connection.execute(
                """
                SELECT unit, value_representation
                FROM macro_series
                WHERE series_id='macro.nyfed.sofr_index'
                """
            ).fetchone()
            missing = connection.execute(
                """
                SELECT version.value_text, version.missing_reason,
                       version.available_precision, version.captured_precision
                FROM macro_observations AS current
                JOIN macro_observation_versions AS version
                  ON version.version_id=current.current_version_id
                WHERE version.series_id='macro.nyfed.sofr'
                  AND version.period_start='2026-08-20'
                """
            ).fetchone()
            outputs = connection.execute(
                "SELECT count(*) FROM ingestion_run_outputs WHERE run_id=?",
                (report.run_id,),
            ).fetchone()[0]
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(tuple(missing), (None, "source_null", "datetime", "datetime"))
        self.assertEqual(tuple(metadata), ("billions_usd", "amount"))
        self.assertEqual(tuple(index_metadata), ("index", "level"))
        self.assertEqual(outputs, len(OUTPUT_DATASET_IDS))

        before = self._counts()
        rows = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))["refRates"]
        replay = self.publisher.publish(
            _parse(
                _body({"refRates": list(reversed(rows))}, pretty=True),
                captured_at="2026-08-22T14:00:00Z",
            )
        )
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual((replay.written_series, replay.written_observation_versions), (0, 0))
        self.assertEqual(self._counts(), before)

        correction = self.publisher.publish(
            _parse(CORRECTION_FIXTURE.read_bytes(), captured_at=CORRECTED_AT)
        )
        self.assertEqual(correction.outcome, "published")
        self.assertEqual(
            (correction.written_series, correction.written_observation_versions),
            (0, 1),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            versions = connection.execute(
                """
                SELECT correction_sequence, value_text, supersedes_version_id
                FROM macro_observation_versions
                WHERE series_id='macro.nyfed.effr'
                  AND period_start='2026-08-21'
                ORDER BY correction_sequence
                """
            ).fetchall()
            current = connection.execute(
                """
                SELECT version.value_text
                FROM macro_observations AS observation
                JOIN macro_observation_versions AS version
                  ON version.version_id=observation.current_version_id
                WHERE observation.series_id='macro.nyfed.effr'
                  AND observation.period_start='2026-08-21'
                """
            ).fetchone()[0]
        self.assertEqual([tuple(item)[:2] for item in versions], [(1, "3.63"), (2, "3.64")])
        self.assertIsNotNone(versions[1]["supersedes_version_id"])
        self.assertEqual(current, "3.64")


if __name__ == "__main__":
    unittest.main()
