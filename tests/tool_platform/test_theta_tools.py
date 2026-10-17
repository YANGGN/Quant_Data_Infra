"""Isolated optional-store tests for the six Theta agent contracts."""
import copy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zlib

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.errors import ValidationError, ResourceLimitError, StoreUnavailableError, CancellationError, DeadlineExceededError
from quant_data.options.model import build_capture
from quant_data.options.monitor.history_model import project_daily
from quant_data.options.tool_registry import predecessor_profile
from quant_data.registry import load_registry, CANONICAL_REGISTRY_PATH, stage5_registry_profile
from quant_data.schema import validate_schema
from quant_data.json_codec import dumps_strict
from quant_data.stores import StoreMap, StoreWriteLock
from quant_data.tool_platform.context import FixedClock, Deadline, ExecutionBudget, CancellationToken
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.theta_contracts import TOOLS, KINDS, parse
from quant_data.tool_platform.theta_access import Reader, invoke
from quant_data.tool_platform.generate import generate
from tests.options.test_theta_compact import temporary_store

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-26T23:00:00+00:00"
CLOCK = FixedClock(Decimal("100"), NOW)


def capture(symbol="SPY", session="2026-09-23", factor=1, iv=".2", stamp="2026-09-24T10:00:00+00:00", dtes=(7,30,90), missing_volume=False, zero_quotes=False):
    eod, oi = [], []
    day = date.fromisoformat(session)
    for dte in dtes:
        for strike in (90, 95, 100, 105, 110):
            for right in ("CALL", "PUT"):
                identity = dict(symbol=symbol, expiration=(day+timedelta(days=dte)).isoformat(), strike=str(strike), right=right)
                eod.append(dict(identity, timestamp=session+"T16:00:00-04:00",
                    underlying_price="100", underlying_timestamp=session+"T16:00:00-04:00",
                    bid="0" if zero_quotes else "2", ask="2.02", bid_size=5, ask_size=7,
                    volume=None if missing_volume else factor*(100 if right=="CALL" else 200), count=10,
                    implied_vol=str(Decimal(iv)+(Decimal(".04") if right=="PUT" and strike==95 else 0)),
                    iv_error=".001", delta=".5", gamma=".01", vega=".1", theta="-.01"))
                oi.append(dict(identity, timestamp=session+"T06:30:00-04:00", open_interest=1000))
    receipt=dict(method="option_history_greeks_eod", params={}, captured_at=stamp, response_sha256="a"*64,
                 response_bytes=100, outcome="success")
    return build_capture(session,eod,oi,[receipt],previous_session=(day-timedelta(days=1)).isoformat(),symbol=symbol)


def fields(record):
    result = {f["name"]:f["value"] for f in record["fields"]}
    return {k[:-5] if k.endswith("_json") else k:json.loads(v) if k.endswith("_json") else v for k,v in result.items()}


def records(result, kind):
    return [fields(r) for r in result["records"] if r["record_type"]==kind]


class ThetaToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(CANONICAL_REGISTRY_PATH,project_root=ROOT,environment={})

    def setUp(self):
        temporary,self.store = temporary_store()
        self.addCleanup(temporary.cleanup)
        self.root = self.store.root
        self.store.initialize()
        for day, factor, iv in (("2026-09-21",1,".1"),("2026-09-22",2,".2"),("2026-09-23",3,".3")):
            self.store.publish(capture(session=day,factor=factor,iv=iv))
        self.store.publish(capture(symbol="QQQ",session="2026-09-21"))
        self.store.publish(capture(symbol="QQQ",session="2026-09-23",factor=4))
        registry = replace(self.registry,project_root=self.root)
        stores=StoreMap.from_mapping({r:self.root/"data"/(r+".sqlite") for r in ("market","macro","company","news")})
        self.app=Stage1Application(stores,registry)
        self.clock_patch=patch("quant_data.boundary.dispatcher.HostClock",return_value=CLOCK)
        self.clock_patch.start();self.addCleanup(self.clock_patch.stop)

    def call(self, index, args):
        before=hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        connect=sqlite3.connect
        def readonly(path,*a,**kw):
            self.assertTrue(str(path).startswith("file:/proc/self/fd/"),path)
            self.assertIn("mode=ro&immutable=1",str(path))
            return connect(path,*a,**kw)
        with patch("sqlite3.connect",side_effect=readonly), patch("socket.create_connection",side_effect=AssertionError("no network")):
            result=self.app.dispatcher.call(TOOLS[index],args,tool_version="1.0.0")
        validate_schema(result,self.registry.tool(TOOLS[index],"1.0.0")["output_schema"])
        result=json.loads(dumps_strict(result))
        self.assertEqual(before,hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(self.store.path)+"-wal").exists())
        self.assertFalse(Path(str(self.store.path)+"-journal").exists())
        self.assertNotIn(str(self.root),json.dumps(result))
        source=records(result,"theta_source")[0]
        self.assertEqual(source["provider"],"thetadata")
        self.assertEqual(source["store_role"],"options")
        self.assertEqual(source["source_store"],"data/options.sqlite")
        self.assertEqual(source["provider_requests"],0)
        self.assertEqual(result["research_contract"]["contract"]["point_in_time_status"],"not_established")
        return result

    def test_coverage_gaps_are_explicit_and_calendar_honest(self):
        result=self.call(0,dict(symbols=["SPY","QQQ","IWM"],start_date="2026-09-20",end_date="2026-09-24"))
        rows={r["symbol"]:r for r in records(result,"theta_coverage")}
        self.assertEqual(rows["SPY"]["present_sessions"],3)
        self.assertEqual(rows["QQQ"]["missing_observed_sessions"],["2026-09-22"])
        self.assertEqual(rows["SPY"]["unobserved_weekdays_calendar_unverified"],["2026-09-24"])
        self.assertEqual(rows["IWM"]["status"],"no_saved_captures")
        self.assertEqual(rows["SPY"]["latest_session"],"2026-09-23")

    def test_history_reuses_daily_projection_and_keeps_oi_date(self):
        result=self.call(1,dict(symbol="SPY",start_date="2026-09-21",end_date="2026-09-23"))
        days=records(result,"theta_daily")
        self.assertEqual(len(days),3)
        self.assertEqual([d["total_volume"] for d in days],[4500,9000,13500])
        self.assertEqual(days[-1]["put_call_ratio"],2)
        self.assertEqual(days[-1]["oi_effective_date"],"2026-09-22")
        self.assertAlmostEqual(days[-1]["atm_iv_30"],.3)
        c=capture(factor=3,iv=".3")
        expected=project_daily(dict(capture_id=3,symbol="SPY",session_date="2026-09-23",semantic_sha256=c["semantic_sha256"],
                              captured_at=c["captured_at"],underlying_price=c["spot"]),c["summaries"],c["details"])
        for key,value in expected.items():
            self.assertEqual(days[-1][key],value)
        self.assertEqual(days[-1]["zero_dte_volume_share"],0)

    def test_snapshot_coherent_populations_and_leaders(self):
        result=self.call(2,dict(symbol="SPY",session="2026-09-23"))
        cells=records(result,"theta_summary_cell")
        self.assertEqual(len(cells),102)
        self.assertEqual({r["population"] for r in cells},{"full","selected","remainder"})
        self.assertEqual({r["capture_id"] for r in cells},{3})
        self.assertLessEqual(len(records(result,"theta_activity_leader")),40)
        self.assertIsNotNone(records(result,"theta_capture")[0]["coverage"])

    def test_selected_contract_filter_is_numeric_and_not_a_chain(self):
        result=self.call(5,dict(symbol="SPY",session="2026-09-23",right="P",strike_min="95",strike_max="105",expiration="2026-10-23"))
        rows=records(result,"theta_selected_contract")
        self.assertEqual([d["strike"] for d in rows],["95","100","105"])
        self.assertTrue(all(d["right"]=="P" for d in rows))
        self.assertEqual(rows[1]["eod_greeks"]["gamma"],".01")
        self.assertTrue(rows[1]["reasons"])
        self.assertFalse(records(result,"theta_source")[0]["is_full_chain"])

    def test_volatility_prior_only_rank_percentile_and_skew(self):
        result=self.call(3,dict(symbol="SPY",session="2026-09-23",lookback_sessions=2,min_observations=2))
        row=records(result,"theta_volatility")[0]
        self.assertEqual(row["valid_iv_observations"],2)
        self.assertAlmostEqual(row["iv_rank_30"],200)
        self.assertEqual(row["iv_percentile_30"],100)
        self.assertEqual(row["term_slope_30_minus_7"],0)
        self.assertAlmostEqual(row["skew_95p_minus_105c"],.04)
        self.assertEqual(row["skew_dte"],30)
        self.assertEqual(row["interpolation"]["30"]["method"],"exact")
        self.assertEqual([r["session_date"] for r in row["baseline_lineage"]],["2026-09-21","2026-09-22"])

    def test_screen_common_session_insufficient_baseline_and_gaps(self):
        result=self.call(4,dict(symbols=["SPY","QQQ","IWM"],session="2026-09-23",lookback_sessions=2,min_observations=2))
        rows=records(result,"theta_activity")
        self.assertEqual(rows[0]["symbol"],"SPY")
        self.assertEqual(rows[0]["relative_volume"],2)
        qqq=next(r for r in rows if r["symbol"]=="QQQ")
        self.assertIsNone(qqq["relative_volume"])
        self.assertIsNone(qqq["put_call_change"])
        self.assertEqual(qqq["missing_baseline_sessions"],["2026-09-22"])
        self.assertIsNone(qqq["rank"])
        self.assertEqual(next(r for r in rows if r["symbol"]=="IWM")["status"],"missing_session")
        self.assertEqual({r["session"] for r in rows},{"2026-09-23"})

    def test_capture_cutoff_retains_prior_revision_and_exact_microseconds(self):
        self.store.publish(capture(factor=7,iv=".4",stamp="2026-09-25T10:00:00.000001+00:00"))
        args=dict(symbol="SPY",session="2026-09-23")
        earlier=self.call(2,dict(args,as_of="2026-09-25T10:00:00Z"))
        latest=self.call(2,args)
        self.assertEqual(records(earlier,"theta_daily")[0]["total_volume"],13500)
        self.assertEqual(records(latest,"theta_daily")[0]["total_volume"],31500)
        empty=self.call(2,dict(args,as_of="2026-09-24T09:59:59Z"))
        self.assertEqual(empty["status"],"not_established")

    def test_no_future_source_data_with_offset_cutoff(self):
        args=dict(symbol="SPY",session="2026-09-23",as_of="2026-09-24T05:00:00-04:00")
        self.assertEqual(self.call(2,args)["status"],"not_established")
        with self.assertRaises(ValidationError):
            self.call(2,dict(args,as_of="2026-10-01T00:00:00Z"))

    def test_bad_args_reject_before_options_open(self):
        args=dict(symbol="SPY",session="2026-09-23")
        bad=[dict(args,path="/tmp/a"),dict(args,sql="select 1"),dict(args,symbol="AAPL"),
             dict(args,as_of="2026-09-23"),dict(args,session="2026-02-30")]
        with patch("quant_data.tool_platform.theta_access.OptionsStore",side_effect=AssertionError("No read")):
            for value in bad:
                with self.assertRaises(ValidationError):
                    self.app.dispatcher.call(TOOLS[2],value,tool_version="1.0.0")
            with self.assertRaises(ResourceLimitError):
                self.app.dispatcher.call(TOOLS[1],dict(symbol="SPY",start_date="2020-01-01",end_date="2026-09-23"),tool_version="1.0.0")
            for extra in (dict(lookback_sessions=True),dict(lookback_sessions=2,min_observations=3)):
                with self.assertRaises(ValidationError):
                    self.app.dispatcher.call(TOOLS[3],dict(args,**extra),tool_version="1.0.0")

    def test_missing_or_busy_source_never_falls_back(self):
        for suffix in ("-wal","-journal"):
            path=Path(str(self.store.path)+suffix);path.write_bytes(b"unsafe")
            try:
                with self.assertRaises(StoreUnavailableError):
                    self.app.dispatcher.call(TOOLS[2],dict(symbol="SPY",session="2026-09-23"),tool_version="1.0.0")
            finally:
                path.unlink()
        self.store.path.rename(self.store.path.with_name("market.sqlite"))
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOLS[2],dict(symbol="SPY",session="2026-09-23"),tool_version="1.0.0")

    def test_cli_http_discovery_and_receipt(self):
        request=dict(api_version="1.0",tool=TOOLS[2],tool_version="1.0.0",arguments=dict(symbol="SPY",session="2026-09-23"))
        stdout,stderr=io.StringIO(),io.StringIO()
        code=run(["call"],stdin=io.BytesIO(json.dumps(request).encode()),stdout=stdout,stderr=stderr,application=self.app)
        self.assertEqual(code,0,stdout.getvalue())
        result=json.loads(stdout.getvalue())
        http=self.app.handle("POST","/api/agent-tools/call",headers={"Content-Type":"application/json"},body=json.dumps(request).encode())
        self.assertEqual(result["result"],json.loads(http.body)["result"])
        self.assertEqual(stderr.getvalue(),"")
        manifest=self.app.dispatcher.manifest()
        for tool in TOOLS:
            declaration=next(t for t in manifest["tools"] if t["name"]==tool)
            self.assertEqual(declaration["version"],"1.0.0")
            self.assertFalse(declaration["live_capability"]["possible"])
            self.assertEqual(declaration["stores"],[])
            validate_schema(self.registry.tool(tool)["examples"][0],self.registry.tool(tool)["input_schema"])

    def test_exact_predecessor_and_generated_contracts(self):
        from quant_data.tool_platform.retained_research_registry import predecessor_profile as retained_predecessor
        current=retained_predecessor(self.registry)
        previous=predecessor_profile(current)
        self.assertEqual(previous.source_sha256,"69df849df7edca58a724021bd36661c6e7b0037d2980fe37f8da11645d4921ad")
        for tool in previous.tools:
            self.assertEqual(tool,current.tool(tool["id"]))
        self.assertEqual(previous.tool_version_policies,current.tool_version_policies)
        self.assertEqual(previous.datasets,current.datasets)
        self.assertEqual(stage5_registry_profile(self.registry).registry_version,"2.3.0")
        generate(ROOT,check=True)

    def test_resource_deadline_cancellation_and_bounded_decode(self):
        name=TOOLS[2]
        context=self.app.dispatcher._stage5_context(name,self.registry.tool(name))
        args=parse(KINDS[name],dict(symbol="SPY",session="2026-09-23"))
        for changed,error in ((dict(cancellation=CancellationToken(True)),CancellationError),
                              (dict(deadline=Deadline(Decimal("99"))),DeadlineExceededError),
                              (dict(budget=ExecutionBudget(max_operations=1)),ResourceLimitError)):
            with self.assertRaises(error):
                invoke(name,args,replace(context,**changed),replace(self.registry,project_root=self.root))
        with StoreWriteLock(self.store.path):
            with self.store.read() as c:
                reader=Reader(c,context,__import__("datetime").datetime.fromisoformat(NOW))
                with self.assertRaises(ResourceLimitError):
                    reader.decode(zlib.compress(b"a"*1048577))

    def test_empty_history_reports_not_established(self):
        result=self.call(1,dict(symbol="IWM",start_date="2026-09-21",end_date="2026-09-23"))
        self.assertEqual(result["status"],"not_established")
        self.assertEqual(len(records(result,"theta_daily")),0)

    def test_missing_volume_and_zero_denominators_are_not_zero_filled(self):
        self.store.publish(capture(symbol="DIA",missing_volume=True))
        row=records(self.call(2,dict(symbol="DIA",session="2026-09-23")),"theta_daily")[0]
        self.assertIsNone(row["total_volume"])
        self.assertIsNone(row["put_call_ratio"])
        self.assertIn("missing_volume",row["quality_flags"])
        self.store.publish(capture(symbol="IWM",factor=0))
        row=records(self.call(2,dict(symbol="IWM",session="2026-09-23")),"theta_daily")[0]
        self.assertEqual(row["total_volume"],0)
        self.assertIsNone(row["put_call_ratio"])
        self.assertIsNone(row["zero_dte_volume_share"])

    def test_iv_brackets_quality_and_constant_baseline(self):
        self.store.publish(capture(symbol="DIA",dtes=(14,60)))
        row=records(self.call(3,dict(symbol="DIA",session="2026-09-23",lookback_sessions=2,min_observations=2)),"theta_volatility")[0]
        self.assertIsNone(row["atm_iv_7"])
        self.assertIsNone(row["atm_iv_90"])
        self.assertAlmostEqual(row["atm_iv_30"],.2)
        self.assertEqual(row["interpolation"]["30"]["method"],"total_variance_interpolation")
        self.assertEqual(row["interpolation"]["30"]["lower_dte"],14)
        self.assertEqual(row["interpolation"]["30"]["upper_dte"],60)
        for day in ("2026-09-21","2026-09-22","2026-09-23"):
            self.store.publish(capture(symbol="IWM",session=day))
        args=dict(symbol="IWM",session="2026-09-23",lookback_sessions=2,min_observations=2)
        row=records(self.call(3,args),"theta_volatility")[0]
        self.assertIsNone(row["iv_rank_30"])
        self.assertEqual(row["rank_reason"],"constant_baseline")
        self.assertEqual(row["iv_percentile_30"],100)
        self.store.publish(capture(symbol="IWM",zero_quotes=True))
        row=records(self.call(3,args),"theta_volatility")[0]
        self.assertIsNone(row["atm_iv_30"])
        self.assertIsNone(row["skew_95p_minus_105c"])
        self.assertEqual(row["rank_reason"],"missing_current_iv")

    def test_zero_dte_share_uses_full_summary_even_without_retained_details(self):
        self.store.publish(capture(symbol="DIA",dtes=(0,7,30,90)))
        row=records(self.call(2,dict(symbol="DIA",session="2026-09-23")),"theta_daily")[0]
        self.assertAlmostEqual(row["zero_dte_volume_share"],.25)
        selected=records(self.call(5,dict(symbol="DIA",session="2026-09-23",expiration="2026-09-23")),"theta_selected_contract")
        self.assertEqual(selected,[])

    def test_symlink_and_hardlink_fail_closed(self):
        original=self.store.path.with_name("saved.sqlite")
        self.store.path.rename(original)
        self.store.path.symlink_to(original)
        args=dict(symbol="SPY",session="2026-09-23")
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOLS[2],args,tool_version="1.0.0")
        self.store.path.unlink()
        self.store.path.hardlink_to(original)
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOLS[2],args,tool_version="1.0.0")

    def test_research_identity_binds_query_capture_and_derivation(self):
        args=dict(symbol="SPY",session="2026-09-23",as_of="2026-09-26T00:00:00Z")
        first=self.call(2,args)["research_contract"]["contract"]
        same=self.call(2,args)["research_contract"]["contract"]
        other=self.call(2,dict(args,symbol="QQQ"))["research_contract"]["contract"]
        self.assertEqual(first["analysis_id"],same["analysis_id"])
        self.assertNotEqual(first["analysis_id"],other["analysis_id"])
        self.assertEqual(first["execution_policy"],"host_fixed_optional_options_immutable_read")
        self.assertEqual(first["availability_policy"],"local_capture_time_only")
        self.assertEqual(first["temporal"]["availability_basis"],"local_capture_time_only")
        self.assertEqual(first["temporal"]["date_only_policy"],"calendar_date_inclusive")
        self.store.publish(capture(factor=9,stamp="2026-09-25T10:00:00+00:00"))
        updated=self.call(2,args)["research_contract"]["contract"]
        self.assertNotEqual(first["analysis_id"],updated["analysis_id"])

    def test_effective_cutoff_metadata_and_extreme_instants(self):
        args=dict(symbol="SPY",session="2026-09-23")
        latest=self.call(2,args)
        temporal=latest["research_contract"]["contract"]["temporal"]
        self.assertEqual(temporal["cutoff"],records(latest,"theta_source")[0]["capture_cutoff"])
        self.assertEqual(temporal["mode"],"as_of")
        self.assertEqual(temporal["availability_basis"],"local_capture_time_only")
        with patch("quant_data.tool_platform.theta_access.OptionsStore",side_effect=AssertionError("No read")):
            for cutoff in ("0001-01-01T00:00:00+14:00","9999-12-31T23:59:59-14:00","0001-01-01T00:00:00Z"):
                with self.assertRaises(ValidationError):
                    self.app.dispatcher.call(TOOLS[2],dict(args,as_of=cutoff),tool_version="1.0.0")
