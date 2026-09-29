from dataclasses import replace
import hashlib,json,unittest
from pathlib import Path
from quant_data.errors import ValidationError,ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_bindings import pin_binding
from quant_data.market.collection_mappings import IdentityEvidence,prepare_mapping,unresolved_mapping
from quant_data.market.collection_universe import CollectionManifestPublisher,parse_manifest
from quant_data.market.stage10_history_importer import _InstrumentPlan
from quant_data.market.stage12_incremental import Stage12BIncrementalCollector
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.operations.collection_prices import PriceWindow,price_units,SelectedPricePublisher
from quant_data.stores import writer_connection
from tests.market import test_collection_mappings as fixtures

ROOT=fixtures.ROOT
CUT="2026-09-09T03:00:00Z"
class SelectedPriceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CollectionMappingTests("runTest");self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.publisher.publish(self.f.batch())
        with writer_connection(self.f.stores,"market") as c:
            c.execute("INSERT INTO stage10_scope_snapshots VALUES (?,?,?,?,?,?,?)",
                ("price_fixture_scope","0"*64,"stage10_fmp_market_history_v1","fmp",
                 "2026-09-09T01:00:00.000000Z","datetime",self.f.run_id))
            for universe,symbol,kind in (("curated_etfs","SPY","etf"),("major_indexes","^GSPC","index")):
                p=_InstrumentPlan(symbol,kind,symbol,None)
                c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p.instrument_id,"fmp",symbol,kind,symbol,None,"provider_native",None,p.identity_seed_sha256,
                     "2026-09-09T01:00:00.000000Z","datetime",self.f.run_id))
                c.execute("INSERT INTO stage10_universes VALUES (?,?,?,?,?)",
                    (universe,"instrument_watchlist","Fixture","reviewed_local_manifest",self.f.run_id))
                c.execute("INSERT INTO stage10_universe_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (universe+"_price",universe,"price_fixture_scope",None,"2026-09-09",
                     "2026-09-09T01:00:00.000000Z","datetime","complete","reviewed_instrument_watchlist",1,"1"*64,self.f.run_id))
                c.execute("INSERT INTO stage10_universe_snapshot_members VALUES (?,?,?)",
                    (universe+"_price",p.instrument_id,1))
        self.scope=pin_binding(self.f.stores,self.f.bindings["daily_prices"])
        self.collector=Stage12BIncrementalCollector(fixture_root=self.f.temp.name,market_store=self.f.stores.market,
            scope=load_stage12b_incremental_market_v1_scope(ROOT/"config/stage12b_incremental_market_v1_scope.json"),
            stage12a_scope=load_stage12_market_v1_scope(ROOT/"config/stage12_market_v1_scope.json"),
            stage12a_scope_source=ROOT/"config/stage12_market_v1_scope.json")
        self.window=PriceWindow("AAPL","2026-09-08","2026-09-08",("2026-09-08",))
        row=json.loads((ROOT/"tests/fixtures/stage12b/daily_price_complete.json").read_text())[0]
        row.update(symbol="AAPL",date="2026-09-08")
        self.body=json.dumps([row]).encode()

    def requests(self,scope=None,windows=None):
        return price_units(self.f.stores,scope or self.scope,windows=windows or (self.window,),
            mode="historical_backfill",observation_window="selected-price-backfill",cutoff=CUT)

    def publish(self,scope=None):
        requests=self.requests(scope)
        u=requests[0][0]
        publisher=SelectedPricePublisher(stores=self.f.stores,selection=scope or self.scope,cutoff=CUT,
            collector=self.collector,requests=requests)
        receipt={"request_id":u.request_id,"unit_id":u.unit_id,"status":200,
            "content_sha256":hashlib.sha256(self.body).hexdigest(),"captured_at":CUT}
        return publisher(unit=u,retained=None,receipt=receipt,body=self.body)

    def test_real_publication_replay_survives_membership_change(self):
        self.assertEqual(self.publish()["written_versions"],1)
        f=self.f
        m=CollectionManifestPublisher(f.stores,f.registry).publish(parse_manifest(
            body=b"Symbol,Description\nAAPL,Apple\n",universe_id="major_index_liquid",name="Major Index Liquid",
            source_reference="fixture/new.csv",captured_at="2026-09-09T02:30:00Z"))
        r=json.loads(json.dumps(f.rows[:1]))
        f.publisher.publish(prepare_mapping(membership_snapshot_id=m.snapshot_id,provider="fmp",
            captured_at="2026-09-09T02:40:00Z",source_reference="fixture/new-mapping.json",
            body=json.dumps(r).encode(),evidence=(f.proof,)))
        latest=pin_binding(f.stores,f.bindings["daily_prices"])
        self.assertNotEqual(latest.scope_sha256,self.scope.scope_sha256)
        before=mutation_fingerprint(f.stores)
        self.assertEqual(self.publish(latest)["outcome"],"unchanged")
        self.assertEqual(before,mutation_fingerprint(f.stores))

    def test_retained_scopes_have_units_and_unselected_equity_does_not(self):
        windows=tuple(replace(self.window,symbol=s) for s in ("AAPL","BRK-B","SPY","^GSPC"))
        requests=self.requests(windows=windows)
        by={u.subject:u for u,w in requests}
        self.assertEqual(set(by),{"AAPL","BRK-B","SPY","^GSPC"})
        self.assertEqual(by["SPY"].selected_symbols,())
        self.assertEqual(by["BRK-B"].selected_symbols,("BRK.B",))
        with self.assertRaises(ValidationError):self.requests(windows=(replace(self.window,symbol="MSFT"),))

    def test_conflicting_calendars_and_future_windows_reject(self):
        with self.assertRaises(ValidationError):
            self.requests(windows=(replace(self.window,start="2026-08-01"),))
        with self.assertRaises(ValidationError):
            self.requests(windows=(PriceWindow("AAPL","2026-09-10","2026-09-10",("2026-09-10",)),))
        with self.assertRaises(ConflictError):
            self.requests(windows=(PriceWindow("AAPL","2026-09-07","2026-09-08",("2026-09-08",)),
                PriceWindow("AAPL","2026-09-07","2026-09-08",("2026-09-07","2026-09-08"))))

    def test_more_than_800_selected_equities_with_retained_etf_and_index(self):
        f=self.f
        names=["AAPL"]+[f"X{i:04d}" for i in range(900)]
        ids={"AAPL":f.instrument_ids["AAPL"]}
        with writer_connection(f.stores,"market") as c:
            for symbol in names[1:]:
                p=_InstrumentPlan(symbol,"equity",symbol,None);ids[symbol]=p.instrument_id
                c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p.instrument_id,"fmp",symbol,"equity",symbol,None,"provider_native",None,p.identity_seed_sha256,
                     "2026-09-09T01:00:00.000000Z","datetime",f.run_id))
        manifest=CollectionManifestPublisher(f.stores,f.registry).publish(parse_manifest(
            body=("Symbol,Description\n"+"".join(s+","+s+"\n" for s in names)).encode(),
            universe_id="major_index_liquid",name="Major Index Liquid",source_reference="fixture/large.csv",
            captured_at="2026-09-09T02:30:00Z"))
        evidence=IdentityEvidence(json.dumps([{"symbol":s,"cik":"0000320193"} for s in names]).encode(),
            "fixture/large-identities.json","2026-09-09T02:30:00Z")
        rows=[]
        for i,s in enumerate(names):
            r=unresolved_mapping(s)
            r.update(status="resolved",provider_symbol=s,provider_subject=s,cik="0000320193",instrument_id=ids[s],
                evidence_sha256=evidence.sha256,evidence_reference=evidence.source_reference,evidence_pointer="/"+str(i),
                symbol_field="symbol",subject_field="symbol",cik_field="cik",reason="Synthetic security")
            rows.append(r)
        f.publisher.publish(prepare_mapping(membership_snapshot_id=manifest.snapshot_id,provider="fmp",
            captured_at="2026-09-09T02:40:00Z",source_reference="fixture/large-mapping.json",
            body=json.dumps(rows).encode(),evidence=(evidence,)))
        scope=pin_binding(f.stores,f.bindings["daily_prices"])
        requests=self.requests(scope,tuple(replace(self.window,symbol=s) for s in names+["SPY","^GSPC"]))
        self.assertEqual(len(requests),903)
        self.collector._bind_selected_prices(stores=f.stores,selection=scope,cutoff=CUT)
        self.assertEqual(len(self.collector._scheduled_instruments),903)
        with self.assertRaises(ValidationError):
            Stage12BIncrementalCollector._validated_scheduled_instruments(self.collector._scheduled_instruments)

    def test_retained_unpublished_price_cannot_predate_its_identity_evidence(self):
        from quant_data.operations.collection_plan import RetainedResponse
        requests=self.requests();u=requests[0][0]
        p=SelectedPricePublisher(stores=self.f.stores,selection=self.scope,cutoff=CUT,
            collector=self.collector,requests=requests,resolve_retained=lambda _:self.body)
        retained=RetainedResponse(u.request_id,"2026-09-09T00:30:00Z",
            hashlib.sha256(self.body).hexdigest(),"fixture/older-price.json",True,"complete_request",True,False,"old")
        before=mutation_fingerprint(self.f.stores)
        with self.assertRaises(ConflictError):
            p(unit=u,retained=retained,receipt=None,body=None)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))

    def test_rebinding_shared_collector_invalidates_the_old_publisher_callback(self):
        requests=self.requests();u=requests[0][0]
        old=SelectedPricePublisher(stores=self.f.stores,selection=self.scope,cutoff=CUT,
            collector=self.collector,requests=requests)
        new=SelectedPricePublisher(stores=self.f.stores,selection=self.scope,cutoff=CUT,
            collector=self.collector,requests=requests)
        receipt={"request_id":u.request_id,"unit_id":u.unit_id,"status":200,
            "content_sha256":hashlib.sha256(self.body).hexdigest(),"captured_at":CUT}
        before=mutation_fingerprint(self.f.stores)
        with self.assertRaisesRegex(ConflictError,"rebound"):
            old(unit=u,retained=None,receipt=receipt,body=self.body)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.assertEqual(new(unit=u,retained=None,receipt=receipt,body=self.body)["written_versions"],1)
