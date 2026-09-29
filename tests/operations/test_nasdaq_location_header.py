"""HTTP response semantics observed during the authorized Nasdaq rollout."""
import unittest
from unittest.mock import patch
from quant_data.operations import collection_transport as transport
from tests.operations.test_collection_transport import FakeConnection, FakeResponse

class NasdaqLocationHeaderTests(unittest.TestCase):
    def invoke(self,status):
        messages=[]
        class Sender:
            def send(self,value):messages.append(value)
            def close(self):pass
        class Response(FakeResponse):
            def getheader(self,name):
                return "https://data.nasdaq.com/resource" if name=="Location" else super().getheader(name)
        response=Response();response.status=status
        route=transport.RequestRoute("data.nasdaq.com","/api/v3/datatables/SHARADAR/TICKERS.json",(),("SHARADAR_API_KEY",),"api_key")
        with patch.object(transport.http.client,"HTTPSConnection",FakeConnection), \
             patch.object(FakeConnection,"getresponse",return_value=response), \
             patch.object(FakeConnection,"request") as request:
            transport._http_once(Sender(),route,"offline-only-fixture-token",1,1024)
            self.assertEqual(request.call_count,1)
        return messages

    def test_http_200_with_location_is_retained_without_following_it(self):
        values=self.invoke(200)
        self.assertEqual(len(values),1)
        self.assertEqual(values[0][0],200)
        self.assertEqual(values[0][1],b"[]")
        self.assertNotIn("location",dict(values[0][3]))

    def test_redirect_status_is_rejected_without_following_it(self):
        for status in (301,302,303,307,308):
            with self.subTest(status=status):self.assertEqual(self.invoke(status),[None])
