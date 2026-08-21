from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage2 import compare_clean_stage2_rebuilds, run_clean_stage2_rebuild
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage2_golden.json"
STORE_FILENAMES = {
    "market.sqlite",
    "macro.sqlite",
    "company.sqlite",
    "news.sqlite",
}


class Stage2IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)

    def tearDown(self) -> None:
        assert_project_data_unchanged(
            self._project_data_before,
            project_root=PROJECT_ROOT,
        )

    def test_two_clean_rebuilds_match_the_approved_stage2_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage2_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage2_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            for flag in (
                "source_reads_unchanged",
                "source_backup_unchanged",
                "backup_restore_unchanged",
                "restored_queries_equal",
                "restored_composition_equal",
            ):
                self.assertTrue(evidence[flag])

            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(golden["schema_version"], "1.0.0")
            self.assertEqual(golden["approval_status"], "fixture_validated")
            actual = {
                "schema_version": evidence["contract_version"],
                "approval_status": "fixture_validated",
                "registry_revision": evidence["registry_revision"],
                "evidence_sha256": evidence["sha256"],
                "stage1_fixture_evidence_sha256": evidence[
                    "stage1_fixture_evidence_sha256"
                ],
                "stage1_golden_results_sha256": evidence[
                    "stage1_golden_results_sha256"
                ],
                "golden_query_sha256": evidence["golden_query_sha256"],
                "composition_sha256": evidence["composition"]["sha256"],
                "logical_manifest_sha256": evidence["logical_manifest_sha256"],
                "migration_heads": evidence["migration_heads"],
                "control_plane_counts": evidence["control_plane_counts"],
            }
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
            dumps_strict(evidence)

            for rebuild in ("first", "second"):
                for cohort in ("source", "backup", "restored"):
                    self.assertEqual(
                        {
                            path.name
                            for path in (temporary / rebuild / cohort).glob("*.sqlite")
                        },
                        STORE_FILENAMES,
                    )
    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "preserve.txt"
            marker.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage2_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
