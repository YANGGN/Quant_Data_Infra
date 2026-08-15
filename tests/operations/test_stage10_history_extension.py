"""Focused offline checks for the Stage 10 history-extension journal skeleton."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest

from quant_data.errors import CapabilityUnavailableError, ConflictError
from quant_data.json_codec import loads_strict
from quant_data.market.stage10_history_windows import (
    STAGE10_HISTORY_WINDOWS,
    Stage10HistoryWindow,
)
from quant_data.operations.stage10_history_extension import (
    HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT,
    HISTORY_EXTENSION_EXPECTED_ROSTER_COUNT,
    HISTORY_EXTENSION_NAMESPACE,
    HISTORY_EXTENSION_WINDOWS,
    HistoryExtensionIntent,
    HistoryExtensionResultDraft,
    VerifiedStage10Base,
    build_history_extension_plan,
    main,
    run_offline_history_extension_rehearsal,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class Stage10HistoryExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.target = self.root / "stage10-target"
        self.target.mkdir(mode=0o700)
        (self.target / "private").mkdir(mode=0o700)
        (self.target / "private" / "candidate").mkdir(mode=0o700)
        os.chmod(self.target, 0o700)
        os.chmod(self.target / "private", 0o700)
        os.chmod(self.target / "private" / "candidate", 0o700)
        self.clock = _Clock()
        self.captured_at = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        roster = ("AAPL",) + tuple(f"Z{number:03d}" for number in range(628))
        self.base = VerifiedStage10Base(
            target_profile_id="stage10_fmp_market_history_v1",
            scope_manifest_sha256="a" * 64,
            registry_source_sha256="b" * 64,
            completion_sha256="c" * 64,
            reconciliation_sha256="d" * 64,
            failure_manifest_sha256="e" * 64,
            roster_symbols=roster,
            inherited_failed_symbols=tuple(f"Z{number:03d}" for number in range(8)),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _now(self) -> datetime:
        value = self.captured_at
        self.captured_at += timedelta(seconds=1)
        return value

    def _base_verifier(self, root: Path) -> VerifiedStage10Base:
        self.assertEqual(root, self.target)
        return self.base

    @staticmethod
    def _draft(intent: HistoryExtensionIntent, *, row_count: int = 1) -> HistoryExtensionResultDraft:
        return HistoryExtensionResultDraft(
            response_sha256=hashlib.sha256(intent.id.encode("ascii")).hexdigest(),
            response_byte_count=2,
            row_count=row_count,
        )

    def _run(
        self,
        capture,
        *,
        max_requests: int,
        base_verifier=None,
    ):
        return run_offline_history_extension_rehearsal(
            self.target,
            base_verifier=base_verifier or self._base_verifier,
            capture=capture,
            max_requests=max_requests,
            now=self._now,
            monotonic=self.clock.monotonic,
            sleeper=self.clock.sleep,
            offline_test_mode=True,
        )

    def test_fixed_plan_is_window_major_and_includes_prior_failures(self) -> None:
        plan = build_history_extension_plan(self.base)

        self.assertEqual(HISTORY_EXTENSION_EXPECTED_ROSTER_COUNT, 629)
        self.assertEqual(len(HISTORY_EXTENSION_WINDOWS), 8)
        self.assertIs(HISTORY_EXTENSION_WINDOWS, STAGE10_HISTORY_WINDOWS)
        self.assertIsInstance(plan.intents[0].window, Stage10HistoryWindow)
        self.assertIs(plan.intents[0].window, STAGE10_HISTORY_WINDOWS[0])
        self.assertEqual(len(plan.intents), HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT)
        self.assertEqual(plan.intents[0].ordinal, 1)
        self.assertEqual(plan.intents[0].symbol, "AAPL")
        self.assertEqual(
            plan.intents[0].window.window_id,
            STAGE10_HISTORY_WINDOWS[0].window_id,
        )
        self.assertTrue(plan.intents[0].is_entitlement_sentinel)
        self.assertEqual(
            plan.intents[628].window.window_id,
            STAGE10_HISTORY_WINDOWS[0].window_id,
        )
        self.assertEqual(plan.intents[629].ordinal, 630)
        self.assertEqual(plan.intents[629].symbol, "AAPL")
        self.assertEqual(
            plan.intents[629].window.window_id,
            STAGE10_HISTORY_WINDOWS[1].window_id,
        )
        self.assertEqual(
            plan.intents[-1].window.end_date,
            STAGE10_HISTORY_WINDOWS[-1].end_date,
        )

        planned_symbols = {intent.symbol for intent in plan.intents}
        self.assertTrue(set(self.base.inherited_failed_symbols).issubset(planned_symbols))
        for failed_symbol in self.base.inherited_failed_symbols:
            self.assertEqual(
                sum(intent.symbol == failed_symbol for intent in plan.intents),
                len(HISTORY_EXTENSION_WINDOWS),
            )

    def test_aapl_is_forced_first_before_punctuation_and_index_symbols(self) -> None:
        # A verified base roster is canonical symbol-sorted for reconciliation,
        # but the explicit entitlement sentinel must override that ordering.
        # This adversarial roster exercises both a punctuation-prefixed symbol
        # and an index-style caret symbol while retaining the reviewed count.
        roster = (
            ("-AAA", "AAPL")
            + tuple(f"Z{number:03d}" for number in range(626))
            + ("^AAA",)
        )
        punctuation_base = replace(self.base, roster_symbols=roster)

        plan = build_history_extension_plan(punctuation_base)

        self.assertEqual(len(plan.intents), HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT)
        self.assertEqual(
            [(intent.symbol, intent.window.window_id) for intent in plan.intents[:3]],
            [
                ("AAPL", STAGE10_HISTORY_WINDOWS[0].window_id),
                ("-AAA", STAGE10_HISTORY_WINDOWS[0].window_id),
                ("Z000", STAGE10_HISTORY_WINDOWS[0].window_id),
            ],
        )
        self.assertEqual(plan.intents[628].symbol, "^AAA")
        self.assertEqual(plan.intents[629].symbol, "AAPL")
        self.assertEqual(
            plan.intents[629].window.window_id,
            STAGE10_HISTORY_WINDOWS[1].window_id,
        )

    def test_non_test_mode_fails_closed_before_base_or_capture(self) -> None:
        base_calls: list[Path] = []
        capture_calls: list[HistoryExtensionIntent] = []

        def base_verifier(root: Path) -> VerifiedStage10Base:
            base_calls.append(root)
            return self.base

        def capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            capture_calls.append(intent)
            return self._draft(intent)

        with self.assertRaises(CapabilityUnavailableError):
            run_offline_history_extension_rehearsal(
                self.target,
                base_verifier=base_verifier,
                capture=capture,
                max_requests=1,
                now=self._now,
            )

        self.assertEqual(base_calls, [])
        self.assertEqual(capture_calls, [])
        self.assertFalse((self.target / HISTORY_EXTENSION_NAMESPACE).exists())

    def test_durable_intent_precedes_capture_paces_and_resumes(self) -> None:
        completion = self.target / "private" / "completion.json"
        completion.write_bytes(b'{"base":"immutable"}')
        os.chmod(completion, 0o600)
        completion_before = completion.read_bytes()
        calls: list[HistoryExtensionIntent] = []

        def capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            intent_path = (
                self.target / HISTORY_EXTENSION_NAMESPACE / "intents" / f"{intent.id}.json"
            )
            self.assertTrue(intent_path.is_file())
            self.assertEqual(stat.S_IMODE(intent_path.stat().st_mode), 0o600)
            calls.append(intent)
            return self._draft(intent)

        first_report = self._run(capture, max_requests=2)

        extension_root = self.target / HISTORY_EXTENSION_NAMESPACE
        self.assertEqual(
            extension_root.parts[-3:],
            ("private", "candidate", "stage10-history-extension-v1"),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual([call.symbol for call in calls], ["AAPL", "Z000"])
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])
        self.assertEqual(first_report.requests_issued, 2)
        self.assertEqual(first_report.recorded_request_count, 2)
        self.assertEqual(first_report.stop_reason, "request_limit_reached")
        self.assertFalse(first_report.completion_claimed)
        self.assertEqual(completion.read_bytes(), completion_before)
        self.assertFalse((self.target / "stores").exists())
        self.assertEqual(stat.S_IMODE(extension_root.stat().st_mode), 0o700)
        self.assertEqual(
            len(list((extension_root / "intents").glob("*.json"))),
            2,
        )
        self.assertEqual(
            len(list((extension_root / "results").glob("*.json"))),
            2,
        )

        second_report = self._run(capture, max_requests=1)

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1].symbol, "Z001")
        self.assertEqual(second_report.requests_issued, 1)
        self.assertEqual(second_report.recorded_request_count, 3)
        self.assertFalse(second_report.completion_claimed)

    def test_empty_aapl_sentinel_records_once_and_stops_future_calls(self) -> None:
        calls: list[HistoryExtensionIntent] = []

        def empty_capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            calls.append(intent)
            return self._draft(intent, row_count=0)

        report = self._run(empty_capture, max_requests=10)

        self.assertEqual([intent.symbol for intent in calls], ["AAPL"])
        self.assertEqual(report.stop_reason, "sentinel_empty")
        self.assertEqual(report.sentinel_status, "empty")
        self.assertEqual(report.requests_issued, 1)
        self.assertEqual(report.recorded_request_count, 1)

        def unexpected_capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            raise AssertionError(f"capture must not run after empty sentinel: {intent.symbol}")

        resumed = self._run(unexpected_capture, max_requests=1)
        self.assertEqual(resumed.stop_reason, "sentinel_empty")
        self.assertEqual(resumed.requests_issued, 0)
        self.assertEqual(resumed.recorded_request_count, 1)

    def test_capture_failure_leaves_unresolved_intent_and_forbids_auto_retry(self) -> None:
        calls: list[HistoryExtensionIntent] = []

        def broken_capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            calls.append(intent)
            self.assertTrue(
                (
                    self.target
                    / HISTORY_EXTENSION_NAMESPACE
                    / "intents"
                    / f"{intent.id}.json"
                ).exists()
            )
            raise RuntimeError("injected capture interruption")

        with self.assertRaisesRegex(RuntimeError, "injected capture interruption"):
            self._run(broken_capture, max_requests=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            len(list((self.target / HISTORY_EXTENSION_NAMESPACE / "results").glob("*.json"))),
            0,
        )

        retry_calls: list[HistoryExtensionIntent] = []

        def retry_capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            retry_calls.append(intent)
            return self._draft(intent)

        with self.assertRaisesRegex(ConflictError, "automatic retry is forbidden"):
            self._run(retry_capture, max_requests=1)
        self.assertEqual(retry_calls, [])

    def test_existing_namespace_is_pinned_to_the_original_base_proof(self) -> None:
        self._run(lambda intent: self._draft(intent), max_requests=1)
        changed_base = replace(self.base, completion_sha256="f" * 64)
        capture_calls: list[HistoryExtensionIntent] = []

        def changed_verifier(root: Path) -> VerifiedStage10Base:
            self.assertEqual(root, self.target)
            return changed_base

        def capture(intent: HistoryExtensionIntent) -> HistoryExtensionResultDraft:
            capture_calls.append(intent)
            return self._draft(intent)

        with self.assertRaisesRegex(ConflictError, "manifest does not match base proof"):
            self._run(capture, max_requests=1, base_verifier=changed_verifier)
        self.assertEqual(capture_calls, [])

    def test_main_rejects_invalid_arguments_without_target_access(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        result = main(["--would-be-live"], stdout=stdout, stderr=stderr)

        self.assertEqual(result, 64)
        self.assertEqual(stdout.getvalue(), "")
        payload = loads_strict(stderr.getvalue())
        self.assertEqual(payload["error"], "invalid_arguments")
        self.assertEqual(payload["exit_code"], 64)
        self.assertFalse((self.target / HISTORY_EXTENSION_NAMESPACE).exists())


if __name__ == "__main__":  # pragma: no cover - direct focused test use
    unittest.main()
