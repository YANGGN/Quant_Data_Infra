from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import quant_data.market.alpaca_options as alpaca_options_module
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.alpaca_options import (
    ALPACA_ETF_OPTIONS_DTE_TARGETS,
    ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS,
    ALPACA_ETF_OPTIONS_UNIVERSE,
    AlpacaHttpResponse,
    MAX_ETF_OPTIONS_CONTRACT_PAGES,
    MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES,
    _EtfGridBudget,
    _fetch_etf_grid_catalog,
    _is_etf_grid_request,
    _observation,
    _surface_quote,
    run_alpaca_etf_option_surface_grid,
    select_alpaca_etf_expirations,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_CAPTURED_AT = "2026-08-03T19:55:00Z"
_CAPTURED = datetime(2026, 8, 3, 19, 55, tzinfo=timezone.utc)
_SESSION = date(2026, 8, 3)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _stores(root: Path) -> StoreMap:
    (root / "data").mkdir()
    return StoreMap.four_explicit(
        market=root / "data" / "market.sqlite",
        macro=root / "data" / "macro.sqlite",
        company=root / "data" / "company.sqlite",
        news=root / "data" / "news.sqlite",
    )


class GridTransport:
    def __init__(
        self,
        *,
        trading: bool = True,
        catalog_pages: int = 1,
        invalid_token: bool = False,
        nested_underlyings: bool = False,
        fail_catalog_status: int | None = None,
        missing_underlying: str | None = None,
        include_chain_quotes: bool = True,
    ) -> None:
        self.trading = trading
        self.catalog_pages = catalog_pages
        self.invalid_token = invalid_token
        self.nested_underlyings = nested_underlyings
        self.fail_catalog_status = fail_catalog_status
        self.missing_underlying = missing_underlying
        self.include_chain_quotes = include_chain_quotes
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def _response(value: object) -> AlpacaHttpResponse:
        return AlpacaHttpResponse(200, "application/json", _json_bytes(value))

    @staticmethod
    def _contract(symbol: str, expiration: str, kind: str) -> dict[str, object]:
        option = (
            f"{symbol}{expiration[2:].replace('-', '')}"
            f"{'C' if kind == 'call' else 'P'}00100000"
        )
        return {
            "id": f"alpaca-{option}",
            "symbol": option,
            "underlying_symbol": symbol,
            "status": "active",
            "expiration_date": expiration,
            "strike_price": "100",
            "type": kind,
            "size": "100",
            "open_interest": "7",
            "open_interest_date": "2026-08-01",
            "close_price": "1.20",
            "close_price_date": "2026-08-01",
            "root_symbol": symbol,
            "deliverables": [
                {
                    "allocation_percentage": "100",
                    "amount": "100",
                    "delayed_settlement": False,
                    "symbol": symbol,
                    "type": "equity",
                }
            ],
        }

    def request(self, **kwargs: object) -> AlpacaHttpResponse:
        self.calls.append(dict(kwargs))
        path = kwargs["path"]
        query = kwargs["query"]
        assert isinstance(path, str) and isinstance(query, dict)
        if path == "/v3/calendar/OPRA":
            return self._response(
                [{"date": _SESSION.isoformat()}] if self.trading else []
            )
        if path == "/v2/stocks/snapshots":
            snapshots = {
                symbol: {
                    "latestQuote": {
                        "bp": "99.90",
                        "ap": "100.10",
                        "bs": "1",
                        "as": "1",
                        "t": _CAPTURED_AT,
                    }
                }
                for symbol in ALPACA_ETF_OPTIONS_UNIVERSE
            }
            if self.missing_underlying is not None:
                snapshots.pop(self.missing_underlying)
            return self._response(
                {"snapshots": snapshots}
                if self.nested_underlyings
                else snapshots
            )
        if path == "/v2/options/contracts":
            if self.fail_catalog_status is not None:
                return AlpacaHttpResponse(
                    self.fail_catalog_status,
                    "application/json",
                    b'{"message":"fixture provider failure"}',
                )
            symbol = query["underlying_symbols"]
            assert isinstance(symbol, str)
            page = int(str(query.get("page_token", "0")).removeprefix("p") or "0")
            expiration = (_SESSION + timedelta(days=1)).isoformat()
            payload: dict[str, object] = {
                "option_contracts": [
                    self._contract(symbol, expiration, "call"),
                    self._contract(symbol, expiration, "put"),
                ]
            }
            if self.invalid_token:
                payload["next_page_token"] = "not valid token"
            elif page + 1 < self.catalog_pages:
                payload["next_page_token"] = f"p{page + 1}"
            return self._response(payload)
        prefix = "/v1beta1/options/snapshots/"
        assert path.startswith(prefix)
        symbol = path.removeprefix(prefix)
        expiration = str(query["expiration_date"])
        call = self._contract(symbol, expiration, "call")["symbol"]
        put = self._contract(symbol, expiration, "put")["symbol"]
        if not self.include_chain_quotes:
            return self._response({"snapshots": {}})
        return self._response(
            {
                "snapshots": {
                    call: {
                        "latestQuote": {
                            "bp": "1.10",
                            "ap": "1.30",
                            "bs": "1",
                            "as": "1",
                            "t": _CAPTURED_AT,
                        }
                    },
                    put: {
                        "latestQuote": {
                            "bp": "1.00",
                            "ap": "1.20",
                            "bs": "1",
                            "as": "1",
                            "t": _CAPTURED_AT,
                        }
                    },
                }
            }
        )


class AlpacaEtfOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.stores = _stores(self.project_root)
        self.registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )
        initialize_all(self.stores, self.registry)
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                "INSERT INTO ingestion_runs(run_id,dataset_id,semantic_identity,"
                "command,scope_json,status,started_at,code_version) "
                "VALUES ('seed','market.stage10.instruments',?,'fixture.seed',"
                "'{}','running',?,'fixture')",
                ("0" * 64, _CAPTURED_AT),
            )
            for symbol in ALPACA_ETF_OPTIONS_UNIVERSE:
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id,provider,provider_symbol,asset_type,display_name,
                        exchange_code,currency_segment,first_trade_date,identity_seed_sha256,
                        captured_at,captured_precision,run_id
                    ) VALUES (?, 'fmp', ?, 'etf', ?, 'ARCX', 'provider_native',
                              NULL, ?, ?, 'datetime', 'seed')
                    """,
                    (
                        f"stage10-{symbol}",
                        symbol,
                        f"{symbol} ETF",
                        "1" * 64,
                        _CAPTURED_AT,
                    ),
                )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, transport: GridTransport):
        return run_alpaca_etf_option_surface_grid(
            project_root=self.project_root,
            stores=self.stores,
            registry=self.registry,
            transport=transport,
            captured_at=_CAPTURED_AT,
            api_key="offline-key",
            api_secret="offline-secret",
        )

    def test_live_request_policy_accepts_safe_opaque_page_tokens_only(self) -> None:
        contract_query = {
            "underlying_symbols": "QQQ",
            "expiration_date_gte": "2026-08-04",
            "expiration_date_lte": (
                _SESSION + timedelta(days=ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS)
            ).isoformat(),
            "strike_price_gte": "80.00",
            "strike_price_lte": "120.00",
            "status": "active",
            "limit": "10000",
            "show_deliverables": "true",
            "page_token": "MA==",
        }
        self.assertTrue(
            _is_etf_grid_request(
                "paper-api.alpaca.markets",
                "/v2/options/contracts",
                contract_query,
            )
        )
        self.assertFalse(
            _is_etf_grid_request(
                "paper-api.alpaca.markets",
                "/v2/options/contracts",
                {**contract_query, "page_token": "not a token"},
            )
        )
        self.assertFalse(
            _is_etf_grid_request(
                "paper-api.alpaca.markets",
                "/v2/options/contracts",
                {**contract_query, "underlying_symbols": "AAPL"},
            )
        )
        self.assertFalse(
            _is_etf_grid_request(
                "data.alpaca.markets",
                "/v1beta1/options/snapshots/AAPL",
                {
                    "feed": "indicative",
                    "expiration_date": "2026-09-18",
                    "strike_price_gte": "80",
                    "strike_price_lte": "120",
                    "limit": "1000",
                },
            )
        )

    def test_fixed_universe_manifest_publication_and_replay(self) -> None:
        first_transport = GridTransport()
        first = self._run(first_transport)
        self.assertEqual(first.outcome, "succeeded")
        self.assertEqual(first.completed_underlyings, ALPACA_ETF_OPTIONS_UNIVERSE)
        self.assertEqual(first.failed_underlyings, ())
        self.assertEqual(first.captures, len(ALPACA_ETF_OPTIONS_UNIVERSE))
        self.assertEqual(first.contracts, 2 * len(ALPACA_ETF_OPTIONS_UNIVERSE))
        self.assertEqual(
            first.requests_issued, 2 + 2 * len(ALPACA_ETF_OPTIONS_UNIVERSE)
        )
        self.assertEqual(
            [
                (call["host"], call["path"], call["query"])
                for call in first_transport.calls[:3]
            ],
            [
                (
                    "paper-api.alpaca.markets",
                    "/v3/calendar/OPRA",
                    {"start": _SESSION.isoformat(), "end": _SESSION.isoformat()},
                ),
                (
                    "data.alpaca.markets",
                    "/v2/stocks/snapshots",
                    {
                        "symbols": ",".join(ALPACA_ETF_OPTIONS_UNIVERSE),
                        "feed": "iex",
                    },
                ),
                (
                    "paper-api.alpaca.markets",
                    "/v2/options/contracts",
                    {
                        "underlying_symbols": "SPY",
                        "expiration_date_gte": "2026-08-04",
                        "expiration_date_lte": (
                            _SESSION
                            + timedelta(days=ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS)
                        ).isoformat(),
                        "strike_price_gte": "80.00",
                        "strike_price_lte": "120.00",
                        "status": "active",
                        "limit": "10000",
                        "show_deliverables": "true",
                    },
                ),
            ],
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_surface_captures"
                ).fetchone()[0],
                15,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_surface_snapshots"
                ).fetchone()[0],
                30,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(DISTINCT underlying_instrument_id) "
                    "FROM option_surface_captures"
                ).fetchone()[0],
                15,
            )
            scopes = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT request_scope_json FROM option_surface_captures"
                )
            ]
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_raw_responses"
                ).fetchone()[0],
                32,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_capture_raw_responses"
                ).fetchone()[0],
                60,
            )
        self.assertTrue(
            all(
                scope["target_dtes"] == list(ALPACA_ETF_OPTIONS_DTE_TARGETS)
                for scope in scopes
            )
        )
        before = mutation_fingerprint(self.stores)
        replay = self._run(GridTransport())
        self.assertEqual(replay.outcome, "succeeded")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])

        class EquivalentNumericTransport(GridTransport):
            @staticmethod
            def _contract(
                symbol: str, expiration: str, kind: str
            ) -> dict[str, object]:
                row = GridTransport._contract(symbol, expiration, kind)
                row["strike_price"] = "100.0"
                row["size"] = 100
                row["open_interest"] = 7
                row["close_price"] = "1.200"
                return row

        equivalent = self._run(EquivalentNumericTransport())
        self.assertEqual(equivalent.outcome, "succeeded")
        self.assertGreater(equivalent.written_count, 0)
        self.assertNotEqual(
            before["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_raw_responses"
                ).fetchone()[0],
                47,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM option_capture_raw_responses"
                ).fetchone()[0],
                120,
            )

    def test_expiry_tie_and_duplicate_target_collapse_are_deterministic(self) -> None:
        def rows(expiration: date) -> list[dict[str, object]]:
            return [
                GridTransport._contract(
                    "SPY", expiration.isoformat(), option_type
                )
                for option_type in ("call", "put")
            ]

        selected = dict(
            select_alpaca_etf_expirations(
                [
                    *rows(_SESSION + timedelta(days=1)),
                    *rows(_SESSION + timedelta(days=3)),
                ],
                session_date=_SESSION.isoformat(),
                spot_price="100",
                underlying_symbol="SPY",
            )
        )
        self.assertEqual(selected["2026-08-04"], (1, 2))
        self.assertIn(3, selected["2026-08-06"])

    def test_one_sided_or_nonstandard_expiry_is_not_eligible(self) -> None:
        near = (_SESSION + timedelta(days=1)).isoformat()
        farther = (_SESSION + timedelta(days=3)).isoformat()
        near_call = GridTransport._contract("SPY", near, "call")
        near_nonstandard_put = GridTransport._contract("SPY", near, "put")
        near_nonstandard_put["size"] = "10"
        selected = dict(
            select_alpaca_etf_expirations(
                [
                    near_call,
                    near_nonstandard_put,
                    GridTransport._contract("SPY", farther, "call"),
                    GridTransport._contract("SPY", farther, "put"),
                ],
                session_date=_SESSION.isoformat(),
                spot_price="100",
                underlying_symbol="SPY",
            )
        )
        self.assertNotIn(near, selected)
        self.assertEqual(
            selected[farther],
            ALPACA_ETF_OPTIONS_DTE_TARGETS,
        )

    def test_horizon_is_sufficient_for_global_nearest_365_dte_selection(self) -> None:
        self.assertEqual(
            ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS,
            2 * max(ALPACA_ETF_OPTIONS_DTE_TARGETS),
        )

    def test_provider_numeric_text_is_bounded_before_normalization(self) -> None:
        expiration = (_SESSION + timedelta(days=1)).isoformat()
        huge_exponent = GridTransport._contract("SPY", expiration, "call")
        huge_exponent["strike_price"] = "1e999999999"
        with self.assertRaises(ValidationError):
            select_alpaca_etf_expirations(
                [huge_exponent],
                session_date=_SESSION.isoformat(),
                spot_price="100",
                underlying_symbol="SPY",
            )
        huge_integer = GridTransport._contract("SPY", expiration, "call")
        huge_integer["size"] = "9" * 1_000
        with self.assertRaises(ValidationError):
            select_alpaca_etf_expirations(
                [huge_integer],
                session_date=_SESSION.isoformat(),
                spot_price="100",
                underlying_symbol="SPY",
            )

    def test_semantic_hashing_streams_under_an_explicit_normalized_cap(self) -> None:
        inputs = {
            "underlying": {},
            "underlying_quote": {},
            "rate_curve": {},
            "dividend_set": {},
            "expiry_inputs": (),
        }
        with (
            patch.object(
                alpaca_options_module,
                "MAX_ETF_OPTIONS_NORMALIZED_BYTES",
                32,
            ),
            self.assertRaises(ResourceLimitError),
        ):
            alpaca_options_module._grid_semantic(
                {"underlying_symbol": "SPY"}, (), (), inputs
            )

    def test_nested_underlying_snapshot_compatibility_is_retained(self) -> None:
        report = self._run(GridTransport(nested_underlyings=True))
        self.assertEqual(report.outcome, "succeeded")

    def test_one_missing_underlying_is_isolated_from_the_other_fourteen(self) -> None:
        report = self._run(GridTransport(missing_underlying="QQQ"))
        self.assertEqual(report.outcome, "partial")
        self.assertEqual(report.failed_underlyings, ("QQQ",))
        self.assertEqual(
            report.completed_underlyings,
            tuple(symbol for symbol in ALPACA_ETF_OPTIONS_UNIVERSE if symbol != "QQQ"),
        )
        self.assertEqual(report.captures, len(ALPACA_ETF_OPTIONS_UNIVERSE) - 1)
        self.assertEqual(
            report.requests_issued,
            2 + 2 * (len(ALPACA_ETF_OPTIONS_UNIVERSE) - 1),
        )

    def test_missing_quotes_do_not_discard_independent_oi_or_close(self) -> None:
        report = self._run(GridTransport(include_chain_quotes=False))
        self.assertEqual(report.outcome, "succeeded")
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            surface = connection.execute(
                "SELECT surface_state, missing_reason FROM option_surface_snapshots "
                "ORDER BY surface_snapshot_id LIMIT 1"
            ).fetchone()
            interest = connection.execute(
                "SELECT open_interest, observation_state, missing_reason "
                "FROM option_open_interest ORDER BY open_interest_id LIMIT 1"
            ).fetchone()
            close = connection.execute(
                "SELECT close_price, observation_state, missing_reason "
                "FROM option_close_prices ORDER BY close_price_id LIMIT 1"
            ).fetchone()
        self.assertEqual(tuple(surface), ("missing", "source_quote_not_returned"))
        self.assertEqual(tuple(interest), (7, "present", None))
        self.assertEqual(tuple(close), (1.2, "present", None))

    def test_future_dated_and_future_instant_evidence_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            _observation(
                {"_open_interest": "7", "_open_interest_date": "2026-08-04"},
                value_key="_open_interest",
                date_key="as_of_date",
                session_date=_SESSION.isoformat(),
                completed_at=_CAPTURED_AT,
                integer=True,
                enforce_session_cutoff=True,
            )
        with self.assertRaises(ValidationError):
            _surface_quote(
                {
                    "latestQuote": {
                        "bp": "1.10",
                        "ap": "1.20",
                        "t": "2026-08-03T19:55:01Z",
                    }
                },
                completed_at=_CAPTURED_AT,
                enforce_completion_cutoff=True,
            )

    def test_aggregate_byte_cap_is_admitted_before_transport_read(self) -> None:
        class RemainingByteTransport:
            def __init__(self) -> None:
                self.max_bytes: list[int] = []

            def request(self, **kwargs: object) -> AlpacaHttpResponse:
                admitted = kwargs["max_bytes"]
                assert isinstance(admitted, int)
                self.max_bytes.append(admitted)
                return AlpacaHttpResponse(200, "application/json", b"{")

        transport = RemainingByteTransport()
        budget = _EtfGridBudget(
            deadline=1_000_000_000.0,
            response_byte_count=MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES - 1,
        )
        self.assertEqual(
            budget.request(
                transport,
                host="data.alpaca.markets",
                path="/v2/stocks/snapshots",
                query={},
                headers={},
            ),
            b"{",
        )
        self.assertEqual(transport.max_bytes, [1])
        with self.assertRaises(ResourceLimitError):
            budget.request(
                transport,
                host="data.alpaca.markets",
                path="/v2/stocks/snapshots",
                query={},
                headers={},
            )

        class OversizedTransport(RemainingByteTransport):
            def request(self, **kwargs: object) -> AlpacaHttpResponse:
                admitted = kwargs["max_bytes"]
                assert isinstance(admitted, int)
                self.max_bytes.append(admitted)
                return AlpacaHttpResponse(200, "application/json", b"{}")

        oversized = OversizedTransport()
        rejected = _EtfGridBudget(
            deadline=1_000_000_000.0,
            response_byte_count=MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES - 1,
        )
        with self.assertRaises(ResourceLimitError):
            rejected.request(
                oversized,
                host="data.alpaca.markets",
                path="/v2/stocks/snapshots",
                query={},
                headers={},
            )
        self.assertTrue(rejected.terminal_response_limit)
        self.assertEqual(oversized.max_bytes, [1])

    def test_store_conflict_stops_before_fetching_later_underlyings(self) -> None:
        transport = GridTransport()
        before = mutation_fingerprint(self.stores)
        with patch(
            "quant_data.market.alpaca_options.AlpacaEtfOptionsPublisher.publish",
            side_effect=ConflictError("fixture lock conflict"),
        ):
            report = self._run(transport)
        self.assertEqual(report.outcome, "partial")
        self.assertEqual(report.completed_underlyings, ())
        self.assertEqual(report.failed_underlyings, ALPACA_ETF_OPTIONS_UNIVERSE)
        self.assertEqual(report.requests_issued, 4)
        self.assertEqual(
            before["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    def test_provider_rejection_stops_after_the_first_failed_catalog(self) -> None:
        transport = GridTransport(fail_catalog_status=429)
        before = mutation_fingerprint(self.stores)
        report = self._run(transport)
        self.assertEqual(report.outcome, "partial")
        self.assertEqual(report.completed_underlyings, ())
        self.assertEqual(report.failed_underlyings, ALPACA_ETF_OPTIONS_UNIVERSE)
        self.assertEqual(report.requests_issued, 3)
        self.assertEqual(
            before["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    def test_overall_deadline_includes_stage10_preflight(self) -> None:
        class FakeClock:
            value = 1_000.0

            def __call__(self) -> float:
                return self.value

        clock = FakeClock()
        real_preflight = alpaca_options_module._stage10_etf_identities

        def slow_preflight(store_map: StoreMap):
            result = real_preflight(store_map)
            clock.value = 1_901.0
            return result

        transport = GridTransport()
        with (
            patch.object(alpaca_options_module, "monotonic", side_effect=clock),
            patch.object(
                alpaca_options_module,
                "_stage10_etf_identities",
                side_effect=slow_preflight,
            ),
            self.assertRaises(ResourceLimitError),
        ):
            self._run(transport)
        self.assertEqual(transport.calls, [])

    def test_parse_deadline_stops_before_publication_and_later_requests(self) -> None:
        class FakeClock:
            value = 1_000.0

            def __call__(self) -> float:
                return self.value

        clock = FakeClock()
        real_parse = alpaca_options_module._parse_alpaca_etf_options_capture

        def slow_parse(**kwargs: object):
            result = real_parse(**kwargs)
            clock.value = 1_901.0
            return result

        transport = GridTransport()
        before = mutation_fingerprint(self.stores)
        with (
            patch.object(alpaca_options_module, "monotonic", side_effect=clock),
            patch.object(
                alpaca_options_module,
                "_parse_alpaca_etf_options_capture",
                side_effect=slow_parse,
            ),
            patch.object(
                alpaca_options_module.AlpacaEtfOptionsPublisher,
                "publish",
                autospec=True,
            ) as publish,
        ):
            report = self._run(transport)
        publish.assert_not_called()
        self.assertEqual(report.outcome, "partial")
        self.assertEqual(report.failed_underlyings, ALPACA_ETF_OPTIONS_UNIVERSE)
        self.assertEqual(report.requests_issued, 4)
        self.assertEqual(
            before["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    def test_publication_overrun_never_returns_success(self) -> None:
        class FakeClock:
            value = 1_000.0

            def __call__(self) -> float:
                return self.value

        clock = FakeClock()
        real_publish = alpaca_options_module.AlpacaEtfOptionsPublisher.publish

        def slow_publish(
            publisher: object,
            parsed: object,
            *,
            held_locks: object = None,
        ):
            receipt = real_publish(
                publisher,
                parsed,
                held_locks=held_locks,
            )
            clock.value = 1_901.0
            return receipt

        transport = GridTransport()
        with (
            patch.object(alpaca_options_module, "monotonic", side_effect=clock),
            patch.object(
                alpaca_options_module.AlpacaEtfOptionsPublisher,
                "publish",
                new=slow_publish,
            ),
        ):
            report = self._run(transport)
        self.assertEqual(report.outcome, "partial")
        self.assertEqual(report.captures, 1)
        self.assertEqual(report.failed_underlyings, ALPACA_ETF_OPTIONS_UNIVERSE)
        self.assertEqual(report.requests_issued, 4)

    def test_stage10_preflight_makes_zero_provider_requests(self) -> None:
        missing_root = Path(self.temporary.name) / "missing-project"
        missing_root.mkdir()
        missing_stores = _stores(missing_root)
        initialize_all(missing_stores, self.registry)
        transport = GridTransport()
        with self.assertRaises(ValidationError):
            run_alpaca_etf_option_surface_grid(
                project_root=missing_root,
                stores=missing_stores,
                registry=self.registry,
                transport=transport,
                captured_at=_CAPTURED_AT,
                api_key="offline-key",
                api_secret="offline-secret",
            )
        self.assertEqual(transport.calls, [])

    def test_non_trading_day_uses_only_calendar_and_does_not_write(self) -> None:
        transport = GridTransport(trading=False)
        before = mutation_fingerprint(self.stores)
        report = self._run(transport)
        self.assertEqual((report.outcome, report.requests_issued), ("skipped", 1))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])

    def test_catalog_page_bound_and_token_validation_fail_closed(self) -> None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "test",
            "APCA-API-KEY-ID": "key",
            "APCA-API-SECRET-KEY": "secret",
        }
        query = {
            "underlying_symbols": "SPY",
            "expiration_date_gte": "2026-08-04",
            "expiration_date_lte": (
                _SESSION + timedelta(days=ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS)
            ).isoformat(),
            "strike_price_gte": "80.00",
            "strike_price_lte": "120.00",
            "status": "active",
            "limit": "10000",
            "show_deliverables": "true",
        }
        transport = GridTransport(
            catalog_pages=MAX_ETF_OPTIONS_CONTRACT_PAGES + 1
        )
        with self.assertRaises(ResourceLimitError):
            _fetch_etf_grid_catalog(
                _EtfGridBudget(1_000_000_000.0),
                transport,
                headers=headers,
                underlying_symbol="SPY",
                query=query,
            )
        self.assertEqual(len(transport.calls), MAX_ETF_OPTIONS_CONTRACT_PAGES)
        invalid = GridTransport(invalid_token=True)
        with self.assertRaises(ValidationError):
            _fetch_etf_grid_catalog(
                _EtfGridBudget(1_000_000_000.0),
                invalid,
                headers=headers,
                underlying_symbol="SPY",
                query=query,
            )
        self.assertEqual(len(invalid.calls), 1)


if __name__ == "__main__":
    unittest.main()
