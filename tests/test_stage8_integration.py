from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant_data.__main__ import _parser
from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage8 import compare_clean_stage8_rebuilds, run_clean_stage8_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage8_golden.json"


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
        "stage7_evidence_sha256",
        "export_ids",
        "export_contract_sha256",
        "query_contract_sha256",
        "schema_contract_sha256",
        "revision_id",
        "public_manifest_sha256",
        "public_snapshot_sha256",
        "private_receipt_manifest_sha256",
        "publication_manifest_sha256",
        "migration_heads",
        "dataset_ids",
        "dataset_rows",
        "dataset_chunks",
        "asset_sha256",
        "source_reads_unchanged",
        "read_only_copies",
        "cross_store_atomic",
        "immutable_revision_reused",
        "failure_preserved_current",
        "pointer_receipt_binding_verified",
        "private_receipt_before_pointer",
        "derived_rebuild_equal",
        "sqlite_authoritative",
        "json_export_only",
        "parquet_decision",
        "parquet_dependencies",
        "duckdb_dependencies",
        "live_provider_calls",
        "scheduler_installations",
        "scheduler_starts",
        "hosting_operations",
        "exports",
    )
    return {
        "schema_version": evidence["contract_version"],
        "approval_status": "fixture_validated",
        "evidence_sha256": evidence["sha256"],
        **{key: evidence[key] for key in keys},
    }


class Stage8IntegrationTests(unittest.TestCase):
    def test_two_clean_rebuilds_match_the_approved_stage8_evidence(self) -> None:
        project_artifacts_before = _project_runtime_artifacts()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary = Path(directory)
            evidence = compare_clean_stage8_rebuilds(
                project_root=PROJECT_ROOT,
                first_work_root=temporary / "first",
                second_work_root=temporary / "second",
            )
            self.assertEqual(evidence["contract"], "quant_data.stage8_rebuild_evidence")
            self.assertEqual(evidence["contract_version"], "1.0.0")
            self.assertEqual(evidence["registry_revision"], "2.6.0")
            self.assertEqual(evidence["registry_schema_version"], "1.4.0")
            self.assertEqual(evidence["export_ids"], ["atlas.fixture_snapshot"])
            self.assertEqual(len(evidence["dataset_ids"]), 4)
            self.assertEqual(evidence["exports"], 1)
            self.assertFalse(evidence["cross_store_atomic"])
            for flag in (
                "source_reads_unchanged",
                "read_only_copies",
                "immutable_revision_reused",
                "failure_preserved_current",
                "pointer_receipt_binding_verified",
                "private_receipt_before_pointer",
                "derived_rebuild_equal",
                "sqlite_authoritative",
                "json_export_only",
            ):
                self.assertTrue(evidence[flag])
            for count in (
                "parquet_dependencies",
                "duckdb_dependencies",
                "live_provider_calls",
                "scheduler_installations",
                "scheduler_starts",
                "hosting_operations",
            ):
                self.assertEqual(evidence[count], 0)
            dumps_strict(evidence)

            for rebuild in ("first", "second"):
                root = temporary / rebuild
                for output_name in ("atlas-output", "atlas-rebuilt"):
                    output = root / output_name
                    self.assertTrue(output.is_dir())
                    self.assertEqual(list((output / ".staging").iterdir()), [])
                    self.assertFalse(
                        any(
                            path.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm"))
                            for path in output.rglob("*")
                        )
                    )
                    self.assertTrue(
                        (output / "current" / "atlas.fixture_snapshot.json").is_file()
                    )
                self.assertTrue((root / "stage7").is_dir())

            actual = _golden_projection(evidence)
            if not GOLDEN_PATH.is_file():
                self.fail(dumps_strict(actual))
            golden = loads_strict(GOLDEN_PATH.read_bytes())
            self.assertEqual(dumps_strict(actual), dumps_strict(golden))
        self.assertEqual(_project_runtime_artifacts(), project_artifacts_before)

    def test_stage8_cli_parser_accepts_the_closed_stage_name(self) -> None:
        arguments = _parser().parse_args(
            [
                "--stage",
                "stage8",
                "--project-root",
                "/tmp/project",
                "--store-root",
                "/tmp/work",
            ]
        )
        self.assertEqual(arguments.stage, "stage8")

    def test_clean_rebuild_refuses_a_nonempty_explicit_work_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            sentinel = root / "preserve.txt"
            sentinel.write_text("user data", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage8_rebuild(project_root=PROJECT_ROOT, work_root=root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")
            self.assertEqual(list(root.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
