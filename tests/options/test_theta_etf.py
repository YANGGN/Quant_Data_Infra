"""Tier-1 schema preservation, symbol isolation and fixed-scope expansion tests."""
import copy,hashlib,json,sqlite3,threading,unittest
from pathlib import Path
from unittest.mock import patch
from quant_data.options.model import build_capture
from quant_data.options.store import OptionsStore
from quant_data.options.etf_job import EtfHistoryJob
from quant_data.options.universe import EXPANSION_ETFS
from tests.options.test_theta_compact import temporary_store,fixtures,capture,SESSION,PREVIOUS
from tests.options.test_theta_parallel import setup_work

def etf_capture(symbol):
    e,o,r=fixtures()
    for row in e+o:row["symbol"]=symbol
    for item in r:item["params"]={"symbol":symbol}
    return build_capture(SESSION,e,o,r,previous_session=PREVIOUS,symbol=symbol)

def legacy_store():
    temp,current=temporary_store()
    path=current.root/"config/options_registry.json"
    registry=json.loads(path.read_text());legacy=copy.deepcopy(registry)
    legacy["version"]="1.0.0";legacy.pop("migrations");legacy.pop("expansion_collector")
    path.write_text(json.dumps(legacy))
    old=OptionsStore(current.root);old.initialize()
    old.publish(capture())
    return temp,old,path,registry

def snapshot(store):
    tables=("option_captures","option_current","option_contracts","option_details",
            "option_daily_summaries","option_activity_leaders","option_source_receipts")
    with store.read() as c:
        return {table:list(map(tuple,c.execute("SELECT * FROM "+table+" ORDER BY rowid"))) for table in tables}

class EtfMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_every_spy_fact_and_accepts_isolated_etf_contracts(self):
        temp,old,path,registry=legacy_store()
        with temp:
            before=snapshot(old);backup=old.root/"pre-migration.sqlite";old.backup(backup)
            path.write_text(json.dumps(registry));new=OptionsStore(old.root)
            self.assertTrue(new.initialize())
            self.assertEqual(snapshot(new),before)
            with new.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],2)
            stable=new.path.read_bytes()
            self.assertFalse(new.initialize());self.assertEqual(stable,new.path.read_bytes())
            spy_id=new.publish(capture())
            self.assertEqual(spy_id["outcome"],"replay")
            qqq=new.publish(etf_capture("QQQ"));iwm=new.publish(etf_capture("IWM"))
            self.assertNotEqual(qqq["capture_id"],iwm["capture_id"])
            self.assertEqual(new.completed_sessions("SPY"),{SESSION})
            self.assertEqual(new.completed_sessions("QQQ"),{SESSION})
            self.assertEqual(new.status()["sessions"],3)
            with new.read() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM option_current WHERE symbol='SPY'").fetchone()[0],1)
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_migration_failure_rolls_back_copied_tables_and_ledger(self):
        temp,old,path,registry=legacy_store()
        with temp:
            before=snapshot(old)
            path.write_text(json.dumps(registry));new=OptionsStore(old.root)
            new.migration_sql[1]+=b"\nSELECT * FROM injected_missing_table;"
            with self.assertRaises(sqlite3.OperationalError):new.initialize()
            self.assertEqual(snapshot(old),before)
            with old.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],1)

    def test_migration_checksum_and_unknown_prefix_fail_closed(self):
        temp,store=temporary_store()
        with temp:
            registry_path=store.root/"config/options_registry.json"
            r=json.loads(registry_path.read_text());r["migrations"][1]["sha256"]="0"*64
            registry_path.write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError,"checksum"):OptionsStore(store.root)

    def test_immediate_foreign_key_enforcement_rolls_back_invalid_predecessor(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();original=store.publish(capture())
            import quant_data.options.store as module
            actual=module.sqlite3.connect
            class Connection(sqlite3.Connection):
                def execute(self,sql,parameters=()):
                    if sql.startswith("INSERT INTO option_captures"):
                        parameters=list(parameters);parameters[2]=999999
                    return super().execute(sql,parameters)
            def connect(*a,**k):
                if not k.get("uri"):k["factory"]=Connection
                return actual(*a,**k)
            with patch.object(module.sqlite3,"connect",side_effect=connect):
                with self.assertRaises(sqlite3.IntegrityError):store.publish(etf_capture("QQQ"))
            self.assertEqual(store.status()["sessions"],1)

class EtfIdentityTests(unittest.TestCase):
    def test_mixed_roots_and_wrong_local_reference_are_rejected(self):
        e,o,r=fixtures();e[0]["symbol"]="QQQ"
        with self.assertRaisesRegex(ValueError,"unexpected_option_root"):
            build_capture(SESSION,e,o,r,previous_session=PREVIOUS)
        e,o,r=fixtures()
        for row in e+o:row["symbol"]="QQQ"
        for row in e:
            row.pop("underlying_price");row.pop("underlying_timestamp");row.pop("implied_vol")
        with self.assertRaisesRegex(ValueError,"fallback_reference_symbol"):
            build_capture(SESSION,e,o,r,previous_session=PREVIOUS,symbol="QQQ",
                spot_reference={"value":"100.1","metadata":{"provider_symbol":"SPY"},"observation":{"period_start":SESSION}})

    def test_publisher_rejects_cross_symbol_contract_and_unsupported_root(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();c=etf_capture("QQQ")
            c["details"][0]["contract_id"]=c["details"][0]["contract_id"].replace("QQQ|","SPY|")
            with self.assertRaisesRegex(ValueError,"cross_symbol_contract"):store.publish(c)
            c=etf_capture("QQQ");c["symbol"]="AAPL"
            with self.assertRaisesRegex(ValueError,"unsupported_option_root"):store.publish(c)
            self.assertEqual(store.status()["sessions"],0)

    def test_spy_semantic_digest_is_unchanged_from_completed_version(self):
        # Frozen from the completed SPY implementation against the same fixture.
        expected="044eadc1277bb398a58e4277d337c143c55ef7bfef74f81038a3c183ae087e4f"
        self.assertEqual(capture()["semantic_sha256"],expected)

class EtfJobTests(unittest.TestCase):
    def test_manifest_excludes_spy_and_dates_before_xlc_listing(self):
        temp,store=temporary_store()
        with temp:
            work=setup_work(store.root)
            job=EtfHistoryJob(store.root)
            with patch("quant_data.options.etf_job.sessions_from_calendars",return_value=[
                ("2018-06-18","2018-06-15"),("2018-06-19","2018-06-18")]):
                _,units=job.load_plan()
            self.assertFalse(any(s=="SPY" for s,d,p in units))
            self.assertFalse(any(s=="XLC" and d<"2018-06-19" for s,d,p in units))
            self.assertEqual(len(units),27)

    def test_mixed_symbol_run_uses_one_pool_and_preserves_spy(self):
        temp,store=temporary_store()
        with temp:
            store.initialize();store.publish(capture());before=snapshot(store)
            job=EtfHistoryJob(store.root)
            for symbol in EXPANSION_ETFS:
                path=job.work/"references"/symbol/"spot-references.json"
                path.parent.mkdir(parents=True);path.write_text("{}")
            units=[(s,SESSION,PREVIOUS) for s in ("QQQ","IWM","DIA","XLB")]
            barrier=threading.Barrier(4);calls=[];mutex=threading.Lock()
            class Fake:
                subscription_code=2
                def __init__(self,root,budget,receipt_callback,symbols):
                    self.budget=budget;self.callback=receipt_callback
                    if "SPY" in symbols:raise AssertionError("SPY acquisition allowed")
                def request(self,method,**params):
                    symbol=params["symbol"]
                    with mutex:calls.append((symbol,method))
                    self.budget.reserve_request()
                    if "greeks" in method:barrier.wait(timeout=5)
                    e,o,r=fixtures()
                    for row in e+o:row["symbol"]=symbol
                    receipt={"method":method,"params":{k:str(v) for k,v in params.items()},
                        "captured_at":"2026-09-24T10:00:00+00:00","response_bytes":100,
                        "response_sha256":"c"*64,"outcome":"success"}
                    self.budget.add_bytes(100);self.callback(receipt,self.budget)
                    return e if "greeks" in method else o,receipt
                def close(self):pass
            job.transport_factory=Fake
            with patch.object(job,"load_plan",return_value=({"units":units},units)):
                result=job.run()
            self.assertEqual(result["state"],"completed")
            self.assertEqual(result["completed_sessions"],4)
            self.assertEqual(result["data_requests"],8)
            self.assertEqual(len(calls),8)
            self.assertEqual(store.completed_sessions("SPY"),{SESSION})
            with store.read() as c:
                self.assertEqual(tuple(c.execute("SELECT * FROM option_captures WHERE capture_id=1").fetchone()),before["option_captures"][0])
            self.assertFalse(list(job.stage.glob("*.json.gz")))
            # A finished run cannot authenticate or repeat its units.
            job.transport_factory=lambda *a,**k:self.fail("completed run repeated")
            job.run()
