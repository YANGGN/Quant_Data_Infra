"""No-socket tests of the fixed provider boundary."""
from unittest import TestCase
from unittest.mock import MagicMock, patch
from quant_data.company.transcript_analysis_model import _exchange, MAX_RESPONSE_BYTES, OpenAITranscriptTransport
from quant_data.company.transcript_analysis import _response
from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.json_codec import dumps_strict


class TranscriptTransportTests(TestCase):
    def test_fixed_tls_endpoint_and_single_request(self):
        pipe=MagicMock()
        connection=MagicMock()
        reply=connection.getresponse.return_value
        reply.status=200
        reply.getheader.return_value="application/json; charset=utf-8"
        reply.read.return_value=b"{}"
        with patch("quant_data.company.transcript_analysis_model.http.client.HTTPSConnection",return_value=connection) as constructor:
            _exchange(pipe,"fixture-key",b"request")
        constructor.assert_called_once_with("api.openai.com",443,timeout=180)
        connection.request.assert_called_once_with("POST","/v1/responses",body=b"request",
            headers={"Authorization":"Bearer fixture-key","Content-Type":"application/json"})
        pipe.send.assert_called_once_with(("ok",200,b"{}"))
        connection.close.assert_called_once()
        pipe.close.assert_called_once()

    def test_http_errors_redirects_and_oversize_are_not_retried(self):
        for status in (302,429,500):
            with self.subTest(status=status):
                pipe=MagicMock();connection=MagicMock()
                connection.getresponse.return_value.status=status
                with patch("quant_data.company.transcript_analysis_model.http.client.HTTPSConnection",return_value=connection):
                    _exchange(pipe,"fixture-key",b"request")
                connection.request.assert_called_once()
                connection.getresponse.return_value.read.assert_not_called()
                pipe.send.assert_called_once_with(("http_error",status,None))
        pipe=MagicMock();connection=MagicMock();reply=connection.getresponse.return_value
        reply.status=200;reply.getheader.return_value="application/json";reply.read.return_value=b"x"*(MAX_RESPONSE_BYTES+1)
        with patch("quant_data.company.transcript_analysis_model.http.client.HTTPSConnection",return_value=connection):
            _exchange(pipe,"fixture-key",b"request")
        pipe.send.assert_called_once_with(("response_bound",None,None))

    def test_refusal_and_wrong_model_never_become_analysis(self):
        value={"model":"gpt-5.6-terra","status":"completed","output":[{"type":"message","role":"assistant",
            "content":[{"type":"refusal","refusal":"Cannot comply."}]}],
            "usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}
        with self.assertRaises(ValidationError):_response(dumps_strict(value).encode(),"gpt-5.6-terra")
        value["model"]="gpt-5.6-sol"
        with self.assertRaises(ValidationError):_response(dumps_strict(value).encode(),"gpt-5.6-terra")

    def test_deadline_terminates_worker_without_a_second_attempt(self):
        context=MagicMock();parent=MagicMock();child=MagicMock();process=MagicMock()
        context.Pipe.return_value=(parent,child);context.Process.return_value=process
        parent.poll.return_value=False;process.is_alive.side_effect=[True,False]
        with patch("quant_data.company.transcript_analysis_model.multiprocessing.get_context",return_value=context):
            with self.assertRaises(ResourceLimitError):OpenAITranscriptTransport("fixture-key").request(b"{}")
        process.start.assert_called_once();process.terminate.assert_called_once()
        parent.recv.assert_not_called()
