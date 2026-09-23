"""Bounded query-count and exact legacy-result checks for news symbol batching."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch
import sqlite3
import unittest

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.news import current_multi_source_repository as repository_module
from quant_data.news.current_multi_source import (
    CurrentMultiSourceCredentials, CurrentMultiSourceImporter, CurrentMultiSourceRequest,
)
from quant_data.news.current_repository import CurrentNewsKeysetAnchor, CurrentNewsQuery
from quant_data.stores import StoreRole, writer_connection
from tests.news import test_current_multi_source_repository as fixtures


def legacy_symbols(connection, version_ids):
    # The previous per-article SELECT is an independent result oracle.
    return {version_id: tuple(row["provider_symbol"] for row in connection.execute(
        "SELECT provider_symbol FROM current_multi_source_article_symbols "
        "WHERE article_version_id=? ORDER BY provider_symbol COLLATE BINARY", (version_id,)))
        for version_id in version_ids}


class CurrentNewsSymbolBatchingTests(unittest.TestCase):
    def fixture(self):
        fixture = fixtures.CurrentMultiSourceNewsRepositoryTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def publish(self, fixture, rows, clock=fixtures.FIXED_TIME):
        CurrentMultiSourceImporter(fixture.stores, fixture.registry, clock=lambda: clock).run_once(
            request=CurrentMultiSourceRequest(feed_id="fmp_general", poll_slot=clock),
            credentials=CurrentMultiSourceCredentials(fmp_api_key="offline-key"),
            transport=fixtures._Transport(url=fixtures.FMP_GENERAL_URL, body=dumps_strict(rows).encode()))
        with writer_connection(fixture.stores, StoreRole.NEWS) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def rows(self, count):
        return [{"id": str(index), "title": f"Headline {index:04}", "text": "Summary",
                 "site": "Example", "url": f"https://example.test/{index}",
                 "publishedDate": ("2026-08-30T10:00:00Z", "2026-08-30", "2026-08-30 10:00:00", None)[index % 4],
                 "symbols": ([], ["AAPL"], ["MSFT", "AAPL"])[index % 3]}
                for index in range(count)]

    def test_requested_page_needs_one_symbol_query_and_zero_writes(self):
        fixture = self.fixture()
        self.publish(fixture, self.rows(603))
        repository = repository_module.CurrentMultiSourceNewsRepository(fixture.stores, fixture.registry)
        before = store_mutation_fingerprint(fixture.stores, StoreRole.NEWS)
        original_reader = repository_module.news_read_connection
        statements = []
        @contextmanager
        def traced(*args, **kwargs):
            with original_reader(*args, **kwargs) as connection:
                connection.set_trace_callback(statements.append)
                try:
                    yield connection
                finally:
                    connection.set_trace_callback(None)
        with patch.object(repository_module, "news_read_connection", traced):
            result = repository.search(CurrentNewsQuery(limit=25))
        symbol_queries = [sql for sql in statements if "FROM current_multi_source_article_symbols" in sql]
        self.assertEqual(len(symbol_queries), 1)
        self.assertEqual(result.total_selected_count, 603)
        self.assertEqual(len(result.records), 25)
        self.assertTrue(result.truncated)
        self.assertEqual(before, store_mutation_fingerprint(fixture.stores, StoreRole.NEWS))

        statements.clear()
        with patch.object(repository_module, "news_read_connection", traced):
            empty = repository.search(CurrentNewsQuery(mode="as_of", as_of="2026-08-29T00:00:00Z"))
        self.assertEqual(empty.records, ())
        self.assertFalse(any("FROM current_multi_source_article_symbols" in sql for sql in statements))

    def test_results_receipts_filters_versions_and_cursors_match_legacy_lookup(self):
        fixture = self.fixture()
        rows = self.rows(11)
        self.publish(fixture, rows)
        self.publish(fixture, [{**rows[1], "title": "Corrected headline", "symbols": ["MSFT"]}],
                     fixtures.FIXED_TIME + timedelta(hours=1))
        fixture._publish(feed_id="fmp_press_releases", url=fixtures.FMP_PRESS_RELEASES_URL,
            article_id="press-1", headline="Press headline", clock=fixtures.FIXED_TIME)
        repository = repository_module.CurrentMultiSourceNewsRepository(fixture.stores, fixture.registry)
        before = store_mutation_fingerprint(fixture.stores, StoreRole.NEWS)
        first = repository.search(CurrentNewsQuery(limit=2))
        anchor = CurrentNewsKeysetAnchor.from_record(first.records[-1])
        cases = [
            (CurrentNewsQuery(limit=25), {}),
            (CurrentNewsQuery(limit=2), {}),
            (CurrentNewsQuery(limit=2), {"after": anchor}),
            (CurrentNewsQuery(symbols=("MSFT",), limit=2), {}),
            (CurrentNewsQuery(query="MSFT", limit=2), {}),
            (CurrentNewsQuery(query="Corrected", limit=2), {}),
            (CurrentNewsQuery(start_date="2026-08-30", end_date="2026-08-30", limit=2), {}),
            (CurrentNewsQuery(mode="as_of", as_of="2026-08-30T16:00:00Z", limit=25), {}),
            (CurrentNewsQuery(mode="as_of", as_of="2026-08-30", limit=25), {}),
            (CurrentNewsQuery(limit=2), {"source_ids": ("fmp_press_releases",)}),
            (CurrentNewsQuery(query="no matches", limit=2), {}),
        ]
        for query, options in cases:
            with self.subTest(query=query, options=options):
                with patch.object(repository_module, "_symbols_by_version", legacy_symbols):
                    expected = repository.search(query, **options)
                actual = repository.search(query, **options)
                self.assertEqual(actual, expected)
        all_rows = repository.search(CurrentNewsQuery(limit=25)).records
        self.assertIn([], [row["symbols"] for row in all_rows])
        self.assertIn(["AAPL", "MSFT"], [row["symbols"] for row in all_rows])
        self.assertEqual(before, store_mutation_fingerprint(fixture.stores, StoreRole.NEWS))

    def test_invalid_duplicate_symbols_and_database_errors_remain_sanitized(self):
        for values in (["AAPL", "AAPL"], [""], [None]):
            connection = Mock()
            connection.execute.return_value = [
                {"article_version_id": "version", "provider_symbol": value} for value in values]
            with self.subTest(values=values), self.assertRaises(ValidationError):
                repository_module._symbols_by_version(connection, ("version",))
        connection = Mock()
        connection.execute.side_effect = sqlite3.OperationalError("private database detail")
        with self.assertRaises(StoreUnavailableError) as caught:
            repository_module._symbols_by_version(connection, ("version",))
        self.assertNotIn("private database detail", caught.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
