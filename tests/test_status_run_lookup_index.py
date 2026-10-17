"""Offline regression for the company Inspector run-metadata lookup index."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.inspector_retention import _generic_capture, read_inspector_retention
from quant_data.migrations import migrate_and_register_store, migrate_store
from quant_data.registry import load_registry
from quant_data.status_run_lookup_registry import (
    MIGRATION_IDS, PREDECESSOR_VERSION, VERSION, add_declarations,
    predecessor_profile,
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
from tests.test_status_lookup_indexes import _contents, _stores


ROOT = Path(__file__).resolve().parents[1]
TARGET = "fixture.company.filings"
ANCHOR = "fixture.company.fundamentals"
INDEX = "ingestion_runs_status_capture"


def _success(connection, snapshot_id: str, dataset: str, captured_at: str,
             *, output: str | None = None) -> None:
    """Insert a valid completed capture through the existing lineage guards."""
    run_id = "run-" + snapshot_id
    artifact_id = "artifact-" + snapshot_id
    semantic = hashlib.sha256(run_id.encode()).hexdigest()
    connection.execute(
        "INSERT INTO ingestion_runs "
        "(run_id,dataset_id,semantic_identity,command,scope_json,status,started_at,"
        "fetched_count,written_count,code_version) "
        "VALUES (?,?,?,'offline-status-test','{}','running',?,1,1,'test')",
        (run_id, dataset, semantic, captured_at),
    )
    connection.execute(
        "INSERT INTO ingestion_run_outputs "
        "(run_id,dataset_id,semantic_identity) VALUES (?,?,?)",
        (run_id, dataset, semantic),
    )
    if output is not None and output != dataset:
        connection.execute(
            "INSERT INTO ingestion_run_outputs "
            "(run_id,dataset_id,semantic_identity) VALUES (?,?,?)",
            (run_id, output, semantic),
        )
    connection.execute(
        "INSERT INTO ingestion_artifacts "
        "(artifact_id,run_id,dataset_id,content_sha256,media_type,byte_count,"
        "source_reference,request_scope_json,captured_at,captured_precision,"
        "normalization_version) "
        "VALUES (?,?,?,?,'application/json',1,'offline-test','{}',?,'datetime','test')",
        (artifact_id, run_id, dataset, semantic, captured_at),
    )
    connection.execute(
        "INSERT INTO ingestion_snapshots "
        "(snapshot_id,run_id,dataset_id,semantic_identity,scope_json,"
        "completeness,row_count,captured_at,captured_precision,"
        "validation_state,warnings_json) "
        "VALUES (?,?,?,?,?,'complete',1,?,'datetime','validated','[]')",
        (snapshot_id, run_id, dataset, semantic, "{}", captured_at),
    )
    connection.execute(
        "INSERT INTO ingestion_snapshot_artifacts "
        "(snapshot_id,artifact_id,artifact_ordinal) VALUES (?,?,1)",
        (snapshot_id, artifact_id),
    )
    connection.execute(
        "UPDATE ingestion_runs SET status='succeeded',completed_at=?,"
        "artifact_id=?,snapshot_id=? WHERE run_id=?",
        (captured_at, artifact_id, snapshot_id, run_id),
    )


class StatusRunLookupIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.stores: StoreMap = _stores(root)
        raw = json.loads((ROOT / "config/system_registry.json").read_text())
        if raw["registry_version"] == MARKET_LOOKUP_VERSION:
            published = market_lookup_predecessor_profile(load_registry(
                ROOT / "config/system_registry.json",
                project_root=ROOT, environment={},
            ))
            self.current = evidence_lookup_predecessor_profile(published)
        elif raw["registry_version"] == EVIDENCE_LOOKUP_VERSION:
            published = load_registry(
                ROOT / "config/system_registry.json",
                project_root=ROOT, environment={},
            )
            self.current = evidence_lookup_predecessor_profile(published)
        else:
            if raw["registry_version"] == PREDECESSOR_VERSION:
                add_declarations(raw, ROOT)
            self.assertEqual(raw["registry_version"], VERSION)
            candidate = root / "candidate-registry.json"
            candidate.write_text(
                json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
            )
            self.current = load_registry(candidate, project_root=ROOT, environment={})
        self.prior = predecessor_profile(self.current)

    def test_exact_2_100_predecessor_keeps_2_99_catalogs(self):
        canonical = ROOT / "config/system_registry.json"
        self.assertIn(
            json.loads(canonical.read_text())["registry_version"],
            (VERSION, EVIDENCE_LOOKUP_VERSION, MARKET_LOOKUP_VERSION),
        )
        self.assertEqual(self.current.registry_version, VERSION)
        self.assertEqual(self.prior.registry_version, PREDECESSOR_VERSION)
        self.assertEqual(
            tuple(m.id for m in self.current.migrations
                  if m.id not in {old.id for old in self.prior.migrations}),
            MIGRATION_IDS,
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

    def _seed_pair(self):
        with writer_connection(self.stores, "company") as connection:
            connection.execute("BEGIN IMMEDIATE")
            _success(connection, "Z-direct", TARGET, "2026-10-16T12:00:00Z")
            _success(connection, "a-shared", ANCHOR, "2026-10-16T12:00:00Z",
                     output=TARGET)
            connection.commit()

    def test_exact_increment_and_populated_migration_replay(self):
        self.assertEqual(self.prior.registry_version, PREDECESSOR_VERSION)
        self.assertEqual(
            tuple(m.id for m in self.current.migrations
                  if m.id not in {old.id for old in self.prior.migrations}),
            MIGRATION_IDS,
        )
        raw = copy.deepcopy(self.prior.raw)
        add_declarations(raw, ROOT)
        self.assertTrue(raw == self.current.raw, "2.100 registry increment drifted")
        self.assertEqual(
            self.current.store("company").migration_order,
            self.prior.store("company").migration_order + MIGRATION_IDS,
        )
        migrate_and_register_store(
            self.stores, self.prior, "company", applied_at="2026-10-16T00:00:00Z"
        )
        self._seed_pair()
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            before_rows, before_triggers, before_ledger = _contents(connection)
            before = dict(_generic_capture(connection, TARGET))
        self.assertEqual(before["snapshot_id"], "a-shared")
        applied = migrate_store(
            self.stores, self.current, "company", applied_at="2026-10-17T00:00:00Z"
        )
        self.assertEqual(applied[-1], MIGRATION_IDS[0])
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            rows, triggers, ledger = _contents(connection)
            after = dict(_generic_capture(connection, TARGET))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(tuple(row[2] for row in connection.execute(
                f"PRAGMA index_info('{INDEX}')"
            )), ("run_id", "status", "snapshot_id", "dataset_id"))
        self.assertEqual(after, before)
        self.assertEqual(rows, before_rows)
        self.assertEqual(triggers, before_triggers)
        self.assertEqual(ledger[:-1], before_ledger)
        fingerprint = store_mutation_fingerprint(self.stores, "company")
        self.assertEqual(migrate_store(
            self.stores, self.current, "company", applied_at="2026-10-17T00:00:00Z"
        ), applied)
        self.assertEqual(store_mutation_fingerprint(self.stores, "company"), fingerprint)

    def test_hot_direct_shared_history_uses_covering_index_under_inspector_deadline(self):
        migrate_and_register_store(
            self.stores, self.current, "company", applied_at="2026-10-17T00:00:00Z"
        )
        with writer_connection(self.stores, "company") as connection:
            connection.execute("BEGIN IMMEDIATE")
            older = datetime(2025, 1, 1, tzinfo=timezone.utc)
            for index in range(1000):
                captured_at = (older + timedelta(minutes=index)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                _success(connection, f"direct-{index:04d}", TARGET, captured_at)
                _success(connection, f"shared-{index:04d}", ANCHOR, captured_at,
                         output=TARGET)
            _success(connection, "Z-direct", TARGET, "2026-10-16T12:00:00Z")
            _success(connection, "a-shared", ANCHOR, "2026-10-16T12:00:00Z",
                     output=TARGET)
            connection.commit()

        with quiet_immutable_read_connection(self.stores, "company") as connection:
            traced = []
            connection.set_trace_callback(traced.append)
            row = _generic_capture(connection, TARGET)
            connection.set_trace_callback(None)
            self.assertEqual(row["snapshot_id"], "a-shared")
            self.assertEqual(row["snapshot_dataset_id"], ANCHOR)
            self.assertEqual(row["anchor_dataset_id"], ANCHOR)
            queries = [query for query in traced
                       if "FROM ingestion_snapshots snapshot" in query
                       and "UNION ALL" not in query]
            self.assertGreaterEqual(len(queries), 2)
            plan = " ".join(
                item[3] for query in queries
                for item in connection.execute("EXPLAIN QUERY PLAN " + query)
            )
            self.assertGreaterEqual(
                plan.count(f"USING COVERING INDEX {INDEX}"), 2, plan
            )

        # This calls Inspector's real 3-second progress deadline on the hot
        # direct/shared output history, with only the company target selected.
        target_registry = replace(
            self.current,
            datasets=tuple(d for d in self.current.datasets if d.id == TARGET),
        )
        retention = read_inspector_retention(
            self.stores, target_registry,
            observed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        )[TARGET]
        self.assertEqual(retention["retention_state"], "retained")
        self.assertEqual(retention["retention_basis"], "shared")
        self.assertEqual(retention["capture_anchor_id"], ANCHOR)
        self.assertEqual(retention["retained_capture_at"], "2026-10-16T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
