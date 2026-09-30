"""Temporary export-only tests for the public saved forward-P/E reader."""
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.registry import (
    load_registry, CANONICAL_REGISTRY_PATH, forward_pe_tools_registry_profile,
    forward_pe_analysis_registry_profile,
)
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.forward_pe_access import TOOL
from tests.operations import test_derived_refresh as refresh_fixture

ROOT=Path(__file__).resolve().parents[2]


def fields(record):
    return {f["name"]:f["value"] for f in record["fields"]}


class ForwardPeToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=load_registry(CANONICAL_REGISTRY_PATH,project_root=ROOT,environment={})

    def setUp(self):
        self.seed=refresh_fixture.DailyRefreshTests("runTest")
        self.seed.setUp();self.addCleanup(self.seed.doCleanups)
        self.root=self.seed.root
        self.seed.root=self.root/"exports"/"forward-pe"
        self.seed.root.mkdir(parents=True)
        registry=replace(self.registry,project_root=self.root)
        stores=StoreMap.from_mapping({role:self.root/"data"/(role+".sqlite") for role in ("market","macro","company","news")})
        self.app=Stage1Application(stores,registry)
        self.args={"ticker":"ODD","start_date":"2023-12-04","end_date":"2023-12-11"}

    def build(self):
        self.seed.build()

    def invoke(self,args=None,version="1.0.0"):
        envelope={"api_version":"1.0","tool":TOOL,"tool_version":version,"arguments":args or self.args}
        body=json.dumps(envelope).encode()
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.seed.root.iterdir() if p.is_file()}
        original=sqlite3.connect
        def connect(path,*a,**kw):
            self.assertTrue(str(path).startswith(self.seed.root.as_uri()),path)
            self.assertIn("mode=ro",str(path));self.assertIn("immutable=1",str(path))
            return original(path,*a,**kw)
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch("socket.create_connection",side_effect=AssertionError("No network")),patch("sqlite3.connect",side_effect=connect):
            code=run(["call"],stdin=io.BytesIO(body),stdout=stdout,stderr=stderr,application=self.app)
        after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.seed.root.iterdir() if p.is_file()}
        self.assertEqual(before,after);self.assertEqual(stderr.getvalue(),"")
        response=json.loads(stdout.getvalue())
        if code==0:validate_schema(response["result"],self.registry.tool(TOOL,"1.0.0")["output_schema"])
        return code,response

    def test_complete_range_includes_gaps_and_window_provenance(self):
        self.build();code,response=self.invoke()
        self.assertEqual(code,0,response)
        result=response["result"]
        summary=fields(result["records"][0])
        days=[fields(r) for r in result["records"] if r["record_type"]=="forward_pe_day"]
        windows=[fields(r) for r in result["records"] if r["record_type"]=="forward_pe_window"]
        self.assertEqual(summary["daily_count"],4)
        self.assertFalse(summary["is_point_in_time"])
        self.assertEqual(days[0]["missing_reason"],"no_announcement_anchor")
        self.assertEqual(days[2]["forward_pe"],12)
        self.assertEqual(days[2]["price_version_id"],"p2")
        self.assertEqual(len(json.loads(windows[0]["components_json"])),4)
        self.assertFalse(result["truncation"]["applied"])
        self.assertEqual(result["research_contract"]["contract"]["point_in_time_status"],"not_established")
        self.assertNotIn(str(self.root),json.dumps(response))

    def test_overlay_corrections_and_new_days_match_dashboard(self):
        from quant_data.dashboard.forward_pe_page import read_forward_pe
        self.build()
        self.seed.inputs["prices"][2].update(version_id="repair",close_value="280")
        self.seed.run_refresh()
        code,response=self.invoke();self.assertEqual(code,0,response)
        days=[fields(r) for r in response["result"]["records"] if r["record_type"]=="forward_pe_day"]
        dashboard=read_forward_pe(self.seed.root,"ODD","all")["days"]
        self.assertEqual([(d["trade_date"],d["close"],d["forward_eps"],d["forward_pe"]) for d in days],
                         [tuple(d[:4]) for d in dashboard])
        self.assertEqual(days[2]["estimate_cutoff"],refresh_fixture.CUTOFF)
        self.assertEqual(days[-1]["observation_kind"],"daily_capture")
        self.assertEqual(days[2]["price_version_id"],"repair")

    def test_negative_ratios_are_numeric(self):
        for item in self.seed.inputs["estimates"]:
            payload=json.loads(item["payload_json"]);payload["epsAvg"]=-1;item["payload_json"]=json.dumps(payload)
        self.build();code,response=self.invoke();self.assertEqual(code,0,response)
        days=[fields(r) for r in response["result"]["records"] if r["record_type"]=="forward_pe_day"]
        self.assertEqual(days[2]["forward_pe"],-42)
        self.assertEqual(days[2]["status"],"expected_loss")

    def test_zero_is_undefined_and_missing_prices_stay_missing(self):
        for item in self.seed.inputs["estimates"]:
            payload=json.loads(item["payload_json"]);payload["epsAvg"]=0;item["payload_json"]=json.dumps(payload)
        self.seed.inputs["prices"].pop(3)
        self.build();code,response=self.invoke();self.assertEqual(code,0,response)
        days=[fields(r) for r in response["result"]["records"] if r["record_type"]=="forward_pe_day"]
        self.assertIsNone(days[2]["forward_pe"]);self.assertEqual(days[2]["missing_reason"],"zero_forward_eps")
        self.assertIsNone(days[3]["close"]);self.assertEqual(days[3]["missing_reason"],"missing_price")

    def test_invalid_inputs_rejected_before_artifact_access(self):
        bad=[dict(self.args,database_path="/tmp/private"),dict(self.args,as_of="2023-12-01"),
             dict(self.args,ticker="../x"),dict(self.args,start_date="2023-12-12"),
             dict(self.args,start_date="2023-02-30"),dict(self.args,start_date="1980-01-01")]
        with patch("quant_data.tool_platform.forward_pe_access.open_current",side_effect=AssertionError("No read")):
            for args in bad:
                code,response=self.invoke(args)
                self.assertNotEqual(code,0)
                self.assertIn(response["error"]["code"],("invalid_request","resource_limit"))
            code,response=self.invoke(version="99.0.0")
            self.assertNotEqual(code,0)

    def test_missing_export_does_not_fall_back_to_canonical_or_live(self):
        code,response=self.invoke()
        self.assertNotEqual(code,0)
        self.assertEqual(response["error"]["code"],"store_unavailable")

    def test_absent_ticker_and_empty_range_are_explicit(self):
        self.build()
        for args,reason in ((dict(self.args,ticker="MISSING"),"ticker_not_in_snapshot"),
                            (dict(self.args,start_date="2024-01-01",end_date="2024-01-02"),"no_observations_in_range")):
            code,response=self.invoke(args);self.assertEqual(code,0,response)
            self.assertEqual(response["result"]["status"],"not_established")
            self.assertEqual(fields(response["result"]["records"][0])["status"],reason)

    def test_cli_http_parity_and_discovery(self):
        self.build();code,response=self.invoke();self.assertEqual(code,0,response)
        request={"api_version":"1.0","tool":TOOL,"tool_version":"1.0.0","arguments":self.args}
        http=self.app.handle("POST","/api/agent-tools/call",headers={"Content-Type":"application/json"},body=json.dumps(request).encode())
        self.assertEqual(response["result"],json.loads(http.body)["result"])
        declared=next(t for t in self.app.dispatcher.manifest()["tools"] if t["name"]==TOOL)
        self.assertEqual(declared["version"],"1.0.0")
        self.assertEqual(declared["stores"],[]);self.assertFalse(declared["live_capability"]["possible"])

    def test_exact_predecessor_and_frozen_contracts(self):
        # Check the tool's introduction boundary, not later additive releases.
        introduced=forward_pe_analysis_registry_profile(self.registry)
        self.assertEqual(introduced.registry_version,"2.88.0")
        self.assertEqual(introduced.source_sha256,"88dcf8cf30ab3541477992041e3d5bd2d2d05bbc5a6ab313eff45622cf8e93cf")
        prior=forward_pe_tools_registry_profile(introduced)
        self.assertEqual(prior.registry_version,"2.87.0")
        self.assertEqual(prior.source_sha256,"6c21f0004b6b5462583846a26e7502ab46a7a0628b619435b03b8d26c63988ab")
        self.assertEqual(prior.raw["migrations"],introduced.raw["migrations"])
        self.assertEqual(prior.raw["datasets"],introduced.raw["datasets"])
        self.assertEqual(prior.raw["tool_versions"],introduced.raw["tool_versions"])
        self.assertEqual({tool["id"] for tool in introduced.tools} -
                         {tool["id"] for tool in prior.tools}, {TOOL})
        for tool in prior.tools:
            self.assertEqual(tool,introduced.tool(tool["id"]))

        # Later additions are allowed; historical definitions must stay frozen.
        current_migrations={item["id"]:item for item in self.registry.raw["migrations"]}
        for migration in prior.raw["migrations"]:
            with self.subTest(migration=migration["id"]):
                self.assertEqual(migration,current_migrations[migration["id"]])
        for tool in introduced.tools:
            with self.subTest(tool=tool["id"]):
                self.assertEqual(tool,self.registry.tool(tool["id"]))
