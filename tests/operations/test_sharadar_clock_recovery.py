"""Clock-step regressions with fake time, temporary queues and real publishers."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quant_data.operations import sharadar_selected_refresh as refresh
from quant_data.operations.collection_queue import QueueResponse, _read
from quant_data.operations.sharadar_refresh_timing import (
    SharadarRefreshTiming, SharadarClockRecoveryError,
)
from tests.operations import test_sharadar_selected_refresh as fixtures


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 16, 9, 1, 6, tzinfo=timezone.utc)
        self.elapsed = 0.0
        self.sleeps = []
        self.logs = StringIO()
        self.output = redirect_stderr(self.logs)
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds
        self.now += timedelta(seconds=seconds)

    def timing(self, *, deadline=100):
        return SharadarRefreshTiming(lambda: self.now, Mock(), deadline=deadline,
            interval=1, monotonic=lambda: self.elapsed, sleeper=self.sleep)

    def test_small_regression_waits_for_real_time_without_clamping(self):
        timing = self.timing()
        first = timing()
        self.now -= timedelta(seconds=0.67)
        value = timing()
        self.assertEqual(value, self.now)
        self.assertGreaterEqual(value, first)
        self.assertGreaterEqual(self.elapsed, 0.67)
        events = [json.loads(line) for line in self.logs.getvalue().splitlines()]
        self.assertEqual([e["outcome"] for e in events], ["waiting", "recovered"])

    def test_persistent_regression_stops_after_five_seconds(self):
        timing = self.timing()
        timing()
        self.now -= timedelta(seconds=60)
        with self.assertRaises(SharadarClockRecoveryError):
            timing()
        self.assertAlmostEqual(self.elapsed, 5)
        self.assertIn('"outcome":"exhausted"', self.logs.getvalue())

    def test_nested_deadline_shortens_wait_and_is_restored(self):
        timing = self.timing()
        timing()
        self.now -= timedelta(seconds=60)
        with self.assertRaises(SharadarClockRecoveryError):
            with timing.bound(0.25):
                timing()
        self.assertAlmostEqual(self.elapsed, 0.25)
        self.assertEqual(timing.deadline, 100)

    def test_recovery_at_deadline_is_not_allowed_to_continue(self):
        timing = self.timing(deadline=0.2)
        timing()
        self.now -= timedelta(seconds=0.2)
        with self.assertRaises(SharadarClockRecoveryError):
            timing()
        self.assertAlmostEqual(self.elapsed, 0.2)

    def test_forward_wall_step_does_not_shorten_monotonic_request_spacing(self):
        timing = self.timing()
        response = QueueResponse(200, b"original", self.now.isoformat())
        timing.transport.return_value = response
        unit = SimpleNamespace(timeout_seconds=20)
        self.assertTrue(timing.before_request(unit=unit, deadline=100))
        self.assertIs(timing.fetch(unit=unit), response)
        self.now += timedelta(hours=1)
        self.assertTrue(timing.before_request(unit=unit, deadline=100))
        self.assertAlmostEqual(self.elapsed, 1)

    def test_pacing_does_not_reserve_or_fetch_without_time_for_request(self):
        timing = self.timing(deadline=20.5)
        timing.last_dispatch = 0
        self.assertFalse(timing.before_request(
            unit=SimpleNamespace(timeout_seconds=20), deadline=20.5))
        self.assertEqual(self.sleeps, [])
        timing.transport.assert_not_called()

    def test_response_is_returned_unchanged_before_clock_wait(self):
        timing = self.timing()
        original = QueueResponse(200, b"original", (self.now + timedelta(seconds=1)).isoformat())
        timing.transport.return_value = original
        self.assertIs(timing.fetch(), original)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(timing().isoformat(), original.captured_at)

    def test_cli_reports_specific_safe_clock_failure(self):
        output = StringIO()
        selection = SimpleNamespace(binding=SimpleNamespace(mode="active"))
        with (
            patch.object(refresh, "load_registry"),
            patch.object(refresh, "load_bindings", return_value={"sharadar_fundamentals": object()}),
            patch.object(refresh, "pin_binding", return_value=selection),
            patch.object(refresh, "host_fetch"),
            patch.object(refresh, "run_refresh", side_effect=SharadarClockRecoveryError("internal")),
            redirect_stdout(output),
        ):
            code = refresh.main([])
        self.assertEqual(code, 75)
        self.assertEqual(json.loads(output.getvalue())["reason"], "clock_recovery_exhausted")
        self.assertNotIn("internal", output.getvalue())

    def test_partition_deadline_blocks_publication_after_slow_inspection(self):
        from quant_data.operations import collection_sharadar_direct as direct
        from quant_data.operations.collection_plan import ProviderBudget
        with TemporaryDirectory() as directory:
            def inspect(**kwargs):
                self.elapsed = 3
                return SimpleNamespace(outcome="ready")
            with (
                patch.object(direct, "_validate_initial"),
                patch.object(direct, "inspect_partition", side_effect=inspect),
                patch.object(direct, "publish_partition") as publish,
            ):
                result = direct.run_partition(
                    root=Path(directory).resolve(), stores=object(), registry=object(),
                    selection=object(), initial_unit=object(),
                    budget=ProviderBudget("sharadar", 1, 16 * 1024 * 1024, 60, 1000),
                    fetch=Mock(), utcnow=lambda: self.now, monotonic=lambda: self.elapsed,
                    sleeper=self.sleep, deadline=2)
            self.assertEqual(result["outcome"], "invocation_budget")
            publish.assert_not_called()

    def test_partition_pacing_applies_to_each_page_before_queue_reservation(self):
        from quant_data.operations import collection_sharadar_direct as direct
        from quant_data.operations.collection_plan import ProviderBudget
        unit = SimpleNamespace(timeout_seconds=30, max_response_bytes=1024)
        progress = SimpleNamespace(outcome="page_required", next_unit=unit,
            captured_bytes=0, page_units=())
        before = Mock(side_effect=(True, False))
        with TemporaryDirectory() as directory:
            with (
                patch.object(direct, "_validate_initial"),
                patch.object(direct, "inspect_partition", return_value=progress),
                patch.object(direct, "build_plan", return_value=object()),
                patch.object(direct, "run_queue",
                    return_value={"outcome": "acquired", "requests": 1, "received_bytes": 10}) as queue,
            ):
                result = direct.run_partition(
                    root=Path(directory).resolve(), stores=object(), registry=object(),
                    selection=object(), initial_unit=unit,
                    budget=ProviderBudget("sharadar", 2, 16 * 1024 * 1024, 100, 1000),
                    fetch=Mock(), utcnow=lambda: self.now, monotonic=lambda: self.elapsed,
                    sleeper=self.sleep, deadline=90, before_request=before)
            self.assertEqual(result["outcome"], "invocation_budget")
            self.assertEqual(result["requests"], 1)
            self.assertEqual(before.call_count, 2)
            self.assertEqual(queue.call_count, 1)
            self.assertEqual(queue.call_args.kwargs["deadline"], 90)


class RefreshClockIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SelectedSharadarRefreshTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.baseline()
        network = patch("socket.socket.connect", side_effect=AssertionError("Offline test"))
        network.start()
        self.addCleanup(network.stop)
        self.logs = StringIO()
        output = redirect_stderr(self.logs)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_clock_steps_after_each_response_keep_original_evidence_and_complete(self):
        original_fetch = self.f.fetch
        responses = {}
        def fetch(**kwargs):
            response = original_fetch(**kwargs)
            responses[kwargs["unit"].unit_id] = response
            self.f.now -= timedelta(seconds=2)
            return response
        self.f.fetch = fetch
        result = self.f.run_worker()
        self.assertEqual((result["outcome"], result["requests"]), ("succeeded", 7))
        self.assertEqual(len(self.f.calls), 7)
        for unit_id, original in responses.items():
            receipt = _read(self.f.root / "responses" / (unit_id + ".json"))
            self.assertEqual(datetime.fromisoformat(receipt["captured_at"]),
                datetime.fromisoformat(original.captured_at))
            self.assertEqual((self.f.root / "blobs" / (receipt["content_sha256"] + ".json")).read_bytes(),
                original.body)
        self.assertIsNone(_read(self.f.root / "refresh-state.json")["pending"])
        self.assertEqual(self.logs.getvalue().count('"outcome":"recovered"'), 7)

    def test_failed_clock_wait_retains_response_and_resume_uses_its_later_time(self):
        original_fetch = self.f.fetch
        def fetch(**kwargs):
            self.f.sleep(0.5)
            response = original_fetch(**kwargs)
            self.f.now = datetime.fromisoformat(response.captured_at) - timedelta(seconds=60)
            return response
        self.f.fetch = fetch
        with self.assertRaises(SharadarClockRecoveryError):
            self.f.run_worker()
        self.assertEqual(len(self.f.calls), 1)
        unit = self.f.calls[0]
        receipt_path = self.f.root / "responses" / (unit.unit_id + ".json")
        original_receipt = receipt_path.read_bytes()
        ledger = _read(self.f.root / "ledger.json")
        self.assertIsNone(ledger["pending"])
        self.assertFalse(_read(self.f.root / "refresh-state.json")["pending"]["definitions_complete"])
        self.f.now = datetime.fromisoformat(ledger["last_request_at"]) + timedelta(seconds=0.1)
        self.f.elapsed = 0  # New process/monotonic origin.
        self.f.fetch = original_fetch
        result = self.f.run_worker()
        self.assertEqual((result["outcome"], result["requests"]), ("succeeded", 6))
        self.assertEqual(len({u.unit_id for u in self.f.calls}), 7)
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

    def test_definitions_clock_wait_cannot_extend_thirty_second_deadline(self):
        original_fetch = self.f.fetch
        def fetch(**kwargs):
            response = original_fetch(**kwargs)
            self.f.elapsed = 29.5
            self.f.now = datetime.fromisoformat(response.captured_at) - timedelta(seconds=2)
            return response
        self.f.fetch = fetch
        with self.assertRaises(SharadarClockRecoveryError):
            self.f.run_worker()
        self.assertAlmostEqual(self.f.elapsed, 30)
        self.assertEqual(len(self.f.calls), 1)
        state = _read(self.f.root / "refresh-state.json")
        self.assertFalse(state["pending"]["definitions_complete"])
        self.assertEqual(state["last_complete_date"], "2026-09-09")
        self.assertIsNone(_read(self.f.root / "ledger.json")["pending"])

    def test_forward_steps_preserve_spacing_across_partition_queues(self):
        original_fetch = self.f.fetch
        dispatches = []
        def fetch(**kwargs):
            dispatches.append(self.f.elapsed)
            response = original_fetch(**kwargs)
            self.f.elapsed -= 1  # Fast transport, independent of wall time.
            self.f.now += timedelta(seconds=10)
            return response
        self.f.fetch = fetch
        self.assertEqual(self.f.run_worker()["outcome"], "succeeded")
        self.assertEqual(len(dispatches), 7)
        self.assertTrue(all(b - a >= 1 for a, b in zip(dispatches, dispatches[1:])))


if __name__ == "__main__":
    unittest.main()
