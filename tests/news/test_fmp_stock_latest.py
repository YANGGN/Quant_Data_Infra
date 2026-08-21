from __future__ import annotations

import http.client
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.migrations import migrate_and_register_store
from quant_data.news.fmp_stock_latest import (
    CapturedFmpStockLatestResponse,
    FMP_STOCK_LATEST_MAX_BYTES,
    FMP_STOCK_LATEST_PATH,
    FmpStockLatestImporter,
    FmpStockLatestRequest,
    _parse_rows,
)
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config/system_registry.json"
FIXTURE_PATH = PROJECT_ROOT / "tests/fixtures/fmp_stock_latest/stock_latest_page0.json"
FIXED_TIME = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)


class _Transport:
    def __init__(self, body: bytes, news_path: Path) -> None:
        self.body = body
        self.news_path = news_path
        self.calls = 0
        self.lock_was_available = False

    def get(self, **kwargs: object) -> CapturedFmpStockLatestResponse:
        self.calls += 1
        if (
            kwargs.get("path") != FMP_STOCK_LATEST_PATH
            or kwargs.get("query") != {"page": "0", "limit": "1000"}
            or kwargs.get("headers") != {"apikey": "offline-key", "Accept": "application/json"}
            or kwargs.get("timeout_seconds") != 60
            or kwargs.get("max_bytes") != FMP_STOCK_LATEST_MAX_BYTES
        ):
            raise AssertionError("FMP stock-latest request drifted")
        with StoreWriteLock(self.news_path, timeout_seconds=0):
            self.lock_was_available = True
        return CapturedFmpStockLatestResponse(200, "application/json; charset=utf-8", self.body)


class _RaisingTransport:
    def __init__(
        self,
        news_path: Path,
        exception_type: type[Exception] = RuntimeError,
    ) -> None:
        self.news_path = news_path
        self.exception_type = exception_type
        self.calls = 0
        self.lock_was_available = False

    def get(self, **kwargs: object) -> CapturedFmpStockLatestResponse:
        del kwargs
        self.calls += 1
        with StoreWriteLock(self.news_path, timeout_seconds=0):
            self.lock_was_available = True
        raise self.exception_type("synthetic transport failure with offline-key")


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class FmpStockLatestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.store_map = _stores(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        migrate_and_register_store(
            self.store_map,
            self.registry,
            StoreRole.NEWS,
            applied_at="2026-08-14T15:00:00Z",
        )
        self.body = FIXTURE_PATH.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _importer(self) -> FmpStockLatestImporter:
        return FmpStockLatestImporter(
            self.store_map,
            self.registry,
            clock=lambda: FIXED_TIME,
        )

    def test_one_request_intent_raw_capture_private_versions_and_precision(self) -> None:
        transport = _Transport(self.body, self.store_map.news)
        importer = self._importer()
        with mock.patch.object(http.client, "HTTPSConnection", side_effect=AssertionError("network")):
            receipt = importer.run_once(api_key="offline-key", transport=transport)
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertEqual(receipt.article_count, 3)
        self.assertTrue(transport.lock_was_available)
        self.assertEqual(transport.calls, 1)
        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_attempts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_outcomes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_captures").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_articles").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_article_versions").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_capture_articles").fetchone()[0], 3)
            capture = connection.execute(
                """
                SELECT response_bytes, response_sha256, completeness, row_count
                FROM fmp_stock_latest_captures
                """
            ).fetchone()
            self.assertEqual(bytes(capture["response_bytes"]), self.body)
            self.assertEqual(
                capture["response_sha256"],
                hashlib.sha256(self.body).hexdigest(),
            )
            self.assertEqual(
                (capture["completeness"], capture["row_count"]),
                ("partial", 3),
            )
            self.assertEqual(len(_parse_rows(bytes(capture["response_bytes"]))), 3)
            precisions = tuple(
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT published_precision, published_offset_status, published_normalized_at
                    FROM fmp_stock_latest_article_versions
                    ORDER BY source_row
                    """
                )
            )
        self.assertEqual(
            precisions,
            (
                ("datetime_offset", "known", "2026-08-14T09:30:00.000000+00:00"),
                ("date", "unknown", None),
                ("datetime_naive", "unknown", None),
            ),
        )
        with self.assertRaises(ConflictError):
            importer.run_once(api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 1)

    def test_prepared_replay_is_a_total_no_write(self) -> None:
        transport = _Transport(self.body, self.store_map.news)
        importer = self._importer()
        attempt = importer.reserve_attempt(FmpStockLatestRequest())
        prepared = importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        first = importer.publish_prepared(prepared)
        before = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)
        replay = importer.publish_prepared(prepared)
        after = store_mutation_fingerprint(self.store_map, StoreRole.NEWS)
        self.assertEqual(first.outcome, "succeeded")
        self.assertEqual((replay.outcome, replay.written_count), ("unchanged", 0))
        self.assertEqual(before, after)
        self.assertEqual(transport.calls, 1)

    def test_attempt_capability_is_consumed_before_transport_exception(self) -> None:
        transport = _RaisingTransport(self.store_map.news)
        importer = self._importer()
        attempt = importer.reserve_attempt()
        with self.assertRaises(StoreUnavailableError) as rejected:
            importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        self.assertNotIn("offline-key", str(rejected.exception))
        self.assertIsNone(rejected.exception.__cause__)
        self.assertIsNone(rejected.exception.__context__)
        self.assertTrue(transport.lock_was_available)
        with self.assertRaises(ConflictError):
            importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 1)

    def test_every_transport_exception_is_sanitized_and_records_one_terminal_failure(self) -> None:
        secret = "offline-key"
        for exception_type in (
            RuntimeError,
            ValidationError,
            StoreUnavailableError,
            ResourceLimitError,
        ):
            with self.subTest(exception_type=exception_type.__name__):
                case_root = self.root / exception_type.__name__
                case_root.mkdir()
                case_stores = _stores(case_root)
                migrate_and_register_store(
                    case_stores,
                    self.registry,
                    StoreRole.NEWS,
                    applied_at="2026-08-14T15:00:00Z",
                )
                importer = FmpStockLatestImporter(
                    case_stores,
                    self.registry,
                    clock=lambda: FIXED_TIME,
                )
                transport = _RaisingTransport(case_stores.news, exception_type)
                with self.assertRaises(StoreUnavailableError) as rejected:
                    importer.run_once(api_key=secret, transport=transport)
                self.assertEqual(transport.calls, 1)
                self.assertTrue(transport.lock_was_available)
                self.assertNotIn(secret, str(rejected.exception))
                self.assertIsNone(rejected.exception.__cause__)
                self.assertIsNone(rejected.exception.__context__)
                with read_connection(case_stores, StoreRole.NEWS) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT outcome_kind FROM fmp_stock_latest_outcomes"
                        ).fetchone()[0],
                        "request_failed",
                    )
                    self.assertEqual(
                        connection.execute("SELECT count(*) FROM fmp_stock_latest_outcomes").fetchone()[0],
                        1,
                    )
                with self.assertRaises(ConflictError):
                    importer.run_once(api_key=secret, transport=transport)
                self.assertEqual(transport.calls, 1)

    def test_attempt_capability_cannot_recapture_before_or_after_publish(self) -> None:
        transport = _Transport(self.body, self.store_map.news)
        importer = self._importer()
        attempt = importer.reserve_attempt()
        prepared = importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        with self.assertRaises(ConflictError):
            importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        self.assertEqual(importer.publish_prepared(prepared).outcome, "succeeded")
        with self.assertRaises(ConflictError):
            importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 1)

    def test_persisted_terminal_outcome_blocks_capture_before_transport(self) -> None:
        transport = _Transport(self.body, self.store_map.news)
        importer = self._importer()
        attempt = importer.reserve_attempt()
        importer.record_terminal_failure(attempt, outcome_kind="request_failed")
        with self.assertRaises(ConflictError):
            importer.capture_attempt(attempt, api_key="offline-key", transport=transport)
        self.assertEqual(transport.calls, 0)

    def test_empty_page_is_a_terminal_partial_success(self) -> None:
        transport = _Transport(b"[]", self.store_map.news)
        receipt = self._importer().run_once(api_key="offline-key", transport=transport)
        self.assertEqual(
            (receipt.outcome, receipt.article_count, receipt.written_count),
            ("succeeded", 0, 2),
        )
        self.assertEqual(transport.calls, 1)
        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            capture = connection.execute(
                "SELECT completeness, row_count FROM fmp_stock_latest_captures"
            ).fetchone()
            self.assertEqual(tuple(capture), ("partial", 0))
            self.assertEqual(
                connection.execute("SELECT count(*) FROM fmp_stock_latest_articles").fetchone()[0],
                0,
            )

    def test_invalid_credential_creates_no_intent_or_request(self) -> None:
        transport = _Transport(self.body, self.store_map.news)
        with self.assertRaises(ValidationError):
            self._importer().run_once(api_key="invalid key", transport=transport)
        self.assertEqual(transport.calls, 0)
        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM fmp_stock_latest_attempts").fetchone()[0],
                0,
            )

    def test_malformed_envelope_rejects_entire_page_before_capture_or_articles(self) -> None:
        transport = _Transport(b'{"Error Message":"not an array"}', self.store_map.news)
        with self.assertRaises(ValidationError):
            self._importer().run_once(api_key="offline-key", transport=transport)
        with read_connection(self.store_map, StoreRole.NEWS) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_attempts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT outcome_kind FROM fmp_stock_latest_outcomes").fetchone()[0], "response_rejected")
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_captures").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM fmp_stock_latest_articles").fetchone()[0], 0)

    def test_response_byte_bound_is_closed(self) -> None:
        with self.assertRaises(ResourceLimitError):
            CapturedFmpStockLatestResponse(200, "application/json", b"x" * (FMP_STOCK_LATEST_MAX_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
