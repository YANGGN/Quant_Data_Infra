"""Offline, explicit-store checks for the one-use operator recovery boundary."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import unittest
from unittest.mock import patch

from quant_data.errors import ConflictError
from quant_data.operations import sharadar_authorized_recovery as operator
from quant_data.operations.collection_queue import _read, atomic
from quant_data.operations.equibles_transcript_backfill import job_lock
from quant_data.operations.sharadar_selected_refresh import BUDGET
from tests.operations.test_sharadar_selected_refresh import SelectedSharadarRefreshTests as Fixture


class AuthorizedSharadarRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket.connect", side_effect=AssertionError("offline"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.f = Fixture("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.baseline()
        self.original = self.f.fetch

        def fail(*, unit, **kwargs):
            if dict(unit.parameters).get("dimension") == "MRQ":
                self.f.calls.append(unit)
                raise OSError("fixture unknown outcome")
            return self.original(unit=unit, **kwargs)

        self.f.fetch = fail
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        self.before = _read(self.f.root / "ledger.json")
        self.reservation = self.before["pending"]
        self.checkpoint = _read(self.f.root / "refresh-state.json")
        self.completed = self.checkpoint["pending"]["completed_units"]
        self.assertEqual(len(self.completed), 3)
        self.artifacts = {p.relative_to(self.f.root): p.read_bytes()
            for name in ("attempts", "responses", "publications", "direct-sf1-publications")
            for p in (self.f.root / name).glob("*.json")}
        self.f.now += timedelta(days=1)
        self.f.calls.clear()
        self.approval = self.make_approval()
        self.journal = self.f.root / "operator-recoveries" / self.approval.approval_id

    def make_approval(self):
        sha = lambda name: hashlib.sha256((self.f.root / name).read_bytes()).hexdigest()
        pending = self.checkpoint["pending"]
        return operator.RecoveryApproval("fixture-one-use", self.f.now.isoformat(),
            "Authorize one exact fixture recovery, preserving completed work.",
            sha("ledger.json"), sha("refresh-state.json"), self.reservation["unit_id"],
            pending["window"], pending["from_date"], pending["to_date"], 3, 6)

    def run_recovery(self, *, budget=BUDGET, fetch=None, factory=None):
        return operator.run_authorized_recovery(root=self.f.root,
            stores=self.f.fixture.stores, registry=self.f.fixture.registry,
            selection=self.f.fixture.selection, fetch_factory=factory or (lambda: fetch or self.original),
            approval=self.approval, budget=budget, utcnow=lambda: self.f.now,
            monotonic=lambda: self.f.elapsed, sleeper=self.f.sleep)

    def test_one_fresh_attempt_completes_fixed_window_and_preserves_evidence(self):
        def fetch(**kwargs):
            with self.assertRaises(ConflictError):
                with job_lock(self.f.root / "refresh-supervisor"):
                    self.fail("supervisor lock released during recovery")
            return self.original(**kwargs)

        result = self.run_recovery(fetch=fetch)
        self.assertEqual((result["outcome"], result["requests"], result["completed_this_run"]),
                         ("succeeded", 3, 3))
        self.assertEqual({dict(u.parameters)["to"] for u in self.f.calls}, {"2026-09-10"})
        self.assertEqual([dict(u.parameters)["dimension"] for u in self.f.calls], ["MRQ", "MRT", "MRY"])
        replacement = self.f.calls[0]
        self.assertNotEqual(replacement.unit_id, self.reservation["unit_id"])
        self.assertEqual(replacement.request_id, self.reservation["request_id"])
        for path, raw in self.artifacts.items():
            self.assertEqual((self.f.root / path).read_bytes(), raw)
        after = _read(self.f.root / "ledger.json")
        old_day = self.reservation["day"]
        self.assertEqual(after["usage"][old_day], {**self.before["usage"][old_day], "uncertain_bytes": 16*1024**2})
        self.assertIsNone(after["pending"])
        saved = _read(self.f.root / "refresh-state.json")
        self.assertEqual(saved["last_complete_date"], "2026-09-10")
        self.assertIsNone(saved["pending"])
        receipt = _read(self.f.root / "failures" / (self.reservation["unit_id"] + ".json"))
        self.assertFalse(receipt["automatic_retry"])
        self.assertIsNone(receipt["http_status"])
        self.assertEqual(receipt["unit"], self.reservation["unit"])
        with self.assertRaisesRegex(ConflictError, "already been consumed"):
            self.run_recovery()
        self.assertEqual(len(self.f.calls), 3)

    def test_changed_preimage_or_recorded_outcome_fails_before_claim(self):
        valid = self.approval
        self.approval = replace(valid, ledger_sha256="0"*64)
        with self.assertRaisesRegex(ConflictError, "preimages"):
            self.run_recovery()
        self.assertFalse((self.journal / "started.json").exists())
        self.approval = valid
        path = self.f.root / "responses" / (self.reservation["unit_id"] + ".json")
        atomic(path, {"unit_id": "unmatched"})
        with self.assertRaises(ConflictError):
            self.run_recovery()
        self.assertFalse((self.journal / "started.json").exists())
        self.assertEqual(self.f.calls, [])
        self.assertEqual(_read(self.f.root / "ledger.json"), self.before)

    def test_crashes_before_retirement_preserve_pending_and_cannot_renew_approval(self):
        real = operator.atomic
        def interrupted(path, *args, **kwargs):
            if path.parent.name == "retry-links":
                raise OSError("fixture interruption before replacement link")
            return real(path, *args, **kwargs)
        with patch.object(operator, "atomic", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.run_recovery()
        self.assertEqual(_read(self.f.root / "ledger.json"), self.before)
        with self.assertRaisesRegex(ConflictError, "already been consumed"):
            self.run_recovery()
        self.assertEqual(self.f.calls, [])

    def test_crash_after_selection_before_ledger_retirement_fails_closed(self):
        with patch.object(operator, "_save", side_effect=OSError("fixture interruption")):
            with self.assertRaises(OSError):
                self.run_recovery()
        self.assertEqual(_read(self.f.root / "ledger.json"), self.before)
        self.f.fetch = self.original
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        with self.assertRaisesRegex(ConflictError, "already been consumed"):
            self.run_recovery()
        self.assertEqual(self.f.calls, [])

    def test_new_unknown_outcome_stays_reserved_without_another_retry(self):
        def fail(**kwargs):
            self.f.calls.append(kwargs["unit"])
            raise OSError("fixture second unknown")
        with self.assertRaises(ConflictError):
            self.run_recovery(fetch=fail)
        after = _read(self.f.root / "ledger.json")
        self.assertEqual(after["pending"]["unit_id"], self.f.calls[0].unit_id)
        self.f.fetch = fail
        with self.assertRaises(ConflictError):
            self.f.run_worker()
        with self.assertRaisesRegex(ConflictError, "already been consumed"):
            self.run_recovery()
        self.assertEqual(len(self.f.calls), 1)

    def test_request_cap_does_not_reset_on_second_invocation(self):
        result = self.run_recovery(budget=replace(BUDGET, max_requests=1))
        self.assertEqual((result["outcome"], result["requests"]), ("invocation_budget", 1))
        with self.assertRaisesRegex(ConflictError, "already been consumed"):
            self.run_recovery()
        self.assertEqual(len(self.f.calls), 1)

    def test_prior_unknown_bytes_are_reserved_within_total_budget(self):
        result = self.run_recovery(budget=replace(BUDGET, max_total_bytes=32*1024**2))
        self.assertEqual((result["outcome"], result["requests"]), ("invocation_budget", 1))
        self.assertEqual(len(self.f.calls), 1)

    def test_elapsed_setup_cannot_extend_original_deadline(self):
        def delayed():
            self.f.sleep(3601)
            return self.original
        result = self.run_recovery(factory=delayed)
        self.assertEqual((result["outcome"], result["requests"]), ("invocation_budget", 0))
        self.assertEqual(self.f.calls, [])

    def test_existing_utc_daily_ceiling_is_preserved(self):
        state = _read(self.f.root / "ledger.json")
        state["usage"][self.f.now.date().isoformat()] = {"charged_attempts": 1000, "received_bytes": 0}
        atomic(self.f.root / "ledger.json", state, replace=True)
        self.approval = self.make_approval()
        result = self.run_recovery()
        self.assertEqual((result["outcome"], result["requests"]), ("daily_budget", 0))
        self.assertEqual(self.f.calls, [])
