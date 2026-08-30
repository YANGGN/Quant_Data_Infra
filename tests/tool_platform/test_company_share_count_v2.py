from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.arguments import CompanyShareCountHistoryArgumentsV2
from quant_data.tool_platform.company_access import (
    SHARE_COUNT_METRIC_IDS,
    TOOL_NAME,
    invoke_company_share_count_history,
)
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
NORTHSTAR_CIK = "0001000001"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(record: object) -> dict[str, object]:
    return {
        field.name: field.value
        for field in getattr(record, "fields")
    }


class CompanyShareCountHistoryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls._root = Path(cls._temporary.name)
        cls.stores = _temporary_store_map(cls._root)
        cls.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(cls.stores, cls.registry)
        manifest = FixtureManifest.load(
            MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        importer = CompanyStage4FixtureImporter(cls.stores, manifest)
        for fixture_id in ("sec_initial", "actions_and_shares"):
            receipt = importer.import_fixture(fixture_id)
            if receipt.outcome != "succeeded":
                raise AssertionError("Temporary Stage 4 fixture import failed")
        cls._registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        cls._baseline = mutation_fingerprint(cls.stores)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def tearDown(self) -> None:
        self.assertEqual(self._baseline, mutation_fingerprint(self.stores))

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self._registry_sha256,
            request_id="company-share-count-history-v2-test",
            api_version="1.0",
            tool_name=TOOL_NAME,
            tool_version="2.0.0",
            operation_graph_id=(
                "tool_platform.company.get_share_count_history.v2"
            ),
            operation_version="2.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
        )

    def _call(
        self,
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
    ):
        arguments = CompanyShareCountHistoryArgumentsV2(
            cik=NORTHSTAR_CIK,
            mode=mode,
            as_of=as_of,
            date_only_policy="completed_date",
            limit=limit,
        )
        return invoke_company_share_count_history(
            TOOL_NAME,
            arguments,
            self._context(),
            self.registry,
        )

    def test_latest_and_as_of_use_one_available_fact_history(self) -> None:
        latest = self._call()
        unavailable = self._call(
            mode="as_of",
            as_of="2026-05-01T14:59:59Z",
        )
        available = self._call(
            mode="as_of",
            as_of="2026-05-01T15:00:00Z",
        )

        self.assertEqual(latest.status, "ok")
        self.assertEqual(unavailable.status, "ok")
        self.assertEqual(len(unavailable.records), 2)
        self.assertEqual(
            {_fields(record)["reference_period_end"] for record in unavailable.records},
            {"2025-12-31"},
        )
        self.assertGreater(len(latest.records), len(unavailable.records))
        self.assertEqual(available.to_primitive(), latest.to_primitive())

    def test_only_reviewed_share_metrics_and_semantics_are_returned(self) -> None:
        result = self._call()
        rows = tuple(_fields(record) for record in result.records)
        quarter_rows = {
            str(row["metric_code"]): row
            for row in rows
            if row["reference_period_end"] == "2026-03-31"
        }

        self.assertEqual(
            set(SHARE_COUNT_METRIC_IDS),
            {
                "shares_outstanding",
                "weighted_average_shares_basic",
                "weighted_average_shares_diluted",
            },
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {row["metric_code"] for row in rows},
            {
                "shares_outstanding",
                "weighted_average_shares_basic",
            },
        )
        self.assertEqual(
            {row["metric_id"] for row in rows},
            {
                SHARE_COUNT_METRIC_IDS["shares_outstanding"],
                SHARE_COUNT_METRIC_IDS["weighted_average_shares_basic"],
            },
        )
        self.assertEqual(
            {
                (row["metric_code"], row["share_semantics"])
                for row in rows
            },
            {
                ("shares_outstanding", "instant"),
                (
                    "weighted_average_shares_basic",
                    "weighted_average",
                ),
            },
        )
        self.assertIsNone(
            quarter_rows["shares_outstanding"]["reference_period_start"]
        )
        self.assertEqual(
            quarter_rows["weighted_average_shares_basic"][
                "reference_period_start"
            ],
            "2026-01-01",
        )
        self.assertEqual(
            quarter_rows["shares_outstanding"]["value"],
            Decimal("101000000"),
        )
        self.assertEqual(
            quarter_rows["weighted_average_shares_basic"]["value"],
            Decimal("99500000"),
        )
        self.assertTrue(
            all(row["base_unit"] == "shares" for row in rows)
        )
        self.assertEqual(
            {lineage.semantic_id for lineage in result.lineage},
            {row["fundamental_version_id"] for row in rows},
        )

    def test_typed_request_has_no_metric_or_date_range_override(self) -> None:
        arguments = CompanyShareCountHistoryArgumentsV2(
            cik=NORTHSTAR_CIK,
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            limit=100,
        )

        self.assertEqual(
            set(arguments),
            {"cik", "mode", "as_of", "date_only_policy", "limit"},
        )
        self.assertNotIn("metric_ids", arguments)
        self.assertNotIn("start_date", arguments)
        self.assertNotIn("end_date", arguments)

    def test_adapter_reads_the_temporary_store_without_mutation(self) -> None:
        before = mutation_fingerprint(self.stores)

        result = self._call()

        self.assertTrue(result.records)
        self.assertEqual(before, mutation_fingerprint(self.stores))


if __name__ == "__main__":
    unittest.main()
