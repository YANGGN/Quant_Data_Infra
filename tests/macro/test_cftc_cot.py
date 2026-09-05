"""Focused offline tests for CFTC futures-only COT parsing and publication."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ValidationError
from quant_data.macro.cftc_cot import (
    COLLECTOR_ID_BY_FAMILY,
    DISAGGREGATED_FUTURES_ONLY,
    HANDLER_BY_FAMILY,
    OUTPUT_DATASET_IDS,
    TFF_FUTURES_ONLY,
    CftcCotPublisher,
    parse_cftc_cot,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
TFF_FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/cftc_tff_futures_only.json"
DISAGGREGATED_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/cftc_disaggregated_futures_only.json"
)
CAPTURED_AT = "2026-08-21T14:00:00Z"


def _parse(
    body: bytes,
    family: str,
    *,
    captured_at: str = CAPTURED_AT,
    start_date: str = "2026-08-18",
    end_date: str = "2026-08-18",
):
    return parse_cftc_cot(
        (body,),
        report_family=family,
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


def _bound_registry(registry, family: str):
    collector_id = COLLECTOR_ID_BY_FAMILY[family]
    if any(
        str(item.get("id")) == collector_id for item in registry.collectors
    ):
        return registry
    collector = {
        "id": collector_id,
        "handler": HANDLER_BY_FAMILY[family],
        "network": True,
        "version": "1.0.0",
        "output_datasets": list(OUTPUT_DATASET_IDS),
        "configuration_env": [],
        "schedule_eligibility": {"mode": "manual_only"},
    }
    datasets = tuple(
        replace(
            item,
            collector_ids=(
                item.collector_ids + (collector_id,)
                if item.id in OUTPUT_DATASET_IDS
                else item.collector_ids
            ),
        )
        for item in registry.datasets
    )
    return replace(
        registry,
        collectors=registry.collectors + (collector,),
        datasets=datasets,
    )


class CftcCotParseTests(unittest.TestCase):
    def test_two_futures_only_families_keep_taxonomy_and_fields_separate(self) -> None:
        tff = _parse(TFF_FIXTURE.read_bytes(), TFF_FUTURES_ONLY)
        disaggregated = _parse(
            DISAGGREGATED_FIXTURE.read_bytes(),
            DISAGGREGATED_FUTURES_ONLY,
        )

        self.assertEqual(len(tff.observations), 17)
        self.assertEqual(len(disaggregated.observations), 16)
        self.assertEqual(
            {item.series_id for item in tff.observations},
            {"macro.cftc.cot.tff_futures_only.123456"},
        )
        self.assertEqual(
            {item.series_id for item in disaggregated.observations},
            {"macro.cftc.cot.disaggregated_futures_only.654321"},
        )
        self.assertEqual(
            {dict(item.dimensions)["participant_group"] for item in tff.observations},
            {
                "all",
                "dealer",
                "asset_manager",
                "leveraged_money",
                "other_reportables",
                "total_reportable",
                "nonreportable",
            },
        )
        self.assertIn(
            "producer_merchant",
            {dict(item.dimensions)["participant_group"] for item in disaggregated.observations},
        )
        self.assertNotIn(
            "dealer",
            {dict(item.dimensions)["participant_group"] for item in disaggregated.observations},
        )
        null = next(item for item in tff.observations if item.value_text is None)
        self.assertEqual(null.missing_reason, "source_null")
        self.assertEqual(null.report_date, "2026-08-18")
        self.assertFalse(
            any(
                key.startswith(("change_", "pct_"))
                for item in tff.observations for key, _ in item.dimensions
            )
        )

    def test_invalid_taxonomy_and_non_tuesday_fail_closed(self) -> None:
        row = json.loads(TFF_FIXTURE.read_text(encoding="utf-8"))[0]
        row["futonly_or_combined"] = "Combined"
        with self.assertRaisesRegex(ValidationError, "not futures-only"):
            _parse(json.dumps([row]).encode("utf-8"), TFF_FUTURES_ONLY)
        row["futonly_or_combined"] = "FutOnly"
        row["report_date_as_yyyy_mm_dd"] = "2026-08-19T00:00:00.000"
        with self.assertRaisesRegex(ValidationError, "Tuesday"):
            _parse(json.dumps([row]).encode("utf-8"), TFF_FUTURES_ONLY)

    def test_contract_code_accepts_official_trailing_plus_verbatim(self) -> None:
        template = json.loads(TFF_FIXTURE.read_text(encoding="utf-8"))[0]
        contract_codes = ("134FM1", "13874+", "138741")
        rows = []
        for contract_code in contract_codes:
            row = dict(template)
            row["cftc_contract_market_code"] = contract_code
            rows.append(row)
        capture = _parse(json.dumps(rows).encode("utf-8"), TFF_FUTURES_ONLY)

        self.assertEqual(
            {item.contract_code for item in capture.observations},
            set(contract_codes),
        )
        self.assertEqual(
            {item.series_id for item in capture.observations},
            {
                "macro.cftc.cot.tff_futures_only.134fm1",
                "macro.cftc.cot.tff_futures_only.13874+",
                "macro.cftc.cot.tff_futures_only.138741",
            },
        )
        self.assertEqual(
            {
                dict(item.dimensions)["cftc_contract_market_code"]
                for item in capture.observations
            },
            set(contract_codes),
        )

        for contract_code in ("138+74", "13874.", "13874/", "13874 ", "13874\x00"):
            with self.subTest(contract_code=contract_code):
                row = dict(template)
                row["cftc_contract_market_code"] = contract_code
                with self.assertRaisesRegex(ValidationError, "contract code is invalid"):
                    _parse(json.dumps([row]).encode("utf-8"), TFF_FUTURES_ONLY)

    def test_live_field_maps_do_not_fallback_to_legacy_or_other_keys(self) -> None:
        required_cases = (
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "asset_mgr_positions_long"),
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "asset_mgr_positions_short"),
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "lev_money_positions_long"),
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "lev_money_positions_short"),
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "other_rept_positions_long"),
            (TFF_FIXTURE, TFF_FUTURES_ONLY, "other_rept_positions_short"),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "prod_merc_positions_long",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "prod_merc_positions_short",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "other_rept_positions_long",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "other_rept_positions_short",
            ),
        )
        for fixture, family, field in required_cases:
            with self.subTest(family=family, field=field):
                row = json.loads(fixture.read_text(encoding="utf-8"))[0]
                row[field + "_all"] = row.pop(field)
                with self.assertRaisesRegex(
                    ValidationError, field + " is required"
                ):
                    _parse(json.dumps([row]).encode("utf-8"), family)

        optional_cases = (
            (
                TFF_FIXTURE,
                TFF_FUTURES_ONLY,
                "asset_mgr_positions_spread",
                "asset_manager",
                "spreading",
            ),
            (
                TFF_FIXTURE,
                TFF_FUTURES_ONLY,
                "lev_money_positions_spread",
                "leveraged_money",
                "spreading",
            ),
            (
                TFF_FIXTURE,
                TFF_FUTURES_ONLY,
                "other_rept_positions_spread",
                "other_reportables",
                "spreading",
            ),
            (
                TFF_FIXTURE,
                TFF_FUTURES_ONLY,
                "tot_rept_positions_short",
                "total_reportable",
                "short",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "m_money_positions_spread",
                "managed_money",
                "spreading",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "other_rept_positions_spread",
                "other_reportables",
                "spreading",
            ),
            (
                DISAGGREGATED_FIXTURE,
                DISAGGREGATED_FUTURES_ONLY,
                "tot_rept_positions_short",
                "total_reportable",
                "short",
            ),
        )
        for fixture, family, field, group, side in optional_cases:
            with self.subTest(family=family, field=field):
                row = json.loads(fixture.read_text(encoding="utf-8"))[0]
                row[field + "_all"] = row.pop(field)
                capture = _parse(json.dumps([row]).encode("utf-8"), family)
                self.assertFalse(
                    any(
                        dict(item.dimensions)["participant_group"] == group
                        and dict(item.dimensions)["position_side"] == side
                        for item in capture.observations
                    )
                )


class CftcCotPublicationTests(unittest.TestCase):
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
        base_registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        migrate_and_register_store(
            self.stores,
            base_registry,
            StoreRole.MACRO,
            applied_at="2026-08-21T13:00:00Z",
        )
        self.publisher = CftcCotPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=_bound_registry(base_registry, TFF_FUTURES_ONLY),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()



    def test_publish_semantic_replay_and_correction_preserve_pit_dates(self) -> None:
        original_body = TFF_FIXTURE.read_bytes()
        original = _parse(original_body, TFF_FUTURES_ONLY)
        report = self.publisher.publish(original)
        self.assertEqual(
            (report.outcome, report.written_series, report.written_observation_versions),
            ("published", 1, 17),
        )
        before = None
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            before = tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "ingestion_runs",
                    "macro_source_artifacts",
                    "macro_observation_versions",
                )
            )
        rows = json.loads(original_body)
        replay = self.publisher.publish(
            _parse(
                json.dumps(list(reversed(rows)), indent=2).encode("utf-8"),
                TFF_FUTURES_ONLY,
                captured_at="2026-08-22T14:00:00Z",
            )
        )
        self.assertEqual(
            (replay.outcome, replay.written_series, replay.written_observation_versions),
            ("unchanged", 0, 0),
        )
        corrected_rows = json.loads(original_body)
        corrected_rows[0]["dealer_positions_long_all"] = "101"
        corrected = self.publisher.publish(
            _parse(
                json.dumps(corrected_rows).encode("utf-8"),
                TFF_FUTURES_ONLY,
                captured_at="2026-08-22T15:00:00Z",
            )
        )
        self.assertEqual(
            (corrected.outcome, corrected.written_series, corrected.written_observation_versions),
            ("published", 0, 1),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            after_replay_base = tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "ingestion_runs",
                    "macro_source_artifacts",
                    "macro_observation_versions",
                )
            )
            versions = connection.execute(
                """
                SELECT correction_sequence, value_text, available_at,
                       available_precision, supersedes_version_id
                FROM macro_observation_versions
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                  AND period_start='2026-08-18'
                  AND dimensions_json LIKE '%"participant_group":"dealer"%'
                  AND dimensions_json LIKE '%"position_side":"long"%'
                ORDER BY correction_sequence
                """
            ).fetchall()
            release = connection.execute(
                """
                SELECT source_release_order, available_at, available_precision,
                       release_stage, source_published_at, source_published_precision
                FROM macro_releases
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                LIMIT 1
                """
            ).fetchone()
            availability_basis = connection.execute(
                """
                SELECT availability_basis
                FROM macro_series
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                """
            ).fetchone()[0]
            dimensions = connection.execute(
                """
                SELECT count(*) FROM macro_series_dimensions
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                """
            ).fetchone()[0]
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(before, (1, 1, 17))
        self.assertEqual(after_replay_base, (2, 2, 18))
        self.assertEqual(
            [tuple(row)[:4] for row in versions],
            [
                (1, "100", "2026-08-21T14:00:00.000000Z", "datetime"),
                (2, "101", "2026-08-22T15:00:00.000000Z", "datetime"),
            ],
        )
        self.assertIsNotNone(versions[1]["supersedes_version_id"])
        self.assertEqual(availability_basis, "local_capture")
        self.assertEqual(
            tuple(release),
            (
                "2026-08-18",
                "2026-08-21T14:00:00.000000Z",
                "datetime",
                "current_state",
                None,
                "unknown",
            ),
        )
        self.assertGreater(dimensions, 0)


    def test_post_holiday_capture_never_invents_a_friday_release(self) -> None:
        rows = json.loads(TFF_FIXTURE.read_text(encoding="utf-8"))
        for row in rows:
            row["report_date_as_yyyy_mm_dd"] = "2026-12-22T00:00:00.000"
        report = self.publisher.publish(
            _parse(
                json.dumps(rows).encode("utf-8"),
                TFF_FUTURES_ONLY,
                captured_at="2026-12-28T14:00:00Z",
                start_date="2026-12-22",
                end_date="2026-12-22",
            )
        )
        self.assertEqual(
            (report.outcome, report.written_series, report.written_observation_versions),
            ("published", 1, 17),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            availability_basis = connection.execute(
                """
                SELECT availability_basis
                FROM macro_series
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                """
            ).fetchone()[0]
            release = connection.execute(
                """
                SELECT vintage_at, vintage_precision, source_release_order,
                       available_at, available_precision, release_stage,
                       source_published_at, source_published_precision
                FROM macro_releases
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                """
            ).fetchone()
            version = connection.execute(
                """
                SELECT period_start, period_end, available_at, available_precision,
                       captured_at, captured_precision
                FROM macro_observation_versions
                WHERE series_id='macro.cftc.cot.tff_futures_only.123456'
                ORDER BY source_row
                LIMIT 1
                """
            ).fetchone()
        self.assertEqual(availability_basis, "local_capture")
        self.assertEqual(
            tuple(release),
            (
                "2026-12-22",
                "date",
                "2026-12-22",
                "2026-12-28T14:00:00.000000Z",
                "datetime",
                "current_state",
                None,
                "unknown",
            ),
        )
        self.assertEqual(
            tuple(version),
            (
                "2026-12-22",
                "2026-12-22",
                "2026-12-28T14:00:00.000000Z",
                "datetime",
                "2026-12-28T14:00:00.000000Z",
                "datetime",
            ),
        )
        stored_text = "\n".join(str(value) for value in tuple(release) + tuple(version))
        self.assertNotIn("2026-12-25", stored_text)

if __name__ == "__main__":
    unittest.main()
