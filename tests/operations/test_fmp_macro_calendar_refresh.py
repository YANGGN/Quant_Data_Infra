from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.operations import fmp_macro_calendar_refresh as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime(2026, 8, 18, 12, 15, tzinfo=timezone.utc)
SECRET = "fixture-fmp-key"


@dataclass(frozen=True, slots=True)
class _Capture:
    body: bytes
    captured_at: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class _PublishReport:
    outcome: str
    semantic_identity: str
    written_versions: int


@dataclass(frozen=True, slots=True)
class _WholesaleCapture:
    response_bytes: bytes
    captured_at: str
    request_start_date: str
    request_end_date: str


@dataclass(frozen=True, slots=True)
class _WholesalePublishReport:
    outcome: str
    semantic_identity: str
    capture_id: str
    written_captures: int
    written_rows: int


class _WholesaleParser:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_WholesaleCapture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _WholesaleCapture:
        if self.lock_state["held"]:
            raise AssertionError("wholesale parser ran while publisher lock was held")
        self.events.append("wholesale_parser")
        capture = _WholesaleCapture(body, captured_at, start_date, end_date)
        self.calls.append(capture)
        return capture


class _WholesalePublisher:
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
        self.calls: list[_WholesaleCapture] = []
        self._latest: dict[tuple[str, str], _WholesaleCapture] = {}

    def publish(self, capture: object) -> _WholesalePublishReport:
        if not isinstance(capture, _WholesaleCapture):
            raise AssertionError("wholesale publisher received an invalid capture")
        self.lock_state["held"] = True
        try:
            self.events.append("wholesale_publish")
            self.calls.append(capture)
            if self.failures:
                self.failures -= 1
                raise StoreUnavailableError("fixture wholesale publisher failure")
            self._latest[(capture.request_start_date, capture.request_end_date)] = capture
            return _WholesalePublishReport(
                self.outcome,
                "fixture-wholesale-semantic-identity",
                "fixture-wholesale-capture",
                0 if self.outcome == "unchanged" else 1,
                0 if self.outcome == "unchanged" else 2,
            )
        finally:
            self.lock_state["held"] = False

    def load_latest_window(
        self, start_date: str, end_date: str
    ) -> _WholesaleCapture | None:
        self.events.append("wholesale_load")
        return self._latest.get((start_date, end_date))


class _Parser:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_Capture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _Capture:
        if self.lock_state["held"]:
            raise AssertionError("parser ran while publisher lock was held")
        self.events.append("parser")
        capture = _Capture(body, captured_at, start_date, end_date)
        self.calls.append(capture)
        return capture


class _Publisher:
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
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _PublishReport:
        if not isinstance(capture, _Capture):
            raise AssertionError("publisher received an invalid capture")
        self.lock_state["held"] = True
        try:
            self.events.append("publish")
            self.calls.append(capture)
            if self.failures:
                self.failures -= 1
                raise StoreUnavailableError("fixture publisher failure")
            return _PublishReport(
                self.outcome,
                "fixture-semantic-identity",
                0 if self.outcome == "unchanged" else 1,
            )
        finally:
            self.lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        response: FmpMacroCalendarTransportResponse | None = None,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.response = response or FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=b"[]",
        )
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        if self.lock_state["held"]:
            raise AssertionError("network ran while publisher lock was held")
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return self.response


class FmpMacroCalendarRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.lock_state = {"held": False}
        self.parser = _Parser(self.events, self.lock_state)
        self.wholesale_parser = _WholesaleParser(self.events, self.lock_state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        publisher: _Publisher | None = None,
        wholesale_parser: object | None = None,
        wholesale_publisher: _WholesalePublisher | None = None,
        employment_parser: object | None = None,
        employment_publisher: _Publisher | None = None,
        transport: _Transport | None = None,
        credential_reader: object | None = None,
        now: datetime = FIXED_NOW,
    ) -> tuple[operation.FmpMacroCalendarRefreshRunner, _Publisher, _Publisher, _Transport]:
        selected_publisher = publisher or _Publisher(self.events, self.lock_state)
        selected_wholesale_parser = wholesale_parser or self.wholesale_parser
        selected_wholesale_publisher = wholesale_publisher or _WholesalePublisher(
            self.events, self.lock_state
        )
        self.wholesale_publisher = selected_wholesale_publisher
        selected_employment_parser = employment_parser or self.parser
        selected_employment_publisher = employment_publisher or _Publisher(
            self.events, self.lock_state
        )
        selected_transport = transport or _Transport(self.events, self.lock_state)

        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return selected_publisher

        def wholesale_publisher_factory() -> _WholesalePublisher:
            self.events.append("wholesale_publisher_factory")
            return selected_wholesale_publisher

        def employment_publisher_factory() -> _Publisher:
            self.events.append("employment_publisher_factory")
            return selected_employment_publisher

        def default_credential_reader(**kwargs: object) -> str:
            self.events.append("credential")
            self.assertEqual(kwargs["name"], "FMP_API_KEY")
            return SECRET

        runner = operation.FmpMacroCalendarRefreshRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.parser,
            publisher_factory=publisher_factory,
            wholesale_parser=selected_wholesale_parser,
            wholesale_publisher_factory=wholesale_publisher_factory,
            employment_parser=selected_employment_parser,
            employment_publisher_factory=employment_publisher_factory,
            transport=selected_transport,
            credential_environment={"FMP_API_KEY": "not-read-directly"},
            credential_reader=credential_reader or default_credential_reader,
            utcnow=lambda: now,
        )
        return runner, selected_publisher, selected_employment_publisher, selected_transport

    def test_one_request_uses_stable_first_block_and_redacts_key(self) -> None:
        employment_parser = _Parser(self.events, self.lock_state)
        runner, publisher, employment_publisher, transport = self._runner(
            employment_parser=employment_parser
        )

        report = runner.run()

        self.assertEqual(
            report.mapping(),
            {
                "requested": 1,
                "published": 1,
                "unchanged": 0,
                "written_versions": 1,
                "start_date": "2026-08-18",
                "end_date": "2026-11-15",
                "employment_outcome": "published",
                "employment_written_versions": 1,
                "wholesale_outcome": "published",
                "wholesale_written_rows": 2,
            },
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0]["parameters"],
            {"country": "US", "from": "2026-08-18", "to": "2026-11-15"},
        )
        self.assertEqual(transport.calls[0]["headers"]["apikey"], SECRET)
        self.assertNotIn("apikey", transport.calls[0]["parameters"])
        self.assertEqual(len(self.parser.calls), 1)
        self.assertEqual(len(employment_parser.calls), 1)
        self.assertEqual(len(self.wholesale_parser.calls), 1)
        self.assertEqual(len(self.wholesale_publisher.calls), 1)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(len(employment_publisher.calls), 1)
        self.assertEqual(
            self.events,
            [
                "wholesale_publisher_factory",
                "credential",
                "transport",
                "wholesale_parser",
                "wholesale_publish",
                "parser",
                "parser",
                "publisher_factory",
                "employment_publisher_factory",
                "publish",
                "publish",
            ],
        )
        self.assertNotIn(SECRET, str(report.mapping()))
        self.assertFalse(self.lock_state["held"])

    def test_block_is_stable_then_rolls_forward_without_history_overlap(self) -> None:
        same = operation.build_fmp_macro_calendar_refresh_window(
            datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)
        )
        following = operation.build_fmp_macro_calendar_refresh_window(
            datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
        )
        self.assertEqual((same.start_date.isoformat(), same.end_date.isoformat()), ("2026-08-18", "2026-11-15"))
        self.assertEqual((following.start_date.isoformat(), following.end_date.isoformat()), ("2026-11-16", "2027-02-13"))
        self.assertEqual((same.start_date - operation.REFRESH_ANCHOR_DATE).days, 0)
        self.assertEqual((following.start_date - same.end_date).days, 1)
        with self.assertRaises(ValidationError):
            operation.build_fmp_macro_calendar_refresh_window(
                datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
            )

    def test_unchanged_report_is_zero_write(self) -> None:
        publisher = _Publisher(self.events, self.lock_state, outcome="unchanged")
        employment_publisher = _Publisher(self.events, self.lock_state, outcome="unchanged")
        wholesale_publisher = _WholesalePublisher(
            self.events, self.lock_state, outcome="unchanged"
        )
        report = self._runner(
            publisher=publisher,
            wholesale_publisher=wholesale_publisher,
            employment_publisher=employment_publisher,
        )[0].run()
        self.assertEqual((report.published, report.unchanged, report.written_versions), (0, 1, 0))
        self.assertEqual((report.employment_outcome, report.employment_written_versions), ("unchanged", 0))
        self.assertEqual((report.wholesale_outcome, report.wholesale_written_rows), ("unchanged", 0))

    def test_failure_is_one_attempt_and_never_publishes(self) -> None:
        bad = FmpMacroCalendarTransportResponse(
            status=503,
            media_type="application/json",
            body=b"{}",
        )
        transport = _Transport(self.events, self.lock_state, response=bad)
        runner, publisher, employment_publisher, _ = self._runner(transport=transport)
        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(self.wholesale_parser.calls, [])
        self.assertEqual(self.wholesale_publisher.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertEqual(employment_publisher.calls, [])

    def test_credential_failure_makes_no_request(self) -> None:
        def failure(**kwargs: object) -> str:
            del kwargs
            raise ValidationError("Credential is missing or invalid")

        runner, publisher, employment_publisher, transport = self._runner(credential_reader=failure)
        with self.assertRaises(ValidationError):
            runner.run()
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.wholesale_publisher.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertEqual(employment_publisher.calls, [])

    def test_wholesale_publish_failure_prevents_normalized_publication(self) -> None:
        wholesale_publisher = _WholesalePublisher(
            self.events, self.lock_state, failures=1
        )
        runner, publisher, employment_publisher, transport = self._runner(
            wholesale_publisher=wholesale_publisher
        )
        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.wholesale_parser.calls), 1)
        self.assertEqual(len(wholesale_publisher.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertEqual(employment_publisher.calls, [])

    def test_employment_parser_failure_keeps_raw_evidence(self) -> None:
        def failure(
            body: bytes,
            *,
            captured_at: str,
            start_date: str,
            end_date: str,
        ) -> _Capture:
            del body, captured_at, start_date, end_date
            raise ValidationError("fixture employment parser failure")

        runner, publisher, employment_publisher, transport = self._runner(
            employment_parser=failure
        )
        with self.assertRaises(ValidationError):
            runner.run()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.parser.calls), 1)
        self.assertEqual(len(self.wholesale_publisher.calls), 1)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(employment_publisher.calls, [])

    def test_normalization_failure_replays_from_raw_without_network(self) -> None:
        employment_publisher = _Publisher(
            self.events, self.lock_state, failures=1
        )
        runner, publisher, _, transport = self._runner(
            employment_publisher=employment_publisher
        )
        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.wholesale_publisher.calls), 1)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(len(employment_publisher.calls), 1)

        publisher.outcome = "unchanged"
        report = runner.replay_latest_wholesale_window()

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.requested, 0)
        self.assertEqual((report.wholesale_outcome, report.wholesale_written_rows), ("replayed", 0))
        self.assertEqual(
            (report.published, report.unchanged, report.written_versions),
            (0, 1, 0),
        )
        self.assertEqual(
            (report.employment_outcome, report.employment_written_versions),
            ("published", 1),
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.wholesale_publisher.calls), 1)
        self.assertEqual(len(self.parser.calls), 4)
        self.assertEqual(len(publisher.calls), 2)
        self.assertEqual(len(employment_publisher.calls), 2)

    def test_cli_and_systemd_units_are_bounded(self) -> None:
        report = operation.FmpMacroCalendarRefreshReport(
            1, 0, 1, 0, operation.REFRESH_ANCHOR_DATE,
            operation.REFRESH_ANCHOR_DATE,
        )
        output = StringIO()
        with patch.object(operation, "refresh_fmp_macro_calendar_live", return_value=report):
            with redirect_stdout(output):
                self.assertEqual(operation.main([]), 0)
        self.assertIn('"requested":1', output.getvalue())
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(operation.main(["--history"]), 2)
        self.assertIn("invalid_arguments", error.getvalue())

        service = (PROJECT_ROOT / "deploy/systemd/quant-data-fmp-macro-calendar.service").read_text()
        timer = (PROJECT_ROOT / "deploy/systemd/quant-data-fmp-macro-calendar.timer").read_text()
        self.assertIn("quant_data.operations.fmp_macro_calendar_refresh", service)
        self.assertNotIn("fmp_macro_calendar_history", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=read-only", service)
        self.assertIn("ReadWritePaths=/home/volatility/Python_Projects/Quant_Data_Infra/data", service)
        self.assertNotIn("FMP_API_KEY=", service)
        self.assertEqual(timer.count("OnCalendar="), 2)
        self.assertIn("08:15:00 America/New_York", timer)
        self.assertIn("08:45:00 America/New_York", timer)
        self.assertIn("Persistent=false", timer)


if __name__ == "__main__":
    unittest.main()
