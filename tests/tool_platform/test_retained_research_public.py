"""Agent-boundary integration on isolated stores; no provider or model access."""
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from quant_data.boundary import Stage1Application
from quant_data.errors import ValidationError,StoreUnavailableError,CancellationError,DeadlineExceededError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict,loads_strict
from quant_data.registry import load_registry,stage5_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import writer_connection
from quant_data.tool_platform.context import FixedClock,CancellationToken,Deadline
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.retained_research_contracts import TOOLS,VERSIONS,KINDS,parse,SUCCESSORS
from quant_data.tool_platform.retained_research_catalog import example
from quant_data.tool_platform.retained_research_registry import predecessor_profile
from quant_data.tool_platform.generate import generate
from tests.company import test_fmp_analyst_history as analyst
from tests.tool_platform import test_market_cross_sectional_performance_v2 as market
from tests.tool_platform import test_transcript_research_tools as transcript
from tests.tool_platform import test_sharadar_company_tools as fundamentals
from tests.tool_platform import test_theta_tools as theta
from tests.options.test_theta_compact import temporary_store
ROOT=Path(__file__).resolve().parents[2]
CLOCK=FixedClock(Decimal(100),"2026-09-28T22:00:00Z")

def records(value,kind):
    return [theta.fields(r) for r in value["records"] if r["record_type"]==kind]

class RetainedResearchPublicTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("quant_data.boundary.dispatcher.HostClock",return_value=CLOCK))
        self.enterContext(patch("socket.create_connection",side_effect=AssertionError("No live requests")))
    def call(self,app,name,args):
        before=mutation_fingerprint(app.dispatcher._store_map)
        out,err=io.StringIO(),io.StringIO()
        request=dict(api_version="1.0",tool=name,tool_version=VERSIONS[name],arguments=args)
        code=run(["call"],stdin=io.BytesIO(dumps_strict(request).encode()),stdout=out,stderr=err,application=app)
        value=loads_strict(out.getvalue())
        if code:app.dispatcher.call(name,args,tool_version=VERSIONS[name])
        self.assertEqual(code,0,value);self.assertEqual(err.getvalue(),"")
        validate_schema(value["result"],app.dispatcher.registry.tool(name,VERSIONS[name])["output_schema"])
        self.assertEqual(before,mutation_fingerprint(app.dispatcher._store_map))
        self.assertEqual(value["result"]["research_contract"]["contract"]["point_in_time_status"],"not_established")
        return value["result"]
    def analyst_fixture(self):
        f=analyst.AnalystHistoryTests();f.setUp();self.addCleanup(f.tearDown)
        registry=replace(f.registry,project_root=Path(f.temp.name))
        return f,Stage1Application(f.stores,registry)
    def test_successors_capture_cutoffs_and_full_watchlist_coverage(self):
        f,app=self.analyst_fixture()
        f.pub([dict(symbol="NST",date="2026-09-10",epsActual=2)],endpoint="earnings")
        row=dict(symbol="NST",date="2026-12-31",epsAvg=2,currency="USD")
        f.pub([row],period="quarter")
        f.pub([dict(row,epsAvg=3)],period="quarter",at="2026-09-08T00:00:00Z")
        for name in TOOLS[:4]:
            args=example(name)
            args["symbols" if "symbols" in args else "symbol"]=["NST"] if "symbols" in args else "NST"
            value=self.call(app,name,args)
            if name==TOOLS[3]:self.assertTrue(records(value,"estimate_revision"))
        changes=self.call(app,TOOLS[7],dict(symbols=["NST"],since="2026-09-06T00:00:00Z"))
        self.assertEqual({r["domain"] for r in records(changes,"watchlist_change")},{"earnings","estimates"})
        coverage=self.call(app,TOOLS[10],dict(symbols=["NST"],start_date="2026-09-01",end_date="2026-09-28"))
        self.assertEqual({r["domain"] for r in records(coverage,"research_dataset_coverage")},
                         {"earnings","estimates","transcripts","fundamentals","prices","news","options"})
        early=self.call(app,TOOLS[0],dict(symbols=["NST"],start_date="2026-09-01",end_date="2026-09-28",as_of="2026-01-01T00:00:00Z"))
        self.assertFalse(records(early,"earnings_event"))
    def test_transcript_call_comparison_and_sharadar_screen(self):
        f=transcript.TranscriptResearchTests();f.setUp();self.addCleanup(f.doCleanups)
        before=f.add_call(quarter=2,day="2026-03-01")
        after=f.add_call(quarter=3,day="2026-06-01")
        value=self.call(f.app,TOOLS[5],dict(symbol="AAPL",before_capture_id=before,after_capture_id=after,sections=["headline","guidance"]))
        self.assertTrue(records(value,"comparison_evidence"))
        sf=fundamentals.SharadarCompanyToolsTests();sf.setUp();self.addCleanup(sf.doCleanups);sf.publish()
        value=self.call(sf.app,TOOLS[6],dict(symbols=["AAPL"],metrics=["revenue"],rank_by="revenue",criteria=[dict(metric="revenue",minimum=10)]))
        row=records(value,"fundamental_screen_row")[0]
        self.assertEqual(row["metrics"]["revenue"],11);self.assertTrue(row["passes"])
    def test_prices_theta_breadth_events_and_missing_options_do_not_fall_back(self):
        f=market.MarketCrossSectionalPerformanceV2Tests();f.setUp();self.addCleanup(f.tearDown)
        # Expand one existing ETF fixture with complete daily closes and unchanged capture evidence.
        with writer_connection(f.stores,"market") as c:
            def insert(table,row):
                c.execute("INSERT INTO "+table+" ("+",".join(row)+") VALUES ("+",".join("?" for _ in row)+")",tuple(row.values()))
            identity=dict(c.execute("SELECT * FROM stage10_instruments WHERE instrument_id='cross-section-ccc'").fetchone())
            identity.update(instrument_id="research-spy",provider_symbol="SPY",identity_seed_sha256="e"*64)
            insert("stage10_instruments",identity)
            capture=dict(c.execute("SELECT * FROM stage10_daily_price_captures WHERE instrument_id='cross-section-ccc'").fetchone())
            f._insert_run(c,run_id="research-spy-run",dataset_id="market.stage10.source_evidence",semantic_identity="f"*64)
            capture.update(run_id="research-spy-run",capture_id="research-spy-capture",instrument_id="research-spy",provider_symbol="SPY",semantic_identity="d"*64,
                snapshot_id="research-spy-snapshot",artifact_id="research-spy-artifact")
            insert("stage10_daily_price_captures",capture)
            template=dict(c.execute("SELECT * FROM stage10_daily_price_versions WHERE instrument_id='cross-section-ccc' LIMIT 1").fetchone())
            template.update(run_id="research-spy-run",instrument_id="research-spy",capture_id=capture["capture_id"],snapshot_id=capture["snapshot_id"],artifact_id=capture["artifact_id"])
            from quant_data.market.etf_calculations import sessions
            days=sessions(date(2026,5,1),date(2026,9,28))
            for i,day in enumerate(days):
                row=dict(template,version_id="research-price-"+str(i),trade_date=day.isoformat(),source_row=i+100,
                    close_value=str(Decimal(100)+Decimal(i)/10+(Decimal(i%3)/5)))
                c.execute("INSERT INTO stage10_daily_price_versions ("+",".join(row)+") VALUES ("+",".join("?" for _ in row)+")",tuple(row.values()))
                c.execute("INSERT INTO stage10_daily_prices VALUES (?,?,?,?,?,?)",(row["instrument_id"],row["trade_date"],row["provider"],row["price_variant"],row["currency_segment"],row["version_id"]))
        f.baseline=mutation_fingerprint(f.stores)
        temporary,store=temporary_store();self.addCleanup(temporary.cleanup);store.initialize()
        for day in ("2026-09-21","2026-09-22","2026-09-23"):store.publish(theta.capture(session=day))
        registry=replace(f.registry,project_root=store.root);app=Stage1Application(f.stores,registry)
        sha=hashlib.sha256(store.path.read_bytes()).hexdigest()
        args=dict(symbol="SPY",start_date="2026-09-21",end_date="2026-09-23")
        value=self.call(app,TOOLS[4],args)
        self.assertTrue(all(r["comparison_status"]=="available" and r["option_capture_id"] and r["option_semantic_sha256"] for r in records(value,"implied_realized_comparison")))
        self.assertEqual(records(value,"comparison_theta_source")[0]["source_store"],"data/options.sqlite")
        breadth=self.call(app,TOOLS[9],dict(symbols=["SPY","MISSING"],start_date="2026-09-21",end_date="2026-09-23",ma_windows=[2,20],high_low_window=20,leadership_window=5))
        self.assertEqual(records(breadth,"market_breadth_day")[-1]["advance_decline_eligible"],1)
        # A real company publisher provides reported earnings; identity disagreement must suppress returns.
        from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
        from quant_data.fixtures import FixtureManifest
        from quant_data.company.fmp_market_data import FmpCompanySubject
        from quant_data.company.fmp_analyst_history import FmpAnalystPublisher,parse_analyst_response
        from quant_data.stores import stable_id
        CompanyStage4FixtureImporter(f.stores,FixtureManifest.load(ROOT/"tests/fixtures/manifest.json",project_root=ROOT)).import_fixture("sec_initial")
        subject=FmpCompanySubject(stable_id("company_issuer",analyst.CIK),analyst.CIK,"SPY","research-spy","1"*64)
        prepared=parse_analyst_response(b'[{"symbol":"SPY","date":"2026-09-21","epsActual":2}]',endpoint="earnings",parameters={"symbol":"SPY"},subject=subject,captured_at="2026-09-24T00:00:00Z",source_reference="fixture/earnings.json")
        FmpAnalystPublisher(f.stores,f.registry).publish(prepared,request_id="research-event-test")
        from quant_data.macro.fmp_calendar_wholesale import FmpWholesaleCalendarPublisher,parse_fmp_us_calendar_wholesale
        from tests.macro.test_fmp_calendar_wholesale import _body,_row
        FmpWholesaleCalendarPublisher(macro_store=f.stores.macro,project_root=f.root,registry=f.registry).publish(
            parse_fmp_us_calendar_wholesale(_body([_row("GDP Test","2026-09-21 12:30:00",actual="2")]),
                captured_at="2026-09-24T00:00:00Z",start_date="2026-09-01",end_date="2026-09-28"))
        f.baseline=mutation_fingerprint(f.stores)
        macro=self.call(app,TOOLS[8],dict(args,event_source="macro",event_name="GDP Test",horizons=[1],include_options=False))
        self.assertEqual(records(macro,"event_response")[0]["status"],"available",records(macro,"event_response")[0])
        events=self.call(app,TOOLS[8],dict(args,horizons=[1],include_options=True))
        self.assertEqual(records(events,"event_response")[0]["status"],"available")
        self.assertIsNotNone(records(events,"event_response")[0]["atm_iv30_change"])
        self.assertEqual(records(events,"event_response")[0]["option_capture_ids"],[1,2])
        changes=self.call(app,TOOLS[7],dict(symbols=["SPY"],domains=["options","prices"],since="2026-09-23T00:00:00Z"))
        self.assertEqual(len(records(changes,"watchlist_change")),3)
        coverage=self.call(app,TOOLS[10],dict(symbols=["SPY"],domains=["options","prices"],start_date="2026-09-21",end_date="2026-09-23"))
        self.assertTrue(all(r["status"]=="available" for r in records(coverage,"research_dataset_coverage")))
        from quant_data.tool_platform import event_distribution
        from quant_data.tool_platform.earnings_research import earnings_rows
        context=app.dispatcher._stage5_context(TOOLS[8],registry.tool(TOOLS[8],"1.0.0"))
        wrong=earnings_rows(context,["SPY"],args["start_date"],args["end_date"],"2026-09-28T22:00:00.000000Z")
        wrong[0]["instrument_id"]="different-security"
        with patch.object(event_distribution,"earnings_rows",return_value=wrong):
            rejected=self.call(app,TOOLS[8],dict(args,horizons=[1],include_options=False))
            self.assertEqual(records(rejected,"event_response")[0]["reason"],"event_price_identity_mismatch")
        self.assertEqual(sha,hashlib.sha256(store.path.read_bytes()).hexdigest())
        missing=Stage1Application(f.stores,replace(f.registry,project_root=f.root))
        with self.assertRaises(StoreUnavailableError):missing.dispatcher.call(TOOLS[4],args,tool_version="1.0.0")
    def test_saved_plan_is_private_file_read_only(self):
        from quant_data.tool_platform.collection_plan import CONTRACT
        f,app=self.analyst_fixture();root=Path(f.temp.name)
        state=dict(contract=CONTRACT,roster=[],watch={},daily={},tasks={"NST":dict(symbol="NST",instrument_id="nst",lane="recent",phase="catalogue",due="2026-09-28",queued_at="2026-09-27",blocked=False)})
        account=dict(roster=[],usage={"2026-09-28":dict(attempted=2,remaining=98)},operating_daily_cap=100)
        paths=[]
        for folder,value in (("equibles-refresh",state),("equibles-transcripts",account)):
            path=root/"data/.operations"/folder/"state.json";path.parent.mkdir(parents=True);path.write_text(json.dumps(value));path.chmod(0o600);paths.append(path)
        before=[p.read_bytes() for p in paths]
        value=self.call(app,TOOLS[11],{})
        self.assertEqual(records(value,"collection_plan")[0]["remaining_request_budget"],98)
        self.assertEqual(records(value,"collection_plan_ticker")[0]["symbol"],"NST")
        self.assertEqual(before,[p.read_bytes() for p in paths])
        paths[0].chmod(0o644)
        with self.assertRaises(StoreUnavailableError):app.dispatcher.call(TOOLS[11],{},tool_version="1.0.0")
    def test_manifest_describe_examples_predecessor_and_invalid_inputs(self):
        f,app=self.analyst_fixture()
        previous=predecessor_profile(f.registry)
        self.assertEqual(previous.source_sha256,"6ee19b30575fc4b2613c0583fd38c36aa1780fdb25bf51e1233a0b61b097316c")
        for old in previous.tools:
            for version in previous.versions_for(old["id"]):
                self.assertEqual(f.registry.tool(old["id"],version["version"]),version)
        self.assertEqual(stage5_registry_profile(previous).source_sha256,stage5_registry_profile(f.registry).source_sha256)
        manifest=app.dispatcher.manifest();self.assertEqual(len(manifest["tools"]),98)
        for name in TOOLS:
            args=example(name);parse(KINDS[name],args)
            out,err=io.StringIO(),io.StringIO()
            code=run(["describe",name,"--tool-version",VERSIONS[name]],stdout=out,stderr=err,application=app)
            self.assertEqual(code,0,(name,err.getvalue()))
            with self.assertRaises(ValidationError):parse(KINDS[name],dict(args,database_path="arbitrary.sqlite"))
            if name in SUCCESSORS:self.assertEqual(f.registry.tool(name),previous.tool(name))
        generate(ROOT,check=True)

    def test_invalid_cutoffs_cancellation_and_deadlines_precede_store_reads(self):
        from quant_data.tool_platform.retained_research_access import invoke
        f,app=self.analyst_fixture();name=TOOLS[0]
        args=example(name);typed=parse(KINDS[name],args)
        context=app.dispatcher._stage5_context(name,f.registry.tool(name,"2.0.0"))
        with patch("quant_data.company.retained_analyst_reader.connection",side_effect=AssertionError("No store read")):
            for changes,error in ((dict(cancellation=CancellationToken(True)),CancellationError),
                                  (dict(deadline=Deadline(Decimal(99))),DeadlineExceededError)):
                with self.assertRaises(error):invoke(name,typed,replace(context,**changes),f.registry)
            with self.assertRaises(ValidationError):app.dispatcher.call(name,dict(args,as_of="2027-01-01T00:00:00Z"),tool_version="2.0.0")
            with self.assertRaises(ValidationError):app.dispatcher.call(name,dict(args,sql="SELECT 1"),tool_version="2.0.0")
