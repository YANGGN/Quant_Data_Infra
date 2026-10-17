from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import json
from pathlib import Path
import stat
import tempfile
import unittest

from quant_data.errors import ValidationError
from quant_data.operations.refresh_status import (
    read_refresh_status,
    record_refresh_attempt,
)


class RefreshStatusTests(unittest.TestCase):
    def _record(
        self,
        root: Path,
        *,
        completed: str,
        outcome: str = "published",
        successful: str | None = None,
    ) -> None:
        started = "2026-09-05T12:00:00Z"
        record_refresh_attempt(
            root,
            source="fmp_macro_calendar",
            started_at=started,
            completed_at=completed,
            outcome=outcome,
            successful_fetch_at=successful or (completed if outcome != "failed" else None),
        )

    def test_reader_rejects_inconsistent_success_markers_without_writing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary) / "refresh-status"
            self._record(root, completed="2026-09-05T12:01:00Z")
            path = root / "fmp_macro_calendar.json"
            valid = json.loads(path.read_text())
            for marker in (None, "2026-09-05T12:02:00Z"):
                invalid = dict(valid, last_successful_fetch_at=marker)
                path.write_text(json.dumps(invalid))
                before = path.read_bytes()
                self.assertEqual(read_refresh_status(root, source="fmp_macro_calendar"), {})
                self.assertEqual(path.read_bytes(), before)

    def test_noop_advances_latest_success_and_failure_retains_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"
            self._record(root, completed="2026-09-05T12:01:00Z")
            self._record(
                root,
                completed="2026-09-05T12:02:00Z",
                outcome="unchanged",
            )
            self._record(
                root,
                completed="2026-09-05T12:03:00Z",
                outcome="failed",
            )

            status = read_refresh_status(root, source="fmp_macro_calendar")

            self.assertEqual(
                status["last_successful_fetch_at"], "2026-09-05T12:02:00Z"
            )
            self.assertEqual(status["last_attempt"]["outcome"], "failed")
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((root / "fmp_macro_calendar.json").stat().st_mode),
                0o600,
            )

    def test_failure_after_fetch_retains_the_new_fetch_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"
            self._record(root, completed="2026-09-05T12:01:00Z")
            record_refresh_attempt(
                root,
                source="fmp_macro_calendar",
                started_at="2026-09-05T12:03:00Z",
                completed_at="2026-09-05T12:04:00Z",
                outcome="failed",
                successful_fetch_at="2026-09-05T12:03:30Z",
            )

            status = read_refresh_status(root, source="fmp_macro_calendar")

            self.assertEqual(
                status["last_successful_fetch_at"], "2026-09-05T12:03:30Z"
            )

    def test_concurrent_and_older_completions_cannot_regress_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"

            def write(second: int) -> None:
                completed = f"2026-09-05T12:00:{second:02d}Z"
                self._record(
                    root,
                    completed=completed,
                    outcome="failed" if second == 9 else "unchanged",
                )

            with ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(write, range(1, 10)))
            self._record(root, completed="2026-09-05T12:00:05Z")

            status = read_refresh_status(root, source="fmp_macro_calendar")

            self.assertEqual(
                status["last_attempt"]["completed_at"], "2026-09-05T12:00:09Z"
            )
            self.assertEqual(status["last_attempt"]["outcome"], "failed")
            self.assertEqual(
                status["last_successful_fetch_at"], "2026-09-05T12:00:08Z"
            )

    def test_delayed_success_updates_fetch_marker_without_replacing_newer_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"
            record_refresh_attempt(
                root,
                source="fmp_macro_calendar",
                started_at="2026-09-05T12:00:00Z",
                completed_at="2026-09-05T12:10:00Z",
                outcome="failed",
                successful_fetch_at=None,
            )
            record_refresh_attempt(
                root,
                source="fmp_macro_calendar",
                started_at="2026-09-05T12:01:00Z",
                completed_at="2026-09-05T12:09:00Z",
                outcome="published",
                successful_fetch_at="2026-09-05T12:08:00Z",
            )

            status = read_refresh_status(root, source="fmp_macro_calendar")

            self.assertEqual(
                status["last_attempt"]["completed_at"], "2026-09-05T12:10:00Z"
            )
            self.assertEqual(
                status["last_successful_fetch_at"], "2026-09-05T12:08:00Z"
            )

    def test_reader_rejects_missing_malformed_and_symlink_state_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"
            self.assertEqual(read_refresh_status(root, source="fmp_macro_calendar"), {})
            self.assertFalse(root.exists())

            root.mkdir()
            target = root / "target.json"
            target.write_text("{}")
            (root / "fmp_macro_calendar.json").symlink_to(target)
            self.assertEqual(read_refresh_status(root, source="fmp_macro_calendar"), {})
            self.assertFalse((root / ".refresh-status.lock").exists())

            (root / "fmp_macro_calendar.json").unlink()
            (root / "fmp_macro_calendar.json").write_text("{bad")
            self.assertEqual(read_refresh_status(root, source="fmp_macro_calendar"), {})

            (root / "fmp_macro_calendar.json").write_text(
                '{"schema":"quant_data.refresh_status","version":1,'
                '"source":"fmp_macro_calendar","last_attempt":'
                '{"started_at":"2026-09-05T12:00:00Z",'
                '"completed_at":"2026-09-05T12:01:00Z",'
                '"outcome":[],"note":""},"last_successful_fetch_at":null}'
            )
            self.assertEqual(read_refresh_status(root, source="fmp_macro_calendar"), {})

    def test_rejects_unsafe_source_and_fetch_outside_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refresh-status"
            with self.assertRaises(ValidationError):
                record_refresh_attempt(
                    root,
                    source="../escape",
                    started_at="2026-09-05T12:00:00Z",
                    completed_at="2026-09-05T12:01:00Z",
                    outcome="published",
                    successful_fetch_at="2026-09-05T12:01:00Z",
                )
            with self.assertRaises(ValidationError):
                record_refresh_attempt(
                    root,
                    source="fmp_macro_calendar",
                    started_at="2026-09-05T12:00:00Z",
                    completed_at="2026-09-05T12:01:00Z",
                    outcome="failed",
                    successful_fetch_at="2026-09-05T11:59:59Z",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
