"""Public retained-news regressions; only explicit temporary stores and fake transports."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.errors import StoreUnavailableError, DeadlineExceededError
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.migrations import migrate_and_register_store, migrate_store
from quant_data.news.current_multi_source import (
    CapturedCurrentMultiSourceResponse, CurrentMultiSourceCredentials,
    CurrentMultiSourceImporter, CurrentMultiSourceRequest)
from quant_data.news.current_repository import CurrentNewsQuery
from quant_data.news.lookup_registry import add_declarations, predecessor_profile
from quant_data.news.read_session import news_read_connection
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreWriteLock, quiet_immutable_read_connection, writer_connection
from quant_data.tool_platform.local_agent_cli import run

ROOT = Path(__file__).resolve().parents[2]
CLOCK = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


class RetainedNewsRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stores = StoreMap.four_explicit(**{r:self.root/(r+".sqlite") for r in ("market","macro","company","news")})
        raw = json.loads((ROOT/"config/system_registry.json").read_text())
        if raw["registry_version"] == "2.91.0":
            add_declarations(raw, ROOT)
        candidate = self.root/"registry.json"
        candidate.write_text(json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True)+chr(10))
        self.registry = load_registry(candidate, project_root=ROOT, environment={})
        migrate_and_register_store(self.stores, self.registry, "news", applied_at="2026-09-23T00:00:00Z")
        self.app = Stage1Application(self.stores, self.registry)

    @staticmethod
    def article(identity, *, symbol="AAPL", published="2026-09-23T10:00:00Z", title=None):
        return dict(id=identity, symbol=symbol, title=title or identity, text="Saved summary",
                    site="Test Wire", url="https://example.test/"+identity, publishedDate=published)

    def publish(self, articles, *, clock=CLOCK, feed="fmp_press_releases"):
        body = json.dumps(articles).encode()
        class Transport:
            def get(self, **kwargs):
                return CapturedCurrentMultiSourceResponse(200, "application/json", body)
        result = CurrentMultiSourceImporter(self.stores, self.registry, clock=lambda:clock).run_once(
            request=CurrentMultiSourceRequest(feed_id=feed, poll_slot=clock),
            credentials=CurrentMultiSourceCredentials(fmp_api_key="offline-fixture"),
            transport=Transport())
        self.assertEqual(result.outcome, "succeeded")

    def call(self, tool="news.search", **arguments):
        output = io.StringIO()
        request = dict(api_version="1.0", tool=tool,
                       tool_version="2.3.0" if tool=="news.search" else "1.0.0",
                       arguments=arguments)
        code = run(["call"], stdin=io.StringIO(json.dumps(request)), stdout=output, application=self.app)
        return code, json.loads(output.getvalue())

    def search(self, **arguments):
        code, payload = self.call(**arguments)
        self.assertEqual(code, 0, payload)
        return [{v["name"]:v["value"] for v in record["fields"]} for record in payload["result"]["records"]], payload

    def test_latest_version_wins_before_symbol_date_and_text_filters(self):
        self.publish([self.article("revised", title="Original search term")])
        self.publish([self.article("revised", symbol="MSFT", published="2026-09-01T10:00:00Z",
                                   title="Corrected")], clock=CLOCK+timedelta(hours=1))
        for filters in (dict(symbols=["AAPL"]), dict(start_date="2026-09-20"),
                        dict(query="Original search term")):
            rows, _ = self.search(**filters)
            self.assertEqual(rows, [])
            rows, _ = self.search(**filters, mode="as_of", as_of="2026-09-23T12:30:00Z")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["version_sequence"], 1)

    def test_keyset_pages_match_complete_as_of_selection_despite_later_correction(self):
        items = [self.article("item-"+str(i), published=f"2026-09-23T10:00:{i:02d}Z") for i in range(13)]
        self.publish(items)
        query = dict(symbols=["aapl"], start_date="2026-09-20", mode="as_of", as_of="2026-09-23T12:30:00Z")
        expected, _ = self.search(**query, limit=100)
        self.publish([self.article("item-12", symbol="MSFT", published="2026-09-01T00:00:00Z")],
                     clock=CLOCK+timedelta(hours=1))
        ids=[]; cursor=None; remaining=len(expected)
        while True:
            rows, payload = self.search(**query, limit=3, cursor=cursor)
            trunc=payload["result"]["truncation"]
            self.assertEqual(trunc["total_known_count"], remaining)
            ids.extend(r["article_version_id"] for r in rows)
            remaining -= len(rows)
            cursor=trunc["next_cursor"]
            if cursor is None:break
        self.assertEqual(ids, [r["article_version_id"] for r in expected])
        self.assertEqual(len(ids), len(set(ids)))

    def test_old_newly_captured_article_preserves_publication_chronology_and_availability(self):
        self.publish([self.article("recent", published="2026-09-23T10:00:00Z")])
        self.publish([self.article("late-old", published="2026-09-20T10:00:00-04:00")],
                     clock=CLOCK+timedelta(hours=1))
        rows,_=self.search(symbols=["AAPL"],start_date="2026-09-20",limit=10)
        self.assertEqual([r["headline"] for r in rows], ["recent","late-old"])
        self.assertEqual(rows[1]["published_normalized_at"], "2026-09-20T14:00:00.000000Z")
        self.assertEqual(rows[1]["available_at"], "2026-09-23T13:00:00.000000Z")
        self.assertTrue(all(r["source_url"].startswith("https://") for r in rows))
        early,_=self.search(symbols=["AAPL"],mode="as_of",as_of="2026-09-23T12:30:00Z")
        self.assertEqual([r["headline"] for r in early],["recent"])

    def test_only_requested_rows_are_materialized_and_symbol_lookup_is_indexed(self):
        from quant_data.news import current_multi_source_repository as repository
        self.publish([self.article("noise-"+str(i),symbol="MSFT") for i in range(200)] +
                     [self.article("hit-"+str(i)) for i in range(12)])
        with patch.object(repository,"_record",wraps=repository._record) as render:
            rows,payload=self.search(symbols=["AAPL"],limit=3)
        self.assertEqual(len(rows),3)
        self.assertEqual(render.call_count,3)
        self.assertEqual(payload["result"]["truncation"]["total_known_count"],12)
        with news_read_connection(self.stores) as c:
            plan=" ".join(r[3] for r in c.execute(
                "EXPLAIN QUERY PLAN SELECT article_version_id FROM current_multi_source_article_symbols WHERE provider_symbol=?",
                ("AAPL",)))
        self.assertIn("current_multi_source_article_symbols_lookup",plan)

    def test_status_uses_index_and_empty_unsupported_failed_are_distinct(self):
        self.publish([self.article("saved")])
        code,payload=self.call("news.get_source_status",source_ids=[])
        self.assertEqual(code,0,payload)
        statuses={next(f["value"] for f in r["fields"] if f["name"]=="source_id"):
                  {f["name"]:f["value"] for f in r["fields"]} for r in payload["result"]["records"]}
        self.assertEqual(len(statuses),8)
        self.assertEqual(statuses["fmp_press_releases"]["status"],"succeeded")
        self.assertEqual(statuses["alpaca_benzinga"]["status"],"no_retained_outcome")
        rows,_=self.search(symbols=["ZZZNOMATCH"])
        self.assertEqual(rows,[])
        code,error=self.call("news.get_source_status",source_ids=["finviz"])
        self.assertNotEqual(code,0)
        self.assertIn("error",error)
        from quant_data.news.tool_repository import _MULTI_LATEST_CAPTURE_SQL
        with news_read_connection(self.stores) as c:
            plan=" ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN "+_MULTI_LATEST_CAPTURE_SQL,("fmp_press_releases",)))
        self.assertIn("current_multi_source_captures_feed_time",plan)
        self.assertNotIn("SCAN capture",plan)

        class FailedTransport:
            def get(self, **kwargs):
                raise OSError("offline simulated provider failure")
        with self.assertRaises(OSError):
            CurrentMultiSourceImporter(self.stores,self.registry,clock=lambda:CLOCK+timedelta(hours=1)).run_once(
                request=CurrentMultiSourceRequest(feed_id="fmp_press_releases",poll_slot=CLOCK+timedelta(hours=1)),
                credentials=CurrentMultiSourceCredentials(fmp_api_key="offline-fixture"),
                transport=FailedTransport())
        code,payload=self.call("news.get_source_status",source_ids=["fmp_press_releases"])
        self.assertEqual(code,0,payload)
        status={f["name"]:f["value"] for f in payload["result"]["records"][0]["fields"]}
        self.assertEqual(status["status"],"request_failed")
        capture=json.loads(status["latest_successful_capture"])
        self.assertEqual(capture["captured_at"],"2026-09-23T12:00:00.000000Z")
        self.assertEqual(status["capture_count"],1)

    def test_legacy_unlocked_public_read_reproduces_changed_store_error(self):
        self.publish([self.article("before")])
        @contextmanager
        def legacy(store_map, **kwargs):
            with quiet_immutable_read_connection(store_map,"news") as c:
                yield c
                self.publish([self.article("during")],clock=CLOCK+timedelta(hours=1))
        with patch("quant_data.tool_platform.news_access.news_read_connection",legacy):
            code,payload=self.call(symbols=["AAPL"],limit=10)
        self.assertNotEqual(code,0)
        self.assertEqual(payload["error"]["code"],"store_unavailable")
        self.assertIn("changed",payload["error"]["message"])

    def test_public_search_and_status_wait_for_short_publication_lock(self):
        self.publish([self.article("saved")])
        for tool,args in (("news.search",dict(symbols=["AAPL"],limit=10)),
                          ("news.get_source_status",dict(source_ids=[]))):
            held=threading.Event(); failures=[]
            def writer():
                try:
                    with StoreWriteLock(self.stores.path("news")):
                        held.set()
                        time.sleep(0.15)
                except Exception as exc:failures.append(exc)
            worker=threading.Thread(target=writer)
            worker.start()
            self.assertTrue(held.wait(2))
            code,payload=self.call(tool,**args)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures,[])
            self.assertEqual(code,0,payload)

    def test_rogue_writer_still_fails_integrity_and_sql_deadline_is_not_masked(self):
        with self.assertRaises(StoreUnavailableError):
            with news_read_connection(self.stores):
                with writer_connection(self.stores,"news") as c:
                    c.execute("PRAGMA user_version=7")
        calls=[]
        def checkpoint():
            calls.append(1)
            if len(calls)>1:raise DeadlineExceededError("fixture deadline")
        with self.assertRaises(DeadlineExceededError):
            with news_read_connection(self.stores,checkpoint=checkpoint) as c:
                c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n").fetchone()

    def test_migration_replay_and_exact_predecessor(self):
        before=store_mutation_fingerprint(self.stores, "news")
        migrate_store(self.stores,self.registry,"news",applied_at="2026-09-23T01:00:00Z")
        self.assertEqual(before,store_mutation_fingerprint(self.stores, "news"))
        predecessor=predecessor_profile(self.registry)
        self.assertEqual(predecessor.registry_version,"2.91.0")
        self.assertEqual(predecessor.source_sha256,"893c9bf9e93a4062b2a20cebf5928d29488600fd769fc88d107b6849c126af19")

    def test_combined_search_holds_one_snapshot_during_actual_publication(self):
        from quant_data.news.current_repository import CurrentNewsRepository
        from quant_data.news.current_multi_source_repository import CurrentMultiSourceNewsRepository
        self.publish([self.article("before")])
        started = threading.Event()
        finished = threading.Event()
        failures = []
        def writer():
            started.set()
            try:
                self.publish([self.article("during")], clock=CLOCK+timedelta(hours=1))
            except Exception as exc:
                failures.append(exc)
            finally:
                finished.set()
        worker = threading.Thread(target=writer)
        original_fmp = CurrentNewsRepository.search
        original_multi = CurrentMultiSourceNewsRepository.search
        def fmp(repository, *args, **kwargs):
            result = original_fmp(repository, *args, **kwargs)
            worker.start()
            self.assertTrue(started.wait(2))
            return result
        def multi(repository, *args, **kwargs):
            self.assertFalse(finished.wait(0.05))
            return original_multi(repository, *args, **kwargs)
        try:
            with patch.object(CurrentNewsRepository, "search", fmp), patch.object(
                CurrentMultiSourceNewsRepository, "search", multi
            ):
                rows, _ = self.search(symbols=["AAPL"], limit=10)
        finally:
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual([r["headline"] for r in rows], ["before"])
        rows, _ = self.search(symbols=["AAPL"], limit=10)
        self.assertEqual({r["headline"] for r in rows}, {"before", "during"})

    def test_populated_upgrade_preserves_facts_and_rolls_back_failed_index(self):
        import sqlite3
        from dataclasses import replace
        import quant_data.migrations as migrations
        from quant_data.errors import MigrationError
        from quant_data.news.lookup_registry import MIGRATION_ID

        self.stores = StoreMap.four_explicit(**{
            r:self.root/("upgrade-"+r+".sqlite") for r in ("market","macro","company","news")})
        candidate = self.registry
        self.registry = predecessor_profile(candidate)
        migrate_and_register_store(self.stores,self.registry,"news",applied_at="2026-09-23T00:00:00Z")
        self.publish([self.article("preserved")])
        before = store_mutation_fingerprint(self.stores, "news")
        bad = replace(candidate, migrations=tuple(
            replace(m, sha256="0"*64) if m.id==MIGRATION_ID else m for m in candidate.migrations))
        with self.assertRaises(MigrationError):
            migrate_store(self.stores,bad,"news",applied_at="2026-09-23T01:00:00Z")
        self.assertEqual(before,store_mutation_fingerprint(self.stores,"news"))

        statements = migrations._sql_statements
        def fail_after_index(sql):
            yield from statements(sql)
            raise sqlite3.IntegrityError("injected failure after index creation")
        with patch.object(migrations,"_sql_statements",fail_after_index):
            with self.assertRaises(MigrationError):
                migrate_store(self.stores,candidate,"news",applied_at="2026-09-23T01:00:00Z")
        self.assertEqual(before,store_mutation_fingerprint(self.stores,"news"))

        migrate_store(self.stores,candidate,"news",applied_at="2026-09-23T01:00:00Z")
        after = store_mutation_fingerprint(self.stores,"news")
        self.assertEqual(
            {k:v for k,v in before["relations"].items() if k!="schema_migrations"},
            {k:v for k,v in after["relations"].items() if k!="schema_migrations"})
        with news_read_connection(self.stores) as c:
            ledger = c.execute("SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal").fetchall()
            self.assertEqual(len(ledger),10)
            self.assertEqual(ledger[-1]["migration_id"],MIGRATION_ID)
            self.assertEqual(ledger[-1]["sha256"],next(m.sha256 for m in candidate.migrations if m.id==MIGRATION_ID))
        migrate_store(self.stores,candidate,"news",applied_at="2026-09-23T02:00:00Z")
        self.assertEqual(after,store_mutation_fingerprint(self.stores,"news"))

    def test_registry_generation_preserves_every_public_contract(self):
        from quant_data.tool_platform.generate import generated_bytes
        target = self.root/"generation"
        (target/"config").mkdir(parents=True)
        candidate_bytes = self.registry.source_path.read_bytes()
        (target/"config/system_registry.json").write_bytes(candidate_bytes)
        registry_bytes, _, _ = generated_bytes(target)
        self.assertEqual(registry_bytes,candidate_bytes)
        predecessor = predecessor_profile(self.registry)
        for key in ("tools","datasets","collectors","jobs","exports","tool_versions"):
            self.assertEqual(self.registry.raw[key],predecessor.raw[key])
