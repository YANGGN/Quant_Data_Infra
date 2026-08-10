from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import Fixture, FixtureManifest
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.catalog import (
    RECOVERED_FMP_INDEXES,
    MarketCatalogFixtureImporter,
    MarketCatalogRepository,
    _parse_fixture,
    _semantic_identity,
)
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection, stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
STAGE2_FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
STAGE3_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "stage3" / "market"

_COLLECTOR_ID = "fixture.market.catalog_import"
_EVIDENCE_DATASET_ID = "fixture.market.catalog_evidence"
_CLASSIFICATION_DATASET_ID = "fixture.market.instrument_classifications"
_UNIVERSE_DATASET_ID = "fixture.market.controlled_universes"
_IDENTITY_DATASET_ID = "fixture.market.instruments"
_UNIVERSE_KEY = "fixture.market.universe.liquid"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class _FixtureManifest:
    """A local manifest-compatible stand-in until shared manifest integration."""

    def __init__(self, fixtures: list[Fixture]) -> None:
        self._fixtures = {fixture.id: fixture for fixture in fixtures}

    def get(self, fixture_id: str) -> Fixture:
        try:
            return self._fixtures[fixture_id]
        except KeyError as exc:
            raise ValidationError("Unknown fixture ID") from exc

    def add(self, fixture: Fixture) -> None:
        self._fixtures[fixture.id] = fixture


def _fixture(
    fixture_id: str,
    *,
    resource_name: str,
    captured_at: str,
    completeness: str,
    tombstone_authoritative: bool,
    scope_kind: str,
    payload: dict[str, Any] | None = None,
    reviewed_identity: bool = True,
) -> Fixture:
    resource = STAGE3_FIXTURE_ROOT / resource_name
    if payload is None:
        content = resource.read_bytes()
    else:
        content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fixture = Fixture(
        id=fixture_id,
        fixture_schema_version="1.0.0",
        ingestion_family_id=_COLLECTOR_ID,
        evidence_dataset_id=_EVIDENCE_DATASET_ID,
        canonical_dataset_id=_UNIVERSE_DATASET_ID,
        identity_dataset_id=_IDENTITY_DATASET_ID,
        store="market",
        provider="fixture_fmp",
        resource=resource,
        resource_name=f"tests/fixtures/stage3/market/{resource_name}",
        sha256=hashlib.sha256(content).hexdigest(),
        expected_semantic_identity="0" * 64,
        byte_count=len(content),
        captured_at=captured_at,
        test_fixture=True,
        promotable=False,
        expected_warnings=(),
        request_scope={
            "scope_kind": scope_kind,
            "provider": "fixture_fmp",
            "universe_key": _UNIVERSE_KEY,
            "completeness": completeness,
        },
        metadata={
            "normalization_version": "1.0.0",
            "tombstone_authoritative": tombstone_authoritative,
        },
        bytes=content,
    )
    if not reviewed_identity:
        return fixture
    parsed, scope, _, normalization_version = _parse_fixture(fixture)
    return replace(
        fixture,
        expected_semantic_identity=_semantic_identity(
            fixture,
            payload=parsed,
            scope=scope,
            normalization_version=normalization_version,
        ),
    )


class Stage3MarketCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        self.stage2_manifest = FixtureManifest.load(
            STAGE2_FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self.stage2_importer = DailyPriceImporter(self.store_map, self.stage2_manifest)
        fixtures = [
            _fixture(
                "stage3.market.catalog_initial",
                resource_name="catalog_initial.json",
                captured_at="2026-07-20T12:00:00-04:00",
                completeness="complete",
                tombstone_authoritative=True,
                scope_kind="catalog_and_universe",
            ),
            _fixture(
                "stage3.market.universe_partial_omission",
                resource_name="universe_partial_omission.json",
                captured_at="2026-07-21T12:00:00-04:00",
                completeness="partial",
                tombstone_authoritative=False,
                scope_kind="universe_membership",
            ),
            _fixture(
                "stage3.market.universe_complete_removal",
                resource_name="universe_complete_removal.json",
                captured_at="2026-07-22T12:00:00-04:00",
                completeness="complete",
                tombstone_authoritative=True,
                scope_kind="universe_membership",
            ),
            _fixture(
                "stage3.market.universe_complete_restoration",
                resource_name="universe_complete_restoration.json",
                captured_at="2026-07-23T12:00:00-04:00",
                completeness="complete",
                tombstone_authoritative=True,
                scope_kind="universe_membership",
            ),
        ]
        self.catalog_manifest = _FixtureManifest(fixtures)
        self.importer = MarketCatalogFixtureImporter(self.store_map, self.catalog_manifest)  # type: ignore[arg-type]
        self.repository = MarketCatalogRepository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _instrument_id(instrument_key: str) -> str:
        bindings = {
            "fixture.market.instrument.spy": "fixture.market.instrument.spy.v1",
            "fixture.market.index.gspc": "fixture.market.instrument.gspc.v1",
        }
        binding = bindings.get(
            instrument_key,
            f"fixture.market.catalog.binding.v1:{instrument_key}",
        )
        return stable_id("instrument", _IDENTITY_DATASET_ID, binding)

    @staticmethod
    def _universe_id() -> str:
        return stable_id("market_universe", _IDENTITY_DATASET_ID, _UNIVERSE_KEY)

    def _stage2_then_initial_catalog(self) -> None:
        self.stage2_importer.import_fixture("market.base")
        receipt = self.importer.import_fixture("stage3.market.catalog_initial")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertIsNotNone(receipt.snapshot_id)

    def _raw_payload(self, name: str = "catalog_initial.json") -> dict[str, Any]:
        return copy.deepcopy(loads_strict((STAGE3_FIXTURE_ROOT / name).read_bytes()))

    def _canonical_counts(self) -> dict[str, int]:
        relations = (
            "instruments",
            "instrument_identifiers",
            "market_instrument_catalog_snapshots",
            "instrument_classifications",
            "market_universes",
            "market_universe_membership_versions",
            "market_universe_memberships",
            "market_universe_snapshot_memberships",
            "ingestion_runs",
            "ingestion_artifacts",
            "ingestion_snapshots",
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            return {
                relation: int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in relations
            }

    def _failure_count(self) -> int:
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            return int(connection.execute("SELECT count(*) FROM ingestion_run_failures").fetchone()[0])

    def _add_dynamic_fixture(
        self,
        fixture_id: str,
        payload: dict[str, Any],
        *,
        captured_at: str,
        completeness: str = "complete",
        tombstone_authoritative: bool = False,
        scope_kind: str = "universe_membership",
    ) -> Fixture:
        fixture = _fixture(
            fixture_id,
            resource_name="catalog_initial.json",
            captured_at=captured_at,
            completeness=completeness,
            tombstone_authoritative=tombstone_authoritative,
            scope_kind=scope_kind,
            payload=payload,
        )
        self.catalog_manifest.add(fixture)
        return fixture

    def test_stage2_identity_upgrade_and_exact_recovered_index_manifest(self) -> None:
        self._stage2_then_initial_catalog()
        indexes = self.repository.list_recovered_fmp_indexes(effective_date="2026-07-20")
        listed_symbols = [item["provider_symbol"] for item in indexes["indexes"]]
        self.assertEqual(listed_symbols, list(RECOVERED_FMP_INDEXES))
        self.assertEqual(
            [item["display_name"] for item in indexes["indexes"]],
            list(RECOVERED_FMP_INDEXES.values()),
        )
        self.assertTrue(all(item["asset_type"] == "index" for item in indexes["indexes"]))
        self.assertNotIn("^MOVE", listed_symbols)
        self.assertEqual(len(indexes["indexes"]), 9)
        spy = self.repository.resolve_provider_identifier(
            provider="fixture_fmp",
            provider_symbol="SPY",
            effective_date="2026-07-16",
        )
        gspc = self.repository.resolve_provider_identifier(
            provider="fixture_fmp",
            provider_symbol="^GSPC",
            effective_date="2026-07-16",
        )
        self.assertIsNotNone(spy)
        self.assertIsNotNone(gspc)
        assert spy is not None and gspc is not None
        self.assertEqual(spy["asset_type"], "etf")
        self.assertEqual(spy["instrument_id"], self._instrument_id("fixture.market.instrument.spy"))
        self.assertEqual(gspc["instrument_id"], self._instrument_id("fixture.market.index.gspc"))

    def test_ticker_reuse_is_dated_and_overlap_fails_closed(self) -> None:
        self._stage2_then_initial_catalog()
        payload = self._raw_payload()
        payload["instruments"] = [
            {
                "instrument_key": "fixture.market.instrument.acme_reused",
                "asset_type": "equity",
                "canonical_symbol": "ACME2",
                "display_name": "Synthetic Acme Reused",
                "active": True,
                "identifiers": [
                    {
                        "provider": "fixture_fmp",
                        "provider_symbol": "ACME",
                        "valid_from": "2026-07-01",
                        "valid_through": None,
                        "available_at": "2026-07-24",
                        "available_precision": "date",
                    }
                ],
            }
        ]
        payload["classifications"] = []
        payload["index_manifest"] = []
        payload["universe"]["members"] = [
            {
                "instrument_key": "fixture.market.instrument.acme",
                "effective_from": "2024-01-01",
                "effective_through": None,
            },
            {
                "instrument_key": "fixture.market.instrument.spy",
                "effective_from": "2024-01-01",
                "effective_through": None,
            },
        ]
        self._add_dynamic_fixture(
            "stage3.market.ticker_reuse_nonoverlap",
            payload,
            captured_at="2026-07-24T12:00:00-04:00",
        )
        self.importer.import_fixture("stage3.market.ticker_reuse_nonoverlap")
        old = self.repository.resolve_provider_identifier(
            provider="fixture_fmp",
            provider_symbol="ACME",
            effective_date="2026-06-30",
        )
        new = self.repository.resolve_provider_identifier(
            provider="fixture_fmp",
            provider_symbol="ACME",
            effective_date="2026-07-01",
        )
        self.assertIsNotNone(old)
        self.assertIsNotNone(new)
        assert old is not None and new is not None
        self.assertNotEqual(old["instrument_id"], new["instrument_id"])
        self.assertEqual(old["instrument_id"], self._instrument_id("fixture.market.instrument.acme"))
        self.assertEqual(
            new["instrument_id"], self._instrument_id("fixture.market.instrument.acme_reused")
        )
        overlap = copy.deepcopy(payload)
        overlap["instruments"][0]["identifiers"][0]["valid_from"] = "2026-06-30"
        self._add_dynamic_fixture(
            "stage3.market.ticker_reuse_overlap",
            overlap,
            captured_at="2026-07-25T12:00:00-04:00",
        )
        before = self._canonical_counts()
        before_failures = self._failure_count()
        with self.assertRaises(ConflictError):
            self.importer.import_fixture("stage3.market.ticker_reuse_overlap")
        self.assertEqual(before, self._canonical_counts())
        self.assertEqual(self._failure_count(), before_failures + 1)

    def test_classification_boundaries_and_as_of_availability(self) -> None:
        self._stage2_then_initial_catalog()
        acme_id = self._instrument_id("fixture.market.instrument.acme")
        earlier = self.repository.get_classification(
            instrument_id=acme_id,
            classification_provider="fixture_fmp",
            effective_date="2026-06-30",
        )
        later_unavailable = self.repository.get_classification(
            instrument_id=acme_id,
            classification_provider="fixture_fmp",
            effective_date="2026-07-01",
            as_of="2026-07-11",
        )
        later = self.repository.get_classification(
            instrument_id=acme_id,
            classification_provider="fixture_fmp",
            effective_date="2026-07-01",
            as_of="2026-07-12T12:00:00-04:00",
        )
        self.assertIsNotNone(earlier)
        self.assertIsNone(later_unavailable)
        self.assertIsNotNone(later)
        assert earlier is not None and later is not None
        self.assertEqual((earlier["sector"], earlier["industry"]), ("Technology", "Software"))
        self.assertEqual((later["sector"], later["industry"]), ("Financials", "Capital Markets"))

    def test_partial_complete_tombstone_restoration_and_prior_as_of_bytes(self) -> None:
        self._stage2_then_initial_catalog()
        universe_id = self._universe_id()
        pre_capture = self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
            as_of="2026-07-19",
        )
        self.assertEqual(pre_capture["memberships"], [])
        prior = self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
            as_of="2026-07-21",
        )
        prior_bytes = dumps_strict(prior)
        self.assertEqual(len(prior["memberships"]), 2)
        self.importer.import_fixture("stage3.market.universe_partial_omission")
        partial_current = self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
        )
        self.assertEqual(len(partial_current["memberships"]), 2)
        self.importer.import_fixture("stage3.market.universe_complete_removal")
        removed_current = self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
        )
        self.assertEqual(
            [item["instrument_id"] for item in removed_current["memberships"]],
            [self._instrument_id("fixture.market.instrument.spy")],
        )
        self.assertEqual(
            prior_bytes,
            dumps_strict(
                self.repository.get_universe_memberships(
                    universe_id=universe_id,
                    effective_date="2026-07-20",
                    as_of="2026-07-21",
                )
            ),
        )
        self.importer.import_fixture("stage3.market.universe_complete_restoration")
        restored_current = self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
        )
        self.assertEqual(len(restored_current["memberships"]), 2)
        self.assertEqual(
            prior_bytes,
            dumps_strict(
                self.repository.get_universe_memberships(
                    universe_id=universe_id,
                    effective_date="2026-07-20",
                    as_of="2026-07-21",
                )
            ),
        )

    def test_reads_and_exact_replay_have_full_zero_mutation_fingerprints(self) -> None:
        self._stage2_then_initial_catalog()
        universe_id = self._universe_id()
        before_reads = mutation_fingerprint(self.store_map)
        self.repository.list_recovered_fmp_indexes(effective_date="2026-07-20")
        self.repository.resolve_provider_identifier(
            provider="fixture_fmp",
            provider_symbol="ACMX",
            effective_date="2026-07-01",
            as_of="2026-07-20",
        )
        self.repository.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
            as_of="2026-07-21",
        )
        self.assertEqual(before_reads["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        before_replay = mutation_fingerprint(self.store_map)
        replay = self.importer.import_fixture("stage3.market.catalog_initial")
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(before_replay["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_tampered_reviewed_semantic_pin_fails_before_any_write(self) -> None:
        fixture = self.catalog_manifest.get("stage3.market.catalog_initial")
        self.catalog_manifest.add(
            replace(
                fixture,
                id="stage3.market.catalog_initial.bad_semantic_pin",
                expected_semantic_identity="0" * 64,
            )
        )
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.import_fixture("stage3.market.catalog_initial.bad_semantic_pin")
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_invalid_partial_tombstone_shape_and_bounds_fail_before_write(self) -> None:
        invalid_partial = _fixture(
            "stage3.market.invalid_partial_authority",
            resource_name="universe_partial_omission.json",
            captured_at="2026-07-21T13:00:00-04:00",
            completeness="partial",
            tombstone_authoritative=True,
            scope_kind="universe_membership",
            reviewed_identity=False,
        )
        with self.assertRaises(ValidationError):
            _parse_fixture(invalid_partial)
        too_many = self._raw_payload()
        too_many["instruments"] = [{}] * 1_001
        oversized = _fixture(
            "stage3.market.too_many_instruments",
            resource_name="catalog_initial.json",
            captured_at="2026-07-20T13:00:00-04:00",
            completeness="complete",
            tombstone_authoritative=True,
            scope_kind="catalog_and_universe",
            payload=too_many,
            reviewed_identity=False,
        )
        with self.assertRaises(ResourceLimitError):
            _parse_fixture(oversized)
        self._stage2_then_initial_catalog()
        with self.assertRaises(ValidationError):
            self.repository.list_recovered_fmp_indexes(
                effective_date="2026-07-20", limit=10
            )
        with self.assertRaises(ValidationError):
            self.repository.get_universe_memberships(
                universe_id=self._universe_id(),
                effective_date="2026-07-20",
                limit=0,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
