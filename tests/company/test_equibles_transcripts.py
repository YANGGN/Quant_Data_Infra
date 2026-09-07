from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quant_data.company.equibles_transcripts import (
    EquiblesTranscriptPublisher, RawPage, validate_bundle, event_page,
)
from quant_data.errors import ValidationError, ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.migrations import initialize_all, migrate_store
from quant_data.registry import load_registry, equibles_transcript_registry_profile
from quant_data.stores import StoreMap, writer_connection, quiet_immutable_read_connection
from quant_data.tool_platform.generate import generated_bytes
from quant_data.operations import equibles_transcript_backfill as job

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 9, 7, 5, tzinfo=timezone.utc)


def event(symbol="A", quarter=1):
    return {"id": symbol + "-" + str(quarter), "eventType": "EarningsCall",
            "fiscalYear": 2020, "fiscalQuarter": quarter, "hasTranscript": True}


def body(symbol="A", quarter=1, offset=0, total=1, count=1):
    return json.dumps({"ticker": symbol, "eventId": event(symbol, quarter)["id"],
        "fiscalYear": 2020, "fiscalQuarter": quarter, "eventTitle": "Call", "callDate": "2020-01-01",
        "offset": offset, "turnCount": count, "totalTurnCount": total,
        "hasMore": offset + count < total,
        "data": [{"speakerName": None, "speakerRole": None, "startSeconds": None,
                  "endSeconds": None, "text": "Raw words " + str(i)} for i in range(offset, offset+count)],
        "corrections": []}).encode()


def page(raw, at=AT.isoformat()):
    return RawPage(raw, at, "equibles-transcripts/blobs/" + hashlib.sha256(raw).hexdigest() + ".json")


def catalog(symbol="A", quarters=(1,)):
    rows = [event(symbol,q) for q in quarters]
    return json.dumps({"data": rows, "meta": {"offset": 0, "count": len(rows), "limit": 100, "hasMore": False}}).encode()


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temp.name)
        self.stores = StoreMap.four_explicit(**{r:root/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry = load_registry(ROOT/"config/system_registry.json", project_root=ROOT, environment={})
        initialize_all(self.stores,self.registry)
        self.publisher = EquiblesTranscriptPublisher(self.stores,self.registry)

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, pages):
        return self.publisher.publish(symbol="A",instrument_id="frozen-A",event=event(),pages=pages)

    def test_raw_bytes_nullable_turns_capture_cutoff_replay_and_immutability(self):
        raw=body()
        result=self.publish((page(raw),))
        self.assertEqual(result.written_count,2)
        with quiet_immutable_read_connection(self.stores,"company") as c:
            self.assertEqual(c.execute("SELECT raw_body FROM company_equibles_transcript_pages").fetchone()[0],raw)
            self.assertEqual(c.execute("SELECT count(*) FROM company_equibles_transcripts WHERE captured_at<=?",
                                     ("2025-01-01T00:00:00.000000Z",)).fetchone()[0],0)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publish((page(raw,(AT+timedelta(days=1)).isoformat()),)).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with writer_connection(self.stores,"company") as c:
            for table in ("company_equibles_transcripts","company_equibles_transcript_pages"):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("DELETE FROM "+table)

    def test_complete_multipage_atomicity_and_scope_tamper(self):
        pages=(page(body(total=3,count=2)),page(body(offset=2,total=3,count=1),(AT+timedelta(minutes=1)).isoformat()))
        self.publish(pages)
        before=mutation_fingerprint(self.stores)
        for bad in (pages[:1],(pages[1],),pages+(pages[1],),
                    (replace(pages[0],reference="equibles-transcripts/blobs/"+"0"*64+".json"),pages[1]),
                    (page(body("B")),)):
            with self.assertRaises(ValidationError): self.publish(bad)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_registry_predecessor_and_migration_rerun(self):
        previous=equibles_transcript_registry_profile(self.registry)
        self.assertEqual(previous.registry_version,"2.73.0")
        self.assertEqual(previous.source_sha256,"658be96e5a171801887adf6ba6c1a13e460228386423bc47e4a8b38714987a4c")
        self.assertEqual(generated_bytes(ROOT)[0],(ROOT/"config/system_registry.json").read_bytes())
        before=mutation_fingerprint(self.stores)
        migrate_store(self.stores,self.registry,"company",applied_at=AT.isoformat())
        self.assertEqual(before,mutation_fingerprint(self.stores))


class FakePublisher:
    def __init__(self): self.calls=[]
    def publish(self,**kwargs):
        validate_bundle(kwargs["symbol"],kwargs["instrument_id"],kwargs["event"],kwargs["pages"])
        self.calls.append((kwargs["symbol"],kwargs["event"]["fiscalQuarter"]))


class FakeTransport:
    def __init__(self, values, clock=lambda:AT, remaining=100):
        self.values=list(values);self.paths=[];self.clock=clock;self.remaining=remaining
    def request(self,path):
        self.paths.append(path)
        value=self.values.pop(0)
        if isinstance(value,Exception): raise value
        status,raw=value if isinstance(value,tuple) else (200,value)
        self.remaining-=1
        reset=self.clock().replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        return status,{"content-type":"application/json","x-ratelimit-limit":"100",
                       "x-ratelimit-remaining":str(self.remaining),"x-ratelimit-reset":str(int(reset.timestamp()))},raw


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/tmp")
        self.root=Path(self.temp.name)/"private"
        job.freeze(self.root,[{"symbol":s,"instrument_id":"frozen-"+s} for s in ("A","B")])
        self.publisher=FakePublisher()
        self.clock=lambda:AT
    def tearDown(self): self.temp.cleanup()
    def state(self): return json.loads((self.root/"state.json").read_bytes())
    def save(self,s): job.atomic(self.root/"state.json",s,replace=True)
    def run_job(self,transport):
        return job.run(self.root,self.publisher,transport,clock=self.clock,sleeper=lambda _:None)
    def test_ticker_then_oldest_call_order_and_complete_no_network(self):
        t=FakeTransport([catalog("A",(2,1)),body("A",1),body("A",2),catalog("B"),body("B")])
        report=self.run_job(t)
        self.assertEqual(self.publisher.calls,[("A",1),("A",2),("B",1)])
        self.assertEqual(report["outcome"],"complete")
        t=FakeTransport([])
        self.assertEqual(self.run_job(t)["requests_this_run"],0)
        self.assertEqual(t.paths,[])
    def test_daily_cap_resumes_current_call_before_next_ticker(self):
        s=self.state();s["usage"][AT.date().isoformat()]={"attempted":98,"remaining":2};self.save(s)
        t=FakeTransport([catalog(),body(total=3,count=2)],remaining=2)
        report=self.run_job(t)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(report["requests_this_run"],2)
        self.assertEqual(report["completed_tickers"],0)
        self.assertEqual(self.state()["current"]["offset"],2)
        self.clock=lambda:AT+timedelta(days=1)
        t=FakeTransport([body(offset=2,total=3),catalog("B"),body("B")],clock=self.clock)
        self.assertEqual(self.run_job(t)["outcome"],"complete")
        self.assertIn("A/earnings-calls/2020/1/speakers?limit=200&offset=2",t.paths[0])
    def test_shared_provider_remaining_wins_and_429_defers(self):
        t=FakeTransport([catalog()],remaining=1)
        report=self.run_job(t)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(report["requests_this_run"],1)
        self.clock=lambda:AT+timedelta(days=1)
        t=FakeTransport([(429,b'{"error":"quota"}')],clock=self.clock)
        self.assertEqual(self.run_job(t)["outcome"],"provider_quota")
        self.assertIsNone(self.state()["blocked"])
        self.assertEqual(self.run_job(FakeTransport([]))["requests_this_run"],0)
    def test_ambiguous_transport_failure_reserved_and_not_retried(self):
        report=self.run_job(FakeTransport([RuntimeError("do not echo credentials")]))
        self.assertEqual(report["outcome"],"blocked")
        self.assertEqual(report["quota"]["attempted"],1)
        self.assertNotIn("credentials",json.dumps(report))
        self.assertEqual(self.run_job(FakeTransport([]))["requests_this_run"],0)
    def test_cached_success_recovers_pending_without_refetch(self):
        path="/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0"
        headers={"x-ratelimit-limit":"100","x-ratelimit-remaining":"99",
                 "x-ratelimit-reset":str(int((AT.replace(hour=0)+timedelta(days=1)).timestamp())),
                 "content-type":"application/json"}
        job.retain_response(self.root,path,catalog(),AT.isoformat(),headers,200)
        s=self.state();s["pending"]={"path":path,"attempt":"old","started_at":AT.isoformat()}
        s["usage"][AT.date().isoformat()]={"attempted":1,"remaining":99};self.save(s)
        t=FakeTransport([body(),catalog("B"),body("B")])
        self.assertEqual(self.run_job(t)["outcome"],"complete")
        self.assertNotIn(path,t.paths)
    def test_cached_pending_quota_zero_is_reconciled_before_next_request(self):
        path="/v1/stocks/A/investor-events?eventType=EarningsCall&limit=100&offset=0"
        headers={"x-ratelimit-limit":"100","x-ratelimit-remaining":"0",
                 "x-ratelimit-reset":str(int((AT.replace(hour=0)+timedelta(days=1)).timestamp())),
                 "content-type":"application/json"}
        job.retain_response(self.root,path,catalog(),AT.isoformat(),headers,200)
        s=self.state();s["pending"]={"path":path,"attempt":"old","started_at":AT.isoformat()}
        s["usage"][AT.date().isoformat()]={"attempted":1,"remaining":99};self.save(s)
        transport=FakeTransport([])
        report=self.run_job(transport)
        self.assertEqual(report["outcome"],"daily_quota")
        self.assertEqual(report["quota"]["remaining"],0)
        self.assertEqual(transport.paths,[])
        self.assertIsNone(self.state()["pending"])

    def test_durable_error_recovers_without_retry_after_crash(self):
        from unittest.mock import patch
        class PowerLoss(BaseException): pass
        original=job.retain_response
        def interrupted(*args,**kwargs):
            result=original(*args,**kwargs)
            raise PowerLoss()
        with patch.object(job,"retain_response",side_effect=interrupted):
            with self.assertRaises(PowerLoss):
                self.run_job(FakeTransport([(401,b'{"error":"unauthorized"}')]))
        self.assertIsNotNone(self.state()["pending"])
        self.assertIsNone(self.state()["blocked"])
        transport=FakeTransport([])
        report=self.run_job(transport)
        self.assertEqual(report["outcome"],"blocked")
        self.assertEqual(transport.paths,[])
        self.assertEqual(self.run_job(FakeTransport([]))["requests_this_run"],0)

    def test_page_cap_is_enforced_before_a_fifty_first_transcript_get(self):
        transport=FakeTransport([catalog()]+[body(offset=i,total=51) for i in range(50)])
        report=self.run_job(transport)
        self.assertEqual(report["outcome"],"blocked")
        self.assertEqual(len(transport.paths),51)  # One catalogue plus exactly fifty pages.
        self.assertEqual(self.publisher.calls,[])
        self.assertEqual(report["current_ticker"],"A")

    def test_transcript_404_or_duplicate_events_blocks_before_next_ticker(self):
        t=FakeTransport([catalog(),(404,b'{"error":"missing"}')])
        report=self.run_job(t)
        self.assertEqual(report["outcome"],"blocked")
        self.assertEqual(report["current_ticker"],"A")
        self.assertEqual(self.publisher.calls,[])
    def test_no_coverage_is_recorded_and_next_ticker_proceeds(self):
        t=FakeTransport([(404,b'{"error":"missing"}'),catalog("B"),body("B")])
        self.assertEqual(self.run_job(t)["outcome"],"complete")
        self.assertEqual(self.state()["completed"]["A"]["status"],"provider_ticker_not_found")
    def test_private_paths_and_concurrent_run_fail_closed(self):
        with job.job_lock(self.root):
            with self.assertRaises(ConflictError):
                self.run_job(FakeTransport([]))
        alias=Path(self.temp.name)/"alias";alias.symlink_to(self.root)
        with self.assertRaises(ValidationError): self.run_job_with_root(alias)
    def run_job_with_root(self,root):
        return job.run(root,self.publisher,FakeTransport([]),clock=self.clock)

if __name__=="__main__":
    unittest.main()
