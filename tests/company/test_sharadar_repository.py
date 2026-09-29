"""Real temporary SQLite publication checks using explicitly synthetic SF1 evidence."""
from dataclasses import replace
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from quant_data.company.sharadar_repository import SharadarSf1Publisher,SharadarSf1Repository
from quant_data.company.sharadar_registry import EVIDENCE_TABLES,CANONICAL_TABLES
from quant_data.market.collection_universe import parse_manifest,CollectionManifestPublisher
from quant_data.market.collection_mappings import IdentityEvidence,unresolved_mapping,prepare_mapping,CollectionMappingPublisher
from quant_data.market.collection_bindings import load_bindings,pin_binding
from quant_data.registry import load_registry,sharadar_registry_profile
from quant_data.migrations import initialize_all,migrate_store
from quant_data.stores import StoreMap,quiet_immutable_read_connection,writer_connection
from quant_data.fingerprint import mutation_fingerprint
from quant_data.errors import ConflictError,ValidationError
from tests.company.test_sharadar_sf1 import AT,LATER,COLS,PARAMS,row,page,partition,metadata

ROOT=Path(__file__).resolve().parents[2]
T3="2026-09-09T03:00:00.000000Z"
class SharadarRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir="/tmp")
        self.stores=StoreMap.four_explicit(**{r:Path(self.tmp.name)/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        receipt=CollectionManifestPublisher(self.stores,self.registry).publish(parse_manifest(
            body=b"Symbol,Description\nAAPL,Apple\n",universe_id="major_index_liquid",name="Synthetic selected universe",
            source_reference="fixture/selection.csv",captured_at=AT))
        proof=IdentityEvidence(b'[{"ticker":"AAPL","permaticker":"199059"}]',"fixture/sharadar-tickers.json",AT,"sharadar")
        mapping=unresolved_mapping("AAPL")
        mapping.update(status="resolved",provider_symbol="AAPL",provider_subject="199059",
            evidence_sha256=proof.sha256,evidence_reference=proof.source_reference,evidence_pointer="/0",
            symbol_field="ticker",subject_field="permaticker",reason="Synthetic provider identity")
        prepared=prepare_mapping(membership_snapshot_id=receipt.snapshot_id,provider="sharadar",captured_at=AT,
            source_reference="fixture/mapping.json",body=json.dumps([mapping]).encode(),evidence=(proof,))
        CollectionMappingPublisher(self.stores,self.registry).publish(prepared)
        self.binding=load_bindings(ROOT/"config/collection_bindings.json")["sharadar_fundamentals"]
        self.selection=pin_binding(self.stores,self.binding,cutoff=AT)
        self.publisher=SharadarSf1Publisher(self.stores,self.registry)
        self.reader=SharadarSf1Repository(self.stores)
    def tearDown(self):self.tmp.cleanup()
    def publish(self,p,at=None,selection=None):
        return self.publisher.publish(p,selection=selection or self.selection,ingested_at=at or p.pages[-1].captured_at)
    def read(self,at=T3,mode="revision_history",**kwargs):
        return self.reader.rows(ticker="AAPL",dimensions=("ARQ","MRQ"),knowledge_cutoff=at,mode=mode,**kwargs)
    def counts(self):
        with quiet_immutable_read_connection(self.stores,"company") as c:
            return {t:c.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in (*EVIDENCE_TABLES,*CANONICAL_TABLES)}

    def test_full_evidence_ar_mr_and_zero_write_replay(self):
        initial=partition(page([row(),row(dimension="MRQ")]))
        result=self.publish(initial)
        self.assertEqual(result.outcome,"succeeded")
        self.assertEqual(self.reader.raw_artifact(initial.pages[0].content_sha256),initial.pages[0].body)
        self.assertEqual(self.reader.raw_artifact(initial.pages[0].schema.content_sha256),initial.pages[0].schema.metadata_body)
        self.assertEqual(len(self.read()["rows"]),2)
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(initial).outcome,"unchanged")
        reordered=partition(page([row(dimension="MRQ"),row()],columns=list(reversed(COLS)),captured_at=LATER))
        self.assertEqual(self.publish(reordered).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(c.execute("SELECT count(*) FROM company_sharadar_identity_assertions WHERE cik IS NULL").fetchone()[0],1)
        self.assertEqual(self.counts()["company_sharadar_sf1_versions"],2)

    def test_value_reversion_is_third_version_and_old_acquisition_stays_no_write(self):
        initial=partition(page([row(dimension="MRQ",revenue="100")]))
        self.publish(initial)
        self.publish(partition(page([row(dimension="MRQ",revenue="95")],captured_at=LATER)))
        self.publish(partition(page([row(dimension="MRQ",revenue="100")],captured_at=T3)))
        values=self.read()["rows"]
        self.assertEqual([r["values"]["revenue"] for r in values],["100","95","100"])
        self.assertEqual([r["version_sequence"] for r in values],[1,2,3])
        self.assertEqual(values[2]["predecessor_version_id"],values[1]["version_id"])
        self.assertEqual(values[1]["change_kind"],"observed_value_change")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(initial).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.assertEqual(self.read(at=AT,mode="local_capture_as_of")["rows"][0]["values"]["revenue"],"100")
        self.assertEqual(self.read(at=LATER,mode="local_capture_as_of")["rows"][0]["values"]["revenue"],"95")

    def test_cross_scope_memberships_reuse_equal_rows_and_later_change_is_not_hidden(self):
        self.publish(partition(page([row(revenue="100")],parameters={**PARAMS,"dimension":"ARQ"})))
        self.publish(partition(page([row(revenue="100")],captured_at=LATER)))
        self.assertEqual(self.counts()["company_sharadar_sf1_versions"],1)
        self.assertEqual(self.counts()["company_sharadar_sf1_membership"],2)
        self.publish(partition(page([row(revenue="95")],parameters={**PARAMS,"dimension":"ARQ"},captured_at=T3)))
        fourth="2026-09-09T04:00:00.000000Z"
        self.publish(partition(page([row(revenue="100")],captured_at=fourth)))
        self.assertEqual(self.counts()["company_sharadar_sf1_versions"],3)
        self.assertEqual(self.read(at=fourth,mode="local_capture_as_of")["rows"][0]["values"]["revenue"],"100")

    def test_incomplete_rejected_and_absence_never_deletes_observations(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):self.publish(partition(page(cursor="next")))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.publish(partition(page()))
        self.publish(partition(page([],captured_at=LATER)))
        self.assertEqual(len(self.read(mode="local_capture_as_of")["rows"]),1)
        self.assertEqual(self.counts()["company_sharadar_sf1_versions"],1)
        self.assertEqual(self.counts()["company_sharadar_captures"],2)

    def test_metadata_changes_survive_without_inventing_restatement_cause(self):
        self.publish(partition(page()))
        self.publish(partition(page([row(lastupdated="2026-09-09")],captured_at=LATER)))
        results=self.read()["rows"]
        self.assertEqual(results[1]["change_kind"],"metadata_change")
        self.assertEqual(results[0]["value_hash"],results[1]["value_hash"])
        self.assertNotEqual(results[0]["row_semantic_hash"],results[1]["row_semantic_hash"])
        self.assertIn("source_definitions_unresolved",self.read()["warnings"])

    def test_cutoffs_use_microseconds_and_provider_filing_date_completed_day(self):
        self.publish(partition(page()),at=LATER)
        earlier="2026-09-09T00:59:59.999999Z"
        self.assertEqual(self.read(at=earlier)["rows"],[])
        self.assertEqual(self.read(at=AT,ingested_cutoff=AT)["rows"],[])
        args=dict(ticker="AAPL",dimensions=("ARQ",),knowledge_cutoff=T3,mode="provider_as_reported")
        self.assertEqual(self.reader.rows(**args,filing_cutoff="2026-07-31T23:59:59Z")["rows"],[])
        self.assertEqual(len(self.reader.rows(**args,filing_cutoff="2026-08-01T00:00:00Z")["rows"]),1)
        with self.assertRaises(ValidationError):self.reader.rows(**{**args,"dimensions":("MRQ",)},filing_cutoff=T3)

    def test_forged_selection_changed_backdating_and_tampered_partition_fail(self):
        initial=partition(page())
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):self.publish(initial,selection=replace(self.selection,mapping_id="forged"))
        with self.assertRaises(ValidationError):self.publish(replace(initial,semantic_hash="0"*64))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.publish(initial)
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):self.publish(partition(page([row(revenue="5")])))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_immutable_tables_heads_and_migration_projection(self):
        self.publish(partition(page()))
        with writer_connection(self.stores,"company") as c:
            for table in (*EVIDENCE_TABLES,*CANONICAL_TABLES):
                with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):
                    c.execute("DELETE FROM "+table)
                with self.subTest(replace_table=table),self.assertRaises(sqlite3.IntegrityError):
                    c.execute("INSERT OR REPLACE INTO "+table+" SELECT * FROM "+table+" LIMIT 1")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE company_sharadar_sf1_heads SET version_id=version_id")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE company_sharadar_scope_heads SET capture_id=capture_id")
        self.assertEqual(sharadar_registry_profile(self.registry).registry_version,"2.76.0")
        before=mutation_fingerprint(self.stores)
        migrate_store(self.stores,self.registry,"company",applied_at=T3)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_changed_source_key_requires_reconciliation_without_new_observations(self):
        self.publish(partition(page()))
        before=mutation_fingerprint(self.stores)
        changed=metadata(keys=("ticker","dimension","datekey"))
        with self.assertRaisesRegex(ConflictError,"identity reconciliation"):
            self.publish(partition(page(schema=changed,captured_at=LATER)))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.assertEqual(len(self.read(mode="local_capture_as_of")["rows"]),1)

    def test_opaque_vendor_types_preserve_numeric_text_and_object_differences(self):
        cols=COLS+[("vendor_value","VendorSpecific")]
        schema=metadata(cols)
        for at,value in ((AT,123),(LATER,"123"),(T3,{"json_type":"number","value":"123"})):
            self.publish(partition(page([row(vendor_value=value)],columns=cols,schema=schema,captured_at=at)))
        records=self.read()["rows"]
        self.assertEqual(len(records),3)
        self.assertEqual([r["values"]["vendor_value"]["json_type"] for r in records],["number","string","object"])
        self.assertEqual(len({r["row_semantic_hash"] for r in records}),3)

    def test_schema_cannot_be_relabelled_as_an_unestablished_earlier_acquisition(self):
        self.publish(partition(page(schema=metadata(captured_at=T3),captured_at=T3)))
        before=mutation_fingerprint(self.stores)
        older=partition(page([row(reportperiod="2026-03-28",calendardate="2026-03-31",datekey="2026-04-30")],
            parameters={**PARAMS,"dimension":"ARQ"},schema=metadata(captured_at=AT),captured_at=LATER))
        with self.assertRaisesRegex(ConflictError,"schema evidence"):
            self.publish(older)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_later_universe_change_does_not_replace_the_runs_original_pin(self):
        CollectionManifestPublisher(self.stores,self.registry).publish(parse_manifest(
            body=b"Symbol,Description\nAAPL,Apple\nMSFT,Microsoft\n",universe_id="major_index_liquid",name="Synthetic selected universe",
            source_reference="fixture/expanded.csv",captured_at=LATER))
        original=self.selection
        result=self.publish(partition(page(captured_at=T3)),selection=original)
        self.assertEqual(result.outcome,"succeeded")
        with quiet_immutable_read_connection(self.stores,"company") as c:
            mapping=c.execute("SELECT membership_snapshot_id,mapping_id FROM company_sharadar_identity_assertions").fetchone()
        self.assertEqual(tuple(mapping),(original.membership_snapshot_id,original.mapping_id))
