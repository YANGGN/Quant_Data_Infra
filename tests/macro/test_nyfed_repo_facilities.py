"""Offline tests for the bounded NY Fed repo-facility publisher."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ValidationError
from quant_data.macro.nyfed_repo_facilities import (
    FACILITY_MANIFEST,
    OUTPUT_DATASET_IDS,
    NyFedRepoFacilitiesPublisher,
    parse_nyfed_repo_facilities,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/nyfed_repo_facilities.json"
CAPTURED_AT = "2026-08-21T14:00:00Z"
START_DATE = "2020-01-01"
END_DATE = "2026-08-21"


def _body(value: object, *, pretty: bool = False) -> bytes:
    separators = None if pretty else (",", ":")
    return json.dumps(
        value,
        indent=2 if pretty else None,
        sort_keys=pretty,
        separators=separators,
    ).encode("utf-8")


def _parse(body: bytes, *, captured_at: str = CAPTURED_AT):
    return parse_nyfed_repo_facilities(
        body,
        captured_at=captured_at,
        start_date=START_DATE,
        end_date=END_DATE,
    )


class NyFedRepoFacilitiesParseTests(unittest.TestCase):
    def test_daily_facilities_aggregate_and_exclude_exercises(self) -> None:
        capture = _parse(FIXTURE.read_bytes())

        self.assertEqual(
            tuple(item.code for item in FACILITY_MANIFEST),
            ("ON_RRP", "SRF"),
        )
        self.assertEqual(
            tuple(item.series_id for item in FACILITY_MANIFEST),
            (
                "macro.nyfed.on_rrp_accepted_amount",
                "macro.nyfed.srf_accepted_amount",
            ),
        )
        self.assertEqual(capture.provider_operation_count, 6)
        self.assertEqual(capture.selected_operation_count, 3)
        self.assertEqual(
            tuple(
                (
                    item.effective_date,
                    item.facility_code,
                    item.accepted_usd_text,
                    item.operation_ids,
                )
                for item in capture.observations
            ),
            (
                (
                    "2026-08-20",
                    "ON_RRP",
                    "25000000000",
                    ("rrp-2026-08-20",),
                ),
                (
                    "2026-08-20",
                    "SRF",
                    "3000000",
                    ("srf-2026-08-20-a", "srf-2026-08-20-b"),
                ),
            ),
        )
        self.assertEqual(capture.captured_at, "2026-08-21T14:00:00.000000Z")

    def test_source_order_capture_time_and_unknown_fields_are_nonsemantic(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        original = _parse(FIXTURE.read_bytes())
        operations = [
            {**row, "futureField": {"ignored": True}}
            for row in reversed(raw["repo"]["operations"])
        ]
        equivalent = _parse(
            _body({"repo": {"operations": operations}}, pretty=True),
            captured_at="2026-08-22T14:00:00+00:00",
        )

        self.assertEqual(original.semantic_identity, equivalent.semantic_identity)
        self.assertNotEqual(original.response_sha256, equivalent.response_sha256)
        self.assertNotEqual(original.captured_at, equivalent.captured_at)
        self.assertEqual(original.observations, equivalent.observations)

    def test_invalid_results_fail_closed(self) -> None:
        row = {
            "operationId": "one",
            "operationDate": "2026-08-20",
            "auctionStatus": "Results",
            "operationType": "Reverse Repo",
            "totalAmtAccepted": 1,
        }
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            _parse(_body({"repo": {"operations": [row, row]}}))
        with self.assertRaisesRegex(ValidationError, "non-negative"):
            _parse(
                _body(
                    {
                        "repo": {
                            "operations": [
                                {**row, "totalAmtAccepted": -1},
                            ]
                        }
                    }
                )
            )
        with self.assertRaisesRegex(ValidationError, "outside"):
            _parse(
                _body(
                    {
                        "repo": {
                            "operations": [
                                {**row, "operationDate": "2019-12-31"},
                            ]
                        }
                    }
                )
            )
        with self.assertRaisesRegex(ValidationError, "repo.operations"):
            _parse(b"[]")


class NyFedRepoFacilitiesPublicationTests(unittest.TestCase):
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
        self.publisher = NyFedRepoFacilitiesPublisher(
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
                    "macro_source_artifacts",
                    "macro_source_snapshots",
                    "macro_observation_versions",
                    "macro_snapshot_observation_membership",
                )
            )

    def test_publish_and_semantic_replay(self) -> None:
        capture = _parse(FIXTURE.read_bytes())
        report = self.publisher.publish(capture)

        self.assertEqual(report.outcome, "published")
        self.assertEqual(
            (report.written_series, report.written_observation_versions),
            (2, 2),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT series.provider_series_code, version.value_text,
                       series.unit, series.value_representation
                FROM macro_observations AS current
                JOIN macro_observation_versions AS version
                  ON version.version_id=current.current_version_id
                JOIN macro_series AS series
                  ON series.series_id=version.series_id
                WHERE series.provider='nyfed'
                ORDER BY series.provider_series_code
                """
            ).fetchall()
            outputs = connection.execute(
                "SELECT count(*) FROM ingestion_run_outputs WHERE run_id=?",
                (report.run_id,),
            ).fetchone()[0]
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("ON_RRP", "25000000000", "usd", "amount"),
                ("SRF", "3000000", "usd", "amount"),
            ],
        )
        self.assertEqual(outputs, len(OUTPUT_DATASET_IDS))

        before = self._counts()
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        replay = self.publisher.publish(
            _parse(
                _body(
                    {
                        "repo": {
                            "operations": list(
                                reversed(raw["repo"]["operations"])
                            )
                        }
                    },
                    pretty=True,
                ),
                captured_at="2026-08-22T14:00:00Z",
            )
        )
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(
            (replay.written_series, replay.written_observation_versions),
            (0, 0),
        )
        self.assertEqual(self._counts(), before)


if __name__ == "__main__":
    unittest.main()
