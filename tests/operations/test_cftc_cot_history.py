"""Focused offline tests for the bounded injected CFTC COT runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from quant_data.operations import cftc_cot_history as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


FIXED_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    pages: tuple[bytes, ...]
    family: str


@dataclass(frozen=True, slots=True)
class _Publication:
    outcome: str
    written_series: int
    written_observation_versions: int


class _Parser:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[_Capture] = []

    def __call__(self, pages: tuple[bytes, ...], *, report_family: str, **kwargs: object):
        del kwargs
        self.events.append("parser")
        capture = _Capture(pages, report_family)
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
        return _Publication("published", 1, 17)


class _Transport:
    def __init__(self, events: list[str], bodies: list[bytes]) -> None:
        self.events = events
        self.bodies = list(bodies)
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json",
            body=self.bodies.pop(0),
        )


class CftcCotHistoryRunnerTests(unittest.TestCase):
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

    def _runner(self, bodies: list[bytes]):
        publisher = _Publisher(self.events)
        transport = _Transport(self.events, bodies)

        def factory() -> _Publisher:
            self.events.append("publisher_factory")
            return publisher

        return (
            operation.CftcCotHistoryRunner(
                project_root=self.project,
                macro_store=self.target,
                parser=self.parser,
                publisher_factory=factory,
                transport=transport,
                utcnow=lambda: FIXED_NOW,
            ),
            publisher,
            transport,
        )

    def test_paginated_network_finishes_before_parser_and_publisher(self) -> None:
        full_page = b"[" + b",".join(b"{}" for _ in range(operation.PAGE_SIZE)) + b"]"
        runner, publisher, transport = self._runner([full_page, b"[{}]"])

        report = runner.run(
            report_family=operation.TFF_FUTURES_ONLY,
            start_date="2026-08-18",
            end_date="2026-08-18",
        )

        self.assertEqual(
            self.events,
            ["transport", "transport", "parser", "publisher_factory", "publish"],
        )
        self.assertEqual((report.published, report.empty, report.written_observation_versions), (1, 0, 17))
        self.assertEqual(len(self.parser.calls[0].pages), 2)
        self.assertEqual(transport.calls[0]["parameters"]["$offset"], "0")
        self.assertEqual(transport.calls[1]["parameters"]["$offset"], "500")
        self.assertEqual(
            transport.calls[0]["url"],
            operation.CFTC_COT_URL_BY_FAMILY[operation.TFF_FUTURES_ONLY],
        )
        self.assertEqual(len(publisher.calls), 1)

    def test_empty_response_is_not_a_tombstone_or_publish(self) -> None:
        runner, publisher, transport = self._runner([b"[]"])

        report = runner.run(
            report_family=operation.DISAGGREGATED_FUTURES_ONLY,
            start_date="2026-08-18",
            end_date="2026-08-18",
        )

        self.assertEqual((report.requested, report.empty, report.published), (1, 1, 0))
        self.assertEqual(self.events, ["transport"])
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertEqual(transport.calls[0]["max_bytes"], 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
