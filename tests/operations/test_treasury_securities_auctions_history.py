"""Focused offline tests for the bounded Treasury auction operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.macro.treasury_securities_auctions import (
    parse_treasury_securities_auctions,
)
from quant_data.operations import treasury_securities_auctions_history as operation
from quant_data.operations.official_conditions_history import CsvResponse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/treasury_securities_auctions.json"
FIXED_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


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
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_Capture] = []

    def __call__(
        self, body: bytes, *, captured_at: str, start_date: str, end_date: str
    ) -> _Capture:
        if self.lock_state["held"]:
            raise AssertionError("parser ran while writer lock was held")
        self.events.append("parser")
        capture = _Capture(body, captured_at, start_date, end_date)
        self.calls.append(capture)
        return capture


class _Publisher:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _Publication:
        if not isinstance(capture, _Capture):
            raise AssertionError("unexpected capture")
        self.lock_state["held"] = True
        try:
            self.events.append("publish")
            self.calls.append(capture)
            return _Publication("published", 11, 33)
        finally:
            self.lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        response: CsvResponse,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> CsvResponse:
        if self.lock_state["held"]:
            raise AssertionError("network ran while writer lock was held")
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return self.response


class TreasurySecuritiesAuctionsHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.lock_state = {"held": False}
        self.parser = _Parser(self.events, self.lock_state)
        self.publisher = _Publisher(self.events, self.lock_state)
        self.transport = _Transport(
            self.events,
            self.lock_state,
            CsvResponse(
                status=200,
                media_type="application/json",
                body=FIXTURE.read_bytes(),
                redirected=False,
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(self, *, parser: object | None = None):
        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return self.publisher

        return operation.TreasurySecuritiesAuctionsHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.parser if parser is None else parser,
            publisher_factory=publisher_factory,
            transport=self.transport,
            utcnow=lambda: FIXED_NOW,
        )

    def test_one_bounded_request_parses_before_publish(self) -> None:
        report = self._runner().run(
            start_date="2026-08-20", end_date="2026-08-21"
        )

        self.assertEqual(
            report.mapping(),
            {
                "requested": 1,
                "published": 1,
                "unchanged": 0,
                "written_series": 11,
                "written_observation_versions": 33,
                "start_date": "2026-08-20",
                "end_date": "2026-08-21",
            },
        )
        self.assertEqual(
            self.events, ["transport", "parser", "publisher_factory", "publish"]
        )
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(len(self.parser.calls), 1)
        self.assertEqual(len(self.publisher.calls), 1)
        call = self.transport.calls[0]
        parsed = urlparse(str(call["url"]))
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.fiscaldata.treasury.gov")
        self.assertEqual(
            query["filter"],
            ["auction_date:gte:2026-08-20,auction_date:lte:2026-08-21"],
        )
        self.assertEqual(
            query["fields"],
            [
                "record_date,cusip,auction_date,security_type,security_term,"
                "issue_date,reopening,auction_format,offering_amt,total_tendered,"
                "total_accepted,high_yield,high_discnt_rate,high_discnt_margin,"
                "bid_to_cover_ratio,direct_bidder_accepted,"
                "indirect_bidder_accepted,primary_dealer_accepted,soma_accepted"
            ],
        )
        self.assertEqual(query["page[size]"], ["1000"])
        self.assertEqual(query["sort"], ["auction_date,cusip"])
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertEqual(call["timeout_seconds"], 60)
        self.assertEqual(call["max_bytes"], 16 * 1024 * 1024)
        self.assertEqual(
            self.parser.calls[0].captured_at, "2026-08-22T12:00:00.000000Z"
        )
        self.assertFalse(self.lock_state["held"])

    def test_invalid_window_stops_before_request(self) -> None:
        runner = self._runner()
        with self.assertRaises(ValidationError):
            runner.run(start_date="2025-01-01", end_date="2026-08-21")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(self.publisher.calls, [])

    def test_bad_provider_response_never_reaches_parser_or_publisher(self) -> None:
        self.transport.response = CsvResponse(
            status=302,
            media_type="application/json",
            body=b"{}",
            redirected=True,
        )
        with self.assertRaises(StoreUnavailableError):
            self._runner().run(start_date="2026-08-20", end_date="2026-08-21")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(self.publisher.calls, [])

    def test_multipage_provider_result_fails_before_publish(self) -> None:
        body = FIXTURE.read_bytes().replace(
            b'"total-pages": "1"', b'"total-pages": "2"'
        )
        self.transport.response = CsvResponse(
            status=200,
            media_type="application/json",
            body=body,
            redirected=False,
        )
        with self.assertRaisesRegex(ValidationError, "one complete page"):
            self._runner(parser=parse_treasury_securities_auctions).run(
                start_date="2026-08-20", end_date="2026-08-21"
            )
        self.assertEqual(self.publisher.calls, [])


if __name__ == "__main__":
    unittest.main()
