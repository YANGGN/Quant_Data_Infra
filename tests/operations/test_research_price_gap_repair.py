from __future__ import annotations

import inspect
import json
import sqlite3
from types import SimpleNamespace
import unittest
import tempfile
from pathlib import Path

from quant_data.fingerprint import mutation_fingerprint
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope

from quant_data.errors import ConflictError, ValidationError
from quant_data.market.stage12_incremental import Stage12BFixtureRequest, Stage12BFixtureResponse, Stage12BIncrementalCollector
from quant_data.operations import research_price_gap_repair as repair

def _collector() -> Stage12BIncrementalCollector:
    collector = object.__new__(Stage12BIncrementalCollector)
    collector._research_repair = True
    collector._research_repair_authority_sha256 = repair.RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256
    collector._research_repair_plan_sha256 = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256
    collector._research_repair_instruments = repair._repair_instruments()
    collector._scheduled_instruments = None
    collector._schedule_authority_sha256 = None
    return collector

def _body(symbol: str) -> bytes:
    item = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN[symbol]
    return json.dumps([{"symbol": symbol, "date": session, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000, "change": 1, "changePercent": 1, "vwap": 100} for session in item["session_dates"]], separators=(",", ":")).encode()

class ResearchPriceGapRepairTests(unittest.TestCase):
    def test_exact_plan_is_accepted_and_unapproved_symbol_or_date_is_refused(self) -> None:
        collector = _collector()
        item = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN["MSFT"]
        scope, captured_at = collector._request_scope(Stage12BFixtureRequest(symbol="MSFT", from_date=item["from"], to_date=item["to"], session_dates=tuple(item["session_dates"]), captured_at="2026-09-06T12:00:00Z"))
        self.assertEqual(captured_at, "2026-09-06T12:00:00.000000Z")
        self.assertEqual(scope["research_repair_plan_sha256"], repair.RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256)
        with self.assertRaises(ValidationError):
            collector._request_scope(Stage12BFixtureRequest(symbol="QQQ", from_date=item["from"], to_date=item["to"], session_dates=tuple(item["session_dates"]), captured_at="2026-09-06T12:00:00Z"))
        with self.assertRaises(ValidationError):
            collector._request_scope(Stage12BFixtureRequest(symbol="MSFT", from_date="2026-08-18", to_date=item["to"], session_dates=tuple(item["session_dates"]), captured_at="2026-09-06T12:00:00Z"))

    def test_complete_batch_is_required_for_each_authorized_response(self) -> None:
        collector = _collector()
        item = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN["AAPL"]
        request = Stage12BFixtureRequest(symbol="AAPL", from_date=item["from"], to_date=item["to"], session_dates=tuple(item["session_dates"]), captured_at="2026-09-06T12:00:00Z")
        scope, _ = collector._request_scope(request)
        self.assertEqual(len(collector._parse_response(scope, Stage12BFixtureResponse(200, "application/json", _body("AAPL"), 0))), 10)
        partial = json.loads(_body("AAPL"))[:-1]
        with self.assertRaises(ValidationError):
            collector._parse_response(scope, Stage12BFixtureResponse(200, "application/json", json.dumps(partial).encode(), 0))

    def test_missing_only_check_stops_the_entire_batch_on_any_overlap(self) -> None:
        collector = _collector()
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE stage10_daily_prices (instrument_id, trade_date, provider, price_variant, currency_segment)")
        rows = tuple(SimpleNamespace(trade_date=value) for value in repair.RESEARCH_PRICE_GAP_REPAIR_SESSIONS)
        state = SimpleNamespace(rows=rows)
        collector._require_research_repair_rows_missing(connection, state, "spy-id")
        connection.execute("INSERT INTO stage10_daily_prices VALUES (?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native')", ("spy-id", repair.RESEARCH_PRICE_GAP_REPAIR_SESSIONS[5]))
        with self.assertRaises(ConflictError):
            collector._require_research_repair_rows_missing(connection, state, "spy-id")



    def test_pinned_hash_refuses_recomputed_alternate_dates_and_empty_batch(self) -> None:
        collector = _collector()
        alternate = repair._repair_instruments()
        alternate["AAPL"] = (
            "equity", "2026-08-18", "2026-08-28",
            tuple(repair.RESEARCH_PRICE_GAP_REPAIR_SESSIONS[1:10]),
        )
        altered_plan = {key: dict(value) for key, value in repair.RESEARCH_PRICE_GAP_REPAIR_PLAN.items()}
        altered_plan["AAPL"]["from"] = "2026-08-18"
        altered_plan["AAPL"]["session_dates"] = list(repair.RESEARCH_PRICE_GAP_REPAIR_SESSIONS[1:10])
        with self.assertRaises(ValidationError):
            collector._configure_research_repair(
                authority_sha256=repair.RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256,
                plan_sha256=repair._sha256_json(altered_plan),
                instruments=alternate,
            )
        item = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN["AAPL"]
        scope, _ = collector._request_scope(
            Stage12BFixtureRequest("AAPL", item["from"], item["to"], tuple(item["session_dates"]), "2026-09-06T12:00:00Z")
        )
        with self.assertRaises(ValidationError):
            collector._parse_response(scope, Stage12BFixtureResponse(200, "application/json", b"[]", 0))

    def test_public_api_has_no_target_path_override(self) -> None:
        self.assertEqual(tuple(inspect.signature(repair.publish_research_price_response).parameters), ("symbol", "body", "status", "media_type", "captured_at"))
        self.assertEqual(repair.RESEARCH_PRICE_GAP_REPAIR_AUTHORITY["max_requests"], 3)
        self.assertEqual(repair.RESEARCH_PRICE_GAP_REPAIR_AUTHORITY["max_rows"], 34)


class ResearchPriceGapRepairFixturePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.project = Path(__file__).resolve().parents[2]
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            self.project / CANONICAL_REGISTRY_PATH,
            project_root=self.project,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at="2026-09-06T00:00:00Z")
        self.stage12a = load_stage12_market_v1_scope(
            self.project / "config" / "stage12_market_v1_scope.json"
        )
        self.scope = load_stage12b_incremental_market_v1_scope(
            self.project / "config" / "stage12b_incremental_market_v1_scope.json"
        )
        for ordinal, (symbol, asset_type) in enumerate(
            (("AAPL", "equity"), ("MSFT", "equity"), ("SPY", "etf")), start=1
        ):
            self._seed_instrument(symbol, asset_type, ordinal)
        self.collector = repair._fixture_repair_collector(
            fixture_root=self.root,
            market_store=self.stores.market,
            scope=self.scope,
            stage12a_scope=self.stage12a,
            stage12a_scope_source=self.project / "config" / "stage12_market_v1_scope.json",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_instrument(self, symbol: str, asset_type: str, ordinal: int) -> None:
        run_id = f"repair-seed-{symbol.lower()}"
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                "INSERT INTO ingestion_runs(run_id, dataset_id, semantic_identity, command, scope_json, status, started_at, code_version) VALUES (?, 'market.stage10.instruments', ?, 'fixture.seed', '{}', 'running', '2026-09-06T00:00:00Z', 'fixture')",
                (run_id, f"{ordinal:x}" * 64),
            )
            connection.execute(
                "INSERT INTO stage10_instruments(instrument_id, provider, provider_symbol, asset_type, display_name, exchange_code, currency_segment, first_trade_date, identity_seed_sha256, captured_at, captured_precision, run_id) VALUES (?, 'fmp', ?, ?, ?, 'XNAS', 'provider_native', NULL, ?, '2026-09-06T00:00:00Z', 'datetime', ?)",
                (f"repair-{symbol.lower()}", symbol, asset_type, symbol, "a" * 64, run_id),
            )

    def _prepared(self, *, captured_at: str = "2026-09-06T12:00:00Z", body: bytes | None = None):
        item = repair.RESEARCH_PRICE_GAP_REPAIR_PLAN["MSFT"]
        return self.collector.prepare(
            Stage12BFixtureRequest(
                "MSFT", item["from"], item["to"], tuple(item["session_dates"]), captured_at
            ),
            Stage12BFixtureResponse(200, "application/json", _body("MSFT") if body is None else body, 0),
        )

    def test_twelve_row_publication_and_exact_replay_leave_store_fingerprint_unchanged(self) -> None:
        first = self.collector.publish(self._prepared())
        self.assertEqual((first.outcome, first.written_versions), ("published", 12))
        before = mutation_fingerprint(self.stores)
        replay = self.collector.publish(self._prepared(captured_at="2026-09-06T13:00:00Z"))
        self.assertEqual((replay.outcome, replay.written_versions), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_existing_requested_session_stops_changed_batch_atomically(self) -> None:
        self.collector.publish(self._prepared())
        before = mutation_fingerprint(self.stores)
        changed = json.loads(_body("MSFT"))
        changed[0]["high"] = 103
        changed[0]["close"] = 102
        with self.assertRaises(ConflictError):
            self.collector.publish(
                self._prepared(
                    captured_at="2026-09-06T13:00:00Z",
                    body=json.dumps(changed, separators=(",", ":")).encode(),
                )
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_alias_target_and_canonical_constructor_path_override_are_refused(self) -> None:
        alias = self.root / "market-alias.sqlite"
        alias.symlink_to(self.stores.market)
        with self.assertRaises(ValidationError):
            repair._fixture_repair_collector(
                fixture_root=self.root,
                market_store=alias,
                scope=self.scope,
                stage12a_scope=self.stage12a,
                stage12a_scope_source=self.project / "config" / "stage12_market_v1_scope.json",
            )
        self.assertEqual(
            tuple(inspect.signature(Stage12BIncrementalCollector._for_canonical_research_repair).parameters),
            ("scope", "stage12a_scope", "research_repair_authority_sha256", "research_repair_plan_sha256", "research_repair_instruments"),
        )


if __name__ == "__main__":
    unittest.main()
