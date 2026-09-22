from __future__ import annotations
from dataclasses import replace
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from quant_data.errors import ConflictError, ValidationError, ResourceLimitError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_universe import CollectionManifestPublisher, parse_manifest, read_collection
from quant_data.migrations import initialize_all, migrate_store
from quant_data.registry import load_registry, collection_universe_registry_profile
from quant_data.stores import StoreMap, quiet_immutable_read_connection, writer_connection
from quant_data.tool_platform.generate import generated_bytes

ROOT = Path(__file__).resolve().parents[2]
AT = "2026-09-09T01:00:00Z"

def manifest(body=b"Symbol,Description\nAAPL,Apple\nBRK.B,Berkshire B\n", at=AT):
    return parse_manifest(body=body, universe_id="major_index_liquid", name="Major Index Liquid",
        source_reference="inputs/selection.csv", source_label_date="2026-09-08", captured_at=at)

class CollectionUniverseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.stores = StoreMap.four_explicit(**{r:Path(self.temp.name)/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry = load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        self.publisher = CollectionManifestPublisher(self.stores,self.registry)
    def tearDown(self): self.temp.cleanup()

    def test_selected_file_retains_all_source_symbols_and_original_bytes(self):
        body=(ROOT/"Major Index Liquid_2026-09-08.csv").read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(),"9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a")
        parsed=manifest(body)
        self.assertEqual(len(parsed.members),2248)
        self.assertIn("BRK.B",{m.symbol for m in parsed.members})
        self.assertNotIn("BRK-B",{m.symbol for m in parsed.members})
        self.publisher.publish(parsed)
        selection=read_collection(self.stores,parsed.universe_id)
        self.assertEqual(selection.members,parsed.members)
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT raw_body FROM market_collection_artifacts").fetchone()[0],body)
            self.assertEqual(c.execute("SELECT count(*) FROM stage10_instruments").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM instruments").fetchone()[0],0)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_replay_and_reordering_cause_zero_persistent_change(self):
        self.publisher.publish(manifest())
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish(manifest()).outcome,"unchanged")
        reordered=manifest(b"Symbol,Description\nBRK.B,Berkshire B\nAAPL,Apple\n",at="2026-09-09T02:00:00Z")
        self.assertEqual(self.publisher.publish(reordered).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_changed_membership_and_return_to_prior_state_are_observed_transitions(self):
        first=manifest()
        self.publisher.publish(first)
        second=manifest(b"Symbol,Description\nAAPL,Apple\nMSFT,Microsoft\n",at="2026-09-09T02:00:00Z")
        self.publisher.publish(second)
        second_selection=read_collection(self.stores,first.universe_id)
        before=mutation_fingerprint(self.stores)
        self.publisher.publish(first)
        self.assertEqual(read_collection(self.stores,first.universe_id),second_selection)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        third=manifest(at="2026-09-09T03:00:00Z")
        self.assertEqual(self.publisher.publish(third).outcome,"succeeded")
        self.assertIsNone(read_collection(self.stores,first.universe_id,cutoff="2026-09-08T23:59:00Z"))
        self.assertEqual(read_collection(self.stores,first.universe_id,cutoff="2026-09-09T01:30:00Z").members,first.members)
        self.assertEqual(read_collection(self.stores,first.universe_id,cutoff="2026-09-09T02:00:00Z"),second_selection)
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM market_collection_snapshots").fetchone()[0],3)

    def test_changed_past_capture_and_forged_payload_fail_without_writes(self):
        self.publisher.publish(manifest())
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):
            self.publisher.publish(manifest(b"Symbol,Description\nAAPL,Changed\n"))
        with self.assertRaises(ValidationError):
            self.publisher.publish(replace(manifest(),membership_sha256="0"*64))
        with self.assertRaises(ValidationError):
            self.publisher.publish(replace(manifest(),name="Renamed"))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_csv_validation_rejects_ambiguous_or_incomplete_membership(self):
        for body in (b"",b"Symbol,Description\n",b"Symbol,Description\nAAPL,Apple\nAAPL,Other\n",
            b"Symbol,Description\naapl,Apple\n",b"Symbol,Description\n,Apple\n",
            b"Symbol,Description\nAAPL,Apple,extra\n",b"Symbol,Symbol,Description\nAAPL,AAPL,Apple\n",
            b'Symbol,Description\nAAPL,"unterminated\n',b"Symbol,Description\nAAPL,\n"):
            with self.subTest(body=body),self.assertRaises((ValidationError,ResourceLimitError)):
                manifest(body)
        with self.assertRaises(ResourceLimitError):
            manifest(("Symbol,Description\n"+"".join(f"S{i},Name\n" for i in range(5001))).encode())
        with self.assertRaises(ValidationError):
            manifest(at="2026-09-09")
        with self.assertRaises(ValidationError):
            parse_manifest(body=b"Symbol,Description\nAAPL,Apple\n",universe_id="x",name="x",
                source_reference="../outside.csv",captured_at=AT)

    def test_tables_reject_mutation_and_head_rewind(self):
        self.publisher.publish(manifest())
        with writer_connection(self.stores,"market") as c:
            for table in ("market_collection_universes","market_collection_artifacts","market_collection_snapshots","market_collection_members","market_collection_heads"):
                with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):
                    c.execute("DELETE FROM "+table)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE market_collection_members SET display_name='Changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE market_collection_heads SET snapshot_id=snapshot_id")

    def test_exact_registry_projection_generation_and_migration_replay(self):
        prior=collection_universe_registry_profile(self.registry)
        self.assertEqual(prior.registry_version,"2.75.0")
        self.assertEqual(prior.source_sha256,"7f5f2eb8f8a3470a7192a327b966a6d993332049c90564baa7a13379b30eadae")
        self.assertEqual(generated_bytes(ROOT)[0],(ROOT/"config/system_registry.json").read_bytes())
        before=mutation_fingerprint(self.stores)
        migrate_store(self.stores,self.registry,"market",applied_at=AT)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_operation_prepares_without_stores_and_import_replays(self):
        from quant_data.operations.collection_universe import prepare_selected,ready,import_selected,apply_schema
        parsed,report=prepare_selected(ROOT,captured_at=AT)
        self.assertEqual(report["selected_members"],2248)
        self.assertEqual(report["provider_requests"],0)
        self.assertEqual(report["canonical_writes"],0)
        self.assertTrue(ready(self.stores,self.registry))
        self.assertEqual(apply_schema(self.stores,self.registry,applied_at=AT)["outcome"],"ready")
        self.assertEqual(import_selected(self.stores,self.registry,parsed).outcome,"succeeded")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(import_selected(self.stores,self.registry,parsed).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_sealed_membership_rejects_replacement_and_late_rows(self):
        receipt=self.publisher.publish(manifest())
        with writer_connection(self.stores,"market") as c:
            for table in ("market_collection_universes","market_collection_snapshots","market_collection_members",
                          "market_collection_artifacts","market_collection_heads"):
                with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):
                    c.execute("INSERT OR REPLACE INTO "+table+" SELECT * FROM "+table+" LIMIT 1")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO market_collection_members VALUES (?,?,?,?,?)",
                    (receipt.snapshot_id,"MSFT",4,"Microsoft",'{"Symbol":"MSFT","Description":"Microsoft"}'))

    def test_schema_upgrade_requires_the_exact_predecessor(self):
        from quant_data.operations.collection_universe import apply_schema,ready
        from quant_data.registry import collection_universe_registry_profile
        prior=collection_universe_registry_profile(self.registry)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            stores=StoreMap.four_explicit(**{r:Path(directory)/(r+".sqlite") for r in ("market","macro","company","news")})
            initialize_all(stores,prior)
            with quiet_immutable_read_connection(stores,"market") as c:
                old=[tuple(r) for r in c.execute("SELECT * FROM schema_migrations ORDER BY ordinal")]
                self.assertEqual(len(old),11)
            with self.assertRaises(ValidationError): ready(stores,self.registry)
            self.assertEqual(apply_schema(stores,self.registry,applied_at=AT)["outcome"],"ready")
            self.assertTrue(ready(stores,self.registry))
            with quiet_immutable_read_connection(stores,"market") as c:
                self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM schema_migrations WHERE ordinal<12 ORDER BY ordinal")],old)
            before=mutation_fingerprint(stores)
            apply_schema(stores,self.registry,applied_at=AT)
            self.assertEqual(before,mutation_fingerprint(stores))
        # Explicit older fixture: preserve all earlier bytes and omit only the last ordinal.
        older=replace(prior,migrations=tuple(m for m in prior.migrations if m.id!="market:0011_option_raw_evidence"),
            stores=tuple(replace(s,migration_order=tuple(m for m in s.migration_order if m!="market:0011_option_raw_evidence")) if s.id=="market" else s for s in prior.stores))
        # Only migrate market here: older datasets intentionally are not registered.
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            stores=StoreMap.four_explicit(**{r:Path(directory)/(r+".sqlite") for r in ("market","macro","company","news")})
            migrate_store(stores,older,"market",applied_at=AT)
            with self.assertRaises(ValidationError): apply_schema(stores,self.registry,applied_at=AT)
            with quiet_immutable_read_connection(stores,"market") as c:
                self.assertEqual(c.execute("SELECT max(ordinal) FROM schema_migrations").fetchone()[0],10)
