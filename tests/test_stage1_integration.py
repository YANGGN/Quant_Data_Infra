from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage1 import compare_clean_rebuilds, run_clean_rebuild
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage1_golden.json"


class Stage1IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)

    def tearDown(self) -> None:
        assert_project_data_unchanged(
            self._project_data_before,
            project_root=PROJECT_ROOT,
        )

    def test_two_clean_rebuilds_produce_equal_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            evidence = compare_clean_rebuilds(
                project_root=PROJECT_ROOT,
                first_store_root=temporary / "first",
                second_store_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage1_rebuild_evidence")
            self.assertTrue(evidence["public_reads_unchanged"])
            self.assertEqual(
                set(evidence["migration_heads"]),
                {"market", "macro", "company", "news"},
            )
            self.assertEqual(evidence["ingestion_receipts"]["market_replay"]["outcome"], "unchanged")
            self.assertEqual(evidence["ingestion_receipts"]["macro_replay"]["outcome"], "unchanged")
            self.assertEqual(len(evidence["fixture_evidence"]), 4)
            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(
                set(golden),
                {
                    "schema_version",
                    "approval_status",
                    "registry_revision",
                    "evidence_sha256",
                    "logical_manifest_sha256",
                    "golden_results_sha256",
                    "fixture_semantic_identities",
                    "state_counts",
                },
            )
            self.assertEqual(golden["schema_version"], "1.0.0")
            self.assertEqual(golden["approval_status"], "fixture_validated")
            # Stage 1's approved golden remains immutable historical evidence.
            # Stage 2 deliberately changes the registry and migration/control
            # plane hashes, while these fixture identities and domain state
            # transitions must remain exactly stable.
            self.assertEqual(evidence["registry_revision"], "2.0.0")
            self.assertEqual(
                {
                    item["id"]: item["semantic_identity"]
                    for item in evidence["fixture_evidence"]
                },
                golden["fixture_semantic_identities"],
            )
            self.assertEqual(evidence["state_counts"], golden["state_counts"])
            dumps_strict(evidence)
            for root_name in ("first", "second"):
                files = {path.name for path in (temporary / root_name).glob("*.sqlite")}
                self.assertEqual(
                    files,
                    {"market.sqlite", "macro.sqlite", "company.sqlite", "news.sqlite"},
                )
    def test_clean_rebuild_refuses_a_nonempty_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "preserve.txt"
            marker.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_rebuild(project_root=PROJECT_ROOT, store_root=root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
