from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import loads_strict
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.arguments import CompanyFundamentalsArgumentsV2
from quant_data.tool_platform.company_fundamentals import (
    TOOL_NAME,
    invoke_company_fundamentals,
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


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(record: object) -> dict[str, object]:
    return {field.name: field.value for field in getattr(record, "fields")}


def _diagnostic(result: object) -> dict[str, object]:
    return {
        item.name: item.value
        for item in getattr(result, "diagnostics")[0].metrics
    }


class CompanyFundamentalsV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls.stores = _stores(Path(cls._temporary.name))
        cls.registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )
        initialize_all(cls.stores, cls.registry)
        importer = CompanyStage4FixtureImporter(
            cls.stores,
            FixtureManifest.load(MANIFEST_PATH, project_root=PROJECT_ROOT),
        )
        receipt = importer.import_fixture("sec_initial")
        if receipt.outcome != "succeeded":
            raise AssertionError("Temporary company fixture import failed")
        correction = importer.import_fixture("companyfacts_correction")
        if correction.outcome != "succeeded":
            raise AssertionError("Temporary company correction fixture import failed")
        cls.registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        cls.baseline = mutation_fingerprint(cls.stores)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def tearDown(self) -> None:
        self.assertEqual(self.baseline, mutation_fingerprint(self.stores))

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="company-fundamentals-v2-test",
            api_version="1.0",
            tool_name=TOOL_NAME,
            tool_version="2.0.0",
            operation_graph_id="tool_platform.company.get_fundamentals.v2",
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
        metric_codes: tuple[str, ...] = (),
        mode: str = "latest",
        as_of: str | None = None,
        date_only_policy: str = "completed_date",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ):
        return invoke_company_fundamentals(
            TOOL_NAME,
            CompanyFundamentalsArgumentsV2(
                cik=NORTHSTAR_CIK,
                metric_codes=metric_codes,
                mode=mode,
                as_of=as_of,
                date_only_policy=date_only_policy,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            ),
            self._context(),
            self.registry,
        )

    def test_returns_raw_normalized_facts_and_reviewed_metric_filter(self) -> None:
        result = self._call()
        rows = tuple(_fields(record) for record in result.records)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            {row["metric_code"] for row in rows},
            {"total_assets", "shares_outstanding", "weighted_average_shares_basic"},
        )
        assets = self._call(metric_codes=("total_assets",))
        asset_rows = tuple(_fields(record) for record in assets.records)
        self.assertEqual(len(asset_rows), 1)
        self.assertEqual(asset_rows[0]["value"], Decimal("1251000000"))
        self.assertEqual(asset_rows[0]["reference_period_end"], "2025-12-31")
        self.assertEqual(asset_rows[0]["base_unit"], "USD")
        self.assertEqual(asset_rows[0]["share_semantics"], "not_share")
        self.assertEqual(
            assets.diagnostics[0].code, "normalized_company_fundamental_selection"
        )
        diagnostic = _diagnostic(assets)
        self.assertEqual(diagnostic["actual_mode"], "latest")
        self.assertEqual(diagnostic["availability_basis"], "source_evidenced")
        self.assertIsNone(diagnostic["cutoff"])
        self.assertIsNone(diagnostic["cutoff_precision"])
        self.assertEqual(diagnostic["point_in_time_status"], "not_applicable")
        self.assertEqual(diagnostic["source_warning_count"], 0)
        self.assertEqual(
            loads_strict(str(diagnostic["selected_fundamental_version_ids"])),
            [asset_rows[0]["fundamental_version_id"]],
        )
        self.assertEqual(
            loads_strict(str(diagnostic["selected_source_fact_version_ids"])),
            [asset_rows[0]["source_fact_version_id"]],
        )
        self.assertEqual(
            loads_strict(str(diagnostic["selected_source_snapshot_ids"])),
            [asset_rows[0]["source_snapshot_id"]],
        )

    def test_as_of_and_reference_period_bounds_are_applied_without_mutation(self) -> None:
        before = self._call(
            mode="as_of", as_of="2026-01-10T16:04:11Z"
        )
        at_acceptance = self._call(
            metric_codes=("total_assets",),
            mode="as_of",
            as_of="2026-01-10T16:04:12Z",
        )
        outside_range = self._call(
            metric_codes=("total_assets",), start_date="2026-01-01"
        )
        self.assertEqual(before.records, ())
        self.assertEqual(len(at_acceptance.records), 1)
        self.assertEqual(outside_range.records, ())
        before_diagnostic = _diagnostic(before)
        self.assertEqual(before_diagnostic["cutoff_precision"], "datetime")
        self.assertEqual(before_diagnostic["point_in_time_status"], "safe")
        self.assertEqual(
            loads_strict(
                str(before_diagnostic["selected_fundamental_version_ids"])
            ),
            [],
        )
        self.assertEqual(
            _diagnostic(at_acceptance)["point_in_time_status"],
            "safe",
        )

    def test_same_day_inclusive_as_of_is_unsafe_when_date_precision_is_retained(
        self,
    ) -> None:
        completed = self._call(
            metric_codes=("total_assets",),
            mode="as_of",
            as_of="2026-04-03T12:00:00Z",
        )
        completed_row = _fields(completed.records[0])
        self.assertEqual(completed_row["value"], Decimal("1250000000"))
        self.assertEqual(_diagnostic(completed)["point_in_time_status"], "safe")

        inclusive = self._call(
            metric_codes=("total_assets",),
            mode="as_of",
            as_of="2026-04-03T12:00:00Z",
            date_only_policy="calendar_date_inclusive",
        )
        inclusive_row = _fields(inclusive.records[0])
        self.assertEqual(inclusive_row["value"], Decimal("1251000000"))
        diagnostic = _diagnostic(inclusive)
        self.assertEqual(diagnostic["point_in_time_status"], "unsafe")
        self.assertEqual(
            loads_strict(str(diagnostic["unsafe_reasons"])),
            ["date_only_same_day_intraday_safety_not_established"],
        )
        self.assertEqual(
            tuple(item.code for item in inclusive.warnings),
            ("source_selection_warning",),
        )
        self.assertEqual(
            inclusive.warnings[0].message,
            "date_only_same_day_intraday_safety_not_established",
        )

    def test_unsupported_metric_and_excess_result_are_explicit(self) -> None:
        with self.assertRaises(ValidationError):
            self._call(metric_codes=("not_a_reviewed_metric",))
        with self.assertRaises(ResourceLimitError):
            self._call(limit=1)
