"""Offline integration coverage for the canonical macro and calendar tools."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.fmp_calendar_wholesale import (
    FmpWholesaleCalendarPublisher,
    parse_fmp_us_calendar_wholesale,
)
from quant_data.macro.live_vintages import (
    GDP_REAL_SERIES_ID,
    MacroLiveVintagePublisher,
    parse_bea_gdp_vintage_xlsx,
)
from quant_data.macro.rtdsm_fixture import MacroFixtureImporter
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.macro.stage3_normalizers import parse_stage3_macro_fixture
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.context import ExecutionBudget
from tests.macro.test_fmp_calendar_wholesale import _body, _row
from tests.macro.test_fmp_calendar_wholesale_migration import (
    WHOLESALE_COMMAND as LEGACY_WHOLESALE_COMMAND,
    WHOLESALE_DATASET as LEGACY_WHOLESALE_DATASET,
    _CAPTURED_AT as LEGACY_CAPTURED_AT,
    _digest as legacy_digest,
    _insert_artifact as insert_legacy_artifact,
    _insert_capture as insert_legacy_capture,
    _insert_output as insert_legacy_output,
    _insert_row as insert_legacy_row,
    _insert_run as insert_legacy_run,
    _insert_snapshot as insert_legacy_snapshot,
)
from tests.macro.test_live_vintages import _workbook
from tests.macro.test_stage3_macro import (
    FIXTURE_ROOT,
    _FixtureManifest,
    _fixture,
    _mutated_complete_retail_fixture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(record: dict[str, object]) -> dict[str, object]:
    fields = record["fields"]
    assert isinstance(fields, list)
    return {
        str(field["name"]): field["value"]
        for field in fields
        if isinstance(field, dict)
    }


def _metrics(diagnostic: dict[str, object]) -> dict[str, object]:
    metrics = diagnostic["metrics"]
    assert isinstance(metrics, list)
    return {
        str(field["name"]): field["value"]
        for field in metrics
        if isinstance(field, dict)
    }


class CanonicalMacroAccessToolTests(unittest.TestCase):
    """Exercise public paths over a fully temporary initialized store map."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.stores = _stores(self.root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry)
        fixtures = {
            path.stem: _fixture(path.stem)
            for path in sorted(FIXTURE_ROOT.glob("*.json"))
        }
        self.stage3_manifest = _FixtureManifest(fixtures)
        self.importer = MacroStage3FixtureImporter(
            self.stores,
            self.stage3_manifest,
        )
        self.legacy_importer = MacroFixtureImporter(
            self.stores,
            FixtureManifest.load(FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT),
        )
        self.dispatcher = ToolDispatcher(self.stores, self.registry)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _v2_series_arguments(
        series_id: str,
        *,
        mode: str = "latest",
        as_of: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        date_only_policy: str = "completed_date",
    ) -> dict[str, object]:
        return {
            "series_id": series_id,
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": date_only_policy,
            "limit": 100,
            "start_date": start_date,
            "end_date": end_date,
        }

    @staticmethod
    def _calendar_arguments(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        event_name: str | None = None,
    ) -> dict[str, object]:
        return {
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": "completed_date",
            "limit": 100,
            "start_date": "2024-04-01",
            "end_date": "2024-06-30",
            "event_name": event_name,
        }

    def _v2_series(self, *args: object, **kwargs: object) -> dict[str, object]:
        return self.dispatcher.call(
            "macro.get_series",
            self._v2_series_arguments(*args, **kwargs),
            tool_version="2.0.0",
        )

    def _register_mutated_stage3_fixture(
        self,
        fixture_id: str,
        source_fixture_id: str,
        document: dict[str, object],
    ) -> None:
        """Register one in-memory Stage 3 fixture against this temporary store."""

        payload = dumps_strict(document).encode("utf-8")
        candidate = parse_stage3_macro_fixture(fixture_id, payload)
        request_scope = document["request_scope"]
        self.assertIsInstance(request_scope, dict)
        source = _fixture(source_fixture_id)
        self.stage3_manifest.fixtures[fixture_id] = replace(
            source,
            id=fixture_id,
            sha256=hashlib.sha256(payload).hexdigest(),
            expected_semantic_identity=candidate.semantic_identity,
            byte_count=len(payload),
            captured_at=str(document["captured_at"]),
            request_scope=dict(request_scope),
            bytes=payload,
        )

    def _insert_legacy_calendar_event(self) -> None:
        row = _row(
            "GDP Test",
            "2024-04-25 12:30:00",
            currency=None,
            actual="1.0",
        )
        response = _body([row])
        raw_row_json = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        semantic = legacy_digest("canonical-access-legacy-calendar")
        scope = '{"fixture":"canonical_access_reconciliation"}'
        connection = sqlite3.connect(self.stores.macro)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            insert_legacy_run(
                connection,
                run_id="canonical-access-legacy-run",
                dataset_id=LEGACY_WHOLESALE_DATASET,
                semantic_identity=semantic,
                command=LEGACY_WHOLESALE_COMMAND,
                scope_json=scope,
            )
            insert_legacy_output(
                connection,
                run_id="canonical-access-legacy-run",
                dataset_id=LEGACY_WHOLESALE_DATASET,
                semantic_identity=semantic,
            )
            insert_legacy_capture(
                connection,
                capture_id="canonical-access-legacy-capture",
                run_id="canonical-access-legacy-run",
                artifact_id="canonical-access-legacy-artifact",
                snapshot_id="canonical-access-legacy-snapshot",
                response=response,
                semantic_identity=semantic,
                start_date="2024-04-01",
                end_date="2024-06-30",
                row_count=1,
            )
            insert_legacy_row(
                connection,
                capture_id="canonical-access-legacy-capture",
                source_row=1,
                raw_row_json=raw_row_json,
                event_name="GDP Test",
            )
            insert_legacy_artifact(
                connection,
                artifact_id="canonical-access-legacy-artifact",
                run_id="canonical-access-legacy-run",
                response=response,
                scope_json=scope,
            )
            insert_legacy_snapshot(
                connection,
                snapshot_id="canonical-access-legacy-snapshot",
                run_id="canonical-access-legacy-run",
                semantic_identity=semantic,
                scope_json=scope,
                row_count=1,
            )
            connection.execute(
                """
                INSERT INTO ingestion_snapshot_artifacts (
                    snapshot_id, artifact_id, artifact_ordinal
                ) VALUES (
                    'canonical-access-legacy-snapshot',
                    'canonical-access-legacy-artifact',
                    1
                )
                """
            )
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status='succeeded', completed_at=?,
                    artifact_id='canonical-access-legacy-artifact',
                    snapshot_id='canonical-access-legacy-snapshot'
                WHERE run_id='canonical-access-legacy-run' AND status='running'
                """,
                (LEGACY_CAPTURED_AT,),
            )
            connection.commit()
        finally:
            connection.close()

    def test_generic_catalog_describe_and_selection_are_public_and_read_only(self) -> None:
        self.importer.import_fixture("treasury_base")
        self.importer.import_fixture("treasury_correction")
        before = mutation_fingerprint(self.stores)

        search = self.dispatcher.call(
            "macro.search_series",
            {"query": "Treasury par yield 2Y", "limit": 10},
            tool_version="2.0.0",
        )
        self.assertEqual(search["contract"], "quant_data.query_result")
        self.assertEqual(search["tool"], "macro.search_series")
        self.assertEqual(len(search["records"]), 1)
        descriptor = _fields(search["records"][0])
        self.assertEqual(descriptor["series_id"], "macro.treasury.par_yield.2y")
        self.assertEqual(descriptor["storage_model"], "generic_version_core")
        self.assertEqual(
            set(json.loads(str(descriptor["supported_modes_json"]))),
            {"latest", "as_of"},
        )

        describe = self.dispatcher.call(
            "macro.describe_series",
            {"series_id": "macro.treasury.par_yield.2y", "limit": 1},
            tool_version="2.0.0",
        )
        self.assertEqual(describe["tool"], "macro.describe_series")
        self.assertEqual(_fields(describe["records"][0]), descriptor)

        latest = self._v2_series(
            "macro.treasury.par_yield.2y",
            start_date="2026-07-17",
            end_date="2026-07-17",
        )
        as_of = self._v2_series(
            "macro.treasury.par_yield.2y",
            mode="as_of",
            as_of="2026-07-19T12:00:00-04:00",
            start_date="2026-07-17",
            end_date="2026-07-17",
        )
        latest_series = latest["series"][0]
        as_of_series = as_of["series"][0]
        self.assertEqual(latest_series["contract"], "quant_data.macro_timeseries")
        self.assertEqual(latest_series["contract_version"], "2.0.0")
        self.assertEqual(latest_series["observations"][0]["value"], Decimal("4.11"))
        self.assertEqual(as_of_series["observations"][0]["value"], Decimal("4.10"))
        self.assertEqual(as_of_series["audit"]["point_in_time_status"], "safe")
        self.assertEqual(
            latest_series["observations"][0]["evidence"]["kind"], "artifact"
        )
        self.assertEqual(
            {item["dataset_id"] for item in latest["lineage"]},
            {
                "fixture.macro.rtdsm_employ",
                "fixture.macro.rtdsm_employ_evidence",
                "fixture.macro.stage3_catalog",
            },
        )
        self.assertNotIn(str(self.root), json.dumps(latest, default=str))
        self.assertNotIn("database_path", json.dumps(latest, default=str))

        with self.assertRaisesRegex(ValidationError, "not evidenced"):
            self._v2_series(
                "macro.treasury.par_yield.2y",
                mode="first_release",
                start_date="2026-07-17",
                end_date="2026-07-17",
            )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_generic_first_release_discloses_partial_evidence_coverage(self) -> None:
        generic_series_id = "macro.fixture.gdp.real_qoq_saar_pct"
        advance = loads_strict(_fixture("gdp_advance").bytes)
        self.assertIsInstance(advance, dict)
        advance_payload = advance["payload"]
        self.assertIsInstance(advance_payload, dict)
        advance_series = advance_payload["series"]
        advance_observations = advance_payload["observations"]
        self.assertIsInstance(advance_series, list)
        self.assertIsInstance(advance_observations, list)
        advance_series[0]["series_id"] = generic_series_id
        advance_observations[0]["series_id"] = generic_series_id
        advance_fixture_id = "canonical_access_gdp_advance_generic_first_release"
        self._register_mutated_stage3_fixture(
            advance_fixture_id,
            "gdp_advance",
            advance,
        )
        self.importer.import_fixture(advance_fixture_id)

        document = loads_strict(_fixture("gdp_second").bytes)
        self.assertIsInstance(document, dict)
        payload = document["payload"]
        self.assertIsInstance(payload, dict)
        series = payload["series"]
        observations = payload["observations"]
        self.assertIsInstance(series, list)
        self.assertIsInstance(observations, list)
        series[0]["series_id"] = generic_series_id
        observations[0]["series_id"] = generic_series_id
        observations.append(
            {
                "series_id": generic_series_id,
                "release_identity": "2026-05-29:second",
                "period_start": "2025-10-01",
                "period_end": "2025-12-31",
                "dimensions": {},
                "value": "2.0",
                "missing_reason": None,
                "source_row": 3,
            }
        )
        fixture_id = "canonical_access_gdp_second_partial_first_release"
        self._register_mutated_stage3_fixture(
            fixture_id,
            "gdp_second",
            document,
        )
        self.importer.import_fixture(fixture_id)
        before = mutation_fingerprint(self.stores)

        result = self._v2_series(
            generic_series_id,
            mode="first_release",
            start_date="2025-10-01",
            end_date="2026-03-31",
        )
        repeated = self._v2_series(
            generic_series_id,
            mode="first_release",
            start_date="2025-10-01",
            end_date="2026-03-31",
        )

        series = result["series"][0]
        self.assertEqual(
            [item["value"] for item in series["observations"]],
            [Decimal("1.8")],
        )
        self.assertEqual(
            set(series["warnings"]),
            {"first_release_evidence_incomplete"},
        )
        self.assertEqual(
            {item["code"] for item in result["warnings"]},
            {"first_release_evidence_incomplete"},
        )
        metrics = _metrics(result["diagnostics"][0])
        self.assertEqual(metrics["first_release_candidate_group_count"], 2)
        self.assertEqual(metrics["first_release_evidenced_group_count"], 1)
        self.assertEqual(
            metrics["first_release_missing_evidence_group_count"],
            1,
        )
        self.assertEqual(
            series["provenance"]["receipt_sha256"],
            repeated["series"][0]["provenance"]["receipt_sha256"],
        )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_generic_as_of_tracks_tombstone_then_restoration(self) -> None:
        self.importer.import_fixture("eia_retail_base")
        self.importer.import_fixture("eia_retail_omission")
        self.importer.import_fixture("eia_retail_restore")
        before = mutation_fingerprint(self.stores)

        before_tombstone = self._v2_series(
            "macro.eia.electricity.retail_sales",
            mode="as_of",
            as_of="2026-06-24",
        )
        tombstoned = self._v2_series(
            "macro.eia.electricity.retail_sales",
            mode="as_of",
            as_of="2026-06-25",
        )
        restored = self._v2_series(
            "macro.eia.electricity.retail_sales",
            mode="as_of",
            as_of="2026-07-01",
        )

        self.assertEqual(
            len(before_tombstone["series"][0]["observations"]),
            2,
        )
        self.assertEqual(tombstoned["series"][0]["observations"], [])
        self.assertEqual(
            [item["value"] for item in restored["series"][0]["observations"]],
            [Decimal("346.0")],
        )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_generic_date_only_same_day_intraday_policy_is_explicit(self) -> None:
        generic_series_id = "macro.fixture.gdp.real_qoq_saar_pct"
        document = loads_strict(_fixture("gdp_advance").bytes)
        self.assertIsInstance(document, dict)
        payload = document["payload"]
        self.assertIsInstance(payload, dict)
        series = payload["series"]
        observations = payload["observations"]
        self.assertIsInstance(series, list)
        self.assertIsInstance(observations, list)
        series[0]["series_id"] = generic_series_id
        observations[0]["series_id"] = generic_series_id
        fixture_id = "canonical_access_gdp_advance_generic_date_only"
        self._register_mutated_stage3_fixture(
            fixture_id,
            "gdp_advance",
            document,
        )
        self.importer.import_fixture(fixture_id)
        before = mutation_fingerprint(self.stores)

        completed_date = self._v2_series(
            generic_series_id,
            mode="as_of",
            as_of="2026-04-30T10:00:00-04:00",
        )
        inclusive = self._v2_series(
            generic_series_id,
            mode="as_of",
            as_of="2026-04-30T10:00:00-04:00",
            date_only_policy="calendar_date_inclusive",
        )

        self.assertEqual(completed_date["series"][0]["observations"], [])
        self.assertEqual(
            [item["value"] for item in inclusive["series"][0]["observations"]],
            [Decimal("1.8")],
        )
        self.assertEqual(
            inclusive["series"][0]["audit"]["point_in_time_status"],
            "unsafe",
        )
        self.assertEqual(
            inclusive["series"][0]["audit"]["unsafe_reasons"],
            ["date_only_same_day_intraday_safety_not_established"],
        )
        self.assertIn(
            "date_only_same_day_intraday_safety_not_established",
            {item["code"] for item in inclusive["warnings"]},
        )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_explicit_v2_uses_official_vintages_without_generic_fallback(self) -> None:
        self.importer.import_fixture("gdp_advance")
        self.legacy_importer.import_fixture("macro.first_vintage")
        legacy = self.dispatcher.call(
            "macro.get_series",
            {
                "series_id": "fixture:philadelphia_fed_rtdsm:EMPLOY",
                "start_date": "2026-01-01",
                "end_date": "2026-02-28",
                "vintage_mode": "latest",
                "as_of": None,
                "date_only_policy": "completed_date",
                "limit": 100,
            },
            tool_version="1.0.0",
        )
        self.assertEqual(legacy["contract"], "quant_data.timeseries")
        self.assertEqual(legacy["observations"][0]["value"], Decimal("100.0"))

        absent_search = self.dispatcher.call(
            "macro.search_series",
            {"query": GDP_REAL_SERIES_ID, "limit": 10},
            tool_version="2.0.0",
        )
        self.assertEqual(absent_search["records"], [])
        with self.assertRaisesRegex(ValidationError, "official-vintage macro series is not populated"):
            self._v2_series(GDP_REAL_SERIES_ID)

        publisher = MacroLiveVintagePublisher(
            market_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )
        report = publisher.publish(
            parse_bea_gdp_vintage_xlsx(
                _workbook(),
                captured_at="2026-08-24T12:00:00Z",
            )
        )
        self.assertEqual(report.outcome, "published")
        before = mutation_fingerprint(self.stores)

        latest = self._v2_series(GDP_REAL_SERIES_ID)
        as_of = self._v2_series(
            GDP_REAL_SERIES_ID,
            mode="as_of",
            as_of="2024-05-01",
        )
        first = self._v2_series(GDP_REAL_SERIES_ID, mode="first_release")
        inclusive_intraday = self._v2_series(
            GDP_REAL_SERIES_ID,
            mode="as_of",
            as_of="2024-04-25T12:00:00Z",
            date_only_policy="calendar_date_inclusive",
        )
        latest_series = latest["series"][0]
        as_of_series = as_of["series"][0]
        first_series = first["series"][0]
        self.assertEqual(latest_series["metadata"]["storage_model"], "official_vintage")
        self.assertEqual(latest_series["observations"][0]["value"], Decimal("1.5"))
        self.assertEqual(as_of_series["observations"][0]["value"], Decimal("1.6"))
        self.assertEqual(first_series["observations"][0]["value"], Decimal("1.6"))
        observation = first_series["observations"][0]
        self.assertEqual(observation["evidence"]["kind"], "capture")
        self.assertIsNone(observation["evidence"]["snapshot_id"])
        self.assertIsNone(observation["evidence"]["run_id"])
        self.assertTrue(observation["release"]["is_first_release"])
        inclusive_series = inclusive_intraday["series"][0]
        self.assertEqual(inclusive_series["audit"]["point_in_time_status"], "unsafe")
        self.assertEqual(
            inclusive_series["audit"]["unsafe_reasons"],
            ["date_only_same_day_intraday_safety_not_established"],
        )
        self.assertIn(
            "date_only_same_day_intraday_safety_not_established",
            {item["code"] for item in inclusive_intraday["warnings"]},
        )
        self.assertEqual(
            {item["dataset_id"] for item in latest["lineage"]},
            {"macro.official_vintages", "macro.official_vintages_evidence"},
        )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_generic_description_excludes_current_tombstones(self) -> None:
        self.importer.import_fixture("eia_retail_base")
        self.importer.import_fixture("eia_retail_omission")

        described = self.dispatcher.call(
            "macro.describe_series",
            {
                "series_id": "macro.eia.electricity.retail_sales",
                "limit": 1,
            },
            tool_version="2.0.0",
        )
        descriptor = _fields(described["records"][0])
        self.assertEqual(descriptor["observation_count"], 0)
        self.assertIsNone(descriptor["coverage_start"])
        self.assertIsNone(descriptor["coverage_end"])
        latest = self._v2_series("macro.eia.electricity.retail_sales")
        self.assertEqual(latest["series"][0]["observations"], [])

    def test_get_series_charges_one_series_to_the_host_budget(self) -> None:
        self.importer.import_fixture("treasury_base")
        with patch.object(
            ExecutionBudget,
            "require",
            autospec=True,
        ) as require:
            result = self._v2_series("macro.treasury.par_yield.2y")

        self.assertEqual(len(result["series"]), 1)
        self.assertEqual(require.call_count, 2)
        self.assertEqual(
            require.call_args_list[-1].kwargs,
            {"rows": 100, "series": 1, "operations": 100},
        )

    def test_macro_gateway_rejects_migration_ledger_drift(self) -> None:
        self.importer.import_fixture("treasury_base")
        connection = sqlite3.connect(self.stores.macro)
        try:
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=4",
                ("a" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            StoreUnavailableError,
            "migration ledger",
        ):
            self.dispatcher.call(
                "macro.describe_series",
                {
                    "series_id": "macro.treasury.par_yield.2y",
                    "limit": 1,
                },
                tool_version="2.0.0",
            )

    def test_macro_gateway_rejects_dataset_contract_drift(self) -> None:
        self.importer.import_fixture("treasury_base")
        connection = sqlite3.connect(self.stores.macro)
        try:
            connection.execute("DROP TRIGGER dataset_registry_contract_immutable")
            connection.execute(
                """
                UPDATE dataset_registry
                SET relations_json='[]'
                WHERE dataset_id='fixture.macro.rtdsm_employ'
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            StoreUnavailableError,
            "dataset declarations",
        ):
            self._v2_series("macro.treasury.par_yield.2y")

    def test_latest_truncation_reports_the_exact_total(self) -> None:
        document = loads_strict(_fixture("eia_retail_base").bytes)
        self.assertIsInstance(document, dict)
        payload = document["payload"]
        self.assertIsInstance(payload, dict)
        observations = payload["observations"]
        self.assertIsInstance(observations, list)
        observations.append(
            {
                "series_id": "macro.eia.electricity.retail_sales",
                "release_identity": "2026-06-20",
                "period_start": "2026-05-01",
                "period_end": "2026-05-31",
                "dimensions": {"area": "US", "measure": "customers"},
                "value": "12.0",
                "missing_reason": None,
                "source_row": 3,
            }
        )
        retail_snapshot = payload["retail_snapshot"]
        self.assertIsInstance(retail_snapshot, dict)
        pages = retail_snapshot["pages"]
        self.assertIsInstance(pages, list)
        pages[0]["row_count"] = 3
        fixture_id = "canonical_access_eia_retail_three"
        self.stage3_manifest.fixtures[fixture_id] = (
            _mutated_complete_retail_fixture(fixture_id, document)
        )
        self.importer.import_fixture(fixture_id)

        arguments = self._v2_series_arguments(
            "macro.eia.electricity.retail_sales"
        )
        arguments["limit"] = 1
        result = self.dispatcher.call(
            "macro.get_series",
            arguments,
            tool_version="2.0.0",
        )
        self.assertEqual(len(result["series"][0]["observations"]), 1)
        self.assertEqual(result["series"][0]["audit"]["total_selected_count"], 3)
        self.assertEqual(result["truncation"]["total_known_count"], 3)
        self.assertTrue(result["truncation"]["has_more"])

    def test_release_calendar_latest_as_of_and_first_release_rejection_are_read_only(self) -> None:
        self._insert_legacy_calendar_event()
        publisher = FmpWholesaleCalendarPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )
        first_capture = parse_fmp_us_calendar_wholesale(
            _body(
                [
                    _row(
                        "GDP Test",
                        "2024-04-25 12:30:00",
                        currency=None,
                        actual="1.5",
                    )
                ]
            ),
            captured_at="2026-08-19T18:00:00Z",
            start_date="2024-04-01",
            end_date="2024-06-30",
        )
        second_capture = parse_fmp_us_calendar_wholesale(
            _body(
                [
                    _row(
                        "GDP Test",
                        "2024-04-25 12:30:00",
                        currency=None,
                        actual="2.0",
                    )
                ]
            ),
            captured_at="2026-08-20T18:00:00Z",
            start_date="2024-04-01",
            end_date="2024-06-30",
        )
        self.assertEqual(publisher.publish(first_capture).outcome, "published")
        self.assertEqual(publisher.publish(second_capture).outcome, "published")
        before = mutation_fingerprint(self.stores)

        latest = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(event_name="gdp"),
        )
        legacy_as_of = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(
                mode="as_of",
                as_of="2026-08-18T18:01:00Z",
                event_name="gdp",
            ),
        )
        incremental_as_of = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(
                mode="as_of",
                as_of="2026-08-19T18:01:00Z",
                event_name="gdp",
            ),
        )
        self.assertEqual(latest["contract"], "quant_data.query_result")
        self.assertEqual(len(latest["records"]), 1)
        latest_fields = _fields(latest["records"][0])
        legacy_fields = _fields(legacy_as_of["records"][0])
        incremental_fields = _fields(incremental_as_of["records"][0])
        self.assertEqual(latest_fields["source_model"], "incremental_event_version")
        self.assertEqual(legacy_fields["source_model"], "legacy_wholesale_capture")
        self.assertEqual(
            legacy_fields["event_id"], incremental_fields["event_id"]
        )
        self.assertEqual(latest_fields["availability_basis"], "local_capture")
        self.assertEqual(json.loads(str(latest_fields["actual_json"])), "2.0")
        self.assertEqual(
            json.loads(str(incremental_fields["actual_json"])), "1.5"
        )
        calendar_audit = _metrics(incremental_as_of["diagnostics"][0])
        self.assertEqual(calendar_audit["requested_mode"], "as_of")
        self.assertEqual(calendar_audit["actual_mode"], "as_of")
        self.assertEqual(calendar_audit["cutoff"], "2026-08-19T18:01:00Z")
        self.assertEqual(calendar_audit["cutoff_precision"], "datetime")
        self.assertEqual(calendar_audit["date_only_policy"], "completed_date")
        self.assertEqual(calendar_audit["availability_basis"], "local_capture")
        self.assertEqual(calendar_audit["point_in_time_status"], "safe")
        self.assertEqual(calendar_audit["unsafe_reasons"], "[]")
        self.assertEqual(
            {item["dataset_id"] for item in latest["lineage"]},
            {
                "macro.fmp.economic_calendar_evidence",
                "macro.fmp.economic_calendar_incremental_evidence",
                "macro.fmp.economic_calendar_incremental_events",
            },
        )
        with self.assertRaises(ValidationError):
            self.dispatcher.call(
                "macro.get_release_calendar",
                self._calendar_arguments(mode="first_release"),
            )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_release_calendar_date_only_cutoff_includes_datetime_capture_day(self) -> None:
        self._insert_legacy_calendar_event()
        before = mutation_fingerprint(self.stores)

        before_capture_day = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(
                mode="as_of",
                as_of="2026-08-17",
                event_name="gdp",
            ),
        )
        on_capture_day = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(
                mode="as_of",
                as_of="2026-08-18",
                event_name="gdp",
            ),
        )

        self.assertEqual(before_capture_day["records"], [])
        self.assertEqual(len(on_capture_day["records"]), 1)
        metrics = _metrics(on_capture_day["diagnostics"][0])
        self.assertEqual(metrics["cutoff_precision"], "date")
        self.assertEqual(metrics["point_in_time_status"], "safe")
        self.assertNotIn(
            "date_only_same_day_intraday_safety_not_established",
            {item["code"] for item in on_capture_day["warnings"]},
        )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_macro_and_calendar_direct_http_results_are_identical(self) -> None:
        self.importer.import_fixture("treasury_base")
        publisher = FmpWholesaleCalendarPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )
        self.assertEqual(
            publisher.publish(
                parse_fmp_us_calendar_wholesale(
                    _body([_row("GDP HTTP", "2024-04-25 12:30:00")]),
                    captured_at="2026-08-19T18:00:00Z",
                    start_date="2024-04-01",
                    end_date="2024-06-30",
                )
            ).outcome,
            "published",
        )
        cases = (
            ("macro.search_series", {"query": "Treasury", "limit": 10}, "2.0.0"),
            (
                "macro.describe_series",
                {"series_id": "macro.treasury.par_yield.2y", "limit": 1},
                "2.0.0",
            ),
            (
                "macro.get_series",
                self._v2_series_arguments("macro.treasury.par_yield.2y"),
                "2.0.0",
            ),
            (
                "macro.get_release_calendar",
                self._calendar_arguments(event_name="gdp http"),
                None,
            ),
        )
        application = Stage1Application(self.stores, self.registry)
        before = mutation_fingerprint(self.stores)
        for tool_name, arguments, tool_version in cases:
            direct = self.dispatcher.call(
                tool_name,
                arguments,
                tool_version=tool_version,
            )
            envelope: dict[str, object] = {
                "api_version": "1.0",
                "tool": tool_name,
                "arguments": arguments,
            }
            if tool_version is not None:
                envelope["tool_version"] = tool_version
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(envelope).encode("utf-8"),
            )
            self.assertEqual(response.status, 200, tool_name)
            http = loads_strict(response.body)
            self.assertEqual(
                dumps_strict(http["result"]),
                dumps_strict(direct),
                tool_name,
            )
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_release_calendar_same_capture_time_uses_correction_sequence(self) -> None:
        publisher = FmpWholesaleCalendarPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )
        for actual in ("1.5", "2.0"):
            capture = parse_fmp_us_calendar_wholesale(
                _body([_row("A", "2024-04-25 12:30:00", actual=actual)]),
                captured_at="2026-08-19T18:00:00Z",
                start_date="2024-04-01",
                end_date="2024-06-30",
            )
            self.assertEqual(publisher.publish(capture).outcome, "published")

        result = self.dispatcher.call(
            "macro.get_release_calendar",
            self._calendar_arguments(event_name="A"),
        )
        fields = _fields(result["records"][0])
        self.assertEqual(fields["correction_sequence"], 2)
        self.assertEqual(json.loads(str(fields["actual_json"])), "2.0")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
