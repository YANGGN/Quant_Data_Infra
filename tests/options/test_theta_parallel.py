"""Offline concurrency, accounting, resume and single-writer regressions."""
import copy,gzip,hashlib,json,threading,time,unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from quant_data.options.job import SpyHistoryJob,DOWNLOAD_WORKERS
from quant_data.options.transport import Budget,ThetaTransport,ProviderFailure
from quant_data.options.model import build_capture
from tests.options.test_theta_compact import temporary_store,fixtures,SESSION,PREVIOUS

def setup_work(root):
    work=root/".local/theta-spy-20260924";work.mkdir(parents=True,exist_ok=True)
    (work/"preflight.json").write_text(json.dumps({"data_requests":0,"response_bytes":0}))
    (work/"preflight-receipts.jsonl").write_text(json.dumps({"captured_at":datetime.now(timezone.utc).isoformat()})+"\n")
    for year in range(2015,2027):
        (work/f"calendar-{year}.json").write_text(json.dumps({
            "rows":[{"date":f"{year}-01-01","type":"full_close"}],"receipt":{"response_bytes":0}}))
    (work/"spot-references.json").write_text("{}")
    return work

def day_rows(session):
    e,o,_=fixtures()
    delta=date.fromisoformat(session)-date.fromisoformat(SESSION)
    for row in e+o:
        row["expiration"]=(date.fromisoformat(row["expiration"])+delta).isoformat()
        for key in ("timestamp","underlying_timestamp"):
            if key in row:row[key]=row[key].replace(SESSION,session)
    return e,o

class ParallelBudgetTests(unittest.TestCase):
    def test_competing_request_reservations_never_exceed_global_cap(self):
        budget=Budget(max_requests=7)
        barrier=threading.Barrier(16)
        def reserve(_):
            barrier.wait(timeout=5)
            try:budget.reserve_request();return True
            except RuntimeError:return False
        with ThreadPoolExecutor(max_workers=16) as pool:success=list(pool.map(reserve,range(16)))
        self.assertEqual(sum(success),7)
        self.assertEqual(budget.snapshot()["data_requests"],7)

    def test_parallel_bytes_are_counted_once_and_stop_new_admission(self):
        budget=Budget(max_bytes=1000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(budget.add_bytes,[10]*100))
        self.assertEqual(budget.snapshot()["received_bytes"],1000)
        with self.assertRaisesRegex(RuntimeError,"response_byte_cap"):budget.reserve_request()

class ParallelJobTests(unittest.TestCase):
    def test_duplicate_concurrent_unit_has_one_provider_attempt(self):
        temp,store=temporary_store()
        with temp:
            job=SpyHistoryJob(store.root);calls=[];entered=threading.Event();release=threading.Event()
            class Fake:
                def request(self,*a,**k):
                    calls.append(1);entered.set()
                    if not release.wait(5):raise AssertionError("test timeout")
                    return [],{"method":"calendar_year","params":{"year":"2026"}}
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(job.cached_get,Fake(),"calendar_year",year="2026")
                self.assertTrue(entered.wait(5))
                second=pool.submit(job.cached_get,Fake(),"calendar_year",year="2026")
                with self.assertRaisesRegex(RuntimeError,"previously_attempted"):second.result(timeout=5)
                release.set();first.result(timeout=5)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(job.attempt_path.read_text().splitlines()),1)

    def test_queue_is_bounded_and_network_overlaps(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();job=SpyHistoryJob(store.root)
            barrier=threading.Barrier(DOWNLOAD_WORKERS);calls=[];mutex=threading.Lock()
            def acquire(transport,session):
                with mutex:calls.append(session)
                if len(calls)<=DOWNLOAD_WORKERS:barrier.wait(timeout=5)
                return {"empty":True,"paths":set()}
            remaining=[(str(date(2026,9,23)-timedelta(days=i)),PREVIOUS) for i in range(10)]
            with patch.object(job,"acquire_session",side_effect=acquire):
                generator=job.downloaded_sessions(None,remaining)
                first=next(generator)
                self.assertEqual(first[0],remaining[0][0])
                self.assertEqual(len(calls),DOWNLOAD_WORKERS)
                generator.close()
                self.assertEqual(len(calls),DOWNLOAD_WORKERS)

    def test_parallel_results_match_serial_transform_and_only_coordinator_writes(self):
        temp,store=temporary_store()
        with temp:
            work=setup_work(store.root)
            sessions=[(str(date(2026,9,20)+timedelta(days=i)),str(date(2026,9,19)+timedelta(days=i))) for i in range(4)]
            batches={s:day_rows(s) for s,p in sessions}
            barrier=threading.Barrier(4);active=0;peak=0;mutex=threading.Lock();calls=[]
            coordinator=threading.get_ident();write_threads=[]
            class Fake:
                subscription_code=2
                def __init__(self,root,budget,receipt_callback):
                    self.budget=budget;self.callback=receipt_callback
                def request(self,method,**params):
                    nonlocal active,peak
                    day=str(params.get("date",params.get("start_date")))
                    self.budget.reserve_request()
                    with mutex:
                        active+=1;peak=max(peak,active);calls.append((method,day))
                    if method=="option_history_greeks_eod":barrier.wait(timeout=5)
                    e,o=batches[day]
                    rows=e if method=="option_history_greeks_eod" else o
                    receipt={"method":method,"params":{k:str(v) for k,v in params.items()},
                        "captured_at":"2026-09-24T10:00:00+00:00","response_sha256":"b"*64,
                        "response_bytes":100,"outcome":"success"}
                    self.budget.add_bytes(100);self.callback(receipt,self.budget)
                    with mutex:active-=1
                    return rows,receipt
                def close(self):pass
            job=SpyHistoryJob(store.root,transport_factory=Fake)
            original_publish=job.store.publish
            def publish(c):
                write_threads.append(threading.get_ident())
                return original_publish(c)
            with patch("quant_data.options.job.sessions_from_calendars",return_value=sessions),patch.object(job.store,"publish",side_effect=publish):
                result=job.run()
            self.assertEqual(result["state"],"completed")
            self.assertEqual(peak,4)
            self.assertEqual(set(write_threads),{coordinator})
            self.assertEqual(len(calls),8)
            self.assertEqual(result["data_requests"],9) # Includes one seeded calendar probe.
            self.assertEqual(result["received_bytes"],800)
            self.assertFalse(list(job.stage.glob("*.json.gz")))
            with store.read() as c:
                actual={row[0]:row[1] for row in c.execute("SELECT session_date,semantic_sha256 FROM option_captures")}
            for session,previous in sessions:
                e,o=batches[session]
                expected=build_capture(session,e,o,[],previous_session=previous)
                self.assertEqual(actual[session],expected["semantic_sha256"])

    def test_cleanup_failure_stops_publication_and_preserves_recovery_journal(self):
        temp,store=temporary_store()
        with temp:
            work=setup_work(store.root)
            sessions=[(str(date(2026,9,19)+timedelta(days=i)),str(date(2026,9,18)+timedelta(days=i))) for i in range(5)]
            calls=[];mutex=threading.Lock()
            class Fake:
                subscription_code=2
                def __init__(self,root,budget,receipt_callback):self.budget=budget
                def request(self,method,**params):
                    day=str(params.get("date",params.get("start_date")))
                    with mutex:calls.append(day)
                    e,o=day_rows(day)
                    return (e if "greeks" in method else o),{
                        "method":method,"params":{k:str(v) for k,v in params.items()},
                        "captured_at":"2026-09-24T10:00:00+00:00","response_sha256":"b"*64,
                        "response_bytes":100,"outcome":"success"}
                def close(self):pass
            job=SpyHistoryJob(store.root,transport_factory=Fake)
            original=job.reconcile_cleanup
            checks=0
            def fail_after_commit():
                nonlocal checks
                checks+=1
                if checks==1:return original()  # Startup reconciliation.
                raise ValueError("injected_cleanup_failure")
            with patch("quant_data.options.job.sessions_from_calendars",return_value=sessions),patch.object(job,"reconcile_cleanup",side_effect=fail_after_commit):
                with self.assertRaisesRegex(ValueError,"injected_cleanup_failure"):job.run()
            self.assertEqual(job.status["state"],"stopped")
            self.assertEqual(store.status()["sessions"],1)
            self.assertLessEqual(len(set(calls)),4)
            journal=json.loads((work/"pending-cleanup.json").read_text())
            self.assertEqual(journal["session"],sessions[-1][0])
            self.assertTrue(all((work/entry["path"]).exists() for entry in journal["files"]))
            self.assertTrue(original())
            self.assertFalse((work/"pending-cleanup.json").exists())

    def test_cached_session_reused_without_any_provider_call(self):
        temp,store=temporary_store()
        with temp:
            job=SpyHistoryJob(store.root);calls=[]
            e,o=day_rows(SESSION)
            class Fake:
                def request(self,method,**params):
                    calls.append(method)
                    return e if "greeks" in method else o,{"method":method,"params":{k:str(v) for k,v in params.items()}}
            job.acquire_session(Fake(),SESSION)
            self.assertEqual(len(calls),2)
            new=SpyHistoryJob(store.root)
            class Forbidden:
                def request(self,*a,**k):raise AssertionError("cached scope repeated")
            batch=new.acquire_session(Forbidden(),SESSION)
            self.assertEqual(len(batch["greeks"]),len(e))
            self.assertEqual(len(batch["oi"]),len(o))

    def test_failure_drains_inflight_inputs_and_prevents_fallback_or_new_day(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();job=SpyHistoryJob(store.root)
            barrier=threading.Barrier(4);calls=[];mutex=threading.Lock()
            class Fake:
                def request(self,method,**params):
                    day=str(params.get("start_date"))
                    with mutex:calls.append((method,day))
                    barrier.wait(timeout=5)
                    if day=="2026-09-23":raise ProviderFailure("RESOURCE_EXHAUSTED",{})
                    if not job._stop.wait(5):raise AssertionError("worker failure not propagated")
                    return [],{"method":method,"params":{k:str(v) for k,v in params.items()}}
            remaining=[(str(date(2026,9,23)-timedelta(days=i)),PREVIOUS) for i in range(8)]
            generator=job.downloaded_sessions(Fake(),remaining)
            with self.assertRaises(ProviderFailure):next(generator)
            self.assertEqual(len(calls),4)
            self.assertEqual(len(list(job.stage.glob("*.json.gz"))),3)
            self.assertTrue(job._stop.is_set())
            self.assertEqual(job._failure["reason"],"RESOURCE_EXHAUSTED")

class ParallelTransportTests(unittest.TestCase):
    def test_interleaved_streams_keep_distinct_hashes_and_byte_receipts(self):
        try:
            from thetadata._proto.endpoints_pb2 import DataTable,ResponseData,CompressionAlgo
        except ImportError:self.skipTest("isolated pinned SDK required")
        barrier=threading.Barrier(4);payloads={}
        for value in range(4):
            messages=[]
            for part in range(2):
                table=DataTable();table.headers.append("volume")
                table.data_table.add().values.add().number=value*10+part
                messages.append(ResponseData(compressed_data=table.SerializeToString(),
                    compression_description={"algo":CompressionAlgo.NONE}))
            payloads[value]=messages
        class Rpc:
            def __init__(self,value):self.value=value
            def __iter__(self):
                for message in payloads[self.value]:
                    barrier.wait(timeout=5);yield message
            def cancel(self):pass
        class Stub:
            def __init__(self,channel):pass
            def GetOptionHistoryEod(self,value,**kwargs):return Rpc(value)
        class Client:
            options_subscription=2
            def __init__(self,**kwargs):pass
            def option_history_eod(self,**params):
                return self._convert_response_stream(self.stub.GetOptionHistoryEod(int(params["strike"])))
        budget=Budget();channel=type("Channel",(),{"close":lambda self:None})()
        with patch("quant_data.options.transport.read_project_credential",return_value="offline-fixture"),patch("thetadata.ThetaClient",Client),patch("grpc.secure_channel",return_value=channel),patch("thetadata._proto.v3grpc.endpoints_pb2_grpc.BetaThetaTerminalStub",Stub),patch.dict("os.environ",{},clear=True):
            transport=ThetaTransport(Path("/tmp"),budget)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(lambda v:transport.request("option_history_eod",symbol="SPY",strike=str(v)),range(4)))
            transport.close()
        total=0
        for value,(rows,receipt) in enumerate(results):
            digest=hashlib.sha256();size=0
            for message in payloads[value]:
                data=message.SerializeToString(deterministic=True)
                digest.update(len(data).to_bytes(8,"big"));digest.update(data);size+=len(data)
            self.assertEqual(rows,[{"volume":value*10},{"volume":value*10+1}])
            self.assertEqual(receipt["response_sha256"],digest.hexdigest())
            self.assertEqual(receipt["response_bytes"],size)
            self.assertEqual(receipt["messages"],2)
            total+=size
        self.assertEqual(budget.snapshot(),{"data_requests":4,"received_bytes":total})
