from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import ValidationError
from quant_data.operations import nyfed_soma_history as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


FIXED_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    body: bytes
    captured_at: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class _Publication:
    published_releases: int
    unchanged_releases: int
    written_components: int
    first_as_of_date: str
    last_as_of_date: str


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
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _Publication:
        if not isinstance(capture, _Capture):
            raise AssertionError("unexpected capture")
        self.events.append("publish")
        self.calls.append(capture)
        return _Publication(2, 0, 18, "2003-07-09", "2026-08-19")


class _Transport:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=b'{"soma":{"summary":[]}}',
        )


class NyFedSomaHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.parser = _Parser(self.events)
        self.publisher = _Publisher(self.events)
        self.transport = _Transport(self.events)

        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return self.publisher

        self.runner = operation.NyFedSomaHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.parser,
            publisher_factory=publisher_factory,
            transport=self.transport,
            utcnow=lambda: FIXED_NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_one_credential_free_request_parses_before_publish(self) -> None:
        report = self.runner.run(
            start_date="2003-07-09",
            end_date="2026-08-21",
        )

        self.assertEqual(
            report.mapping(),
            {
                "requested": 1,
                "published_releases": 2,
                "unchanged_releases": 0,
                "written_components": 18,
                "start_date": "2003-07-09",
                "end_date": "2026-08-21",
                "first_as_of_date": "2003-07-09",
                "last_as_of_date": "2026-08-19",
            },
        )
        self.assertEqual(
            self.events,
            ["publisher_factory", "transport", "parser", "publish"],
        )
        self.assertEqual(len(self.transport.calls), 1)
        call = self.transport.calls[0]
        self.assertEqual(call["url"], operation.NYFED_SOMA_URL)
        self.assertEqual(call["parameters"], {})
        self.assertEqual(
            call["headers"],
            {"Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"},
        )
        self.assertEqual(call["timeout_seconds"], 60)
        self.assertEqual(call["max_bytes"], 16 * 1024 * 1024)
        self.assertEqual(
            self.parser.calls[0].captured_at,
            "2026-08-22T12:00:00.000000Z",
        )

    def test_invalid_window_stops_before_request(self) -> None:
        with self.assertRaises(ValidationError):
            self.runner.run(
                start_date="1990-01-01",
                end_date="2026-08-21",
            )
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(self.publisher.calls, [])


if __name__ == "__main__":
    unittest.main()
