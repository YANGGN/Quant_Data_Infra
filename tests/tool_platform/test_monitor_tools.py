"""Offline contracts and immutable-store invariants for intraday agent tools."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.errors import (ValidationError, ResourceLimitError, StoreUnavailableError,
    CancellationError, DeadlineExceededError, RegistryError)
from quant_data.json_codec import dumps_strict
from quant_data.options.monitor.store import MonitorStore
from quant_data.registry import load_registry, stage5_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap, StoreWriteLock
from quant_data.tool_platform.context import FixedClock, Deadline, ExecutionBudget, CancellationToken
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.monitor_access import invoke
from quant_data.tool_platform.monitor_contracts import TOOLS, KINDS, parse
from quant_data.tool_platform.monitor_registry import predecessor_profile, PREDECESSOR_SHA256
from quant_data.tool_platform.generate import generate

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-29T15:00:00Z"
CLOCK = FixedClock(Decimal("100"), NOW)


def records(result, kind):
    values = [{f["name"]: f["value"] for f in r["fields"]}
              for r in result["records"] if r["record_type"] == kind]
    return [{k[:-5] if k.endswith("_json") else k:
             json.loads(v) if k.endswith("_json") else v for k, v in row.items()} for row in values]


def observation(symbol="SPY", stamp="2026-09-29T14:55:00+00:00", **values):
    out = dict(symbol=symbol, session="2026-09-29", captured_at=stamp, cadence_minutes=5,
        coverage="complete", quality_flags=["baseline_warmup"], status="warming_up",
        call_volume=0, put_volume=20, interval_volume=None, interval_start=None,
        open_interest=1000, oi_effective_date="2026-09-28", atm_iv_30=.2,
        relative_volume=None, baseline_sessions=2, contract_count=2, active_contract_count=2,
        leaders=[dict(expiration="2026-10-02", strike=700, right="P", volume=20, share=1)],
        _fingerprint="f"*64)
    out.update(values)
    return out


class MonitorToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry("config/system_registry.json", project_root=ROOT, environment={})

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="monitor-agent-tools-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/"config").mkdir()
        (self.root/"config/options_monitor_universe.json").write_text(json.dumps(dict(schema_version=1,
            members=[dict(symbol=s, name=s, group="equity" if s=="AAPL" else "market", cadence_minutes=5)
                     for s in ("SPY", "QQQ", "AAPL")])))
        self.store = MonitorStore(self.root)
        self.store.initialize()
        self.store.publish(observation(stamp="2026-09-29T14:50:00+00:00", call_volume=10))
        self.store.publish(observation())
        self.registry_at_root = replace(self.registry, project_root=self.root)
        stores = StoreMap.from_mapping({r: self.root/"data"/(r+".sqlite") for r in ("market","macro","company","news")})
        self.app = Stage1Application(stores, self.registry_at_root)
        clock = patch("quant_data.boundary.dispatcher.HostClock", return_value=CLOCK)
        clock.start()
        self.addCleanup(clock.stop)

    def call(self, index, args):
        before = self.store.path.read_bytes()
        connect = sqlite3.connect
        def readonly(path, *a, **kw):
            self.assertTrue(str(path).startswith("file:/proc/self/fd/"), path)
            self.assertIn("mode=ro&immutable=1", str(path))
            return connect(path, *a, **kw)
        with patch("sqlite3.connect", side_effect=readonly), patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            value = self.app.dispatcher.call(TOOLS[index], args, tool_version="1.0.0")
        validate_schema(value, self.registry.tool(TOOLS[index])["output_schema"])
        result = json.loads(dumps_strict(value))
        self.assertEqual(self.store.path.read_bytes(), before)
        for suffix in ("-wal","-shm","-journal"):
            self.assertFalse(Path(str(self.store.path)+suffix).exists())
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(records(result,"monitor_source")[0]["provider_requests"],0)
        self.assertEqual(result["research_contract"]["contract"]["point_in_time_status"],"not_established")
        self.assertFalse(result["truncation"]["applied"])
        return result

    def test_snapshot_latest_mixed_scope_and_preserved_nulls(self):
        self.store.publish(observation("AAPL", call_volume=120, put_volume=None, coverage="incomplete",
            quality_flags=["incomplete_volume"]))
        result = self.call(0, dict(symbols=["SPY","AAPL","QQQ","ZZZZ"]))
        rows = records(result,"monitor_observation")
        self.assertEqual([r["symbol"] for r in rows], ["AAPL","SPY"])
        spy = rows[1]
        self.assertEqual(spy["call_volume"],0)
        self.assertIsNone(spy["interval_volume"])
        self.assertIsNone(spy["relative_volume"])
        self.assertEqual(spy["leaders"][0]["right"],"P")
        self.assertEqual(spy["oi_effective_date"],"2026-09-28")
        self.assertEqual(spy["age_seconds"],300)
        self.assertEqual(spy["freshness_at_cutoff"],"within_cadence")
        self.assertEqual(rows[0]["coverage"],"incomplete")
        self.assertIsNone(rows[0]["put_volume"])
        self.assertEqual([r["reason"] for r in records(result,"monitor_missing")],
            ["no_saved_observation_at_cutoff","outside_current_monitor_universe"])

    def test_history_order_offsets_microseconds_and_cutoff(self):
        self.store.publish(observation(stamp="2026-09-29T10:52:00-04:00", call_volume=5))
        self.store.publish(observation(stamp="2026-09-29T14:55:00.000001Z", call_volume=99))
        args = dict(symbol="SPY", start_date="2026-09-29", end_date="2026-09-29", as_of="2026-09-29T10:55:00-04:00")
        rows = records(self.call(1,args),"monitor_observation")
        self.assertEqual([r["call_volume"] for r in rows],[10,5,0])
        snap = records(self.call(0,dict(symbols=["SPY"],as_of=args["as_of"])),"monitor_observation")[0]
        self.assertEqual(snap["call_volume"],0)
        latest = records(self.call(0,dict(symbols=["SPY"])),"monitor_observation")[0]
        self.assertEqual(latest["call_volume"],99)

    def test_previous_session_snapshot_is_visibly_old_and_optional_session_exact(self):
        self.store.publish(observation("AAPL",stamp="2026-09-28T19:55:00Z",session="2026-09-28"))
        row=records(self.call(0,dict(symbols=["AAPL"])),"monitor_observation")[0]
        self.assertEqual(row["session"],"2026-09-28")
        self.assertGreater(row["age_seconds"],60000)
        self.assertEqual(row["freshness_at_cutoff"],"older_than_cadence")
        missing=self.call(0,dict(symbols=["AAPL"],session="2026-09-29"))
        self.assertEqual(missing["status"],"not_established")
        self.assertFalse(records(missing,"monitor_observation"))

    def test_future_observations_are_not_visible(self):
        self.store.publish(observation(stamp="2026-09-29T15:00:00.000001Z",call_volume=999))
        row=records(self.call(0,dict(symbols=["SPY"])),"monitor_observation")[0]
        self.assertEqual(row["call_volume"],0)

    def test_strict_inputs_fail_before_store_access(self):
        bad = [
            (0,dict(symbols=[])), (0,dict(symbols=["SPY"]*51)),
            (0,dict(symbols=["SPY","SPY"])), (0,dict(symbols=["spy"])),
            (0,dict(symbols=["SPY"],database_path="/tmp/other")),
            (0,dict(symbols=["SPY"],sql="SELECT 1")),
            (0,dict(symbols=["SPY"],session="2026-02-30")),
            (0,dict(symbols=["SPY"],as_of="2026-09-30T00:00:00Z")),
            (0,dict(symbols=["SPY"],as_of="2026-09-29T00:00:00")),
            (0,dict(symbols=["SPY"],as_of="0001-01-01T00:00:00Z")),
            (1,dict(symbol="SPY",start_date="2026-09-29",end_date="2026-09-28")),
            (1,dict(symbol="SPY",start_date="2026-09-01",end_date="2026-09-08")),
        ]
        with patch("quant_data.tool_platform.monitor_access.MonitorStore",side_effect=AssertionError("no read")):
            for index,args in bad:
                with self.subTest(args=args),self.assertRaises((ValidationError,ResourceLimitError)):
                    self.app.dispatcher.call(TOOLS[index],args,tool_version="1.0.0")

    def test_history_limits_are_inclusive_and_empty_is_explicit(self):
        result=self.call(1,dict(symbol="SPY",start_date="2026-09-23",end_date="2026-09-29"))
        self.assertEqual(len(records(result,"monitor_observation")),2)
        for symbol in ("QQQ","ZZZZ"):
            result=self.call(1,dict(symbol=symbol,start_date="2026-09-23",end_date="2026-09-29"))
            self.assertEqual(result["status"],"not_established")

    def test_missing_source_is_not_created_or_fallen_back(self):
        self.store.path.unlink()
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")
        self.assertFalse(self.store.path.exists())

    def test_journals_schema_and_aliases_fail_closed(self):
        for suffix in ("-wal","-journal"):
            path=Path(str(self.store.path)+suffix);path.write_bytes(b"unsafe")
            try:
                with self.assertRaises(StoreUnavailableError):
                    self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")
            finally:path.unlink()
        self.store.write("UPDATE metadata SET schema_sha256='invalid'")
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")

    def test_symlink_and_hardlink_fail_closed(self):
        original=self.store.path.with_name("saved.sqlite")
        self.store.path.rename(original)
        for mode in ("symlink_to","hardlink_to"):
            getattr(self.store.path,mode)(original)
            try:
                with self.assertRaises(StoreUnavailableError):
                    self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")
            finally:self.store.path.unlink()

    def test_saved_identity_mismatch_and_invalid_json_fail_closed(self):
        for payload in ('{"symbol":"QQQ"}', '{"call_volume":NaN}', '[]'):
            self.store.write("UPDATE observations SET payload=?",(payload,))
            with self.assertRaises(StoreUnavailableError):
                self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")

    def test_private_and_unknown_fields_do_not_cross_boundary(self):
        self.store.publish(observation("AAPL",internal_secret="DO_NOT_EXPOSE",
            leaders=[dict(expiration="2026-10-02",strike=100,right="C",volume=1,share=1,secret="DO_NOT_EXPOSE")]))
        result=self.call(0,dict(symbols=["AAPL"]))
        self.assertNotIn("DO_NOT_EXPOSE",json.dumps(result))
        row=records(result,"monitor_observation")[0]
        self.assertEqual(len(row["payload_sha256"]),64)
        self.assertEqual(len(row["observation_id"]),64)

    def test_payload_and_history_bounds_fail_without_truncation(self):
        self.store.write("UPDATE observations SET payload=?",("x"*65537,))
        with self.assertRaises(ResourceLimitError):
            self.app.dispatcher.call(TOOLS[0],dict(symbols=["SPY"]),tool_version="1.0.0")
        # Cheap synthetic rows exercise admission before decoding.
        with sqlite3.connect(self.store.path) as c:
            c.execute("DELETE FROM observations")
            for i in range(1001):
                stamp=(datetime.fromisoformat("2026-09-29T13:30:00+00:00")+timedelta(seconds=i)).isoformat()
                c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                    ("2026-09-29","SPY",stamp,stamp,5,"f"*64,"{}"))
        with self.assertRaises(ResourceLimitError):
            self.app.dispatcher.call(TOOLS[1],dict(symbol="SPY",start_date="2026-09-29",end_date="2026-09-29"),tool_version="1.0.0")

    def test_budget_cancellation_deadline_and_progress_interrupt(self):
        context=self.app.dispatcher._stage5_context(TOOLS[0],self.registry.tool(TOOLS[0]))
        args=parse(KINDS[TOOLS[0]],dict(symbols=["SPY"]))
        for changes,error in (
            (dict(cancellation=CancellationToken(True)),CancellationError),
            (dict(deadline=Deadline(Decimal("99"))),DeadlineExceededError),
            (dict(budget=ExecutionBudget(max_operations=1)),ResourceLimitError)):
            with self.subTest(error=error),self.assertRaises(error):
                invoke(TOOLS[0],args,replace(context,**changes),self.registry_at_root)

    def test_reader_takes_existing_physical_store_lock(self):
        with patch("quant_data.tool_platform.monitor_access.StoreWriteLock", wraps=StoreWriteLock) as lock:
            self.call(0,dict(symbols=["SPY"]))
        lock.assert_called_once_with(self.store.path,timeout_seconds=1)

    def test_cli_discovery_versions_receipt_and_errors(self):
        request=dict(api_version="1.0",tool=TOOLS[0],tool_version="1.0.0",arguments=dict(symbols=["SPY"]))
        stdout,stderr=io.StringIO(),io.StringIO()
        code=run(["call"],stdin=io.BytesIO(json.dumps(request).encode()),stdout=stdout,stderr=stderr,application=self.app)
        self.assertEqual(code,0,stdout.getvalue())
        result=json.loads(stdout.getvalue())
        self.assertTrue(result["receipt"])
        self.assertEqual(stderr.getvalue(),"")
        http=self.app.handle("POST","/api/agent-tools/call",headers={"Content-Type":"application/json"},body=json.dumps(request).encode())
        self.assertEqual(result["result"],json.loads(http.body)["result"])
        for name in TOOLS:
            found=next(t for t in self.app.dispatcher.manifest()["tools"] if t["name"]==name)
            self.assertFalse(found["live_capability"]["possible"])
            self.assertEqual(found["stores"],[])
            self.assertEqual(found["version"],"1.0.0")

    def test_exact_predecessor_contracts_and_generation(self):
        old=predecessor_profile(self.registry)
        self.assertEqual(old.source_sha256,PREDECESSOR_SHA256)
        self.assertEqual(old.tool_version_policies,self.registry.tool_version_policies)
        self.assertEqual(old.datasets,self.registry.datasets)
        for entry in old.tools:self.assertEqual(entry,self.registry.tool(entry["id"]))
        self.assertEqual(stage5_registry_profile(self.registry).registry_version,"2.3.0")
        generate(ROOT,check=True)
        tampered=copy.deepcopy(dict(self.registry.raw))
        tampered["tools"][-1]["description"]="tampered"
        with self.assertRaises(RegistryError):
            predecessor_profile(replace(self.registry,raw=tampered))
