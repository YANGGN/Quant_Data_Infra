"""Offline v2-to-v3 options migration and cross-asset boundary checks."""
from copy import deepcopy
import json
import socket
import sqlite3
import unittest
from unittest.mock import patch

from quant_data.options.daily import POLICY
from quant_data.options.model import build_capture
from quant_data.options.store import OptionsStore
from quant_data.options.universe import CROSS_ASSET_ETFS, EXPANSION_ETFS, OPTIONS_ETFS, TIER1_ETFS
from tests.options.test_theta_compact import PREVIOUS, SESSION, fixtures, temporary_store


FACT_TABLES = (
    "option_captures", "option_current", "option_contracts", "option_details",
    "option_daily_summaries", "option_activity_leaders", "option_source_receipts",
)


def small_capture(symbol):
    eod, oi, receipts = fixtures()
    eod, oi = eod[:8], oi[:8]
    for row in eod + oi:
        row["symbol"] = symbol
    receipts[0]["params"] = {"symbol": symbol}
    return build_capture(SESSION, eod, oi, receipts,
                         previous_session=PREVIOUS, symbol=symbol)


def facts(store):
    with store.read() as connection:
        result = {}
        for table in FACT_TABLES:
            columns = len(connection.execute("PRAGMA table_info(" + table + ")").fetchall())
            rows = connection.execute(
                "SELECT * FROM " + table + " ORDER BY " + ",".join(str(n) for n in range(1, columns + 1)))
            result[table] = [tuple(row) for row in rows]
        return result


def ledger(store):
    with store.read() as connection:
        return [tuple(row) for row in connection.execute(
            "SELECT ordinal,migration_id,resource,sha256 FROM schema_migrations ORDER BY ordinal")]


def v2_store(symbols):
    temp, current = temporary_store()
    registry_path = current.root / "config/options_registry.json"
    v3_registry = json.loads(registry_path.read_text())
    v2_registry = deepcopy(v3_registry)
    v2_registry["version"] = "1.1.0"
    v2_registry["migrations"] = v2_registry["migrations"][:2]
    v2_registry.pop("scheduled_collection_cross_asset")
    registry_path.write_text(json.dumps(v2_registry))
    old = OptionsStore(current.root)
    old.initialize()
    for symbol in symbols:
        old.publish(small_capture(symbol))
    return temp, old, registry_path, v3_registry


class CrossAssetMigrationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("socket.create_connection", side_effect=AssertionError("offline test")).start()
        patch.object(socket.socket, "connect", side_effect=AssertionError("offline test")).start()

    def test_upgrade_preserves_all_15_roots_and_every_fact(self):
        temp, old, registry_path, v3_registry = v2_store(TIER1_ETFS)
        with temp:
            before = facts(old)
            old_ledger = ledger(old)
            self.assertEqual(len(before["option_captures"]), 15)
            self.assertEqual(len(before["option_daily_summaries"]), 15 * 102)
            self.assertEqual(len(old_ledger), 2)
            registry_path.write_text(json.dumps(v3_registry))
            upgraded = OptionsStore(old.root)
            # The current reader accepts an already applied migration prefix.
            self.assertEqual(facts(upgraded), before)
            with upgraded.read() as connection:
                with self.assertRaisesRegex(ValueError, "migration_ledger"):
                    upgraded._check(connection, require_latest=True)
            self.assertTrue(upgraded.initialize())
            self.assertEqual(facts(upgraded), before)
            self.assertEqual(ledger(upgraded)[:2], old_ledger)
            self.assertEqual(len(ledger(upgraded)), 3)
            with upgraded.read() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            stable = upgraded.path.read_bytes()
            self.assertFalse(upgraded.initialize())
            self.assertEqual(upgraded.path.read_bytes(), stable)
            replay = upgraded.publish(small_capture("SPY"))
            self.assertEqual(replay["outcome"], "replay")
            self.assertEqual(replay["canonical_changes"], 0)
            self.assertEqual(upgraded.path.read_bytes(), stable)

    def test_new_roots_publish_replay_and_reject_cross_symbol_data(self):
        temp, store = temporary_store()
        with temp:
            store.initialize()
            for symbol in CROSS_ASSET_ETFS:
                result = store.publish(small_capture(symbol))
                self.assertEqual(result["outcome"], "published", symbol)
            with store.read() as connection:
                roots = {row[0] for row in connection.execute(
                    "SELECT symbol FROM option_current")}
                self.assertEqual(roots, set(CROSS_ASSET_ETFS))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            # The migrated tables enforce the roster even outside the publisher.
            with sqlite3.connect(store.path) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO option_captures(symbol,session_date,predecessor_id,"
                        "semantic_sha256,captured_at,underlying_price,metadata) "
                        "VALUES (?,?,?,?,?,?,?)",
                        ("AAPL", SESSION, None, "a" * 64, "2026-09-24T10:00:00+00:00", "100", b"{}"),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO option_contracts VALUES (?,?,?,?,?,?)",
                        ("AAPL|2026-10-01|100|C", "AAPL", "2026-10-01", "100", "C",
                         "provider_root_only_unverified"),
                    )
            stable = store.path.read_bytes()
            for symbol in CROSS_ASSET_ETFS:
                replay = store.publish(small_capture(symbol))
                self.assertEqual((replay["outcome"], replay["canonical_changes"]), ("replay", 0))
            self.assertEqual(store.path.read_bytes(), stable)
            invalid = small_capture("GLD")
            invalid["symbol"] = "AAPL"
            with self.assertRaisesRegex(ValueError, "unsupported_option_root"):
                store.publish(invalid)
            invalid = small_capture("GLD")
            invalid["details"][0]["contract_id"] = invalid["details"][0]["contract_id"].replace("GLD|", "SPY|")
            with self.assertRaisesRegex(ValueError, "cross_symbol_contract"):
                store.publish(invalid)
            self.assertEqual(store.path.read_bytes(), stable)

    def test_failed_third_migration_rolls_back_tables_and_ledger(self):
        temp, old, registry_path, v3_registry = v2_store(("SPY", "XLF"))
        with temp:
            before = facts(old)
            old_ledger = ledger(old)
            with old.read() as connection:
                schema_before = [tuple(row) for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE tbl_name IN ('option_captures','option_contracts') "
                    "ORDER BY type,name")]
            registry_path.write_text(json.dumps(v3_registry))
            upgraded = OptionsStore(old.root)
            upgraded.migration_sql[2] += b"\nSELECT * FROM injected_missing_table;"
            with self.assertRaises(sqlite3.OperationalError):
                upgraded.initialize()
            self.assertEqual(facts(old), before)
            self.assertEqual(ledger(old), old_ledger)
            with old.read() as connection:
                schema_after = [tuple(row) for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE tbl_name IN ('option_captures','option_contracts') "
                    "ORDER BY type,name")]
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(schema_after, schema_before)
            self.assertTrue(OptionsStore(old.root).initialize())
            self.assertEqual(facts(OptionsStore(old.root)), before)

    def test_third_migration_checksum_and_ordinal_fail_before_store_access(self):
        temp, store = temporary_store()
        with temp:
            registry_path = store.root / "config/options_registry.json"
            original = json.loads(registry_path.read_text())
            bad = deepcopy(original)
            bad["migrations"][2]["sha256"] = "0" * 64
            registry_path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "migration_checksum"):
                OptionsStore(store.root)
            bad = deepcopy(original)
            bad["migrations"][2]["ordinal"] = 2
            registry_path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "migration_order"):
                OptionsStore(store.root)
            registry_path.write_text(json.dumps(original))
            migration = store.root / original["migrations"][2]["resource"]
            migration.write_bytes(migration.read_bytes() + b"\n-- changed bytes")
            with self.assertRaisesRegex(ValueError, "migration_checksum"):
                OptionsStore(store.root)
            self.assertFalse(store.path.exists())

    def test_historical_collector_scope_remains_the_original_tier1_set(self):
        temp, store = temporary_store()
        with temp:
            registry = store.registry
            self.assertEqual(tuple(OPTIONS_ETFS), TIER1_ETFS + CROSS_ASSET_ETFS)
            self.assertEqual(tuple(registry["expansion_collector"]["symbols"]), EXPANSION_ETFS)
            self.assertEqual(EXPANSION_ETFS, TIER1_ETFS[1:])
            self.assertEqual(registry["collector"]["symbol"], "SPY")
            self.assertEqual(tuple(registry["scheduled_collection"]["symbols"]), TIER1_ETFS)
            self.assertEqual(tuple(POLICY["symbols"]), TIER1_ETFS)


if __name__ == "__main__":
    unittest.main()
