from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.boundary.dispatcher import ToolDispatcher
from quant_data.errors import ConflictError, ResourceLimitError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.alpaca_options import (
    ALPACA_ETF_OPTIONS_UNIVERSE,
    AlpacaHttpResponse,
    run_alpaca_etf_option_surface_grid,
)
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import (
    StoreMap,
    StoreRole,
    quiet_immutable_read_connection,
    writer_connection,
)
from quant_data.temporal import DateOnlyPolicy, TemporalValue
from quant_data.tool_platform.catalog import build_tool_version_policies
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.options_access import (
    OPTIONS_V2_OPERATION_CHARGES,
    OptionsV2Repository,
    invoke_options_v2,
    parse_capture_search_query,
    parse_contract_search_query,
    parse_surface_snapshot_query,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CAPTURED_AT = "2026-08-03T19:55:00Z"
_SESSION_DATE = "2026-08-03"
_EXPIRATION = "2026-08-04"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _fields(record: object) -> dict[str, object]:
    return {field.name: field.value for field in getattr(record, "fields")}


class _GridTransport:
    """One completely offline fixed-universe Alpaca response plan."""

    def request(self, **kwargs: object) -> AlpacaHttpResponse:
        path = kwargs["path"]
        query = kwargs["query"]
        assert isinstance(path, str) and isinstance(query, dict)
        if path == "/v3/calendar/OPRA":
            return AlpacaHttpResponse(
                200, "application/json", _json_bytes([{"date": _SESSION_DATE}])
            )
        if path == "/v2/stocks/snapshots":
            return AlpacaHttpResponse(
                200,
                "application/json",
                _json_bytes(
                    {
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
                ),
            )
        if path == "/v2/options/contracts":
            symbol = query["underlying_symbols"]
            assert isinstance(symbol, str)
            return AlpacaHttpResponse(
                200,
                "application/json",
                _json_bytes(
                    {
                        "option_contracts": [
                            self._contract(symbol, "call"),
                            self._contract(symbol, "put"),
                            self._contract(
                                symbol,
                                "call",
                                standard=False,
                                strike_price="110",
                            ),
                        ]
                    }
                ),
            )
        prefix = "/v1beta1/options/snapshots/"
        assert path.startswith(prefix)
        # An empty chain makes standard contracts explicitly missing.  The
        # third listing remains an explicit nonstandard exclusion.
        return AlpacaHttpResponse(200, "application/json", b'{"snapshots":{}}')

    @staticmethod
    def _contract(
        symbol: str,
        option_type: str,
        *,
        standard: bool = True,
        strike_price: str = "100",
    ) -> dict[str, object]:
        suffix = "C" if option_type == "call" else "P"
        strike_code = "00100000" if strike_price == "100" else "00110000"
        contract_symbol = f"{symbol}260804{suffix}{strike_code}"
        return {
            "id": f"alpaca-{contract_symbol}",
            "symbol": contract_symbol,
            "underlying_symbol": symbol,
            "status": "active",
            "expiration_date": _EXPIRATION,
            "strike_price": strike_price,
            "type": option_type,
            "size": "100",
            "open_interest": "7",
            "open_interest_date": "2026-08-01",
            "close_price": "1.20",
            "close_price_date": "2026-08-01",
            "root_symbol": symbol,
            "deliverables": [
                {
                    "allocation_percentage": "100",
                    "amount": "100" if standard else "50",
                    "delayed_settlement": False,
                    "symbol": symbol,
                    "type": "equity",
                }
            ],
        }


class OptionsAccessV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project_root = Path(self.temporary.name) / "project"
        (self.project_root / "data").mkdir(parents=True)
        self.stores = StoreMap.four_explicit(
            market=self.project_root / "data" / "market.sqlite",
            macro=self.project_root / "data" / "macro.sqlite",
            company=self.project_root / "data" / "company.sqlite",
            news=self.project_root / "data" / "news.sqlite",
        )
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry)
        self._seed_stage10_etfs()
        report = run_alpaca_etf_option_surface_grid(
            project_root=self.project_root,
            stores=self.stores,
            registry=self.registry,
            transport=_GridTransport(),
            captured_at=_CAPTURED_AT,
            api_key="offline-key",
            api_secret="offline-secret",
        )
        self.assertEqual(report.outcome, "succeeded")
        self.repository = OptionsV2Repository(self.stores, self.registry)
        self.baseline = mutation_fingerprint(self.stores)

    def tearDown(self) -> None:
        self.assertEqual(self.baseline, mutation_fingerprint(self.stores))
        self.temporary.cleanup()

    def _seed_stage10_etfs(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES ('options-v2-seed', 'market.stage10.instruments', ?,
                          'fixture.seed', '{}', 'running', ?, 'fixture')
                """,
                ("a" * 64, _CAPTURED_AT),
            )
            connection.executemany(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type,
                    display_name, exchange_code, currency_segment,
                    first_trade_date, identity_seed_sha256, captured_at,
                    captured_precision, run_id
                ) VALUES (?, 'fmp', ?, 'etf', ?, 'ARCX', 'provider_native',
                          NULL, ?, ?, 'datetime', 'options-v2-seed')
                """,
                tuple(
                    (
                        f"options-v2-{symbol}",
                        symbol,
                        f"{symbol} ETF",
                        f"{index + 1:064x}",
                        _CAPTURED_AT,
                    )
                    for index, symbol in enumerate(ALPACA_ETF_OPTIONS_UNIVERSE)
                ),
            )

    def _context(
        self, name: str, *, max_operations: int = 5_000_000
    ) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=hashlib.sha256(
                (PROJECT_ROOT / CANONICAL_REGISTRY_PATH).read_bytes()
            ).hexdigest(),
            request_id="options-access-v2-test",
            api_version="1.0",
            tool_name=name,
            tool_version="2.0.0",
            operation_graph_id=f"tool_platform.{name}.v2",
            operation_version="2.0.0",
            budget=ExecutionBudget(max_operations=max_operations),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
            execution="read_only",
        )

    def test_capture_search_uses_the_fixed_universe_and_current_cohorts(self) -> None:
        selection = self.repository.search_captures(
            parse_capture_search_query({"limit": 500})
        )
        self.assertEqual(selection.total_selected_count, 15)
        self.assertFalse(selection.truncated)
        self.assertEqual(
            {row["underlying_symbol"] for row in selection.records},
            set(ALPACA_ETF_OPTIONS_UNIVERSE),
        )
        self.assertTrue(
            all(row["resolved_feed"] == "alpaca_indicative" for row in selection.records)
        )
        self.assertTrue(
            all(row["environment"] == "paper" for row in selection.records)
        )
        unavailable = self.repository.search_captures(
            parse_capture_search_query(
                {"mode": "as_of", "as_of": "2026-01-01T00:00:00Z", "limit": 500}
            )
        )
        self.assertEqual(unavailable.records, ())

    def test_contract_search_keeps_explicit_missing_and_excluded_states(self) -> None:
        selection = self.repository.search_contracts(
            parse_contract_search_query(
                {"underlying_symbol": "SPY", "query": "", "limit": 2_000}
            )
        )
        self.assertEqual(selection.total_selected_count, 3)
        rows = tuple(selection.records)
        self.assertEqual(
            {row["deliverable_kind"] for row in rows}, {"standard", "nonstandard"}
        )
        self.assertEqual({row["surface_state"] for row in rows}, {"missing", "excluded"})
        excluded = next(row for row in rows if row["deliverable_kind"] == "nonstandard")
        self.assertEqual(excluded["surface_exclusion_reason"], "nonstandard_deliverable")
        self.assertEqual(excluded["strike_price"], "110")
        narrowed = self.repository.search_contracts(
            parse_contract_search_query(
                {"underlying_symbol": "SPY", "query": "SPY260804C00100000", "limit": 2_000}
            )
        )
        self.assertEqual(len(narrowed.records), 1)
        self.assertEqual(narrowed.records[0]["option_type"], "call")

    def test_surface_is_one_capture_with_explicit_missing_states(self) -> None:
        selection = self.repository.get_surface_snapshot(
            parse_surface_snapshot_query(
                {"underlying_symbol": "SPY", "target_dte": 30, "limit": 5_000}
            )
        )
        self.assertEqual(len(selection.captures), 1)
        self.assertEqual(selection.total_selected_count, 3)
        rows = tuple(selection.records)
        self.assertEqual({row["surface_state"] for row in rows}, {"missing", "excluded"})
        missing = next(row for row in rows if row["surface_state"] == "missing")
        self.assertIsInstance(missing["surface_missing_reason"], str)
        self.assertEqual(missing["rate_curve_state"], "missing")
        self.assertIsInstance(missing["rate_curve_missing_reason"], str)
        excluded = next(row for row in rows if row["surface_state"] == "excluded")
        self.assertEqual(excluded["deliverable_kind"], "nonstandard")
        self.assertEqual(excluded["surface_exclusion_reason"], "nonstandard_deliverable")
        self.assertTrue(all("rho" in row for row in rows))

        limited = self.repository.get_surface_snapshot(
            parse_surface_snapshot_query(
                {"underlying_symbol": "SPY", "limit": 1}
            )
        )
        self.assertTrue(limited.truncated)
        self.assertEqual(len(limited.records), 1)

    def test_repeated_observations_are_unique_and_cutoff_safe(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE option_open_interest(
                capture_id TEXT, contract_id TEXT, as_of_date TEXT,
                source_row INTEGER, open_interest_id TEXT,
                available_at TEXT, available_precision TEXT
            );
            CREATE TABLE option_close_prices(
                capture_id TEXT, contract_id TEXT, trade_date TEXT,
                source_row INTEGER, close_price_id TEXT,
                available_at TEXT, available_precision TEXT
            );
            INSERT INTO option_open_interest VALUES
                ('capture-1', 'contract-1', '2026-08-01', 1, 'oi-early',
                 '2026-08-03T20:00:00Z', 'datetime'),
                ('capture-1', 'contract-1', '2026-08-02', 2, 'oi-late',
                 '2026-08-04T12:00:00Z', 'datetime');
            INSERT INTO option_close_prices VALUES
                ('capture-1', 'contract-1', '2026-08-01', 1, 'close-early',
                 '2026-08-03T20:00:00Z', 'datetime'),
                ('capture-1', 'contract-1', '2026-08-02', 2, 'close-late',
                 '2026-08-04T12:00:00Z', 'datetime');
            """
        )
        latest_interest, latest_close = (
            OptionsV2Repository._latest_observations_for_capture(
                connection,
                capture_id="capture-1",
                cutoff=None,
                date_only_policy=DateOnlyPolicy.COMPLETED_DATE,
                warnings=set(),
            )
        )
        self.assertEqual(latest_interest["contract-1"]["open_interest_id"], "oi-late")
        self.assertEqual(latest_close["contract-1"]["close_price_id"], "close-late")

        cutoff = TemporalValue.parse(
            "2026-08-03T23:59:59Z", pointer="/as_of"
        )
        historical_interest, historical_close = (
            OptionsV2Repository._latest_observations_for_capture(
                connection,
                capture_id="capture-1",
                cutoff=cutoff,
                date_only_policy=DateOnlyPolicy.COMPLETED_DATE,
                warnings=set(),
            )
        )
        self.assertEqual(
            historical_interest["contract-1"]["open_interest_id"], "oi-early"
        )
        self.assertEqual(
            historical_close["contract-1"]["close_price_id"], "close-early"
        )
        connection.close()

    def test_capture_id_cannot_cross_the_requested_underlying(self) -> None:
        captures = self.repository.search_captures(
            parse_capture_search_query({"limit": 500})
        )
        qqq_capture = next(
            row["capture_id"] for row in captures.records if row["underlying_symbol"] == "QQQ"
        )
        with self.assertRaises(ConflictError):
            self.repository.get_surface_snapshot(
                parse_surface_snapshot_query(
                    {"underlying_symbol": "SPY", "capture_id": qqq_capture, "limit": 5_000}
                )
            )

    def test_contract_expiration_cannot_cross_the_capture_scope(self) -> None:
        with quiet_immutable_read_connection(
            self.stores, StoreRole.MARKET
        ) as connection:
            capture = self.repository._capture_candidates(
                connection,
                symbols=("SPY",),
                cutoff=None,
                date_only_policy=DateOnlyPolicy.COMPLETED_DATE,
                warnings=set(),
            )[0]
        with self.assertRaisesRegex(
            ConflictError, "outside its selected capture scope"
        ):
            self.repository._row_capture(
                {capture.capture_id: capture},
                {
                    "capture_id": capture.capture_id,
                    "provider": "alpaca",
                    "deliverable_kind": "standard",
                    "contract_status": "active",
                    "expiration_date": "2099-01-01",
                },
            )

    def test_public_operation_projects_bounded_read_only_records(self) -> None:
        result = invoke_options_v2(
            "options.get_surface_snapshot",
            {"underlying_symbol": "SPY", "surface_state": "missing", "limit": 5_000},
            self._context("options.get_surface_snapshot"),
            self.registry,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.records), 2)
        fields = _fields(result.records[0])
        self.assertEqual(fields["surface_state"], "missing")
        self.assertEqual(fields["resolved_feed"], "alpaca_indicative")
        self.assertTrue(result.lineage)

    def test_public_preflight_charges_the_full_candidate_workload(self) -> None:
        calls = (
            ("options.search_captures", {"limit": 1}),
            (
                "options.search_contracts",
                {"underlying_symbol": "SPY", "query": "", "limit": 1},
            ),
            (
                "options.get_surface_snapshot",
                {"underlying_symbol": "SPY", "limit": 1},
            ),
        )
        policies = {
            policy["tool"]: policy
            for policy in build_tool_version_policies()
        }
        for name, arguments in calls:
            required = OPTIONS_V2_OPERATION_CHARGES[name]
            with self.subTest(name=name, boundary="reject"):
                with self.assertRaises(ResourceLimitError):
                    invoke_options_v2(
                        name,
                        arguments,
                        self._context(name, max_operations=required - 1),
                        self.registry,
                    )
            with self.subTest(name=name, boundary="catalog"):
                variant = next(
                    item
                    for item in policies[name]["variants"]
                    if item["version"] == "2.0.0"
                )
                self.assertEqual(
                    variant["workload_bounds"]["max_operations"], required
                )

    def test_public_dispatcher_uses_each_options_v2_argument_contract(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        calls = (
            (
                "options.search_captures",
                {
                    "underlying_symbols": ["SPY"],
                    "mode": "latest",
                    "as_of": None,
                    "date_only_policy": "completed_date",
                    "limit": 10,
                },
            ),
            (
                "options.search_contracts",
                {
                    "underlying_symbol": "SPY",
                    "query": "SPY",
                    "expiration_date": None,
                    "option_type": None,
                    "mode": "latest",
                    "as_of": None,
                    "date_only_policy": "completed_date",
                    "limit": 10,
                },
            ),
            (
                "options.get_surface_snapshot",
                {
                    "underlying_symbol": "SPY",
                    "capture_id": None,
                    "target_dte": 30,
                    "expiration_date": None,
                    "option_type": None,
                    "surface_state": None,
                    "mode": "latest",
                    "as_of": None,
                    "date_only_policy": "completed_date",
                    "limit": 10,
                },
            ),
        )
        for name, arguments in calls:
            with self.subTest(name=name):
                result = dispatcher.call(name, arguments, tool_version="2.0.0")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["tool"], name)


if __name__ == "__main__":
    unittest.main()
