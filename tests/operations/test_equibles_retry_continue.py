"""Retry/continuation uses isolated checkpoints, fake HTTP, and fixed quotas."""
import copy
import json
import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations import equibles_parallel_backfill as parallel
from quant_data.operations import equibles_failure_policy as policy
from quant_data.operations import equibles_daily_backfill as daily
from tests.company import test_equibles_transcripts as raw
from tests.operations import test_equibles_paid_policy as paid


class Scripted:
    def __init__(self, scripts=None, clock=lambda: paid.AT):
        self.scripts = {p:list(v) for p,v in (scripts or {}).items()}
        self.paths = []
        self.lock = threading.Lock()
        self.clock = clock
    def request(self, path):
        with self.lock:
            self.paths.append(path)
            script = self.scripts.get(path)
            value = script.pop(0) if script else None
            number = len(self.paths)
        if isinstance(value, Exception):
            raise value
        if value is None:
            value = raw.catalog("A", (1,2,3,4)) if "investor-events" in path else raw.body("A", int(path.split("/")[6]))
        if isinstance(value, tuple):
            status, value = value
        else:
            status = 200
        at = self.clock().replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        return status, {"content-type":"application/json","x-ratelimit-limit":"10000",
                        "x-ratelimit-remaining":str(10000-number),
                        "x-ratelimit-reset":str(int(at.timestamp()))}, value


class RetryContinueTests(unittest.TestCase):
    def setUp(self):
        self.f = paid.EquiblesPaidPolicyTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.convert(paid.selection(("A",)))
        self.root = self.f.root
        self.publisher = raw.FakePublisher()
        state = self.f.state()
        state["download_failure_policy"] = dict(policy.POLICY)
        events = [raw.event("A", q) for q in (1,2,3,4)]
        state["current"] = {"catalog":events,"catalog_done":True,"event_index":0,
                            "events":events,"pages":[],"offset":0,"captured":0}
        bucket = paid.quota_bucket(state, paid.AT.date().isoformat())
        bucket.update(paid_headers_verified=True,remaining=10000)
        self.f.save(state)
        spacing = patch.object(parallel, "SPACING_SECONDS", 0.001)
        spacing.start()
        self.addCleanup(spacing.stop)
        self.path = parallel.planned_paths(state)[0]

    def batch(self, transport, count=1):
        return parallel.acquire_batch(self.root, transport, max_requests=count,
            max_bytes=32*1024*1024,deadline=time.monotonic()+120,
            day=paid.AT.date().isoformat(),clock=lambda:paid.AT)

    def wave(self, transport, **kwargs):
        return parallel.run_wave(self.root,self.publisher,transport,clock=lambda:paid.AT,
                                 max_run_seconds=120,**kwargs)

    def test_timeout_retries_once_then_publishes_and_continues(self):
        t = Scripted({self.path:[job.EquiblesTransportFailure("timeout"),raw.body("A",1)]})
        self.assertEqual(self.batch(t)["outcome"],"acquired")
        first = policy.record(self.f.state(),self.path)
        self.assertEqual(first["outcome"],"retry_pending")
        report = self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(t.paths.count(self.path),2)
        self.assertEqual(self.publisher.calls,[("A",q) for q in (1,2,3,4)])
        self.assertEqual(report["unresolved_quarters"],0)
        self.assertEqual(report["download_retry_charges_today"],1)
        self.assertEqual(policy.record(self.f.state(),self.path)["outcome"],"recovered")
        self.assertEqual(self.f.state()["usage"][paid.AT.date().isoformat()]["attempted"],5)

    def test_second_timeout_records_unresolved_and_next_quarters_finish(self):
        t = Scripted({self.path:[job.EquiblesTransportFailure("timeout"),job.EquiblesTransportFailure("timeout")]})
        self.batch(t)
        report = self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["unresolved_quarters"],1)
        self.assertEqual(report["skipped_quarters"],0)
        self.assertEqual(t.paths.count(self.path),2)
        self.assertEqual(self.publisher.calls,[("A",2),("A",3),("A",4)])
        completed = self.f.state()["completed"]["A"]
        self.assertEqual(completed["status"],"completed_with_unresolved_transcripts")
        gap = completed["unresolved_transcripts"][0]
        self.assertEqual(gap["fiscal_quarter"],1)
        self.assertEqual(len(gap["attempts"]),2)
        self.assertEqual(gap["reason"],"download_unresolved_after_retry")
        self.assertNotIn("response_sha256",gap)
        self.assertIsNone(job.retained(self.root,self.path))
        self.assertEqual(self.wave(Scripted())["requests_this_run"],0)

    def test_recovery_after_crash_replays_failure_once_and_does_not_refund(self):
        class Crash(BaseException): pass
        original = job.atomic
        def crash(path,value,**kwargs):
            if path==self.root/"state.json" and isinstance(value,dict) and value.get("download_failures"):
                raise Crash()
            return original(path,value,**kwargs)
        t = Scripted({self.path:[job.EquiblesTransportFailure("connection_error")]})
        with patch.object(job,"atomic",side_effect=crash), self.assertRaises(Crash):
            self.batch(t)
        usage = copy.deepcopy(self.f.state()["usage"])
        self.assertTrue(parallel.recover_batch(self.root,clock=lambda:paid.AT))
        self.assertTrue(parallel.recover_batch(self.root,clock=lambda:paid.AT))
        self.assertEqual(len(policy.record(self.f.state(),self.path)["failures"]),1)
        self.assertEqual(self.f.state()["usage"],usage)
        self.assertEqual(t.paths,[self.path])

    def test_legacy_uncertain_attempt_retries_once_without_invented_diagnostic(self):
        t = Scripted({self.path:[job.EquiblesTransportFailure("worker_lost"),job.EquiblesTransportFailure("worker_lost")]})
        with patch.object(job,"retain_transport_failure",return_value=None):
            self.batch(t)
        original = policy.record(self.f.state(),self.path)["failures"][0]
        self.assertEqual(original["diagnostic"]["category"],"uncertain_attempt")
        report = self.wave(t)
        self.assertEqual(report["unresolved_quarters"],1)
        self.assertEqual(t.paths.count(self.path),2)
        self.assertEqual(len(policy.record(self.f.state(),self.path)["failures"]),2)

    def test_daily_cap_defers_pending_retry_without_dispatch_or_refund(self):
        state = self.f.state()
        bucket = state["usage"][paid.AT.date().isoformat()]
        bucket.update(attempted=9999,remaining=1)
        self.f.save(state)
        t = Scripted({self.path:[job.EquiblesTransportFailure("timeout")]})
        self.batch(t)
        report = self.wave(t)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(t.paths,[self.path])
        self.assertEqual(report["quota"]["attempted"],10000)
        self.assertEqual(policy.record(self.f.state(),self.path)["outcome"],"retry_pending")

    def test_invocation_request_cap_preserves_retry_for_next_invocation(self):
        t = Scripted({self.path:[job.EquiblesTransportFailure("timeout"),raw.body("A",1)]})
        report = self.wave(t,max_requests=1)
        self.assertEqual(report["requests_this_run"],1)
        self.assertEqual(report["outcome"],"run_resource_limit")
        self.assertEqual(t.paths,[self.path])
        report = self.wave(t,max_requests=1)
        self.assertEqual(t.paths.count(self.path),2)
        self.assertEqual(report["download_retry_charges_today"],1)
        self.assertEqual(self.publisher.calls,[("A",1)])

    def test_http_503_retries_and_preserves_both_raw_error_responses(self):
        t = Scripted({self.path:[(503,b'{"error":"temporary"}'),(503,b'{"error":"temporary"}')]})
        self.batch(t)
        report = self.wave(t)
        self.assertEqual(report["unresolved_quarters"],1)
        failures = policy.record(self.f.state(),self.path)["failures"]
        self.assertEqual(len(failures),2)
        for failure in failures:
            entry = failure["reservation"]
            receipt,body = job.response_receipt(self.root,entry["attempt"],entry["path"])
            self.assertEqual(receipt["status"],503)
            self.assertEqual(body,b'{"error":"temporary"}')
        self.assertIsNone(job.retained(self.root,self.path))

    def test_authentication_failure_still_blocks_without_retry_or_skip(self):
        t = Scripted({self.path:[(401,b'{"error":"unauthorized"}')]})
        self.assertEqual(self.batch(t)["outcome"],"blocked")
        self.assertEqual(self.wave(t)["outcome"],"blocked")
        self.assertEqual(t.paths,[self.path])
        self.assertIsNone(policy.record(self.f.state(),self.path))

    def test_malformed_quota_headers_on_503_still_block(self):
        t = Scripted({self.path:[(503,b"{}")]})
        request = t.request
        def invalid(path):
            status,headers,body=request(path)
            headers["x-ratelimit-reset"]="invalid"
            return status,headers,body
        t.request=invalid
        self.assertEqual(self.batch(t)["outcome"],"blocked")
        self.assertIsNone(policy.record(self.f.state(),self.path))

    def test_null_on_retry_is_missing_response_not_unresolved_download(self):
        t = Scripted({self.path:[job.EquiblesTransportFailure("timeout"),b"null"]})
        self.batch(t)
        report = self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["skipped_quarters"],1)
        self.assertEqual(report["unresolved_quarters"],0)
        self.assertEqual(policy.record(self.f.state(),self.path)["outcome"],"recovered")

    def test_failed_later_page_preserves_partial_evidence_without_partial_publication(self):
        later = self.path.replace("offset=0","offset=1")
        t = Scripted({self.path:[raw.body("A",1,total=2,count=1)],
                      later:[job.EquiblesTransportFailure("timeout"),job.EquiblesTransportFailure("timeout")]})
        report = self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["unresolved_quarters"],1)
        self.assertNotIn(("A",1),self.publisher.calls)
        gap=self.f.state()["completed"]["A"]["unresolved_transcripts"][0]
        self.assertEqual(gap["partial_page_paths"],[self.path])
        self.assertEqual(job.retained(self.root,self.path)[1],raw.body("A",1,total=2,count=1))
        self.assertEqual(t.paths.count(later),2)

    def test_failed_catalogue_is_explicitly_unresolved_without_invented_calls(self):
        state=self.f.state()
        state["current"]=None
        self.f.save(state)
        path=parallel.planned_paths(state)[0]
        t=Scripted({path:[job.EquiblesTransportFailure("timeout"),job.EquiblesTransportFailure("timeout")]})
        report=self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["unresolved_catalogues"],1)
        self.assertEqual(self.publisher.calls,[])
        self.assertEqual(self.f.state()["completed"]["A"]["status"],"unresolved_catalogue_after_retry")
        self.assertEqual(t.paths,[path,path])

    def test_failed_initial_verification_can_retry_once_and_verify_headers(self):
        state=self.f.state()
        state["current"]=None
        state["usage"][paid.AT.date().isoformat()]["paid_headers_verified"]=False
        self.f.save(state)
        path=parallel.planned_paths(state)[0]
        t=Scripted({path:[job.EquiblesTransportFailure("timeout"),raw.catalog("A",(1,2,3,4))]})
        report=self.wave(t)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(t.paths.count(path),2)
        self.assertTrue(report["quota"]["paid_headers_verified"])

    def test_third_failed_attempt_and_wrong_retry_link_are_rejected(self):
        state=self.f.state()
        entry={"attempt":"one","path":self.path,"started_at":job.utc(paid.AT.isoformat())}
        diagnostic={"category":"timeout","http_status":None}
        policy.fail(state,entry,diagnostic,observed_at=paid.AT.isoformat())
        with self.assertRaises(job.ConflictError):
            policy.fail(state,{**entry,"attempt":"two"},diagnostic,observed_at=paid.AT.isoformat())
        policy.fail(state,{**entry,"attempt":"two","retry_of":"one"},diagnostic,observed_at=paid.AT.isoformat())
        with self.assertRaises(job.ConflictError):
            policy.fail(state,{**entry,"attempt":"three","retry_of":"one"},diagnostic,observed_at=paid.AT.isoformat())
        with self.assertRaises(job.ConflictError):
            policy.retry_of(state,self.path)

    def test_module_entrypoint_uses_the_same_typed_transport_as_parallel_workers(self):
        import contextlib
        import io
        import runpy
        import warnings
        from types import SimpleNamespace
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            shadow = runpy.run_module("quant_data.operations.equibles_transcript_backfill",
                                     run_name="equibles_entrypoint_fixture")
        received = []
        def execute(root, publisher, transport, **kwargs):
            received.append(transport)
            self.assertIsInstance(transport, job.EquiblesTransport)
            self.assertEqual(transport.start_method, "spawn")
            return {"outcome":"complete"}
        replacements = {
            "load_registry":lambda *a,**k: object(),
            "EquiblesTranscriptPublisher":lambda *a,**k: object(),
            "stores":lambda: object(),
            "read_project_credential":lambda **k:"offline-test-credential",
            "STATE_ROOT":self.root,
        }
        with patch.dict(shadow["main"].__globals__,replacements), patch.object(daily,"run_day",side_effect=execute), \
             patch("quant_data.market.collection_bindings.load_bindings",
                   return_value={"equibles_transcripts":SimpleNamespace(mode="active")}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(shadow["main"]([]),0)
        self.assertEqual(len(received),1)

    def test_daily_controller_reports_retries_and_unresolved_coverage(self):
        t=Scripted({self.path:[job.EquiblesTransportFailure("timeout"),job.EquiblesTransportFailure("timeout")]})
        self.batch(t)
        report=daily.run_day(self.root,self.publisher,t,clock=lambda:paid.AT,parallel=True,max_seconds=120)
        self.assertEqual(report["outcome"],"complete")
        self.assertEqual(report["automatic_retries"],1)
        self.assertEqual(report["unresolved_quarters"],1)
        self.assertEqual(report["download_failure_policy"],policy.POLICY)


if __name__=="__main__":
    unittest.main()
