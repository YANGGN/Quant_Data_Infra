from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quant_data.inspector_retention import (
    _generic_capture, _shared_capture_anchors, lifecycle_annotations,
    read_inspector_retention,
)
from quant_data.registry import load_registry, CANONICAL_REGISTRY_PATH
from quant_data.stores import StoreMap

PROJECT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
ORIGINAL_GENERIC_CAPTURE_SQL = """
    SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
           snapshot.row_count, snapshot.completeness,
           snapshot.dataset_id AS snapshot_dataset_id,
           run.dataset_id AS anchor_dataset_id
    FROM ingestion_snapshots snapshot
    JOIN ingestion_runs run ON run.run_id=snapshot.run_id
    WHERE snapshot.dataset_id=? AND run.status='succeeded'
    UNION ALL
    SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
           snapshot.row_count, snapshot.completeness,
           snapshot.dataset_id AS snapshot_dataset_id,
           run.dataset_id AS anchor_dataset_id
    FROM ingestion_run_outputs output
    CROSS JOIN ingestion_runs run ON run.run_id=output.run_id
    CROSS JOIN ingestion_snapshots snapshot ON snapshot.snapshot_id=run.snapshot_id
                                           AND snapshot.run_id=run.run_id
    WHERE output.dataset_id=? AND run.status='succeeded'
      AND snapshot.dataset_id=run.dataset_id
      AND output.dataset_id<>run.dataset_id
    ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
    LIMIT 1
"""


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

    def add_status_lookup_indexes(self):
        self.connection.executescript("""
            CREATE INDEX ingestion_snapshots_dataset_capture
                ON ingestion_snapshots(dataset_id,captured_at DESC,snapshot_id DESC);
            CREATE INDEX ingestion_runs_status_capture
                ON ingestion_runs(run_id,status,snapshot_id,dataset_id);
            CREATE INDEX ingestion_run_outputs_dataset
                ON ingestion_run_outputs(dataset_id,run_id);
        """)

    def assert_original_capture(self, dataset):
        expected = self.connection.execute(
            ORIGINAL_GENERIC_CAPTURE_SQL, (dataset, dataset)
        ).fetchone()
        actual = _generic_capture(self.connection, dataset)
        self.assertEqual(
            None if actual is None else dict(actual),
            None if expected is None else dict(expected),
        )
        return actual

    def test_successful_raw_candidate_uses_primary_key_and_shared_can_win(self):
        at = "2026-09-05T12:00:00Z"
        self.capture("Z-direct", "target", at)
        self.capture("a-direct", "target", at)
        self.capture("older-direct", "target", "2026-09-04T12:00:00Z")
        traced = []
        self.connection.set_trace_callback(traced.append)
        try:
            row = self.assert_original_capture("target")
        finally:
            self.connection.set_trace_callback(None)
        self.assertEqual(row["snapshot_id"], "a-direct")
        sql = next(query for query in traced
                   if "FROM ingestion_snapshots snapshot" in query
                   and "AND snapshot.snapshot_id='a-direct'" in query)
        self.assertIn("AND snapshot.snapshot_id='a-direct'", sql)
        details = [plan[3] for plan in self.connection.execute("EXPLAIN QUERY PLAN " + sql)]
        self.assertTrue(
            any("SEARCH snapshot" in detail and "(snapshot_id=?)" in detail
                for detail in details), details,
        )

        # The direct branch's winner is fixed, but the final ordering must
        # still choose a newer binary ID from a successful shared run.
        self.capture("z-shared", "anchor", at, outputs=("target",))
        self.assertEqual(self.assert_original_capture("target")["snapshot_id"], "z-shared")

    def test_newer_non_success_and_orphan_raw_candidates_keep_full_direct_fallback(self):
        at = "2026-09-05T12:00:00Z"
        self.capture("A-success", "target", at)
        self.capture("z-running", "target", at, status="running")
        self.capture("failed", "target", "2026-09-06T12:00:00Z", status="failed")
        for expected_id in ("failed", "orphan"):
            if expected_id == "orphan":
                self.connection.execute(
                    "INSERT INTO ingestion_snapshots VALUES(?,?,?,?,?,?,?,?)",
                    ("orphan", "absent-run", "target", "2026-09-07T12:00:00Z",
                     "datetime", 1, "complete", "orphan"),
                )
            traced = []
            self.connection.set_trace_callback(traced.append)
            try:
                row = self.assert_original_capture("target")
            finally:
                self.connection.set_trace_callback(None)
            self.assertEqual(row["snapshot_id"], "A-success")
            sql = next(query for query in reversed(traced)
                       if "FROM ingestion_snapshots snapshot" in query
                       and "WHERE snapshot.dataset_id='target'" in query
                       and "UNION ALL" not in query)
            self.assertNotIn("AND snapshot.snapshot_id=", sql)

    def test_malformed_latest_success_stays_the_selected_raw_capture(self):
        self.capture("older", "target", "2026-09-04T12:00:00Z")
        self.capture("malformed", "target", "2026-09-05T12:00:00Z")
        self.connection.execute(
            "UPDATE ingestion_snapshots SET captured_precision='invalid', row_count=-1 "
            "WHERE snapshot_id='malformed'"
        )
        row = self.assert_original_capture("target")
        self.assertEqual(row["snapshot_id"], "malformed")
        self.assertEqual(row["captured_precision"], "invalid")
        self.assertEqual(row["row_count"], -1)

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
        self.add_status_lookup_indexes()
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

    def test_multiple_legacy_anchors_keep_older_eligible_and_malformed_winners(self):
        self.add_status_lookup_indexes()
        self.capture("old-a", "anchor-a", "2026-09-01T12:00:00Z",
                     outputs=("target",))
        self.capture("old-legacy", "legacy-anchor", "2026-09-02T12:00:00Z",
                     outputs=("target",))
        self.capture("failed-a", "anchor-a", "2026-09-06T12:00:00Z",
                     status="failed", outputs=("target",))
        self.capture("wrong-primary", "anchor-a", "2026-09-07T12:00:00Z",
                     primary="missing", outputs=("target",))
        self.assertEqual(self.assert_original_capture("target")["snapshot_id"],
                         "old-legacy")

        self.capture("malformed", "legacy-anchor", "2026-09-08T12:00:00Z",
                     outputs=("target",))
        self.connection.execute(
            "UPDATE ingestion_snapshots SET captured_precision='invalid', "
            "row_count=-1 WHERE snapshot_id='malformed'"
        )
        anchors = _shared_capture_anchors(self.connection, ("target",))
        self.assertEqual(set(anchors["target"]), {"anchor-a", "legacy-anchor"})
        expected = dict(self.assert_original_capture("target"))
        actual = _generic_capture(self.connection, "target",
                                  shared_anchors=anchors["target"])
        self.assertEqual(dict(actual), expected)
        self.assertEqual(actual["snapshot_id"], "malformed")
        self.assertEqual(actual["captured_precision"], "invalid")
        self.assertEqual(actual["row_count"], -1)

        self.capture("z-anchor", "anchor-a", "2026-09-08T12:00:00Z",
                     outputs=("target",))
        self.assertEqual(self.assert_original_capture("target")["snapshot_id"],
                         "z-anchor")

    def test_anchor_discovery_preserves_original_selection_and_shared_eligibility(self):
        at = "2026-09-05T12:00:00Z"
        self.capture("direct", "target", "2026-09-04T12:00:00Z")
        self.capture("valid-shared", "anchor", at, outputs=("target",))
        # This run points to a snapshot belonging to a different dataset.
        # Anchor discovery may include it, but shared eligibility rejects it.
        self.capture("orphan-shared", "anchor", "2026-09-06T12:00:00Z",
                     anchor="orphan", outputs=("target",))
        # This anchor does have another snapshot, so the original equality
        # condition must still reject its mismatched primary snapshot.
        self.capture("anchor-presence", "present", "2026-09-01T12:00:00Z")
        self.capture("mismatched-shared", "anchor", "2026-09-07T12:00:00Z",
                     anchor="present", outputs=("target",))
        self.connection.executemany(
            "INSERT INTO ingestion_runs VALUES(?,?,?,?)",
            ((f"orphan-run-{i}", "orphan", "succeeded", f"orphan-snap-{i}")
             for i in range(500)),
        )
        self.connection.executemany(
            "INSERT INTO ingestion_run_outputs VALUES(?,?)",
            ((f"orphan-run-{i}", "target") for i in range(500)),
        )
        for dataset in ("target", "missing"):
            self.assert_original_capture(dataset)
        self.assertEqual(_generic_capture(self.connection, "target")["snapshot_id"],
                         "valid-shared")

        traced = []
        self.connection.set_trace_callback(traced.append)
        anchors = _shared_capture_anchors(self.connection, ("target", "missing"))
        _generic_capture(self.connection, "target",
                         shared_anchors=anchors.get("target", ()))
        self.connection.set_trace_callback(None)
        self.assertIn("orphan", anchors["target"])
        self.assertNotIn("missing", anchors)
        discovery = next(query for query in traced
                         if "SELECT DISTINCT output.dataset_id" in query)
        self.assertIn("output.dataset_id IN ('target','missing')", discovery)
        shared = [query for query in traced
                  if "FROM ingestion_snapshots snapshot" in query
                  and "CROSS JOIN ingestion_run_outputs output" in query]
        self.assertEqual(len(shared), len(anchors["target"]))
        self.assertTrue(all("run.snapshot_id=snapshot.snapshot_id" in query
                            and "snapshot.dataset_id=run.dataset_id" in query
                            and "LIMIT 1" in query for query in shared))
        details = [row[3] for row in self.connection.execute(
            "EXPLAIN QUERY PLAN " + shared[0]
        )]
        self.assertTrue(
            any("SEARCH snapshot" in detail and "(dataset_id=?)" in detail
                for detail in details), details,
        )

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

    def test_sparse_old_shared_output_behind_newer_anchor_history_has_bounded_work(self):
        self.add_status_lookup_indexes()
        self.capture("old-linked", "anchor", "2025-01-01T00:00:00Z",
                     outputs=("target",))
        count = 20_000
        self.connection.executemany("INSERT INTO ingestion_runs VALUES(?,?,?,?)",
            ((f"run-newer-{i}", "anchor", "succeeded", f"newer-{i}")
             for i in range(count)))
        self.connection.executemany(
            "INSERT INTO ingestion_snapshots VALUES(?,?,?,?,?,?,?,?)",
            ((f"newer-{i}", f"run-newer-{i}", "anchor",
              "2026-01-01T00:00:00Z", "datetime", 1, "complete", str(i))
             for i in range(count)),
        )
        self.assertEqual(self.assert_original_capture("target")["snapshot_id"],
                         "old-linked")
        anchors = _shared_capture_anchors(self.connection, ("target",))
        self.assertEqual(anchors["target"], ("anchor",))
        calls = 0

        def budget():
            nonlocal calls
            calls += 1
            return int(calls > 1000)

        traced = []
        self.connection.set_trace_callback(traced.append)
        self.connection.set_progress_handler(budget, 100)
        try:
            row = _generic_capture(self.connection, "target",
                                   shared_anchors=anchors["target"])
        finally:
            self.connection.set_progress_handler(None, 0)
            self.connection.set_trace_callback(None)
        self.assertEqual(row["snapshot_id"], "old-linked")
        self.assertLessEqual(calls, 1000)
        self.assertTrue(any("FROM ingestion_run_outputs output" in query
                            and "CROSS JOIN ingestion_snapshots snapshot" in query
                            and "LIMIT 1" in query for query in traced))

    def test_dense_output_history_keeps_snapshot_first_shared_branch(self):
        self.add_status_lookup_indexes()
        for index in range(65):
            self.capture(f"linked-{index:03d}", "anchor",
                         f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z",
                         outputs=("target",))
        anchors = _shared_capture_anchors(self.connection, ("target",))
        self.assertEqual(anchors["target"], ("anchor",))
        traced = []
        self.connection.set_trace_callback(traced.append)
        try:
            row = _generic_capture(self.connection, "target",
                                   shared_anchors=anchors["target"])
        finally:
            self.connection.set_trace_callback(None)
        self.assertEqual(row["snapshot_id"], "linked-064")
        expected = self.connection.execute(
            ORIGINAL_GENERIC_CAPTURE_SQL, ("target", "target")
        ).fetchone()
        self.assertEqual(dict(row), dict(expected))
        self.assertTrue(any("FROM ingestion_run_outputs WHERE dataset_id=" in query
                            and "LIMIT 65" in query for query in traced))
        self.assertTrue(any("FROM ingestion_snapshots snapshot" in query
                            and "CROSS JOIN ingestion_run_outputs output" in query
                            for query in traced))


if __name__ == '__main__':
    unittest.main()
