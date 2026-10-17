"""Isolated regressions for the retained dataset-status lookup indexes."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.data_status import _control_plane_state
from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.migrations import migrate_and_register_store, migrate_store
from quant_data.news.tool_repository import (
    _FMP_LATEST_CAPTURE_SQL,
    _FMP_LATEST_OUTCOME_SQL,
    _MULTI_LATEST_OUTCOME_SQL,
)
from quant_data.registry import load_registry
from quant_data.status_lookup_registry import (
    MIGRATION_IDS,
    PREDECESSOR_VERSION,
    VERSION,
    add_declarations,
    predecessor_profile,
)
from quant_data.status_run_lookup_registry import (
    VERSION as RUN_LOOKUP_VERSION,
    predecessor_profile as run_lookup_predecessor_profile,
)
from quant_data.status_evidence_lookup_registry import (
    VERSION as EVIDENCE_LOOKUP_VERSION,
    predecessor_profile as evidence_lookup_predecessor_profile,
)
from quant_data.status_market_lookup_registry import (
    VERSION as MARKET_LOOKUP_VERSION,
    predecessor_profile as market_lookup_predecessor_profile,
)
from quant_data.stores import StoreMap, quiet_immutable_read_connection, writer_connection
from tests.news.test_tool_repository import (
    _insert_fmp_failure,
    _insert_fmp_success,
    _insert_multi_source_version,
)


ROOT = Path(__file__).resolve().parents[1]
INDEXES = {
    "company": {
        "ingestion_snapshots_dataset_capture": (
            "ingestion_snapshots", ("dataset_id", "captured_at", "snapshot_id"),
        ),
    },
    "news": {
        "fmp_stock_latest_current_captures_time": (
            "fmp_stock_latest_current_captures", ("captured_at", "capture_id"),
        ),
        "current_multi_source_attempts_feed": (
            "current_multi_source_attempts", ("feed_id", "attempt_id"),
        ),
    },
}


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(**{
        role: root / f"{role}.sqlite" for role in ("market", "macro", "company", "news")
    })


def _contents(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' ORDER BY name"
    )]
    rows = {table: sorted(
        (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')),
        key=repr,
    ) for table in tables}
    triggers = [tuple(row) for row in connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    )]
    ledger = [tuple(row) for row in connection.execute(
        "SELECT * FROM schema_migrations ORDER BY ordinal"
    )]
    return rows, triggers, ledger


class StatusLookupIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stores = _stores(self.root)
        canonical = ROOT / "config/system_registry.json"
        raw = json.loads(canonical.read_text())
        if raw["registry_version"] == MARKET_LOOKUP_VERSION:
            current_registry = market_lookup_predecessor_profile(load_registry(
                canonical, project_root=ROOT, environment={}
            ))
            self.current = run_lookup_predecessor_profile(
                evidence_lookup_predecessor_profile(current_registry)
            )
        elif raw["registry_version"] == EVIDENCE_LOOKUP_VERSION:
            current_registry = load_registry(
                canonical, project_root=ROOT, environment={}
            )
            self.current = run_lookup_predecessor_profile(
                evidence_lookup_predecessor_profile(current_registry)
            )
        elif raw["registry_version"] == RUN_LOOKUP_VERSION:
            self.current = run_lookup_predecessor_profile(
                load_registry(canonical, project_root=ROOT, environment={})
            )
        else:
            if raw["registry_version"] == PREDECESSOR_VERSION:
                add_declarations(raw, ROOT)
            self.assertEqual(raw["registry_version"], VERSION)
            candidate = self.root / "candidate-registry.json"
            candidate.write_text(
                json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
            )
            self.current = load_registry(candidate, project_root=ROOT, environment={})
        self.prior = predecessor_profile(self.current)

    def test_registry_increment_is_exactly_two_additive_migrations(self):
        self.assertEqual(self.prior.registry_version, PREDECESSOR_VERSION)
        self.assertEqual(tuple(m.id for m in self.current.migrations
                               if m.id not in {m.id for m in self.prior.migrations}), MIGRATION_IDS)
        raw = copy.deepcopy(self.prior.raw)
        add_declarations(raw, ROOT)
        self.assertTrue(raw == self.current.raw, "2.99 registry increment drifted")
        for role in ("company", "news"):
            old_order = self.prior.store(role).migration_order
            new_order = self.current.store(role).migration_order
            self.assertEqual(new_order[:-1], old_order)
            self.assertEqual(new_order[-1], next(mid for mid in MIGRATION_IDS
                                                  if mid.startswith(role + ":")))

    def _seed_company(self):
        with writer_connection(self.stores, "company") as connection:
            dataset = connection.execute(
                "SELECT dataset_id FROM dataset_registry ORDER BY dataset_id LIMIT 1"
            ).fetchone()[0]
            for suffix, status, captured in (
                ("a", "succeeded", "2026-10-15T12:00:00Z"),
                ("z", "succeeded", "2026-10-15T12:00:00Z"),
                ("future", "running", "2026-10-16T12:00:00Z"),
            ):
                run_id = "status-" + suffix
                semantic_identity = hashlib.sha256(suffix.encode()).hexdigest()
                connection.execute(
                    "INSERT INTO ingestion_runs "
                    "(run_id,dataset_id,semantic_identity,command,scope_json,status,started_at,"
                    "fetched_count,written_count,code_version) "
                    "VALUES (?,?,?,'offline-status-test','{}','running',?,1,1,'test')",
                    (run_id, dataset, semantic_identity, captured),
                )
                if status == "succeeded":
                    connection.execute(
                        "INSERT INTO ingestion_run_outputs "
                        "(run_id,dataset_id,semantic_identity) VALUES (?,?,?)",
                        (run_id, dataset, semantic_identity),
                    )
                    artifact_id = "artifact-" + suffix
                    connection.execute(
                        "INSERT INTO ingestion_artifacts "
                        "(artifact_id,run_id,dataset_id,content_sha256,media_type,"
                        "byte_count,source_reference,request_scope_json,captured_at,"
                        "captured_precision,normalization_version) "
                        "VALUES (?,?,?,?, 'application/json',1,'offline-test','{}',"
                        "?,'datetime','test')",
                        (artifact_id, run_id, dataset, semantic_identity, captured),
                    )
                    connection.execute(
                        "INSERT INTO ingestion_snapshots "
                        "(snapshot_id,run_id,dataset_id,semantic_identity,scope_json,"
                        "completeness,row_count,captured_at,captured_precision,"
                        "validation_state,warnings_json) "
                        "VALUES (?,?,?,?,?,'complete',1,?,'datetime','validated','[]')",
                        ("capture-" + suffix, run_id, dataset, semantic_identity,
                         "{}", captured),
                    )
                    connection.execute(
                        "INSERT INTO ingestion_snapshot_artifacts "
                        "(snapshot_id,artifact_id,artifact_ordinal) VALUES (?,?,1)",
                        ("capture-" + suffix, artifact_id),
                    )
                    connection.execute(
                        "UPDATE ingestion_runs SET status='succeeded',completed_at=?,"
                        "artifact_id=?,snapshot_id=? "
                        "WHERE run_id=?",
                        (captured, artifact_id, "capture-" + suffix, run_id),
                    )
            connection.execute(
                "INSERT INTO ingestion_run_failures "
                "(failure_id,run_id,dataset_id,semantic_identity,command,scope_json,"
                "status,started_at,completed_at,fetched_count,error_code,code_version) "
                "VALUES ('failure','failed-run',?,?,'offline-status-test','{}','failed',"
                "'2026-10-16T11:00:00Z','2026-10-16T11:00:00Z',0,'test','test')",
                (dataset, "f" * 64),
            )
        return dataset

    def _seed_news(self):
        with writer_connection(self.stores, "news") as connection:
            for suffix, captured_at in (
                ("a", "2026-10-15T11:59:00Z"),
                ("z", "2026-10-15T12:00:00Z"),
            ):
                _insert_fmp_success(
                    connection, suffix=suffix, captured_at=captured_at,
                    provider_rows=1, accepted_rows=1, rejected_rows=0,
                )
            _insert_fmp_failure(connection, captured_at="2026-10-16T12:00:00Z")
            _insert_multi_source_version(connection)
            connection.execute(
                "INSERT INTO current_multi_source_attempts "
                "(attempt_id,dataset_id,collector_id,feed_id,profile_id,poll_slot,"
                "request_scope_json,request_scope_sha256,intent_recorded_at,"
                "intent_recorded_precision) "
                "SELECT 'fed-attempt-later',dataset_id,collector_id,feed_id,profile_id,"
                "'2026-10-16T12:00:00Z',request_scope_json,?,"
                "'2026-10-16T12:00:00Z',intent_recorded_precision "
                "FROM current_multi_source_attempts WHERE attempt_id='fed-attempt-1'",
                (hashlib.sha256(b"fed-later-scope").hexdigest(),),
            )
            connection.execute(
                "INSERT INTO current_multi_source_outcomes "
                "(outcome_id,attempt_id,outcome_kind,recorded_at,recorded_precision) "
                "VALUES ('fed-outcome-later','fed-attempt-later','request_failed',"
                "'2026-10-16T12:00:00Z','datetime')"
            )

    def test_populated_migrations_preserve_rows_triggers_ledger_and_replay(self):
        for role, seed in (("company", self._seed_company), ("news", self._seed_news)):
            with self.subTest(role=role):
                migrate_and_register_store(
                    self.stores, self.prior, role, applied_at="2026-10-15T00:00:00Z"
                )
                seed()
                with quiet_immutable_read_connection(self.stores, role) as connection:
                    before_rows, before_triggers, before_ledger = _contents(connection)
                applied = migrate_store(
                    self.stores, self.current, role, applied_at="2026-10-16T00:00:00Z"
                )
                self.assertEqual(applied[-1], next(mid for mid in MIGRATION_IDS
                                                   if mid.startswith(role + ":")))
                with quiet_immutable_read_connection(self.stores, role) as connection:
                    rows, triggers, ledger = _contents(connection)
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                    for index, (table, columns) in INDEXES[role].items():
                        self.assertIn(index, {row[1] for row in connection.execute(
                            f"PRAGMA index_list('{table}')"
                        )})
                        self.assertEqual(tuple(row[2] for row in connection.execute(
                            f"PRAGMA index_info('{index}')"
                        )), columns)
                self.assertEqual(rows, before_rows)
                self.assertEqual(triggers, before_triggers)
                self.assertEqual(ledger[:-1], before_ledger)
                fingerprint = store_mutation_fingerprint(self.stores, role)
                self.assertEqual(migrate_store(
                    self.stores, self.current, role, applied_at="2026-10-16T00:00:00Z"
                ), applied)
                self.assertEqual(store_mutation_fingerprint(self.stores, role), fingerprint)

    def test_status_queries_use_indexes_and_keep_success_selection(self):
        for role, seed in (("company", self._seed_company), ("news", self._seed_news)):
            migrate_and_register_store(
                self.stores, self.current, role, applied_at="2026-10-16T00:00:00Z"
            )
            dataset = seed()
            with quiet_immutable_read_connection(self.stores, role) as connection:
                if role == "company":
                    traced = []
                    connection.set_trace_callback(traced.append)
                    state = _control_plane_state(connection, dataset)
                    connection.set_trace_callback(None)
                    self.assertEqual(state.capture.primitive["snapshot_id"], "capture-z")
                    self.assertEqual(state.outcome["outcome_kind"], "failed")
                    capture_sql = next(sql for sql in traced if "SELECT snapshot.snapshot_id" in sql)
                    plan = " ".join(row[3] for row in connection.execute(
                        "EXPLAIN QUERY PLAN " + capture_sql
                    ))
                    self.assertIn("ingestion_snapshots_dataset_capture", plan)
                    self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan)
                else:
                    capture = connection.execute(_FMP_LATEST_CAPTURE_SQL).fetchone()
                    self.assertEqual(capture["capture_id"], "fmp-capture-z")
                    latest_fmp_outcome = connection.execute(
                        _FMP_LATEST_OUTCOME_SQL
                    ).fetchone()
                    self.assertEqual(
                        latest_fmp_outcome["outcome_id"], "fmp-outcome-failure"
                    )
                    outcome = connection.execute(_MULTI_LATEST_OUTCOME_SQL,
                                                 ("fed_press",)).fetchone()
                    self.assertEqual(outcome["outcome_id"], "fed-outcome-later")
                    for sql, args, index in (
                        (_FMP_LATEST_CAPTURE_SQL, (), "fmp_stock_latest_current_captures_time"),
                        (_MULTI_LATEST_OUTCOME_SQL, ("fed_press",),
                         "current_multi_source_attempts_feed"),
                    ):
                        plan = " ".join(row[3] for row in connection.execute(
                            "EXPLAIN QUERY PLAN " + sql, args
                        ))
                        self.assertIn(index, plan)
                        if sql == _FMP_LATEST_CAPTURE_SQL:
                            self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan)


class LatestOutcomeLookupTests(unittest.TestCase):
    def test_outcome_order_and_bounded_success_history(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            connection = sqlite3.connect(Path(temporary) / "status.sqlite")
            self.addCleanup(connection.close)
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE ingestion_runs (
                    run_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL,
                    status TEXT NOT NULL, completed_at TEXT,
                    fetched_count INTEGER NOT NULL, written_count INTEGER NOT NULL
                );
                CREATE INDEX ingestion_runs_dataset_completion
                    ON ingestion_runs(dataset_id, completed_at);
                CREATE TABLE ingestion_snapshots (
                    snapshot_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, captured_at TEXT NOT NULL,
                    captured_precision TEXT NOT NULL, row_count INTEGER NOT NULL,
                    completeness TEXT NOT NULL
                );
                CREATE TABLE ingestion_run_failures (
                    run_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL,
                    status TEXT NOT NULL, completed_at TEXT NOT NULL,
                    fetched_count INTEGER NOT NULL
                );
                """
            )

            def success(run_id, at, dataset="target"):
                connection.execute(
                    "INSERT INTO ingestion_runs VALUES (?,?,'succeeded',?,1,1)",
                    (run_id, dataset, at),
                )

            def failure(run_id, kind, at, dataset="target"):
                connection.execute(
                    "INSERT INTO ingestion_run_failures VALUES (?,?,?,?,0)",
                    (run_id, dataset, kind, at),
                )

            def observed():
                traced = []
                steps = 0

                def count_work():
                    nonlocal steps
                    steps += 1
                    return 0

                connection.set_trace_callback(traced.append)
                connection.set_progress_handler(count_work, 10)
                try:
                    state = _control_plane_state(connection, "target")
                finally:
                    connection.set_progress_handler(None, 0)
                    connection.set_trace_callback(None)
                outcome_sql = next(
                    sql for sql in traced if "SELECT run_id, outcome_kind" in sql
                )
                return state.outcome, outcome_sql, steps

            # An unrelated dataset has newer evidence and must never win.
            success("other-success", "2026-10-17T00:00:00Z", "other")
            failure("other-failed", "failed", "2026-10-17T01:00:00Z", "other")
            success("success-m", "2026-10-16T12:00:00Z")
            failure("failed-old", "failed", "2026-10-16T11:00:00Z")
            self.assertEqual(observed()[0]["run_id"], "success-m")

            failure("failed-a", "failed", "2026-10-16T12:30:00Z")
            self.assertEqual(observed()[0]["run_id"], "failed-a")
            failure("partial-z", "partial", "2026-10-16T12:30:00Z")
            self.assertEqual(observed()[0]["run_id"], "partial-z")
            self.assertEqual(observed()[0]["outcome_kind"], "partial")

            success("success-zz", "2026-10-16T12:30:00Z")
            self.assertEqual(observed()[0]["run_id"], "success-zz")
            failure("zzz-failed", "failed", "2026-10-16T12:30:00Z")
            winner, outcome_sql, baseline_work = observed()
            self.assertEqual(winner["run_id"], "zzz-failed")
            self.assertEqual(winner["outcome_kind"], "failed")
            plan = " ".join(
                row[3] for row in connection.execute(
                    "EXPLAIN QUERY PLAN " + outcome_sql
                )
            )
            self.assertIn("ingestion_runs_dataset_completion", plan)

            older = datetime(2025, 1, 1, tzinfo=timezone.utc)
            connection.executemany(
                "INSERT INTO ingestion_runs VALUES (?, 'target','succeeded',?,1,1)",
                (
                    (f"older-{index:04d}",
                     (older + timedelta(minutes=index)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ"
                     ))
                    for index in range(2000)
                ),
            )
            after, _, history_work = observed()
            self.assertEqual(after, winner)
            self.assertLessEqual(
                history_work, baseline_work + 50,
                (baseline_work, history_work, plan),
            )


if __name__ == "__main__":
    unittest.main()
