import json,unittest
from dataclasses import replace
from quant_data.market.collection_identity_preparation import identity_locators,prepare_evidenced_mappings
from quant_data.market.collection_mappings import IdentityEvidence,CollectionMappingPublisher
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from tests.company import test_sharadar_repository as fixture

AT="2026-09-09T04:00:00Z"
class IdentityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SharadarRepositoryTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
    def prepare(self,e):
        return prepare_evidenced_mappings(self.f.stores,membership_snapshot_id=self.f.selection.membership_snapshot_id,
            provider=e.provider,evidence=(e,),locators=identity_locators(e),captured_at=AT,source_reference="fixture/prepared-mapping.json")
    def test_nasdaq_tickers_maps_original_array_pointer_and_retains_original_bytes(self):
        raw=json.dumps({"datatable":{"columns":[{"name":n,"type":t} for n,t in
            (("table","String"),("ticker","String"),("permaticker","Integer"),("name","String"))],
            "data":[["SF1","AAPL",199059,"Apple"]]},"meta":{"next_cursor_id":None}}).encode()
        e=IdentityEvidence(raw,"fixture/nasdaq-tickers.json",AT,"sharadar")
        batch=self.prepare(e)
        self.assertEqual(json.loads(batch.rows_json)[0]["evidence_pointer"],"/datatable/data/0")
        self.assertEqual(batch.evidence[0].body,raw)
        self.assertEqual(CollectionMappingPublisher(self.f.stores,self.f.registry).publish(batch).outcome,"succeeded")
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(CollectionMappingPublisher(self.f.stores,self.f.registry).publish(batch).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        with self.assertRaises(ValidationError):self.prepare(replace(e,body=raw.replace(b'"SF1"',b'"SF3"')))
    def test_fmp_profile_creates_existing_format_instrument_with_its_mapping(self):
        raw=b'[{"symbol":"AAPL","cik":"320193","companyName":"Apple","isEtf":false,"isFund":false}]'
        e=IdentityEvidence(raw,"fixture/profile.json",AT,"fmp")
        batch=self.prepare(e)
        result=CollectionMappingPublisher(self.f.stores,self.f.registry).publish(batch,create_missing_instruments=True)
        self.assertEqual(result.outcome,"succeeded")
        self.assertEqual(json.loads(batch.rows_json)[0]["cik"],"0000320193")
    def test_competing_identity_and_missing_equity_classification_remain_unresolved(self):
        for raw,status in ((b'[{"symbol":"AAPL","cik":"320193"},{"symbol":"AAPL","cik":"12345"}]',"ambiguous"),
            (b'[{"symbol":"AAPL","cik":"320193"}]',"unresolved")):
            batch=self.prepare(IdentityEvidence(raw,"fixture/profiles.json",AT,"fmp"))
            self.assertEqual(json.loads(batch.rows_json)[0]["status"],status)
            self.assertEqual(batch.evidence,())


    def test_future_evidence_cannot_influence_an_ambiguous_or_unsupported_mapping(self):
        from quant_data.errors import ConflictError
        for raw in (
            b'[{"symbol":"AAPL","cik":"320193"},{"symbol":"AAPL","cik":"12345"}]',
            b'[{"symbol":"AAPL","cik":"320193","isEtf":true,"isFund":false}]'):
            e=IdentityEvidence(raw,"fixture/future.json","2026-09-09T05:00:00Z","fmp")
            with self.assertRaisesRegex(ConflictError,"Future identity"):
                self.prepare(e)
