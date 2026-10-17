"""Offline FMP-only population, identity, cutoff, replay and migration invariants."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from quant_data.company.short_descriptions import DescriptionPublisher, prepare_record, selected_inputs
from quant_data.errors import ConflictError, ValidationError, ResourceLimitError, MigrationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.classifications import ClassificationPublisher, prepare_classifications, read_classifications, digest
from quant_data.market.classification_registry import DATASET, MIGRATION_ID, predecessor_profile
from quant_data.market.collection_mappings import IdentityEvidence
from quant_data.migrations import initialize_all, migrate_and_register_store, migrate_store
from quant_data.stores import StoreMap, acquire_write_session, quiet_immutable_read_connection, writer_connection
from tests.market import test_collection_mappings as mapping_fixture

ROOT=Path(__file__).resolve().parents[2]
AT="2026-09-22T12:00:00.000000Z"
LATER="2026-09-22T13:00:00.000000Z"
FINAL="2026-09-22T14:00:00.000000Z"


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        f=mapping_fixture.CollectionMappingTests("runTest"); f.setUp(); self.addCleanup(f.tearDown)
        profiles=json.loads(f.proof.body)
        for p,sector,industry in zip(profiles,("Technology","Financial Services"),("Consumer Electronics","Insurance - Diversified")):
            p.update(sector=sector,industry=industry,description="This company provides products and services to customers across the world.")
        f.proof=IdentityEvidence(json.dumps(profiles).encode(),f.proof.source_reference,f.proof.captured_at)
        for r in f.rows: r["evidence_sha256"]=f.proof.sha256
        f.publisher.publish(f.batch())
        self.fixture=f
        self.stores,self.registry=f.stores,f.registry
        with acquire_write_session(self.stores,("market",)):
            members=selected_inputs(self.stores,cutoff=AT)
        self.records=[prepare_record(m,m["source"]) for m in members]
        self.descriptions=DescriptionPublisher(self.stores,self.registry)
        self.descriptions.publish(self.records,published_at=AT)
        self.publisher=ClassificationPublisher(self.stores,self.registry)

    def publish(self,at=AT,**kw):
        return self.publisher.publish(published_at=at,expected_count=2,**kw)

    def revise(self,index,updates,at=LATER):
        old=self.records[index]
        source=deepcopy(old["source"])
        profiles=json.loads(source["raw_body"])
        profile=profiles[index]
        profile.update(updates)
        profile["description"] += " Updated at " + at
        source.update(raw_body=json.dumps(profiles),captured_at=at,origin="fmp_profile",source_evidence_id=None)
        record=prepare_record(old["member"],source)
        self.descriptions.publish([record],published_at=at)
        return record

    def test_complete_mapping_aliases_lineage_and_zero_write_replay(self):
        prepared=prepare_classifications(self.stores,as_of=AT,expected_count=2)
        with patch("socket.create_connection",side_effect=AssertionError("No network")), patch("quant_data.credentials.read_project_credential",side_effect=AssertionError("No credentials")):
            receipt=self.publish(expected_source_sha256=digest(prepared))
        self.assertEqual(receipt.written_count,2)
        rows=read_classifications(self.stores,["AAPL","BRK.B"],as_of=AT)
        self.assertEqual([(r["classification_provider"],r["sector"],r["industry"]) for r in rows],
            [("fmp","Technology","Consumer Electronics"),("fmp","Financial Services","Insurance - Diversified")])
        self.assertEqual(rows[1]["provider_symbol"],"BRK-B")
        self.assertEqual(rows[0]["source_evidence_id"],prepared[0]["source_evidence_id"])
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(LATER).written_count,0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_cutoff_missing_and_read_only(self):
        self.publish()
        before=mutation_fingerprint(self.stores)
        self.assertIsNone(read_classifications(self.stores,["AAPL"],as_of="2026-09-22T11:59:59Z")[0]["sector"])
        self.assertEqual(read_classifications(self.stores,["MSFT"],as_of=AT)[0]["missing_reason"],"not_established")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        for symbols in ([],["AAPL","AAPL"],[{}],["AAPL; SELECT 1"]):
            with self.assertRaises(ValidationError): read_classifications(self.stores,symbols,as_of=AT)

    def test_profile_missing_label_aborts_whole_population(self):
        self.revise(1,{"industry":None})
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError): self.publish(LATER)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM market_classification_versions").fetchone()[0],0)

    def test_pinned_source_change_and_wrong_population_fail_without_writes(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError): self.publish(expected_source_sha256="0"*64)
        with self.assertRaises(ConflictError): self.publisher.publish(published_at=AT,expected_count=3)
        with self.assertRaises(ResourceLimitError): self.publisher.publish(published_at=AT,expected_count=2249)
        with self.assertRaises(ConflictError): self.publish("2026-09-21T00:00:00Z")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_revision_return_to_original_and_historical_selection(self):
        self.publish()
        self.revise(0,{"industry":"Software - Infrastructure"})
        self.assertEqual(self.publish(LATER).written_count,1)
        self.assertEqual(read_classifications(self.stores,["AAPL"],as_of=AT)[0]["industry"],"Consumer Electronics")
        self.revise(0,{"industry":"Consumer Electronics"},at=FINAL)
        self.assertEqual(self.publish(FINAL).written_count,1)
        current=read_classifications(self.stores,["AAPL"],as_of=FINAL)[0]
        self.assertEqual(current["version_sequence"],3)
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(FINAL).written_count,0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_new_identical_profile_does_not_rewrite_classification(self):
        self.publish()
        self.revise(0,{})
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(LATER).written_count,0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_new_mapping_cik_does_not_relabel_original_profile_identity(self):
        self.publish()
        f=self.fixture
        changed=deepcopy(f.rows)
        proof=json.loads(f.proof.body)
        proof[0]["cik"]="0000000001"
        evidence=IdentityEvidence(json.dumps(proof).encode(),"collection/new-profile.json",LATER)
        for r in changed: r["evidence_sha256"]=evidence.sha256; r["evidence_reference"]=evidence.source_reference
        changed[0]["cik"]="0000000001"
        # Rebuild the reviewed alias association for the new evidence hash.
        changed[1]["association_sha256"]=None; changed[1]["association_pointer"]=None
        f.publisher.publish(f.batch(changed,at=LATER,evidence=(evidence,)))
        prepared=prepare_classifications(self.stores,as_of=LATER,expected_count=2)
        self.assertEqual(prepared[0]["source_cik"],"0000320193")
        self.assertEqual(prepared[0]["cik"],"0000000001")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(LATER).written_count,0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_corrupt_source_bytes_and_wrong_profile_symbol_are_rejected(self):
        with patch("quant_data.market.classifications.identity_source_object",return_value={"symbol":"WRONG"}):
            with self.assertRaises(ConflictError): self.publish()
        with writer_connection(self.stores,"company") as c:
            c.execute("DROP TRIGGER company_profile_evidence_immutable_update")
            c.execute("UPDATE company_profile_evidence SET raw_body=?",(b'[]',))
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError): self.publish()
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_database_constraints_preserve_fmp_only_and_immutable_rows(self):
        self.publish()
        with writer_connection(self.stores,"market") as c:
            for sql in ("UPDATE market_classification_versions SET sector='Other'", "DELETE FROM market_classification_versions",
                        "INSERT OR REPLACE INTO market_classification_versions SELECT * FROM market_classification_versions"):
                with self.assertRaises(sqlite3.IntegrityError): c.execute(sql)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_transaction_failure_rolls_back_facts_and_success_metadata(self):
        with writer_connection(self.stores,"market") as c:
            c.execute("""CREATE TRIGGER fixture_classification_failure BEFORE INSERT ON market_classification_versions
                WHEN NEW.source_symbol='BRK.B' BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with self.assertRaisesRegex(Exception,"injected failure"):
            self.publish()
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM market_classification_versions").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_runs WHERE dataset_id=?",(DATASET,)).fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_artifacts WHERE dataset_id=?",(DATASET,)).fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_snapshots WHERE dataset_id=?",(DATASET,)).fetchone()[0],0)

    def test_exact_predecessor_and_unchanged_generated_public_contracts(self):
        from quant_data.tool_platform.classification_registry import predecessor_profile as tool_predecessor
        current=tool_predecessor(self.registry)
        old=predecessor_profile(current)
        self.assertEqual(old.source_sha256,"61e06644d980ebf993ab44196c3794c457bba8cf6fe2b8fa582cc925081dc9ea")
        self.assertEqual(current.tools,old.tools)
        self.assertEqual(current.tool_version_policies,old.tool_version_policies)
        from quant_data.tool_platform.generate import generated_bytes,REGISTRY_RESOURCE,CATALOG_RESOURCE,VERSIONED_CATALOG_RESOURCE
        for actual,path in zip(generated_bytes(ROOT),(REGISTRY_RESOURCE,CATALOG_RESOURCE,VERSIONED_CATALOG_RESOURCE)):
            self.assertEqual(actual,(ROOT/path).read_bytes())

    def test_migration_preserves_prior_schema_ledger_and_rejects_tamper(self):
        old=predecessor_profile(self.registry)
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            stores=StoreMap.four_explicit(**{r:Path(folder)/(r+".sqlite") for r in ("market","macro","company","news")})
            initialize_all(stores,old)
            with quiet_immutable_read_connection(stores,"market") as c:
                ledger=[tuple(r) for r in c.execute("SELECT * FROM schema_migrations ORDER BY ordinal")]
                schema=[tuple(r) for r in c.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")]
            migrate_and_register_store(stores,self.registry,"market",applied_at=AT)
            with quiet_immutable_read_connection(stores,"market") as c:
                self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM schema_migrations ORDER BY ordinal")][:-1],ledger)
                for name,sql in schema: self.assertEqual(c.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0],sql)
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            before=mutation_fingerprint(stores)
            migrate_and_register_store(stores,self.registry,"market",applied_at=LATER)
            self.assertEqual(before,mutation_fingerprint(stores))
            bad=replace(self.registry,migrations=tuple(replace(m,sha256="0"*64) if m.id==MIGRATION_ID else m for m in self.registry.migrations))
            with self.assertRaises(MigrationError): migrate_store(stores,bad,"market",applied_at=LATER)
            self.assertEqual(before,mutation_fingerprint(stores))

if __name__=="__main__": unittest.main()
