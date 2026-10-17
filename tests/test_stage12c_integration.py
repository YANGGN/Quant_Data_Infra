from __future__ import annotations

import io
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

from quant_data.__main__ import main
from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stage12c import (
    STAGE12C_SCOPE_RAW_SHA256,
    STAGE12C_SCOPE_SEMANTIC_SHA256,
    compare_clean_stage12c_rebuilds,
    run_clean_stage12c_rehearsal,
    validate_stage12c_live_static_preflight,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage12c_golden.json"


def _cli(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = main(list(arguments))
    return status, stdout.getvalue(), stderr.getvalue()


class _ExplodingEnvironment(dict[str, str]):
    def get(self, key: str, default: object = None) -> object:
        raise AssertionError("Stage 12C static preflight must not read an environment")

    def __getitem__(self, key: str) -> str:
        raise AssertionError("Stage 12C static preflight must not read an environment")


class Stage12CStaticPreflightTests(unittest.TestCase):
    def test_static_preflight_is_source_only_path_free_and_uses_final_scope_digests(self) -> None:
        cwd = Path.cwd()
        with (
            patch.object(
                sqlite3,
                "connect",
                side_effect=AssertionError("Stage 12C static preflight must not open SQLite"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("Stage 12C static preflight must not use a socket"),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("Stage 12C static preflight must not use a socket"),
            ),
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("Stage 12C static preflight must not start a subprocess"),
            ),
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("Stage 12C static preflight must not start a subprocess"),
            ),
            patch.object(
                os,
                "getenv",
                side_effect=AssertionError("Stage 12C static preflight must not read an environment"),
            ),
            patch.object(
                Path,
                "mkdir",
                side_effect=AssertionError("Stage 12C static preflight must not create a path"),
            ),
            patch.object(
                Path,
                "write_bytes",
                side_effect=AssertionError("Stage 12C static preflight must not write a path"),
            ),
            patch.object(
                Path,
                "write_text",
                side_effect=AssertionError("Stage 12C static preflight must not write a path"),
            ),
            patch("quant_data.stage12c.os.environ", _ExplodingEnvironment()),
        ):
            evidence = validate_stage12c_live_static_preflight(Path.cwd())

        self.assertEqual(evidence["scope"]["raw_sha256"], STAGE12C_SCOPE_RAW_SHA256)  # type: ignore[index]
        self.assertEqual(evidence["scope"]["semantic_sha256"], STAGE12C_SCOPE_SEMANTIC_SHA256)  # type: ignore[index]
        self.assertEqual(evidence["plan"]["request"]["sentinel_symbol"], "AAPL")  # type: ignore[index]
        self.assertEqual(evidence["plan"]["request"]["session_dates"], ["2026-08-13", "2026-08-14"])  # type: ignore[index]
        self.assertEqual(evidence["zero_effects"], {  # type: ignore[index]
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "network_calls": 0,
            "sqlite_opens": 0,
            "subprocess_calls": 0,
        })
        serialized = dumps_strict(evidence)
        self.assertNotIn(str(cwd), serialized)
        self.assertNotIn("/home/volatility/quant-data-nonprod", serialized)


class Stage12CFixtureRehearsalTests(unittest.TestCase):
    def test_one_isolated_fixture_rehearsal_uses_aapl_first_two_sessions_and_no_repeat(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            work_root = Path(directory) / "fixture-root"
            evidence = run_clean_stage12c_rehearsal(
                project_root=PROJECT_ROOT,
                work_root=work_root,
            )
        rehearsal = evidence["fixture_rehearsal"]
        self.assertEqual(rehearsal["aapl_first"], True)  # type: ignore[index]
        self.assertEqual(rehearsal["expected_session_dates"], ["2026-08-13", "2026-08-14"])  # type: ignore[index]
        self.assertEqual(rehearsal["all_symbols_closed"], 629)  # type: ignore[index]
        self.assertEqual(rehearsal["published_complete_count"], 629)  # type: ignore[index]
        self.assertEqual(rehearsal["semantic_replay_requests"], 0)  # type: ignore[index]
        self.assertEqual(rehearsal["semantic_replay_zero_write"], True)  # type: ignore[index]
        self.assertEqual(rehearsal["two_sessions_per_request"], True)  # type: ignore[index]
        self.assertEqual(rehearsal["injected_transport_calls"], 629)  # type: ignore[index]
        self.assertGreaterEqual(rehearsal["virtual_pacing_seconds"], 628)  # type: ignore[index]

class Stage12CComparisonTests(unittest.TestCase):
    def test_two_clean_rehearsals_match_the_strict_path_free_golden(self) -> None:
        original_connect = sqlite3.connect
        opened_databases: list[str] = []

        def guarded_connect(
            database: object, *args: object, **kwargs: object
        ) -> sqlite3.Connection:
            database_text = str(database)
            opened_databases.append(database_text)
            for protected in (
                PROJECT_ROOT / "data" / "market.sqlite",
                PROJECT_ROOT / "data" / "market_data.sqlite",
                Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"),
            ):
                self.assertNotIn(str(protected), database_text)
            return original_connect(database, *args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "first"
            second_root = temporary_root / "second"
            with (
                patch.object(sqlite3, "connect", side_effect=guarded_connect),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("Stage 12C rehearsal must not use a socket"),
                ),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("Stage 12C rehearsal must not use a socket"),
                ),
                patch.object(
                    subprocess,
                    "Popen",
                    side_effect=AssertionError("Stage 12C rehearsal must not start a subprocess"),
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=AssertionError("Stage 12C rehearsal must not start a subprocess"),
                ),
                patch.object(
                    os,
                    "getenv",
                    side_effect=AssertionError("Stage 12C rehearsal must not read process environment"),
                ),
            ):
                evidence = compare_clean_stage12c_rebuilds(
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
        expected = loads_strict(GOLDEN_PATH.read_bytes())
        self.assertIsInstance(expected, dict)
        self.assertEqual(dumps_strict(evidence), dumps_strict(expected))

    def test_compare_and_rehearsal_reject_same_alias_and_protected_roots(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            same_root = temporary_root / "same"
            with self.assertRaises(ConflictError):
                compare_clean_stage12c_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=same_root,
                    second_work_root=same_root,
                )
            self.assertFalse(same_root.exists())

            physical_root = temporary_root / "physical"
            physical_root.mkdir()
            alias_root = temporary_root / "alias"
            alias_root.symlink_to(physical_root, target_is_directory=True)
            with self.assertRaises(ConflictError):
                compare_clean_stage12c_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=physical_root,
                    second_work_root=alias_root,
                )
            self.assertTrue(alias_root.is_symlink())
            self.assertEqual(tuple(physical_root.iterdir()), ())

        for protected_root in (
            PROJECT_ROOT,
            PROJECT_ROOT / "data" / "market.sqlite",
            Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"),
        ):
            with self.assertRaises(ConflictError):
                run_clean_stage12c_rehearsal(
                    project_root=PROJECT_ROOT,
                    work_root=protected_root,
                )


class Stage12CRestrictedCliTests(unittest.TestCase):
    def test_cli_only_dispatches_the_offline_compare_and_rejects_unsafe_roots(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            temporary_root = Path(directory)
            first_root = temporary_root / "first"
            second_root = temporary_root / "second"
            fixture_evidence = {"contract": "stage12c-offline-fixture"}
            with patch(
                "quant_data.__main__.compare_clean_stage12c_rebuilds",
                return_value=fixture_evidence,
            ) as compare:
                status, stdout, stderr = _cli(
                    "--stage",
                    "stage12c",
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--store-root",
                    str(first_root),
                    "--second-store-root",
                    str(second_root),
                )
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(loads_strict(stdout), fixture_evidence)
            compare.assert_called_once_with(
                project_root=str(PROJECT_ROOT),
                first_work_root=str(first_root),
                second_work_root=str(second_root),
            )
            self.assertNotIn("run_stage12c_market_gap_live", main.__globals__)

            status, stdout, stderr = _cli(
                "--stage",
                "stage12c",
                "--project-root",
                str(PROJECT_ROOT),
                "--store-root",
                str(temporary_root / "missing"),
            )
            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(loads_strict(stderr)["error"]["code"], "invalid_request")  # type: ignore[index]

            for unsafe_root in (
                PROJECT_ROOT,
                PROJECT_ROOT / "data" / "market.sqlite",
                Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"),
            ):
                status, stdout, stderr = _cli(
                    "--stage",
                    "stage12c",
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--store-root",
                    str(unsafe_root),
                    "--second-store-root",
                    str(temporary_root / "safe"),
                )
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertEqual(loads_strict(stderr)["error"]["code"], "conflict")  # type: ignore[index]
if __name__ == "__main__":
    unittest.main()
