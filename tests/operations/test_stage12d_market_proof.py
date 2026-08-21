from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import inspect
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import threading
import unittest
import uuid
from unittest import mock

from quant_data.errors import ConflictError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage12d_scope import load_stage12d_market_no_transfer_adoption_scope
from quant_data.operations import stage12d_market_proof as operations


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_SCOPE_SHA256 = "c" * 64
_FIXTURE_PLAN_SHA256 = "d" * 64


class Stage12DMarketProofTests(unittest.TestCase):
    """Every proof here uses a new owned fixture root directly under /tmp."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        self.target = self.root / "data" / "market.sqlite"
        self.scope = load_stage12d_market_no_transfer_adoption_scope()
        self._seed_target()
        self.expectations = operations.Stage12DFixtureExpectations(
            fixture_id="fixture-stage12d-expectations",
            current_rows=2,
            version_rows=2,
            captures=1,
            stage12c_current_rows=2,
            stage12c_version_rows=2,
            stage12c_captures=1,
            aapl_current_rows=2,
        )
        self.completion = self._write_completion()
        self.clock = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_target(self) -> None:
        with sqlite3.connect(self.target) as connection:
            connection.executescript(
                """
                CREATE TABLE stage10_instruments(
                    instrument_id TEXT PRIMARY KEY,
                    provider_symbol TEXT NOT NULL
                );
                CREATE TABLE stage10_daily_price_captures(
                    capture_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    scope_manifest_sha256 TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL
                );
                CREATE TABLE stage10_daily_price_versions(
                    version_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    price_variant TEXT NOT NULL,
                    currency_segment TEXT NOT NULL,
                    correction_sequence INTEGER NOT NULL,
                    supersedes_version_id TEXT,
                    capture_id TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE TABLE stage10_daily_prices(
                    instrument_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    price_variant TEXT NOT NULL,
                    currency_segment TEXT NOT NULL,
                    current_version_id TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO stage10_instruments VALUES (?, ?)", ("fixture-aapl", "AAPL")
            )
            connection.execute(
                "INSERT INTO stage10_daily_price_captures VALUES (?, ?, ?, ?, ?)",
                ("fixture-capture", "fixture-aapl", "AAPL", _FIXTURE_SCOPE_SHA256, "e" * 64),
            )
            for number, trade_date in enumerate(("2026-08-13", "2026-08-14"), start=1):
                version_id = f"fixture-version-{number}"
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_versions VALUES (
                        ?, ?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native', 1,
                        NULL, 'fixture-capture', '2026-08-16T12:00:00Z',
                        '2026-08-16T12:00:00Z'
                    )
                    """,
                    (version_id, "fixture-aapl", trade_date),
                )
                connection.execute(
                    "INSERT INTO stage10_daily_prices VALUES (?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native', ?)",
                    ("fixture-aapl", trade_date, version_id),
                )
        os.chmod(self.target, 0o600)

    def _insert_scoped_symbol(
        self,
        symbol: str,
        token: str,
        *,
        scope_sha256: str = _FIXTURE_SCOPE_SHA256,
        include_versions: bool = True,
    ) -> None:
        instrument_id = f"fixture-{token}"
        capture_id = f"fixture-capture-{token}"
        with sqlite3.connect(self.target) as connection:
            connection.execute(
                "INSERT INTO stage10_instruments VALUES (?, ?)", (instrument_id, symbol)
            )
            connection.execute(
                "INSERT INTO stage10_daily_price_captures VALUES (?, ?, ?, ?, ?)",
                (capture_id, instrument_id, symbol, scope_sha256, f"{token[0]}" * 64),
            )
            if not include_versions:
                return
            for number, trade_date in enumerate(("2026-08-13", "2026-08-14"), start=1):
                version_id = f"fixture-version-{token}-{number}"
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_versions VALUES (
                        ?, ?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native', 1,
                        NULL, ?, '2026-08-16T12:00:00Z', '2026-08-16T12:00:00Z'
                    )
                    """,
                    (version_id, instrument_id, trade_date, capture_id),
                )
                connection.execute(
                    "INSERT INTO stage10_daily_prices VALUES (?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native', ?)",
                    (instrument_id, trade_date, version_id),
                )
        os.chmod(self.target, 0o600)

    def _completion_path(self) -> Path:
        return self.root / self.scope.stage12c.completion_receipt_path

    def _write_completion(
        self,
        *,
        expectations: operations.Stage12DFixtureExpectations | None = None,
        terminal_outcomes: tuple[operations.Stage12DFixtureTerminalOutcome, ...] = (),
        fixture_id: str = "fixture-stage12d-completion",
    ) -> operations.Stage12DFixtureCompletion:
        expectations = self.expectations if expectations is None else expectations
        path = self._completion_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = operations._regular_stamp(
            self.target,
            "fixture market target",
            allow_absent=False,
            require_single_link=True,
        )
        empty = sum(item.outcome == "noncoverage_empty" for item in terminal_outcomes)
        authorized = sum(
            item.outcome == "noncoverage_http_402_authorized" for item in terminal_outcomes
        )
        closed = expectations.stage12c_captures + len(terminal_outcomes)
        final_target = {
            "byte_count": stamp.size,
            "identity_sha256": stamp.identity_sha256(),
            "mtime_ns": stamp.mtime_ns,
            "sha256": "f" * 64,
        }
        business_material: dict[str, object] = {
            "baseline_sha256": operations._fixture_completion_authority_sha256(fixture_id),
            "final_target": final_target,
            "final_target_checks": {
                "current_price_rows": expectations.current_rows,
                "daily_price_captures": expectations.captures,
                "foreign_key_violation_count": expectations.foreign_key_violations,
                "immutable_price_versions": expectations.version_rows,
                "integrity_check": "ok",
                "scoped_current_row_count": expectations.stage12c_current_rows,
                "stage10_capture_duplicate_count": expectations.duplicate_captures,
                "stage10_current_duplicate_count": expectations.duplicate_current,
                "stage10_current_pointer_anomaly_count": expectations.pointer_anomalies,
                "stage10_version_duplicate_count": expectations.duplicate_versions,
            },
            "plan_sha256": _FIXTURE_PLAN_SHA256,
            "published_unit_count": expectations.stage12c_captures,
            "result_sha256s": ["1" * 64 for _ in range(closed)],
            "scope_manifest_sha256": _FIXTURE_SCOPE_SHA256,
            "source_final": {
                "byte_count": 1,
                "identity_sha256": "2" * 64,
                "mtime_ns": 1,
                "sha256": "3" * 64,
            },
            "static_preflight_sha256": "4" * 64,
            "terminal_noncoverage_count": len(terminal_outcomes),
            "terminal_outcomes": [item.to_primitive() for item in terminal_outcomes],
            "total_attempt_count": closed,
            "version": "1.0.0",
        }
        receipt = {
            "contract": "quant_data.stage12c_market_gap_completion",
            **business_material,
            "sha256": operations._sha256_json(business_material),
        }
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC,
            0o600,
        )
        try:
            payload = dumps_strict(receipt).encode("utf-8")
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(path, 0o600)
        return operations.Stage12DFixtureCompletion(
            fixture_id=fixture_id,
            receipt_sha256=str(receipt["sha256"]),
            plan_sha256=_FIXTURE_PLAN_SHA256,
            scope_semantic_sha256=_FIXTURE_SCOPE_SHA256,
            published_complete=expectations.stage12c_captures,
            successful_empty=empty,
            authorized_http_402=authorized,
            closed=closed,
            terminal_outcomes=terminal_outcomes,
        )

    def _runner(
        self,
        *,
        expectations: operations.Stage12DFixtureExpectations | None = None,
        completion: operations.Stage12DFixtureCompletion | None = None,
        hooks: operations.Stage12DFixtureHooks | None = None,
    ) -> operations.Stage12DMarketProofRunner:
        return operations.Stage12DMarketProofRunner(
            project_root=self.root,
            scope=self.scope,
            expectations=self.expectations if expectations is None else expectations,
            completion=self.completion if completion is None else completion,
            utcnow=lambda: self.clock,
            hooks=hooks,
        )

    def _receipt_root(self) -> Path:
        return self.root / self.scope.receipt_policy.private_receipt_root

    def _proof_path(self, ordinal: int) -> Path:
        matches = tuple(self._receipt_root().glob(f"proof-{ordinal}-*.json"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _stamps(self) -> dict[str, operations.Stage12DFileStamp]:
        return operations._sidecar_stamps(self.target)

    def _replacement(self) -> None:
        replacement = self.root / "replacement.sqlite"
        shutil.copyfile(self.target, replacement)
        os.replace(replacement, self.target)

    def test_two_serial_proofs_are_stamp_neutral_and_semantically_equal(self) -> None:
        before = self._stamps()
        first = self._runner().run()
        self.clock += timedelta(seconds=1)
        second = self._runner().run()
        after = self._stamps()

        self.assertEqual(before, after)
        self.assertEqual((first.proof_ordinal, second.proof_ordinal), (1, 2))
        self.assertNotEqual(first.receipt_sha256, second.receipt_sha256)
        self.assertEqual(first.semantic_proof_sha256, second.semantic_proof_sha256)
        self.assertEqual(first.scope_manifest_sha256, self.scope.manifest_sha256)
        self.assertEqual(first.completion_receipt_sha256, self.completion.receipt_sha256)

        root = self._receipt_root()
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        first_paths = tuple(root.glob("proof-1-*.json"))
        second_paths = tuple(root.glob("proof-2-*.json"))
        self.assertEqual(len(first_paths), 1)
        self.assertEqual(len(second_paths), 1)
        first_path, second_path = first_paths[0], second_paths[0]
        self.assertEqual(
            {path.name for path in root.iterdir()},
            {first_path.name, second_path.name},
        )
        self.assertEqual(stat.S_IMODE(first_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(second_path.stat().st_mode), 0o600)
        first_receipt = loads_strict(first_path.read_bytes())
        second_receipt = loads_strict(second_path.read_bytes())
        self.assertEqual(first.receipt_sha256, first_receipt["sha256"])
        self.assertEqual(second.receipt_sha256, second_receipt["sha256"])
        self.assertNotEqual(first_receipt["invocation"], second_receipt["invocation"])
        self.assertEqual(
            first_receipt["semantic_proof_sha256"],
            second_receipt["semantic_proof_sha256"],
        )
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(self.target.with_name(self.target.name + suffix).exists())

    def test_third_proof_is_rejected_without_target_change(self) -> None:
        self._runner().run()
        self.clock += timedelta(seconds=1)
        self._runner().run()
        before = self._stamps()
        with self.assertRaises(ConflictError):
            self._runner().run()
        self.assertEqual(before, self._stamps())

    def test_receipt_directory_flock_rejects_concurrent_proof_two_then_releases(self) -> None:
        self._runner().run()
        before = self._stamps()
        root = self._receipt_root()
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        started = threading.Event()
        finished = threading.Event()
        failures: list[BaseException] = []

        def concurrent_proof_two() -> None:
            started.set()
            try:
                self._runner().run()
            except BaseException as exc:
                failures.append(exc)
            finally:
                finished.set()

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            thread = threading.Thread(target=concurrent_proof_two)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertTrue(finished.wait(5))
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], ConflictError)
            self.assertEqual(tuple(root.glob("proof-2-*.json")), ())
            self.assertEqual(before, self._stamps())
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        self.clock += timedelta(seconds=1)
        second = self._runner().run()
        self.assertEqual(second.proof_ordinal, 2)
        self.assertEqual(before, self._stamps())

    def test_same_fixture_authority_normalizes_root_specific_completion_identity(self) -> None:
        first = self._runner().run()
        other = self.__class__(methodName="runTest")
        other.setUp()
        try:
            second = other._runner().run()
            self.assertNotEqual(
                first.completion_receipt_sha256,
                second.completion_receipt_sha256,
            )
            self.assertNotEqual(first.target_identity_sha256, second.target_identity_sha256)
            self.assertEqual(first.semantic_proof_sha256, second.semantic_proof_sha256)
        finally:
            other.tearDown()

    def test_descriptor_uri_and_query_only_are_required(self) -> None:
        original_connect = sqlite3.connect
        calls: list[tuple[object, dict[str, object]]] = []

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            calls.append((args[0], dict(kwargs)))
            return original_connect(*args, **kwargs)

        with mock.patch.object(operations.sqlite3, "connect", side_effect=traced_connect):
            self._runner().run()
        self.assertEqual(len(calls), 1)
        uri, options = calls[0]
        self.assertIsInstance(uri, str)
        self.assertTrue(str(uri).startswith("file:/proc/self/fd/"))
        self.assertTrue(str(uri).endswith("?mode=ro&immutable=1"))
        self.assertTrue(options["uri"])
        self.assertEqual(options["timeout"], 0.0)
        self.assertIsNone(options["isolation_level"])

    def test_nonzero_wal_or_journal_is_a_pre_sqlite_stop(self) -> None:
        for suffix in ("-wal", "-journal"):
            with self.subTest(suffix=suffix):
                path = self.target.with_name(self.target.name + suffix)
                path.write_bytes(b"x")
                with self.assertRaises(ConflictError):
                    self._runner().run()
                self.assertFalse((self._receipt_root() / "proof-1.json").exists())
                path.unlink()

    def test_target_replacement_is_rejected_at_each_proof_boundary(self) -> None:
        for hook_name in ("before_connect", "before_query", "before_receipt", "after_receipt"):
            with self.subTest(hook_name=hook_name):
                self._write_completion()
                hook = self._replacement if hook_name != "before_query" else lambda _: self._replacement()
                hooks = operations.Stage12DFixtureHooks(**{hook_name: hook})
                with self.assertRaises(ConflictError):
                    self._runner(hooks=hooks).run()
                self.assertFalse((self._receipt_root() / "proof-1.json").exists())
                self.completion = self._write_completion()

    def test_direct_file_and_fixture_root_boundaries_reject_aliases(self) -> None:
        hardlink = self.root / "market-hardlink.sqlite"
        os.link(self.target, hardlink)
        with self.assertRaises(ConflictError):
            self._runner().run()
        hardlink.unlink()

        target_copy = self.root / "target-copy.sqlite"
        shutil.copyfile(self.target, target_copy)
        self.target.unlink()
        os.symlink(target_copy, self.target)
        with self.assertRaises(ConflictError):
            self._runner().run()

    def test_former_default_root_alias_and_forged_scope_are_rejected(self) -> None:
        former_default = self.root / "data" / "market_data.sqlite"
        former_default.write_bytes(b"fixture")
        with self.assertRaises(ValidationError):
            self._runner()
        former_default.unlink()
        alias = self.root.parent / f"{self.root.name}-alias"
        os.symlink(self.root, alias)
        try:
            with self.assertRaises(ValidationError):
                operations.Stage12DMarketProofRunner(
                    project_root=alias,
                    scope=self.scope,
                    expectations=self.expectations,
                    completion=self.completion,
                )
        finally:
            alias.unlink()
        with self.assertRaises(ValidationError):
            operations.Stage12DMarketProofRunner(
                project_root=self.root,
                scope=replace(self.scope, manifest_sha256="0" * 64),
                expectations=self.expectations,
                completion=self.completion,
            )

    def test_completion_embedded_business_hash_rejects_file_and_legacy_digests(self) -> None:
        completion_path = self._completion_path()
        payload = completion_path.read_bytes()
        receipt = loads_strict(payload)
        business_material = {
            key: value
            for key, value in receipt.items()
            if key not in {"contract", "sha256"}
        }
        embedded_sha256 = operations._sha256_json(business_material)
        full_file_sha256 = hashlib.sha256(payload).hexdigest()
        legacy_sha256 = operations._sha256_json(
            {
                key: value
                for key, value in receipt.items()
                if key != "sha256"
            }
        )
        self.assertEqual(receipt["sha256"], embedded_sha256)
        self.assertNotEqual(full_file_sha256, embedded_sha256)
        self.assertNotEqual(legacy_sha256, embedded_sha256)

        accepted = self._runner().run()
        self.assertEqual(accepted.completion_receipt_sha256, embedded_sha256)

        with self.assertRaises(ConflictError):
            self._runner(
                completion=replace(self.completion, receipt_sha256=full_file_sha256)
            ).run()

        legacy_receipt = {**receipt, "sha256": legacy_sha256}
        completion_path.write_bytes(
            dumps_strict(legacy_receipt).encode("utf-8")
        )
        os.chmod(completion_path, 0o600)
        with self.assertRaises(ConflictError):
            self._runner(
                completion=replace(self.completion, receipt_sha256=legacy_sha256)
            ).run()
        self.assertEqual(tuple(self._receipt_root().glob("proof-2-*.json")), ())

    def test_forged_completion_cross_bound_receipt_and_count_are_rejected(self) -> None:
        completion_path = self._completion_path()
        forged = loads_strict(completion_path.read_bytes())
        forged["plan_sha256"] = "9" * 64
        completion_path.write_bytes(dumps_strict(forged).encode("utf-8"))
        os.chmod(completion_path, 0o600)
        with self.assertRaises(ConflictError):
            self._runner().run()

        self.completion = self._write_completion()
        wrong_completion = replace(self.completion, plan_sha256="8" * 64)
        with self.assertRaises(ConflictError):
            self._runner(completion=wrong_completion).run()
        wrong_fixture = replace(self.completion, fixture_id="fixture-forged-completion")
        with self.assertRaises(ConflictError):
            self._runner(completion=wrong_fixture).run()
        wrong_receipt_sha = replace(self.completion, receipt_sha256="7" * 64)
        with self.assertRaises(ConflictError):
            self._runner(completion=wrong_receipt_sha).run()
        wrong_expectations = replace(self.expectations, current_rows=3)
        with self.assertRaises(ConflictError):
            self._runner(expectations=wrong_expectations).run()

    def test_existing_unsafe_or_cross_bound_receipt_blocks_the_sequence(self) -> None:
        root = self._receipt_root()
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        proof = root / "proof-1.json"
        proof.write_text("{}", encoding="utf-8")
        os.chmod(proof, 0o600)
        with self.assertRaises(ConflictError):
            self._runner().run()

    def test_partial_receipt_write_rolls_back_the_exact_created_inode(self) -> None:
        writes = 0

        def partial_then_fail(descriptor: int, payload: bytes) -> int:
            nonlocal writes
            if writes == 0:
                writes += 1
                return os.write(descriptor, payload[:8])
            raise OSError("fixture partial write failure")

        with mock.patch.object(operations, "_write_receipt_chunk", side_effect=partial_then_fail):
            with self.assertRaises(OSError):
                self._runner().run()
        self.assertEqual(tuple(self._receipt_root().iterdir()), ())

    def test_fixed_invocation_identity_rejects_proof_two_without_creating_it(self) -> None:
        fixed = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")
        with mock.patch.object(operations.uuid, "uuid4", return_value=fixed):
            self._runner().run()
            with self.assertRaises(ConflictError):
                self._runner().run()
        self.assertEqual(len(tuple(self._receipt_root().glob("proof-1-*.json"))), 1)
        self.assertEqual(tuple(self._receipt_root().glob("proof-2-*.json")), ())

    def test_completion_and_prior_receipt_drift_are_rejected_and_do_not_advance(self) -> None:
        completion_path = self._completion_path()

        def replace_completion() -> None:
            replacement = completion_path.with_name("completion-replacement.json")
            replacement.write_bytes(b"{}")
            os.chmod(replacement, 0o600)
            os.replace(replacement, completion_path)

        with self.assertRaises(ConflictError):
            self._runner(
                hooks=operations.Stage12DFixtureHooks(before_connect=replace_completion)
            ).run()
        self.assertEqual(tuple(self._receipt_root().glob("proof-1-*.json")), ())

        self.completion = self._write_completion()
        self._runner().run()

        def overwrite_prior(_: str) -> None:
            proof = self._proof_path(1)
            proof.write_bytes(b"{}")
            os.chmod(proof, 0o600)

        with self.assertRaises(ConflictError):
            self._runner(
                hooks=operations.Stage12DFixtureHooks(before_query=overwrite_prior)
            ).run()
        self.assertEqual(tuple(self._receipt_root().glob("proof-2-*.json")), ())

    def test_unsafe_or_self_consistent_forged_prior_receipts_are_rejected(self) -> None:
        self._runner().run()
        proof = self._proof_path(1)
        os.chmod(proof, 0o644)
        with self.assertRaises(ConflictError):
            self._runner().run()
        self.assertEqual(tuple(self._receipt_root().glob("proof-2-*.json")), ())

    def test_self_consistent_forged_prior_stamps_are_rejected_against_current_target(self) -> None:
        self._runner().run()
        proof = self._proof_path(1)
        receipt = loads_strict(proof.read_bytes())
        for key in ("pre_stamp", "post_stamp"):
            receipt["target"][key]["inode"] += 1
        material = {key: value for key, value in receipt.items() if key != "sha256"}
        receipt["sha256"] = operations._sha256_json(material)
        proof.write_bytes(dumps_strict(receipt).encode("utf-8"))
        os.chmod(proof, 0o600)
        with self.assertRaises(ConflictError):
            self._runner().run()
        self.assertEqual(tuple(self._receipt_root().glob("proof-2-*.json")), ())

    def test_receipt_root_replacement_after_write_rolls_back_via_original_dirfd(self) -> None:
        original = self._receipt_root()
        moved = self.root / "moved-stage12d-receipts"

        def replace_root() -> None:
            os.rename(original, moved)
            original.mkdir(mode=0o700)
            os.chmod(original, 0o700)

        with self.assertRaises(ConflictError):
            self._runner(
                hooks=operations.Stage12DFixtureHooks(after_receipt=replace_root)
            ).run()
        self.assertEqual(tuple(original.iterdir()), ())
        self.assertEqual(tuple(moved.iterdir()), ())

    def test_terminal_fact_cannot_substitute_for_a_missing_published_symbol(self) -> None:
        expectations = operations.Stage12DFixtureExpectations(
            fixture_id="fixture-stage12d-two-symbols",
            current_rows=4,
            version_rows=4,
            captures=2,
            stage12c_current_rows=4,
            stage12c_version_rows=4,
            stage12c_captures=2,
            aapl_current_rows=2,
            published_symbols=("AAPL", "MSFT"),
        )
        terminal = (
            operations.Stage12DFixtureTerminalOutcome(
                ordinal=3,
                symbol="TSLA",
                outcome="noncoverage_empty",
                http_status=200,
            ),
        )
        self._insert_scoped_symbol("TSLA", "tsla")
        completion = self._write_completion(
            expectations=expectations, terminal_outcomes=terminal
        )
        with self.assertRaises(ConflictError):
            self._runner(expectations=expectations, completion=completion).run()

    def test_terminal_capture_only_and_unscoped_aapl_do_not_pass_scoped_coverage(self) -> None:
        terminal = (
            operations.Stage12DFixtureTerminalOutcome(
                ordinal=2,
                symbol="TSLA",
                outcome="noncoverage_empty",
                http_status=200,
            ),
        )
        expectations = operations.Stage12DFixtureExpectations(
            fixture_id="fixture-stage12d-terminal-capture",
            current_rows=2,
            version_rows=2,
            captures=2,
            stage12c_current_rows=2,
            stage12c_version_rows=2,
            stage12c_captures=1,
            aapl_current_rows=2,
        )
        self._insert_scoped_symbol("TSLA", "terminal", include_versions=False)
        completion = self._write_completion(
            expectations=expectations, terminal_outcomes=terminal
        )
        with self.assertRaises(ConflictError):
            self._runner(expectations=expectations, completion=completion).run()

    def test_unscoped_baseline_aapl_rows_do_not_satisfy_scoped_aapl_proof(self) -> None:
        with sqlite3.connect(self.target) as connection:
            connection.execute(
                "UPDATE stage10_daily_price_captures SET scope_manifest_sha256=?",
                ("b" * 64,),
            )
        os.chmod(self.target, 0o600)
        self.completion = self._write_completion()
        with self.assertRaises(ConflictError):
            self._runner().run()

    def test_public_surface_is_fixed_and_does_not_expose_ambient_capabilities(self) -> None:
        signature = inspect.signature(operations.run_stage12d_market_no_transfer_proof)
        self.assertEqual(tuple(signature.parameters), ())
        runner_signature = inspect.signature(operations.Stage12DMarketProofRunner)
        self.assertNotIn("target", runner_signature.parameters)
        self.assertNotIn("sql", runner_signature.parameters)
        source = inspect.getsource(operations)
        for forbidden in ("subprocess", "requests", "urllib", "os.environ"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
