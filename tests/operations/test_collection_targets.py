from dataclasses import replace
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from quant_data.market.collection_bindings import load_bindings
from quant_data.operations import collection_targets as target_module
from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_pins import validate_pinned_collection
from quant_data.market.collection_bindings import pin_binding
from quant_data.market.collection_mappings import IdentityEvidence
from quant_data.market.collection_universe import CollectionManifestPublisher,parse_manifest
from quant_data.operations.collection_targets import selected_sec_issuers,selected_fmp_subjects,selected_market_rows,validate_selection
from quant_data.stores import writer_connection
from tests.market import test_collection_mappings as fixtures

AT=fixtures.AT
LATER=fixtures.LATER
T3="2026-09-09T03:00:00Z"
class CollectionTargetTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CollectionMappingTests("runTest")
        self.fixture.setUp()
        self.stores=self.fixture.stores
        config=json.loads((fixtures.ROOT/"config/collection_bindings.json").read_text())
        for binding in config["bindings"]:
            binding["mode"]="prepared"
        path=Path(self.fixture.temp.name)/"prepared-host-bindings.json"
        path.write_text(json.dumps(config))
        self.fixture.bindings=load_bindings(path)
        host=patch.object(target_module,"HOST_BINDINGS_PATH",path)
        host.start()
        self.addCleanup(host.stop)
        self.fixture.publisher.publish(self.fixture.batch())
    def tearDown(self):self.fixture.tearDown()

    def test_original_pin_survives_later_membership_and_rejects_forgery_or_future_evidence(self):
        scope=pin_binding(self.stores,self.fixture.bindings["fmp_statements"])
        CollectionManifestPublisher(self.stores,self.fixture.registry).publish(parse_manifest(
            body=b"Symbol,Description\nAAPL,Apple\n",universe_id="major_index_liquid",name="Major Index Liquid",
            source_reference="fixture/change.csv",captured_at=T3))
        before=mutation_fingerprint(self.stores)
        self.assertEqual(validate_pinned_collection(self.stores,scope,cutoff=T3),scope)
        with self.assertRaises(ConflictError):validate_pinned_collection(self.stores,scope,cutoff=AT)
        with self.assertRaises(ConflictError):validate_pinned_collection(self.stores,replace(scope,mapping_id="forged"),cutoff=T3)
        with self.assertRaises(ConflictError):validate_pinned_collection(self.stores,replace(scope,subjects=()),cutoff=T3)
        with self.assertRaises(ConflictError):validate_selection(self.stores,scope,cutoff=T3,collection="fmp_statements",require_active=True)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_sec_share_classes_are_one_issuer_with_complete_member_accounting(self):
        f=self.fixture
        raw=[{"symbol":"AAPL","cik":"0000320193"},{"symbol":"BRK-B","cik":"0000320193"}]
        proof=IdentityEvidence(json.dumps(raw).encode(),"fixture/sec-identities.json",AT,"sec")
        rows=json.loads(json.dumps(f.rows))
        for r in rows:
            r.update(cik="0000320193",provider_subject="0000320193",subject_field="cik",instrument_id=None,
                evidence_sha256=proof.sha256,evidence_reference=proof.source_reference)
        f.publisher.publish(f.batch(rows,evidence=(proof,),provider="sec"))
        scope=pin_binding(self.stores,f.bindings["sec_filings_companyfacts"])
        targets=selected_sec_issuers(self.stores,scope,cutoff=LATER)
        self.assertEqual(len(targets),1)
        self.assertEqual(targets[0].source_members,("AAPL","BRK.B"))
        self.assertEqual(targets[0].provider_symbols,("AAPL","BRK-B"))

    def test_missing_company_issuers_are_reported_without_inventing_subjects(self):
        scope=pin_binding(self.stores,self.fixture.bindings["fmp_statements"])
        before=mutation_fingerprint(self.stores)
        subjects,missing=selected_fmp_subjects(self.stores,scope,cutoff=LATER)
        self.assertEqual(subjects,())
        self.assertEqual(missing,("0000320193","0001067983"))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_retained_market_snapshots_are_fixed_and_unselected_equities_excluded(self):
        from quant_data.market.stage10_history_importer import _InstrumentPlan
        f=self.fixture
        with writer_connection(self.stores,"market") as c:
            c.execute("INSERT INTO stage10_scope_snapshots VALUES (?,?,?,?,?,?,?)",
                ("fixture_scope","0"*64,"stage10_fmp_market_history_v1","fmp","2026-09-09T01:00:00.000000Z","datetime",f.run_id))
            for universe,symbol,kind in (("curated_etfs","SPY","etf"),("major_indexes","^GSPC","index")):
                plan=_InstrumentPlan(symbol,kind,"Synthetic "+symbol,None)
                c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (plan.instrument_id,"fmp",symbol,kind,plan.display_name,None,"provider_native",None,plan.identity_seed_sha256,
                     "2026-09-09T01:00:00.000000Z","datetime",f.run_id))
                c.execute("INSERT INTO stage10_universes VALUES (?,?,?,?,?)",
                    (universe,"instrument_watchlist","Synthetic fixture","reviewed_local_manifest",f.run_id))
                c.execute("INSERT INTO stage10_universe_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (universe+"_v1",universe,"fixture_scope",None,"2026-09-09","2026-09-09T01:00:00.000000Z","datetime","complete",
                     "reviewed_instrument_watchlist",1,"1"*64,f.run_id))
                c.execute("INSERT INTO stage10_universe_snapshot_members VALUES (?,?,?)",(universe+"_v1",plan.instrument_id,1))
        scope=pin_binding(self.stores,f.bindings["daily_prices"])
        before=mutation_fingerprint(self.stores)
        rows=selected_market_rows(self.stores,scope,cutoff=LATER)
        self.assertEqual({r["provider_symbol"] for r in rows},{"AAPL","BRK-B","SPY","^GSPC"})
        self.assertNotIn("MSFT",{r["provider_symbol"] for r in rows})
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with self.assertRaises(ConflictError):
            validate_pinned_collection(self.stores,replace(scope,retained=scope.retained[:1]),cutoff=LATER)

    def test_changing_a_pin_mode_does_not_grant_host_activation(self):
        scope=pin_binding(self.stores,self.fixture.bindings["fmp_statements"])
        forged=replace(scope,binding=replace(scope.binding,mode="active"))
        before=mutation_fingerprint(self.stores)
        with self.assertRaisesRegex(ConflictError,"activated host binding"):
            validate_selection(self.stores,forged,cutoff=LATER,collection="fmp_statements",require_active=True)
        with self.assertRaisesRegex(ConflictError,"activated host binding"):
            selected_fmp_subjects(self.stores,forged,cutoff=LATER,require_active=True)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_only_the_fixed_host_configuration_can_activate_the_selection(self):
        from pathlib import Path
        from unittest.mock import patch
        from quant_data.operations import collection_targets as target_module
        from quant_data.market.collection_bindings import load_bindings
        config=json.loads((fixtures.ROOT/"config/collection_bindings.json").read_text())
        next(b for b in config["bindings"] if b["id"]=="fmp_statements")["mode"]="active"
        path=Path(self.fixture.temp.name)/"host-bindings.json"
        path.write_text(json.dumps(config))
        binding=load_bindings(path)["fmp_statements"]
        scope=pin_binding(self.stores,binding)
        # The private module constant represents the host in this explicit fixture.
        # Production callers have no argument that changes the configuration path.
        with patch.object(target_module,"HOST_BINDINGS_PATH",path):
            self.assertEqual(validate_selection(self.stores,scope,cutoff=LATER,
                collection="fmp_statements",require_active=True),scope)
        with self.assertRaises(ConflictError):
            validate_selection(self.stores,scope,cutoff=LATER,collection="fmp_statements",require_active=True)
