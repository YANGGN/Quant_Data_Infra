"""Public-boundary checks for the additive FMP tools; no network/default stores."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from quant_data.boundary import Stage1Application
from quant_data.errors import CapabilityUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import load_registry, fmp_research_registry_profile
from quant_data.stores import StoreMap
from quant_data.tool_platform.local_agent_cli import run
from quant_data.tool_platform.realtime_quote import QuoteCapture

ROOT = Path(__file__).resolve().parents[1]

class FmpToolBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(ROOT / "config/system_registry.json", project_root=ROOT, environment={})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.stores = StoreMap.four_explicit(**{role:root/(role+".sqlite") for role in ("company","market","macro","news")})

    def tearDown(self):
        self.temporary.cleanup()

    def test_manifest_discovery_is_offline_and_predecessor_exact(self):
        fetch = Mock()
        app = Stage1Application(self.stores, self.registry, quote_fetcher=fetch)
        manifest = app.dispatcher.manifest()
        names = {item["name"] for item in manifest["tools"]}
        self.assertIn("price_realtime", names)
        self.assertIn("company.get_research_inputs", names)
        self.assertEqual(len(names),77)
        fetch.assert_not_called()
        self.assertFalse(any(Path(self.temporary.name).iterdir()))
        predecessor = fmp_research_registry_profile(self.registry)
        self.assertEqual(predecessor.source_sha256,"4c2de9ef1ac49a4c23ab326000878fa66629caa1f8a0bcb65d4c089d827e9ac3")

    def test_local_envelope_uses_host_quote_once_generic_host_disabled(self):
        body = b'[{"symbol":"MSFT","price":400.25,"timestamp":1788544800}]'
        fetch = Mock(return_value=QuoteCapture(body,"2026-09-06T12:00:00Z"))
        app = Stage1Application(self.stores,self.registry,quote_fetcher=fetch)
        output=io.StringIO()
        code=run(["call"],stdin=io.BytesIO(dumps_strict({"api_version":"1.0","tool":"price_realtime","tool_version":"1.0.0","arguments":{"ticker":"MSFT"}}).encode()),stdout=output,stderr=io.StringIO(),application=app)
        payload=loads_strict(output.getvalue())
        self.assertEqual(code,0,payload)
        fetch.assert_called_once_with("MSFT")
        self.assertFalse(any(Path(self.temporary.name).iterdir()))
        with self.assertRaises(CapabilityUnavailableError):
            Stage1Application(self.stores,self.registry).dispatcher.call("price_realtime",{"ticker":"MSFT"},tool_version="1.0.0")

    def test_company_arguments_reach_reader_as_typed_query_without_host_controls(self):
        from quant_data.tool_platform.results import QueryResult
        from quant_data.company.fmp_research import FmpResearchInputsQuery
        app=Stage1Application(self.stores,self.registry)
        with patch("quant_data.company.fmp_research.read_research_inputs",return_value=QueryResult(tool="company.get_research_inputs",status="not_established")) as reader:
            result=app.dispatcher.call("company.get_research_inputs",{"cik":"0000789019","endpoints":["income-statement"],"period":"quarter","limit":8},tool_version="1.0.0")
            self.assertEqual(result["status"],"not_established")
            self.assertIsInstance(reader.call_args.args[1],FmpResearchInputsQuery)
        with self.assertRaises(ValidationError):
            app.dispatcher.call("company.get_research_inputs",{"cik":"0000789019","database":"bad.sqlite"},tool_version="1.0.0")
