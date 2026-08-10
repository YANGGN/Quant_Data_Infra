from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage4 import compare_clean_stage4_rebuilds, run_clean_stage4_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage4_golden.json"
STORE_FILENAMES = {
    "market.sqlite",
    "macro.sqlite",
    "company.sqlite",
    "news.sqlite",
}


class Stage4IntegrationTests(unittest.TestCase):
    def test_two_clean_rebuilds_match_the_approved_stage4_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage4_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage4_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            for flag in (
                "repeated_initialization_unchanged",
                "source_reads_unchanged",
                "source_backup_unchanged",
                "backup_restore_unchanged",
                "restored_queries_equal",
                "restored_health_equal",
            ):
                self.assertTrue(evidence[flag])
            self.assertTrue(all(evidence["no_write"].values()))
            self.assertEqual(
                len(evidence["stage4_fixture_evidence"]["fixtures"]),
                17,
            )
            self.assertEqual(
                set(evidence["receipts"]["rejected_options"]),
                {
                    "stage4.market.options.mixed_feed_environment",
                    "stage4.market.options.unknown_contract",
                    "stage4.market.options.incoherent_inputs",
                },
            )

            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(golden["schema_version"], "1.0.0")
            self.assertEqual(golden["approval_status"], "fixture_validated")
            actual = {
                "schema_version": evidence["contract_version"],
                "approval_status": "fixture_validated",
                "registry_revision": evidence["registry_revision"],
                "evidence_sha256": evidence["sha256"],
                "stage3_evidence_sha256": evidence["stage3_evidence_sha256"],
                "stage4_fixture_evidence_sha256": evidence[
                    "stage4_fixture_evidence"
                ]["sha256"],
                "golden_query_sha256": evidence["golden_query_sha256"],
                "logical_manifest_sha256": evidence["logical_manifest_sha256"],
                "migration_heads": evidence["migration_heads"],
                "dataset_counts": evidence["dataset_counts"],
            }
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
            dumps_strict(evidence)

            for rebuild in ("first", "second"):
                for cohort in ("stage3/stage2/source", "backup", "restored"):
                    self.assertEqual(
                        {
                            path.name
                            for path in (temporary / rebuild / cohort).glob("*.sqlite")
                        },
                        STORE_FILENAMES,
                    )
        self.assertFalse((PROJECT_ROOT / "data").exists())

    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            marker = root / "preserve.txt"
            marker.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage4_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
