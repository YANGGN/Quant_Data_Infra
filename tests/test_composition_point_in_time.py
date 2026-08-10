from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.composition import (
    BoundedCrossStoreComposer,
    CompositionContext,
    read_ingestion_run_component,
)
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import dumps_strict
from quant_data.macro import MacroFixtureImporter
from quant_data.market import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stage1 import explicit_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


class CompositionPointInTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name).resolve()
        self.store_map = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )
        initialize_all(self.store_map, self.registry)
        manifest = FixtureManifest.load(FIXTURE_MANIFEST, project_root=PROJECT_ROOT)
        self.market = DailyPriceImporter(self.store_map, manifest)
        self.macro = MacroFixtureImporter(self.store_map, manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _context(self, cutoff: str) -> CompositionContext:
        return CompositionContext(
            cutoff=cutoff,
            date_only_policy="completed_date",
            component_limit=100,
            joined_limit=100,
        )

    def _compose(
        self, context: CompositionContext, *, reverse_components: bool = False
    ) -> dict[str, object]:
        before = mutation_fingerprint(self.store_map)
        components = (
            read_ingestion_run_component(
                self.store_map,
                self.registry,
                dataset_id="fixture.market.daily_prices",
                context=context,
            ),
            read_ingestion_run_component(
                self.store_map,
                self.registry,
                dataset_id="fixture.macro.rtdsm_employ",
                context=context,
            ),
        )
        if reverse_components:
            components = tuple(reversed(components))
        result = BoundedCrossStoreComposer(self.registry).compose(
            context=context, components=components
        )
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        return result

    @staticmethod
    def _receipts(result: dict[str, object]) -> dict[str, dict[str, object]]:
        components = result["components"]
        assert isinstance(components, list)
        return {
            str(component["dataset_id"]): component
            for component in components
            if isinstance(component, dict)
        }

    def test_pre_history_cutoff_has_no_future_lineage_or_future_state_hash(self) -> None:
        market_base = self.market.import_fixture("market.base")
        macro_first = self.macro.import_fixture("macro.first_vintage")
        context = self._context("1900-01-01")

        before_future_insert = self._compose(context)
        self.assertEqual(before_future_insert["rows"], [])
        for component in self._receipts(before_future_insert).values():
            self.assertEqual(component["accepted_count"], 0)
            receipt = component["store_receipt"]
            assert isinstance(receipt, dict)
            self.assertEqual(receipt["run_ids"], [])
            self.assertEqual(receipt["snapshot_ids"], [])
            self.assertEqual(
                receipt["read_start_state_sha256"],
                receipt["read_completion_state_sha256"],
            )

        serialized = dumps_strict(before_future_insert)
        for imported in (market_base, macro_first):
            for identity in (
                imported.semantic_identity,
                imported.run_id,
                imported.artifact_id,
                imported.snapshot_id,
            ):
                if identity is not None:
                    self.assertNotIn(identity, serialized)

        macro_revised = self.macro.import_fixture("macro.revised_vintage")
        after_future_insert = self._compose(context)
        self.assertEqual(
            dumps_strict(before_future_insert), dumps_strict(after_future_insert)
        )
        for identity in (
            macro_revised.semantic_identity,
            macro_revised.run_id,
            macro_revised.artifact_id,
            macro_revised.snapshot_id,
        ):
            if identity is not None:
                self.assertNotIn(identity, dumps_strict(after_future_insert))

    def test_mid_history_cutoff_includes_only_eligible_lineage_and_is_ordered(self) -> None:
        market_base = self.market.import_fixture("market.base")
        macro_first = self.macro.import_fixture("macro.first_vintage")
        macro_revised = self.macro.import_fixture("macro.revised_vintage")
        context = self._context("2026-07-11T00:00:00-04:00")

        result = self._compose(context)
        reversed_result = self._compose(context, reverse_components=True)
        self.assertEqual(dumps_strict(result), dumps_strict(reversed_result))

        components = self._receipts(result)
        market_component = components["fixture.market.daily_prices"]
        self.assertEqual(market_component["accepted_count"], 0)
        market_receipt = market_component["store_receipt"]
        assert isinstance(market_receipt, dict)
        self.assertEqual(market_receipt["run_ids"], [])
        self.assertEqual(market_receipt["snapshot_ids"], [])

        macro_component = components["fixture.macro.rtdsm_employ"]
        expected_macro = sorted(
            (macro_first, macro_revised),
            key=lambda receipt: (receipt.semantic_identity, receipt.run_id or ""),
        )
        self.assertEqual(macro_component["accepted_count"], len(expected_macro))
        macro_receipt = macro_component["store_receipt"]
        assert isinstance(macro_receipt, dict)
        self.assertEqual(
            macro_receipt["run_ids"],
            [receipt.run_id for receipt in expected_macro],
        )
        self.assertEqual(
            macro_receipt["snapshot_ids"],
            [receipt.snapshot_id for receipt in expected_macro],
        )
        self.assertNotIn(market_base.run_id, dumps_strict(result))
        self.assertNotIn(market_base.snapshot_id, dumps_strict(result))

    def test_future_insert_keeps_earlier_complete_output_and_receipts_byte_stable(self) -> None:
        self.market.import_fixture("market.base")
        self.macro.import_fixture("macro.first_vintage")
        context = self._context("2026-07-09T00:00:00-04:00")

        before_future_insert = self._compose(context)
        self.assertTrue(before_future_insert["rows"])
        self.macro.import_fixture("macro.revised_vintage")
        after_future_insert = self._compose(context)

        self.assertEqual(
            dumps_strict(before_future_insert), dumps_strict(after_future_insert)
        )


if __name__ == "__main__":
    unittest.main()
