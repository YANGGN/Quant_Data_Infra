"""Public v3 Sharadar defaults, exact legacy contracts and local-only reads."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from quant_data.boundary import Stage1Application
from quant_data.company.sharadar_repository import SharadarSf1Publisher
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import sharadar_company_tools_registry_profile
from quant_data.schema import validate_schema
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.sharadar_company_contracts import TOOLS, KINDS, parse
from tests.company import test_fundamental_sources as sources_fixture
CUT = sources_fixture.CUT
from tests.company import test_sharadar_sf1 as sf

def fields(record):
    return {f["name"]: f["value"] for f in record["fields"]}

class SharadarCompanyToolsTests(unittest.TestCase):
    def setUp(self):
        self.seed = sources_fixture.FundamentalSourcesTests("runTest")
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        self.f = self.seed.f
        self.app = Stage1Application(self.f.stores, self.f.registry)

    def publish(self):
        SharadarSf1Publisher(self.f.stores, self.f.registry).publish(
            sf.partition(sf.page([sf.row(dimension="ARQ", revenue="11"), sf.row(dimension="MRQ", revenue="12")],
                captured_at="2026-09-09T05:30:00Z")),
            selection=self.seed.selections["sharadar_fundamentals"], ingested_at=CUT)

    def call(self, name=TOOLS[0], **args):
        request = {"api_version": "1.0", "tool": name, "tool_version": "3.0.0", "arguments": args}
        out, err = io.StringIO(), io.StringIO()
        before = mutation_fingerprint(self.f.stores)
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            code = run(["call"], stdin=io.BytesIO(dumps_strict(request).encode()),
                stdout=out, stderr=err, application=self.app)
        self.assertEqual(before, mutation_fingerprint(self.f.stores))
        return code, loads_strict(out.getvalue())

    def test_default_arq_source_native_rows_and_explicit_mrq(self):
        self.publish()
        for args, revenue in (({"symbol": "AAPL"}, "11"), ({"symbol": "AAPL", "dimension": "MRQ"}, "12")):
            code, response = self.call(**args)
            self.assertEqual(code, 0, response)
            validate_schema(response["result"], self.f.registry.tool(TOOLS[0], "3.0.0")["output_schema"])
            row = fields(response["result"]["records"][0])
            self.assertEqual(row["source"], "sharadar")
            self.assertEqual(json.loads(row["values_json"])["revenue"], revenue)
            self.assertTrue(row["provider_subject"])
            self.assertTrue(response["result"]["lineage"])

    def test_missing_sharadar_never_falls_back_to_existing_sec_or_fmp(self):
        code, response = self.call(symbol="AAPL")
        self.assertEqual(code, 0, response)
        self.assertEqual(response["result"]["status"], "not_established")
        self.assertEqual(response["result"]["records"], [])

    def test_as_of_excludes_future_capture_and_shares_have_source_native_names(self):
        self.publish()
        code, response = self.call(symbol="AAPL", mode="as_of", as_of="2026-09-09T05:15:00Z")
        self.assertEqual(code, 0, response)
        self.assertEqual(response["result"]["records"], [])
        code, response = self.call(TOOLS[1], symbol="AAPL")
        self.assertEqual(code, 0, response)
        values = json.loads(fields(response["result"]["records"][0])["values_json"])
        self.assertEqual(set(values), {"sharesbas", "shareswa", "shareswadil"})

    def test_invalid_inputs_fail_before_store_access(self):
        from quant_data.errors import ValidationError
        for args in ({"symbol": "AAPL", "dimension": "ALL"}, {"symbol": "AAPL", "limit": True},
            {"symbol": "AAPL", "mode": "as_of"}, {"symbol": "AAPL", "as_of": CUT},
            {"symbol": "AAPL", "database_path": "x"}, {"symbol": "AAPL", "source": "sec"}):
            with self.assertRaises(ValidationError):
                parse(KINDS[TOOLS[0]], args)

    def test_exact_predecessor_preserves_all_sec_versions_and_migrations(self):
        previous = sharadar_company_tools_registry_profile(self.f.registry)
        self.assertEqual(previous.registry_version, "2.86.0")
        self.assertEqual(previous.source_sha256, "6a90749d2514a3846bca8991aa9277e1c595072186203959a124def507a30069")
        self.assertEqual(previous.raw["migrations"], self.f.registry.raw["migrations"])
        for name in TOOLS:
            self.assertEqual(previous.tool(name), self.f.registry.tool(name))
            self.assertEqual(previous.tool(name, "2.0.0"), self.f.registry.tool(name, "2.0.0"))
        self.assertEqual(previous.tool(TOOLS[0], "2.1.0"), self.f.registry.tool(TOOLS[0], "2.1.0"))
