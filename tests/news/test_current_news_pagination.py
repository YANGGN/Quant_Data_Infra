"""Offline keyset-pagination coverage for current-news repositories."""

from __future__ import annotations

import unittest
from datetime import timedelta

from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.news.current_multi_source_repository import (
    CurrentMultiSourceNewsRepository,
)
from quant_data.news.current_repository import (
    CurrentNewsKeysetAnchor,
    CurrentNewsQuery,
)
from quant_data.stores import StoreRole
from tests.news import test_current_multi_source_repository as multi_source_tests
from tests.news import test_current_repository as fmp_tests


class CurrentNewsPaginationTests(unittest.TestCase):
    def _fmp_fixture(self) -> fmp_tests.CurrentNewsRepositoryTests:
        fixture = fmp_tests.CurrentNewsRepositoryTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def _multi_source_fixture(
        self,
    ) -> multi_source_tests.CurrentMultiSourceNewsRepositoryTests:
        fixture = multi_source_tests.CurrentMultiSourceNewsRepositoryTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_fmp_pages_are_stable_disjoint_and_read_only(self) -> None:
        fixture = self._fmp_fixture()
        for article_id, source_row in (("article-alpha", 3), ("article-zulu", 4)):
            fmp_tests._insert_article(
                fixture.store_map,
                article_id=article_id,
                version_id=f"version-{article_id}",
                capture_id="capture-2",
                source_row=source_row,
                site="Tie News",
                source_url=f"https://example.test/{article_id}",
                symbol="TIE",
                headline=f"{article_id} headline",
                body_text="private body text",
                raw="2026-08-20T09:00:00+02:00",
                normalized="2026-08-20T09:00:00.000000+02:00",
                precision="datetime_offset",
                offset_status="known",
            )

        query = CurrentNewsQuery(limit=2)
        before = store_mutation_fingerprint(fixture.store_map, StoreRole.NEWS)
        default_selection = fixture.repository.search(query)
        explicit_default = fixture.repository.search(query, after=None)
        first = fixture.repository.search(query)
        second = fixture.repository.search(
            query,
            after=CurrentNewsKeysetAnchor.from_record(first.records[-1]),
        )
        third = fixture.repository.search(
            query,
            after=CurrentNewsKeysetAnchor.from_record(second.records[-1]),
        )
        after = store_mutation_fingerprint(fixture.store_map, StoreRole.NEWS)

        self.assertEqual(before, after)
        self.assertEqual(default_selection, explicit_default)
        self.assertEqual(
            tuple(record["article_id"] for record in first.records),
            ("article-alpha", "article-msft"),
        )
        self.assertEqual(
            tuple(record["article_id"] for record in second.records),
            ("article-zulu", "article-spy"),
        )
        self.assertEqual(
            tuple(record["article_id"] for record in third.records),
            ("article-aapl", "article-qqq"),
        )
        self.assertEqual(
            (first.total_selected_count, second.total_selected_count, third.total_selected_count),
            (6, 4, 2),
        )
        self.assertEqual(
            (first.truncated, second.truncated, third.truncated),
            (True, True, False),
        )
        article_ids = tuple(
            record["article_id"]
            for selection in (first, second, third)
            for record in selection.records
        )
        self.assertEqual(len(article_ids), len(set(article_ids)))
        self.assertTrue(
            all(
                "body_text" not in record and "response_bytes" not in record
                for record in first.records
            )
        )

    def test_multi_source_pages_use_capture_then_article_id_ties(self) -> None:
        fixture = self._multi_source_fixture()
        fixture._publish(
            feed_id="fmp_press_releases",
            url=multi_source_tests.FMP_PRESS_RELEASES_URL,
            article_id="article-zulu",
            headline="Zulu",
            clock=multi_source_tests.FIXED_TIME,
        )
        fixture._publish(
            feed_id="fmp_general",
            url=multi_source_tests.FMP_GENERAL_URL,
            article_id="article-alpha",
            headline="Alpha",
            clock=multi_source_tests.FIXED_TIME,
        )
        fixture._publish(
            feed_id="fmp_press_releases",
            url=multi_source_tests.FMP_PRESS_RELEASES_URL,
            article_id="article-later",
            headline="Later capture",
            clock=multi_source_tests.FIXED_TIME + timedelta(hours=1),
        )
        repository = CurrentMultiSourceNewsRepository(fixture.stores, fixture.registry)
        query = CurrentNewsQuery(limit=1)

        default_selection = repository.search(query)
        explicit_default = repository.search(query, after=None)
        first = repository.search(query)
        second = repository.search(
            query,
            after=CurrentNewsKeysetAnchor.from_record(first.records[-1]),
        )
        third = repository.search(
            query,
            after=CurrentNewsKeysetAnchor.from_record(second.records[-1]),
        )

        self.assertEqual(default_selection, explicit_default)
        self.assertEqual(tuple(record["headline"] for record in first.records), ("Later capture",))
        self.assertEqual(
            {record["headline"] for record in (*second.records, *third.records)},
            {"Alpha", "Zulu"},
        )
        self.assertEqual(second.records[0]["available_at"], third.records[0]["available_at"])
        self.assertEqual(
            second.records[0]["published_normalized_at"],
            third.records[0]["published_normalized_at"],
        )
        self.assertLess(second.records[0]["article_id"], third.records[0]["article_id"])
        self.assertEqual(
            (first.total_selected_count, second.total_selected_count, third.total_selected_count),
            (3, 2, 1),
        )
        self.assertEqual(
            (first.truncated, second.truncated, third.truncated),
            (True, True, False),
        )
        self.assertTrue(
            all(
                "body_text" not in record and "response_bytes" not in record
                for record in first.records
            )
        )


if __name__ == "__main__":
    unittest.main()
