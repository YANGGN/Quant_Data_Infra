from pathlib import Path
import unittest
from quant_data.operations.collection_inventory import inventory_units
from quant_data.operations.collection_plan import build_plan,ProviderBudget
from quant_data.operations.collection_sharadar import metadata_unit
from quant_data.operations.collection_sharadar_definitions import definition_unit
from quant_data.company.sharadar_definition_repository import SharadarDefinitionPublisher
from quant_data.fingerprint import mutation_fingerprint
from tests.operations import test_collection_sharadar as fixtures
from tests.company import test_sharadar_definitions as definitions
class SharadarInventoryTests(unittest.TestCase):
    def test_exact_pages_and_metadata_can_be_reused_without_writes_and_keep_cutoffs(self):
        f=fixtures.SelectedSharadarTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        f.run_worker()
        work=(metadata_unit(f.f.selection,mode="historical_backfill",observation_window="full-history"),*f.units)
        before=mutation_fingerprint(f.f.stores)
        inv=inventory_units(f.f.stores,units=work,cutoff=f.now.isoformat())
        self.assertEqual(len(inv.retained),3)
        self.assertTrue(all(r.raw_verified and r.published and r.coverage=="complete_request" for r in inv.retained))
        planned=build_plan(selections=(f.f.selection,),units=work,budgets=(ProviderBudget("sharadar",100,64*1024*1024,600,1000),),
            retained=inv.retained,created_at=f.now.isoformat())
        self.assertEqual(len(planned.reuse),2)
        early=inventory_units(f.f.stores,units=work,cutoff="2026-09-09T04:00:00Z")
        self.assertEqual(early.entries,())
        self.assertEqual(before,mutation_fingerprint(f.f.stores))
    def test_definitions_and_facts_never_share_request_identity(self):
        f=fixtures.SelectedSharadarTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        SharadarDefinitionPublisher(f.f.stores,f.f.registry).publish(definitions.snapshot(),ingested_at=definitions.AT)
        work=tuple(definition_unit(f.f.selection,mode="historical_backfill",observation_window="definitions",metadata=m) for m in (True,False))
        inv=inventory_units(f.f.stores,units=work,cutoff=f.now.isoformat())
        self.assertEqual(len(inv.retained),2)
        self.assertEqual(len({r.request_id for r in inv.retained}),2)
        # The fixture page omitted per_page, so it is not an exact request match
        # to the executable request that explicitly supplies 10000.
        planned=build_plan(selections=(f.f.selection,),units=work,budgets=(ProviderBudget("sharadar",2,32*1024*1024,600,1000),),
            retained=inv.retained,created_at=f.now.isoformat())
        self.assertEqual(len(planned.reuse),1)



class CollectionExactCaptureInventoryTests(unittest.TestCase):
    def test_large_offset_fmp_capture_is_known_and_cannot_trigger_reacquisition(self):
        import json
        from dataclasses import replace
        from tests.operations import test_collection_fmp as fmp
        from quant_data.operations.collection_fmp import SelectedFmpPublisher
        from quant_data.company.fmp_research import parse_research_response
        f=fmp.SelectedFmpTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        selection,work=f.work("fmp_statements");unit=work.units[0]
        publisher=SelectedFmpPublisher(stores=f.f.stores,registry=f.f.registry,selection=selection,
            units=(unit,),cutoff=fmp.CUT)
        prepared=parse_research_response(b"[]",endpoint=unit.endpoint,parameters=dict(unit.parameters),
            subject=publisher.subjects[unit.subject],captured_at="2026-09-10T04:29:00.000100+23:59",
            source_reference="fixture/original-missing.json",history_profile=True)
        publisher.research.publish(prepared,request_id="fixture-offset")
        inventory=inventory_units(f.f.stores,units=(unit,),cutoff="2026-09-09T05:00:00Z")
        self.assertEqual(len(inventory.retained),1)
        self.assertEqual(inventory.retained[0].captured_at,"2026-09-09T04:30:00.000100Z")
        before=inventory_units(f.f.stores,units=(unit,),cutoff="2026-09-09T04:30:00.000099Z")
        self.assertEqual(before.entries,())
        plan=build_plan(selections=(selection,),units=(unit,),retained=inventory.retained,
            budgets=(ProviderBudget("fmp",1,8*1024*1024,600,1000),),created_at=fmp.CUT)
        self.assertEqual(len(plan.blocked_units),1)

class SharadarInventoryScaleTests(unittest.TestCase):
    def test_unrelated_captures_and_shared_metadata_do_not_consume_subject_cap(self):
        import sqlite3, tempfile, json, hashlib, zlib
        from contextlib import contextmanager
        from dataclasses import replace
        from unittest.mock import patch
        from quant_data.errors import ResourceLimitError
        from quant_data.operations.collection_plan import AcquisitionUnit
        from quant_data.operations import collection_inventory as inventory
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            c=sqlite3.connect(str(Path(temporary)/"query-fixture.sqlite"))
            self.addCleanup(c.close);c.row_factory=sqlite3.Row
            c.executescript("""
                CREATE TABLE company_sharadar_captures(capture_id TEXT,row_count INTEGER,captured_at TEXT);
                CREATE TABLE company_sharadar_capture_artifacts(capture_id TEXT,ordinal INTEGER,
                    captured_at TEXT,content_sha256 TEXT,source_reference TEXT,parameters_json TEXT);
                CREATE TABLE company_sharadar_identity_assertions(capture_id TEXT,source_ticker TEXT,provider_subject TEXT);
                CREATE TABLE company_sharadar_artifacts(content_sha256 TEXT,raw_byte_count INTEGER,compressed_body BLOB);
            """)
            at="2026-09-09T04:00:00.000000Z";body=b"{}";sha=hashlib.sha256(body).hexdigest()
            c.execute("INSERT INTO company_sharadar_artifacts VALUES (?,?,?)",(sha,len(body),zlib.compress(body)))
            for i in range(2001):
                capture="capture-"+str(i)
                c.execute("INSERT INTO company_sharadar_captures VALUES (?,?,?)",(capture,1,at))
                c.execute("INSERT INTO company_sharadar_identity_assertions VALUES (?,?,?)",(capture,"MSFT","2"))
                for ordinal,params in ((-1,{}),(0,{"ticker":"MSFT","dimension":"ARQ"})):
                    c.execute("INSERT INTO company_sharadar_capture_artifacts VALUES (?,?,?,?,?,?)",
                        (capture,ordinal,at,sha,"fixture/shared.json",json.dumps(params)))
            @contextmanager
            def reader(*args):yield c
            unit=AcquisitionUnit("sharadar_fundamentals","sharadar","SHARADAR/SF1","1",
                (("dimension","ARQ"),("ticker","AAPL")),"historical_backfill","full-history",
                ("AAPL",),"1"*64,max_response_bytes=16*1024*1024,max_rows=10000,timeout_seconds=30)
            meta=replace(unit,endpoint="SHARADAR/SF1/metadata",subject="SHARADAR/SF1",parameters=(),selected_symbols=())
            with patch.object(inventory,"quiet_immutable_read_connection",reader):
                self.assertEqual(inventory_units(object(),units=(unit,),cutoff=at).entries,())
                shared=inventory_units(object(),units=(meta,),cutoff=at)
                self.assertEqual(len(shared.retained),1)
                self.assertTrue(shared.retained[0].raw_verified)
                c.execute("UPDATE company_sharadar_identity_assertions SET source_ticker='AAPL',provider_subject='1'")
                c.execute("UPDATE company_sharadar_capture_artifacts SET parameters_json=? WHERE ordinal=0",
                    (json.dumps({"ticker":"AAPL","dimension":"ARQ"}),))
                with self.assertRaisesRegex(ResourceLimitError,"per-subject"):
                    inventory_units(object(),units=(unit,),cutoff=at)
