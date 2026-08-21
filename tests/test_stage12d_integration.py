from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import inspect
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from quant_data.__main__ import main
from quant_data.errors import ConflictError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.operations import stage12d_market_proof as proof_module
from quant_data.stage12d import (
    STAGE12D_GATE_CONTRACT,
    STAGE12D_GATE_VERSION,
    STAGE12D_REGISTRY_SCHEMA_VERSION,
    STAGE12D_REGISTRY_SOURCE_SHA256,
    STAGE12D_REGISTRY_VERSION,
    compare_clean_stage12d_rebuilds,
    validate_stage12d_static_preflight,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ExplodingEnvironment(dict[str, str]):
    def get(self, key: str, default: object = None) -> object:
        raise AssertionError("Stage 12D static preflight must not read an environment")

    def __getitem__(self, key: str) -> str:
        raise AssertionError("Stage 12D static preflight must not read an environment")


class Stage12DStaticPreflightTests(unittest.TestCase):
    def test_static_preflight_is_source_only_path_free_and_binds_stage12c(self) -> None:
        with (
            patch.object(
                sqlite3,
                "connect",
                side_effect=AssertionError("Stage 12D static preflight must not open SQLite"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("Stage 12D static preflight must not use a socket"),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("Stage 12D static preflight must not use a socket"),
            ),
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("Stage 12D static preflight must not start a subprocess"),
            ),
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("Stage 12D static preflight must not start a subprocess"),
            ),
            patch.object(
                os,
                "getenv",
                side_effect=AssertionError("Stage 12D static preflight must not read an environment"),
            ),
            patch.object(
                Path,
                "mkdir",
                side_effect=AssertionError("Stage 12D static preflight must not create a path"),
            ),
            patch.object(
                Path,
                "write_bytes",
                side_effect=AssertionError("Stage 12D static preflight must not write a path"),
            ),
            patch.object(
                Path,
                "write_text",
                side_effect=AssertionError("Stage 12D static preflight must not write a path"),
            ),
            patch.object(
                Path,
                "touch",
                side_effect=AssertionError("Stage 12D static preflight must not write a path"),
            ),
        ):
            evidence = validate_stage12d_static_preflight(PROJECT_ROOT)

        self.assertEqual(
            evidence["registry"],  # type: ignore[index]
            {
                "revision": STAGE12D_REGISTRY_VERSION,
                "schema": STAGE12D_REGISTRY_SCHEMA_VERSION,
                "source_sha256": STAGE12D_REGISTRY_SOURCE_SHA256,
            },
        )
        self.assertEqual(evidence["scope"]["contract"], "quant_data.stage12d_market_no_transfer_adoption_v1")  # type: ignore[index]
        self.assertEqual(evidence["stage12c_binding"]["plan_sha256"], "5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1")  # type: ignore[index]
        self.assertEqual(evidence["stage12c_binding"]["scope_semantic_sha256"], "2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392")  # type: ignore[index]
        self.assertEqual(evidence["stage12c_binding"]["ledger"], {  # type: ignore[index]
            "authorized_http_402": 7,
            "closed": 629,
            "published_complete": 619,
            "successful_empty": 3,
        })
        self.assertEqual(evidence["target_policy"], {  # type: ignore[index]
            "direct_regular_file": True,
            "immutable_uri_query": "mode=ro&immutable=1",
            "non_symlink": True,
            "project_relative_path": "data/market.sqlite",
            "query_only": True,
            "required_link_count": 1,
            "wal_and_rollback_journal": "absent_or_zero",
        })
        self.assertEqual(evidence["zero_effects"], {  # type: ignore[index]
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "network_calls": 0,
            "sqlite_opens": 0,
            "subprocess_calls": 0,
        })
        serialized = dumps_strict(evidence)
        self.assertNotIn(str(PROJECT_ROOT), serialized)
        self.assertNotIn("/home/volatility/quant-data-nonprod", serialized)


_GOLDEN_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage12d_golden.json"


class Stage12DOfflineIntegrationTests(unittest.TestCase):
    def _compare_two_roots(self) -> dict[str, object]:
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as first_root,
            tempfile.TemporaryDirectory(dir="/tmp") as second_root,
        ):
            with (
                patch.object(
                    os,
                    "getenv",
                    side_effect=AssertionError("Stage 12D fixture gate must not read an environment"),
                ),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("Stage 12D fixture gate must not use a socket"),
                ),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("Stage 12D fixture gate must not use a socket"),
                ),
                patch.object(
                    subprocess,
                    "Popen",
                    side_effect=AssertionError("Stage 12D fixture gate must not start a subprocess"),
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=AssertionError("Stage 12D fixture gate must not start a subprocess"),
                ),
            ):
                return compare_clean_stage12d_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=first_root,
                    second_work_root=second_root,
                )

    def test_two_root_gate_matches_path_free_golden_and_isolated_proof_contract(self) -> None:
        evidence = self._compare_two_roots()
        golden = loads_strict(_GOLDEN_PATH.read_bytes())
        self.assertEqual(evidence, golden)
        self.assertEqual(evidence["contract"], STAGE12D_GATE_CONTRACT)
        self.assertEqual(evidence["version"], STAGE12D_GATE_VERSION)
        fixture = evidence["fixture"]
        self.assertEqual(
            fixture,
            {
                "cross_root_completion_receipts_distinct": True,
                "cross_root_semantic_proof_equal": True,
                "cross_root_target_identities_distinct": True,
                "immutable_uri_query": "mode=ro&immutable=1",
                "migration_head": "market:0010_stage10_market_history",
                "proof_ordinals": [1, 2],
                "query_only": True,
                "semantic_proof_sha256": "87943bdc3cd4baea033110ddf61ff66bff4e238a9e91814bf5046f3cb6e677a2",
                "serial_receipts_distinct": True,
                "target_sidecar_stamps_unchanged": True,
                "third_proof_rejected": True,
            },
        )
        serialized = dumps_strict(evidence)
        for forbidden in (
            str(PROJECT_ROOT),
            "/tmp/",
            "inode",
            "mtime",
            "FMP_API_KEY",
            "secret",
            "2026-08-16T",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_cli_requires_two_roots_and_only_dispatches_the_offline_compare(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                (
                    "--stage",
                    "stage12d",
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--store-root",
                    "/tmp/stage12d-cli-missing-second-root",
                )
            )
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Stage 12D requires an explicit --second-store-root", stderr.getvalue())

        with (
            tempfile.TemporaryDirectory(dir="/tmp") as first_root,
            tempfile.TemporaryDirectory(dir="/tmp") as second_root,
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(
                    (
                        "--stage",
                        "stage12d",
                        "--project-root",
                        str(PROJECT_ROOT),
                        "--store-root",
                        first_root,
                        "--second-store-root",
                        second_root,
                    )
                )
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(loads_strict(stdout.getvalue().encode("utf-8")), loads_strict(_GOLDEN_PATH.read_bytes()))

    def test_fixture_roots_reject_same_nonempty_alias_and_canonical_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as first_root:
            with self.assertRaises(ConflictError):
                compare_clean_stage12d_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=first_root,
                    second_work_root=first_root,
                )

        with (
            tempfile.TemporaryDirectory(dir="/tmp") as first_root,
            tempfile.TemporaryDirectory(dir="/tmp") as second_root,
        ):
            (Path(first_root) / "occupied").mkdir()
            with self.assertRaises(ConflictError):
                compare_clean_stage12d_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=first_root,
                    second_work_root=second_root,
                )

        with (
            tempfile.TemporaryDirectory(dir="/tmp") as target_root,
            tempfile.TemporaryDirectory(dir="/tmp") as alias_parent,
            tempfile.TemporaryDirectory(dir="/tmp") as second_root,
        ):
            alias = Path(alias_parent) / "alias"
            os.symlink(target_root, alias)
            with self.assertRaises(ConflictError):
                compare_clean_stage12d_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=alias,
                    second_work_root=second_root,
                )

        with tempfile.TemporaryDirectory(dir="/tmp") as second_root:
            with self.assertRaises(ConflictError):
                compare_clean_stage12d_rebuilds(
                    project_root=PROJECT_ROOT,
                    first_work_root=PROJECT_ROOT,
                    second_work_root=second_root,
                )

    def test_proof_implementation_has_only_immutable_descriptor_reader_surface(self) -> None:
        source = inspect.getsource(proof_module)
        self.assertIn("file:/proc/self/fd/", source)
        self.assertIn("mode=ro&immutable=1", source)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', source)
        self.assertNotIn("read_connection(", source)
        self.assertNotIn("StoreWriteLock(", source)



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
