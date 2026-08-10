"""Focused offline coverage for bounded Stage 3 macro fixture ingestion."""

from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.fingerprint import logical_manifest, mutation_fingerprint
from quant_data.fixtures import Fixture
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage3_fixture_importers import _FAMILY_SPECS, MacroStage3FixtureImporter
from quant_data.macro.stage3_normalizers import parse_stage3_macro_fixture
from quant_data.macro.stage3_repository import (
    MacroStage3RecessionQuery,
    MacroStage3Repository,
    MacroStage3SeriesQuery,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "stage3" / "macro"


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


@dataclass(frozen=True)
class _FixtureManifest:
    fixtures: dict[str, Fixture]

    def get(self, fixture_id: str) -> Fixture:
        try:
            return self.fixtures[fixture_id]
        except KeyError as exc:
            raise ValidationError("Unknown Stage 3 fixture") from exc


def _fixture(name: str) -> Fixture:
    resource = FIXTURE_ROOT / f"{name}.json"
    payload = resource.read_bytes()
    document = loads_strict(payload)
    assert isinstance(document, dict)
    family = document["family"]
    assert isinstance(family, str)
    spec = _FAMILY_SPECS[family]
    if name == "eia_retail_partial":
        expected_identity = "0" * 64
    else:
        expected_identity = parse_stage3_macro_fixture(name, payload).semantic_identity
    return Fixture(
        id=name,
        fixture_schema_version="1.0.0",
        ingestion_family_id=spec.collector_id,
        evidence_dataset_id=spec.evidence_dataset_id,
        canonical_dataset_id=spec.canonical_dataset_id,
        identity_dataset_id=spec.identity_dataset_id,
        store="macro",
        provider=str(document["provider"]),
        resource=resource,
        resource_name=resource.relative_to(PROJECT_ROOT).as_posix(),
        sha256=hashlib.sha256(payload).hexdigest(),
        expected_semantic_identity=expected_identity,
        byte_count=len(payload),
        captured_at=str(document["captured_at"]),
        test_fixture=True,
        promotable=False,
        expected_warnings=(),
        request_scope=dict(document["request_scope"]),
        metadata={"normalization_version": "stage3_macro_v1"},
        bytes=payload,
    )


def _mutated_complete_retail_fixture(fixture_id: str, document: dict[str, object]) -> Fixture:
    """Build an in-memory reviewed fixture so validation, not its pin, fails."""

    payload = dumps_strict(document).encode("utf-8")
    candidate = parse_stage3_macro_fixture(fixture_id, payload)
    request_scope = document["request_scope"]
    assert isinstance(request_scope, dict)
    return replace(
        _fixture("eia_retail_base"),
        id=fixture_id,
        sha256=hashlib.sha256(payload).hexdigest(),
        expected_semantic_identity=candidate.semantic_identity,
        byte_count=len(payload),
        captured_at=str(document["captured_at"]),
        request_scope=dict(request_scope),
        bytes=payload,
    )


class Stage3MacroFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.store_map = _store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        fixtures = {
            path.stem: _fixture(path.stem)
            for path in sorted(FIXTURE_ROOT.glob("*.json"))
        }
        self.fixture_manifest = _FixtureManifest(fixtures)
        self.importer = MacroStage3FixtureImporter(self.store_map, self.fixture_manifest)
        self.repository = MacroStage3Repository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _series(
        self,
        series_id: str,
        mode: str,
        *,
        as_of: str | None = None,
        policy: str = "completed_date",
    ):
        return self.repository.get_series(
            MacroStage3SeriesQuery(
                series_id=series_id,
                start_date="2026-01-01",
                end_date="2026-12-31",
                vintage_mode=mode,
                as_of=as_of,
                date_only_policy=policy,
                limit=100,
            )
        )

    def _count(self, relation: str) -> int:
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])

    def test_gdp_latest_as_of_first_release_and_later_correction(self) -> None:
        first = self.importer.import_fixture("gdp_advance")
        self.assertEqual(first.outcome, "succeeded")
        before_replay = logical_manifest(self.store_map, self.registry)
        replay = self.importer.import_fixture("gdp_advance")
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(before_replay["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        series_id = "macro.gdp.real_qoq_saar_pct"
        self.assertEqual([item.value for item in self._series(series_id, "latest").observations], [Decimal("1.8")])
        self.assertEqual(len(self._series(series_id, "as_of", as_of="2026-04-29").observations), 0)
        self.assertEqual([item.value for item in self._series(series_id, "as_of", as_of="2026-04-30").observations], [Decimal("1.8")])
        self.assertEqual(
            len(self._series(series_id, "as_of", as_of="2026-04-30T10:00:00-04:00").observations),
            0,
        )
        self.assertEqual([item.value for item in self._series(series_id, "first_release").observations], [Decimal("1.8")])

        self.importer.import_fixture("gdp_second")
        before_correction = self._series(series_id, "as_of", as_of="2026-05-29")
        before_correction_json = dumps_strict(before_correction.to_primitive())
        self.assertEqual([item.value for item in before_correction.observations], [Decimal("2.1")])
        self.importer.import_fixture("gdp_second_correction")
        self.assertEqual([item.value for item in self._series(series_id, "latest").observations], [Decimal("2.2")])
        self.assertEqual(
            dumps_strict(self._series(series_id, "as_of", as_of="2026-05-29").to_primitive()),
            before_correction_json,
        )
        self.assertEqual([item.value for item in self._series(series_id, "first_release").observations], [Decimal("1.8")])
        self.assertEqual(self._count("gdp_vintages"), 2)

    def test_treasury_local_capture_correction_and_first_release_rejection(self) -> None:
        series_id = "macro.treasury.par_yield.2y"
        self.importer.import_fixture("treasury_base")
        self.assertEqual([item.value for item in self._series(series_id, "latest").observations], [Decimal("4.10")])
        with self.assertRaises(ValidationError):
            self._series(series_id, "first_release")
        self.importer.import_fixture("treasury_correction")
        self.assertEqual([item.value for item in self._series(series_id, "latest").observations], [Decimal("4.11")])
        self.assertEqual(
            [item.value for item in self._series(series_id, "as_of", as_of="2026-07-19T12:00:00-04:00").observations],
            [Decimal("4.10")],
        )
        self.assertEqual(self._count("treasury_yield_curves"), 1)
        self.assertEqual(self._count("treasury_yield_curve_versions"), 3)

    def test_source_specific_no_write_gates_and_generic_semantic_exclusions(self) -> None:
        before = logical_manifest(self.store_map, self.registry)
        with self.assertRaises(ValidationError):
            self.importer.import_fixture("eia_retail_partial")
        self.assertEqual(before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        self.importer.import_fixture("bls_base")
        bls_before = logical_manifest(self.store_map, self.registry)
        self.assertEqual(self.importer.import_fixture("bls_response_time_only").outcome, "unchanged")
        self.assertEqual(bls_before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        self.importer.import_fixture("bea_base")
        bea_before = logical_manifest(self.store_map, self.registry)
        self.assertEqual(self.importer.import_fixture("bea_utc_only").outcome, "unchanged")
        self.assertEqual(bea_before["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        self.assertEqual(self.importer.import_fixture("economic_calendar").outcome, "succeeded")
        self.assertEqual(self.importer.import_fixture("bis_base").outcome, "succeeded")
        self.assertEqual(self.importer.import_fixture("chicagofed_base").outcome, "succeeded")
        self.assertEqual(self.importer.import_fixture("chicagofed_scope_change").outcome, "succeeded")

    def test_eia_retail_authoritative_pagination_and_scope_fail_before_any_write(self) -> None:
        source = loads_strict(_fixture("eia_retail_base").bytes)
        assert isinstance(source, dict)

        def altered() -> dict[str, object]:
            document = copy.deepcopy(source)
            assert isinstance(document, dict)
            return document

        def snapshot(document: dict[str, object]) -> dict[str, object]:
            payload = document["payload"]
            assert isinstance(payload, dict)
            retail_snapshot = payload["retail_snapshot"]
            assert isinstance(retail_snapshot, dict)
            return retail_snapshot

        gap = altered()
        snapshot(gap)["pages"] = [
            {"page": 1, "row_count": 1},
            {"page": 3, "row_count": 1},
        ]
        duplicate = altered()
        snapshot(duplicate)["pages"] = [
            {"page": 1, "row_count": 1},
            {"page": 1, "row_count": 1},
        ]
        under_report = altered()
        snapshot(under_report)["pages"] = [{"page": 1, "row_count": 1}]
        over_report = altered()
        snapshot(over_report)["pages"] = [{"page": 1, "row_count": 3}]
        wrong_area_scope = altered()
        snapshot(wrong_area_scope)["scope"] = "CA:2026-05"
        wrong_period_scope = altered()
        snapshot(wrong_period_scope)["scope"] = "US:2026-04"

        cases = {
            "page_gap": gap,
            "duplicate_page": duplicate,
            "row_count_under": under_report,
            "row_count_over": over_report,
            "scope_area": wrong_area_scope,
            "scope_period": wrong_period_scope,
        }
        for label, document in cases.items():
            with self.subTest(label=label):
                fixture_id = f"eia_retail_invalid_authority_{label}"
                self.fixture_manifest.fixtures[fixture_id] = _mutated_complete_retail_fixture(
                    fixture_id,
                    document,
                )
                before = mutation_fingerprint(self.store_map)
                with self.assertRaises(ValidationError):
                    self.importer.import_fixture(fixture_id)
                self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_eia_retail_explicit_missing_partial_omission_tombstone_and_restoration(self) -> None:
        series_id = "macro.eia.electricity.retail_sales"
        self.importer.import_fixture("eia_retail_base")
        base = self._series(series_id, "latest")
        self.assertEqual([item.value for item in base.observations], [None, Decimal("345.0")])
        self.assertEqual(base.observations[0].missing_reason, "suppressed")
        before_partial = logical_manifest(self.store_map, self.registry)
        with self.assertRaises(ValidationError):
            self.importer.import_fixture("eia_retail_partial")
        self.assertEqual(before_partial["sha256"], logical_manifest(self.store_map, self.registry)["sha256"])

        self.importer.import_fixture("eia_retail_omission")
        self.assertEqual(len(self._series(series_id, "latest").observations), 0)
        self.assertEqual(
            [item.value for item in self._series(series_id, "as_of", as_of="2026-06-24").observations],
            [None, Decimal("345.0")],
        )
        self.importer.import_fixture("eia_retail_restore")
        restored = self._series(series_id, "latest")
        self.assertEqual([item.value for item in restored.observations], [Decimal("346.0")])
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            states = list(
                connection.execute(
                    """
                    SELECT version.state FROM eia_electricity_retail_sales AS current
                    JOIN eia_electricity_retail_sales_versions AS version
                      ON version.retail_sales_version_id=current.current_version_id
                    ORDER BY version.dimensions_json
                    """
                )
            )
        self.assertEqual([row["state"] for row in states], ["tombstone", "active"])

    def test_eia_weekly_scope_change_and_recession_mode_rejection_are_read_only_on_query(self) -> None:
        self.importer.import_fixture("eia_weekly_base")
        self.importer.import_fixture("eia_weekly_scope_change")
        self.assertEqual(self._count("eia_weekly_source_snapshots"), 2)
        self.assertEqual(self._count("eia_weekly_fundamental_versions"), 1)

        self.importer.import_fixture("recession_periods")
        before = logical_manifest(self.store_map, self.registry)
        periods = self.repository.get_recession_periods()
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(periods[0]["peak_month"], "2020-02-01")
        with self.assertRaises(ValidationError):
            MacroStage3RecessionQuery(vintage_mode="as_of")

    def test_soma_summary_has_no_security_level_path_and_rejects_first_release(self) -> None:
        self.importer.import_fixture("soma_summary")
        self.assertEqual(self._count("soma_summary_components"), 2)
        with self.assertRaises(ValidationError):
            self._series("macro.soma.total.treasuries", "first_release")
        fixture_text = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE_ROOT.glob("*.json"))
        self.assertNotIn("CU" + "SIP", fixture_text.upper())
