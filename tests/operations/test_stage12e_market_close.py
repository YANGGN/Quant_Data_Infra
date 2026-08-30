from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from quant_data.errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.market.stage12_incremental import Stage12BPublicationReceipt
from quant_data.operations.stage12e_market_close import (
    MAX_SYMBOL_COUNT,
    Stage12EMarketCloseRunner,
    Stage12ETransportResponse,
    _ScheduledInstrument,
    _scheduled_universe_from_rows,
    scheduled_session_date,
)


def _body(symbol: str, session: str) -> bytes:
    return json.dumps(
        [
            {
                "symbol": symbol,
                "date": session,
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 1000,
                "change": 1,
                "changePercent": 1,
                "vwap": 100,
            }
        ],
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass
class _Clock:
    monotonic_value: float = 0.0
    now_value: datetime = datetime(2026, 8, 17, 22, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def sleep(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.now_value += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.now_value


class _Environment(dict[str, str]):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.reads = 0

    def get(self, key: str, default: object = None) -> object:
        self.reads += 1
        return super().get(key, default)


class _Collector:
    def __init__(self, market_store: Path) -> None:
        self.market_store = market_store
        self.prepared: list[tuple[object, object]] = []

    def prepare(self, request: object, response: object) -> tuple[object, object]:
        self.prepared.append((request, response))
        return request, response

    def publish(self, prepared: tuple[object, object]) -> Stage12BPublicationReceipt:
        request, _ = prepared
        symbol = str(getattr(request, "symbol"))
        return Stage12BPublicationReceipt(
            outcome="published",
            semantic_identity=("a" * 63) + str(len(self.prepared) % 10),
            capture_id=f"capture-{symbol}",
            written_versions=1,
        )


class _Transport:
    def __init__(
        self,
        session: str,
        responses: dict[str, Stage12ETransportResponse] | None = None,
        *,
        fail: bool = False,
        clock: _Clock | None = None,
    ) -> None:
        self.session = session
        self.responses = responses or {}
        self.fail = fail
        self.clock = clock
        self.calls: list[str] = []
        self.starts: list[float] = []

    def get(
        self,
        *,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12ETransportResponse:
        self.calls.append(query["symbol"])
        if self.clock is not None:
            self.starts.append(self.clock.monotonic())
        self.assert_request(path, query, headers, timeout_seconds, max_bytes)
        if self.fail:
            raise StoreUnavailableError("injected transport failure")
        return self.responses.get(
            query["symbol"],
            Stage12ETransportResponse(
                status=200,
                media_type="application/json; charset=utf-8",
                body=_body(query["symbol"], self.session),
            ),
        )

    def assert_request(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> None:
        if path != "/stable/historical-price-eod/full":
            raise AssertionError("unexpected endpoint")
        if query["from"] != self.session or query["to"] != self.session:
            raise AssertionError("unexpected session")
        if set(headers) != {"apikey"} or headers["apikey"] != "fixture-key":
            raise AssertionError("unexpected credential")
        if timeout_seconds != 45 or max_bytes != 65_536:
            raise AssertionError("unexpected bounds")


class Stage12EMarketCloseTests(unittest.TestCase):
    session = "2026-08-17"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        (self.project / "data").mkdir(parents=True)
        self.market = self.project / "data" / "market.sqlite"
        self.market.write_bytes(b"fixture-market")
        self.state = self.project / "data" / ".stage12e"
        self.clock = _Clock()
        self.environment = _Environment(FMP_API_KEY="fixture-key")
        self.collector = _Collector(self.market)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        transport: _Transport,
        *,
        symbols: tuple[str, ...] = ("AAPL", "MSFT"),
        environment: _Environment | None = None,
        scheduled_instruments: tuple[_ScheduledInstrument, ...] | None = None,
        universe_sha256: str | None = None,
    ) -> Stage12EMarketCloseRunner:
        return Stage12EMarketCloseRunner(
            project_root=self.project,
            market_store=self.market,
            state_root=self.state,
            session_date=self.session,
            symbols=symbols,
            scheduled_instruments=scheduled_instruments,
            universe_sha256=universe_sha256,
            collector=self.collector,  # type: ignore[arg-type]
            transport=transport,
            environment=environment or self.environment,
            monotonic=self.clock.monotonic,
            sleeper=self.clock.sleep,
            utcnow=self.clock.now,
        )

    def test_complete_cycle_is_aapl_first_paced_and_replay_is_credential_free(self) -> None:
        transport = _Transport(self.session, clock=self.clock)
        first = self._runner(transport).run()
        self.assertEqual(transport.calls, ["AAPL", "MSFT"])
        self.assertEqual(transport.starts, [0.0, 1.0])
        self.assertEqual(
            (first.outcome, first.published, first.requests_issued),
            ("complete", 2, 2),
        )
        self.assertTrue((self.state / self.session / "completion.json").is_file())
        prior_reads = self.environment.reads

        replay_transport = _Transport(self.session, fail=True, clock=self.clock)
        replay = self._runner(replay_transport).run()
        self.assertEqual(replay.requests_issued, 0)
        self.assertEqual(replay.receipt_sha256, first.receipt_sha256)
        self.assertEqual(replay_transport.calls, [])
        self.assertEqual(self.environment.reads, prior_reads)

    def test_scheduled_universe_binds_current_identity_digest_into_plan(self) -> None:
        universe = _scheduled_universe_from_rows(
            (
                {
                    "provider_symbol": "MSFT",
                    "instrument_id": "instrument-msft",
                    "asset_type": "equity",
                },
                {
                    "provider_symbol": "AAPL",
                    "instrument_id": "instrument-aapl",
                    "asset_type": "equity",
                },
                {
                    "provider_symbol": "IWM",
                    "instrument_id": "instrument-iwm",
                    "asset_type": "etf",
                },
                {
                    "provider_symbol": "^VIX",
                    "instrument_id": "instrument-vix",
                    "asset_type": "index",
                },
            )
        )
        symbols = tuple(item.symbol for item in universe.instruments)
        report = self._runner(
            _Transport(self.session, clock=self.clock),
            symbols=symbols,
            scheduled_instruments=universe.instruments,
            universe_sha256=universe.sha256,
        ).run()

        plan = json.loads((self.state / self.session / "plan.json").read_text())
        self.assertEqual((report.total_units, plan["universe_sha256"]), (4, universe.sha256))
        self.assertEqual(
            [(item["symbol"], item["instrument_id"], item["asset_type"]) for item in plan["units"]],
            [
                ("AAPL", "instrument-aapl", "equity"),
                ("IWM", "instrument-iwm", "etf"),
                ("MSFT", "instrument-msft", "equity"),
                ("^VIX", "instrument-vix", "index"),
            ],
        )

    def test_scheduled_universe_enforces_its_dynamic_bound(self) -> None:
        rows = (
            {
                "provider_symbol": "AAPL",
                "instrument_id": "instrument-aapl",
                "asset_type": "equity",
            },
        ) + tuple(
            {
                "provider_symbol": f"T{index:04d}",
                "instrument_id": f"instrument-{index:04d}",
                "asset_type": "equity",
            }
            for index in range(MAX_SYMBOL_COUNT)
        )
        with self.assertRaises(ResourceLimitError):
            _scheduled_universe_from_rows(rows)

    def test_empty_aapl_closes_no_market_session_without_later_requests(self) -> None:
        transport = _Transport(
            self.session,
            responses={
                "AAPL": Stage12ETransportResponse(
                    status=200,
                    media_type="application/json",
                    body=b"[]",
                )
            },
            clock=self.clock,
        )
        report = self._runner(transport).run()
        self.assertEqual(report.outcome, "no_market_session")
        self.assertEqual(report.requests_issued, 1)
        self.assertEqual(transport.calls, ["AAPL"])
        self.assertEqual(self.collector.prepared, [])

    def test_pinned_terminal_responses_complete(self) -> None:
        report = self._runner(
            _Transport(
                self.session,
                responses={
                    "EA": Stage12ETransportResponse(
                        status=200,
                        media_type="application/json",
                        body=b"[]",
                    ),
                    "^NDX": Stage12ETransportResponse(
                        status=402,
                        media_type="application/json",
                        body=b'{"Error Message":"payment required"}',
                    ),
                },
                clock=self.clock,
            ),
            symbols=("AAPL", "EA", "^NDX"),
        ).run()
        self.assertEqual(
            (report.outcome, report.published, report.terminal_noncoverage),
            ("complete", 1, 2),
        )

    def test_pinned_terminal_402_rejects_control_characters(self) -> None:
        with self.assertRaises(ValidationError):
            self._runner(
                _Transport(
                    self.session,
                    responses={
                        "^NDX": Stage12ETransportResponse(
                            status=402,
                            media_type="application/json",
                            body=b'{"Error Message":"payment\\u0000required"}',
                        )
                    },
                    clock=self.clock,
                ),
                symbols=("AAPL", "^NDX"),
            ).run()

    def test_unknown_empty_response_fails_closed(self) -> None:
        with self.assertRaises(StoreUnavailableError):
            self._runner(
                _Transport(
                    self.session,
                    responses={
                        "MSFT": Stage12ETransportResponse(
                            status=200, media_type="application/json", body=b"[]"
                        )
                    },
                    clock=self.clock,
                ),
                symbols=("AAPL", "MSFT"),
            ).run()

    def test_systemic_response_records_failure_and_never_retries(self) -> None:
        response = Stage12ETransportResponse(
            status=402,
            media_type="application/json; charset=utf-8",
            body=b'{"Error Message":"payment required"}',
        )
        first_transport = _Transport(
            self.session,
            responses={"AAPL": response},
            clock=self.clock,
        )
        with self.assertRaises(StoreUnavailableError):
            self._runner(first_transport, symbols=("AAPL",)).run()
        self.assertEqual(first_transport.calls, ["AAPL"])
        prior_reads = self.environment.reads

        second_transport = _Transport(self.session, fail=True, clock=self.clock)
        with self.assertRaises(ConflictError):
            self._runner(second_transport, symbols=("AAPL",)).run()
        self.assertEqual(second_transport.calls, [])
        self.assertEqual(self.environment.reads, prior_reads)

    def test_transport_failure_leaves_ambiguous_intent_and_never_retries(self) -> None:
        first = _Transport(self.session, fail=True, clock=self.clock)
        with self.assertRaises(StoreUnavailableError):
            self._runner(first, symbols=("AAPL",)).run()
        self.assertEqual(first.calls, ["AAPL"])
        failure = json.loads(
            (self.state / self.session / "failure.json").read_text(encoding="utf-8")
        )
        self.assertEqual(failure["error"], "store_unavailable")
        self.assertIsNotNone(failure["plan_sha256"])
        self.assertTrue(str(failure["failed_at"]).endswith("Z"))
        self.assertNotIn("injected transport failure", json.dumps(failure))

        second = _Transport(self.session, clock=self.clock)
        with self.assertRaises(ConflictError):
            self._runner(second, symbols=("AAPL",)).run()
        self.assertEqual(second.calls, [])

    def test_schedule_date_requires_weekday_at_or_after_1800_new_york(self) -> None:
        zone = ZoneInfo("America/New_York")
        self.assertEqual(
            scheduled_session_date(datetime(2026, 8, 17, 18, 0, tzinfo=zone)),
            self.session,
        )
        for value in (
            datetime(2026, 8, 17, 17, 59, tzinfo=zone),
            datetime(2026, 8, 16, 18, 0, tzinfo=zone),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    scheduled_session_date(value)


if __name__ == "__main__":
    unittest.main()
