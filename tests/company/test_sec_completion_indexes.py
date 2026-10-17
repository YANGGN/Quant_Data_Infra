from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quant_data.company.sec_companyfacts import SecAaplCompanyFactsPublisher, parse_sec_aapl_bundle
from quant_data.company.sec_completion_registry import MIGRATION_ID, PREDECESSOR_SHA256, add_declarations
from quant_data.errors import RegistryError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.migrations import initialize_all, migrate_store
from quant_data.registry import load_registry, sec_completion_registry_profile, fmp_research_lookup_registry_profile
from quant_data.stores import StoreMap, quiet_immutable_read_connection, writer_connection
from quant_data.tool_platform.generate import generated_bytes
from tests.company.test_sec_companyfacts import _submissions_body, _companyfacts_body

ROOT = Path(__file__).resolve().parents[2]
AT = "2026-09-09T21:00:00Z"
INDEXES = {
    "idx_company_sec_filings_run_id": ("company_sec_filings", ("run_id",)),
    "idx_company_sec_fact_versions_run_id": ("company_sec_fact_versions", ("run_id",)),
    "idx_company_sec_fact_membership_version_run": (
        "company_sec_fact_snapshot_membership", ("fact_version_id", "run_id")),
}


class SecCompletionIndexesTests(unittest.TestCase):
    def setUp(self):
        self.current = fmp_research_lookup_registry_profile(load_registry(ROOT / "config/system_registry.json", project_root=ROOT, environment={}))
        self.prior = sec_completion_registry_profile(self.current)

    def test_exact_predecessor_and_generated_catalogs_unchanged(self):
        self.assertEqual(self.prior.registry_version, "2.79.0")
        self.assertEqual(self.prior.source_sha256, PREDECESSOR_SHA256)
        for section in ("datasets", "collectors", "tools", "dashboard", "presentation_order"):
            self.assertEqual(self.current.raw[section], self.prior.raw[section])
        for migration in self.prior.migrations:
            self.assertEqual(next(m for m in self.current.migrations if m.id == migration.id), migration)
            self.assertEqual(hashlib.sha256((ROOT / migration.resource).read_bytes()).hexdigest(), migration.sha256)
        raw = copy.deepcopy(self.prior.raw)
        add_declarations(raw, ROOT)
        self.assertEqual(raw, self.current.raw)
        self.assertEqual(generated_bytes(ROOT), (
            (ROOT / "config/system_registry.json").read_bytes(),
            (ROOT / "quant_data/generated/tool_contract_schemas_v1.json").read_bytes(),
            (ROOT / "quant_data/generated/tool_contract_schemas_v2.json").read_bytes(),
        ))

    def test_projection_rejects_raw_or_parsed_migration_drift(self):
        raw = copy.deepcopy(self.current.raw)
        raw["migrations"][-1]["semantic_scope"] += " drift"
        with self.assertRaises(RegistryError):
            sec_completion_registry_profile(replace(self.current, raw=raw))
        migrations = tuple(replace(m, ordinal=99) if m.id == MIGRATION_ID else m for m in self.current.migrations)
        with self.assertRaises(RegistryError):
            sec_completion_registry_profile(replace(self.current, migrations=migrations))
        raw = copy.deepcopy(self.prior.raw)
        raw["collectors"][0]["handler"] += "_drift"
        with self.assertRaises(ValueError):
            add_declarations(raw, ROOT)

    def test_populated_store_preserves_evidence_guards_and_exact_replay(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            stores = StoreMap.four_explicit(**{
                role: Path(temporary) / (role + ".sqlite")
                for role in ("market", "macro", "company", "news")
            })
            initialize_all(stores, self.prior)
            parsed = parse_sec_aapl_bundle(
                submissions_body=_submissions_body(), companyfacts_body=_companyfacts_body(),
                captured_at=datetime(2026, 5, 1, 18, tzinfo=timezone.utc),
            )
            SecAaplCompanyFactsPublisher(stores, self.prior).publish(parsed)

            def preserved():
                with quiet_immutable_read_connection(stores, "company") as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' ORDER BY name"
                    )]
                    rows = {table: sorted(tuple(row) for row in connection.execute('SELECT * FROM "' + table + '"'))
                            for table in tables}
                    triggers = [tuple(row) for row in connection.execute(
                        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
                    ledger = [tuple(row) for row in connection.execute(
                        "SELECT * FROM schema_migrations ORDER BY ordinal")]
                    return rows, triggers, ledger

            before_rows, before_triggers, before_ledger = preserved()
            result = migrate_store(stores, self.current, "company", applied_at=AT)
            self.assertEqual(result, self.current.store("company").migration_order)
            after_rows, after_triggers, after_ledger = preserved()
            self.assertEqual(after_rows, before_rows)
            self.assertEqual(after_triggers, before_triggers)
            self.assertEqual(after_ledger[:-1], before_ledger)
            with quiet_immutable_read_connection(stores, "company") as connection:
                for index, (table, columns) in INDEXES.items():
                    details = next(row for row in connection.execute('PRAGMA index_list("' + table + '")') if row[1] == index)
                    self.assertEqual(details[2], 0)
                    self.assertEqual(tuple(row[2] for row in connection.execute('PRAGMA index_info("' + index + '")')), columns)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            fingerprint = mutation_fingerprint(stores)
            self.assertEqual(migrate_store(stores, self.current, "company", applied_at=AT), result)
            self.assertEqual(SecAaplCompanyFactsPublisher(stores, self.current).publish(parsed).outcome, "unchanged")
            self.assertEqual(mutation_fingerprint(stores), fingerprint)
            with writer_connection(stores, "company") as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM company_sec_fact_snapshot_membership")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE company_sec_fact_versions SET run_id='missing'")

    def test_completion_queries_use_indexes_for_both_outer_rows_and_membership(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            stores = StoreMap.four_explicit(**{
                role: Path(temporary) / (role + ".sqlite")
                for role in ("market", "macro", "company", "news")
            })
            initialize_all(stores, self.current)
            with quiet_immutable_read_connection(stores, "company") as connection:
                for table, membership, key, index in (
                    ("company_sec_fact_versions", "company_sec_fact_snapshot_membership",
                     "fact_version_id", "idx_company_sec_fact_membership_version_run"),
                    ("company_sec_filings", "company_sec_filing_snapshot_membership",
                     "accession_number", "idx_company_sec_filing_membership_accession"),
                ):
                    query = ("SELECT 1 FROM " + table + " AS source WHERE source.run_id=? AND NOT EXISTS("
                             "SELECT 1 FROM " + membership + " AS membership WHERE membership." + key +
                             "=source." + key + " AND membership.run_id=?)")
                    plan = " ".join(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query, ("run", "run")))
                    self.assertIn("idx_" + table + "_run_id", plan)
                    self.assertIn(index, plan)
                    self.assertNotIn("SCAN source", plan)
                    self.assertNotIn("SCAN membership", plan)


if __name__ == "__main__":
    unittest.main()
