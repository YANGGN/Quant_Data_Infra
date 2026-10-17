"""Bounded opt-in tools, compatibility proofs and no-write regressions."""
import copy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quant_data.errors import ValidationError, ResourceLimitError, RegistryError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.registry import load_registry
from quant_data.schema import validate_schema
from quant_data.tool_platform.context import FixedClock, ExecutionBudget
from quant_data.tool_platform.generate import generate
from quant_data.tool_platform.research_extensions_contracts import PEERS, GUIDANCE, KINDS, parse
from quant_data.tool_platform.research_extensions_registry import predecessor_profile, PREDECESSOR_SHA256
from quant_data.tool_platform.retained_research_common import record
from quant_data.tool_platform import peer_comparison
from tests.tool_platform import test_monitor_tools as monitor
from tests.tool_platform import test_transcript_research_tools as transcripts
from tests.tool_platform import test_canonical_macro_access as macro
ROOT = Path(__file__).resolve().parents[2]

def rows(value, kind):
    return monitor.records(json.loads(dumps_strict(value)), kind)

class ExtensionContractsTests(unittest.TestCase):
    def test_exact_predecessor_and_all_old_schemas_defaults_and_data_declarations(self):
        current = load_registry("config/system_registry.json", project_root=ROOT, environment={})
        old = predecessor_profile(current)
        self.assertEqual(old.source_sha256, PREDECESSOR_SHA256)
        self.assertEqual(old.migrations, current.migrations)
        self.assertEqual(old.stores, current.stores)
        for item in old.tools: self.assertEqual(item, current.tool(item["id"]))
        for policy in old.tool_version_policies:
            self.assertEqual(policy, next(p for p in current.tool_version_policies if p["tool"] == policy["tool"]))
        for before, after in zip(old.datasets, current.datasets):
            self.assertEqual(before, replace(after, tool_ids=before.tool_ids))
        catalog = json.loads((ROOT / old.raw["tool_version_schema_catalog"]["resource"]).read_text())
        added = {f"urn:quant-data:tool:{name}:{direction}:{version}" for name, version in
                 ((PEERS,"1.0.0"),(GUIDANCE,"2.0.0"),*((n,"2.0.0") for n in monitor.TOOLS))
                 for direction in ("input","output")}
        catalog["contracts"] = [c for c in catalog["contracts"] if c["id"] not in added]
        catalog["schema_version"] = "2.40.0"
        self.assertEqual(hashlib.sha256((json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True)+chr(10)).encode()).hexdigest(),
                         old.raw["tool_version_schema_catalog"]["sha256"])
        generate(ROOT, check=True)
        changed = copy.deepcopy(dict(current.raw)); changed["tools"][-1]["description"] = "changed"
        with self.assertRaises(RegistryError): predecessor_profile(replace(current, raw=changed))

    def test_current_guide_and_explicit_version_discovery(self):
        import re, io
        from quant_data.boundary import Stage1Application
        from quant_data.stores import StoreMap
        from quant_data.tool_platform.local_agent_cli import run
        from tempfile import TemporaryDirectory
        registry=load_registry("config/system_registry.json",project_root=ROOT,environment={})
        with TemporaryDirectory() as directory:
            stores=StoreMap.from_mapping({r:Path(directory)/(r+".sqlite") for r in ("market","macro","company","news")})
            app=Stage1Application(stores,registry)
            manifest=app.dispatcher.manifest()
            expected={t["name"]:max((v["version"] for v in t.get("versions",[t])),key=lambda v:tuple(map(int,v.split(".")))) for t in manifest["tools"]}
            pattern="^"+re.escape("| "+chr(96))+r"([a-z0-9_.]+)"+re.escape(chr(96)+" | "+chr(96))+r"([0-9]+[.][0-9]+[.][0-9]+)"+re.escape(chr(96)+" |")+"$"
            self.assertEqual(dict(re.findall(pattern,(ROOT/"docs/LOCAL_AGENT_TOOLS.md").read_text(),re.M)),expected)
            for name, version in ((PEERS,"1.0.0"),(GUIDANCE,"2.0.0"),*((n,"2.0.0") for n in monitor.TOOLS)):
                out=io.StringIO()
                self.assertEqual(run(["describe",name,"--tool-version",version],stdout=out,application=app),0)
                value=json.loads(out.getvalue())
                self.assertEqual(value["selection"],dict(name=name,tool_version=version))
                self.assertEqual(value["receipt"]["registry_revision"],registry.registry_version)

    def test_strict_inputs(self):
        for name, args in [(PEERS,dict(symbol="AAPL",peers=["AAPL"])),
                (PEERS,dict(symbol="AAPL",peers=["MSFT"]*20)),(PEERS,dict(symbol="aapl")),
                (PEERS,dict(symbol="AAPL",sql="x")),(PEERS,dict(symbol="AAPL",calendar_date="2026-02-30")),
                (GUIDANCE,dict(ticker="AAPL",limit=21)),(GUIDANCE,dict(ticker="AAPL",database_path="/tmp/x")),
                (GUIDANCE,dict(ticker="AAPL",start_date="2026-10-01",end_date="2026-09-01"))]:
            with self.subTest(args=args), self.assertRaises((ValidationError, ResourceLimitError)): parse(KINDS[name], args)

class CumulativeTests(unittest.TestCase):
    def setUp(self):
        self.f = monitor.MonitorToolsTests("runTest")
        self.f.setUpClass(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def call(self, name=monitor.TOOLS[0], **args):
        before = self.f.store.path.read_bytes()
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            out = self.f.app.dispatcher.call(name, args, tool_version="2.0.0")
        validate_schema(out, self.f.registry.tool(name,"2.0.0")["output_schema"])
        self.assertEqual(before, self.f.store.path.read_bytes())
        return out

    def test_v2_cli_receipt_and_history_discovery(self):
        import io
        from quant_data.tool_platform.local_agent_cli import run
        for name,args in ((monitor.TOOLS[0],dict(symbols=["SPY"])),(monitor.TOOLS[1],dict(symbol="SPY",start_date="2026-09-29",end_date="2026-09-29"))):
            out=io.StringIO()
            request=dict(api_version="1.0",tool=name,tool_version="2.0.0",arguments=args)
            before=self.f.store.path.read_bytes()
            self.assertEqual(run(["call"],stdin=io.StringIO(json.dumps(request)),stdout=out,application=self.f.app),0)
            value=json.loads(out.getvalue())
            self.assertTrue(value["receipt"])
            self.assertTrue(rows(value["result"],"monitor_observation"))
            self.assertEqual(self.f.store.path.read_bytes(),before)

    def test_native_leaders_normalized_and_v1_cumulative_fields_frozen(self):
        leaders = [dict(expiration="2026-10-02",strike=700,right=right,volume=10,share=.5) for right in ("call","put")]
        self.f.store.publish(monitor.observation("AAPL",leaders=leaders,cumulative_volume=100,private_token="never"))
        old = self.f.call(0,dict(symbols=["AAPL"]))
        item = rows(old,"monitor_observation")[0]
        self.assertEqual([r["right"] for r in item["leaders"]],["C","P"])
        self.assertFalse(any(k.startswith("cumulative") for k in item))
        self.assertNotIn("never",dumps_strict(self.call(symbols=["AAPL"])))

    def test_missing_zero_and_quality_are_distinct_snapshot_and_history(self):
        old = rows(self.call(symbols=["SPY"]),"monitor_observation")[0]
        self.assertEqual(old["cumulative_data_status"],"not_saved")
        self.assertIsNone(old["cumulative_volume"])
        self.assertIsNone(old["cumulative_quality_flags"])
        self.f.store.publish(monitor.observation("AAPL",cumulative_volume=0,cumulative_relative_volume=None,
            cumulative_expected_volume=None,cumulative_baseline_sessions=2,cumulative_baseline_minute=655,
            cumulative_quality_flags=["baseline_warmup"]))
        item = rows(self.call(symbols=["AAPL"]),"monitor_observation")[0]
        self.assertEqual(item["cumulative_volume"],0)
        self.assertIsNone(item["cumulative_relative_volume"])
        self.assertEqual(item["cumulative_data_status"],"quality_flagged")
        self.assertEqual(item["cumulative_baseline_sessions"],2)
        history = rows(self.call(monitor.TOOLS[1],symbol="AAPL",start_date="2026-09-29",end_date="2026-09-29"),"monitor_observation")
        self.assertEqual(history,[item])
        early = self.call(symbols=["AAPL"],as_of="2026-09-29T14:54:00Z")
        self.assertFalse(rows(early,"monitor_observation"))

    def test_complete_metrics_and_invalid_saved_field_fail_closed(self):
        self.f.store.publish(monitor.observation("AAPL",cumulative_volume=600,cumulative_relative_volume=3,
            cumulative_expected_volume=200,cumulative_baseline_sessions=25,cumulative_baseline_minute=655,
            cumulative_quality_flags=[]))
        item = rows(self.call(symbols=["AAPL"]),"monitor_observation")[0]
        self.assertEqual(item["cumulative_relative_volume"],3)
        self.assertEqual(item["cumulative_data_status"],"saved")
        self.f.store.publish(monitor.observation("QQQ",cumulative_volume=True))
        with self.assertRaises(monitor.StoreUnavailableError): self.call(symbols=["QQQ"])

class PeerTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(clock=FixedClock(Decimal(1),"2026-10-10T23:00:00Z"),checkpoint=lambda:None,budget=ExecutionBudget())
        self.classifications = {s:dict(ticker=s,sector="Technology",industry="Chips",instrument_id="id-"+s,membership_snapshot_id="membership") for s in ("MU","AMD","NVDA")}
        self.data = [dict(symbol=s,status="available",metrics={"netmargin":Decimal(v)},missingness={},
             source={"provider_subject":s,"provider_symbol":s},cohort=dict(dimension="ARQ",calendar_date="2026-06-30",currency="USD"))
             for s,v in (("MU",".2"),("AMD",".3"),("NVDA",".3"))]
        self.calls = []
        def compose(name,version,args,context,registry):
            self.calls.append((name,version,args))
            values = [record("instrument_classification",v) for v in self.classifications.values()] if name=="market.get_classifications" else [
                record("fundamental_screen_row",r) for r in self.data if r["symbol"] in args["symbols"]]
            return SimpleNamespace(records=values,lineage=())
        self.enterContext(patch.object(peer_comparison,"compose",side_effect=compose))
        self.links = {s:dict(instrument_id="id-"+s,cik=None,membership_snapshot_id="membership",
            provider_subject=s,provider_symbol=s,basis="explicit_fixture_mapping") for s in self.classifications}
        self.enterContext(patch.object(peer_comparison,"peer_links",return_value=self.links))
    def call(self, **args):
        value = peer_comparison.invoke(parse(KINDS[PEERS], dict({"symbol":"MU","rank_by":"netmargin"},**args)),self.context,None)
        return value.to_primitive()

    def test_complete_group_ties_and_selected_subset(self):
        result = rows(self.call(),"peer_comparison_row")
        ranks = {r["symbol"]:r["rank"] for r in result}
        self.assertEqual(ranks,{"MU":3,"AMD":1,"NVDA":1})
        self.assertTrue(all(r["rank_cohort_size"]==3 for r in result))
        self.assertEqual({r["symbol"] for r in rows(self.call(peers=["AMD"]),"peer_comparison_row")},{"MU","AMD"})
        self.assertEqual(self.calls[0][1],"1.0.0")
        self.assertTrue(all(c[2]["as_of"].startswith("2026-10-10") for c in self.calls))

    def test_currency_period_and_identity_mismatches_do_not_enter_anchor_rank(self):
        self.data[1]["cohort"]["calendar_date"]="2026-03-31"
        self.data[2]["status"]="requested_period_unavailable"
        out = rows(self.call(),"peer_comparison_row")
        self.assertIsNone(next(r for r in out if r["symbol"]=="NVDA")["rank"])
        self.assertEqual(next(r for r in out if r["symbol"]=="MU")["rank_cohort_size"],1)
        with patch.object(peer_comparison,"peer_links",return_value={}):
            self.assertTrue(all(r["rank"] is None for r in rows(self.call(),"peer_comparison_row")))

    def test_money_requires_currency_but_dimensionless_ratios_do_not(self):
        for row in self.data:
            row["cohort"]["currency"] = None
            row["metrics"]["revenue"] = Decimal(100)
        money = rows(self.call(rank_by="revenue"),"peer_comparison_row")
        self.assertTrue(all(r["rank"] is None and r["ranking_status"]=="currency_unavailable" for r in money))
        ratios = rows(self.call(),"peer_comparison_row")
        self.assertTrue(all(r["rank"] is not None for r in ratios))
        self.assertTrue(all(r["cohort"]["currency"] is None for r in ratios))
        self.assertTrue(all(r["ranking_cohort"]["currency"]=="not_applicable_dimensionless_metric" for r in ratios))
        classification = self.classifications["MU"]
        link = self.links["MU"]
        for changed in (dict(link,membership_snapshot_id="different"),dict(link,provider_subject="different"),
                        dict(link,instrument_id="different")):
            self.assertIsNone(peer_comparison.matched_link(changed,classification,self.data[0]["source"]))

    def test_oversized_unknown_and_off_group_are_explicit(self):
        self.classifications["AMD"]["industry"]="Other"
        value=self.call(peers=["AMD","ZZZZ"])
        self.assertEqual(len(rows(value,"peer_exclusion")),2)
        for i in range(30):
            self.classifications["T"+str(i)] = dict(ticker="T"+str(i),industry="Chips",instrument_id=str(i),sector="Technology")
        self.calls.clear()
        value=self.call()
        self.assertEqual(rows(value,"peer_selection")[0]["status"],"explicit_peer_selection_required")
        self.assertEqual(len(self.calls),1)
        self.classifications.pop("MU")
        self.assertEqual(rows(self.call(),"peer_selection")[0]["status"],"anchor_classification_unavailable")

class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.f=transcripts.TranscriptResearchTests("runTest");self.f.setUp();self.addCleanup(self.f.doCleanups)
    def call(self, **args):
        before=mutation_fingerprint(self.f.stores)
        with patch("socket.create_connection",side_effect=AssertionError("network")), patch("quant_data.credentials.read_project_credential",side_effect=AssertionError("credentials")):
            out=self.f.app.dispatcher.call(GUIDANCE,dict(ticker="AAPL",start_date="2026-03-01",**args),tool_version="2.0.0")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        validate_schema(out,self.f.registry.tool(GUIDANCE,"2.0.0")["output_schema"])
        return out
    def test_actual_dates_gaps_blocked_calls_evidence_and_bounded_window(self):
        first=self.f.add_call(quarter=4,day="2026-03-01")
        gap=self.f.add_call(quarter=2,day="2026-04-01",summary=False)
        last=self.f.add_call(quarter=3,day="2026-06-01",blocked=True)
        out=self.call()
        calls=rows(out,"guidance_call")
        self.assertEqual([r["capture_id"] for r in calls],[first,gap,last])
        self.assertEqual([r["guidance_status"] for r in calls],["saved_draft","no_draft_at_cutoff","blocked_draft_excluded"])
        self.assertTrue(rows(out,"guidance_evidence"))
        self.assertTrue(all(r["numeric_change"] is None for r in rows(out,"guidance_change")))
        included=self.call(include_blocked=True)
        self.assertEqual(rows(included,"guidance_call")[-1]["guidance_status"],"saved_draft")
        limited=self.call(limit=1)
        self.assertEqual(rows(limited,"guidance_timeline")[0]["omitted_older_calls"],2)
    def test_adjacent_assessed_drafts_compare_with_original_turns(self):
        first=self.f.add_call(quarter=2,day="2026-03-01")
        last=self.f.add_call(quarter=3,day="2026-06-01")
        out=self.call()
        changes=rows(out,"guidance_change")
        self.assertTrue(changes)
        numeric=[r for r in changes if r.get("numeric_change")]
        self.assertTrue(numeric)
        self.assertTrue(all(r["before_capture_id"]==first and r["after_capture_id"]==last for r in changes))
        self.assertTrue(all(r["source_sha256"] and r["source_json_pointer"] for r in rows(out,"guidance_evidence")))

    def test_cutoff_preserves_source_but_hides_future_draft(self):
        self.f.add_call(quarter=2,day="2026-03-01")
        out=self.call(mode="as_of",as_of="2026-09-08T23:00:00Z")
        self.assertEqual(rows(out,"guidance_call")[0]["guidance_status"],"no_draft_at_cutoff")
        self.assertFalse(rows(out,"guidance_item"))
        self.assertFalse(rows(out,"guidance_evidence"))

class MacroSearchWorkTests(unittest.TestCase):
    def test_search_filters_before_statistics_and_matches_complete_descriptor(self):
        f=macro.CanonicalMacroAccessToolTests("runTest");f.setUp();self.addCleanup(f.tearDown)
        f.importer.import_fixture("treasury_base");f.importer.import_fixture("treasury_correction")
        from quant_data.macro.canonical_access import CanonicalMacroRepository, MacroCatalogQuery
        from quant_data.stores import quiet_immutable_read_connection
        repo=CanonicalMacroRepository(f.stores,f.registry)
        with quiet_immutable_read_connection(f.stores,"macro") as c:
            expected=repo._catalog(c)
        query=next(r.series_id for r in expected if r.observation_count)
        calls=[]
        original=CanonicalMacroRepository._generic_descriptors
        def tracked(connection,series_ids=None,**kwargs):
            calls.append((series_ids,kwargs.get("statistics",True)))
            return original(connection,series_ids,**kwargs)
        with patch.object(CanonicalMacroRepository,"_generic_descriptors",side_effect=tracked):
            out=repo.search_series(MacroCatalogQuery(query=query,limit=1))
        self.assertEqual(out.descriptors,(next(r for r in expected if r.series_id==query),))
        self.assertEqual(calls[0],(None,False))
        self.assertEqual(calls[1],((query,),True))
