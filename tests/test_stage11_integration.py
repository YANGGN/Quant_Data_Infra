from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage11 import compare_clean_stage11_rebuilds, run_clean_stage11_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage11_golden.json"


def _runtime_artifacts() -> tuple[str, ...]:
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
        "scope_manifest_sha256",
        "target_profile_id",
        "providers",
        "synthetic_capture_counts",
        "publication_written_count",
        "semantic_replay_outcome",
        "correction_rehearsal",
        "canonical_counts",
        "reconciliation_sha256",
        "repopulation_evidence_sha256",
        "candidate_receipt",
        "migration_heads_sha256",
        "macro_migration_head",
        "backup_source_sha256",
        "backup_copy_sha256",
        "restored_sha256",
        "backup_restore_equal",
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


class Stage11IntegrationTests(unittest.TestCase):
    def test_two_clean_rebuilds_match_path_free_offline_evidence(self) -> None:
        artifacts_before = _runtime_artifacts()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            evidence = compare_clean_stage11_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=root / "first",
                second_work_root=root / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage11_rebuild_evidence")
            self.assertEqual(evidence["registry_revision"], "2.9.0")
            self.assertEqual(evidence["registry_schema_version"], "1.7.0")
            self.assertEqual(evidence["providers"], ["bea", "eia"])
            self.assertEqual(evidence["synthetic_capture_counts"], {
                "bea_requests": 2, "eia_retail_pages": 1, "eia_weekly_requests": 1,
            })
            self.assertEqual(evidence["semantic_replay_outcome"], "unchanged")
            self.assertEqual(evidence["correction_rehearsal"]["max_bea_correction_sequence"], 2)
            self.assertEqual(evidence["canonical_counts"], {
                "stage11_bea_nipa_captures": 3,
                "stage11_bea_nipa_observation_versions": 5,
                "stage11_bea_nipa_observations": 4,
                "stage11_eia_retail_captures": 1,
                "stage11_eia_retail_observation_versions": 8,
                "stage11_eia_retail_observations": 8,
                "stage11_eia_weekly_captures": 1,
                "stage11_eia_weekly_observation_versions": 2,
                "stage11_eia_weekly_observations": 2,
            })
            self.assertTrue(evidence["backup_restore_equal"])
            self.assertFalse(evidence["operational_promotion_performed"])
            self.assertFalse(evidence["old_store_retirement_performed"])
            self.assertEqual(evidence["live_provider_calls"], 0)
            self.assertEqual(evidence["credential_values_recorded"], 0)
            self.assertEqual(evidence["public_export_ids"], [])
            dumps_strict(evidence)
            for name in ("first", "second"):
                work = root / name
                for child in ("source", "backup", "restored", "private-state"):
                    self.assertTrue((work / child).is_dir())
                receipts = tuple((work / "private-state" / "promotion-candidates").glob("*.json"))
                self.assertEqual(len(receipts), 1)
            self.assertTrue(GOLDEN_PATH.is_file())
            self.assertEqual(
                dumps_strict(_golden_projection(evidence)),
                dumps_strict(loads_strict(GOLDEN_PATH.read_bytes())),
            )
        self.assertEqual(_runtime_artifacts(), artifacts_before)

    def test_nonempty_work_root_is_neutral(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory) / "occupied"
            root.mkdir()
            sentinel = root / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage11_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
