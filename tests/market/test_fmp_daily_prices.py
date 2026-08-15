from __future__ import annotations

import json
import sqlite3
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.fmp_daily_prices import (
    CapturedFmpResponse,
    FMP_APPROVED_TARGET_ROOT,
    FmpDailyPriceImporter,
    FmpDailyPriceRequest,
    PreparedFmpDailyPriceBackfill,
    StdlibFmpDailyPriceTransport,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry, stage9_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
EXPECTED_DATES = (
    "2026-07-01", "2026-07-02", "2026-07-06", "2026-07-07",
    "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13",
    "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
    "2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29",
    "2026-07-30", "2026-07-31",
)
KEY = "test-only-secret-key"


def _rows(*, correction: bool = False) -> list[dict[str, object]]:
    rows = [
        {
            "symbol": "SPY",
            "date": trade_date,
            "open": 600 + index,
            "high": 603 + index,
            "low": 599 + index,
            "close": 602 + index,
            "volume": 1_000_000 + index,
            "change": 2,
            "changePercent": 0.3,
            "vwap": 601 + index,
        }
        for index, trade_date in enumerate(EXPECTED_DATES)
    ]
    if correction:
        rows[10]["close"] = 603 + 10
    return rows


def _body(*, correction: bool = False, rows=None) -> bytes:
    value = _rows(correction=correction) if rows is None else rows
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class _Transport:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "application/json") -> None:
        self.body = body
        self.status = status
        self.content_type = content_type
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs):
        self.calls.append(dict(kwargs))
        return CapturedFmpResponse(self.status, self.content_type, self.body)


class FmpDailyPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = stage9_registry_profile(
            load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        )
        self.stores = explicit_store_map(self.root / "stores")
        initialize_all(self.stores, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _importer(self, *, day: int = 11) -> FmpDailyPriceImporter:
        return FmpDailyPriceImporter(
            self.stores,
            self.registry,
            clock=lambda: datetime(2026, 8, day, 14, 30, tzinfo=timezone.utc),
        )

    def test_prepare_fetches_once_before_locks_and_publish_replays_without_writes(self) -> None:
        before_prepare = mutation_fingerprint(self.stores)
        transport = _Transport(_body())
        importer = self._importer()
        prepared = importer.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=transport)
        self.assertEqual(mutation_fingerprint(self.stores), before_prepare)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["path"], "/stable/historical-price-eod/full")
        self.assertEqual(call["query"], {"symbol": "SPY", "from": "2026-07-01", "to": "2026-07-31"})
        self.assertEqual(call["headers"], {"apikey": KEY, "Accept": "application/json"})
        self.assertNotIn(KEY, repr(prepared))

        nonmarket_before = {
            role.value: before_prepare["stores"][role.value]
            for role in (StoreRole.MACRO, StoreRole.COMPANY, StoreRole.NEWS)
        }
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network during publish")):
            first = importer.publish_prepared(prepared)
        self.assertEqual(first.outcome, "succeeded")
        self.assertGreater(first.written_count, 0)
        after_first = mutation_fingerprint(self.stores)
        self.assertEqual(
            {role: after_first["stores"][role] for role in nonmarket_before},
            nonmarket_before,
        )
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_daily_prices").fetchone()[0], 22)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_daily_price_versions").fetchone()[0], 22)
            capture = connection.execute(
                "SELECT response_bytes, request_scope_json, captured_at FROM fmp_daily_price_captures"
            ).fetchone()
            self.assertEqual(capture["response_bytes"], _body())
            self.assertNotIn(KEY, capture["request_scope_json"])
            self.assertEqual(capture["captured_at"], "2026-08-11T14:30:00.000000Z")

        before_replay = mutation_fingerprint(self.stores)
        replay = importer.publish_prepared(prepared)
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(mutation_fingerprint(self.stores), before_replay)
        self.assertNotIn(KEY, repr(first))

    def test_changed_batch_appends_one_correction_and_moves_current(self) -> None:
        base = self._importer(day=11)
        base.publish_prepared(
            base.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(_body()))
        )
        correction = self._importer(day=12)
        receipt = correction.publish_prepared(
            correction.prepare(
                FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(_body(correction=True))
            )
        )
        self.assertEqual(receipt.outcome, "succeeded")
        with read_connection(self.stores, StoreRole.MARKET) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_daily_price_captures").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_daily_price_versions").fetchone()[0], 23)
            versions = tuple(
                connection.execute(
                    """
                    SELECT correction_sequence, close_value, supersedes_version_id
                    FROM fmp_daily_price_versions
                    WHERE trade_date='2026-07-16' ORDER BY correction_sequence
                    """
                )
            )
            self.assertEqual(tuple(row["correction_sequence"] for row in versions), (1, 2))
            self.assertIsNone(versions[0]["supersedes_version_id"])
            self.assertEqual(versions[1]["supersedes_version_id"], connection.execute(
                "SELECT version_id FROM fmp_daily_price_versions WHERE trade_date='2026-07-16' AND correction_sequence=1"
            ).fetchone()[0])
            current = connection.execute(
                """
                SELECT version.close_value FROM fmp_daily_prices AS current
                JOIN fmp_daily_price_versions AS version ON version.version_id=current.current_version_id
                WHERE current.trade_date='2026-07-16'
                """
            ).fetchone()[0]
            self.assertEqual(current, "613")

    def test_migration_enforces_immutable_versions_and_forward_only_current(self) -> None:
        base = self._importer(day=11)
        base.publish_prepared(
            base.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(_body()))
        )
        correction = self._importer(day=12)
        correction.publish_prepared(
            correction.prepare(
                FmpDailyPriceRequest(),
                api_key=KEY,
                transport=_Transport(_body(correction=True)),
            )
        )
        connection = sqlite3.connect(self.stores.path(StoreRole.MARKET))
        try:
            old_version_id = connection.execute(
                """
                SELECT version_id
                FROM fmp_daily_price_versions
                WHERE trade_date='2026-07-16' AND correction_sequence=1
                """
            ).fetchone()[0]
            hostile_statements = (
                (
                    "UPDATE fmp_daily_price_versions SET close_value='1' "
                    "WHERE trade_date='2026-07-16' AND correction_sequence=1",
                    (),
                ),
                (
                    "DELETE FROM fmp_daily_price_versions "
                    "WHERE trade_date='2026-07-16' AND correction_sequence=1",
                    (),
                ),
                (
                    "UPDATE fmp_daily_prices SET current_version_id=? "
                    "WHERE trade_date='2026-07-16'",
                    (old_version_id,),
                ),
                (
                    "UPDATE fmp_daily_prices SET trade_date='2026-07-03' "
                    "WHERE trade_date='2026-07-16'",
                    (),
                ),
                (
                    "DELETE FROM fmp_daily_prices WHERE trade_date='2026-07-16'",
                    (),
                ),
            )
            for statement, parameters in hostile_statements:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)
                    connection.rollback()
        finally:
            connection.close()

    def test_migration_requires_complete_predecessor_lineage_for_versions_and_current(self) -> None:
        base = self._importer(day=11)
        base.publish_prepared(
            base.prepare(
                FmpDailyPriceRequest(),
                api_key=KEY,
                transport=_Transport(_body()),
            )
        )
        correction = self._importer(day=12)
        correction.publish_prepared(
            correction.prepare(
                FmpDailyPriceRequest(),
                api_key=KEY,
                transport=_Transport(_body(correction=True)),
            )
        )
        connection = sqlite3.connect(self.stores.path(StoreRole.MARKET))
        try:
            predecessor = connection.execute(
                """
                SELECT version_id FROM fmp_daily_price_versions
                WHERE trade_date='2026-07-16' AND correction_sequence=1
                """
            ).fetchone()[0]
            source_version = connection.execute(
                """
                SELECT version_id FROM fmp_daily_price_versions
                WHERE trade_date='2026-07-17' AND correction_sequence=1
                """
            ).fetchone()[0]

            def clone(
                version_id: str,
                trade_date: str,
                sequence: int,
                supersedes: str | None,
            ) -> None:
                connection.execute(
                    """
                    INSERT INTO fmp_daily_price_versions (
                        version_id, instrument_id, trade_date, provider,
                        price_variant, currency_segment, open_value, high_value,
                        low_value, close_value, volume, available_at,
                        available_precision, captured_at, captured_precision,
                        correction_sequence, supersedes_version_id, capture_id,
                        artifact_id, snapshot_id, run_id, source_row
                    )
                    SELECT ?, instrument_id, ?, provider, price_variant,
                           currency_segment, open_value, high_value, low_value,
                           close_value, volume, available_at,
                           available_precision, captured_at, captured_precision,
                           ?, ?, capture_id, artifact_id, snapshot_id, run_id,
                           source_row
                    FROM fmp_daily_price_versions WHERE version_id=?
                    """,
                    (version_id, trade_date, sequence, supersedes, source_version),
                )

            hostile = (
                ("hostile-seq1-predecessor", 1, predecessor),
                ("hostile-seq2-null", 2, None),
                ("hostile-seq2-cross-date", 2, predecessor),
            )
            for version_id, sequence, supersedes in hostile:
                with self.subTest(version_id=version_id):
                    with self.assertRaises(sqlite3.IntegrityError):
                        clone(version_id, "2026-07-03", sequence, supersedes)
                    connection.rollback()

            connection.execute(
                "DROP TRIGGER fmp_daily_price_versions_lineage_guard"
            )
            clone("corrupt-current-insert", "2026-07-03", 1, predecessor)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO fmp_daily_prices (
                        instrument_id, trade_date, provider, price_variant,
                        currency_segment, current_version_id
                    )
                    SELECT instrument_id, trade_date, provider, price_variant,
                           currency_segment, version_id
                    FROM fmp_daily_price_versions
                    WHERE version_id='corrupt-current-insert'
                    """
                )
            connection.rollback()
            connection.execute(
                "DROP TRIGGER fmp_daily_price_versions_immutable_update"
            )
            connection.execute(
                """
                UPDATE fmp_daily_price_versions SET supersedes_version_id=?
                WHERE version_id=?
                """,
                (predecessor, source_version),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE fmp_daily_prices
                    SET current_version_id=current_version_id
                    WHERE trade_date='2026-07-17'
                    """
                )
        finally:
            connection.close()

    def test_hostile_responses_and_scope_fail_before_store_mutation(self) -> None:
        importer = self._importer()
        baseline = mutation_fingerprint(self.stores)
        cases: list[tuple[str, object]] = []
        missing = _rows(); missing[0].pop("vwap"); cases.append(("missing", missing))
        extra = _rows(); extra[0]["adjClose"] = 1; cases.append(("extra", extra))
        wrong_symbol = _rows(); wrong_symbol[0]["symbol"] = "QQQ"; cases.append(("symbol", wrong_symbol))
        duplicate = _rows(); duplicate[-1]["date"] = duplicate[0]["date"]; cases.append(("duplicate", duplicate))
        outside = _rows(); outside[-1]["date"] = "2026-08-03"; cases.append(("range", outside))
        ohlc = _rows(); ohlc[0]["low"] = ohlc[0]["high"] + 1; cases.append(("ohlc", ohlc))
        volume = _rows(); volume[0]["volume"] = -1; cases.append(("volume", volume))
        boolean = _rows(); boolean[0]["close"] = True; cases.append(("boolean", boolean))
        for name, rows in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    importer.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(_body(rows=rows)))
                self.assertEqual(mutation_fingerprint(self.stores), baseline)

        malformed = (
            b'[{"symbol":"SPY","symbol":"SPY"}]',
            b"\xff",
            b"[NaN]",
        )
        for body in malformed:
            with self.subTest(body=body[:12]):
                with self.assertRaises(ValidationError):
                    importer.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(body))
                self.assertEqual(mutation_fingerprint(self.stores), baseline)

        for status, content_type in ((302, "application/json"), (403, "application/json"), (200, "text/html")):
            with self.subTest(status=status, content_type=content_type):
                with self.assertRaises(StoreUnavailableError) as captured:
                    importer.prepare(
                        FmpDailyPriceRequest(),
                        api_key=KEY,
                        transport=_Transport(_body(), status=status, content_type=content_type),
                    )
                self.assertNotIn(KEY, str(captured.exception))
        with self.assertRaises(ResourceLimitError):
            CapturedFmpResponse(200, "application/json", b"x" * (262_144 + 1))
        with self.assertRaises(ValidationError):
            FmpDailyPriceRequest(symbol="QQQ")
        with self.assertRaises(ValidationError) as captured:
            importer.prepare(FmpDailyPriceRequest(), api_key="", transport=_Transport(_body()))
        self.assertNotIn(KEY, str(captured.exception))

    def test_prepared_candidates_are_instance_bound_and_forgery_resistant(self) -> None:
        first = self._importer()
        second = self._importer()
        prepared = first.prepare(FmpDailyPriceRequest(), api_key=KEY, transport=_Transport(_body()))
        with self.assertRaises(ValidationError):
            second.publish_prepared(prepared)
        with self.assertRaises(ValidationError):
            first.publish_prepared(PreparedFmpDailyPriceBackfill())

    def test_stdlib_transport_never_places_key_in_target_and_does_not_follow_redirects(self) -> None:
        response = mock.Mock()
        response.status = 302
        response.getheader.side_effect = lambda name: {
            "Content-Length": "2",
            "Content-Type": "application/json",
        }.get(name)
        response.read.return_value = b"[]"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch(
            "quant_data.market.fmp_daily_prices.http.client.HTTPSConnection",
            return_value=connection,
        ):
            transport = StdlibFmpDailyPriceTransport(
                explicit_store_map(FMP_APPROVED_TARGET_ROOT / "stores")
            )
            captured = transport.get(
                path="/stable/historical-price-eod/full",
                query={"symbol": "SPY", "from": "2026-07-01", "to": "2026-07-31"},
                headers={"apikey": KEY, "Accept": "application/json"},
                timeout_seconds=30,
                max_bytes=262_144,
            )
        self.assertEqual(captured.status, 302)
        target = connection.request.call_args.args[1]
        self.assertNotIn(KEY, target)
        self.assertEqual(connection.request.call_count, 1)


        with mock.patch(
            "quant_data.market.fmp_daily_prices.http.client.HTTPSConnection"
        ) as https:
            with self.assertRaises(ValidationError):
                self._importer().prepare(
                    FmpDailyPriceRequest(),
                    api_key=KEY,
                    transport=transport,
                )
        https.assert_not_called()

if __name__ == "__main__":
    unittest.main()
