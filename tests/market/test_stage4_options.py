from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.market.stage4_options import (
    OptionsStage4Query,
    OptionsStage4Repository,
    Stage4OptionsFixtureImporter,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection, stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"

_OPTION_FIXTURE_IDS = (
    "stage4.market.options.spy_complete",
    "stage4.market.options.nonstandard_exclusion",
    "stage4.market.options.mixed_feed_environment",
    "stage4.market.options.unknown_contract",
    "stage4.market.options.incoherent_inputs",
)


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class Stage4OptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        for fixture_id in _OPTION_FIXTURE_IDS:
            self.manifest.get(fixture_id)
        receipt = DailyPriceImporter(self.store_map, self.manifest).import_fixture("market.base")
        self.assertEqual(receipt.outcome, "succeeded")
        self.importer = Stage4OptionsFixtureImporter(self.store_map, self.manifest)
        self.repository = OptionsStage4Repository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _spy_id() -> str:
        return stable_id(
            "instrument",
            "fixture.market.instruments",
            "fixture.market.instrument.spy.v1",
        )

    def _query(
        self,
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 1_000,
    ) -> OptionsStage4Query:
        return OptionsStage4Query(
            underlying_instrument_id=self._spy_id(),
            resolved_feed="fixture_options",
            mode=mode,
            as_of=as_of,
            limit=limit,
        )

    def _counts(self) -> dict[str, int]:
        relations = (
            "option_contracts",
            "option_surface_captures",
            "option_capture_underlyings",
            "option_capture_underlying_quotes",
            "option_capture_rate_curves",
            "option_capture_rate_curve_points",
            "option_capture_dividend_sets",
            "option_capture_dividend_cashflows",
            "option_capture_expiry_inputs",
            "option_surface_snapshots",
            "option_open_interest",
            "option_close_prices",
            "option_bars",
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            return {
                relation: int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in relations
            }

    def _assert_rejected_before_write(self, fixture_id: str) -> None:
        fixture = self.manifest.get(fixture_id)
        expected_rule = fixture.metadata["expected_error_rule"]
        self.assertIsInstance(expected_rule, str)
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError) as caught:
            self.importer.import_fixture(fixture_id)
        self.assertTrue(caught.exception.issues)
        self.assertEqual(caught.exception.issues[0].rule, expected_rule)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_successful_spy_capture_persists_one_atomic_cohort(self) -> None:
        receipt = self.importer.import_fixture("stage4.market.options.spy_complete")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertGreater(receipt.written_count, 0)
        self.assertIsNotNone(receipt.run_id)
        self.assertEqual(
            self._counts(),
            {
                "option_contracts": 2,
                "option_surface_captures": 1,
                "option_capture_underlyings": 1,
                "option_capture_underlying_quotes": 1,
                "option_capture_rate_curves": 1,
                "option_capture_rate_curve_points": 1,
                "option_capture_dividend_sets": 1,
                "option_capture_dividend_cashflows": 0,
                "option_capture_expiry_inputs": 1,
                "option_surface_snapshots": 2,
                "option_open_interest": 2,
                "option_close_prices": 2,
                "option_bars": 1,
            },
        )
        result = self.repository.get_capture(self._query())
        self.assertEqual(result["capture"]["environment"], "synthetic")
        self.assertEqual(result["capture"]["resolved_feed"], "fixture_options")
        self.assertEqual(len(result["surface"]), 2)
        self.assertEqual(
            {row["state"] for row in result["surface"]},
            {"present", "missing"},
        )
        self.assertEqual(result["inputs"]["underlying_quote"]["last_price"], 650.0)
        self.assertEqual(result["inputs"]["expiry_inputs"][0]["risk_free_rate"], 0.0425)

    def test_exact_replay_is_total_zero_persistent_write(self) -> None:
        self.importer.import_fixture("stage4.market.options.spy_complete")
        before = mutation_fingerprint(self.store_map)
        receipt = self.importer.import_fixture("stage4.market.options.spy_complete")
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertEqual(receipt.written_count, 0)
        self.assertIsNone(receipt.run_id)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_mixed_feed_unknown_contract_and_incoherent_inputs_reject_prewrite(self) -> None:
        for fixture_id in (
            "stage4.market.options.mixed_feed_environment",
            "stage4.market.options.unknown_contract",
            "stage4.market.options.incoherent_inputs",
        ):
            with self.subTest(fixture_id=fixture_id):
                self._assert_rejected_before_write(fixture_id)

    def test_nonstandard_deliverable_is_explicitly_excluded(self) -> None:
        self.importer.import_fixture("stage4.market.options.nonstandard_exclusion")
        result = self.repository.get_capture(self._query())
        excluded = [row for row in result["surface"] if row["state"] == "excluded"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["exclusion_reason"], "nonstandard_deliverable")
        self.assertEqual(excluded[0]["contract"]["deliverable_kind"], "nonstandard")
        self.assertEqual(excluded[0]["open_interest"]["state"], "missing")
        self.assertEqual(excluded[0]["close_price"]["state"], "missing")

    def test_latest_and_as_of_are_cohort_bound_and_reads_are_immutable(self) -> None:
        first = self.importer.import_fixture("stage4.market.options.spy_complete")
        second = self.importer.import_fixture("stage4.market.options.nonstandard_exclusion")
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        before = mutation_fingerprint(self.store_map)
        latest = self.repository.get_capture(self._query())
        as_of = self.repository.get_capture(
            self._query(mode="as_of", as_of="2026-08-03T16:30:00Z")
        )
        self.assertEqual(latest["capture"]["completed_at"], "2026-08-04T16:00:00Z")
        self.assertEqual(as_of["capture"]["completed_at"], "2026-08-03T16:00:00Z")
        self.assertNotEqual(latest["capture"]["capture_id"], as_of["capture"]["capture_id"])
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_read_limits_fail_closed_before_returning_an_unbounded_surface(self) -> None:
        self.importer.import_fixture("stage4.market.options.spy_complete")
        with self.assertRaises(ResourceLimitError):
            self.repository.get_capture(self._query(limit=1))


if __name__ == "__main__":
    unittest.main()
