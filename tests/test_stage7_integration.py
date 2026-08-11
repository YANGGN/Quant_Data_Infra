from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.__main__ import _parser
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage7 import compare_clean_stage7_rebuilds, run_clean_stage7_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage7_golden.json"


def _project_runtime_artifacts() -> tuple[str, ...]:
    suffixes = (".sqlite", ".sqlite-shm", ".sqlite-wal", ".pyc")
    names = {"data", "state", "private-state", ".quant_data_locks"}
    return tuple(
        sorted(
            str(path.relative_to(PROJECT_ROOT))
            for path in PROJECT_ROOT.rglob("*")
            if path.name in names or path.name.endswith(suffixes)
        )
    )


def _golden_projection(evidence: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": evidence["contract_version"],
        "approval_status": "fixture_validated",
        "registry_revision": evidence["registry_revision"],
        "registry_schema_version": evidence["registry_schema_version"],
        "evidence_sha256": evidence["sha256"],
        "stage6_evidence_sha256": evidence["stage6_evidence_sha256"],
        "job_ids": evidence["job_ids"],
        "job_catalog_sha256": evidence["job_catalog_sha256"],
        "dry_run_plan_sha256": evidence["dry_run_plan_sha256"],
        "dry_run_catalog_sha256": evidence["dry_run_catalog_sha256"],
        "outcome_matrix_sha256": evidence["outcome_matrix_sha256"],
        "receipt_manifest_sha256": evidence["receipt_manifest_sha256"],
        "backup_manifest_sha256": evidence["backup_manifest_sha256"],
        "real_fixture_replay_manifest_sha256": evidence[
            "real_fixture_replay_manifest_sha256"
        ],
        "migration_heads": evidence["migration_heads"],
        "source_reads_unchanged": evidence["source_reads_unchanged"],
        "restored_reads_unchanged": evidence["restored_reads_unchanged"],
        "source_restored_equal": evidence["source_restored_equal"],
        "backup_source_neutral": evidence["backup_source_neutral"],
        "backup_neutral": evidence["backup_neutral"],
        "backup_restored_equal": evidence["backup_restored_equal"],
        "backup_read_only_equality": evidence["backup_read_only_equality"],
        "manual_fixture_only": evidence["manual_fixture_only"],
        "mocked_provider_calls_only": evidence["mocked_provider_calls_only"],
        "real_fixture_replay_unchanged": evidence["real_fixture_replay_unchanged"],
        "real_fixture_replay_no_write": evidence["real_fixture_replay_no_write"],
        "live_provider_calls": evidence["live_provider_calls"],
        "scheduler_installations": evidence["scheduler_installations"],
        "scheduler_starts": evidence["scheduler_starts"],
        "jobs_scheduling_enabled": evidence["jobs_scheduling_enabled"],
        "exports": evidence["exports"],
    }


class Stage7IntegrationTests(unittest.TestCase):
    def test_two_clean_rebuilds_match_the_approved_stage7_evidence(self) -> None:
        project_artifacts_before = _project_runtime_artifacts()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage7_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage7_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            self.assertEqual(len(evidence["job_ids"]), 8)
            self.assertEqual(evidence["exports"], 0)
            self.assertEqual(evidence["live_provider_calls"], 0)
            self.assertEqual(evidence["scheduler_installations"], 0)
            self.assertEqual(evidence["scheduler_starts"], 0)
            self.assertEqual(evidence["jobs_scheduling_enabled"], 0)
            for flag in (
                "source_reads_unchanged",
                "restored_reads_unchanged",
                "source_restored_equal",
                "backup_source_neutral",
                "backup_neutral",
                "backup_restored_equal",
                "backup_read_only_equality",
                "manual_fixture_only",
                "mocked_provider_calls_only",
                "real_fixture_replay_unchanged",
                "real_fixture_replay_no_write",
            ):
                self.assertTrue(evidence[flag])
            dumps_strict(evidence)

            for rebuild in ("first", "second"):
                rebuild_root = temporary / rebuild
                for cohort in ("private-state", "fixture-replay-state", "backup", "restored"):
                    self.assertTrue((rebuild_root / cohort).is_dir())
                self.assertEqual(
                    {
                        path.name
                        for path in (rebuild_root / "fixture-replay-state").iterdir()
                        if path.is_dir()
                    },
                    set(evidence["job_ids"]),
                )
                self.assertFalse((rebuild_root / "dry-run-stores").exists())
                self.assertFalse((rebuild_root / "dry-run-state").exists())

            actual = _golden_projection(evidence)
            if not GOLDEN_PATH.is_file():
                self.fail(dumps_strict(actual))
            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
        self.assertEqual(_project_runtime_artifacts(), project_artifacts_before)

    def test_stage7_cli_parser_accepts_the_closed_stage_name(self) -> None:
        arguments = _parser().parse_args(
            [
                "--stage",
                "stage7",
                "--project-root",
                "/tmp/project",
                "--store-root",
                "/tmp/work",
            ]
        )
        self.assertEqual(arguments.stage, "stage7")

    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            sentinel = root / "preserve.txt"
            sentinel.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage7_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
