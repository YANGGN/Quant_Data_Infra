import json,sqlite3,unittest
from pathlib import Path
from quant_data.errors import ConflictError,ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_mappings import CollectionMappingPublisher,IdentityEvidence,prepare_mapping,unresolved_mapping
from quant_data.market.collection_universe import CollectionManifestPublisher,parse_manifest
from quant_data.market.stage10_history_importer import _InstrumentPlan
from quant_data.registry import statement_history_registry_profile
from quant_data.stores import writer_connection,quiet_immutable_read_connection
from tests.market import test_collection_mappings as fixtures

class SelectedInstrumentCreationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CollectionMappingTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f
        receipt=CollectionManifestPublisher(f.stores,f.registry).publish(parse_manifest(
            body=b"Symbol,Description\nAAPL,Apple\nNVDA,Nvidia\n",universe_id="major_index_liquid",
            name="Major Index Liquid",source_reference="fixture/expanded.csv",captured_at=fixtures.LATER))
        self.membership=receipt.snapshot_id
        self.nvda=_InstrumentPlan("NVDA","equity","NVIDIA Corporation",None)
    def batch(self,flags=None):
        profiles=[{"symbol":"AAPL","cik":"0000320193","companyName":"Updated name","isEtf":False,"isFund":False},
            {"symbol":"NVDA","cik":"0001045810","companyName":"NVIDIA Corporation","isEtf":False,"isFund":False}]
        if flags is not None:profiles[1].update(flags)
        proof=IdentityEvidence(json.dumps(profiles).encode(),"fixture/fmp-profiles.json",fixtures.LATER)
        rows=[]
        for i,symbol in enumerate(("AAPL","NVDA")):
            r=unresolved_mapping(symbol)
            r.update(status="resolved",provider_symbol=symbol,provider_subject=symbol,cik=profiles[i]["cik"],
                instrument_id=self.f.instrument_ids["AAPL"] if symbol=="AAPL" else self.nvda.instrument_id,
                evidence_sha256=proof.sha256,evidence_reference=proof.source_reference,evidence_pointer="/"+str(i),
                symbol_field="symbol",subject_field="symbol",cik_field="cik",reason="Explicit FMP equity profile")
            rows.append(r)
        return prepare_mapping(membership_snapshot_id=self.membership,provider="fmp",
            captured_at="2026-09-09T03:00:00Z",source_reference="fixture/new-identity-map.json",
            body=json.dumps(rows).encode(),evidence=(proof,))
    def test_new_identity_and_mapping_publish_together_then_replay_without_writes(self):
        f=self.f;batch=self.batch()
        before=mutation_fingerprint(f.stores)
        with self.assertRaises(ConflictError):f.publisher.publish(batch)
        self.assertEqual(before,mutation_fingerprint(f.stores))
        self.assertEqual(f.publisher.publish(batch,create_missing_instruments=True).outcome,"succeeded")
        with quiet_immutable_read_connection(f.stores,"market") as c:
            row=c.execute("SELECT provider_symbol,asset_type,display_name FROM stage10_instruments WHERE instrument_id=?",
                (self.nvda.instrument_id,)).fetchone()
            self.assertEqual(tuple(row),("NVDA","equity","NVIDIA Corporation"))
            self.assertEqual(c.execute("SELECT display_name FROM stage10_instruments WHERE instrument_id=?",
                (f.instrument_ids["AAPL"],)).fetchone()[0],"Synthetic AAPL")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
        before=mutation_fingerprint(f.stores)
        self.assertEqual(f.publisher.publish(batch,create_missing_instruments=True).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(f.stores))
    def test_unproven_or_fund_classification_cannot_create_an_equity(self):
        for flags in ({"isEtf":True},{"isFund":True},{"isEtf":None}):
            before=mutation_fingerprint(self.f.stores)
            with self.subTest(flags=flags),self.assertRaises(ValidationError):
                self.f.publisher.publish(self.batch(flags),create_missing_instruments=True)
            self.assertEqual(before,mutation_fingerprint(self.f.stores))
    def test_mapping_failure_rolls_back_the_new_instrument_and_original_evidence(self):
        with writer_connection(self.f.stores,"market") as c:
            c.execute("CREATE TRIGGER fixture_fail_new_mapping BEFORE INSERT ON market_collection_mapping_snapshots BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        tables=("stage10_instruments","market_collection_identity_evidence","market_collection_artifacts",
            "market_collection_mapping_snapshots","market_collection_provider_mappings","market_collection_mapping_heads")
        def canonical_rows():
            with quiet_immutable_read_connection(self.f.stores,"market") as c:
                return {table:tuple(tuple(r) for r in c.execute("SELECT * FROM "+table+" ORDER BY 1")) for table in tables}
        before=canonical_rows()
        with self.assertRaises(sqlite3.IntegrityError):
            self.f.publisher.publish(self.batch(),create_missing_instruments=True)
        self.assertEqual(before,canonical_rows())
        # The existing coordinator deliberately retains a failed-attempt receipt.
        with quiet_immutable_read_connection(self.f.stores,"market") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_run_failures").fetchone()[0],1)
    def test_old_registry_cannot_enable_identity_creation(self):
        publisher=CollectionMappingPublisher(self.f.stores,statement_history_registry_profile(self.f.registry))
        before=mutation_fingerprint(self.f.stores)
        with self.assertRaises(ValidationError):publisher.publish(self.batch(),create_missing_instruments=True)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
