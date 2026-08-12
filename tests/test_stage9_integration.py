from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.__main__ import _parser
from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage9 import compare_clean_stage9_rebuilds, run_clean_stage9_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage9_golden.json"


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
    keys = (
        "registry_revision",
        "registry_schema_version",
        "registry_source_sha256",
        "stage8_evidence_sha256",
        "collector_id",
        "provider",
        "symbol",
        "start_date",
        "end_date",
        "expected_dates",
        "request_count",
        "response_kind",
        "publication_outcome",
        "replay_outcome",
        "coverage",
        "repopulation_evidence_sha256",
        "candidate_receipt",
        "promotion_state",
        "correction_rehearsal",
        "migration_heads",
        "backup_source_sha256",
        "backup_copy_sha256",
        "restored_sha256",
        "source_unchanged_after_publication",
        "backup_restore_equal",
        "correction_rehearsed_on_isolated_restore",
        "operational_promotion_performed",
        "old_store_retirement_performed",
        "live_provider_calls",
        "credential_values_recorded",
        "public_export_ids",
    )
    return {
        "schema_version": evidence["contract_version"],
        "approval_status": "offline_fixture_validated_live_not_run",
        "evidence_sha256": evidence["sha256"],
        **{key: evidence[key] for key in keys},
    }


class Stage9IntegrationTests(unittest.TestCase):
    def test_two_clean_rebuilds_match_approved_offline_stage9_evidence(self) -> None:
        artifacts_before = _project_runtime_artifacts()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            evidence = compare_clean_stage9_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=root / "first",
                second_work_root=root / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage9_rebuild_evidence")
            self.assertEqual(evidence["registry_revision"], "2.7.0")
            self.assertEqual(evidence["registry_schema_version"], "1.5.0")
            self.assertEqual(evidence["request_count"], 1)
            self.assertEqual(evidence["coverage"]["row_count"], 22)
            self.assertEqual(evidence["coverage"]["capture_count"], 1)
            self.assertEqual(evidence["coverage"]["version_count"], 22)
            self.assertEqual(evidence["correction_rehearsal"]["capture_count"], 2)
            self.assertEqual(evidence["correction_rehearsal"]["version_count"], 23)
            self.assertEqual(
                evidence["promotion_state"],
                "candidate_only_no_operational_promotion",
            )
            self.assertFalse(evidence["operational_promotion_performed"])
            self.assertFalse(evidence["old_store_retirement_performed"])
            self.assertEqual(evidence["live_provider_calls"], 0)
            self.assertEqual(evidence["credential_values_recorded"], 0)
            self.assertEqual(evidence["public_export_ids"], [])
            dumps_strict(evidence)

            for name in ("first", "second"):
                work = root / name
                for child in ("stage8", "source", "backup", "restored", "rehearsal", "private-state"):
                    self.assertTrue((work / child).is_dir())
                receipt_files = tuple((work / "private-state" / "promotion-candidates").glob("*.json"))
                self.assertEqual(len(receipt_files), 1)

            actual = _golden_projection(evidence)
            if not GOLDEN_PATH.is_file():
                self.fail(dumps_strict(actual))
            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
        self.assertEqual(_project_runtime_artifacts(), artifacts_before)

    def test_stage9_cli_parser_is_closed_and_nonempty_root_is_neutral(self) -> None:
        arguments = _parser().parse_args(
            [
                "--stage",
                "stage9",
                "--project-root",
                "/tmp/project",
                "--store-root",
                "/tmp/work",
            ]
        )
        self.assertEqual(arguments.stage, "stage9")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory) / "occupied"
            root.mkdir()
            sentinel = root / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage9_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
