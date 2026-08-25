from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.alpaca_options import (
    ALPACA_ENVIRONMENT,
    ALPACA_REQUESTED_FEED,
    ALPACA_RESOLVED_FEED,
    AlpacaHttpResponse,
    AlpacaSpyOptionsPublisher,
    MAX_RESPONSE_BYTES,
    parse_alpaca_spy_options_capture,
    run_alpaca_spy_option_surface,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_CAPTURED_AT = "2026-08-03T19:55:00Z"
_CAPTURED = datetime(2026, 8, 3, 19, 55, tzinfo=timezone.utc)
_SESSION_DATE = "2026-08-03"
_SELECTED_EXPIRATION = "2026-09-02"
_STAGE10_SPY_ID = "alpaca_options_stage10_spy"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


class RecordingTransport:
    """Offline response queue that records the collector's fixed request plan."""

    def __init__(self, responses: list[AlpacaHttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> AlpacaHttpResponse:
        self.calls.append(dict(kwargs))
        if not self._responses:
            raise AssertionError("collector made an unplanned transport request")
        return self._responses.pop(0)


def _temporary_store_map(project_root: Path) -> StoreMap:
    data = project_root / "data"
    data.mkdir()
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


class AlpacaSpyOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.stores = _temporary_store_map(self.project_root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry)
        self._seed_stage10_spy()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_stage10_spy(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "alpaca_options_stage10_seed",
                    "market.stage10.instruments",
                    "0" * 64,
                    "fixture.seed",
                    "{}",
                    _CAPTURED_AT,
                    "fixture",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type, display_name,
                    exchange_code, currency_segment, first_trade_date,
                    identity_seed_sha256, captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', 'SPY', 'etf', 'SPDR S&P 500 ETF Trust', 'ARCX',
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                (
                    _STAGE10_SPY_ID,
                    "1" * 64,
                    _CAPTURED_AT,
                    "alpaca_options_stage10_seed",
                ),
            )

    @staticmethod
    def _response(body: bytes) -> AlpacaHttpResponse:
        return AlpacaHttpResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=body,
        )

    @staticmethod
    def _contract(
        *,
        contract_id: str,
        symbol: str,
        expiration: str,
        option_type: str,
        open_interest: str,
    ) -> dict[str, object]:
        return {
            "id": contract_id,
            "symbol": symbol,
            "underlying_symbol": "SPY",
            "status": "active",
            "expiration_date": expiration,
            "strike_price": "650",
            "type": option_type,
            "size": "100",
            "open_interest": open_interest,
            "open_interest_date": "2026-07-31",
            "close_price": "8.20",
            "close_price_date": "2026-07-31",
            "root_symbol": "SPY",
            "deliverables": [],
        }

    @classmethod
    def _bodies(cls) -> dict[str, bytes]:
        call_symbol = "SPY260902C00650000"
        put_symbol = "SPY260902P00650000"
        contracts = {
            "option_contracts": [
                cls._contract(
                    contract_id="alpaca-near-call",
                    symbol="SPY260828C00650000",
                    expiration="2026-08-28",
                    option_type="call",
                    open_interest="101",
                ),
                cls._contract(
                    contract_id="alpaca-target-call",
                    symbol=call_symbol,
                    expiration=_SELECTED_EXPIRATION,
                    option_type="call",
                    open_interest="1234",
                ),
                cls._contract(
                    contract_id="alpaca-target-put",
                    symbol=put_symbol,
                    expiration=_SELECTED_EXPIRATION,
                    option_type="put",
                    open_interest="987",
                ),
                cls._contract(
                    contract_id="alpaca-far-call",
                    symbol="SPY260904C00650000",
                    expiration="2026-09-04",
                    option_type="call",
                    open_interest="202",
                ),
            ]
        }
        return {
            "calendar": _json_bytes([{"date": _SESSION_DATE}]),
            "underlying": _json_bytes(
                {
                    "snapshots": {
                        "SPY": {
                            "latestQuote": {
                                "bp": "649.90",
                                "ap": "650.10",
                                "bs": "10",
                                "as": "20",
                                "t": _CAPTURED_AT,
                            },
                            "latestTrade": {
                                "p": "650.00",
                                "s": "3",
                                "t": _CAPTURED_AT,
                            },
                        }
                    }
                }
            ),
            "contracts": _json_bytes(contracts),
            "snapshots": _json_bytes(
                {
                    "snapshots": {
                        call_symbol: {
                            "latestQuote": {
                                "bp": "8.10",
                                "ap": "8.30",
                                "bs": "5",
                                "as": "7",
                                "t": _CAPTURED_AT,
                            },
                            "latestTrade": {
                                "p": "8.20",
                                "s": "2",
                                "t": _CAPTURED_AT,
                            },
                            "impliedVolatility": "0.2000",
                            "greeks": {"delta": "0.5000", "gamma": "0.0100"},
                        },
                        put_symbol: {
                            "latestQuote": {
                                "bp": "7.90",
                                "ap": "8.10",
                                "bs": "4",
                                "as": "8",
                                "t": _CAPTURED_AT,
                            },
                            "latestTrade": {
                                "p": "8.00",
                                "s": "1",
                                "t": _CAPTURED_AT,
                            },
                            "impliedVolatility": "0.2100",
                            "greeks": {"delta": "-0.5000", "gamma": "0.0100"},
                        },
                    }
                }
            ),
        }

    @classmethod
    def _responses(cls) -> list[AlpacaHttpResponse]:
        bodies = cls._bodies()
        return [
            cls._response(bodies["calendar"]),
            cls._response(bodies["underlying"]),
            cls._response(bodies["contracts"]),
            cls._response(bodies["snapshots"]),
        ]

    @classmethod
    def _parsed(cls):
        bodies = cls._bodies()
        return parse_alpaca_spy_options_capture(
            calendar_body=bodies["calendar"],
            underlying_body=bodies["underlying"],
            contracts_body=bodies["contracts"],
            snapshots_body=bodies["snapshots"],
            session_date=_SESSION_DATE,
            requested_at=_CAPTURED,
            completed_at=_CAPTURED,
        )

    def _run(
        self,
        transport: RecordingTransport,
        *,
        captured_at: str = _CAPTURED_AT,
    ):
        return run_alpaca_spy_option_surface(
            project_root=self.project_root,
            stores=self.stores,
            registry=self.registry,
            transport=transport,
            captured_at=captured_at,
            api_key="offline-alpaca-key",
            api_secret="offline-alpaca-secret",
        )

    def _counts(self) -> dict[str, int]:
        relations = (
            "instruments",
            "option_contracts",
            "option_surface_captures",
            "option_surface_snapshots",
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            return {
                relation: int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
                for relation in relations
            }

    def test_parser_accepts_string_size_open_interest_and_multiple_expiries(self) -> None:
        parsed = self._parsed()

        self.assertEqual(parsed.scope["selected_expiration"], _SELECTED_EXPIRATION)
        self.assertEqual(
            [contract["provider_contract_id"] for contract in parsed.contracts],
            ["alpaca-target-call", "alpaca-target-put"],
        )
        self.assertEqual(
            [contract["contract_multiplier"] for contract in parsed.contracts],
            [100, 100],
        )
        open_interest = {
            row["provider_contract_id"]: row["open_interest"]["value"]
            for row in parsed.surface_rows
        }
        self.assertEqual(open_interest, {"alpaca-target-call": 1234, "alpaca-target-put": 987})

    def test_prior_session_option_quote_is_explicitly_missing_not_current(self) -> None:
        bodies = self._bodies()
        snapshots = json.loads(bodies["snapshots"])
        symbol = "SPY260902C00650000"
        snapshots["snapshots"][symbol]["latestQuote"]["t"] = "2026-07-31T19:55:00Z"
        snapshots["snapshots"][symbol]["latestTrade"]["t"] = "2026-07-31T19:55:00Z"

        parsed = parse_alpaca_spy_options_capture(
            calendar_body=bodies["calendar"],
            underlying_body=bodies["underlying"],
            contracts_body=bodies["contracts"],
            snapshots_body=_json_bytes(snapshots),
            session_date=_SESSION_DATE,
            requested_at=_CAPTURED,
            completed_at=_CAPTURED,
        )

        surfaces = {row["provider_contract_id"]: row for row in parsed.surface_rows}
        stale = surfaces["alpaca-target-call"]
        self.assertEqual(stale["surface_state"], "missing")
        self.assertEqual(stale["missing_reason"], "source_quote_not_current_session")
        self.assertTrue(all(value is None for value in stale["quote"].values()))
        self.assertEqual(surfaces["alpaca-target-put"]["surface_state"], "present")

    def test_nanosecond_source_times_are_preserved_and_identity_bearing(self) -> None:
        bodies = self._bodies()
        source_time = "2026-08-03T19:54:59.123456789Z"
        underlying = json.loads(bodies["underlying"])
        underlying_snapshot = underlying["snapshots"]["SPY"]
        underlying_snapshot["latestQuote"]["t"] = source_time
        underlying_snapshot["latestTrade"]["t"] = source_time
        snapshots = json.loads(bodies["snapshots"])
        for snapshot in snapshots["snapshots"].values():
            snapshot["latestQuote"]["t"] = source_time
            snapshot["latestTrade"]["t"] = source_time

        parsed = parse_alpaca_spy_options_capture(
            calendar_body=bodies["calendar"],
            underlying_body=_json_bytes(underlying),
            contracts_body=bodies["contracts"],
            snapshots_body=_json_bytes(snapshots),
            session_date=_SESSION_DATE,
            requested_at=_CAPTURED,
            completed_at=_CAPTURED,
        )

        self.assertEqual(parsed.inputs["underlying_quote"]["quote_at"], source_time)
        self.assertEqual(parsed.inputs["underlying"]["observed_at"], source_time)
        self.assertEqual(parsed.capture["requested_at"], "2026-08-03T19:55:00.000000Z")
        self.assertEqual(parsed.capture["completed_at"], "2026-08-03T19:55:00.000000Z")
        self.assertEqual(
            parsed.inputs["underlying_quote"]["available_at"],
            "2026-08-03T19:55:00.000000Z",
        )
        self.assertEqual(
            {row["quote"]["quote_at"] for row in parsed.surface_rows},
            {source_time},
        )
        self.assertEqual({row["quote"]["trade_at"] for row in parsed.surface_rows}, {source_time})

        alternate_time = "2026-08-03T19:54:59.123456788Z"
        underlying_snapshot["latestQuote"]["t"] = alternate_time
        underlying_snapshot["latestTrade"]["t"] = alternate_time
        for snapshot in snapshots["snapshots"].values():
            snapshot["latestQuote"]["t"] = alternate_time
            snapshot["latestTrade"]["t"] = alternate_time
        alternate = parse_alpaca_spy_options_capture(
            calendar_body=bodies["calendar"],
            underlying_body=_json_bytes(underlying),
            contracts_body=bodies["contracts"],
            snapshots_body=_json_bytes(snapshots),
            session_date=_SESSION_DATE,
            requested_at=_CAPTURED,
            completed_at=_CAPTURED,
        )
        self.assertNotEqual(parsed.semantic_identity, alternate.semantic_identity)

        first_receipt = AlpacaSpyOptionsPublisher(
            self.stores, self.registry
        ).publish(parsed)
        second_receipt = AlpacaSpyOptionsPublisher(
            self.stores, self.registry
        ).publish(alternate)
        self.assertEqual(
            (first_receipt.outcome, second_receipt.outcome),
            ("succeeded", "succeeded"),
        )
        canonical_time = "2026-08-03T19:55:00.000000Z"
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            input_rows = {
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT underlying.observed_at, quote.quote_at, quote.available_at
                    FROM option_capture_underlyings AS underlying
                    JOIN option_capture_underlying_quotes AS quote USING (capture_id)
                    """
                )
            }
            surface_times = {
                tuple(row)
                for row in connection.execute(
                    "SELECT quote_at, trade_at FROM option_surface_snapshots"
                )
            }
            capture_times = {
                tuple(row)
                for row in connection.execute(
                    "SELECT requested_at, completed_at FROM option_surface_captures"
                )
            }
        self.assertEqual(
            input_rows,
            {
                (source_time, source_time, canonical_time),
                (alternate_time, alternate_time, canonical_time),
            },
        )
        self.assertEqual(
            surface_times,
            {(source_time, source_time), (alternate_time, alternate_time)},
        )
        self.assertEqual(capture_times, {(canonical_time, canonical_time)})

        underlying_snapshot["latestQuote"]["t"] = (
            "2026-08-03T19:54:59.1234567890Z"
        )
        with self.assertRaises(ValidationError):
            parse_alpaca_spy_options_capture(
                calendar_body=bodies["calendar"],
                underlying_body=_json_bytes(underlying),
                contracts_body=bodies["contracts"],
                snapshots_body=_json_bytes(snapshots),
                session_date=_SESSION_DATE,
                requested_at=_CAPTURED,
                completed_at=_CAPTURED,
            )

    def test_runner_uses_fixed_four_request_host_and_query_plan(self) -> None:
        transport = RecordingTransport(self._responses())
        report = self._run(transport)

        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.requests_issued, 4)
        self.assertEqual(report.selected_expiration, _SELECTED_EXPIRATION)
        self.assertEqual(
            [(call["host"], call["path"], call["query"]) for call in transport.calls],
            [
                (
                    "paper-api.alpaca.markets",
                    "/v3/calendar/OPRA",
                    {"start": _SESSION_DATE, "end": _SESSION_DATE},
                ),
                (
                    "data.alpaca.markets",
                    "/v2/stocks/snapshots",
                    {"symbols": "SPY", "feed": "iex"},
                ),
                (
                    "paper-api.alpaca.markets",
                    "/v2/options/contracts",
                    {
                        "underlying_symbols": "SPY",
                        "expiration_date_gte": "2026-08-26",
                        "expiration_date_lte": "2026-09-09",
                        "strike_price_gte": "520.00",
                        "strike_price_lte": "780.00",
                        "status": "active",
                        "limit": "10000",
                        "show_deliverables": "true",
                    },
                ),
                (
                    "data.alpaca.markets",
                    "/v1beta1/options/snapshots/SPY",
                    {
                        "feed": "indicative",
                        "expiration_date": _SELECTED_EXPIRATION,
                        "strike_price_gte": "520.00",
                        "strike_price_lte": "780.00",
                        "limit": "1000",
                    },
                ),
            ],
        )
        for call in transport.calls:
            self.assertEqual(call["timeout_seconds"], 60)
            self.assertEqual(call["max_bytes"], MAX_RESPONSE_BYTES)
            self.assertEqual(call["headers"]["Accept"], "application/json")

    def test_non_trading_day_stops_after_one_request_without_store_mutation(self) -> None:
        transport = RecordingTransport([self._response(_json_bytes([]))])
        before = mutation_fingerprint(self.stores)

        report = self._run(transport, captured_at="2026-08-02T19:55:00Z")

        self.assertEqual(report.outcome, "skipped")
        self.assertEqual(report.requests_issued, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def test_prior_session_underlying_stops_before_catalog_without_store_mutation(self) -> None:
        bodies = self._bodies()
        underlying = json.loads(bodies["underlying"])
        underlying["snapshots"]["SPY"]["latestQuote"]["t"] = "2026-07-31T19:55:00Z"
        underlying["snapshots"]["SPY"]["latestTrade"]["t"] = "2026-07-31T19:55:00Z"
        transport = RecordingTransport(
            [
                self._response(bodies["calendar"]),
                self._response(_json_bytes(underlying)),
            ]
        )
        before = mutation_fingerprint(self.stores)

        with self.assertRaises(ValidationError):
            self._run(transport)

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def test_current_zero_quote_cannot_mask_prior_session_trade_fallback(self) -> None:
        bodies = self._bodies()
        underlying = json.loads(bodies["underlying"])
        snapshot = underlying["snapshots"]["SPY"]
        snapshot["latestQuote"]["bp"] = "0"
        snapshot["latestQuote"]["ap"] = "0"
        snapshot["latestTrade"]["t"] = "2026-07-31T19:55:00Z"
        transport = RecordingTransport(
            [
                self._response(bodies["calendar"]),
                self._response(_json_bytes(underlying)),
            ]
        )
        before = mutation_fingerprint(self.stores)

        with self.assertRaises(ValidationError):
            self._run(transport)

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(
            before["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    def test_overall_deadline_fails_after_first_response_without_store_write(self) -> None:
        bodies = self._bodies()
        transport = RecordingTransport([self._response(bodies["calendar"])])
        before = mutation_fingerprint(self.stores)

        with (
            patch(
                "quant_data.market.alpaca_options.monotonic",
                side_effect=[0.0, 0.0, 121.0],
            ),
            self.assertRaises(ResourceLimitError),
        ):
            self._run(transport)

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def test_publish_creates_stage10_spy_bridge_and_exact_replay_writes_nothing(self) -> None:
        parsed = self._parsed()
        publisher = AlpacaSpyOptionsPublisher(self.stores, self.registry)

        first = publisher.publish(parsed)

        self.assertEqual(first.outcome, "succeeded")
        self.assertGreater(first.written_count, 0)
        self.assertEqual(
            self._counts(),
            {
                "instruments": 1,
                "option_contracts": 2,
                "option_surface_captures": 1,
                "option_surface_snapshots": 2,
            },
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            bridge = connection.execute(
                "SELECT instrument_id, asset_type, canonical_symbol FROM instruments"
            ).fetchone()
            capture = connection.execute(
                """
                SELECT requested_feed, resolved_feed, environment
                FROM option_surface_captures
                """
            ).fetchone()
        self.assertEqual(tuple(bridge), (_STAGE10_SPY_ID, "etf", "SPY"))
        self.assertEqual(
            tuple(capture),
            (ALPACA_REQUESTED_FEED, ALPACA_RESOLVED_FEED, ALPACA_ENVIRONMENT),
        )

        before_replay = mutation_fingerprint(self.stores)
        replay = publisher.publish(parsed)

        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertIsNone(replay.run_id)
        self.assertEqual(before_replay["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def test_pagination_marker_fails_closed_before_any_store_write(self) -> None:
        bodies = self._bodies()
        catalog = json.loads(bodies["contracts"])
        catalog["next_page_token"] = "second-page-is-not-allowed"
        transport = RecordingTransport(
            [
                self._response(bodies["calendar"]),
                self._response(bodies["underlying"]),
                self._response(_json_bytes(catalog)),
            ]
        )
        before = mutation_fingerprint(self.stores)

        with self.assertRaises(ResourceLimitError):
            self._run(transport)

        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])


if __name__ == "__main__":
    unittest.main()
