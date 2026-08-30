from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.fmp_bulk_daily_prices import CapturedFmpStage10Response
from quant_data.market.fmp_iwm_etf_history import (
    IWM_ETF_HISTORY_SYMBOL,
    IwmEtfHistoryPublisher,
    load_iwm_etf_history_scope,
    run_iwm_etf_history,
)
from quant_data.market.fmp_bulk_daily_prices import (
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_universe_response,
)
from quant_data.market.stage10_history_importer import Stage10HistoryImporter
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection, stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"
IWM_SCOPE_PATH = PROJECT_ROOT / "config" / "iwm_etf_history_v1_scope.json"
MIGRATION_TIME = "2026-08-27T12:00:00Z"
CAPTURE_TIME = datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc)


def _symbols(required: tuple[str, ...], prefix: str, count: int) -> tuple[str, ...]:
    return required + tuple(f"{prefix}{index:03d}" for index in range(1, count - len(required) + 1))


def _universe_capture(path: str, symbols: tuple[str, ...]):
    body = json.dumps(
        [{"symbol": symbol, "name": f"Company {symbol}"} for symbol in symbols],
        separators=(",", ":"),
    ).encode("utf-8")
    return parse_fmp_stage10_universe_response(endpoint_path=path, body=body)


def _price_body(*, close_second: int = 102) -> bytes:
    rows = [
        {
            "symbol": "IWM",
            "date": "2000-05-26",
            "open": 100,
            "high": 105,
            "low": 99,
            "close": 101,
            "volume": 1000,
            "change": 1,
            "changePercent": 1,
            "vwap": 101,
        },
        {
            "symbol": "IWM",
            "date": "2000-05-30",
            "open": 100,
            "high": 105,
            "low": 99,
            "close": close_second,
            "volume": 1001,
            "change": close_second - 100,
            "changePercent": close_second - 100,
            "vwap": close_second,
        },
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


class _FailingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedFmpStage10Response:
        del kwargs
        self.calls += 1
        raise AssertionError("network must not run after failed store preflight")


class FmpIwmEtfHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.base_registry = stage10_registry_profile(self.registry)
        initialize_all(self.stores, self.registry, applied_at=MIGRATION_TIME)
        self.base_scope = load_stage10_market_scope(BASE_SCOPE_PATH)
        self.scope = load_iwm_etf_history_scope(IWM_SCOPE_PATH, base_scope=self.base_scope)
        self._publish_frozen_base()

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
                    ("ALAB", "CRWV", "LITE", "NBIS", "RKLB", "SNDK", "SPCX", "TER", "WMT"),
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
            self.assertEqual(importer.publish_prepared(candidate).outcome, "succeeded")

    def _run(self, body: bytes):
        transport = _Transport(body)
        report = run_iwm_etf_history(
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
                    "query": {"symbol": "IWM"},
                    "headers": {"apikey": "offline-fmp-key"},
                    "timeout_seconds": 45,
                    "max_bytes": 16 * 1024 * 1024,
                }
            ],
        )
        return report

    def test_one_fixed_capture_appends_iwm_and_preserves_frozen_95_snapshot(self) -> None:
        report = self._run(_price_body())

        self.assertEqual((report.outcome, report.price_row_count), ("succeeded", 2))
        seed = hashlib.sha256(
            json.dumps(
                {
                    "identity_version": "stage10.fmp.instrument.v1",
                    "provider": "fmp",
                    "provider_symbol": IWM_ETF_HISTORY_SYMBOL,
                    "asset_type": "etf",
                    "currency_segment": "provider_native",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        expected_instrument_id = stable_id("stage10_instrument", seed)
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(
                tuple(
                    connection.execute(
                        """
                        SELECT provider_symbol, asset_type, currency_segment, identity_seed_sha256
                        FROM stage10_instruments WHERE instrument_id=?
                        """,
                        (expected_instrument_id,),
                    ).fetchone()
                ),
                ("IWM", "etf", "provider_native", seed),
            )
            snapshots = tuple(
                connection.execute(
                    """
                    SELECT scope.scope_manifest_sha256, snapshot.member_count
                    FROM stage10_universe_snapshots AS snapshot
                    JOIN stage10_scope_snapshots AS scope
                      ON scope.scope_snapshot_id=snapshot.scope_snapshot_id
                    WHERE snapshot.universe_id='curated_etfs'
                    ORDER BY snapshot.member_count
                    """
                )
            )
            self.assertEqual(
                tuple((row["scope_manifest_sha256"], row["member_count"]) for row in snapshots),
                (
                    (self.base_scope.manifest_sha256, 95),
                    (self.scope.manifest_sha256, 96),
                ),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM stage10_daily_prices WHERE instrument_id=?",
                    (expected_instrument_id,),
                ).fetchone()[0],
                2,
            )

    def test_exact_replay_has_zero_persistent_writes_and_correction_appends(self) -> None:
        first = self._run(_price_body())
        self.assertEqual(first.outcome, "succeeded")
        before_replay = mutation_fingerprint(self.stores)

        replay = self._run(_price_body())
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), before_replay)

        corrected = self._run(_price_body(close_second=104))
        self.assertEqual(corrected.outcome, "succeeded")
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            current = connection.execute(
                """
                SELECT version.close_value, version.correction_sequence
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id=current.current_version_id
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=current.instrument_id
                WHERE instrument.provider_symbol='IWM' AND current.trade_date='2000-05-30'
                """
            ).fetchone()
            self.assertEqual(tuple(current), ("104", 2))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM stage10_daily_price_captures WHERE provider_symbol='IWM'"
                ).fetchone()[0],
                2,
            )

    def test_corrupted_successor_membership_is_rejected_before_network(self) -> None:
        self._run(_price_body())
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            successor_snapshot_id = connection.execute(
                """
                SELECT snapshot.universe_snapshot_id
                FROM stage10_universe_snapshots AS snapshot
                JOIN stage10_scope_snapshots AS scope
                  ON scope.scope_snapshot_id=snapshot.scope_snapshot_id
                WHERE scope.scope_manifest_sha256=? AND snapshot.universe_id='curated_etfs'
                """,
                (self.scope.manifest_sha256,),
            ).fetchone()[0]
            outsider_id = connection.execute(
                "SELECT instrument_id FROM stage10_instruments WHERE provider_symbol='AAPL'"
            ).fetchone()[0]
        with sqlite3.connect(self.stores.path(StoreRole.MARKET)) as connection:
            connection.execute(
                """
                INSERT INTO stage10_universe_snapshot_members (
                    universe_snapshot_id, instrument_id, source_row
                ) VALUES (?, ?, 97)
                """,
                (successor_snapshot_id, outsider_id),
            )

        transport = _FailingTransport()
        with self.assertRaises(ConflictError):
            run_iwm_etf_history(
                store_map=self.stores,
                registry=self.registry,
                scope=self.scope,
                transport=transport,
                api_key="offline-fmp-key",
                captured_at=CAPTURE_TIME,
            )
        self.assertEqual(transport.calls, 0)


if __name__ == "__main__":
    unittest.main()
