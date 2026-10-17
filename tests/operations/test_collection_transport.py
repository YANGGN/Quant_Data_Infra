import unittest,time
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from quant_data.operations.collection_transport import request_route,SelectedProviderFetch,host_fetch
from quant_data.operations.collection_plan import AcquisitionUnit
from quant_data.errors import ValidationError,ResourceLimitError,StoreUnavailableError

def unit(provider="fmp",endpoint="income-statement"):
    collection={"fmp":"fmp_statements","sec":"sec_filings_companyfacts","sharadar":"sharadar_fundamentals"}[provider]
    subject={"fmp":"AAPL","sec":"0000320193","sharadar":"199059"}[provider]
    params={"fmp":{"symbol":"AAPL","period":"annual","limit":"1000"},"sec":{"cik":subject},
        "sharadar":{"ticker":"AAPL","dimension":"ARQ","qopts.per_page":"10000"}}[provider]
    if endpoint.endswith("/metadata"):subject="SHARADAR/SF1";params={}
    return AcquisitionUnit(collection,provider,endpoint,subject,tuple(sorted(params.items())),"historical_backfill",
        "fixture",() if endpoint.endswith("/metadata") else ("AAPL",),"1"*64,max_response_bytes=1024,timeout_seconds=2)

def stall(sender,*args):
    try:time.sleep(5)
    finally:sender.close()

class FakeResponse:
    status=200
    def getheader(self,k):return {"Content-Type":"application/json","Content-Length":"2"}.get(k)
    def getheaders(self):return [("Content-Type","application/json"),("X-RateLimit-Remaining","99")]
    def read(self,n):return b"[]"
class FakeConnection:
    def __init__(self,*a,**kw):pass
    def request(self,*a,**kw):pass
    def getresponse(self):return FakeResponse()
    def close(self):pass

class SelectedTransportTests(unittest.TestCase):
    def setUp(self):
        # These HTTP fixtures must never discover or charge the host allowance.
        allowance=patch('quant_data.operations.collection_provider_policy.host_allowance',return_value=None)
        allowance.start();self.addCleanup(allowance.stop)
    def test_routes_are_fixed_and_metadata_uses_the_documented_json_endpoint(self):
        self.assertEqual(request_route(unit()).host,"financialmodelingprep.com")
        self.assertEqual(request_route(unit("sec","companyfacts")).path,"/api/xbrl/companyfacts/CIK0000320193.json")
        r=request_route(unit("sharadar","SHARADAR/SF1/metadata"))
        self.assertEqual(r.path,"/api/v3/datatables/SHARADAR/SF1/metadata.json")
        self.assertEqual(r.credential_names,("SHARADAR_API_KEY",))
        with self.assertRaises(ValidationError):
            request_route(replace(unit(),parameters=(("file","evil"),("limit","1000"),("period","annual"),("symbol","AAPL"))))
    def test_original_body_and_safe_quota_headers_survive_the_worker_process(self):
        with patch("quant_data.operations.collection_transport.http.client.HTTPSConnection",FakeConnection):
            response=SelectedProviderFetch("fmp","synthetic-test-token")(unit=unit(),timeout_seconds=1,max_bytes=1024)
        self.assertEqual(response.body,b"[]")
        self.assertIn(("x-ratelimit-remaining","99"),response.headers)
    def test_hard_timeout_terminates_one_worker_without_retry(self):
        start=time.monotonic()
        with patch("quant_data.operations.collection_transport._http_once",stall),self.assertRaises(ResourceLimitError):
            SelectedProviderFetch("fmp","synthetic-test-token")(unit=unit(),timeout_seconds=1,max_bytes=1024)
        self.assertLess(time.monotonic()-start,3)
    def test_bounds_and_secret_echo_reject(self):
        f=SelectedProviderFetch("fmp","synthetic-test-token")
        with self.assertRaises(ValidationError):f(unit=unit(),timeout_seconds=3,max_bytes=1024)
        with patch("quant_data.operations.collection_transport.http.client.HTTPSConnection",FakeConnection),patch.object(FakeResponse,"read",return_value=b"synthetic-test-token"):
            with self.assertRaises(StoreUnavailableError):f(unit=unit(),timeout_seconds=1,max_bytes=1024)
    def test_existing_named_credential_resolver_is_reused_without_dotenv_fallback(self):
        f=host_fetch(Path("/tmp"),"fmp",environment={"FMP_API_KEY":"synthetic-test-token"})
        self.assertEqual(f.provider,"fmp")
        f=host_fetch(Path("/tmp"),"sec",environment={"SEC_USER_AGENT_NAME":"Fixture","SEC_USER_AGENT_EMAIL":"fixture@example.com"})
        self.assertEqual(f.provider,"sec")


def partial_frame(sender,*args):
    import os,struct
    os.write(sender.fileno(),struct.pack("!i",1024)+b"x")
    time.sleep(5)

class SelectedTransportRegressionTests(unittest.TestCase):
    def setUp(self):
        # These HTTP fixtures must never discover or charge the host allowance.
        allowance=patch('quant_data.operations.collection_provider_policy.host_allowance',return_value=None)
        allowance.start();self.addCleanup(allowance.stop)
    def test_partial_ipc_frame_cannot_extend_the_request_deadline(self):
        start=time.monotonic()
        with patch("quant_data.operations.collection_transport._http_once",partial_frame),self.assertRaises(ResourceLimitError):
            SelectedProviderFetch("fmp","synthetic-test-token")(unit=unit(),timeout_seconds=1,max_bytes=1024)
        self.assertLess(time.monotonic()-start,2.5)
    def test_native_http_truncated_declared_length_is_rejected(self):
        import http.client,io
        class Socket:
            def makefile(self,*args):return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n[]")
        response=http.client.HTTPResponse(Socket());response.begin()
        with patch("quant_data.operations.collection_transport.http.client.HTTPSConnection",FakeConnection),patch.object(FakeConnection,"getresponse",return_value=response):
            with self.assertRaises(StoreUnavailableError):
                SelectedProviderFetch("fmp","synthetic-test-token")(unit=unit(),timeout_seconds=1,max_bytes=1024)
