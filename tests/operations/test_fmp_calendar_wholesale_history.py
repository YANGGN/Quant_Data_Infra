from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.errors import ConflictError, StoreUnavailableError, ValidationError
from quant_data.operations import fmp_calendar_wholesale_history as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


FIXED_NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
SECRET = "fixture-fmp-key-not-for-reports"


@dataclass(frozen=True, slots=True)
class _RawCapture:
    response_bytes: bytes
    captured_at: str
    request_start_date: str
    request_end_date: str


@dataclass(frozen=True, slots=True)
class _RawReport:
    outcome: str
    semantic_identity: str
    capture_id: str
    written_captures: int
    written_rows: int


@dataclass(frozen=True, slots=True)
class _EmploymentCapture:
    events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EmploymentReport:
    outcome: str
    semantic_identity: str
    written_versions: int


class _RawParser:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_RawCapture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _RawCapture:
        if self.lock_state["held"]:
            raise AssertionError("raw parser ran while publisher lock was held")
        self.events.append("raw_parser")
        capture = _RawCapture(body, captured_at, start_date, end_date)
        self.calls.append(capture)
        return capture


class _RawPublisher:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        outcome: str = "published",
        failures: int = 0,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.outcome = outcome
        self.failures = failures
        self.calls: list[_RawCapture] = []
        self._completed: dict[tuple[str, str], _RawCapture] = {}

    def completed_windows(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._completed))

    def load_latest_window(
        self, start_date: str, end_date: str
    ) -> _RawCapture | None:
        self.events.append("raw_load")
        return self._completed.get((start_date, end_date))

    def publish(self, capture: object) -> _RawReport:
        if not isinstance(capture, _RawCapture):
            raise AssertionError("raw publisher received an invalid capture")
        self.lock_state["held"] = True
        try:
            self.events.append("raw_publish")
            self.calls.append(capture)
            if self.failures:
                self.failures -= 1
                raise StoreUnavailableError("fixture wholesale raw publication failed")
            self._completed[(capture.request_start_date, capture.request_end_date)] = capture
            unchanged = self.outcome == "unchanged"
            return _RawReport(
                self.outcome,
                "fixture-wholesale-semantic-identity",
                "fixture-wholesale-capture",
                0 if unchanged else 1,
                0 if unchanged else 2,
            )
        finally:
            self.lock_state["held"] = False


class _EmploymentParser:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        no_reviewed_alias: bool = False,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.no_reviewed_alias = no_reviewed_alias
        self.calls: list[tuple[bytes, str, str, str]] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _EmploymentCapture:
        if self.lock_state["held"]:
            raise AssertionError("employment parser ran while publisher lock was held")
        self.events.append("employment_parser")
        self.calls.append((body, captured_at, start_date, end_date))
        if self.no_reviewed_alias:
            raise ValidationError("fixture source contains no reviewed employment event")
        return _EmploymentCapture(("payroll",))


class _EmploymentPublisher:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        failures: int = 0,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.failures = failures
        self.calls: list[_EmploymentCapture] = []

    def publish(self, capture: object) -> _EmploymentReport:
        if not isinstance(capture, _EmploymentCapture):
            raise AssertionError("employment publisher received an invalid capture")
        self.lock_state["held"] = True
        try:
            self.events.append("employment_publish")
            self.calls.append(capture)
            if self.failures:
                self.failures -= 1
                raise StoreUnavailableError("fixture employment publication failed")
            return _EmploymentReport(
                "published",
                "fixture-employment-semantic-identity",
                1,
            )
        finally:
            self.lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        failure_at: int | None = None,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.failure_at = failure_at
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        if self.lock_state["held"]:
            raise AssertionError("network ran while publisher lock was held")
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        if self.failure_at == len(self.calls):
            raise StoreUnavailableError("fixture one-attempt transport failure")
        return FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=b"[]",
        )


class _CredentialReader:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.events.append("credential")
        self.calls.append(dict(kwargs))
        return SECRET


class FmpWholesaleCalendarHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.lock_state = {"held": False}
        self.raw_parser = _RawParser(self.events, self.lock_state)
        self.credential = _CredentialReader(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        raw_publisher: _RawPublisher | None = None,
        employment_parser: _EmploymentParser | None = None,
        employment_publisher: _EmploymentPublisher | None = None,
        transport: _Transport | None = None,
    ) -> tuple[
        operation.FmpWholesaleCalendarHistoryRunner,
        _RawPublisher,
        _EmploymentParser,
        _EmploymentPublisher,
        _Transport,
    ]:
        selected_raw_publisher = raw_publisher or _RawPublisher(
            self.events, self.lock_state
        )
        selected_employment_parser = employment_parser or _EmploymentParser(
            self.events, self.lock_state
        )
        selected_employment_publisher = employment_publisher or _EmploymentPublisher(
            self.events, self.lock_state
        )
        selected_transport = transport or _Transport(self.events, self.lock_state)

        def raw_publisher_factory() -> _RawPublisher:
            self.events.append("raw_publisher_factory")
            return selected_raw_publisher

        def employment_publisher_factory() -> _EmploymentPublisher:
            self.events.append("employment_publisher_factory")
            return selected_employment_publisher

        runner = operation.FmpWholesaleCalendarHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.raw_parser,
            publisher_factory=raw_publisher_factory,
            employment_parser=selected_employment_parser,
            employment_publisher_factory=employment_publisher_factory,
            transport=selected_transport,
            credential_environment={"FMP_API_KEY": "fixture-environment-value"},
            credential_reader=self.credential,
            utcnow=lambda: FIXED_NOW,
        )
        return (
            runner,
            selected_raw_publisher,
            selected_employment_parser,
            selected_employment_publisher,
            selected_transport,
        )

    def test_fixed_plan_is_serial_redacts_key_and_persists_raw_first(self) -> None:
        runner, raw_publisher, employment_parser, employment_publisher, transport = (
            self._runner()
        )

        report = runner.run()

        windows = operation.FMP_US_CALENDAR_WHOLESALE_WINDOWS
        self.assertEqual(len(windows), 56)
        self.assertEqual(
            (windows[0].start_date.isoformat(), windows[0].end_date.isoformat()),
            ("2013-01-01", "2013-03-31"),
        )
        self.assertEqual(
            (windows[-1].start_date.isoformat(), windows[-1].end_date.isoformat()),
            ("2026-07-22", "2026-08-17"),
        )
        self.assertEqual(
            report.mapping(),
            {
                "requested": 56,
                "published": 56,
                "unchanged": 0,
                "written_rows": 112,
                "replayed_windows": 0,
                "employment_published": 56,
                "employment_unchanged": 0,
                "employment_written_versions": 56,
                "employment_normalization_pending": 0,
                "employment_recognized_events": 56,
            },
        )
        self.assertEqual(len(self.credential.calls), 1)
        self.assertEqual(len(transport.calls), len(windows))
        self.assertEqual(len(self.raw_parser.calls), len(windows))
        self.assertEqual(len(raw_publisher.calls), len(windows))
        self.assertEqual(len(employment_parser.calls), len(windows))
        self.assertEqual(len(employment_publisher.calls), len(windows))
        self.assertEqual(self.events[:5], [
            "raw_publisher_factory",
            "employment_publisher_factory",
            "credential",
            "transport",
            "raw_parser",
        ])
        self.assertEqual(self.events[5], "raw_publish")
        self.assertEqual(self.events[6], "employment_parser")
        self.assertEqual(self.events[7], "employment_publish")
        for window, call in zip(windows, transport.calls, strict=True):
            self.assertEqual(call["url"], operation.FMP_ECONOMIC_CALENDAR_URL)
            self.assertEqual(call["parameters"], window.parameters)
            self.assertEqual(call["headers"]["apikey"], SECRET)
            self.assertNotIn("apikey", call["parameters"])
            self.assertEqual(call["timeout_seconds"], 60)
            self.assertEqual(call["max_bytes"], 1024 * 1024)
        self.assertNotIn(SECRET, str(report.mapping()))
        self.assertFalse(self.lock_state["held"])

    def test_missing_only_resume_never_reuses_old_normalized_checkpoint(self) -> None:
        transport = _Transport(self.events, self.lock_state, failure_at=3)
        runner, raw_publisher, _, _, _ = self._runner(transport=transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(len(raw_publisher.calls), 2)

        report = runner.run()

        self.assertEqual((report.requested, report.replayed_windows), (54, 2))
        self.assertEqual(len(transport.calls), 57)
        self.assertEqual(
            transport.calls[3]["parameters"],
            operation.FMP_US_CALENDAR_WHOLESALE_WINDOWS[2].parameters,
        )
        self.assertEqual(len(self.credential.calls), 2)
        self.assertEqual(len(raw_publisher.calls), 56)

    def test_normalization_failure_replays_stored_raw_without_duplicate_request(self) -> None:
        employment_publisher = _EmploymentPublisher(
            self.events, self.lock_state, failures=1
        )
        runner, raw_publisher, _, _, transport = self._runner(
            employment_publisher=employment_publisher
        )

        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(raw_publisher.calls), 1)
        self.assertEqual(len(employment_publisher.calls), 1)

        report = runner.run()

        self.assertEqual((report.requested, report.replayed_windows), (55, 1))
        self.assertEqual(
            transport.calls[1]["parameters"],
            operation.FMP_US_CALENDAR_WHOLESALE_WINDOWS[1].parameters,
        )
        self.assertEqual(len(raw_publisher.calls), 56)
        self.assertEqual(len(transport.calls), 56)

    def test_unreviewed_or_missing_employment_alias_is_pending_not_raw_failure(self) -> None:
        employment_parser = _EmploymentParser(
            self.events, self.lock_state, no_reviewed_alias=True
        )
        runner, raw_publisher, _, employment_publisher, transport = self._runner(
            employment_parser=employment_parser
        )

        report = runner.run()

        self.assertEqual(report.requested, 56)
        self.assertEqual(report.employment_normalization_pending, 56)
        self.assertEqual(report.employment_recognized_events, 0)
        self.assertEqual(len(raw_publisher.calls), 56)
        self.assertEqual(len(employment_publisher.calls), 0)
        self.assertEqual(len(transport.calls), 56)

    def test_complete_raw_plan_replays_locally_without_credential_or_network(self) -> None:
        runner, raw_publisher, _, _, transport = self._runner()

        initial = runner.run()
        replay = runner.run()

        self.assertEqual(initial.requested, 56)
        self.assertEqual(
            (replay.requested, replay.replayed_windows, replay.published, replay.written_rows),
            (0, 56, 0, 0),
        )
        self.assertEqual(len(transport.calls), 56)
        self.assertEqual(len(self.credential.calls), 1)
        self.assertEqual(len(raw_publisher.calls), 56)

    def test_nonplan_raw_checkpoint_fails_before_credential_or_transport(self) -> None:
        raw_publisher = _RawPublisher(self.events, self.lock_state)
        raw_publisher._completed[("2000-01-01", "2000-03-31")] = _RawCapture(
            b"[]", "2026-08-18T12:00:00Z", "2000-01-01", "2000-03-31"
        )
        runner, _, _, _, transport = self._runner(raw_publisher=raw_publisher)

        with self.assertRaises(ConflictError):
            runner.run()

        self.assertEqual(self.credential.calls, [])
        self.assertEqual(transport.calls, [])


    def test_canonical_domain_factory_wiring_is_lazy_and_has_no_store_or_network_work(self) -> None:
        registry = object()
        with (
            patch.object(operation, "_require_canonical_target") as require_target,
            patch.object(operation, "load_registry", return_value=registry) as load_registry,
        ):
            raw_parser, raw_factory, employment_parser, employment_factory = (
                operation._domain_api()
            )

        require_target.assert_called_once_with()
        load_registry.assert_called_once_with(
            operation.PROJECT_ROOT / operation.CANONICAL_REGISTRY_PATH,
            project_root=operation.PROJECT_ROOT,
            environment={},
        )
        self.assertTrue(callable(raw_parser))
        self.assertTrue(callable(raw_factory))
        self.assertTrue(callable(employment_parser))
        self.assertTrue(callable(employment_factory))

    def test_zero_arg_canonical_entrypoint_is_injected_and_does_not_transport(self) -> None:
        raw_parser = object()
        raw_factory = object()
        employment_parser = object()
        employment_factory = object()
        transport = object()
        expected = operation.FmpWholesaleCalendarHistoryReport(
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0
        )
        captured: dict[str, object] = {}

        class _Runner:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def run(self) -> operation.FmpWholesaleCalendarHistoryReport:
                return expected

        with (
            patch.object(
                operation,
                "_domain_api",
                return_value=(raw_parser, raw_factory, employment_parser, employment_factory),
            ),
            patch.object(operation, "FmpWholesaleCalendarHistoryRunner", _Runner),
            patch.object(operation, "_StdlibTransport", return_value=transport),
        ):
            actual = operation.populate_fmp_us_calendar_wholesale_history_live()

        self.assertIs(actual, expected)
        self.assertEqual(captured["project_root"], operation.PROJECT_ROOT)
        self.assertEqual(captured["macro_store"], operation.MACRO_STORE)
        self.assertIs(captured["parser"], raw_parser)
        self.assertIs(captured["publisher_factory"], raw_factory)
        self.assertIs(captured["employment_parser"], employment_parser)
        self.assertIs(captured["employment_publisher_factory"], employment_factory)
        self.assertIs(captured["transport"], transport)
        self.assertIs(captured["credential_environment"], operation.os.environ)
        self.assertTrue(captured["_canonical"])

    def test_cli_accepts_no_arguments_and_rejects_extra_arguments_safely(self) -> None:
        report = operation.FmpWholesaleCalendarHistoryReport(
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0
        )
        output = StringIO()
        with patch.object(
            operation,
            "populate_fmp_us_calendar_wholesale_history_live",
            return_value=report,
        ):
            with redirect_stdout(output):
                self.assertEqual(operation.main([]), 0)
        self.assertIn('"requested":0', output.getvalue())

        error = StringIO()
        with patch.object(
            operation,
            "populate_fmp_us_calendar_wholesale_history_live",
        ) as populate:
            with redirect_stderr(error):
                self.assertEqual(operation.main(["--history"]), 64)
        populate.assert_not_called()
        self.assertIn('"error":"invalid_request"', error.getvalue())


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
