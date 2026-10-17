from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.fingerprint import mutation_fingerprint
from quant_data.registry import load_registry, stage11_registry_profile
from quant_data.stage11 import run_clean_stage11_rebuild
from quant_data.stores import StoreMap
from quant_data.tool_platform.arguments import (
    EnergyElectricityRetailArgumentsV2,
    EnergyWeeklyFundamentalsArgumentsV2,
)
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.energy_access import (
    ELECTRICITY_RETAIL_TOOL_NAME,
    WEEKLY_FUNDAMENTALS_TOOL_NAME,
    invoke_energy_access,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _source_stores(root: Path) -> StoreMap:
    source = root / "source"
    return StoreMap.four_explicit(
        market=source / "market.sqlite",
        macro=source / "macro.sqlite",
        company=source / "company.sqlite",
        news=source / "news.sqlite",
    )


def _fields(record: object) -> dict[str, object]:
    return {field.name: field.value for field in getattr(record, "fields")}


class EnergyAccessV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls.root = Path(cls._temporary.name) / "stage11"
        run_clean_stage11_rebuild(project_root=PROJECT_ROOT, work_root=cls.root)
        cls.stores = _source_stores(cls.root)
        cls.registry = stage11_registry_profile(
            load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        )
        cls.registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        cls.baseline = mutation_fingerprint(cls.stores)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def tearDown(self) -> None:
        self.assertEqual(self.baseline, mutation_fingerprint(self.stores))

    def _context(self, name: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="energy-access-v2-test",
            api_version="1.0",
            tool_name=name,
            tool_version="2.0.0",
            operation_graph_id=f"tool_platform.{name}.v2",
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

    def _retail(
        self,
        *,
        metric: str = "sales",
        mode: str = "latest",
        as_of: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ):
        return invoke_energy_access(
            ELECTRICITY_RETAIL_TOOL_NAME,
            EnergyElectricityRetailArgumentsV2(
                metric=metric,
                mode=mode,
                as_of=as_of,
                date_only_policy="completed_date",
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            ),
            self._context(ELECTRICITY_RETAIL_TOOL_NAME),
            self.registry,
        )

    def _weekly(
        self,
        *,
        mode: str = "latest",
        as_of: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ):
        return invoke_energy_access(
            WEEKLY_FUNDAMENTALS_TOOL_NAME,
            EnergyWeeklyFundamentalsArgumentsV2(
                mode=mode,
                as_of=as_of,
                date_only_policy="completed_date",
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            ),
            self._context(WEEKLY_FUNDAMENTALS_TOOL_NAME),
            self.registry,
        )

    def test_retail_returns_one_metric_with_stage11_scope_and_date_bounds(self) -> None:
        result = self._retail(start_date="2024-02-01")
        rows = tuple(_fields(record) for record in result.records)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metric"], "sales")
        self.assertEqual(rows[0]["canonical_series_id"], "macro.eia.electricity.retail_sales")
        self.assertEqual(rows[0]["period"], "2024-02")
        self.assertEqual(rows[0]["state_id"], "US")
        self.assertEqual(rows[0]["sector_id"], "ALL")
        self.assertEqual(rows[0]["value"], Decimal("110"))
        self.assertTrue(result.lineage[0].evidence_id)

    def test_weekly_as_of_fails_closed_then_returns_retained_data(self) -> None:
        before_capture = self._weekly(
            mode="as_of", as_of="2026-08-12T14:31:59Z"
        )
        after_capture = self._weekly(
            mode="as_of",
            as_of="2026-08-12T14:32:00Z",
            start_date="2024-01-12",
        )
        rows = tuple(_fields(record) for record in after_capture.records)
        self.assertEqual(before_capture.records, ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period"], "2024-01-12")
        self.assertEqual(rows[0]["value"], Decimal("411.1"))
        self.assertEqual(
            after_capture.diagnostics[0].code, "stage11_energy_selection"
        )

    def test_truncation_is_explicit_and_source_stays_immutable(self) -> None:
        result = self._retail(limit=1)
        self.assertTrue(result.truncation.applied)
        self.assertTrue(result.truncation.has_more)
        self.assertEqual(result.truncation.total_known_count, 2)
