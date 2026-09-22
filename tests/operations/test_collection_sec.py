import json,unittest
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from quant_data.errors import ConflictError,ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_bindings import pin_binding
from quant_data.market.collection_mappings import IdentityEvidence
from quant_data.operations.collection_plan import ProviderBudget,build_plan
from quant_data.operations.collection_queue import QueueResponse,run_queue
from quant_data.operations.collection_sec import sec_units,publish_sec_pairs,submissions_history_files
from quant_data.stores import quiet_immutable_read_connection
from tests.market import test_collection_mappings as mappings
from tests.company import test_sec_companyfacts as payloads

CUT="2026-09-09T04:00:00Z"
class SelectedSecTests(unittest.TestCase):
    def setUp(self):
        self.f=mappings.CollectionMappingTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f;f.publisher.publish(f.batch())
        proof=IdentityEvidence(json.dumps([{"symbol":"AAPL","cik":"0000320193"},
            {"symbol":"BRK-B","cik":"0000320193"}]).encode(),"fixture/sec.json",mappings.AT,"sec")
        rows=json.loads(json.dumps(f.rows))
        for r in rows:r.update(cik="0000320193",provider_subject="0000320193",subject_field="cik",
            instrument_id=None,evidence_sha256=proof.sha256,evidence_reference=proof.source_reference)
        f.publisher.publish(f.batch(rows,evidence=(proof,),provider="sec"))
        self.selection=pin_binding(f.stores,f.bindings["sec_filings_companyfacts"])
        self.units=sec_units(f.stores,self.selection,mode="historical_backfill",observation_window="initial",cutoff=CUT)
        self.root=Path(f.temp.name)/"sec"
        self.calls=[]
    def plan(self,requests=10):
        return build_plan(selections=(self.selection,),units=self.units,
            budgets=(ProviderBudget("sec",requests,128*1024*1024,600,200),),created_at=CUT)
    def fetch(self,**kw):
        e=kw["unit"].endpoint;self.calls.append(e)
        body=payloads._submissions_body() if e=="submissions" else payloads._companyfacts_body()
        at="2026-09-09T03:00:00Z" if e=="submissions" else "2026-09-09T03:00:01Z"
        return QueueResponse(200,body,at)
    def acquire(self,requests=10):
        return run_queue(root=self.root,plan=self.plan(requests),provider="sec",fetch=self.fetch,
            publish=None,acquire_only=True,utcnow=lambda:datetime(2026,9,9,3,tzinfo=timezone.utc),sleeper=lambda _:None)
    def publish(self):
        return publish_sec_pairs(root=self.root,stores=self.f.stores,registry=self.f.registry,
            selection=self.selection,units=self.units,cutoff=CUT)
    def test_shared_cik_two_requests_complete_pair_atomic_and_replay(self):
        self.assertEqual(len(self.units),2)
        self.assertTrue(all(u.selected_symbols==("AAPL","BRK.B") for u in self.units))
        self.acquire()
        result=self.publish();self.assertEqual(len(result),1)
        with quiet_immutable_read_connection(self.f.stores,"company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_issuers").fetchone()[0],1)
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.publish()[0]["outcome"],"reused")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.acquire();self.assertEqual(len(self.calls),2)
        manifest=json.loads(next((self.root/"sec-bundles").iterdir()).read_text())
        self.assertEqual(len({r["captured_at"] for r in manifest["responses"]}),2)
        self.assertEqual(manifest["bundle_available_at"],"2026-09-09T03:00:01.000000Z")
    def test_partial_pair_is_retained_without_company_write_then_resumed(self):
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.acquire(requests=1)["outcome"],"invocation_budget")
        self.assertEqual(self.publish()[0]["outcome"],"incomplete_pair")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.acquire(requests=1);self.publish()
        self.assertEqual(len(self.calls),2)
    def test_history_file_validation_preserves_exact_issuer_and_never_infers_documents(self):
        body=json.loads(payloads._submissions_body())
        body["filings"]["files"]=[{"name":"CIK0000320193-submissions-001.json"}]
        self.assertEqual(submissions_history_files(json.dumps(body).encode(),cik="0000320193"),
            ("CIK0000320193-submissions-001.json",))
        body["filings"]["files"][0]["name"]="CIK0001067983-submissions-001.json"
        with self.assertRaises(ValidationError):
            submissions_history_files(json.dumps(body).encode(),cik="0000320193")
