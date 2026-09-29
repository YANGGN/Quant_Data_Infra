"""Temporary research-artifact checks for the analysis tool and dashboard."""
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.canonical_inspector import CanonicalInspectorApplication
from quant_data.dashboard.forward_pe_page import analysis_tool_view
from quant_data.registry import load_registry, CANONICAL_REGISTRY_PATH, forward_pe_analysis_registry_profile
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.forward_pe_analysis_access import TOOL, parse
from tests.dashboard import test_forward_pe_page as fixture

ROOT = Path(__file__).resolve().parents[2]


def fields(record):
    return {f["name"]: f["value"] for f in record["fields"]}


def observations(result):
    output = []
    for record in result["records"]:
        if record["record_type"] == "forward_pe_analysis_chunk":
            chunk = fields(record)
            columns = json.loads(chunk["columns_json"])
            output.extend(dict(zip(columns, values, strict=True)) for values in json.loads(chunk["rows_json"]))
    return output


class AnalysisToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(CANONICAL_REGISTRY_PATH, project_root=ROOT, environment={})

    def setUp(self):
        self.seed = fixture.ForwardPePageTests("runTest")
        self.seed.setUp(); self.addCleanup(self.seed.doCleanups)
        self.host = self.seed.root / "host"
        self.root = self.host / "exports" / "forward-pe"
        self.root.mkdir(parents=True)
        self.seed.path.replace(self.root / self.seed.name)
        (self.seed.root / "current.json").replace(self.root / "current.json")
        self.path = self.root / self.seed.name
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM daily WHERE symbol='AAPL'")
            rows = []
            for i in range(1100):
                day = (date(2023, 9, 15) + timedelta(days=i)).isoformat()
                pe = None if i in (600, 900) else -1000 if i == 500 else 10000 if i == 999 else 10 + (i % 17)
                rows.append(("AAPL",day,100,10,pe,"missing_price" if pe is None else "available","w1"))
            db.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?)",rows)
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        registry = replace(self.registry,project_root=self.host)
        stores = StoreMap.from_mapping({r:self.host/(r+".sqlite") for r in ("market","macro","company","news")})
        self.app = Stage1Application(stores,registry)
        self.page = CanonicalInspectorApplication(stores,registry)

    def invoke(self, **options):
        args = dict(ticker="AAPL", **options)
        request = {"api_version":"1.0","tool":TOOL,"tool_version":"1.0.0","arguments":args}
        stdout, stderr = io.StringIO(), io.StringIO()
        original = sqlite3.connect
        def connect(path,*a,**kw):
            self.assertTrue(str(path).startswith(self.root.as_uri()))
            self.assertIn("mode=ro&immutable=1",str(path))
            return original(path,*a,**kw)
        with patch("sqlite3.connect",side_effect=connect), patch("socket.create_connection",side_effect=AssertionError("No network")):
            code = run(["call"],stdin=io.BytesIO(json.dumps(request).encode()),stdout=stdout,stderr=stderr,application=self.app)
        self.assertEqual(code,0,stdout.getvalue())
        result = json.loads(stdout.getvalue())["result"]
        validate_schema(result,self.registry.tool(TOOL,"1.0.0")["output_schema"])
        self.assertEqual(self.before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(self.path)+"-wal").exists())
        self.assertNotIn(str(self.host),json.dumps(result))
        return result

    def test_defaults_raw_values_and_explicit_method(self):
        result = self.invoke()
        summary = fields(result["records"][0])
        self.assertEqual(summary["window_sessions"],756)
        self.assertEqual(summary["winsor_tail_pct"],2.5)
        self.assertEqual(summary["min_observations"],252)
        rows = observations(result)
        self.assertEqual(rows[999]["raw_pe"],10000)
        self.assertTrue(rows[999]["was_clipped"])
        self.assertEqual(rows[900]["z_score_reason"],"missing_pe")
        self.assertIsNone(rows[100]["z_score"])
        self.assertEqual(result["research_contract"]["contract"]["point_in_time_status"],"not_established")

    def test_range_changes_reuse_preceding_history(self):
        long = self.invoke(range="5y")
        short = self.invoke(range="1y")
        by_date = {r["trade_date"]:r for r in observations(long)}
        selected = observations(short)
        self.assertGreater(fields(short["records"][0])["warmup_sessions"],500)
        for row in selected:
            self.assertEqual(row,by_date[row["trade_date"]])

    def test_page_and_tool_values_match_without_duplicate_statistics(self):
        result = self.invoke()
        expected = analysis_tool_view(result)
        from html import unescape
        import re
        response = self.page.handle("GET","/forward-pe?symbol=AAPL&range=5y")
        self.assertEqual(response.status,200,response.body.decode())
        match = re.search(r'data-series="([^"]+)"',response.body.decode())
        payload = json.loads(unescape(match.group(1)))
        self.assertEqual(payload["days"],expected["days"])
        self.assertEqual([dict(zip(payload["statistic_columns"], row, strict=True)) for row in payload["statistic_rows"]],expected["statistics"])
        self.assertIn('id="fp-z-chart"',response.body.decode())
        self.assertEqual(self.before,hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_no_caps_and_alternate_window_are_explicit(self):
        result = self.invoke(window_sessions=252,winsor_tail_pct="0")
        rows = observations(result)
        for row in rows:
            self.assertEqual(row["raw_z_score"],row["z_score"])
            if row["z_score"] is not None:
                self.assertEqual(row["raw_pe"],row["winsorized_pe"])

    def test_bad_arguments_fail_before_read_and_old_contract_is_unchanged(self):
        from quant_data.errors import ValidationError
        with patch("quant_data.tool_platform.forward_pe_analysis_access.read_analysis",side_effect=AssertionError("No read")):
            for extra in ({"path":"/tmp/a"},{"as_of":"2020"},{"window_sessions":True},{"window_sessions":19},{"window_sessions":1300},{"min_observations":757},{"winsor_tail_pct":"50"},{"winsor_tail_pct":2.5},{"range":"20y"}):
                with self.assertRaises(ValidationError):
                    self.app.dispatcher.call(TOOL,dict(ticker="AAPL",**extra),tool_version="1.0.0")
        prior=forward_pe_analysis_registry_profile(self.registry)
        self.assertEqual(prior.registry_version,"2.88.0")
        self.assertEqual(prior.source_sha256,"88dcf8cf30ab3541477992041e3d5bd2d2d05bbc5a6ab313eff45622cf8e93cf")
        for tool in prior.tools:
            self.assertEqual(tool,self.registry.tool(tool["id"]))

    def test_empty_and_unavailable_publications_do_not_fall_back(self):
        result=self.app.dispatcher.call(TOOL,{"ticker":"UNKNOWN"},tool_version="1.0.0")
        self.assertEqual(result["status"],"not_established")
        from quant_data.errors import StoreUnavailableError
        (self.root/"current.json").unlink()
        with self.assertRaises(StoreUnavailableError):
            self.app.dispatcher.call(TOOL,{"ticker":"AAPL"},tool_version="1.0.0")
