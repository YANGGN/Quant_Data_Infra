"""Transport diagnostics use fake HTTPS responses and isolated checkpoints."""
import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations import equibles_parallel_backfill as parallel
from tests.company import test_equibles_transcripts as fixtures
from tests.operations import test_equibles_paid_policy as paid
from tests.operations.test_equibles_parallel_backfill import Transport


class Sender:
    def __init__(self):
        self.values = []
        self.closed = False
    def send(self, value):
        self.values.append(value)
    def close(self):
        self.closed = True


def diagnostic_frame(sender, *args):
    sender.send(job.EquiblesTransportFailure("timeout", 200).diagnostic())
    sender.close()


def invalid_diagnostic_frame(sender, *args):
    value = job.EquiblesTransportFailure("timeout").diagnostic()
    value["message"] = "synthetic-secret-do-not-persist"
    sender.send(value)
    sender.close()


class WorkerDiagnosticTests(unittest.TestCase):
    path = "/v1/stocks/MSFT/earnings-calls/2025/1/speakers?limit=200&offset=0"
    key = "synthetic-secret-do-not-persist"

    def child(self, *, request_error=None, read_error=None, body=b"{}", headers=()):
        response = Mock(status=200)
        response.read.return_value = body
        response.read.side_effect = read_error
        response.getheaders.return_value = list(headers)
        connection = Mock()
        connection.request.side_effect = request_error
        connection.getresponse.return_value = response
        sender = Sender()
        with patch.object(job.http.client, "HTTPSConnection", return_value=connection):
            job._http_child(sender, self.path, self.key)
        self.assertTrue(sender.closed)
        connection.close.assert_called_once()
        self.assertEqual(len(sender.values), 1)
        return sender.values[0]

    def test_timeout_preserves_whether_http_headers_were_received_without_error_text(self):
        for request_error, read_error, status in (
                (TimeoutError(self.key), None, None),
                (None, TimeoutError(self.key), 200)):
            with self.subTest(status=status):
                value = self.child(request_error=request_error, read_error=read_error)
                self.assertEqual(value, job.EquiblesTransportFailure("timeout", status).diagnostic())
                self.assertNotIn(self.key, json.dumps(value))

    def test_connection_and_incomplete_body_failures_have_distinct_safe_categories(self):
        value = self.child(request_error=ConnectionResetError(self.key))
        self.assertEqual(value["category"], "connection_error")
        value = self.child(read_error=http.client.IncompleteRead(self.key.encode(), 100))
        self.assertEqual(value["category"], "incomplete_response")
        self.assertEqual(value["http_status"], 200)
        self.assertNotIn(self.key, json.dumps(value))

    def test_oversize_and_credential_echo_never_retain_response_content(self):
        with patch.object(job, "MAX_BYTES", 8):
            value = self.child(body=b"x" * 9)
        self.assertEqual(value["category"], "response_too_large")
        for kwargs in ({"body": self.key.encode()}, {"headers": [("content-type", self.key)]}):
            value = self.child(**kwargs)
            self.assertEqual(value["category"], "credential_exposure")
            self.assertNotIn(self.key, json.dumps(value))

    def test_parent_decodes_typed_worker_failure_and_malformed_diagnostics_fail_closed(self):
        for worker, category in ((diagnostic_frame, "timeout"),
                                 (invalid_diagnostic_frame, "invalid_worker_response")):
            with self.subTest(category=category), patch.object(job, "_http_child", worker):
                with self.assertRaises(job.EquiblesTransportFailure) as raised:
                    job.EquiblesTransport(self.key).request(self.path)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn(self.key, str(raised.exception))


class RetainedFailureTests(unittest.TestCase):
    def test_serial_failure_is_retained_with_no_retry_no_skip_and_charge_preserved(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)/"private"
            job.freeze(root, [{"symbol": "A", "instrument_id": "frozen-A"}])
            transport = fixtures.FakeTransport([job.EquiblesTransportFailure("timeout")])
            publisher = fixtures.FakePublisher()
            result = job.run(root, publisher, transport, clock=lambda: fixtures.AT, sleeper=lambda _: None)
            state = json.loads((root/"state.json").read_bytes())
            self.assertEqual(result["outcome"], "blocked")
            self.assertEqual(result["quota"]["attempted"], 1)
            self.assertEqual(result["skipped_quarters"], 0)
            self.assertEqual(publisher.calls, [])
            expected = job.EquiblesTransportFailure("timeout").diagnostic()
            self.assertEqual(job.retained_transport_failure(root, state["pending"]), expected)
            self.assertEqual(result["blocked"]["transport_failure"], expected)
            result = job.run(root, publisher, fixtures.FakeTransport([]), clock=lambda: fixtures.AT)
            self.assertEqual(result["requests_this_run"], 0)
            self.assertEqual(len(transport.paths), 1)

    def fixture(self):
        fixture = paid.EquiblesPaidPolicyTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.convert(paid.selection(("A",)))
        return fixture

    def batch(self, fixture, error):
        root = fixture.root
        path = parallel.planned_paths(fixture.state())[0]
        transport = Transport(root, {path: error})
        with patch.object(parallel, "SPACING_SECONDS", 0):
            result = parallel.acquire_batch(root, transport, max_requests=1,
                max_bytes=job.MAX_BYTES, deadline=time.monotonic()+60,
                day=paid.AT.date().isoformat(), clock=lambda: paid.AT)
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(len(transport.paths), 1)
        return fixture.state()

    def test_parallel_diagnostic_survives_recovery_without_refetch_or_quota_refund(self):
        fixture = self.fixture()
        state = self.batch(fixture, job.EquiblesTransportFailure("timeout", 200))
        saved = state["blocked"]["transport_failures"]
        self.assertEqual(saved[0]["category"], "timeout")
        self.assertEqual(saved[0]["http_status"], 200)
        self.assertEqual(saved[0]["attempt"], state["pending"]["attempt"])
        before = state["usage"]
        self.assertFalse(parallel.recover_batch(fixture.root, clock=lambda: paid.AT))
        after = fixture.state()
        self.assertEqual(after["usage"], before)
        self.assertEqual(after["blocked"]["transport_failures"], saved)
        self.assertEqual(after["transcripts"], 0)

    def test_generic_exception_message_is_never_persisted(self):
        fixture = self.fixture()
        secret = "synthetic-secret-do-not-persist"
        state = self.batch(fixture, RuntimeError(secret))
        path = fixture.root/"attempts"/(state["pending"]["attempt"]+".failure.json")
        self.assertNotIn(secret, path.read_text())
        self.assertNotIn(secret, json.dumps(state))
        self.assertEqual(state["blocked"]["transport_failures"][0]["category"], "transport_error")

    def test_failure_receipt_cannot_be_reassigned_to_another_request(self):
        fixture = self.fixture()
        state = self.batch(fixture, job.EquiblesTransportFailure("connection_error"))
        path = fixture.root/"attempts"/(state["pending"]["attempt"]+".failure.json")
        value = json.loads(path.read_bytes())
        value["reservation"]["path"] = "/different"
        job.atomic(path, value, replace=True)
        with self.assertRaises(job.ConflictError):
            parallel.recover_batch(fixture.root, clock=lambda: paid.AT)
        self.assertEqual(fixture.state(), state)


if __name__ == "__main__":
    unittest.main()
