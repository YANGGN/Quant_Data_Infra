from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    JobDeclaration,
    JobStepDeclaration,
    load_registry,
    stage7_registry_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
JOB_IDS = (
    "news-hourly",
    "sec-daily",
    "options-close",
    "macro-daily",
    "market-close",
    "expectations",
    "company-weekly",
    "macro-monthly",
)
JOB_KEYS = frozenset(
    {
        "id",
        "version",
        "lifecycle",
        "calendar",
        "steps",
        "required_stores",
        "overlap_policy",
        "timeout_seconds",
        "receipt",
        "exit_code_policy",
        "aggregate_status",
        "success_marker",
        "dry_run",
        "owner",
        "escalation",
    }
)
STEP_KEYS = frozenset(
    {
        "id",
        "version",
        "collector_id",
        "depends_on",
        "dependency_policy",
        "read_stores",
        "write_stores",
        "network_mode",
        "configuration_env",
        "timeout_seconds",
        "retry_class",
        "if_new",
        "identity_version",
    }
)
EXPECTED_CADENCES = {
    "news-hourly": "hourly_at_minute_10",
    "sec-daily": "daily_at_0715",
    "options-close": "weekdays_at_1320_and_1620_calendar_gated",
    "macro-daily": "daily_at_1800",
    "market-close": "daily_or_weekdays_at_1800",
    "expectations": "weekdays_at_2000",
    "company-weekly": "saturday_at_0900",
    "macro-monthly": "sunday_at_1100",
}
EXPECTED_TIMEOUTS = {
    "news-hourly": 60,
    "sec-daily": 60,
    "options-close": 90,
    "macro-daily": 60,
    "market-close": 60,
    "expectations": 60,
    "company-weekly": 60,
    "macro-monthly": 120,
}


class Stage7RegistryTests(unittest.TestCase):
    def _load_mutation(self, mutate):
        raw = loads_strict(REGISTRY_PATH.read_bytes(), max_bytes=16 * 1024 * 1024)
        mutate(raw)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "registry.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            return load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_canonical_manual_fixture_catalog_is_exact(self) -> None:
        before = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        canonical = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        registry = stage7_registry_profile(canonical)
        self.assertEqual(registry.schema_version, "1.3.0")
        self.assertEqual(registry.registry_version, "2.5.0")
        self.assertEqual(tuple(job.id for job in registry.jobs), JOB_IDS)
        self.assertEqual(tuple(item["id"] for item in registry.raw["jobs"]), JOB_IDS)
        self.assertEqual(registry.raw["exports"], [])
        self.assertEqual(registry.job("options-close").required_stores, ("macro", "market"))
        with self.assertRaises(RegistryError):
            registry.job("unregistered-job")

        for raw_job, job in zip(registry.raw["jobs"], registry.jobs):
            with self.subTest(job=job.id):
                self.assertIsInstance(job, JobDeclaration)
                self.assertEqual(frozenset(raw_job), JOB_KEYS)
                self.assertEqual(raw_job["version"], "1.0.0")
                self.assertEqual(raw_job["lifecycle"], {
                    "state": "fixture_validated",
                    "execution_mode": "manual_fixture_only",
                    "scheduling_enabled": False,
                })
                self.assertEqual(raw_job["calendar"], {
                    "mode": "manual_fixture_only",
                    "recovered_cadence": EXPECTED_CADENCES[job.id],
                    "timezone_policy": "unreconciled",
                    "external_definition": "unresolved",
                })
                self.assertEqual(raw_job["required_stores"], "derived_from_steps")
                self.assertEqual(raw_job["overlap_policy"], "ignore_new")
                self.assertEqual(raw_job["timeout_seconds"], EXPECTED_TIMEOUTS[job.id])
                self.assertEqual(raw_job["owner"], "quant_data.operations")
                self.assertEqual(raw_job["escalation"], "manual_review")
                self.assertEqual(raw_job["receipt"], {
                    "schema_version": "1.0",
                    "visibility": "private",
                    "state_directory": "explicit",
                    "publication": "atomic",
                    "immutability": "immutable",
                    "directory_mode": "0700",
                    "file_mode": "0600",
                })
                self.assertEqual(raw_job["exit_code_policy"], {
                    "id": "stage7_sysexits_v1",
                    "mapping": {
                        "success": 0,
                        "invalid_plan": 64,
                        "unavailable": 69,
                        "internal": 70,
                        "io": 74,
                        "temporary": 75,
                        "configuration": 78,
                        "timeout": 124,
                    },
                    "precedence": [74, 78, 64, 124, 70, 75, 69],
                })
                self.assertEqual(raw_job["aggregate_status"], {
                    "attempted_failure": "nonzero",
                    "partial_commit": "partial",
                    "unchanged": "zero_persistent_writes",
                    "cross_store_atomicity": False,
                })
                self.assertEqual(raw_job["dry_run"], {
                    "provider": "none",
                    "database": "none",
                    "lock": "none",
                    "state": "none",
                    "store_aliases": "sanitized_aliases_only",
                })
                expected_stores = tuple(
                    dict.fromkeys(
                        store for step in job.steps for store in step.write_stores
                    )
                )
                self.assertEqual(job.required_stores, expected_stores)
                for raw_step, step in zip(raw_job["steps"], job.steps):
                    self.assertIsInstance(step, JobStepDeclaration)
                    self.assertEqual(frozenset(raw_step), STEP_KEYS)
                    self.assertEqual(step.id, step.collector_id)
                    self.assertEqual(raw_step["network_mode"], "fixture_only_no_network")
                    self.assertEqual(raw_step["configuration_env"], [])
                    self.assertEqual(raw_step["retry_class"], "collector_declared")
                    self.assertTrue(raw_step["if_new"])
                    self.assertEqual(
                        raw_step["identity_version"],
                        "collector_semantic_identity_v1",
                    )
                    self.assertEqual(raw_step["timeout_seconds"], 30)
                    self.assertTrue(set(step.write_stores).issubset(step.read_stores))
        self.assertEqual(
            registry.job("macro-monthly").success_marker,
            "monthly_full_success_only",
        )
        self.assertEqual(registry.job("news-hourly").success_marker, "none")
        self.assertEqual(before, hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest())

    def test_hostile_job_declarations_fail_without_touching_canonical_source(self) -> None:
        cases = (
            (
                "unknown_job_field",
                lambda raw: raw["jobs"][0].__setitem__("unexpected", True),
            ),
            (
                "extra_step_field",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__("unexpected", True),
            ),
            (
                "scheduling_enabled",
                lambda raw: raw["jobs"][0]["lifecycle"].__setitem__(
                    "scheduling_enabled", True
                ),
            ),
            (
                "network_mode",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "network_mode", "live_provider"
                ),
            ),
            (
                "cycle",
                lambda raw: raw["jobs"][2]["steps"][1].__setitem__(
                    "depends_on", ["fixture.market.options_import"]
                ),
            ),
            (
                "dependency_policy",
                lambda raw: raw["jobs"][2]["steps"][1].__setitem__(
                    "dependency_policy", "independent"
                ),
            ),
            (
                "collector",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "collector_id", "fixture.unknown.import"
                ),
            ),
            (
                "configuration",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "configuration_env", ["QUANT_UNAPPROVED"]
                ),
            ),
            (
                "write_store",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "write_stores", ["market"]
                ),
            ),
            (
                "read_store",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "read_stores", ["market"]
                ),
            ),
            (
                "retry_class",
                lambda raw: raw["jobs"][0]["steps"][0].__setitem__(
                    "retry_class", "unbounded"
                ),
            ),
            (
                "marker",
                lambda raw: raw["jobs"][7].__setitem__("success_marker", "none"),
            ),
            (
                "export",
                lambda raw: raw["exports"].append({"id": "forbidden"}),
            ),
        )
        source_before = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        for name, mutate in cases:
            with self.subTest(case=name):
                with self.assertRaises(RegistryError):
                    self._load_mutation(mutate)
                self.assertEqual(
                    source_before,
                    hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(),
                )

    def test_loaded_job_policy_is_detached_and_recursively_immutable(self) -> None:
        registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT)
        job = registry.job("options-close")

        with self.assertRaises(TypeError):
            job.lifecycle["scheduling_enabled"] = True
        with self.assertRaises(TypeError):
            job.exit_code_policy["mapping"]["success"] = 99

        registry.raw["jobs"][2]["lifecycle"]["scheduling_enabled"] = True
        registry.raw["jobs"][2]["exit_code_policy"]["mapping"]["success"] = 99
        self.assertFalse(job.lifecycle["scheduling_enabled"])
        self.assertEqual(job.exit_code_policy["mapping"]["success"], 0)


if __name__ == "__main__":
    unittest.main()
