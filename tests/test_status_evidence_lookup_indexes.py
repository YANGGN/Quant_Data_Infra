"""Isolated migration and reader regressions for retained status evidence lookups."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quant_data.data_status import _capture_from_row, _control_plane_state
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.inspector_retention import _generic_capture
from quant_data.migrations import migrate_and_register_store, migrate_store
from quant_data.news.tool_repository import (
    _MULTI_LATEST_OUTCOME_INDEXED_SQL, _MULTI_LATEST_OUTCOME_SQL,
    _SOURCE_BY_ID, _source_latest_outcome,
)
from quant_data.registry import load_registry
from quant_data.status_evidence_lookup_registry import (
    MIGRATION_IDS, PREDECESSOR_VERSION, VERSION, add_declarations,
    predecessor_profile,
)
from quant_data.status_market_lookup_registry import (
    VERSION as MARKET_LOOKUP_VERSION,
    predecessor_profile as market_lookup_predecessor_profile,
)
from quant_data.stores import quiet_immutable_read_connection, writer_connection
from tests.news.test_tool_repository import _insert_multi_source_version
from tests.test_status_lookup_indexes import _contents, _stores
from tests.test_status_run_lookup_index import ANCHOR, TARGET, _success


ROOT = Path(__file__).resolve().parents[1]
INDEXES = {
    "company": ("ingestion_run_outputs_dataset",
                "ingestion_run_outputs", ("dataset_id", "run_id")),
    "news": ("current_multi_source_outcomes_status",
             "current_multi_source_outcomes",
             ("attempt_id", "recorded_at", "outcome_id")),
}
_OLD_CAPTURE_SQL = """
SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
       snapshot.row_count, snapshot.completeness
FROM ingestion_snapshots AS snapshot
JOIN ingestion_runs AS run ON run.run_id = snapshot.run_id
WHERE snapshot.dataset_id = ? AND run.status = 'succeeded'
ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
LIMIT 1
"""


class StatusEvidenceLookupIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stores = _stores(self.root)
        raw = json.loads((ROOT / "config/system_registry.json").read_text())
        if raw["registry_version"] == MARKET_LOOKUP_VERSION:
            self.current = market_lookup_predecessor_profile(load_registry(
                ROOT / "config/system_registry.json",
                project_root=ROOT, environment={},
            ))
            self.prior = predecessor_profile(self.current)
            return
        if raw["registry_version"] == PREDECESSOR_VERSION:
            add_declarations(raw, ROOT)
        self.assertEqual(raw["registry_version"], VERSION)
        candidate = self.root / "candidate-registry.json"
        candidate.write_text(
            json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
        )
        self.current = load_registry(candidate, project_root=ROOT, environment={})
        self.prior = predecessor_profile(self.current)

    def _seed(self, role):
        with writer_connection(self.stores, role) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if role == "company":
                _success(connection, "direct", TARGET, "2026-10-16T12:00:00Z")
                _success(connection, "shared", ANCHOR, "2026-10-16T13:00:00Z",
                         output=TARGET)
            else:
                _insert_multi_source_version(connection)
            connection.commit()

    def test_exact_increment_and_populated_migrations_replay_without_fact_changes(self):
        self.assertEqual(self.prior.registry_version, PREDECESSOR_VERSION)
        self.assertEqual(
            tuple(m.id for m in self.current.migrations
                  if m.id not in {old.id for old in self.prior.migrations}),
            MIGRATION_IDS,
        )
        raw = copy.deepcopy(self.prior.raw)
        add_declarations(raw, ROOT)
        self.assertTrue(raw == self.current.raw, "2.101 registry increment drifted")
        for role in ("company", "news"):
            with self.subTest(role=role):
                migration_id = next(mid for mid in MIGRATION_IDS
                                    if mid.startswith(role + ":"))
                self.assertEqual(
                    self.current.store(role).migration_order,
                    self.prior.store(role).migration_order + (migration_id,),
                )
                migrate_and_register_store(
                    self.stores, self.prior, role, applied_at="2026-10-16T00:00:00Z"
                )
                self._seed(role)
                with quiet_immutable_read_connection(self.stores, role) as connection:
                    before_rows, before_triggers, before_ledger = _contents(connection)
                applied = migrate_store(
                    self.stores, self.current, role,
                    applied_at="2026-10-17T00:00:00Z",
                )
                self.assertEqual(applied[-1], migration_id)
                with quiet_immutable_read_connection(self.stores, role) as connection:
                    rows, triggers, ledger = _contents(connection)
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(), []
                    )
                    index, table, columns = INDEXES[role]
                    self.assertIn(index, {row[1] for row in connection.execute(
                        f"PRAGMA index_list('{table}')"
                    )})
                    self.assertEqual(tuple(row[2] for row in connection.execute(
                        f"PRAGMA index_info('{index}')"
                    )), columns)
                    if role == "news":
                        self.assertEqual(tuple(row[3] for row in connection.execute(
                            f"PRAGMA index_xinfo('{index}')"
                        ) if row[5]), (0, 1, 1))
                self.assertEqual(rows, before_rows)
                self.assertEqual(triggers, before_triggers)
                self.assertEqual(ledger[:-1], before_ledger)
                fingerprint = store_mutation_fingerprint(self.stores, role)
                self.assertEqual(migrate_store(
                    self.stores, self.current, role,
                    applied_at="2026-10-17T00:00:00Z",
                ), applied)
                self.assertEqual(
                    store_mutation_fingerprint(self.stores, role), fingerprint
                )

    def test_shared_output_branch_uses_dataset_covering_index(self):
        migrate_and_register_store(
            self.stores, self.current, "company",
            applied_at="2026-10-17T00:00:00Z",
        )
        self._seed("company")
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            traced = []
            connection.set_trace_callback(traced.append)
            row = _generic_capture(connection, TARGET)
            connection.set_trace_callback(None)
            self.assertEqual(row["snapshot_id"], "shared")
            self.assertEqual(row["snapshot_dataset_id"], ANCHOR)
            sql = next(query for query in traced
                       if "SELECT DISTINCT output.dataset_id" in query)
            plan = " ".join(item[3] for item in connection.execute(
                "EXPLAIN QUERY PLAN " + sql
            ))
            self.assertIn(
                "USING COVERING INDEX ingestion_run_outputs_dataset", plan
            )

    def test_news_indexed_winner_matches_legacy_on_failed_ties_and_empty_feed(self):
        migrate_and_register_store(
            self.stores, self.prior, "news",
            applied_at="2026-10-16T00:00:00Z",
        )
        self._seed("news")
        with writer_connection(self.stores, "news") as connection:
            connection.execute("BEGIN IMMEDIATE")
            for attempt_id, outcome_id, kind in (
                ("fed-attempt-Z", "Z-failed", "request_failed"),
                ("fed-attempt-a", "a-rejected", "response_rejected"),
            ):
                connection.execute(
                    "INSERT INTO current_multi_source_attempts "
                    "(attempt_id,dataset_id,collector_id,feed_id,profile_id,"
                    "poll_slot,request_scope_json,request_scope_sha256,"
                    "intent_recorded_at,intent_recorded_precision) "
                    "SELECT ?,dataset_id,collector_id,feed_id,profile_id,"
                    "?,request_scope_json,?,?,intent_recorded_precision "
                    "FROM current_multi_source_attempts "
                    "WHERE attempt_id='fed-attempt-1'",
                    (attempt_id, "2026-10-16T12:00:00Z",
                     hashlib.sha256(attempt_id.encode()).hexdigest(),
                     "2026-10-16T12:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO current_multi_source_outcomes "
                    "(outcome_id,attempt_id,outcome_kind,recorded_at,"
                    "recorded_precision) VALUES (?,?,?,'2026-10-16T12:00:00Z',"
                    "'datetime')",
                    (outcome_id, attempt_id, kind),
                )
            connection.commit()
        source = _SOURCE_BY_ID["fed_press"]
        empty = _SOURCE_BY_ID["ecb_press"]
        with quiet_immutable_read_connection(self.stores, "news") as connection:
            expected = dict(connection.execute(
                _MULTI_LATEST_OUTCOME_SQL, ("fed_press",)
            ).fetchone())
            self.assertEqual(expected["outcome_id"], "a-rejected")
            self.assertEqual(expected["outcome_kind"], "response_rejected")
            self.assertEqual(
                dict(_source_latest_outcome(connection, source)), expected
            )
            self.assertIsNone(_source_latest_outcome(connection, empty))
        migrate_store(
            self.stores, self.current, "news",
            applied_at="2026-10-17T00:00:00Z",
        )
        with quiet_immutable_read_connection(self.stores, "news") as connection:
            traced = []
            connection.set_trace_callback(traced.append)
            indexed = _source_latest_outcome(connection, source)
            connection.set_trace_callback(None)
            self.assertEqual(dict(indexed), expected)
            self.assertIsNone(_source_latest_outcome(connection, empty))
            self.assertTrue(any("INDEXED BY current_multi_source_outcomes_status"
                                in query for query in traced))
            plan = " ".join(item[3] for item in connection.execute(
                "EXPLAIN QUERY PLAN " + _MULTI_LATEST_OUTCOME_INDEXED_SQL,
                ("fed_press",),
            ))
            self.assertIn(
                "USING COVERING INDEX current_multi_source_outcomes_status", plan
            )

    def test_exact_2_101_predecessor_keeps_2_100_catalogs(self):
        canonical = ROOT / "config/system_registry.json"
        self.assertIn(
            json.loads(canonical.read_text())["registry_version"],
            (PREDECESSOR_VERSION, VERSION, MARKET_LOOKUP_VERSION),
        )
        for section in (
            "tool_schema_catalog", "tool_version_schema_catalog",
            "tools", "tool_versions",
        ):
            self.assertTrue(
                self.current.raw[section] == self.prior.raw[section],
                f"{section} changed from the exact predecessor",
            )
        self.assertFalse(any(path.exists() for _, path in self.stores.items()))


class FastCaptureCandidateParityTests(unittest.TestCase):
    def test_newest_candidate_matches_original_join_for_all_fallback_cases(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            connection = sqlite3.connect(Path(temporary) / "status.sqlite")
            connection.row_factory = sqlite3.Row
            try:
                connection.executescript(
                    """
                    CREATE TABLE ingestion_runs (
                        run_id TEXT PRIMARY KEY, dataset_id TEXT, status TEXT,
                        completed_at TEXT, fetched_count INTEGER, written_count INTEGER
                    );
                    CREATE TABLE ingestion_snapshots (
                        snapshot_id TEXT PRIMARY KEY, run_id TEXT, dataset_id TEXT,
                        captured_at TEXT, captured_precision TEXT, row_count INTEGER,
                        completeness TEXT
                    );
                    CREATE INDEX ingestion_snapshots_dataset_capture
                        ON ingestion_snapshots(dataset_id,captured_at DESC,snapshot_id DESC);
                    CREATE TABLE ingestion_run_failures (
                        run_id TEXT PRIMARY KEY, dataset_id TEXT, status TEXT,
                        completed_at TEXT, fetched_count INTEGER
                    );
                    """
                )

                def parity(expected_id, *, malformed=False):
                    old_row = connection.execute(
                        _OLD_CAPTURE_SQL, ("target",)
                    ).fetchone()
                    state = _control_plane_state(connection, "target")
                    old_capture = (
                        None if old_row is None else _capture_from_row(old_row)
                    )
                    self.assertEqual(state.capture, old_capture)
                    self.assertEqual(
                        None if old_row is None else old_row["snapshot_id"],
                        expected_id,
                    )
                    self.assertEqual(state.malformed, malformed)

                def capture(snapshot_id, run_id, status, at, *,
                            dataset="target", precision="datetime"):
                    connection.execute(
                        "INSERT INTO ingestion_runs VALUES (?,?,?,?,1,1)",
                        (run_id, dataset, status,
                         at if status == "succeeded" else None),
                    )
                    connection.execute(
                        "INSERT INTO ingestion_snapshots VALUES (?,?,?,?,?,1,'complete')",
                        (snapshot_id, run_id, dataset, at, precision),
                    )

                parity(None)
                capture("older", "run-older", "succeeded",
                        "2026-10-16T11:00:00Z")
                parity("older")
                capture("newer-running", "run-running", "running",
                        "2026-10-16T12:00:00Z")
                parity("older")
                capture("Z-tie", "run-Z", "succeeded", "2026-10-16T13:00:00Z")
                capture("a-tie", "run-a", "succeeded", "2026-10-16T13:00:00Z")
                parity("a-tie")
                capture("wrong-dataset", "run-wrong", "succeeded",
                        "2026-10-16T16:00:00Z", dataset="other")
                parity("a-tie")
                capture("invalid", "run-invalid", "succeeded",
                        "2026-10-16T15:00:00Z", precision="invalid")
                parity("invalid", malformed=True)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
