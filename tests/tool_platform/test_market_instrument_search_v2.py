from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.stage10_series import (
    Stage10DailyPriceRepository,
    Stage10InstrumentSearchQuery,
)
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.arguments import (
    Stage10MarketInstrumentSearchArgumentsV2,
)
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.market_tickers import (
    INSTRUMENT_SEARCH_TOOL_NAME,
    invoke_stage10_market_instrument_search,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CAPTURED_AT = "2026-08-27T00:00:00Z"


def _fields(result: object) -> tuple[dict[str, object], ...]:
    return tuple(
        {field.name: field.value for field in record.fields}
        for record in getattr(result, "records")
    )


class MarketInstrumentSearchV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at=_CAPTURED_AT)
        self._seed_identities()
        self.repository = Stage10DailyPriceRepository(self.stores, self.registry)
        self.baseline = mutation_fingerprint(self.stores)
        self.registry_sha256 = hashlib.sha256(
            (PROJECT_ROOT / CANONICAL_REGISTRY_PATH).read_bytes()
        ).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(
            self.baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        self.temporary.cleanup()

    def _seed_identities(self) -> None:
        identities = (
            (
                "market-search-aapl",
                "AAPL",
                "equity",
                "Apple Inc.",
                "XNAS",
                "1980-12-12",
            ),
            (
                "market-search-under",
                "A_B",
                "equity",
                "Under_score Holdings",
                "XNYS",
                "2001-01-01",
            ),
            (
                "market-search-iwm",
                "IWM",
                "etf",
                "iShares Russell 2000 ETF",
                "ARCX",
                "2000-05-22",
            ),
            (
                "market-search-percent",
                "P%Q",
                "equity",
                "Percent Holdings",
                "XNAS",
                None,
            ),
            (
                "market-search-zzzz",
                "ZZZZ",
                "index",
                None,
                None,
                None,
            ),
            (
                "market-search-lower",
                "aapl",
                "equity",
                "Lowercase fixture",
                "XNAS",
                "2002-02-02",
            ),
        )
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "market_search_seed_run",
                    "market.stage10.instruments",
                    "a" * 64,
                    "fixture.seed",
                    "{}",
                    _CAPTURED_AT,
                    "fixture",
                ),
            )
            connection.executemany(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type,
                    display_name, exchange_code, currency_segment,
                    first_trade_date, identity_seed_sha256, captured_at,
                    captured_precision, run_id
                ) VALUES (?, 'fmp', ?, ?, ?, ?, 'provider_native', ?, ?, ?,
                          'datetime', 'market_search_seed_run')
                """,
                tuple(
                    (
                        instrument_id,
                        ticker,
                        asset_type,
                        display_name,
                        exchange_code,
                        first_trade_date,
                        f"{index:064x}",
                        _CAPTURED_AT,
                    )
                    for index, (
                        instrument_id,
                        ticker,
                        asset_type,
                        display_name,
                        exchange_code,
                        first_trade_date,
                    ) in enumerate(identities, start=1)
                ),
            )

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self.registry_sha256,
            request_id="market-instrument-search-v2-test",
            api_version="1.0",
            tool_name=INSTRUMENT_SEARCH_TOOL_NAME,
            tool_version="2.0.0",
            operation_graph_id="tool_platform.market.search_instruments.v2",
            operation_version="2.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(Decimal("0"), _CAPTURED_AT),
        )

    def _call(
        self,
        *,
        query: str = "",
        asset_type: str | None = None,
        cursor: str | None = None,
        limit: int = 2,
    ):
        return invoke_stage10_market_instrument_search(
            INSTRUMENT_SEARCH_TOOL_NAME,
            Stage10MarketInstrumentSearchArgumentsV2(
                query=query,
                asset_type=asset_type,
                cursor=cursor,
                limit=limit,
            ),
            self._context(),
            self.registry,
        )

    def test_repository_search_is_literal_paginated_and_read_only(self) -> None:
        first = self.repository.search_instruments(
            Stage10InstrumentSearchQuery("", None, 2)
        )
        second = self.repository.search_instruments(
            Stage10InstrumentSearchQuery(
                "",
                None,
                2,
                (first.instruments[-1].ticker, first.instruments[-1].instrument_id),
            )
        )
        third = self.repository.search_instruments(
            Stage10InstrumentSearchQuery(
                "",
                None,
                2,
                (second.instruments[-1].ticker, second.instruments[-1].instrument_id),
            )
        )
        self.assertEqual(
            tuple(item.ticker for item in first.instruments),
            ("AAPL", "A_B"),
        )
        self.assertEqual(
            tuple(item.ticker for item in second.instruments),
            ("IWM", "P%Q"),
        )
        self.assertEqual(
            tuple(item.ticker for item in third.instruments),
            ("ZZZZ", "aapl"),
        )
        self.assertEqual(
            (first.truncated, second.truncated, third.truncated),
            (True, True, False),
        )
        for query, expected in (
            (" _ ", ("A_B",)),
            ("%", ("P%Q",)),
            ("apple", ("AAPL",)),
        ):
            with self.subTest(query=query):
                found = self.repository.search_instruments(
                    Stage10InstrumentSearchQuery(query, None, 100)
                )
                self.assertEqual(
                    tuple(item.ticker for item in found.instruments),
                    expected,
                )
        etfs = self.repository.search_instruments(
            Stage10InstrumentSearchQuery("", "etf", 100)
        )
        self.assertEqual(tuple(item.ticker for item in etfs.instruments), ("IWM",))
        self.assertEqual(
            self.baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        with self.assertRaises(ValidationError):
            Stage10InstrumentSearchQuery("bad\u0001", None, 1)
        with self.assertRaises(ResourceLimitError):
            Stage10InstrumentSearchQuery("", None, 101)

    def test_adapter_binds_cursor_and_projects_identity_only_records(self) -> None:
        first = self._call()
        first_fields = _fields(first)
        self.assertEqual(
            tuple(item["ticker"] for item in first_fields),
            ("AAPL", "A_B"),
        )
        self.assertEqual(
            set(first_fields[0]),
            {
                "ticker",
                "instrument_id",
                "asset_type",
                "display_name",
                "exchange_code",
                "first_trade_date",
                "provider",
                "currency_segment",
            },
        )
        self.assertEqual(first_fields[0]["provider"], "fmp")
        self.assertEqual(first.truncation.total_known_count, None)
        self.assertEqual(
            tuple(item.code for item in first.warnings),
            ("current_universe_not_point_in_time", "result_truncated"),
        )
        self.assertEqual(
            tuple(item.dataset_id for item in first.lineage),
            ("market.stage10.instruments",),
        )

        second = self._call(cursor=first.truncation.next_cursor)
        third = self._call(cursor=second.truncation.next_cursor)
        self.assertEqual(
            tuple(item["ticker"] for item in _fields(second)),
            ("IWM", "P%Q"),
        )
        self.assertEqual(
            tuple(item["ticker"] for item in _fields(third)),
            ("ZZZZ", "aapl"),
        )
        self.assertFalse(third.truncation.has_more)
        self.assertIsNone(third.truncation.next_cursor)
        with self.assertRaises(ValidationError):
            self._call(query="apple", cursor=first.truncation.next_cursor)
        with self.assertRaises(ValidationError):
            self._call(asset_type="equity", cursor=first.truncation.next_cursor)
        with self.assertRaises(ValidationError):
            self._call(cursor=f"{first.truncation.next_cursor}x")
