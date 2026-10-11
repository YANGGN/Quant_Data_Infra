from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quant_data.inspector_retention import read_inspector_retention, lifecycle_annotations, _generic_capture
from quant_data.registry import load_registry, CANONICAL_REGISTRY_PATH
from quant_data.stores import StoreMap

PROJECT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class InspectorRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(CANONICAL_REGISTRY_PATH, project_root=PROJECT, environment={})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temp.name)
        self.stores = StoreMap.four_explicit(**{role: self.root / (role + ".sqlite") for role in ("market", "macro", "company", "news")})
        for role in ("market", "macro", "company", "news"):
            with sqlite3.connect(self.root / (role + ".sqlite")) as c:
                c.executescript("""
                    CREATE TABLE store_metadata(singleton,store_role);
                    CREATE TABLE ingestion_runs(run_id,dataset_id,status,snapshot_id);
                    CREATE TABLE ingestion_snapshots(snapshot_id,run_id,dataset_id,captured_at,captured_precision,row_count,completeness);
                    CREATE TABLE ingestion_run_outputs(run_id,dataset_id);
                """)
                c.execute("INSERT INTO store_metadata VALUES(1,?)", (role,))
        with sqlite3.connect(self.stores.company) as c:
            c.executescript("""
                INSERT INTO ingestion_runs VALUES('r','fixture.company.fundamentals','succeeded','s'),
                    ('bad','fixture.company.fundamentals','failed','bad-s');
                INSERT INTO ingestion_snapshots VALUES('s','r','fixture.company.fundamentals','2026-09-01T12:00:00Z','datetime',2,'complete'),
                    ('bad-s','bad','fixture.company.fundamentals','2026-09-05T12:00:00Z','datetime',2,'complete'),
                    ('unrelated','r','fixture.company.issuers','2026-09-05T13:00:00Z','datetime',1,'complete');
                INSERT INTO ingestion_run_outputs VALUES('r','fixture.company.filings'),('bad','fixture.company.filings');
            """)
        with sqlite3.connect(self.stores.macro) as c:
            c.executescript("""
                CREATE TABLE macro_live_vintage_captures(capture_id,captured_at,captured_precision,observation_count);
                CREATE TABLE macro_live_vintage_capture_membership(capture_id,version_id,series_id);
                CREATE TABLE macro_live_vintage_observation_versions(version_id,series_id);
                INSERT INTO macro_live_vintage_captures VALUES('native','2026-09-04T14:05:00Z','datetime',1);
                INSERT INTO macro_live_vintage_capture_membership VALUES('native','v','gdp');
                INSERT INTO macro_live_vintage_observation_versions VALUES('v','gdp');
            """)
        with sqlite3.connect(self.stores.news) as c:
            c.executescript("""
                CREATE TABLE fmp_stock_latest_outcomes(outcome_id,outcome_kind,recorded_at,http_status);
                INSERT INTO fmp_stock_latest_outcomes VALUES('old','response_rejected','2026-08-15T16:26:04Z',200);
            """)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, now=NOW):
        return read_inspector_retention(self.stores, self.registry, observed_at=now)

    def fingerprints(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}

    def test_shared_capture_requires_output_link_and_uses_primary_snapshot(self):
        before = self.fingerprints()
        m = self.read()
        linked = m['fixture.company.filings']
        self.assertEqual(linked['retention_state'], 'retained')
        self.assertEqual(linked['retention_basis'], 'shared')
        self.assertEqual(linked['capture_anchor_id'], 'fixture.company.fundamentals')
        self.assertEqual(linked['retained_capture_at'], '2026-09-01T12:00:00Z')
        self.assertEqual(m['fixture.company.fundamentals']['retention_basis'], 'direct')
        self.assertEqual(m['fixture.company.corporate_actions']['retention_state'], 'missing')
        self.assertNotIn('latest_successful_fetch_at', linked)
        self.assertNotIn('retained_outcome', linked)
        self.assertNotIn('retained_outcome', m['fixture.company.fundamentals'])
        self.assertEqual(before, self.fingerprints())

    def test_native_capture_uses_membership_without_synthesizing_generic_runs(self):
        before = self.fingerprints()
        m = self.read()
        for id in ('macro.official_vintages', 'macro.official_vintages_evidence'):
            self.assertEqual(m[id]['retention_basis'], 'native')
            self.assertEqual(m[id]['retained_capture_at'], '2026-09-04T14:05:00Z')
            self.assertIsNone(m[id]['capture_anchor_id'])
        self.assertEqual(before, self.fingerprints())
        with sqlite3.connect(self.stores.macro) as c:
            c.execute("DELETE FROM macro_live_vintage_capture_membership")
        self.assertEqual(self.read()['macro.official_vintages']['retention_state'], 'unknown')

    def test_busy_store_is_unknown_without_partial_capture_or_file_changes(self):
        Path(str(self.stores.company) + '-wal').write_bytes(b'busy')
        before = self.fingerprints()
        self.assertEqual(self.read()['fixture.company.filings']['retention_state'], 'unknown')
        self.assertEqual(before, self.fingerprints())

    def test_lifecycle_is_explicit_and_old_rejection_is_not_a_live_failure(self):
        m = self.read()
        self.assertEqual(m['fixture.market.instrument_classifications']['lifecycle'], 'planned')
        self.assertEqual(m['fixture.macro.eia_weekly']['lifecycle'], 'fixture')
        self.assertEqual(m['market.fmp.daily_prices']['lifecycle'], 'historical')
        self.assertEqual(m['fixture.company.filings']['lifecycle'], 'active')
        old = m['news.fmp.stock_latest_evidence']
        self.assertEqual(old['lifecycle'], 'legacy')
        self.assertEqual(old['retained_outcome'], 'response_rejected')
        self.assertNotIn('refresh_state', old)
        self.assertNotIn('fixture.macro.soma_evidence', lifecycle_annotations())

    def test_retention_freshness_and_invalid_capture_do_not_claim_health(self):
        late = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.assertEqual(self.read(late)['fixture.company.filings']['retention_freshness'], 'stale')
        with sqlite3.connect(self.stores.company) as c:
            c.execute("UPDATE ingestion_snapshots SET captured_at='2026-09-01',captured_precision='date' WHERE snapshot_id='s'")
        self.assertEqual(self.read()['fixture.company.filings']['retention_state'], 'unknown')
        self.assertEqual(self.read(datetime(2026, 1, 1, tzinfo=timezone.utc))['macro.official_vintages']['retention_state'], 'unknown')


class InspectorCaptureQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.connection = sqlite3.connect(Path(self.temp.name) / "captures.sqlite")
        self.connection.row_factory = sqlite3.Row
        # Match the existing ledger keys, including the dataset-first snapshot
        # index and the run-first output key. No new production index is needed.
        self.connection.executescript("""
            CREATE TABLE ingestion_runs(
                run_id TEXT PRIMARY KEY, dataset_id TEXT, status TEXT, snapshot_id TEXT);
            CREATE TABLE ingestion_snapshots(
                snapshot_id TEXT PRIMARY KEY, run_id TEXT UNIQUE, dataset_id TEXT,
                captured_at TEXT, captured_precision TEXT, row_count INTEGER,
                completeness TEXT, semantic_identity TEXT,
                UNIQUE(dataset_id, semantic_identity));
            CREATE TABLE ingestion_run_outputs(
                run_id TEXT, dataset_id TEXT, PRIMARY KEY(run_id, dataset_id));
        """)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def capture(self, identity, dataset, at, *, status="succeeded", anchor=None,
                primary=None, outputs=()):
        run = "run-" + identity
        self.connection.execute("INSERT INTO ingestion_runs VALUES(?,?,?,?)",
            (run, dataset if anchor is None else anchor, status,
             identity if primary is None else primary))
        self.connection.execute("INSERT INTO ingestion_snapshots VALUES(?,?,?,?,?,?,?,?)",
            (identity, run, dataset, at, "datetime", 1, "complete", identity))
        self.connection.executemany("INSERT INTO ingestion_run_outputs VALUES(?,?)",
            ((run, output) for output in outputs))

    def test_direct_and_shared_candidates_keep_binary_ties_and_full_metadata(self):
        at = "2026-09-04T12:00:00Z"
        self.capture("Z-direct", "target", at, outputs=("target",))
        self.capture("a-shared", "anchor", at, outputs=("target",))
        self.capture("older", "target", "2026-09-03T12:00:00Z")
        row = _generic_capture(self.connection, "target")
        self.assertEqual(dict(row), {
            "snapshot_id": "a-shared", "captured_at": at,
            "captured_precision": "datetime", "row_count": 1,
            "completeness": "complete", "snapshot_dataset_id": "anchor",
            "anchor_dataset_id": "anchor",
        })
        self.capture("newest-direct", "target", "2026-09-05T12:00:00Z", primary="other")
        # Direct attribution never required the run primary-snapshot pointer.
        self.assertEqual(_generic_capture(self.connection, "target")["snapshot_id"], "newest-direct")

    def test_shared_attribution_requires_success_link_and_matching_primary(self):
        self.capture("valid", "target", "2026-09-01T12:00:00Z")
        at = "2026-09-05T12:00:00Z"
        self.capture("failed-direct", "target", at, status="failed")
        self.capture("failed-shared", "anchor", at, status="failed", outputs=("target",))
        self.capture("unlinked", "anchor", at)
        self.capture("not-primary", "anchor", at, primary="missing", outputs=("target",))
        self.capture("wrong-anchor", "anchor", at, anchor="different", outputs=("target",))
        self.capture("foreign-run", "anchor", at, primary="unlinked", outputs=("target",))
        self.assertEqual(_generic_capture(self.connection, "target")["snapshot_id"], "valid")
        self.assertIsNone(_generic_capture(self.connection, "missing"))
        self.assertIsNone(_generic_capture(self.connection, "target OR 1=1"))

    def test_unrelated_history_has_a_bounded_query_work_budget(self):
        count = 20_000
        self.connection.executemany("INSERT INTO ingestion_runs VALUES(?,?,?,?)",
            ((f"r{i}", "unrelated", "succeeded", f"s{i}") for i in range(count)))
        self.connection.executemany("INSERT INTO ingestion_snapshots VALUES(?,?,?,?,?,?,?,?)",
            ((f"s{i}", f"r{i}", "unrelated", "2026-09-01T00:00:00Z", "datetime",
              1, "complete", str(i)) for i in range(count)))
        self.connection.executemany("INSERT INTO ingestion_run_outputs VALUES(?,?)",
            ((f"r{i}", "unrelated") for i in range(count)))
        self.capture("direct", "target", "2026-09-02T00:00:00Z")
        self.capture("shared", "anchor", "2026-09-03T00:00:00Z", outputs=("target",))
        calls = 0

        def budget():
            nonlocal calls
            calls += 1
            return int(calls > 1500)

        # A VM-work bound is stable across host load. The former OR query
        # exceeds this budget by joining every unrelated snapshot to its run.
        self.connection.set_progress_handler(budget, 100)
        try:
            self.assertEqual(_generic_capture(self.connection, "target")["snapshot_id"], "shared")
        finally:
            self.connection.set_progress_handler(None, 0)
        self.assertLessEqual(calls, 1500)


if __name__ == '__main__':
    unittest.main()
