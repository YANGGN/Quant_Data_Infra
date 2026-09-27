import copy,gzip,hashlib,json,sqlite3,tempfile,unittest
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
from unittest.mock import patch
from quant_data.options.model import build_capture,roundness
from quant_data.options.store import OptionsStore
from quant_data.options.job import sessions_from_calendars,SpyHistoryJob
from quant_data.options.transport import Budget,decode_stream,ProviderFailure

ROOT=Path(__file__).resolve().parents[2]
SESSION="2026-09-23"
PREVIOUS="2026-09-22"

def fixtures():
    rows=[];oi=[]
    for dte in (1,2,3,7,14,30,60,90,180,365):
        expiry=(date.fromisoformat(SESSION)+timedelta(days=dte)).isoformat()
        for strike in range(60,141):
            for right in ("CALL","PUT"):
                volume=10000 if strike==139 else 1
                r={"symbol":"SPY","expiration":expiry,"strike":str(strike),"right":right,
                   "timestamp":SESSION+"T16:00:00-04:00","underlying_price":"100.1",
                   "underlying_timestamp":SESSION+"T17:14:00-04:00",
                   "bid":"2.00","ask":"2.02","bid_size":5,"ask_size":7,
                   "volume":volume,"count":volume,"implied_vol":".20","iv_error":".001"}
                rows.append(r)
                oi.append({"symbol":"SPY","expiration":expiry,"strike":str(strike),"right":right,
                    "timestamp":SESSION+"T06:30:00-04:00","open_interest":10000 if strike==138 else 2})
    receipt={"method":"option_history_greeks_eod","params":{},"captured_at":"2026-09-24T10:00:00+00:00",
             "response_sha256":"a"*64,"response_bytes":100,"outcome":"success"}
    return rows,oi,[receipt]

def capture(rows=None,oi=None):
    e,o,r=fixtures()
    return build_capture(SESSION,rows if rows is not None else e,oi if oi is not None else o,r,previous_session=PREVIOUS)

def temporary_store():
    temp=tempfile.TemporaryDirectory(prefix="theta-fixture-")
    root=Path(temp.name)
    for name in ("config/options_registry.json","quant_data/migrations/options/0001_theta_compact.sql","quant_data/migrations/options/0002_tier1_etf_roots.sql"):
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    return temp,OptionsStore(root)

class SelectionTests(unittest.TestCase):
    def test_fixed_anchors_survive_outside_band_activity_additions(self):
        c=capture()
        self.assertLessEqual(len(c["details"]),300)
        reasons={d["strike"]:d["reasons"] for d in c["details"]}
        self.assertIn("139",reasons);self.assertIn("leader:volume",reasons["139"])
        self.assertIn("138",reasons);self.assertIn("leader:oi",reasons["138"])
        self.assertIn("100",reasons);self.assertTrue(any(x.startswith("anchor:") for x in reasons["100"]))
        self.assertEqual(len(c["summaries"]),102)
        self.assertLessEqual(len(c["leaders"]),40)

    def test_roundness_is_disjoint(self):
        self.assertEqual(roundness("100"),"multiple_10")
        self.assertEqual(roundness("105"),"multiple_5_only")
        self.assertEqual(roundness("106"),"other")

    def test_full_equals_selected_plus_remainder_for_every_cell(self):
        c=capture()
        cells={(r["population"],r["dimension"],r["bucket"],r["right"]):r["stats"] for r in c["summaries"]}
        for pop,dim,bucket,right in cells:
            if pop!="full":continue
            a,b,d=[cells[(p,dim,bucket,right)] for p in ("full","selected","remainder")]
            for field in ("contracts","volume","open_interest","volume_missing","oi_missing"):
                self.assertEqual(a[field],b[field]+d[field])

    def test_future_oi_is_excluded_and_missing_is_not_zero(self):
        e,o,r=fixtures()
        for row in o:row["timestamp"]="2026-09-24T06:30:00-04:00"
        c=build_capture(SESSION,e,o,r,previous_session=PREVIOUS)
        full=[s for s in c["summaries"] if s["population"]=="full" and s["dimension"]=="overall"]
        self.assertTrue(all(s["stats"]["open_interest"] is None for s in full))
        self.assertTrue(all(s["stats"]["oi_missing"]==s["stats"]["contracts"] for s in full))
        self.assertTrue(all(d["oi"] is None for d in c["details"]))

    def test_changed_unselected_source_changes_semantic_identity(self):
        e,o,r=fixtures();a=capture(e,o)
        e[0]["volume"]=5
        b=capture(e,o)
        self.assertNotEqual(a["semantic_sha256"],b["semantic_sha256"])

    def test_conflicting_duplicate_rejected(self):
        e,o,r=fixtures();bad=dict(e[0]);bad["volume"]=9;e.append(bad)
        with self.assertRaisesRegex(ValueError,"conflicting_eod_duplicate"):capture(e,o)

    def test_no_reference_or_wrong_date_rejected(self):
        e,o,r=fixtures()
        for row in e:row["underlying_timestamp"]="2025-09-23T16:00:00-04:00"
        with self.assertRaisesRegex(ValueError,"invalid_greek_reference_time"):capture(e,o)
        with self.assertRaisesRegex(ValueError,"invalid_previous_session"):
            build_capture(SESSION,e,o,r,previous_session=SESSION)

    def test_source_order_does_not_change_semantic_replay(self):
        e,o,r=fixtures();a=capture(e,o)
        self.assertEqual(a["semantic_sha256"],capture(list(reversed(e)),list(reversed(o)))["semantic_sha256"])

    def test_iv_missing_quality_and_zero_trade_prices(self):
        e,o,r=fixtures()
        for row in e:row.update(count=0,implied_vol=None,bid="0")
        c=capture(e,o)
        self.assertTrue(all(d["iv_quality"]=="excluded" and d["trade_prices_state"]=="no_eligible_trade" for d in c["details"]))

    def test_future_trade_and_individual_model_reference_fail(self):
        e,o,_=fixtures()
        e[0]["timestamp"]="2026-09-24T01:00:00-04:00"
        with self.assertRaisesRegex(ValueError,"future_eod_time"):capture(e,o)
        e,o,_=fixtures()
        e[0]["underlying_timestamp"]="2026-09-24T01:00:00-04:00"
        with self.assertRaisesRegex(ValueError,"invalid_greek_reference_time"):capture(e,o)

    def test_old_trade_valid_but_wrong_report_or_missing_time_fails(self):
        e,o,_=fixtures()
        e[0]["timestamp"]="2026-09-22T15:00:00-04:00"
        self.assertEqual(capture(e,o)["session"],SESSION)
        e[0]["created"]="2026-09-22T19:00:00-04:00"
        with self.assertRaisesRegex(ValueError,"eod_report_session"):capture(e,o)
        e[0].pop("created");e[0].pop("timestamp")
        with self.assertRaisesRegex(ValueError,"missing_eod_time"):capture(e,o)

    def test_prices_only_reference_mismatch_fails(self):
        e,o,r=fixtures()
        for row in e:row.pop("underlying_price");row.pop("underlying_timestamp");row.pop("implied_vol")
        reference={"value":"100.1","observation":{"period_start":SESSION}}
        c=build_capture(SESSION,e,o,r,previous_session=PREVIOUS,spot_reference=reference)
        self.assertEqual(c["policy"]["spot_basis"],"retained_fmp_split_only_close_compatibility_proxy")
        reference["value"]="200"
        with self.assertRaisesRegex(ValueError,"fallback_reference_not_crosschecked|fallback_reference_basis_mismatch"):
            build_capture(SESSION,e,o,r,previous_session=PREVIOUS,spot_reference=reference)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp,self.store=temporary_store();self.addCleanup(self.temp.cleanup)
        self.store.initialize();self.c=capture()

    def test_atomic_publish_replay_and_A_B_A(self):
        a=self.store.publish(self.c)
        before=self.store.path.read_bytes()
        self.assertEqual(self.store.publish(self.c)["outcome"],"replay")
        self.assertEqual(before,self.store.path.read_bytes())
        e,o,_=fixtures();e[0]["volume"]=4
        b=self.store.publish(capture(e,o))
        c=self.store.publish(self.c)
        self.assertEqual([a["capture_id"],b["capture_id"],c["capture_id"]],[1,2,3])
        with self.store.read() as connection:
            self.assertEqual(connection.execute("SELECT predecessor_id FROM option_captures WHERE capture_id=3").fetchone()[0],2)

    def test_publication_failure_rolls_back_all_relations(self):
        from quant_data.options import store as module
        actual=module.packed;calls=0
        def fail(value):
            nonlocal calls
            calls+=1
            if calls==4:raise RuntimeError("injected_failure")
            return actual(value)
        with patch.object(module,"packed",side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,"injected_failure"):self.store.publish(self.c)
        self.assertEqual(self.store.status()["sessions"],0)
        self.assertEqual(self.store.status()["contracts"],0)

    def test_history_is_immutable_and_reader_is_query_only(self):
        self.store.publish(self.c)
        with self.store.read() as c:
            with self.assertRaises(sqlite3.OperationalError):c.execute("DELETE FROM option_captures")
        with sqlite3.connect(self.store.path) as c:
            with self.assertRaisesRegex(sqlite3.IntegrityError,"immutable"):c.execute("DELETE FROM option_captures")
        self.assertEqual(self.store.status()["sessions"],1)

    def test_registry_checksum_tamper_fails_before_store_write(self):
        p=self.store.root/"config/options_registry.json"
        d=json.loads(p.read_text());d["migration"]["sha256"]="0"*64;p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError,"checksum"):OptionsStore(self.store.root)

    def test_backup_and_health(self):
        self.store.publish(self.c)
        target=self.store.root/"backup.sqlite"
        self.assertEqual(self.store.backup(target)["integrity"],"ok")
        with sqlite3.connect("file:"+str(target)+"?mode=ro&immutable=1",uri=True) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM option_current").fetchone()[0],1)
        self.assertEqual(self.store.status()["foreign_key_violations"],0)
        with self.assertRaisesRegex(ValueError,"backup_target_exists"):self.store.backup(target)

    def test_restore_requires_absent_destination_and_preserves_records(self):
        self.store.publish(self.c)
        backup=self.store.root/"backup.sqlite";self.store.backup(backup)
        temp,other=temporary_store()
        with temp:
            self.assertEqual(other.restore_from_backup(backup)["sessions"],1)
            with self.assertRaisesRegex(ValueError,"restore_target_exists"):other.restore_from_backup(backup)

    def test_nonquiet_read_and_hardlink_alias_fail_closed(self):
        Path(str(self.store.path)+"-wal").write_bytes(b"pending")
        with self.assertRaisesRegex(ValueError,"not_quiet"):self.store.status()
        Path(str(self.store.path)+"-wal").unlink()
        import os
        os.link(self.store.path,self.store.path.parent/"other.sqlite")
        with self.assertRaisesRegex(ValueError,"hardlink"):OptionsStore(self.store.root)

class JobTests(unittest.TestCase):
    def test_calendar_holiday_and_previous_session(self):
        calendars={2015:[],2016:[{"date":"2016-01-01","type":"full_close"}]}
        dates=sessions_from_calendars(calendars,date(2016,1,4),date(2016,1,5))
        self.assertEqual(dates,[("2016-01-04","2015-12-31"),("2016-01-05","2016-01-04")])

    def test_attempted_unit_never_retried_without_retained_response(self):
        temp,store=temporary_store()
        with temp:
            job=SpyHistoryJob(store.root)
            class Failing:
                def __init__(self):self.calls=0
                def request(self,*args,**kwargs):
                    self.calls+=1;raise RuntimeError("failure")
            transport=Failing()
            with self.assertRaisesRegex(RuntimeError,"failure"):job.cached_get(transport,"calendar_year",year="2026")
            with self.assertRaisesRegex(RuntimeError,"previously_attempted"):job.cached_get(transport,"calendar_year",year="2026")
            self.assertEqual(transport.calls,1)

    def test_preconstructed_jobs_reload_attempts_under_lock(self):
        temp,store=temporary_store()
        with temp:
            work=store.root/".local/theta-spy-20260924";work.mkdir(parents=True)
            (work/"preflight.json").write_text(json.dumps({"data_requests":0,"response_bytes":0}))
            (work/"preflight-receipts.jsonl").write_text(json.dumps({"captured_at":datetime.now(timezone.utc).isoformat()})+"\n")
            for year in range(2015,2027):
                (work/f"calendar-{year}.json").write_text(json.dumps({
                    "rows":[{"date":f"{year}-01-01","type":"full_close"}],"receipt":{"response_bytes":0}}))
            (work/"spot-references.json").write_text("{}")
            class Denied:
                subscription_code=2
                calls=0
                def __init__(self,*a,**k):pass
                def request(self,*a,**k):
                    Denied.calls+=1
                    raise ProviderFailure("PERMISSION_DENIED",{})
                def close(self):pass
            first=SpyHistoryJob(store.root,transport_factory=Denied)
            second=SpyHistoryJob(store.root,transport_factory=Denied)
            with patch("quant_data.options.job.sessions_from_calendars",return_value=[(SESSION,PREVIOUS)]):
                with self.assertRaises(ProviderFailure):first.run()
                with self.assertRaisesRegex(RuntimeError,"previously_attempted"):second.run()
            self.assertEqual(Denied.calls,1)
            self.assertEqual(len((work/"attempts.jsonl").read_text().splitlines()),1)

    def test_committed_cleanup_reconciles_on_completed_resume(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();c=capture()
            job=SpyHistoryJob(store.root)
            a=job.stage/"a.json.gz";b=job.stage/"b.json.gz"
            a.write_bytes(b"source-a");b.write_bytes(b"source-b")
            job.prepare_cleanup(c,{a,b})
            self.assertFalse(job.reconcile_cleanup())
            self.assertTrue(a.exists())
            store.publish(c)
            a.unlink()  # interruption part way through cleanup
            job.status["state"]="completed";job.persist()
            resumed=SpyHistoryJob(store.root,transport_factory=lambda *a,**k:self.fail("unexpected provider"))
            self.assertEqual(resumed.run()["state"],"completed")
            self.assertFalse(b.exists())
            self.assertFalse((job.work/"pending-cleanup.json").exists())

    def test_changed_cleanup_source_is_retained(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();c=capture();job=SpyHistoryJob(store.root)
            source=job.stage/"source.json.gz";source.write_bytes(b"original")
            job.prepare_cleanup(c,{source});store.publish(c);source.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError,"cleanup_source_changed"):job.reconcile_cleanup()
            self.assertTrue(source.exists())

    def test_budget_limits_without_network(self):
        with self.assertRaisesRegex(RuntimeError,"request_cap"):Budget(max_requests=0).check()
        with self.assertRaisesRegex(RuntimeError,"response_byte_cap"):Budget(max_bytes=0).check()
        with self.assertRaisesRegex(RuntimeError,"duration_cap"):Budget(max_seconds=0).check()

class ProtobufTests(unittest.TestCase):
    def test_decimal_prices_and_null_scale_are_preserved(self):
        try:from thetadata._proto.endpoints_pb2 import DataTable,ResponseData,CompressionAlgo
        except ImportError:self.skipTest("pinned SDK only in isolated Theta runtime")
        table=DataTable();table.headers.extend(["bid","missing","volume"])
        row=table.data_table.add()
        value=row.values.add();value.price.value=5555;value.price.type=8
        value=row.values.add();value.price.value=0;value.price.type=0
        value=row.values.add();value.number=0
        message=ResponseData(compressed_data=table.SerializeToString())
        message.compression_description.algo=CompressionAlgo.NONE
        self.assertEqual(decode_stream([message]),[{"bid":"55.55","missing":None,"volume":0}])
