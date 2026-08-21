from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quant_data.__main__ import main
from quant_data.errors import ConflictError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage12 import (
    compare_stage12a_authority_gates,
    run_stage12a_authority_gate,
)
from quant_data.stores import StoreRole, resolve_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage12_golden.json"
_STORE_ARTIFACTS = (
    "data/market.sqlite",
    "data/market.sqlite-journal",
    "data/market.sqlite-shm",
    "data/market.sqlite-wal",
    "data/market_data.sqlite",
    "data/market_data.sqlite-journal",
    "data/market_data.sqlite-shm",
    "data/market_data.sqlite-wal",
)


def _artifact_state() -> dict[str, tuple[int, int, int, int, int] | None]:
    state: dict[str, tuple[int, int, int, int, int] | None] = {}
    for relative_path in _STORE_ARTIFACTS:
        path = PROJECT_ROOT / relative_path
        try:
            metadata = path.stat()
        except FileNotFoundError:
            state[relative_path] = None
        else:
            state[relative_path] = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
    return state


def _golden_projection(evidence: dict[str, object]) -> dict[str, object]:
    keys = (
        "approval_status",
        "canonical_registry",
        "contract",
        "contract_version",
        "excluded_claims",
        "execution",
        "market_v1_baseline",
        "retained_stage10",
        "roster",
        "scope_manifest_sha256",
        "source_bindings",
        "stage10_projection",
        "store_defaults",
        "successor_phases",
        "target_profile_id",
    )
    return {
        "schema_version": evidence["contract_version"],
        "evidence_sha256": evidence["sha256"],
        **{key: evidence[key] for key in keys},
    }


def _evidence_sha256(evidence: dict[str, object]) -> str:
    material = {key: value for key, value in evidence.items() if key != "sha256"}
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _cli(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = main(list(arguments))
    return status, stdout.getvalue(), stderr.getvalue()


class Stage12IntegrationTests(unittest.TestCase):
    def test_current_registry_resolves_the_approved_market_path_without_opening_store(
        self,
    ) -> None:
        before = _artifact_state()
        with patch(
            "sqlite3.connect",
            side_effect=AssertionError("Stage 12A resolver must not open SQLite"),
        ):
            registry = load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
            store_map = resolve_store_map(
                registry,
                project_root=PROJECT_ROOT,
                environment={},
            )
        expected = {
            StoreRole.MARKET: PROJECT_ROOT / "data" / "market.sqlite",
            StoreRole.MACRO: PROJECT_ROOT / "data" / "macro.sqlite",
            StoreRole.COMPANY: PROJECT_ROOT / "data" / "company.sqlite",
            StoreRole.NEWS: PROJECT_ROOT / "data" / "news.sqlite",
        }
        self.assertEqual(
            {role: store_map.path(role) for role in expected},
            {role: path.resolve(strict=False) for role, path in expected.items()},
        )
        self.assertEqual(_artifact_state(), before)

    def test_two_neutral_roots_produce_golden_path_free_evidence_without_side_effects(
        self,
    ) -> None:
        before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "first"
            second_root = temporary_root / "second"
            with (
                patch(
                    "sqlite3.connect",
                    side_effect=AssertionError("Stage 12A must not open SQLite"),
                ),
                patch(
                    "socket.create_connection",
                    side_effect=AssertionError("Stage 12A must not use the network"),
                ),
                patch(
                    "subprocess.Popen",
                    side_effect=AssertionError("Stage 12A must not start a subprocess"),
                ),
                patch(
                    "pathlib.Path.mkdir",
                    side_effect=AssertionError("Stage 12A must not create a work root"),
                ),
                patch(
                    "pathlib.Path.write_bytes",
                    side_effect=AssertionError("Stage 12A must not write"),
                ),
                patch(
                    "pathlib.Path.write_text",
                    side_effect=AssertionError("Stage 12A must not write"),
                ),
                patch(
                    "pathlib.Path.touch",
                    side_effect=AssertionError("Stage 12A must not write"),
                ),
                patch(
                    "pathlib.Path.unlink",
                    side_effect=AssertionError("Stage 12A must not delete"),
                ),
                patch(
                    "pathlib.Path.rename",
                    side_effect=AssertionError("Stage 12A must not move"),
                ),
                patch(
                    "pathlib.Path.replace",
                    side_effect=AssertionError("Stage 12A must not replace"),
                ),
            ):
                evidence = compare_stage12a_authority_gates(
                    project_root=PROJECT_ROOT,
                    first_work_root=first_root,
                    second_work_root=second_root,
                )
            self.assertFalse(first_root.exists())
            self.assertFalse(second_root.exists())
            serialized = dumps_strict(evidence)
            self.assertNotIn(str(PROJECT_ROOT), serialized)
            self.assertNotIn(str(first_root), serialized)
            self.assertNotIn(str(second_root), serialized)

        expected_keys = {
            "approval_status",
            "canonical_registry",
            "contract",
            "contract_version",
            "excluded_claims",
            "execution",
            "market_v1_baseline",
            "retained_stage10",
            "roster",
            "scope_manifest_sha256",
            "sha256",
            "source_bindings",
            "stage10_projection",
            "store_defaults",
            "successor_phases",
            "target_profile_id",
        }
        self.assertEqual(set(evidence), expected_keys)
        self.assertEqual(evidence["canonical_registry"]["schema_version"], "1.8.0")  # type: ignore[index]
        self.assertEqual(evidence["canonical_registry"]["revision"], "2.12.0")  # type: ignore[index]
        self.assertEqual(evidence["stage10_projection"]["schema_version"], "1.6.0")  # type: ignore[index]
        self.assertEqual(evidence["stage10_projection"]["revision"], "2.8.0")  # type: ignore[index]
        self.assertEqual(evidence["roster"], {  # type: ignore[index]
            "asset_type_counts": {"equity": 519, "etf": 95, "index": 15},
            "sha256": "a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb",
            "symbol_count": 629,
        })
        self.assertEqual(evidence["execution"]["sqlite_stores_opened"], 0)  # type: ignore[index]
        self.assertEqual(evidence["execution"]["filesystem_mutations"], 0)  # type: ignore[index]
        self.assertEqual(_evidence_sha256(evidence), evidence["sha256"])
        self.assertTrue(GOLDEN_PATH.is_file())
        self.assertEqual(
            dumps_strict(_golden_projection(evidence)),
            dumps_strict(loads_strict(GOLDEN_PATH.read_bytes())),
        )
        self.assertEqual(_artifact_state(), before)

    def test_nonempty_and_inside_project_roots_are_rejected_without_mutation(self) -> None:
        before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            occupied = Path(directory) / "occupied"
            occupied.mkdir()
            sentinel = occupied / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_stage12a_authority_gate(
                    project_root=PROJECT_ROOT,
                    work_root=occupied,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

        inside_project = PROJECT_ROOT / "tests"
        before_inside = inside_project.stat()
        with self.assertRaises(ConflictError):
            run_stage12a_authority_gate(
                project_root=PROJECT_ROOT,
                work_root=inside_project,
            )
        after_inside = inside_project.stat()
        self.assertEqual(
            (after_inside.st_ino, after_inside.st_size, after_inside.st_mtime_ns),
            (before_inside.st_ino, before_inside.st_size, before_inside.st_mtime_ns),
        )
        self.assertEqual(_artifact_state(), before)

    def test_relative_project_and_work_roots_are_rejected_before_resolution(self) -> None:
        before = _artifact_state()
        with self.assertRaises(ValidationError):
            run_stage12a_authority_gate(
                project_root=".",
                work_root=Path("/tmp") / "stage12a-relative-project-root",
            )
        with self.assertRaises(ValidationError):
            run_stage12a_authority_gate(
                project_root=PROJECT_ROOT,
                work_root="stage12a-relative-work-root",
            )
        self.assertEqual(_artifact_state(), before)

    def test_compare_rejects_same_and_symlink_alias_work_roots_without_mutation(
        self,
    ) -> None:
        before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            same_root = temporary_root / "same"
            with self.assertRaises(ConflictError):
                compare_stage12a_authority_gates(
                    project_root=PROJECT_ROOT,
                    first_work_root=same_root,
                    second_work_root=same_root,
                )
            self.assertFalse(same_root.exists())

            physical_root = temporary_root / "physical"
            physical_root.mkdir()
            alias = temporary_root / "alias"
            alias.symlink_to(physical_root, target_is_directory=True)
            with self.assertRaises(ConflictError):
                compare_stage12a_authority_gates(
                    project_root=PROJECT_ROOT,
                    first_work_root=physical_root,
                    second_work_root=alias,
                )
            self.assertTrue(alias.is_symlink())
            self.assertEqual(tuple(physical_root.iterdir()), ())
        self.assertEqual(_artifact_state(), before)

    def test_cli_two_roots_success_and_conflict_boundaries(self) -> None:
        before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "cli-first"
            second_root = temporary_root / "cli-second"
            with patch(
                "sqlite3.connect",
                side_effect=AssertionError("Stage 12A CLI must not open SQLite"),
            ):
                status, stdout, stderr = _cli(
                    "--stage",
                    "stage12a",
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--store-root",
                    str(first_root),
                    "--second-store-root",
                    str(second_root),
                )
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            evidence = loads_strict(stdout)
            self.assertIsInstance(evidence, dict)
            self.assertEqual(evidence["sha256"], _evidence_sha256(evidence))  # type: ignore[arg-type,index]
            self.assertFalse(first_root.exists())
            self.assertFalse(second_root.exists())

            same_root = temporary_root / "cli-same"
            status, stdout, stderr = _cli(
                "--stage",
                "stage12a",
                "--project-root",
                str(PROJECT_ROOT),
                "--store-root",
                str(same_root),
                "--second-store-root",
                str(same_root),
            )
            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            error = loads_strict(stderr)
            self.assertEqual(error["error"]["code"], "conflict")  # type: ignore[index]
            self.assertFalse(same_root.exists())

        inside_project = PROJECT_ROOT / "tests"
        before_inside = inside_project.stat()
        status, stdout, stderr = _cli(
            "--stage",
            "stage12a",
            "--project-root",
            str(PROJECT_ROOT),
            "--store-root",
            str(inside_project),
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        error = loads_strict(stderr)
        self.assertEqual(error["error"]["code"], "conflict")  # type: ignore[index]
        after_inside = inside_project.stat()
        self.assertEqual(
            (after_inside.st_ino, after_inside.st_size, after_inside.st_mtime_ns),
            (before_inside.st_ino, before_inside.st_size, before_inside.st_mtime_ns),
        )
        self.assertEqual(_artifact_state(), before)


if __name__ == "__main__":
    unittest.main()
