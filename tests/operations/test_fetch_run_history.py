from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from quant_data.operations import fetch_run_history as history
from quant_data.inspector_schedules import TIMER_BINDINGS

ROOT = Path(__file__).resolve().parents[2]
BATCH = "quant-data-fmp-macro-calendar.timer"
START = datetime(2026, 9, 4, 12, 15, 12, tzinfo=timezone.utc)
FINISH = datetime(2026, 9, 4, 12, 15, 24, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)
FIRST = datetime(2026, 9, 1, 4, tzinfo=timezone.utc)
LAST = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)


class FetchRunHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fetch-history-test-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "history"

    def invoke(self, operation, **kwargs):
        return history.run_recorded_cli(BATCH, operation, argv=(), root=self.root,
            clock=Mock(side_effect=[START, FINISH]), environment={"INVOCATION_ID": "a" * 32}, **kwargs)

    def read(self, **kwargs):
        return history.read_fetch_run_history(self.root, start=FIRST, end=LAST,
            observed_at=kwargs.get("now", NOW))

    def files(self):
        return sorted(self.root.glob("*/*/run.json"))

    def test_success_failure_and_stdout_are_preserved_across_restart(self):
        for code in (0, 75, None):
            def operation():
                print("original output with private token")
                print("original error", file=sys.stderr)
                return code
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertIs(self.invoke(operation), code)
            self.assertEqual(output.getvalue(), "original output with private token\n")
            self.assertEqual(error.getvalue(), "original error\n")
        with patch.object(history, "_process_identity", return_value=None):
            rows = self.read()["records"]
        self.assertEqual(sorted(row["outcome"] for row in rows), ["failed", "succeeded", "succeeded"])
        self.assertEqual(sorted(row["exit_code"] for row in rows), [0, 0, 75])
        for path in self.files():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("private token", path.read_text())
            self.assertEqual(json.loads(path.read_text())["finished_at"], "2026-09-04T12:15:24Z")

    def test_exceptions_and_system_exit_keep_the_original_exception(self):
        for exception, code in ((SystemExit("private secret"), 1), (SystemExit(75), 75),
                                (RuntimeError("private secret"), 1), (KeyboardInterrupt(), 130)):
            operation = Mock(side_effect=exception)
            with self.assertRaises(type(exception)) as caught:
                self.invoke(operation)
            self.assertIs(caught.exception, exception)
            operation.assert_called_once_with()
        self.assertEqual(sorted(row["exit_code"] for row in self.read()["records"]), [1, 1, 75, 130])
        self.assertTrue(all("private secret" not in path.read_text() for path in self.files()))

    def test_help_invalid_arguments_and_backfill_never_create_history(self):
        for argv in (("--help",), ("--bad",), ("--mode", "backfill")):
            operation = Mock(return_value=64)
            self.assertEqual(history.run_recorded_cli(BATCH, operation, argv=argv, root=self.root), 64)
            operation.assert_called_once_with()
            self.assertFalse(self.root.exists())
        self.assertFalse(self.read()["available"])
        self.assertFalse(self.root.exists())

    def test_telemetry_failure_never_replaces_fetch_result_or_leaks_error(self):
        operation = Mock(return_value=75)
        error = io.StringIO()
        with patch.object(history, "_publish", side_effect=OSError("password=/private/file")), redirect_stderr(error):
            self.assertEqual(self.invoke(operation), 75)
        operation.assert_called_once_with()
        self.assertNotIn("password", error.getvalue())
        self.assertNotIn("/private/file", error.getvalue())
        self.assertIn("Unable to save run status", error.getvalue())

    def test_running_requires_the_same_live_process_and_complete_time_respects_cutoff(self):
        def operation():
            rows = self.read(now=START)["records"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "running")
            with patch.object(history, "_process_identity", return_value=None):
                self.assertEqual(self.read(now=START)["records"][0]["outcome"], "unconfirmed")
            return 0
        self.invoke(operation)
        self.assertEqual(self.read(now=START)["records"], [])
        self.assertEqual(self.read()["records"][0]["outcome"], "succeeded")

    def test_abruptly_killed_process_cannot_remain_running(self):
        source = 'import time; from pathlib import Path; from quant_data.operations.fetch_run_history import run_recorded_cli; run_recorded_cli(' + repr(BATCH) + ', lambda: time.sleep(30), argv=(), root=Path(' + repr(str(self.root)) + '), environment={})'
        process = subprocess.Popen([sys.executable, "-c", source], cwd=ROOT,
            env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(200):
                if self.files():
                    break
                self.assertIsNone(process.poll())
                time.sleep(0.01)
            self.assertTrue(self.files())
            process.kill()
            process.wait(timeout=3)
            now = datetime.now(timezone.utc)
            row = json.loads(self.files()[0].read_text())
            start = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            from datetime import timedelta
            result = history.read_fetch_run_history(self.root, start=start - timedelta(seconds=1),
                end=now + timedelta(seconds=1), observed_at=now)
            self.assertEqual(result["records"][0]["outcome"], "unconfirmed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def test_concurrent_runs_are_separate_and_reader_budget_is_explicit(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda code: self.invoke(lambda: code), (0, 64, 75)))
        self.assertEqual(results, [0, 64, 75])
        self.assertEqual(len(self.files()), 3)
        self.assertEqual(len({row["run_id"] for row in self.read()["records"]}), 3)
        with patch.object(history, "_MAX_RUNS", 1):
            result = self.read()
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["records"]), 1)

    def test_invalid_and_symlink_receipts_are_ignored_without_following_them(self):
        self.invoke(lambda: 0)
        path = self.files()[0]
        valid = path.read_text()
        for changes in ({"exit_code": 75}, {"batch_id": "other.timer"}, {"version": True},
                        {"started_at": "2026-08-04T12:15:12Z"}, {"url": "https://private"}):
            row = json.loads(valid)
            row.update(changes)
            path.write_text(json.dumps(row))
            self.assertEqual(self.read()["records"], [], changes)
            self.assertEqual(self.read()["invalid"], 1)
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text(valid)
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(self.read()["records"], [])
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(outside.parent, target_is_directory=True)
        self.assertEqual(self.read()["records"], [])
        self.assertEqual(outside.read_text(), valid)

    def test_directory_replacement_cannot_redirect_receipt_reads(self):
        self.invoke(lambda: 0)
        path = self.files()[0]
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "run.json").write_text(path.read_text())
        actual = history.os.open
        raced = False
        def replace_run_directory(name, flags, *args, **kwargs):
            nonlocal raced
            if name == path.parent.name and not raced:
                raced = True
                path.parent.rename(path.parent.with_name("moved-original"))
                path.parent.symlink_to(outside, target_is_directory=True)
            return actual(name, flags, *args, **kwargs)
        with patch.object(history.os, "open", side_effect=replace_run_directory):
            result = self.read()
        self.assertTrue(raced)
        self.assertEqual(result["records"], [])
        self.assertGreaterEqual(result["invalid"], 1)

    def test_malformed_bytes_count_toward_total_read_budget(self):
        for _ in range(3):
            self.invoke(lambda: 0)
        for path in self.files():
            path.write_bytes(b"!" * 4096)
        with patch.object(history, "_MAX_TOTAL_BYTES", 8193):
            result = self.read()
        self.assertTrue(result["truncated"])
        self.assertEqual(result["invalid"], 2)
        self.assertEqual(result["records"], [])

    def test_utc_month_storage_is_read_for_the_full_eastern_month(self):
        started = datetime(2026, 10, 1, 3, 15, tzinfo=timezone.utc)
        completed = datetime(2026, 10, 1, 3, 16, tzinfo=timezone.utc)
        history.run_recorded_cli(BATCH, lambda: 0, argv=(), root=self.root,
            clock=Mock(side_effect=[started, completed]), environment={})
        result = self.read(now=completed)
        self.assertEqual(len(result["records"]), 1)
        self.assertTrue(self.files()[0].parent.parent.name == "2026-10")

    def test_all_ten_deployed_entrypoints_record_exact_original_cli_calls(self):
        self.assertEqual(set(history.BATCH_ARGUMENTS), {binding.unit for binding in TIMER_BINDINGS})
        actual = history.run_recorded_cli
        for batch, expected_args in history.BATCH_ARGUMENTS.items():
            service = ROOT / "deploy/systemd" / batch.replace(".timer", ".service")
            command = next(line.split("=", 1)[1] for line in service.read_text().splitlines() if line.startswith("ExecStart="))
            args = shlex.split(command)
            self.assertEqual(args[:2], ["/usr/bin/python3", "-m"])
            self.assertEqual(tuple(args[3:]), expected_args)
            module = ROOT / (args[2].replace(".", "/") + ".py")
            tree = ast.parse(module.read_text())
            entrypoint = next(node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")
            operation = Mock(return_value=75)
            def instrument(identity, callback, *, argv):
                self.assertEqual(identity, batch)
                return actual(identity, callback, argv=argv, root=self.root,
                    clock=Mock(side_effect=[START, FINISH]), environment={})
            namespace = {"__name__": "__main__", "__package__": "quant_data.operations", "main": operation, "sys": sys}
            with patch.object(history, "run_recorded_cli", side_effect=instrument), patch.object(sys, "argv", [str(module), *expected_args]):
                with self.assertRaises(SystemExit) as caught:
                    exec(compile(ast.Module(body=[entrypoint], type_ignores=[]), str(module), "exec"), namespace)
            self.assertEqual(caught.exception.code, 75)
            if batch == "quant-data-sec-company-fundamentals.timer":
                operation.assert_called_once_with(list(expected_args))
            else:
                operation.assert_called_once_with()
        self.assertEqual({row["batch_id"] for row in self.read()["records"]}, set(history.BATCH_ARGUMENTS))


if __name__ == "__main__":
    unittest.main()
