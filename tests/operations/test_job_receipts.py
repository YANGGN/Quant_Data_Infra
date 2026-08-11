from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.operations.job_receipts import (
    MAX_LOG_EVENTS,
    JobRunReceipt,
    LockWaitReceipt,
    PrivateJobState,
    StepRunReceipt,
)


_RUN_ID = "a" * 64
_PLAN_SHA256 = "b" * 64
_INGESTION_RUN_ID = "c" * 64


def _lock() -> LockWaitReceipt:
    return LockWaitReceipt(
        alias="store-1",
        key_prefix="0123456789ab",
        wait_seconds=Decimal("0.125"),
        outcome="acquired",
    )


def _step(
    *,
    outcome: str = "succeeded",
    exit_code: int = 0,
    timeout: bool = False,
    attempts: int = 1,
    errors: tuple[str, ...] = (),
    messages: tuple[str, ...] = (),
) -> StepRunReceipt:
    return StepRunReceipt(
        step_id="macro_fixture",
        outcome=outcome,
        attempts=attempts,
        elapsed_seconds=Decimal("1.25"),
        timeout=timeout,
        exit_code=exit_code,
        ingestion_run_ids=(_INGESTION_RUN_ID,) if outcome == "succeeded" else (),
        error_codes=errors,
        error_messages=messages,
    )


def _receipt(
    *,
    run_id: str = _RUN_ID,
    job_id: str = "macro-monthly",
    semantic_outcome: str = "succeeded",
    aggregate_exit_code: int = 0,
    calendar_decision: str = "run",
    skip_reason: str | None = None,
    steps: tuple[StepRunReceipt, ...] | None = None,
    error_codes: tuple[str, ...] = (),
    error_messages: tuple[str, ...] = (),
    committed_store_aliases: tuple[str, ...] = ("store-1",),
    ingestion_run_ids: tuple[str, ...] = (_INGESTION_RUN_ID,),
) -> JobRunReceipt:
    if steps is None:
        steps = (_step(),)
    return JobRunReceipt(
        schema_version="1.0.0",
        run_id=run_id,
        job_id=job_id,
        job_version="1.0.0",
        plan_sha256=_PLAN_SHA256,
        trigger_kind="manual",
        requested_at="2026-08-10T10:00:00Z",
        started_at="2026-08-10T10:00:01Z",
        finished_at="2026-08-10T10:00:02Z",
        timezone="America/Toronto",
        calendar_decision=calendar_decision,
        skip_reason=skip_reason,
        logical_stores=("macro",),
        lock_order=(_lock(),),
        steps=steps,
        semantic_outcome=semantic_outcome,
        committed_store_aliases=committed_store_aliases,
        ingestion_run_ids=ingestion_run_ids,
        aggregate_exit_code=aggregate_exit_code,
        code_revision="d" * 40,
        warnings=(),
        error_codes=error_codes,
        error_messages=error_messages,
    )


class JobReceiptContractTests(unittest.TestCase):
    def test_all_semantic_outcomes_are_strict_path_free_json(self) -> None:
        completed = _receipt()
        variants = (
            _receipt(semantic_outcome="changed"),
            _receipt(semantic_outcome="unchanged"),
            _receipt(
                semantic_outcome="skipped",
                calendar_decision="skipped",
                skip_reason="calendar closed",
                steps=(
                    _step(
                        outcome="skipped",
                        attempts=0,
                    ),
                ),
            ),
            completed,
            _receipt(
                semantic_outcome="partial",
                aggregate_exit_code=70,
                steps=(
                    _step(
                        outcome="partial",
                        exit_code=70,
                        errors=("partial_scope",),
                        messages=("scope incomplete",),
                    ),
                ),
                error_codes=("partial_scope",),
                error_messages=("scope incomplete",),
            ),
            _receipt(
                semantic_outcome="failed",
                aggregate_exit_code=75,
                steps=(
                    _step(
                        outcome="failed",
                        exit_code=75,
                        errors=("lock_timeout",),
                        messages=("lock wait elapsed",),
                    ),
                ),
                error_codes=("lock_timeout",),
                error_messages=("lock wait elapsed",),
            ),
        )
        for receipt in variants:
            with self.subTest(receipt.semantic_outcome):
                rendered = dumps_strict(receipt.to_primitive())
                self.assertEqual(json.loads(rendered)["run_id"], _RUN_ID)
                self.assertNotIn("/tmp", rendered)
                self.assertNotIn("\\\\", rendered)

    def test_receipt_validation_rejects_unsafe_or_inconsistent_material(self) -> None:
        with self.assertRaises(ValidationError):
            LockWaitReceipt("macro", "0123456789ab", Decimal("0"), "acquired")
        with self.assertRaises(ValidationError):
            LockWaitReceipt("store-1", "A" * 12, Decimal("0"), "acquired")
        with self.assertRaises(ValidationError):
            _step(
                outcome="failed",
                exit_code=70,
                errors=("provider_failure",),
                messages=("body {raw}",),
            )
        with self.assertRaises(ValidationError):
            _step(
                outcome="failed",
                exit_code=70,
                errors=("provider_failure",),
                messages=("request https://example.invalid/x",),
            )
        with self.assertRaises(ValidationError):
            _step(
                outcome="failed",
                exit_code=70,
                errors=("provider_failure",),
                messages=("SELECT value FROM receipts",),
            )
        with self.assertRaises(ValidationError):
            _receipt(run_id="A" * 64)

    def test_receipt_rejects_nonmanual_trigger_and_nonchronological_times(self) -> None:
        values = _receipt().to_primitive()
        values["trigger_kind"] = "external"
        with self.assertRaises(ValidationError):
            JobRunReceipt(**values)  # type: ignore[arg-type]
        values = _receipt().to_primitive()
        values["finished_at"] = "2026-08-10T09:59:59Z"
        with self.assertRaises(ValidationError):
            JobRunReceipt(**values)  # type: ignore[arg-type]


class PrivateJobStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_root = self.root / "private-state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_construction_is_dry_and_receipt_publication_is_private_atomic_and_immutable(self) -> None:
        state = PrivateJobState(self.state_root)
        self.assertFalse(self.state_root.exists())
        receipt = _receipt()

        published = state.publish_receipt(receipt)

        self.assertEqual(published, self.state_root / "receipts" / f"{_RUN_ID}.json")
        self.assertEqual(self._mode(self.state_root), 0o700)
        self.assertEqual(self._mode(published.parent), 0o700)
        self.assertEqual(self._mode(published), 0o600)
        raw = published.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(json.loads(raw)["plan_sha256"], _PLAN_SHA256)
        self.assertEqual(tuple(published.parent.glob("*.tmp")), ())
        with self.assertRaises(ConflictError):
            state.publish_receipt(receipt)
        self.assertEqual(published.read_text(encoding="utf-8"), raw)

    def test_invalid_roots_dry_mode_and_publication_failure_leave_no_mutable_output(self) -> None:
        with self.assertRaises(ValidationError):
            PrivateJobState("")
        with self.assertRaises(ValidationError):
            PrivateJobState("relative/state")
        with self.assertRaises(ValidationError):
            PrivateJobState(Path.home())
        file_root = self.root / "state-file"
        file_root.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(ValidationError):
            PrivateJobState(file_root)

        dry_root = self.root / "dry"
        dry_state = PrivateJobState(dry_root, dry_run=True)
        self.assertFalse(dry_root.exists())
        with self.assertRaises(ValidationError):
            dry_state.publish_receipt(_receipt())
        self.assertFalse(dry_root.exists())

        state = PrivateJobState(self.state_root)
        with mock.patch(
            "quant_data.operations.job_receipts.os.link",
            side_effect=OSError("simulated local I/O failure"),
        ):
            with self.assertRaises(OSError):
                state.publish_receipt(_receipt())
        self.assertFalse((self.state_root / "receipts" / f"{_RUN_ID}.json").exists())
        if (self.state_root / "receipts").exists():
            self.assertEqual(tuple((self.state_root / "receipts").glob("*.tmp")), ())

    def test_private_log_is_bounded_strict_and_immutable(self) -> None:
        state = PrivateJobState(self.state_root)
        events = (
            {
                "event": "job_started",
                "fields": {"attempt": 1, "detail": "manual rehearsal"},
            },
            {"event": "step_finished", "fields": {"elapsed": Decimal("1.25")}},
        )
        path = state.publish_log(_RUN_ID, events)
        self.assertEqual(self._mode(path.parent), 0o700)
        self.assertEqual(self._mode(path), 0o600)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["event"], "job_started")
        with self.assertRaises(ConflictError):
            state.publish_log(_RUN_ID, events)
        with self.assertRaises(ValidationError):
            state.publish_log(_PLAN_SHA256, ({"event": "bad", "fields": {"url": "x"}},))
        with self.assertRaises(ValidationError):
            state.publish_log(
                _PLAN_SHA256,
                ({"event": "bad", "fields": {"detail": "/tmp/private"}},),
            )
        with self.assertRaises(ResourceLimitError):
            state.publish_log(
                _PLAN_SHA256,
                tuple({"event": "event", "fields": {}} for _ in range(MAX_LOG_EVENTS + 1)),
            )

    def test_marker_requires_full_monthly_success_and_never_overwrites(self) -> None:
        state = PrivateJobState(self.state_root)
        success = _receipt()
        marker = state.publish_monthly_success_marker(success, "2026-08")
        self.assertEqual(self._mode(marker.parent), 0o700)
        self.assertEqual(self._mode(marker), 0o600)
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["period"], "2026-08")
        with self.assertRaises(ConflictError):
            state.publish_monthly_success_marker(success, "2026-08")
        unchanged = _receipt(
            semantic_outcome="unchanged",
            steps=(_step(outcome="unchanged"),),
            committed_store_aliases=(),
            ingestion_run_ids=(),
        )
        unchanged_marker = state.publish_monthly_success_marker(unchanged, "2026-09")
        self.assertEqual(
            json.loads(unchanged_marker.read_text(encoding="utf-8"))["semantic_outcome"],
            "unchanged",
        )
        with self.assertRaises(ValidationError):
            state.publish_monthly_success_marker(_receipt(job_id="macro-daily"), "2026-10")
        partial = _receipt(
            semantic_outcome="partial",
            aggregate_exit_code=70,
            steps=(
                _step(
                    outcome="partial",
                    exit_code=70,
                    errors=("partial_scope",),
                    messages=("scope incomplete",),
                ),
            ),
            error_codes=("partial_scope",),
            error_messages=("scope incomplete",),
        )
        with self.assertRaises(ValidationError):
            state.publish_monthly_success_marker(partial, "2026-10")
        with self.assertRaises(ValidationError):
            state.publish_monthly_success_marker(success, "2026-13")


if __name__ == "__main__":
    unittest.main()
