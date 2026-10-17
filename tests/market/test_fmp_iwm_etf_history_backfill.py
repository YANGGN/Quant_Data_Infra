from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_universe_response,
)
from quant_data.market.fmp_iwm_etf_history import (
    load_iwm_etf_history_scope,
    run_iwm_etf_history,
)
from quant_data.market.fmp_iwm_etf_history_backfill import (
    IWM_ETF_HISTORY_BACKFILL_END_DATE,
    IWM_ETF_HISTORY_BACKFILL_START_DATE,
    IwmEtfHistoryBackfillPublisher,
    capture_iwm_etf_history_backfill,
    load_iwm_etf_history_backfill_scope,
    run_iwm_etf_history_backfill,
)
from quant_data.market.stage10_history_importer import Stage10HistoryImporter
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"
IWM_SCOPE_PATH = PROJECT_ROOT / "config" / "iwm_etf_history_v1_scope.json"
BACKFILL_SCOPE_PATH = (
    PROJECT_ROOT / "config" / "iwm_etf_history_backfill_v1_scope.json"
)
MIGRATION_TIME = "2026-08-27T12:00:00Z"
CAPTURE_TIME = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)


def _symbols(required: tuple[str, ...], prefix: str, count: int) -> tuple[str, ...]:
    return required + tuple(
        f"{prefix}{index:03d}"
        for index in range(1, count - len(required) + 1)
    )


def _universe_capture(path: str, symbols: tuple[str, ...]):
    body = json.dumps(
        [{"symbol": symbol, "name": f"Company {symbol}"} for symbol in symbols],
        separators=(",", ":"),
    ).encode("utf-8")
    return parse_fmp_stage10_universe_response(endpoint_path=path, body=body)


def _row(trade_date: str, value: int, source: int) -> dict[str, object]:
    return {
        "symbol": "IWM",
        "date": trade_date,
        "open": value,
        "high": value + 2,
        "low": value - 1,
        "close": value + 1,
        "volume": 1_000 + source,
        "change": 1,
        "changePercent": 1,
        "vwap": value + 1,
    }


def _retained_body() -> bytes:
    rows: list[dict[str, object]] = []
    cursor = date(2021, 8, 30)
    while len(rows) < 1_254:
        if cursor.weekday() < 5:
            rows.append(_row(cursor.isoformat(), 100 + len(rows) % 20, len(rows)))
        cursor += timedelta(days=1)
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


def _backfill_body() -> bytes:
    rows = [
        _row("2000-05-26", 50, 1),
        _row("2010-01-04", 60, 2),
        _row("2021-08-27", 70, 3),
    ]
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


class _Transport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> CapturedFmpStage10Response:
        self.calls.append(dict(kwargs))
        return CapturedFmpStage10Response(
            status=200,
            content_type="application/json; charset=utf-8",
            body=self.body,
        )


class FmpIwmEtfHistoryBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at=MIGRATION_TIME)
        self.base_registry = stage10_registry_profile(self.registry)
        self.base_scope = load_stage10_market_scope(BASE_SCOPE_PATH)
        self.iwm_scope = load_iwm_etf_history_scope(
            IWM_SCOPE_PATH,
            base_scope=self.base_scope,
        )
        self.scope = load_iwm_etf_history_backfill_scope(
            BACKFILL_SCOPE_PATH,
            iwm_scope=self.iwm_scope,
        )
        self._publish_frozen_base()
        self._publish_retained_iwm()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish_frozen_base(self) -> None:
        importer = Stage10HistoryImporter(
            self.stores,
            self.base_registry,
            self.base_scope,
            clock=lambda: CAPTURE_TIME,
        )
        captures = {
            "sp500_current": _universe_capture(
                FMP_STAGE10_SP500_CONSTITUENT_PATH,
                _symbols(("AAPL", "MSFT", "NVDA"), "S", 500),
            ),
            "nasdaq100_current": _universe_capture(
                FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
                _symbols(
                    (
                        "ALAB",
                        "CRWV",
                        "LITE",
                        "NBIS",
                        "RKLB",
                        "SNDK",
                        "SPCX",
                        "TER",
                        "WMT",
                    ),
                    "N",
                    100,
                ),
            ),
            "dow30_current": _universe_capture(
                FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
                _symbols(("AAPL", "MSFT", "JPM"), "D", 30),
            ),
        }
        for candidate in importer.prepare_universe_publications(captures):
            self.assertEqual(
                importer.publish_prepared(candidate).outcome,
                "succeeded",
            )

    def _publish_retained_iwm(self) -> None:
        report = run_iwm_etf_history(
            store_map=self.stores,
            registry=self.registry,
            scope=self.iwm_scope,
            transport=_Transport(_retained_body()),
            api_key="offline-fmp-key",
            captured_at=CAPTURE_TIME,
        )
        self.assertEqual(
            (report.outcome, report.price_row_count),
            ("succeeded", 1_254),
        )

    def test_fixed_request_appends_only_the_missing_interval(self) -> None:
        transport = _Transport(_backfill_body())
        report = run_iwm_etf_history_backfill(
            store_map=self.stores,
            registry=self.registry,
            scope=self.scope,
            transport=transport,
            api_key="offline-fmp-key",
            captured_at=CAPTURE_TIME,
        )
        self.assertEqual(
            transport.calls,
            [
                {
                    "path": "/stable/historical-price-eod/full",
                    "query": {
                        "symbol": "IWM",
                        "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
                        "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
                    },
                    "headers": {"apikey": "offline-fmp-key"},
                    "timeout_seconds": 45,
                    "max_bytes": 16 * 1024 * 1024,
                }
            ],
        )
        self.assertEqual(
            (
                report.outcome,
                report.earliest_trade_date,
                report.latest_trade_date,
                report.price_row_count,
            ),
            ("succeeded", "2000-05-26", "2021-08-27", 3),
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            stats = connection.execute(
                """
                SELECT count(*), min(price.trade_date)
                FROM stage10_daily_prices AS price
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=price.instrument_id
                WHERE instrument.provider='fmp'
                  AND instrument.provider_symbol='IWM'
                """
            ).fetchone()
            self.assertEqual(tuple(stats), (1_257, "2000-05-26"))
            backfill = connection.execute(
                """
                SELECT count(*), min(trade_date), max(trade_date)
                FROM stage10_daily_price_versions AS version
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=version.instrument_id
                WHERE instrument.provider_symbol='IWM'
                  AND trade_date BETWEEN ? AND ?
                """,
                (
                    IWM_ETF_HISTORY_BACKFILL_START_DATE,
                    IWM_ETF_HISTORY_BACKFILL_END_DATE,
                ),
            ).fetchone()
            self.assertEqual(tuple(backfill), (3, "2000-05-26", "2021-08-27"))
            self.assertEqual(
                connection.execute(
                    """
                    SELECT count(*) FROM stage10_universe_snapshots
                    WHERE universe_id='curated_etfs'
                    """
                ).fetchone()[0],
                2,
            )

    def test_prepared_exact_replay_is_a_total_no_write(self) -> None:
        transport = _Transport(_backfill_body())
        capture = capture_iwm_etf_history_backfill(
            api_key="offline-fmp-key",
            transport=transport,
        )
        publisher = IwmEtfHistoryBackfillPublisher(
            self.stores,
            self.registry,
            self.scope,
            clock=lambda: CAPTURE_TIME,
        )
        publisher.preflight_store()
        prepared = publisher.prepare(capture)
        first = publisher.publish(prepared)
        self.assertEqual(first.outcome, "succeeded")
        fingerprint = mutation_fingerprint(self.stores)

        replay = publisher.publish(prepared)
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), fingerprint)

    def test_writer_abort_rolls_back_capture_and_price_rows(self) -> None:
        capture = capture_iwm_etf_history_backfill(
            api_key="offline-fmp-key",
            transport=_Transport(_backfill_body()),
        )
        publisher = IwmEtfHistoryBackfillPublisher(
            self.stores,
            self.registry,
            self.scope,
            clock=lambda: CAPTURE_TIME,
        )
        prepared = publisher.prepare(capture)
        with sqlite3.connect(self.stores.path(StoreRole.MARKET)) as connection:
            connection.execute(
                """
                CREATE TRIGGER abort_iwm_backfill
                BEFORE INSERT ON stage10_daily_price_versions
                WHEN NEW.trade_date='2010-01-04'
                BEGIN
                    SELECT RAISE(ABORT, 'fixture abort');
                END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            publisher.publish(prepared)
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT count(*) FROM stage10_daily_prices AS price
                    JOIN stage10_instruments AS instrument
                      ON instrument.instrument_id=price.instrument_id
                    WHERE instrument.provider_symbol='IWM'
                      AND price.trade_date BETWEEN ? AND ?
                    """,
                    (
                        IWM_ETF_HISTORY_BACKFILL_START_DATE,
                        IWM_ETF_HISTORY_BACKFILL_END_DATE,
                    ),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT count(*) FROM stage10_daily_price_captures
                    WHERE request_scope_json LIKE '%fixed_missing_interval%'
                    """
                ).fetchone()[0],
                0,
            )

    def test_out_of_scope_response_is_rejected_before_publication(self) -> None:
        body = json.dumps(
            [_row("2021-08-30", 70, 1)],
            separators=(",", ":"),
        ).encode("utf-8")
        before = mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            run_iwm_etf_history_backfill(
                store_map=self.stores,
                registry=self.registry,
                scope=self.scope,
                transport=_Transport(body),
                api_key="offline-fmp-key",
                captured_at=CAPTURE_TIME,
            )
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_existing_gap_row_blocks_before_network(self) -> None:
        run_iwm_etf_history_backfill(
            store_map=self.stores,
            registry=self.registry,
            scope=self.scope,
            transport=_Transport(_backfill_body()),
            api_key="offline-fmp-key",
            captured_at=CAPTURE_TIME,
        )
        transport = _Transport(_backfill_body())
        with self.assertRaises(ConflictError):
            run_iwm_etf_history_backfill(
                store_map=self.stores,
                registry=self.registry,
                scope=self.scope,
                transport=transport,
                api_key="offline-fmp-key",
                captured_at=CAPTURE_TIME,
            )
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
