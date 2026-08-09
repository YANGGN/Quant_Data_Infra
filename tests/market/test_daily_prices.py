from __future__ import annotations

import copy
import dataclasses
import hashlib
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.fingerprint import logical_manifest
from quant_data.fixtures import Fixture, FixtureManifest
from quant_data.json_codec import dumps_strict
from quant_data.market import DailyPriceImporter, DailyPriceQuery, DailyPriceRepository
from quant_data.market.daily_prices import _parse_fixture_rows
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class _OneFixtureManifest:
    """Test-only manifest stand-in for pre-coordinator invalid candidates."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture

    def get(self, fixture_id: str) -> Fixture:
        return self.fixture


class DailyPriceMarketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT)
        self.importer = DailyPriceImporter(self.store_map, self.manifest)
        self.repository = DailyPriceRepository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _counts(self) -> dict[str, int]:
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            return {
                relation: int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in (
                    "instruments",
                    "instrument_identifiers",
                    "prices_daily",
                    "prices_daily_versions",
                    "market_price_ingestion_requests",
                    "ingestion_runs",
                )
            }

    @staticmethod
    def _query(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        policy: str = "completed_date",
        limit: int = 100,
        start_date: str = "2026-07-16",
        end_date: str = "2026-07-17",
        symbol: str = "SPY",
    ) -> DailyPriceQuery:
        return DailyPriceQuery(
            start_date=start_date,
            end_date=end_date,
            provider="fixture_fmp",
            price_variant="raw",
            currency_segment="USD",
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            limit=limit,
            provider_symbol=symbol,
        )

    @staticmethod
    def _public_payload_digest(payload: dict[str, object]) -> str:
        material = {
            field_name: value
            for field_name, value in payload.items()
            if field_name != "lineage_digest"
        }
        return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()

    @staticmethod
    def _tampered_value(value: object) -> object:
        if value is None:
            return "tampered"
        if isinstance(value, bool):
            return not value
        if isinstance(value, Decimal):
            return value + Decimal("1")
        if isinstance(value, int):
            return value + 1
        if isinstance(value, str):
            return f"{value}-tampered"
        raise AssertionError(f"Unsupported public daily-price value type: {type(value).__name__}")

    def test_mkt_id_001_and_ing_001_base_identity_and_state(self) -> None:
        receipt = self.importer.import_fixture("market.base")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertEqual(receipt.written_count, 4)
        self.assertIsNotNone(receipt.run_id)
        self.assertIsNotNone(receipt.artifact_id)
        self.assertIsNotNone(receipt.snapshot_id)
        self.assertEqual(
            self._counts(),
            {
                "instruments": 2,
                "instrument_identifiers": 2,
                "prices_daily": 4,
                "prices_daily_versions": 4,
                "market_price_ingestion_requests": 1,
                "ingestion_runs": 1,
            },
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            identities = list(
                connection.execute(
                    """
                    SELECT i.instrument_id, i.asset_type, ii.provider_symbol
                    FROM instruments AS i
                    JOIN instrument_identifiers AS ii ON ii.instrument_id=i.instrument_id
                    ORDER BY ii.provider_symbol
                    """
                )
            )
        self.assertEqual([row["asset_type"] for row in identities], ["etf", "index"])
        self.assertEqual([row["provider_symbol"] for row in identities], ["SPY", "^GSPC"])
        self.assertEqual(len({row["instrument_id"] for row in identities}), 2)
        self.assertTrue(all(row["instrument_id"].startswith("instrument_") for row in identities))
        self.assertNotIn("SPY", {row["instrument_id"] for row in identities})

    def test_mkt_ing_002_exact_replay_is_total_zero_write(self) -> None:
        self.importer.import_fixture("market.base")
        before = logical_manifest(self.store_map, self.registry)
        receipt = self.importer.import_fixture("market.base")
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertEqual(receipt.written_count, 0)
        self.assertIsNone(receipt.run_id)
        self.assertIsNone(receipt.artifact_id)
        self.assertIsNone(receipt.snapshot_id)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(self._counts()["ingestion_runs"], 1)

    def test_semantic_identity_canonicalizes_source_row_order(self) -> None:
        self.importer.import_fixture("market.base")
        base_fixture = self.manifest.get("market.base")
        lines = base_fixture.bytes.decode("utf-8").splitlines()
        reordered_text = "\n".join([lines[0], *reversed(lines[1:])]) + "\n"
        reordered = dataclasses.replace(
            base_fixture,
            id="market.base.reordered",
            bytes=reordered_text.encode("utf-8"),
            sha256=hashlib.sha256(reordered_text.encode("utf-8")).hexdigest(),
            byte_count=len(reordered_text.encode("utf-8")),
        )
        before = logical_manifest(self.store_map, self.registry)
        receipt = DailyPriceImporter(self.store_map, _OneFixtureManifest(reordered)).import_fixture(
            reordered.id
        )
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertEqual(before["sha256"], after["sha256"])

    def test_mkt_ing_003_and_004_correction_appends_and_never_tombstones_scope_omissions(self) -> None:
        base = self.importer.import_fixture("market.base")
        correction = self.importer.import_fixture("market.correction")
        self.assertEqual(correction.outcome, "succeeded")
        self.assertEqual(correction.written_count, 1)
        self.assertEqual(
            self._counts(),
            {
                "instruments": 2,
                "instrument_identifiers": 2,
                "prices_daily": 4,
                "prices_daily_versions": 5,
                "market_price_ingestion_requests": 2,
                "ingestion_runs": 2,
            },
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            spy_versions = list(
                connection.execute(
                    """
                    SELECT version_id, close_value, supersedes_version_id, correction_sequence
                    FROM prices_daily_versions AS version
                    JOIN instrument_identifiers AS identifier ON identifier.instrument_id=version.instrument_id
                    WHERE identifier.provider_symbol='SPY' AND version.trade_date='2026-07-17'
                    ORDER BY correction_sequence
                    """
                )
            )
            gspc_current = int(
                connection.execute(
                    """
                    SELECT count(*) FROM prices_daily AS current
                    JOIN instrument_identifiers AS identifier ON identifier.instrument_id=current.instrument_id
                    WHERE identifier.provider_symbol='^GSPC'
                    """
                ).fetchone()[0]
            )
        self.assertEqual([row["close_value"] for row in spy_versions], ["640", "640.25"])
        self.assertIsNone(spy_versions[0]["supersedes_version_id"])
        self.assertEqual(spy_versions[1]["supersedes_version_id"], spy_versions[0]["version_id"])
        self.assertEqual(gspc_current, 2)
        self.assertNotEqual(base.snapshot_id, correction.snapshot_id)

    def test_mkt_time_001_latest_and_as_of_select_correct_versions(self) -> None:
        self.importer.import_fixture("market.base")
        self.importer.import_fixture("market.correction")
        latest = self.repository.get_prices(self._query())
        latest_by_date = {row["trade_date"]: row for row in latest["observations"]}
        self.assertEqual(str(latest_by_date["2026-07-17"]["close"]), "640.25")
        self.assertEqual(latest["audit"]["mode"], "latest")
        original = self.repository.get_prices(
            self._query(mode="as_of", as_of="2026-07-19")
        )
        self.assertEqual(str(original["observations"][1]["close"]), "640")
        before_correction = self.repository.get_prices(
            self._query(mode="as_of", as_of="2026-07-20T08:00:00-04:00")
        )
        after_correction = self.repository.get_prices(
            self._query(mode="as_of", as_of="2026-07-20T09:00:00-04:00")
        )
        self.assertEqual(str(before_correction["observations"][1]["close"]), "640")
        self.assertEqual(str(after_correction["observations"][1]["close"]), "640.25")
        self.assertNotEqual(
            before_correction["observations"][1]["version_id"],
            after_correction["observations"][1]["version_id"],
        )

    def test_mkt_time_002_date_only_policy_matrix(self) -> None:
        self.importer.import_fixture("market.base")
        completed = self.repository.get_prices(
            self._query(
                mode="as_of",
                as_of="2026-07-18T10:00:00-04:00",
                start_date="2026-07-17",
                end_date="2026-07-17",
            )
        )
        self.assertEqual(completed["observations"], [])
        inclusive = self.repository.get_prices(
            self._query(
                mode="as_of",
                as_of="2026-07-18T10:00:00-04:00",
                policy="calendar_date_inclusive",
                start_date="2026-07-17",
                end_date="2026-07-17",
            )
        )
        self.assertEqual(len(inclusive["observations"]), 1)
        self.assertIn("date_only_same_day_intraday_safety_not_established", inclusive["warnings"])
        self.assertEqual(inclusive["audit"]["date_only_policy"], "calendar_date_inclusive")

    def test_lineage_digest_binds_every_public_daily_price_field(self) -> None:
        self.importer.import_fixture("market.base")
        self.importer.import_fixture("market.correction")
        result = self.repository.get_prices(self._query())
        baseline_digest = result["lineage_digest"]
        self.assertEqual(baseline_digest, self._public_payload_digest(result))

        def assert_digest_changes(label: str, candidate: dict[str, object]) -> None:
            with self.subTest(field=label):
                self.assertNotEqual(baseline_digest, self._public_payload_digest(candidate))

        candidate = copy.deepcopy(result)
        candidate["contract"] = "quant_data.daily_price_series.tampered"
        assert_digest_changes("contract", candidate)

        candidate = copy.deepcopy(result)
        candidate["contract_version"] = "999.0.0"
        assert_digest_changes("contract_version", candidate)

        candidate = copy.deepcopy(result)
        candidate["series_id"] = "daily_price_series_tampered"
        assert_digest_changes("series_id", candidate)

        candidate = copy.deepcopy(result)
        candidate["instrument"]["instrument_id"] = "instrument_tampered"
        assert_digest_changes("instrument", candidate)

        for observation_index, observation in enumerate(result["observations"]):
            for field_name, value in observation.items():
                candidate = copy.deepcopy(result)
                candidate["observations"][observation_index][field_name] = self._tampered_value(value)
                assert_digest_changes(f"observations/{observation_index}/{field_name}", candidate)

        candidate = copy.deepcopy(result)
        candidate["warnings"].append("tampered_warning")
        assert_digest_changes("warnings", candidate)

        candidate = copy.deepcopy(result)
        candidate["audit"]["limit"] = 99
        assert_digest_changes("audit", candidate)

        candidate = copy.deepcopy(result)
        candidate["provenance"]["store_receipt"]["sha256"] = "0" * 64
        assert_digest_changes("provenance/store_receipt", candidate)

        candidate = copy.deepcopy(result)
        candidate["truncated"] = not candidate["truncated"]
        assert_digest_changes("truncated", candidate)

    def test_store_receipt_tracks_latest_lineage_but_preserves_earlier_as_of_bytes(self) -> None:
        self.importer.import_fixture("market.base")
        latest_before = self.repository.get_prices(self._query())
        earlier_as_of_query = self._query(mode="as_of", as_of="2026-07-19")
        earlier_before = self.repository.get_prices(earlier_as_of_query)
        earlier_before_json = dumps_strict(earlier_before)

        self.importer.import_fixture("market.correction")
        latest_after = self.repository.get_prices(self._query())
        earlier_after = self.repository.get_prices(earlier_as_of_query)

        self.assertNotEqual(
            latest_before["observations"][1]["version_id"],
            latest_after["observations"][1]["version_id"],
        )
        self.assertNotEqual(
            latest_before["provenance"]["store_receipt"]["sha256"],
            latest_after["provenance"]["store_receipt"]["sha256"],
        )
        self.assertNotEqual(latest_before["lineage_digest"], latest_after["lineage_digest"])
        self.assertEqual(earlier_before_json, dumps_strict(earlier_after))
        self.assertEqual(
            earlier_before["provenance"]["store_receipt"],
            earlier_after["provenance"]["store_receipt"],
        )
        self.assertEqual(earlier_before["lineage_digest"], earlier_after["lineage_digest"])

    def test_store_receipt_hash_includes_applied_migration_checksums(self) -> None:
        self.importer.import_fixture("market.base")
        before = self.repository.get_prices(self._query())
        with sqlite3.connect(self.store_map.market) as connection:
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=2",
                ("f" * 64,),
            )
            connection.commit()
        after = self.repository.get_prices(self._query())
        self.assertEqual(
            before["provenance"]["store_receipt"]["migration_ids"],
            after["provenance"]["store_receipt"]["migration_ids"],
        )
        self.assertNotEqual(
            before["provenance"]["store_receipt"]["sha256"],
            after["provenance"]["store_receipt"]["sha256"],
        )
        self.assertNotEqual(before["lineage_digest"], after["lineage_digest"])

    def test_query_validation_and_deterministic_limit(self) -> None:
        self.importer.import_fixture("market.base")
        result = self.repository.get_prices(self._query(limit=1))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["observations"][0]["trade_date"], "2026-07-16")
        self.assertEqual(result["instrument"]["provider_symbol"], "SPY")
        self.assertTrue(result["series_id"].startswith("daily_price_series_"))
        dumps_strict(result)
        common = {
            "start_date": "2026-07-16",
            "end_date": "2026-07-17",
            "provider": "fixture_fmp",
            "price_variant": "raw",
            "currency_segment": "USD",
            "mode": "latest",
            "as_of": None,
            "date_only_policy": "completed_date",
            "limit": 100,
        }
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**common)
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**common, instrument_id="instrument_x", provider_symbol="SPY")
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**common, instrument_id="", provider_symbol="SPY")
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**{**common, "start_date": "2026-07-18", "end_date": "2026-07-17", "provider_symbol": "SPY"})
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**{**common, "limit": 10_001, "provider_symbol": "SPY"})
        with self.assertRaises(ValidationError):
            DailyPriceQuery(**{**common, "mode": "as_of", "as_of": None, "provider_symbol": "SPY"})

    def test_negative_validation_rejects_every_candidate_before_store_mutation(self) -> None:
        base_fixture = self.manifest.get("market.base")
        base_text = base_fixture.bytes.decode("utf-8")
        candidates = {
            "conflicting_duplicate": base_text
            + "fixture_fmp,SPY,etf,2026-07-16,635.00,639.00,633.00,639.00,70000000,USD,raw,2026-07-17,date\n",
            "invalid_ohlc": base_text.replace("639.00,633.00,638.00", "637.00,633.00,638.00", 1),
            "negative_volume": base_text.replace("70000000", "-1", 1),
            "nonfinite": base_text.replace("635.00", "NaN", 1),
            "naive_availability": base_text.replace(
                "2026-07-17,date", "2026-07-17T09:00:00,datetime", 1
            ),
        }
        for name, text in candidates.items():
            with self.subTest(name=name):
                candidate = dataclasses.replace(
                    base_fixture,
                    id=f"market.invalid.{name}",
                    bytes=text.encode("utf-8"),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    byte_count=len(text.encode("utf-8")),
                )
                with self.assertRaises(ValidationError):
                    _parse_fixture_rows(candidate)

        before = logical_manifest(self.store_map, self.registry)
        text = candidates["invalid_ohlc"]
        invalid = dataclasses.replace(
            base_fixture,
            id="market.invalid.before_coordinator",
            bytes=text.encode("utf-8"),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            byte_count=len(text.encode("utf-8")),
        )
        with self.assertRaises(ValidationError):
            DailyPriceImporter(self.store_map, _OneFixtureManifest(invalid)).import_fixture(invalid.id)
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(before["sha256"], after["sha256"])

    def test_read_only_fingerprint_and_integrity(self) -> None:
        self.importer.import_fixture("market.base")
        self.importer.import_fixture("market.correction")
        before = logical_manifest(self.store_map, self.registry)
        result = self.repository.get_prices(self._query())
        after = logical_manifest(self.store_map, self.registry)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(result["provenance"]["store_role"], "market")
        self.assertEqual(len(result["provenance"]["store_receipt"]["migration_ids"]), 2)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM prices_daily")

    def test_deterministic_clean_rebuild(self) -> None:
        self.importer.import_fixture("market.base")
        self.importer.import_fixture("market.correction")
        first = logical_manifest(self.store_map, self.registry)
        with tempfile.TemporaryDirectory() as second_directory:
            second_root = Path(second_directory)
            second_map = temporary_store_map(second_root)
            initialize_all(second_map, self.registry)
            second_manifest = FixtureManifest.load(FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT)
            second_importer = DailyPriceImporter(second_map, second_manifest)
            second_importer.import_fixture("market.base")
            second_importer.import_fixture("market.correction")
            second = logical_manifest(second_map, self.registry)
        self.assertEqual(first["sha256"], second["sha256"])


if __name__ == "__main__":
    unittest.main()
