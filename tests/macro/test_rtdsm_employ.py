from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import logical_manifest, store_logical_manifest
from quant_data.fixtures import Fixture, FixtureManifest
from quant_data.json_codec import dumps_strict
from quant_data.macro import MacroFixtureImporter, MacroSeriesQuery, MacroSeriesRepository
from quant_data.macro.rtdsm_fixture import parse_rtdsm_fixture
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
SERIES_ID = "fixture:philadelphia_fed_rtdsm:EMPLOY"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


@dataclass(frozen=True)
class _SingleFixtureManifest:
    fixture: Fixture

    def get(self, fixture_id: str) -> Fixture:
        if fixture_id != self.fixture.id:
            raise ValidationError("Unknown fixture ID")
        return self.fixture


class RtdsmEmployTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT)
        self.importer = MacroFixtureImporter(self.store_map, self.manifest)
        self.repository = MacroSeriesRepository(self.store_map, self.registry)

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        finally:
            assert_project_data_unchanged(
                self._project_data_before,
                project_root=PROJECT_ROOT,
            )

    def _counts(self) -> dict[str, int]:
        relations = (
            "macro_series",
            "macro_releases",
            "macro_source_artifacts",
            "macro_source_snapshots",
            "macro_snapshot_scopes",
            "macro_observation_versions",
            "macro_snapshot_observation_membership",
            "ingestion_runs",
        )
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            return {
                relation: int(connection.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])
                for relation in relations
            }

    def _query(
        self,
        mode: str,
        *,
        as_of: str | None = None,
        policy: str = "completed_date",
        limit: int = 100,
    ):
        return self.repository.get_series(
            MacroSeriesQuery(
                SERIES_ID,
                "2026-01-01",
                "2026-02-28",
                mode,
                as_of,
                policy,
                limit,
            )
        )

    def _fixture_from_bytes(
        self,
        *,
        fixture_id: str,
        payload: bytes,
        captured_at: str = "2026-06-13T17:00:00-04:00",
        request_scope: dict[str, object] | None = None,
    ) -> Fixture:
        resource = self.root / f"{fixture_id}.csv"
        resource.write_bytes(payload)
        return Fixture(
            id=fixture_id,
            fixture_schema_version="1.0.0",
            ingestion_family_id="fixture.macro.rtdsm_employ_import",
            evidence_dataset_id="fixture.macro.rtdsm_employ_evidence",
            canonical_dataset_id="fixture.macro.rtdsm_employ",
            identity_dataset_id=None,
            store="macro",
            provider="fixture_philadelphia_fed",
            resource=resource,
            resource_name=f"temporary/{fixture_id}.csv",
            sha256=hashlib.sha256(payload).hexdigest(),
            expected_semantic_identity="0" * 64,
            byte_count=len(payload),
            captured_at=captured_at,
            test_fixture=True,
            promotable=False,
            expected_warnings=(),
            request_scope=request_scope
            or {
                "series_id": SERIES_ID,
                "source_vintage_identity": "2026-06-12",
                "completeness": "complete",
            },
            metadata={
                "normalization_version": "macro_rtdsm_v1",
                "expected_rows": 2,
                "expected_missing": 1,
            },
            bytes=payload,
        )

    def test_mac_rtdsm_001_first_vintage_and_exact_replay_are_total_no_write(self) -> None:
        receipt = self.importer.import_fixture("macro.first_vintage")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertEqual(receipt.written_count, 2)
        self.assertEqual(
            self._counts(),
            {
                "macro_series": 1,
                "macro_releases": 1,
                "macro_source_artifacts": 1,
                "macro_source_snapshots": 1,
                "macro_snapshot_scopes": 1,
                "macro_observation_versions": 2,
                "macro_snapshot_observation_membership": 2,
                "ingestion_runs": 1,
            },
        )
        before = logical_manifest(self.store_map, self.registry)
        replay = self.importer.import_fixture("macro.first_vintage")
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertIsNone(replay.run_id)
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(before["sha256"], after["sha256"])

    def test_mac_rtdsm_002_revised_vintage_and_query_modes(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        first = self._query("latest")
        self.assertEqual([item.value for item in first.observations], [100.0, None])
        self.assertEqual(first.observations[1].missing_reason, "source_suppressed")
        pre_revision_as_of = self._query("as_of", as_of="2026-07-09")
        pre_revision_as_of_json = dumps_strict(pre_revision_as_of.to_primitive())
        pre_revision_latest_receipt = first.provenance["store_receipt"]["sha256"]

        self.importer.import_fixture("macro.revised_vintage")
        self.assertEqual(
            self._counts(),
            {
                "macro_series": 1,
                "macro_releases": 2,
                "macro_source_artifacts": 2,
                "macro_source_snapshots": 2,
                "macro_snapshot_scopes": 2,
                "macro_observation_versions": 4,
                "macro_snapshot_observation_membership": 4,
                "ingestion_runs": 2,
            },
        )
        latest = self._query("latest")
        self.assertEqual([item.value for item in latest.observations], [101.0, 98.5])
        self.assertNotEqual(
            latest.provenance["store_receipt"]["sha256"], pre_revision_latest_receipt
        )
        as_of = self._query("as_of", as_of="2026-07-09")
        self.assertEqual([item.value for item in as_of.observations], [100.0, None])
        self.assertEqual(as_of.observations[1].missing_reason, "source_suppressed")
        self.assertEqual(dumps_strict(as_of.to_primitive()), pre_revision_as_of_json)
        self.assertEqual(
            as_of.provenance["store_receipt"]["sha256"],
            pre_revision_as_of.provenance["store_receipt"]["sha256"],
        )
        first_release = self._query("first_release")
        self.assertEqual([item.value for item in first_release.observations], [100.0, None])
        self.assertEqual(first_release.observations[0].vintage_at, "2026-06-12")
        self.assertEqual(latest.audit["mode"], "latest")
        self.assertEqual(latest.audit["period_range_rule"], "period_start")

    def test_time_rtdsm_date_only_matrix_and_same_day_warning(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        self.assertEqual(len(self._query("as_of", as_of="2026-06-11").observations), 0)
        self.assertEqual(len(self._query("as_of", as_of="2026-06-12").observations), 2)
        completed = self._query("as_of", as_of="2026-06-12T10:00:00-04:00")
        self.assertEqual(len(completed.observations), 0)
        next_day = self._query("as_of", as_of="2026-06-13T00:00:00-04:00")
        self.assertEqual(len(next_day.observations), 2)
        inclusive = self._query(
            "as_of",
            as_of="2026-06-12T10:00:00-04:00",
            policy="calendar_date_inclusive",
        )
        self.assertEqual(len(inclusive.observations), 2)
        self.assertIn("date_only_same_day_intraday_safety_not_established", inclusive.warnings)
        self.assertEqual(inclusive.audit["cutoff_precision"], "datetime")

    def test_mac_rtdsm_first_release_uses_source_order_not_ingestion_order(self) -> None:
        self.importer.import_fixture("macro.revised_vintage")
        self.importer.import_fixture("macro.first_vintage")
        self.assertEqual([item.value for item in self._query("latest").observations], [101.0, 98.5])
        self.assertEqual([item.value for item in self._query("first_release").observations], [100.0, None])

    def test_mac_rtdsm_same_vintage_correction_supersedes_without_redefining_first_release(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        original = self.manifest.get("macro.first_vintage")
        corrected_bytes = original.bytes.replace(b",100.0,\n", b",100.5,\n", 1).replace(
            b",2026-06-12,date,true,", b",2026-06-13,date,true,"
        )
        correction = self._fixture_from_bytes(
            fixture_id="macro.same_vintage_correction",
            payload=corrected_bytes,
        )
        receipt = MacroFixtureImporter(self.store_map, _SingleFixtureManifest(correction)).import_fixture(correction.id)
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertEqual(receipt.written_count, 2)
        self.assertEqual([item.value for item in self._query("latest").observations], [100.5, None])
        self.assertEqual(
            [item.value for item in self._query("as_of", as_of="2026-06-12").observations],
            [100.0, None],
        )
        self.assertEqual(
            [item.value for item in self._query("as_of", as_of="2026-06-13").observations],
            [100.5, None],
        )
        self.assertEqual([item.value for item in self._query("first_release").observations], [100.0, None])
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            chain = list(
                connection.execute(
                    """
                    SELECT correction_sequence, supersedes_version_id
                    FROM macro_observation_versions
                    WHERE period_start='2026-01-01'
                    ORDER BY correction_sequence
                    """
                )
            )
        self.assertEqual([row["correction_sequence"] for row in chain], [1, 2])
        self.assertIsNotNone(chain[1]["supersedes_version_id"])

    def test_mac_rtdsm_invalid_or_partial_fixture_fails_before_any_write(self) -> None:
        original = self.manifest.get("macro.first_vintage")
        invalid_bytes = original.bytes.replace(b",100.0,\n", b",100.0,source_suppressed\n", 1)
        invalid = self._fixture_from_bytes(fixture_id="macro.invalid_missingness", payload=invalid_bytes)
        before = logical_manifest(self.store_map, self.registry)
        with self.assertRaises(ValidationError):
            MacroFixtureImporter(self.store_map, _SingleFixtureManifest(invalid)).import_fixture(invalid.id)
        self.assertEqual(before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        partial = self._fixture_from_bytes(
            fixture_id="macro.partial",
            payload=original.bytes,
            request_scope={
                "series_id": SERIES_ID,
                "source_vintage_identity": "2026-06-12",
                "completeness": "partial",
            },
        )
        with self.assertRaises(ValidationError):
            parse_rtdsm_fixture(partial)
        self.assertEqual(before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

    def test_mac_rtdsm_validation_rejects_every_negative_before_coordinator_write(self) -> None:
        original = self.manifest.get("macro.first_vintage")
        lines = original.bytes.splitlines()
        unit_conflict = b"\n".join(
            (lines[0], lines[1], lines[2].replace(b"fixture_employment_index", b"other_unit", 1))
        ) + b"\n"
        invalid_period = original.bytes.replace(
            b"2026-02-01,2026-02-28", b"2026-02-01,2026-01-31", 1
        )
        naive_availability = original.bytes.replace(
            b",2026-06-12,date,true,", b",2026-06-12T10:00:00,datetime,true,"
        )
        duplicate = original.bytes + lines[1] + b"\n"
        fixtures = {
            "both_value_and_reason": self._fixture_from_bytes(
                fixture_id="macro.both_value_and_reason",
                payload=original.bytes.replace(b",100.0,\n", b",100.0,source_suppressed\n", 1),
            ),
            "neither_value_nor_reason": self._fixture_from_bytes(
                fixture_id="macro.neither_value_nor_reason",
                payload=original.bytes.replace(b",100.0,\n", b",,\n", 1),
            ),
            "nonfinite": self._fixture_from_bytes(
                fixture_id="macro.nonfinite",
                payload=original.bytes.replace(b",100.0,\n", b",NaN,\n", 1),
            ),
            "frequency": self._fixture_from_bytes(
                fixture_id="macro.frequency",
                payload=original.bytes.replace(b",monthly,", b",weekly,"),
            ),
            "unit_conflict": self._fixture_from_bytes(
                fixture_id="macro.unit_conflict", payload=unit_conflict
            ),
            "invalid_period": self._fixture_from_bytes(
                fixture_id="macro.invalid_period", payload=invalid_period
            ),
            "naive_availability": self._fixture_from_bytes(
                fixture_id="macro.naive_availability", payload=naive_availability
            ),
            "duplicate": self._fixture_from_bytes(
                fixture_id="macro.duplicate", payload=duplicate
            ),
        }
        fixtures["digest"] = replace(fixtures["nonfinite"], id="macro.bad_digest", sha256="0" * 64)
        before = logical_manifest(self.store_map, self.registry)
        for fixture in fixtures.values():
            with self.subTest(fixture=fixture.id):
                with self.assertRaises(ValidationError):
                    MacroFixtureImporter(self.store_map, _SingleFixtureManifest(fixture)).import_fixture(fixture.id)
                self.assertEqual(
                    before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"]
                )

    def test_semantic_identity_includes_title_and_conflicts_roll_back_instead_of_unchanged(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        original = self.manifest.get("macro.first_vintage")
        conflicting = self._fixture_from_bytes(
            fixture_id="macro.title_conflict",
            payload=original.bytes.replace(
                b"Fixture employment index", b"Conflicting employment index"
            ),
        )
        self.assertNotEqual(
            parse_rtdsm_fixture(original).semantic_identity,
            parse_rtdsm_fixture(conflicting).semantic_identity,
        )
        before = logical_manifest(self.store_map, self.registry)
        with self.assertRaises(ValidationError) as caught:
            MacroFixtureImporter(self.store_map, _SingleFixtureManifest(conflicting)).import_fixture(
                conflicting.id
            )
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertNotEqual(
            before["sha256"],
            logical_manifest(self.store_map, self.registry)["sha256"],
        )
        self.assertEqual(self._counts()["ingestion_runs"], 1)
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            failure = connection.execute(
                """
                SELECT status, error_code FROM ingestion_run_failures
                WHERE dataset_id=?
                """,
                ("fixture.macro.rtdsm_employ",),
            ).fetchone()
        self.assertEqual(tuple(failure), ("rejected", "validation_rejected"))

    def test_equivalent_decimal_spelling_is_a_semantic_total_no_write(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        original = self.manifest.get("macro.first_vintage")
        equivalent = self._fixture_from_bytes(
            fixture_id="macro.equivalent_decimal_spelling",
            payload=original.bytes.replace(b",100.0,\n", b",100.00,\n", 1),
        )
        self.assertEqual(
            parse_rtdsm_fixture(original).semantic_identity,
            parse_rtdsm_fixture(equivalent).semantic_identity,
        )
        before = logical_manifest(self.store_map, self.registry)
        receipt = MacroFixtureImporter(self.store_map, _SingleFixtureManifest(equivalent)).import_fixture(
            equivalent.id
        )
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertIsNone(receipt.run_id)
        self.assertEqual(before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

    def test_repository_is_read_only_bounded_strict_and_has_schema_exact_audit_provenance(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        before = logical_manifest(self.store_map, self.registry)
        result = self._query("latest", limit=1)
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertTrue(result.truncated)
        self.assertIn("result_truncated", result.warnings)
        self.assertEqual(
            set(result.audit),
            {
                "mode",
                "cutoff",
                "cutoff_precision",
                "date_only_policy",
                "availability_basis",
                "period_range_rule",
                "requested_start_date",
                "requested_end_date",
                "limit",
                "selected_count",
            },
        )
        self.assertEqual(set(result.provenance), {"dataset_id", "store_role", "registry_revision", "store_receipt"})
        self.assertEqual(set(result.provenance["store_receipt"]), {"migration_ids", "sha256"})
        primitive = result.to_primitive()
        self.assertEqual(primitive["contract"], "quant_data.timeseries")
        self.assertEqual(primitive["observations"][0]["value"], 100.0)
        self.assertEqual(primitive["observations"][0]["available_precision"], "date")
        self.assertEqual(primitive["observations"][0]["captured_precision"], "datetime")
        self.assertNotIn("NaN", dumps_strict(primitive))
        with self.assertRaises(ResourceLimitError):
            MacroSeriesQuery(SERIES_ID, "2026-01-01", "2026-02-28", "latest", None, "completed_date", 0)
        with self.assertRaises(ValidationError):
            MacroSeriesQuery(SERIES_ID, "2026-02-28", "2026-01-01", "latest", None, "completed_date", 1)
        with self.assertRaises(ValidationError):
            MacroSeriesQuery(SERIES_ID, "2026-01-01", "2026-02-28", "as_of", None, "completed_date", 1)

    def test_mac_rtdsm_integrity_and_two_clean_rebuilds_are_deterministic(self) -> None:
        self.importer.import_fixture("macro.first_vintage")
        self.importer.import_fixture("macro.revised_vintage")
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])

        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            first_map = temporary_store_map(first_root)
            second_map = temporary_store_map(second_root)
            initialize_all(first_map, self.registry)
            initialize_all(second_map, self.registry)
            first_importer = MacroFixtureImporter(first_map, self.manifest)
            second_importer = MacroFixtureImporter(second_map, self.manifest)
            for fixture_id in ("macro.first_vintage", "macro.revised_vintage"):
                first_importer.import_fixture(fixture_id)
                second_importer.import_fixture(fixture_id)
            self.assertEqual(
                logical_manifest(first_map, self.registry)["sha256"],
                logical_manifest(second_map, self.registry)["sha256"],
            )
        self.assertIsInstance(store_logical_manifest(self.store_map, self.registry, StoreRole.MACRO)["sha256"], str)


if __name__ == "__main__":
    unittest.main()
