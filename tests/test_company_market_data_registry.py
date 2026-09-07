"""Private collector bindings and exact pre-change registry projection."""
from dataclasses import replace
from pathlib import Path
import unittest
from quant_data.errors import RegistryError
from quant_data.registry import load_registry, company_market_data_registry_profile

ROOT = Path(__file__).resolve().parents[1]
IDS = ("fmp.company.corporate_actions_current", "fmp.company.analyst_estimates_current")

class CompanyMarketDataRegistryTests(unittest.TestCase):
    def test_private_bindings_preserve_exact_previous_registry(self):
        registry = load_registry(ROOT / "config/system_registry.json", project_root=ROOT, environment={})
        self.assertEqual(registry.revision, "2.74.0")
        self.assertEqual((len(registry.migrations), len(registry.datasets), len(registry.collectors)), (48, 64, 70))
        previous = company_market_data_registry_profile(registry)
        self.assertEqual(previous.revision, "2.68.0")
        self.assertEqual(previous.source_sha256, "9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e")
        datasets = {d.id: d for d in registry.datasets}
        for collector in registry.collectors:
            if collector["id"] not in IDS:
                continue
            self.assertEqual(collector["schedule_eligibility"], {"mode":"manual_only"})
            self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
            self.assertEqual(collector["workload_bounds"]["max_bytes"], 1048576)
            for output in collector["output_datasets"]:
                self.assertEqual(datasets[output].store, "company")
                self.assertIn(collector["id"], datasets[output].collector_ids)
        self.assertTrue(all(s.collector_id not in IDS for j in registry.jobs for s in j.steps))
        altered = replace(registry, collectors=registry.collectors[:-1])
        with self.assertRaises(RegistryError):
            company_market_data_registry_profile(altered)
