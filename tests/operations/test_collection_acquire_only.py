import hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from quant_data.errors import ConflictError,ValidationError
from quant_data.operations.collection_plan import RetainedResponse
from quant_data.operations.collection_queue import QueueResponse,run_queue
from tests.operations import test_collection_work as f

class CollectionAcquireOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir="/tmp");self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"fmp";self.calls=0
    def fetch(self,**kw):
        self.calls+=1;return QueueResponse(200,b"[]",f.AT)
    def run_acquire(self,plan=None,**kw):
        return run_queue(root=self.root,plan=plan or f.plan(),provider="fmp",fetch=self.fetch,
            publish=None,acquire_only=True,utcnow=f.clock,sleeper=lambda _:None,**kw)
    def test_retained_acquisition_never_creates_publication_receipts_and_replays_without_get(self):
        result=self.run_acquire()
        self.assertEqual((result["outcome"],result["published"],result["acquired"]),("acquired",0,1))
        self.assertEqual(list((self.root/"publications").iterdir()),[])
        before=(self.root/"ledger.json").read_bytes()
        result=self.run_acquire()
        self.assertEqual(result["requests"],0);self.assertEqual(self.calls,1)
        self.assertEqual(before,(self.root/"ledger.json").read_bytes())
        published=[]
        run_queue(root=self.root,plan=f.plan(),provider="fmp",fetch=self.fetch,
            publish=lambda **kw: published.append(kw) or {"outcome":"succeeded"},utcnow=f.clock)
        self.assertEqual(len(published),1);self.assertEqual(self.calls,1)
    def test_canonical_retained_reuse_keeps_original_time_and_zero_charge(self):
        r=RetainedResponse(f.unit().request_id,"2026-09-08T01:00:00Z",hashlib.sha256(b"[]").hexdigest(),
            "fixture/original",True,"partial_history",True,True,"prior")
        result=self.run_acquire(f.plan(retained=(r,)),resolve_retained=lambda _:QueueResponse(200,b"[]",r.captured_at))
        self.assertEqual((result["requests"],result["published"],result["acquired"]),(0,0,1))
        state=json.loads((self.root/"ledger.json").read_text());self.assertEqual(state["usage"],{})
        receipt=json.loads(next((self.root/"responses").iterdir()).read_text())
        self.assertEqual(receipt["captured_at"],"2026-09-08T01:00:00.000000Z")
    def test_retimed_retained_response_and_publisher_in_acquisition_mode_reject(self):
        r=RetainedResponse(f.unit().request_id,"2026-09-08T01:00:00Z",hashlib.sha256(b"[]").hexdigest(),
            "fixture/original",True,"partial_history",True,True,"prior")
        with self.assertRaises(ConflictError):
            self.run_acquire(f.plan(retained=(r,)),resolve_retained=lambda _:QueueResponse(200,b"[]",f.AT))
        self.assertEqual(self.calls,0)
        with self.assertRaises(ValidationError):
            run_queue(root=self.root,plan=f.plan(),provider="fmp",fetch=self.fetch,
                publish=lambda **kw:{"outcome":"succeeded"},acquire_only=True)

    def test_substituted_other_subject_reference_is_rejected_before_resolution(self):
        r=RetainedResponse(f.unit().request_id,"2026-09-08T01:00:00Z",hashlib.sha256(b"[]").hexdigest(),
            "fixture/original",True,"partial_history",True,True,"prior")
        plan=f.plan(retained=(r,))
        for changed in (replace(r,request_id=f.unit("MSFT").request_id),
            replace(r,captured_at="2026-09-10T00:00:00Z"),replace(r,raw_verified=False),
            replace(r,transport_complete=False)):
            wrong=replace(plan,reuse=((f.unit().unit_id,changed),))
            resolved=[]
            with self.subTest(changed=changed),self.assertRaises(ConflictError):
                self.run_acquire(wrong,resolve_retained=lambda x:resolved.append(x) or QueueResponse(200,b"[]",x.captured_at))
            self.assertEqual(resolved,[])
        self.assertEqual(self.calls,0)

    def test_known_publication_without_original_bytes_cannot_be_reacquired(self):
        r=RetainedResponse(f.unit().request_id,"2026-09-08T01:00:00Z",hashlib.sha256(b"[]").hexdigest(),
            "fixture/original",True,"partial_history",True,True,"prior")
        result=run_queue(root=self.root,plan=f.plan(retained=(r,)),provider="fmp",fetch=self.fetch,
            publish=lambda **kw:{"outcome":"reused"},utcnow=f.clock)
        self.assertEqual(result["requests"],0)
        result=self.run_acquire()
        self.assertEqual(result["outcome"],"retained_evidence_required")
        self.assertEqual(self.calls,0)
