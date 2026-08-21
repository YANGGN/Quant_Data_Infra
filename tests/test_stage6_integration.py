from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage6 import compare_clean_stage6_rebuilds, run_clean_stage6_rebuild
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage6_golden.json"
STORE_FILENAMES = {
    "market.sqlite",
    "macro.sqlite",
    "company.sqlite",
    "news.sqlite",
}


class Stage6IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)

    def tearDown(self) -> None:
        assert_project_data_unchanged(
            self._project_data_before,
            project_root=PROJECT_ROOT,
        )

    def test_two_clean_rebuilds_match_the_approved_stage6_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage6_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage6_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            for flag in (
                "source_reads_unchanged",
                "restored_reads_unchanged",
                "source_restored_equal",
                "loopback_only",
            ):
                self.assertTrue(evidence[flag])
            self.assertEqual(evidence["runtime_network_assets"], 0)
            self.assertEqual(evidence["jobs"], 0)
            self.assertEqual(evidence["exports"], 0)
            self.assertEqual(
                evidence["dashboard_routes"],
                ["/", "/gdp-vintages", "/table-inspector", "/agent-tools"],
            )

            golden = loads_strict(GOLDEN_PATH.read_bytes())
            actual = {
                "schema_version": evidence["contract_version"],
                "approval_status": "fixture_validated",
                "registry_revision": evidence["registry_revision"],
                "registry_schema_version": evidence["registry_schema_version"],
                "evidence_sha256": evidence["sha256"],
                "stage5_evidence_sha256": evidence["stage5_evidence_sha256"],
                "portal_snapshot_sha256": evidence["portal_snapshot_sha256"],
                "inter_font_sha256": evidence["inter_font_sha256"],
                "inter_license_sha256": evidence["inter_license_sha256"],
                "dashboard_ids": evidence["dashboard_ids"],
                "dashboard_routes": evidence["dashboard_routes"],
                "dashboard_api_routes": evidence["dashboard_api_routes"],
                "page_sha256": evidence["page_sha256"],
                "api_sha256": evidence["api_sha256"],
                "asset_sha256": evidence["asset_sha256"],
                "migration_heads": evidence["migration_heads"],
            }
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
            dumps_strict(evidence)

            for rebuild in ("first", "second"):
                for cohort in (
                    "stage5/stage4/stage3/stage2/source",
                    "stage5/stage4/backup",
                    "stage5/stage4/restored",
                ):
                    self.assertEqual(
                        {
                            path.name
                            for path in (temporary / rebuild / cohort).glob("*.sqlite")
                        },
                        STORE_FILENAMES,
                    )
    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            marker = root / "preserve.txt"
            marker.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage6_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
