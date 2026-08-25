from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.migrations import initialize_all
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage5_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.stage4 import run_clean_stage4_rebuild
from quant_data.stage5 import (
    _tool_matrix,
    compare_clean_stage5_rebuilds,
    run_clean_stage5_rebuild,
)
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage5_golden.json"


class Stage5IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)

    def tearDown(self) -> None:
        assert_project_data_unchanged(
            self._project_data_before,
            project_root=PROJECT_ROOT,
        )

    def test_two_clean_rebuilds_match_the_approved_stage5_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage5_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage5_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            for flag in (
                "source_reads_unchanged",
                "restored_reads_unchanged",
                "source_restored_equal",
                "http_in_process_parity",
                "offline_intraday_disabled",
            ):
                self.assertTrue(evidence[flag])
            self.assertEqual(evidence["tool_count"], 57)
            self.assertEqual(evidence["jobs"], 0)
            self.assertEqual(evidence["exports"], 0)

            golden = loads_strict(GOLDEN_PATH.read_bytes())
            actual = {
                "schema_version": evidence["contract_version"],
                "approval_status": "fixture_validated",
                "registry_revision": evidence["registry_revision"],
                "evidence_sha256": evidence["sha256"],
                "stage4_evidence_sha256": evidence["stage4_evidence_sha256"],
                "schema_catalog_sha256": evidence["schema_catalog_sha256"],
                "manifest_sha256": evidence["manifest_sha256"],
                "tool_matrix_sha256": evidence["tool_matrix_sha256"],
                "tool_count": evidence["tool_count"],
                "outcome_counts": evidence["outcome_counts"],
                "migration_heads": evidence["migration_heads"],
            }
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
            dumps_strict(evidence)
    def test_stage5_public_tools_cannot_reach_writer_entry_points(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            work_root = temporary / "work"
            run_clean_stage4_rebuild(
                project_root=PROJECT_ROOT,
                work_root=work_root / "stage4",
            )
            registry = stage5_registry_profile(
                load_registry(
                    PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                    project_root=PROJECT_ROOT,
                    environment={},
                )
            )
            stores = explicit_store_map(
                work_root / "stage4" / "stage3" / "stage2" / "source"
            )
            initialize_all(stores, registry)
            before = mutation_fingerprint(stores)

            with (
                patch(
                    "quant_data.ingestion.IngestionCoordinator.execute",
                    side_effect=AssertionError("tool reached ingestion writer"),
                ) as ingestion_writer,
                patch(
                    "quant_data.migrations.initialize_all",
                    side_effect=AssertionError("tool reached initialization"),
                ) as initializer,
                patch(
                    "quant_data.migrations.migrate_store",
                    side_effect=AssertionError("tool reached migration writer"),
                ) as migration_writer,
                patch(
                    "quant_data.stores.writer_connection",
                    side_effect=AssertionError("tool reached writer connection"),
                ) as connection_writer,
            ):
                matrix = _tool_matrix(stores, registry)

            self.assertEqual(len(matrix["tools"]), 57)
            ingestion_writer.assert_not_called()
            initializer.assert_not_called()
            migration_writer.assert_not_called()
            connection_writer.assert_not_called()
            self.assertEqual(
                dumps_strict(before),
                dumps_strict(mutation_fingerprint(stores)),
            )

    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            marker = root / "preserve.txt"
            marker.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage5_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
