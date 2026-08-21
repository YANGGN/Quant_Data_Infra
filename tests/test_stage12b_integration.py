from __future__ import annotations

import hashlib
import io
import os
import socket
import sqlite3
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ConflictError, ValidationError
from quant_data.__main__ import main
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage12b import (
    compare_clean_stage12b_rebuilds,
    run_clean_stage12b_rebuild,
    run_stage12b_dry_run,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage12b_golden.json"
_DEFAULT_ARTIFACTS = (
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
    result: dict[str, tuple[int, int, int, int, int] | None] = {}
    for relative in _DEFAULT_ARTIFACTS:
        path = PROJECT_ROOT / relative
        try:
            metadata = path.stat()
        except FileNotFoundError:
            result[relative] = None
        else:
            result[relative] = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
    return result


def _evidence_sha256(evidence: dict[str, object]) -> str:
    material = {key: value for key, value in evidence.items() if key != "sha256"}
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _golden_projection(evidence: dict[str, object]) -> dict[str, object]:
    keys = (
        "approval_status",
        "canonical_registry",
        "collector",
        "contract",
        "contract_version",
        "dry_run",
        "execution",
        "fixture_rehearsal",
        "plan",
        "scope",
        "stage12a_projection",
    )
    return {
        "schema_version": evidence["contract_version"],
        "evidence_sha256": evidence["sha256"],
        **{key: evidence[key] for key in keys},
    }

def _cli(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = main(list(arguments))
    return status, stdout.getvalue(), stderr.getvalue()




class Stage12BDryRunTests(unittest.TestCase):
    def test_dry_run_is_deterministic_path_free_and_has_no_operational_effects(
        self,
    ) -> None:
        artifacts_before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "first"
            second_root = temporary_root / "second"
            with (
                patch.object(
                    sqlite3,
                    "connect",
                    side_effect=AssertionError("Stage 12B dry run must not open SQLite"),
                ),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("Stage 12B dry run must not use a socket"),
                ),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("Stage 12B dry run must not use a socket"),
                ),
                patch.object(
                    subprocess,
                    "Popen",
                    side_effect=AssertionError("Stage 12B dry run must not start a subprocess"),
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=AssertionError("Stage 12B dry run must not start a subprocess"),
                ),
                patch.object(
                    os,
                    "getenv",
                    side_effect=AssertionError("Stage 12B dry run must not read environment"),
                ),
                patch.object(
                    Path,
                    "mkdir",
                    side_effect=AssertionError("Stage 12B dry run must not create a root"),
                ),
                patch.object(
                    Path,
                    "write_bytes",
                    side_effect=AssertionError("Stage 12B dry run must not write"),
                ),
                patch.object(
                    Path,
                    "write_text",
                    side_effect=AssertionError("Stage 12B dry run must not write"),
                ),
            ):
                first = run_stage12b_dry_run(
                    project_root=PROJECT_ROOT,
                    work_root=first_root,
                )
                second = run_stage12b_dry_run(
                    project_root=PROJECT_ROOT,
                    work_root=second_root,
                )
            self.assertFalse(first_root.exists())
            self.assertFalse(second_root.exists())

        self.assertEqual(dumps_strict(first), dumps_strict(second))
        self.assertEqual(first["sha256"], _evidence_sha256(first))
        serialized = dumps_strict(first)
        self.assertNotIn(str(PROJECT_ROOT), serialized)
        self.assertNotIn(str(first_root), serialized)
        self.assertNotIn(str(second_root), serialized)
        self.assertEqual(first["canonical_registry"], {  # type: ignore[index]
            "revision": "2.13.0",
            "schema_version": "1.8.0",
            "source_sha256": "b39057d548d6ced6e7c0663ffafbee7e0c16c88a94baced584ff6949da7766f6",
        })
        self.assertEqual(first["dry_run"], {  # type: ignore[index]
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "fixture_transport_calls": 0,
            "locks_acquired": 0,
            "network_calls": 0,
            "scheduler_actions": 0,
            "sqlite_stores_opened": 0,
            "subprocess_calls": 0,
            "transactions_started": 0,
            "work_root_policy": "explicit_absent_or_empty_isolated_fixture_only",
        })
        self.assertEqual(first["plan"]["fixture_request"], {  # type: ignore[index]
            "asset_type": "equity",
            "endpoint_path": "/stable/historical-price-eod/full",
            "from": "2026-08-10",
            "price_variant": "fmp_full_eod_v1",
            "provider": "fmp",
            "session_count": 3,
            "symbol": "AAPL",
            "to": "2026-08-12",
        })
        self.assertEqual(_artifact_state(), artifacts_before)

    def test_dry_run_rejects_relative_project_and_unsafe_roots_without_mutation(
        self,
    ) -> None:
        artifacts_before = _artifact_state()
        with self.assertRaises(ValidationError):
            run_stage12b_dry_run(
                project_root=".",
                work_root="/tmp/stage12b-relative-project-root",
            )
        with self.assertRaises(ValidationError):
            run_stage12b_dry_run(
                project_root=PROJECT_ROOT,
                work_root="stage12b-relative-work-root",
            )
        with self.assertRaises(ConflictError):
            run_stage12b_dry_run(
                project_root=PROJECT_ROOT,
                work_root=PROJECT_ROOT / "data" / "market.sqlite",
            )
        with self.assertRaises(ConflictError):
            run_stage12b_dry_run(
                project_root=PROJECT_ROOT,
                work_root="/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1",
            )
        self.assertEqual(_artifact_state(), artifacts_before)


class Stage12BFixtureRehearsalTests(unittest.TestCase):
    def test_two_isolated_roots_produce_deterministic_path_free_golden_evidence(self) -> None:
        artifacts_before = _artifact_state()
        original_connect = sqlite3.connect
        opened_databases: list[str] = []

        def guarded_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
            database_text = str(database)
            opened_databases.append(database_text)
            for protected in (
                PROJECT_ROOT / "data" / "market.sqlite",
                PROJECT_ROOT / "data" / "market_data.sqlite",
                Path("/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607"),
                Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"),
            ):
                self.assertNotIn(str(protected), database_text)
            return original_connect(database, *args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "first"
            second_root = temporary_root / "second"
            second_root.mkdir()
            with (
                patch.object(sqlite3, "connect", side_effect=guarded_connect),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("Stage 12B must not use a socket"),
                ),
                patch.object(
                    socket,
                    "getaddrinfo",
                    side_effect=AssertionError("Stage 12B must not resolve DNS"),
                ),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("Stage 12B must not use a socket"),
                ),
                patch.object(
                    subprocess,
                    "Popen",
                    side_effect=AssertionError("Stage 12B must not start a subprocess"),
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=AssertionError("Stage 12B must not start a subprocess"),
                ),
                patch.object(
                    os,
                    "getenv",
                    side_effect=AssertionError("Stage 12B must not read environment"),
                ),
            ):
                evidence = compare_clean_stage12b_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=first_root,
                    second_work_root=second_root,
                )
            self.assertTrue(first_root.is_dir())
            self.assertTrue(second_root.is_dir())
            serialized = dumps_strict(evidence)
            self.assertNotIn(str(PROJECT_ROOT), serialized)
            self.assertNotIn(str(first_root), serialized)
            self.assertNotIn(str(second_root), serialized)

        self.assertTrue(opened_databases)
        expected_keys = {
            "approval_status",
            "canonical_registry",
            "collector",
            "contract",
            "contract_version",
            "dry_run",
            "execution",
            "fixture_rehearsal",
            "plan",
            "scope",
            "sha256",
            "stage12a_projection",
        }
        self.assertEqual(set(evidence), expected_keys)
        self.assertEqual(evidence["sha256"], _evidence_sha256(evidence))
        self.assertEqual(evidence["execution"], {  # type: ignore[index]
            "credential_environment_reads": 0,
            "fixture_transport_calls": 14,
            "live_provider_calls": 0,
            "network_calls": 0,
            "operational_sqlite_stores_opened": 0,
            "scheduler_actions": 0,
            "stage9_to_stage11_runner_calls": 0,
            "temporary_fixture_store_roles_initialized": 4,
        })
        rehearsal = evidence["fixture_rehearsal"]  # type: ignore[index]
        self.assertEqual(rehearsal["canonical_counts"], {  # type: ignore[index]
            "ingestion_artifacts": 3,
            "ingestion_runs": 4,
            "ingestion_snapshots": 3,
            "stage10_daily_price_captures": 3,
            "stage10_daily_price_versions": 5,
            "stage10_daily_prices": 4,
            "stage10_instruments": 1,
        })
        self.assertEqual(rehearsal["rejections"], [  # type: ignore[index]
            "malformed_json",
            "partial_complete_batch",
            "retry_classified_status_503",
            "raw_bytes_different_descriptor",
            "elapsed_seconds_bound",
            "backdated_changed_key",
            "lock_failure",
            "commit_failure",
        ])
        self.assertEqual(rehearsal["integrity"], {  # type: ignore[index]
            "broken_supersession_count": 0,
            "duplicate_current_count": 0,
            "foreign_key_violation_count": 0,
            "integrity_check": ["ok"],
            "invalid_current_pointer_count": 0,
            "maximum_correction_sequence": 2,
        })
        self.assertTrue(GOLDEN_PATH.is_file())
        self.assertEqual(
            dumps_strict(_golden_projection(evidence)),
            dumps_strict(loads_strict(GOLDEN_PATH.read_bytes())),
        )
        self.assertEqual(_artifact_state(), artifacts_before)

    def test_rehearsal_rejects_nonempty_project_default_candidate_same_and_symlink_roots(self) -> None:
        artifacts_before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            occupied = temporary_root / "occupied"
            occupied.mkdir()
            sentinel = occupied / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(ConflictError):
                run_clean_stage12b_rebuild(project_root=PROJECT_ROOT, work_root=occupied)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

            same_root = temporary_root / "same"
            with self.assertRaises(ConflictError):
                compare_clean_stage12b_rebuilds(
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
                compare_clean_stage12b_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=physical_root,
                    second_work_root=alias,
                )
            self.assertTrue(alias.is_symlink())
            self.assertEqual(tuple(physical_root.iterdir()), ())

        for protected_root in (
            PROJECT_ROOT / "tests",
            PROJECT_ROOT / "data" / "market.sqlite",
            Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"),
        ):
            with self.assertRaises(ConflictError):
                run_clean_stage12b_rebuild(
                    project_root=PROJECT_ROOT,
                    work_root=protected_root,
                )
        self.assertEqual(_artifact_state(), artifacts_before)


    def test_cli_requires_two_distinct_isolated_roots(self) -> None:
        artifacts_before = _artifact_state()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "cli-first"
            second_root = temporary_root / "cli-second"
            status, stdout, stderr = _cli(
                "--stage",
                "stage12b",
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
            self.assertEqual(
                dumps_strict(_golden_projection(evidence)),  # type: ignore[arg-type]
                dumps_strict(loads_strict(GOLDEN_PATH.read_bytes())),
            )
            self.assertTrue(first_root.is_dir())
            self.assertTrue(second_root.is_dir())
            serialized = dumps_strict(evidence)
            self.assertNotIn(str(PROJECT_ROOT), serialized)
            self.assertNotIn(str(first_root), serialized)
            self.assertNotIn(str(second_root), serialized)

            missing_first = temporary_root / "missing-first"
            status, stdout, stderr = _cli(
                "--stage",
                "stage12b",
                "--project-root",
                str(PROJECT_ROOT),
                "--store-root",
                str(missing_first),
            )
            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            missing_error = loads_strict(stderr)
            self.assertEqual(missing_error["error"]["code"], "invalid_request")  # type: ignore[index]
            self.assertFalse(missing_first.exists())

            physical_root = temporary_root / "cli-physical"
            physical_root.mkdir()
            alias_root = temporary_root / "cli-alias"
            alias_root.symlink_to(physical_root, target_is_directory=True)
            status, stdout, stderr = _cli(
                "--stage",
                "stage12b",
                "--project-root",
                str(PROJECT_ROOT),
                "--store-root",
                str(physical_root),
                "--second-store-root",
                str(alias_root),
            )
            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            alias_error = loads_strict(stderr)
            self.assertEqual(alias_error["error"]["code"], "conflict")  # type: ignore[index]
            self.assertTrue(alias_root.is_symlink())
            self.assertEqual(tuple(physical_root.iterdir()), ())
        self.assertEqual(_artifact_state(), artifacts_before)


if __name__ == "__main__":
    unittest.main()
