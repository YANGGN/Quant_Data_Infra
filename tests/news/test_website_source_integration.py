from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ConflictError, MigrationError, ValidationError
from quant_data.migrations import migrate_and_register_store
from quant_data.news.current_multi_source import (
    CURRENT_MULTI_SOURCE_FEED_IDS, WEBSITE_SOURCE_MIGRATION_ID,
    CapturedCurrentMultiSourceResponse, CurrentMultiSourceCredentials,
    CurrentMultiSourceImporter, CurrentMultiSourceRequest,
)
from quant_data.news.current_multi_source_repository import CurrentMultiSourceNewsRepository
from quant_data.news.current_repository import CurrentNewsQuery
from quant_data.stores import StoreRole, StoreWriteLock
from tests.news.test_current_multi_source import _fixture_registry, _stores, _rss, _json
from tests.news.test_website_listings import CLOCK, finviz, rss


class _Response:
    def __init__(self, stores, body, media):
        self.stores, self.body, self.media = stores, body, media
        self.calls = 0

    def get(self, **kwargs):
        self.calls += 1
        with StoreWriteLock(self.stores.news, timeout_seconds=0):
            pass  # A network callback must be able to acquire the physical writer lock.
        return CapturedCurrentMultiSourceResponse(200, self.media, self.body)


class WebsiteSourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.stores = _stores(Path(self.temp.name))
        self.registry = _fixture_registry()
        self.predecessor = replace(self.registry,
            migrations=tuple(m for m in self.registry.migrations if m.id != WEBSITE_SOURCE_MIGRATION_ID),
            stores=tuple(replace(s, migration_order=tuple(m for m in s.migration_order
                if m != WEBSITE_SOURCE_MIGRATION_ID)) for s in self.registry.stores))
        migrate_and_register_store(self.stores, self.predecessor, StoreRole.NEWS,
                                   applied_at="2026-09-06T00:00:00Z")

    def tearDown(self):
        self.temp.cleanup()

    def migrate(self):
        return migrate_and_register_store(self.stores, self.registry, StoreRole.NEWS,
                                         applied_at="2026-09-06T04:00:00Z")

    def run_feed(self, source, body, media, when=CLOCK, registry=None):
        transport = _Response(self.stores, body, media)
        result = CurrentMultiSourceImporter(self.stores, registry or self.registry,
            clock=lambda: when).run_once(
                request=CurrentMultiSourceRequest(feed_id=source, poll_slot=when,
                    symbols=("AAPL",) if source == "alpaca_benzinga" else None),
                credentials=CurrentMultiSourceCredentials(fmp_api_key="offline",
                    alpaca_api_key="offline", alpaca_api_secret="offline"),
                transport=transport)
        self.assertEqual(transport.calls, 1)
        return result

    def snapshot(self):
        with closing(sqlite3.connect(self.stores.news)) as conn:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: tuple(sorted(conn.execute('SELECT * FROM "' + name + '"'), key=repr))
                    for name in names}

    def seed_old_sources(self):
        for source in CURRENT_MULTI_SOURCE_FEED_IDS:
            if source.startswith("fmp_"):
                body = _json([{"id": source, "title": source, "summary": "retained",
                    "site": "Example", "url": "https://example.test/" + source,
                    "publishedDate": "2026-09-05T12:00:00Z", "symbol": "AAPL"}])
                media = "application/json"
            elif source == "alpaca_benzinga":
                body = _json({"news": [{"id": 123, "headline": source, "summary": "retained",
                    "source": "Benzinga", "url": "https://example.test/" + source,
                    "updated_at": "2026-09-05T12:00:00Z", "symbols": ["AAPL"]}]})
                media = "application/json"
            else:
                body = _rss(guid=source, title=source, link="https://example.test/" + source)
                media = "application/rss+xml"
            self.run_feed(source, body, media, registry=self.predecessor)

    def test_populated_migration_preserves_all_sources_and_guards(self):
        self.seed_old_sources()
        before = self.snapshot()
        with closing(sqlite3.connect(self.stores.news)) as conn:
            guards = tuple(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))
        self.migrate()
        after = self.snapshot()
        self.assertEqual(set(before), set(after))
        for table, rows in before.items():
            if table != "schema_migrations":
                self.assertEqual(after[table], rows, table)
        self.assertTrue(set(before["schema_migrations"]) < set(after["schema_migrations"]))
        with closing(sqlite3.connect(self.stores.news)) as conn:
            self.assertEqual(tuple(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name")), guards)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(tuple(conn.execute("PRAGMA foreign_key_check")), ())
            for table in ("current_multi_source_attempts", "current_multi_source_captures",
                          "current_multi_source_articles", "current_multi_source_article_versions"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute('DELETE FROM "' + table + '"')
                conn.rollback()
        self.migrate()
        self.assertEqual(self.snapshot(), after)

    def test_failed_rebuild_rolls_back_every_table_and_ledger(self):
        self.seed_old_sources()
        before = self.snapshot()
        import quant_data.migrations as migrations
        original = migrations._sql_statements

        def injected(sql):
            yield from original(sql)
            if "current_multi_source_articles_website_extension" in sql:
                yield "INSERT INTO deliberately_absent_relation VALUES (1);"

        with patch.object(migrations, "_sql_statements", injected):
            with self.assertRaises(MigrationError):
                self.migrate()
        self.assertEqual(self.snapshot(), before)
        self.migrate()  # A failed forward migration does not poison a valid rerun.

    def test_website_requires_exact_applied_migration_before_network(self):
        transport = _Response(self.stores, finviz(), "text/html")
        importer = CurrentMultiSourceImporter(self.stores, self.registry, clock=lambda: CLOCK)
        with self.assertRaises(ValidationError):
            importer.run_once(request=CurrentMultiSourceRequest(feed_id="finviz", poll_slot=CLOCK),
                credentials=CurrentMultiSourceCredentials(), transport=transport)
        self.assertEqual(transport.calls, 0)

    def test_shared_storage_replay_corrections_and_capture_cutoff(self):
        self.migrate()
        self.run_feed("finviz", finviz(), "text/html")
        self.run_feed("financialjuice", rss(), "text/xml")
        before = self.snapshot()
        digest = hashlib.sha256(self.stores.news.read_bytes()).hexdigest()
        result = self.run_feed("finviz", b"<!-- advert -->" + finviz(),
                               "text/html", CLOCK + timedelta(hours=1))
        self.assertEqual((result.outcome, result.written_count), ("unchanged", 0))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(hashlib.sha256(self.stores.news.read_bytes()).hexdigest(), digest)
        self.run_feed("financialjuice", rss(title="Corrected"), "text/xml", CLOCK + timedelta(hours=2))
        repo = CurrentMultiSourceNewsRepository(self.stores, self.registry)
        latest = repo.search(CurrentNewsQuery(), source_ids=("financialjuice",))
        past = repo.search(CurrentNewsQuery(mode="as_of", as_of="2026-09-06T05:00:00Z"),
                           source_ids=("financialjuice",))
        self.assertEqual(latest.records[0]["headline"], "Corrected")
        self.assertEqual(past.records[0]["headline"], "Example headline")
        self.assertEqual(latest.records[0]["article_id"], past.records[0]["article_id"])
        with closing(sqlite3.connect(self.stores.news)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM current_multi_source_articles").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM current_multi_source_article_versions").fetchone()[0], 3)
        self.assertEqual(repo.search(CurrentNewsQuery(mode="as_of",
            as_of="2026-09-06T04:00:00Z")).records, ())

    def test_reverted_listing_appends_third_capture_and_version(self):
        self.migrate()
        for offset, title in enumerate(("A", "B", "A")):
            self.run_feed("financialjuice", rss(title=title), "text/xml",
                          CLOCK + timedelta(hours=offset))
        with closing(sqlite3.connect(self.stores.news)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM current_multi_source_captures").fetchone()[0], 3)
            self.assertEqual([tuple(r) for r in conn.execute(
                "SELECT title, version_sequence FROM current_multi_source_article_versions ORDER BY version_sequence")],
                [("A", 1), ("B", 2), ("A", 3)])
        before = self.snapshot()
        self.assertEqual(self.run_feed("financialjuice", rss(title="A"), "text/xml",
                         CLOCK + timedelta(hours=3)).written_count, 0)
        self.assertEqual(self.snapshot(), before)

    def test_live_factory_uses_post_response_website_availability(self):
        from quant_data.operations.current_news_refresh import _live_collectors
        self.migrate()
        clock = [CLOCK]
        class Delayed(_Response):
            def get(inner, **kwargs):
                clock[0] = CLOCK + timedelta(minutes=2)
                return super().get(**kwargs)
        transport = Delayed(self.stores, rss(), "text/xml")
        with patch("quant_data.news.current_multi_source.StdlibCurrentMultiSourceTransport",
                   return_value=transport):
            collectors = _live_collectors(stores=self.stores, registry=self.registry,
                environment={}, observed_at=CLOCK, website_clock=lambda: clock[0])
            collectors["financialjuice"]()
        repo = CurrentMultiSourceNewsRepository(self.stores, self.registry)
        self.assertEqual(repo.search(CurrentNewsQuery(mode="as_of",
            as_of=(CLOCK + timedelta(minutes=1)).isoformat())).records, ())
        latest = repo.search(CurrentNewsQuery()).records
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["available_at"], "2026-09-06T04:57:00.000000Z")
        with closing(sqlite3.connect(self.stores.news)) as conn:
            self.assertEqual(conn.execute("SELECT poll_slot FROM current_multi_source_attempts").fetchone()[0],
                             "2026-09-06T04:00:00Z")

    def test_rejected_page_retains_failure_and_blocks_same_slot(self):
        self.migrate()
        with self.assertRaises(ValidationError):
            self.run_feed("finviz", b"<html>Challenge</html>", "text/html")
        with closing(sqlite3.connect(self.stores.news)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM current_multi_source_outcomes").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM current_multi_source_captures").fetchone()[0], 0)
        transport = _Response(self.stores, finviz(), "text/html")
        with self.assertRaises(ConflictError):
            CurrentMultiSourceImporter(self.stores, self.registry, clock=lambda: CLOCK).run_once(
                request=CurrentMultiSourceRequest(feed_id="finviz", poll_slot=CLOCK),
                credentials=CurrentMultiSourceCredentials(), transport=transport)
        self.assertEqual(transport.calls, 0)


if __name__ == "__main__":
    unittest.main()
