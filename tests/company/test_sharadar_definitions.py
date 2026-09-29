import json,tempfile,unittest,hashlib,sqlite3
from pathlib import Path
from dataclasses import replace
from quant_data.errors import ConflictError,ValidationError
from quant_data.company.sharadar_definitions import parse_definition_metadata,parse_definition_page,prepare_definition_snapshot
from quant_data.company.sharadar_definition_repository import SharadarDefinitionPublisher,SharadarDefinitionRepository
from quant_data.company.sharadar_repository import SharadarSf1Repository
from quant_data.company.sharadar_definition_registry import TABLES
from quant_data.registry import load_registry,sharadar_definitions_registry_profile
from quant_data.migrations import initialize_all,migrate_store
from quant_data.stores import StoreMap,quiet_immutable_read_connection,writer_connection
from quant_data.fingerprint import mutation_fingerprint
ROOT=Path(__file__).resolve().parents[2]
AT="2026-09-09T01:00:00.000000Z";B="2026-09-09T02:00:00.000000Z";C="2026-09-09T03:00:00.000000Z"
COLS=[{"name":n,"type":"String"} for n in ("table","indicator","title","description","unitstype","future_field")]
def metadata(**changes):
    table={"vendor_code":"SHARADAR","datatable_code":"INDICATORS","columns":COLS,"primary_key":["table","indicator"],"filters":["table"]}
    table.update(changes)
    return parse_definition_metadata(json.dumps({"datatable":table}).encode(),captured_at=AT,source_reference="fixture/indicators-metadata")
def page(description="Revenue first definition",at=AT,rows=None,cursor=None,params=None,schema=None):
    body=json.dumps({"datatable":{"columns":COLS,"data":rows if rows is not None else [
        ["SF1","revenue","Revenue",description,"currency","retained"]]},"meta":{"next_cursor_id":cursor}}).encode()
    return parse_definition_page(body,schema=schema or metadata(),parameters=params or {"table":"SF1"},
        captured_at=at,source_reference="fixture/indicators-page")
def snapshot(**kwargs):return prepare_definition_snapshot((page(**kwargs),))
class SharadarDefinitionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir="/tmp");self.addCleanup(tmp.cleanup)
        self.stores=StoreMap.four_explicit(**{r:Path(tmp.name)/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        self.publisher=SharadarDefinitionPublisher(self.stores,self.registry);self.reader=SharadarDefinitionRepository(self.stores)
    def publish(self,s):return self.publisher.publish(s,ingested_at=s.pages[-1].captured_at)
    def test_original_bytes_exact_replay_and_cutoff(self):
        first=snapshot();self.publish(first);before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish(first).outcome,"unchanged")
        self.assertEqual(self.publish(snapshot(at=B)).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.assertEqual(self.reader.rows(knowledge_cutoff="2026-09-09T00:00:00Z")["state"],"unresolved")
        read=self.reader.rows(knowledge_cutoff=AT)
        self.assertEqual(read["rows"][0]["values"]["future_field"],"retained")
        raw=SharadarSf1Repository(self.stores)
        for body in (first.pages[0].body,first.pages[0].schema.body):
            self.assertEqual(raw.raw_artifact(hashlib.sha256(body).hexdigest()),body)
    def test_restatement_reversion_and_absence_snapshot_do_not_delete_history(self):
        first=snapshot();self.publish(first)
        self.publish(snapshot(description="Revenue amended definition",at=B))
        self.publish(snapshot(at=C))
        history=self.reader.rows(knowledge_cutoff=C,mode="revision_history")["rows"]
        self.assertEqual([r["version_sequence"] for r in history],[1,2,3])
        self.assertEqual(history[2]["predecessor_version_id"],history[1]["version_id"])
        self.assertEqual(self.reader.rows(knowledge_cutoff=B)["rows"][0]["values"]["description"],"Revenue amended definition")
        self.publish(snapshot(at="2026-09-09T04:00:00Z",rows=[]))
        self.assertEqual(self.reader.rows(knowledge_cutoff="2026-09-09T05:00:00Z")["rows"],[])
        self.assertEqual(len(self.reader.rows(knowledge_cutoff="2026-09-09T05:00:00Z",mode="revision_history")["rows"]),3)
        before=mutation_fingerprint(self.stores);self.publish(first);self.assertEqual(before,mutation_fingerprint(self.stores))
    def test_incomplete_changed_backdated_and_malformed_metadata_reject(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError):prepare_definition_snapshot((page(cursor="next"),))
        with self.assertRaises(ValidationError):metadata(primary_key=[[]])
        with self.assertRaises(ValidationError):metadata(filters=[{}])
        with self.assertRaises(ValidationError):page(cursor="bad\n")
        with self.assertRaises(ConflictError):page(rows=[["OTHER","revenue","","","",""]])
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.publish(snapshot(at=B))
        with self.assertRaises(ConflictError):self.publish(snapshot(description="backdate",at=AT))
    def test_cursor_evidence_cannot_switch_schema_capture_or_page_size(self):
        first=page(cursor="next")
        with self.assertRaises(ConflictError):
            prepare_definition_snapshot((first,page(at=B,params={"table":"SF1","qopts.cursor_id":"next","qopts.per_page":"50"})))
        with self.assertRaises(ConflictError):
            prepare_definition_snapshot((first,replace(page(at=B,params={"table":"SF1","qopts.cursor_id":"next"}),schema=replace(metadata(),captured_at=B))))
    def test_forward_upgrade_preserves_migration_bytes_and_definition_guards(self):
        previous=sharadar_definitions_registry_profile(self.registry)
        self.assertEqual(previous.registry_version,"2.78.0")
        for m in self.registry.migrations:
            self.assertEqual(hashlib.sha256((ROOT/m.resource).read_bytes()).hexdigest(),m.sha256)
        self.publish(snapshot())
        with writer_connection(self.stores,"company") as c:
            for table in TABLES:
                with self.assertRaises(sqlite3.IntegrityError):c.execute("DELETE FROM "+table)
                c.rollback()
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])



    def test_regressing_ingestion_time_cannot_reuse_future_committed_versions(self):
        first=snapshot()
        self.publisher.publish(first,ingested_at="2026-09-09T05:00:00Z")
        # Revenue is unchanged, but a second indicator makes the snapshot change.
        new=snapshot(at=B,rows=[["SF1","revenue","Revenue","Revenue first definition","currency","retained"],
            ["SF1","assets","Assets","Assets definition","currency",None]])
        before=mutation_fingerprint(self.stores)
        with self.assertRaisesRegex(ConflictError,"ingestion time"):
            self.publisher.publish(new,ingested_at=C)
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.assertEqual(self.reader.rows(knowledge_cutoff=C,ingested_cutoff=C)["rows"],[])
