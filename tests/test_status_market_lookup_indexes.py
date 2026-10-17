"""Isolated market retained-status lookup migration and reader regressions."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.fingerprint import store_mutation_fingerprint
from quant_data.inspector_retention import _generic_capture, _shared_capture_anchors
from quant_data.migrations import migrate_and_register_store, migrate_store
from quant_data.registry import load_registry
from quant_data.status_market_lookup_registry import (
    MIGRATION_IDS, PREDECESSOR_VERSION, VERSION, add_declarations,
    predecessor_profile,
)
from quant_data.stores import quiet_immutable_read_connection, writer_connection
from quant_data.tool_platform.generate import generated_bytes
from tests.test_status_lookup_indexes import _contents, _stores
from tests.test_status_run_lookup_index import _success


ROOT = Path(__file__).resolve().parents[1]
TARGET = "market.fmp.daily_prices"
ANCHOR = "market.fmp.daily_price_evidence"
INDEXES = {
    "ingestion_snapshots_dataset_capture": (
        "ingestion_snapshots", ("dataset_id", "captured_at", "snapshot_id"),
    ),
    "ingestion_runs_status_capture": (
        "ingestion_runs", ("run_id", "status", "snapshot_id", "dataset_id"),
    ),
    "ingestion_run_outputs_dataset": (
        "ingestion_run_outputs", ("dataset_id", "run_id"),
    ),
}


class StatusMarketLookupIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stores = _stores(self.root)
        raw = json.loads((ROOT / "config/system_registry.json").read_text())
        if raw["registry_version"] == PREDECESSOR_VERSION:
            add_declarations(raw, ROOT)
        self.assertEqual(raw["registry_version"], VERSION)
        self.candidate = self.root / "candidate-registry.json"
        self.candidate.write_text(
            json.dumps(raw, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
        )
        self.current = load_registry(self.candidate, project_root=ROOT, environment={})
        self.prior = predecessor_profile(self.current)

    def _seed(self):
        with writer_connection(self.stores, "market") as connection:
            connection.execute("BEGIN IMMEDIATE")
            _success(connection, "Z-market-direct", TARGET, "2026-10-16T12:00:00Z")
            _success(connection, "a-market-shared", ANCHOR,
                     "2026-10-16T12:00:00Z", output=TARGET)
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
        self.assertTrue(raw == self.current.raw, "2.102 registry increment drifted")
        self.assertEqual(
            self.current.store("market").migration_order,
            self.prior.store("market").migration_order + MIGRATION_IDS,
        )
        migrate_and_register_store(
            self.stores, self.prior, "market", applied_at="2026-10-16T00:00:00Z"
        )
        self._seed()
        with quiet_immutable_read_connection(self.stores, "market") as connection:
            before_rows, before_triggers, before_ledger = _contents(connection)
            before = dict(_generic_capture(connection, TARGET))
        self.assertEqual(before["snapshot_id"], "a-market-shared")
        applied = migrate_store(
            self.stores, self.current, "market", applied_at="2026-10-17T00:00:00Z"
        )
        self.assertEqual(applied[-1], MIGRATION_IDS[0])
        with quiet_immutable_read_connection(self.stores, "market") as connection:
            rows, triggers, ledger = _contents(connection)
            after = dict(_generic_capture(connection, TARGET))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for index, (table, columns) in INDEXES.items():
                self.assertIn(index, {row[1] for row in connection.execute(
                    f"PRAGMA index_list('{table}')"
                )})
                self.assertEqual(tuple(row[2] for row in connection.execute(
                    f"PRAGMA index_info('{index}')"
                )), columns)
            self.assertEqual(tuple(row[3] for row in connection.execute(
                "PRAGMA index_xinfo('ingestion_snapshots_dataset_capture')"
            ) if row[5]), (0, 1, 1))
        self.assertEqual(after, before)
        self.assertEqual(rows, before_rows)
        self.assertEqual(triggers, before_triggers)
        self.assertEqual(ledger[:-1], before_ledger)
        fingerprint = store_mutation_fingerprint(self.stores, "market")
        self.assertEqual(migrate_store(
            self.stores, self.current, "market", applied_at="2026-10-17T00:00:00Z"
        ), applied)
        self.assertEqual(store_mutation_fingerprint(self.stores, "market"), fingerprint)

    def test_market_capture_and_anchor_plans_use_new_indexes(self):
        migrate_and_register_store(
            self.stores, self.current, "market", applied_at="2026-10-17T00:00:00Z"
        )
        self._seed()
        with quiet_immutable_read_connection(self.stores, "market") as connection:
            traced = []
            connection.set_trace_callback(traced.append)
            anchors = _shared_capture_anchors(connection, (TARGET,))
            row = _generic_capture(
                connection, TARGET, shared_anchors=anchors.get(TARGET, ())
            )
            connection.set_trace_callback(None)
            self.assertEqual(row["snapshot_id"], "a-market-shared")
            self.assertEqual(anchors[TARGET], (ANCHOR,))
            sparse = next(query for query in traced
                          if "FROM ingestion_run_outputs output" in query
                          and "CROSS JOIN ingestion_snapshots snapshot" in query)
            sparse_plan = " ".join(item[3] for item in connection.execute(
                "EXPLAIN QUERY PLAN " + sparse
            ))
            self.assertIn("USING COVERING INDEX ingestion_run_outputs_dataset",
                          sparse_plan)
            plans = " ".join(
                item[3] for query in traced
                for item in connection.execute("EXPLAIN QUERY PLAN " + query)
            )
            for index in INDEXES:
                self.assertIn(index, plans)

    def test_candidate_generator_bytes_keep_published_catalogs(self):
        for section in (
            "tool_schema_catalog", "tool_version_schema_catalog",
            "tools", "tool_versions",
        ):
            self.assertTrue(
                self.current.raw[section] == self.prior.raw[section],
                f"{section} changed from the exact predecessor",
            )
        paths = (
            "config/system_registry.json",
            "quant_data/generated/tool_contract_schemas_v1.json",
            "quant_data/generated/tool_contract_schemas_v2.json",
        )
        published = tuple((ROOT / path).read_bytes() for path in paths)
        with patch("sqlite3.connect", side_effect=AssertionError("SQLite access")), \
             patch("socket.create_connection", side_effect=AssertionError("network access")), \
             patch("quant_data.tool_platform.generate.REGISTRY_RESOURCE", self.candidate):
            generated = generated_bytes(ROOT)
        self.assertTrue(generated[0] == self.candidate.read_bytes(),
                        "candidate registry bytes differ from generated bytes")
        for name, actual, expected in zip(paths[1:], generated[1:], published[1:]):
            self.assertTrue(
                actual == expected,
                f"{name}: generated SHA-256 {hashlib.sha256(actual).hexdigest()} "
                f"differs from published {hashlib.sha256(expected).hexdigest()}",
            )
        if json.loads((ROOT / paths[0]).read_text())["registry_version"] == VERSION:
            self.assertTrue(generated[0] == published[0],
                            "published 2.102 registry bytes differ from generated bytes")
        self.assertFalse(any(path.exists() for _, path in self.stores.items()))


if __name__ == "__main__":
    unittest.main()
