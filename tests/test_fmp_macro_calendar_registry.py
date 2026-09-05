from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    fmp_gdp_cpi_release_calendar_registry_profile,
    load_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
COLLECTOR_ID = "fmp.macro.gdp_cpi_release_calendar_history"
DATASET_ID = "fixture.macro.economic_calendar"
OFFICIAL_DATASET_ID = "macro.official_vintages"
SURPRISE_TOOL_ID = "macro.release_surprises"
PRE_FMP_SOURCE_SHA256 = (
    "177908e080573f051231c9f89b2bb2967e12fd1a3cc2b20754ce86c7d8a216e6"
)


class FmpMacroCalendarRegistryTests(unittest.TestCase):
    def test_manual_collector_reuses_the_existing_calendar_dataset(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(registry.revision, "2.69.0")
        self.assertEqual(
            (len(registry.migrations), len(registry.datasets), len(registry.collectors)),
            (44, 59, 66),
        )
        collector = next(
            item for item in registry.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(collector["handler"], "macro.fmp_gdp_cpi_release_calendar")
        self.assertEqual(collector["configuration_env"], ["FMP_API_KEY"])
        self.assertEqual(collector["output_datasets"], [DATASET_ID])
        self.assertTrue(collector["network"])
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        self.assertEqual(collector["schedule_eligibility"], {"mode": "manual_only"})
        dataset = next(item for item in registry.datasets if item.id == DATASET_ID)
        self.assertEqual(
            dataset.collector_ids,
            (
                "fixture.macro.calendar_import",
                COLLECTOR_ID,
                "fmp.macro.employment_release_calendar_refresh",
            ),
        )
        official = next(
            item for item in registry.datasets if item.id == OFFICIAL_DATASET_ID
        )
        surprise_tool = registry.tool(SURPRISE_TOOL_ID)
        self.assertEqual(
            official.tool_ids,
            (
                SURPRISE_TOOL_ID,
                "data.get_dataset_status",
                "macro.search_series",
                "macro.describe_series",
                "macro.get_series",
                "macro.revision_analysis",
                "macro.standardize_surprises",
                "macro.get_liquidity_snapshot",
                "macro.get_liquidity_impulse",
                "macro.get_credit_conditions",
                "macro.regime_snapshot",
                "rates.get_funding_conditions",
                "rates.get_repo_facility_usage",
                "rates.curve_analytics",
                "research.liquidity_credit_state",
            ),
        )
        self.assertEqual(
            surprise_tool["datasets"],
            [DATASET_ID, OFFICIAL_DATASET_ID],
        )
        self.assertEqual(
            surprise_tool["examples"][0]["identifiers"],
            ["us_gdp_real_qoq_saar_advance"],
        )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in registry.jobs
                for step in job.steps
            )
        )

    def test_revision_218_projection_is_byte_exact_and_drift_closed(self) -> None:
        registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        historical = fmp_gdp_cpi_release_calendar_registry_profile(registry)
        self.assertEqual(historical.revision, "2.18.0")
        self.assertEqual(historical.source_sha256, PRE_FMP_SOURCE_SHA256)
        self.assertNotIn(COLLECTOR_ID, {item["id"] for item in historical.collectors})
        historical_official = next(
            item for item in historical.datasets if item.id == OFFICIAL_DATASET_ID
        )
        historical_tool = historical.tool(SURPRISE_TOOL_ID)
        self.assertEqual(historical_official.tool_ids, ())
        self.assertEqual(historical_tool["datasets"], [DATASET_ID])
        self.assertEqual(
            historical_tool["examples"][0]["identifiers"],
            ["fixture"],
        )
        payload = (
            json.dumps(historical.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PRE_FMP_SOURCE_SHA256)

        raw = loads_strict(REGISTRY_PATH.read_bytes())
        collector = next(item for item in raw["collectors"] if item["id"] == COLLECTOR_ID)
        collector["handler"] = "macro.unreviewed"
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "registry.json"
            path.write_bytes(dumps_strict(raw).encode("utf-8"))
            with self.assertRaises(RegistryError):
                load_registry(path, project_root=PROJECT_ROOT, environment={})


if __name__ == "__main__":
    unittest.main()
