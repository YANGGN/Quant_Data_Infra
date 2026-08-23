from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.operations import nyfed_overnight_rates_history as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


FIXED_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    body: bytes
    captured_at: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class _Publication:
    outcome: str
    written_series: int
    written_observation_versions: int


class _Parser:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[_Capture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _Capture:
        self.events.append("parser")
        capture = _Capture(body, captured_at, start_date, end_date)
        self.calls.append(capture)
        return capture


class _Publisher:
    def __init__(self, events: list[str], *, outcome: str = "published") -> None:
        self.events = events
        self.outcome = outcome
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _Publication:
        if not isinstance(capture, _Capture):
            raise AssertionError("unexpected capture")
        self.events.append("publish")
        self.calls.append(capture)
        unchanged = self.outcome == "unchanged"
        return _Publication(
            self.outcome,
            0 if unchanged else 14,
            0 if unchanged else 24,
        )


class _Transport:
    def __init__(
        self,
        events: list[str],
        *,
        response: FmpMacroCalendarTransportResponse | None = None,
    ) -> None:
        self.events = events
        self.response = response or FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=b'{"refRates":[]}',
        )
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return self.response


class NyFedOvernightRatesHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.parser = _Parser(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        publisher: _Publisher | None = None,
        transport: _Transport | None = None,
    ):
        selected_publisher = publisher or _Publisher(self.events)
        selected_transport = transport or _Transport(self.events)

        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return selected_publisher

        return (
            operation.NyFedOvernightRatesHistoryRunner(
                project_root=self.project,
                macro_store=self.target,
                parser=self.parser,
                publisher_factory=publisher_factory,
                transport=selected_transport,
                utcnow=lambda: FIXED_NOW,
            ),
            selected_publisher,
            selected_transport,
        )

    def test_one_credential_free_request_parses_before_publish(self) -> None:
        runner, publisher, transport = self._runner()

        report = runner.run(start_date="2016-03-01", end_date="2026-08-21")

        self.assertEqual(
            report.mapping(),
            {
                "requested": 1,
                "published": 1,
                "unchanged": 0,
                "written_series": 14,
                "written_observation_versions": 24,
                "start_date": "2016-03-01",
                "end_date": "2026-08-21",
            },
        )
        self.assertEqual(
            self.events,
            ["publisher_factory", "transport", "parser", "publish"],
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(publisher.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], operation.NYFED_OVERNIGHT_RATES_URL)
        self.assertEqual(
            call["parameters"],
            {
                "startDate": "2016-03-01",
                "endDate": "2026-08-21",
            },
        )
        self.assertEqual(
            call["headers"],
            {"Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"},
        )
        self.assertEqual(call["timeout_seconds"], 60)
        self.assertEqual(call["max_bytes"], 16 * 1024 * 1024)
        self.assertEqual(
            self.parser.calls[0].captured_at,
            "2026-08-21T12:00:00.000000Z",
        )

    def test_unchanged_is_explicit_and_still_one_request(self) -> None:
        publisher = _Publisher(self.events, outcome="unchanged")
        runner, _, transport = self._runner(publisher=publisher)

        report = runner.run(start_date=date(2026, 8, 21), end_date=date(2026, 8, 21))

        self.assertEqual(
            (
                report.requested,
                report.published,
                report.unchanged,
                report.written_series,
                report.written_observation_versions,
            ),
            (1, 0, 1, 0, 0),
        )
        self.assertEqual(len(transport.calls), 1)

    def test_invalid_window_and_provider_response_stop_before_parse(self) -> None:
        runner, _, transport = self._runner()
        with self.assertRaises(ValidationError):
            runner.run(start_date="2010-01-01", end_date="2026-08-21")
        self.assertEqual(transport.calls, [])

        redirect = _Transport(
            self.events,
            response=FmpMacroCalendarTransportResponse(
                status=302,
                media_type="application/json",
                body=b'{"refRates":[]}',
                redirected=True,
            ),
        )
        runner, publisher, _ = self._runner(transport=redirect)
        with self.assertRaises(StoreUnavailableError):
            runner.run(start_date="2026-08-21", end_date="2026-08-21")
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_target_binding_rejects_alternate_store(self) -> None:
        alternate = self.project / "other.sqlite"
        alternate.write_bytes(b"fixture alternate store")
        with self.assertRaises(ValidationError):
            operation.NyFedOvernightRatesHistoryRunner(
                project_root=self.project,
                macro_store=alternate,
                parser=self.parser,
                publisher_factory=lambda: _Publisher(self.events),
                transport=_Transport(self.events),
            )


if __name__ == "__main__":
    unittest.main()
