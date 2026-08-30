from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from quant_data.errors import RegistryError
from quant_data.registry import (
    load_registry,
    sec_market_companyfacts_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
SERVICE_PATH = (
    PROJECT_ROOT / "deploy" / "systemd" / "quant-data-sec-company-fundamentals.service"
)
TIMER_PATH = (
    PROJECT_ROOT / "deploy" / "systemd" / "quant-data-sec-company-fundamentals.timer"
)
COLLECTOR_ID = "sec.company.market_fundamentals"
DATASET_IDS = (
    "fixture.company.sec_evidence",
    "fixture.company.issuers",
    "fixture.company.filings",
    "fixture.company.fundamentals",
    "fixture.company.filing_issuer_membership",
)
CURRENT_SOURCE_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
PREVIOUS_SOURCE_SHA256 = (
    "af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182"
)


class SecMarketCompanyFactsRegistryTests(unittest.TestCase):
    def _registry(self):
        return load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_collector_is_bounded_reciprocal_and_projects_exactly(self) -> None:
        current = self._registry()
        self.assertEqual(
            (current.schema_version, current.revision, current.source_sha256),
            ("1.9.0", "2.64.0", CURRENT_SOURCE_SHA256),
        )
        collector = next(
            item for item in current.collectors if item["id"] == COLLECTOR_ID
        )
        self.assertEqual(collector["handler"], "company.sec_market_fundamentals")
        self.assertEqual(
            collector["configuration_env"],
            ["SEC_USER_AGENT_NAME", "SEC_USER_AGENT_EMAIL"],
        )
        self.assertEqual(
            collector["input_datasets"], ["market.stage10.instruments"]
        )
        self.assertEqual(collector["output_datasets"], list(DATASET_IDS))
        self.assertEqual(
            collector["workload_bounds"],
            {
                "max_bytes": 67_108_864,
                "max_requests": 2,
                "max_rows": 50_000,
                "max_seconds": 120,
            },
        )
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        self.assertEqual(
            collector["schedule_eligibility"], {"mode": "manual_only"}
        )
        datasets = {item.id: item for item in current.datasets}
        for dataset_id in DATASET_IDS:
            self.assertEqual(
                datasets[dataset_id].collector_ids.count(COLLECTOR_ID), 1
            )
        self.assertFalse(
            any(
                step.collector_id == COLLECTOR_ID
                for job in current.jobs
                for step in job.steps
            )
        )

        previous = sec_market_companyfacts_registry_profile(current)
        self.assertEqual(
            (previous.revision, previous.source_sha256),
            ("2.44.0", PREVIOUS_SOURCE_SHA256),
        )
        self.assertNotIn(
            COLLECTOR_ID, {str(item["id"]) for item in previous.collectors}
        )
        previous_datasets = {item.id: item for item in previous.datasets}
        for dataset_id in DATASET_IDS:
            self.assertNotIn(
                COLLECTOR_ID, previous_datasets[dataset_id].collector_ids
            )
        payload = (
            json.dumps(previous.raw, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PREVIOUS_SOURCE_SHA256)
        self.assertIs(sec_market_companyfacts_registry_profile(previous), previous)

    def test_current_delta_drift_fails_closed(self) -> None:
        current = self._registry()
        raw = json.loads(json.dumps(current.raw))
        collector = next(
            item for item in raw["collectors"] if item["id"] == COLLECTOR_ID
        )
        collector["handler"] = "company.unreviewed"
        with self.assertRaises(RegistryError):
            sec_market_companyfacts_registry_profile(replace(current, raw=raw))

    def test_host_units_are_fixed_nonpersistent_and_hardened(self) -> None:
        service = SERVICE_PATH.read_text(encoding="utf-8")
        timer = TIMER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/usr/bin/python3 -m "
            "quant_data.operations.sec_market_companyfacts_refresh",
            service,
        )
        self.assertIn("TimeoutStartSec=6h", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=read-only", service)
        self.assertIn(
            "ReadWritePaths=/home/volatility/Python_Projects/"
            "Quant_Data_Infra/data",
            service,
        )
        self.assertIn(
            "OnCalendar=Mon..Fri *-*-* 07:15:00 America/New_York", timer
        )
        self.assertIn("Persistent=false", timer)
        self.assertIn(
            "Unit=quant-data-sec-company-fundamentals.service", timer
        )


if __name__ == "__main__":
    unittest.main()
