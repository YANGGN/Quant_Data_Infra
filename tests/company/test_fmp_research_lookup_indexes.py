from __future__ import annotations
import copy,hashlib,json,sqlite3,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from quant_data.company.fmp_research_lookup_registry import MIGRATION_ID,PREDECESSOR_SHA256,add_declarations
from quant_data.company.fmp_research import FmpResearchPublisher,parse_research_response,_exact_time_sql
from quant_data.company.fmp_market_data import FmpCompanySubject
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import RegistryError
from quant_data.fixtures import FixtureManifest
from quant_data.fingerprint import mutation_fingerprint
from quant_data.migrations import initialize_all,migrate_store
from quant_data.registry import load_registry,fmp_research_lookup_registry_profile,sharadar_direct_registry_profile
from quant_data.stores import StoreMap,quiet_immutable_read_connection,writer_connection,stable_id
from quant_data.tool_platform.generate import generated_bytes
ROOT=Path(__file__).resolve().parents[2]
INDEXES={'idx_company_fmp_research_rows_natural_capture':('natural_identity','captured_at'),
         'idx_company_fmp_research_rows_issuer':('issuer_id',)}
class FmpResearchLookupIndexTests(unittest.TestCase):
    def setUp(self):
        self.current=sharadar_direct_registry_profile(load_registry(ROOT/'config/system_registry.json',project_root=ROOT,environment={}))
        self.prior=fmp_research_lookup_registry_profile(self.current)
    def test_exact_predecessor_and_no_catalog_change(self):
        self.assertEqual(self.prior.registry_version,'2.80.0');self.assertEqual(self.prior.source_sha256,PREDECESSOR_SHA256)
        for section in ('datasets','collectors','tools','dashboard','presentation_order','jobs'):
            self.assertEqual(self.current.raw[section],self.prior.raw[section])
        for migration in self.prior.migrations:
            self.assertEqual(next(m for m in self.current.migrations if m.id==migration.id),migration)
            self.assertEqual(hashlib.sha256((ROOT/migration.resource).read_bytes()).hexdigest(),migration.sha256)
        raw=copy.deepcopy(self.prior.raw);add_declarations(raw,ROOT);self.assertEqual(raw,self.current.raw)
        self.assertEqual(generated_bytes(ROOT),tuple((ROOT/name).read_bytes() for name in (
            'config/system_registry.json','quant_data/generated/tool_contract_schemas_v1.json','quant_data/generated/tool_contract_schemas_v2.json')))
    def test_projection_rejects_raw_and_parsed_drift(self):
        raw=copy.deepcopy(self.current.raw);next(m for m in raw['migrations'] if m['id']==MIGRATION_ID)['semantic_scope']+=' drift'
        with self.assertRaises(RegistryError):fmp_research_lookup_registry_profile(replace(self.current,raw=raw))
        migrations=tuple(replace(m,ordinal=99) if m.id==MIGRATION_ID else m for m in self.current.migrations)
        with self.assertRaises(RegistryError):fmp_research_lookup_registry_profile(replace(self.current,migrations=migrations))
        raw=copy.deepcopy(self.prior.raw);raw['collectors'][0]['handler']+='_drift'
        with self.assertRaises(ValueError):add_declarations(raw,ROOT)
    def test_populated_migration_preserves_facts_guards_and_replay(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            stores=StoreMap.four_explicit(**{r:Path(temporary)/(r+'.sqlite') for r in ('market','macro','company','news')})
            initialize_all(stores,self.prior)
            fixture=FixtureManifest.load(ROOT/'tests/fixtures/manifest.json',project_root=ROOT)
            CompanyStage4FixtureImporter(stores,fixture).import_fixture('sec_initial')
            cik='0001000001';subject=FmpCompanySubject(issuer_id=stable_id('company_issuer',cik),cik=cik,
                symbol='NST',instrument_id='synthetic-stage10-nst',identity_evidence_sha256='1'*64)
            def response(revenue,captured):
                body=json.dumps([{'symbol':'NST','date':'2025-12-31','calendarYear':'2025','period':'FY','revenue':revenue}]).encode()
                return parse_research_response(body,endpoint='income-statement',parameters={'symbol':'NST','period':'annual','limit':'3'},
                    subject=subject,captured_at=captured,source_reference='fixture/fmp-research.json')
            original=response(10,'2026-09-05T12:00:00Z')
            self.assertEqual(FmpResearchPublisher(stores,self.prior).publish(original,request_id='before').outcome,'succeeded')
            def preserved():
                with quiet_immutable_read_connection(stores,'company') as con:
                    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' ORDER BY name")]
                    rows={t:sorted((tuple(r) for r in con.execute('SELECT * FROM '+t)),key=repr) for t in tables}
                    triggers=[tuple(r) for r in con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
                    ledger=[tuple(r) for r in con.execute('SELECT * FROM schema_migrations ORDER BY ordinal')]
                return rows,triggers,ledger
            before_rows,before_triggers,before_ledger=preserved()
            result=migrate_store(stores,self.current,'company',applied_at='2026-09-11T13:00:00Z')
            after_rows,after_triggers,after_ledger=preserved()
            self.assertEqual(after_rows,before_rows);self.assertEqual(after_triggers,before_triggers);self.assertEqual(after_ledger[:-1],before_ledger)
            with quiet_immutable_read_connection(stores,'company') as con:
                for index,columns in INDEXES.items():
                    details=next(r for r in con.execute('PRAGMA index_list(company_fmp_research_rows)') if r[1]==index)
                    self.assertEqual(details[2],0)
                    self.assertEqual(tuple(r[2] for r in con.execute('PRAGMA index_info('+index+')')),columns)
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
            before=mutation_fingerprint(stores)
            self.assertEqual(migrate_store(stores,self.current,'company',applied_at='2026-09-11T13:00:00Z'),result)
            publisher=FmpResearchPublisher(stores,self.current)
            self.assertEqual(publisher.publish(original,request_id='replay').outcome,'unchanged')
            self.assertEqual(mutation_fingerprint(stores),before)
            corrected=response(11,'2026-09-06T12:00:00Z')
            self.assertEqual(publisher.publish(corrected,request_id='correction').outcome,'succeeded')
            before=mutation_fingerprint(stores)
            self.assertEqual(publisher.publish(corrected,request_id='corrected-replay').outcome,'unchanged')
            self.assertEqual(mutation_fingerprint(stores),before)
            with writer_connection(stores,'company') as con:
                with self.assertRaises(sqlite3.IntegrityError):con.execute('DELETE FROM company_fmp_research_rows')
                with self.assertRaises(sqlite3.IntegrityError):con.execute("UPDATE company_fmp_research_rows SET run_id='missing'")
    def test_prior_guard_and_issuer_predicates_use_lookup_indexes(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            stores=StoreMap.four_explicit(**{r:Path(temporary)/(r+'.sqlite') for r in ('market','macro','company','news')})
            initialize_all(stores,self.current)
            with quiet_immutable_read_connection(stores,'company') as con:
                _exact_time_sql(con)
                for query,values,index in (
                    ('SELECT research_row_id,captured_at FROM company_fmp_research_rows WHERE natural_identity=? ORDER BY fmp_capture_key(captured_at) DESC,research_row_id DESC LIMIT 1',('x',),'idx_company_fmp_research_rows_natural_capture'),
                    ('SELECT 1 FROM company_fmp_research_rows WHERE natural_identity=? AND fmp_capture_key(captured_at)>=fmp_capture_key(?)',('x','2026-09-11T13:00:00Z'),'idx_company_fmp_research_rows_natural_capture'),
                    ('SELECT r.natural_identity,r.semantic_hash,s.semantic_identity FROM company_fmp_research_rows r JOIN company_fmp_research_snapshots s ON s.snapshot_id=r.snapshot_id WHERE r.issuer_id=?',('x',),'idx_company_fmp_research_rows_issuer')):
                    plan=' '.join(r[3] for r in con.execute('EXPLAIN QUERY PLAN '+query,values))
                    self.assertIn(index,plan);self.assertNotIn('SCAN company_fmp_research_rows',plan);self.assertNotIn('SCAN r',plan)
