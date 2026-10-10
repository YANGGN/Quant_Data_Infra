"""SEC request gaps are terminal, diagnosed and bounded; store errors still stop."""
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from quant_data.errors import ConflictError
from quant_data.operations import collection_queue as queue
from quant_data.operations import collection_transport as transport
from quant_data.operations.fetch_run_summary import summarize_report
from tests.operations import test_sec_conditional_refresh as fixture
from tests.operations.test_collection_transport import unit, stall, FakeConnection
from tests.company import test_sec_companyfacts as payloads


class SecFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ConditionalSecTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.seed = self.fixture.seed
        self.seed._select_two_issuers_for_conflict_checks()
        self.failure_endpoint = "submissions"
        self.failure = transport.ProviderRequestFailure("timeout")
        self.fixture.fetch = self.fetch

    @property
    def state_root(self):
        return self.seed.root / "filing-triggered/current/2026-09-10"

    def state(self):
        return json.loads((self.state_root / "ledger.json").read_text())

    def fetch(self, *, unit, **kwargs):
        self.seed.calls.append(unit)
        if unit.subject == "0000320193" and unit.endpoint == self.failure_endpoint:
            raise self.failure
        arguments = dict(cik=unit.subject, issuer_name="Fixture " + unit.subject,
            accession=unit.subject + "-26-000001")
        body = (payloads._generic_submissions_body(**arguments) if unit.endpoint == "submissions"
            else payloads._generic_companyfacts_body(**arguments))
        response = queue.QueueResponse(200, body, self.seed.now.isoformat())
        self.seed.sleep(1)
        return response

    def test_submissions_timeout_records_failure_then_next_issuer_completes_without_retry(self):
        result = self.fixture.run_worker()
        self.assertEqual((result["requests"], result["failed_cik_count"],
            result["succeeded_cik_count"], result["unattempted_cik_count"]), (3, 1, 1, 0))
        self.assertEqual([u.subject for u in self.seed.calls],
            ["0000320193", "0001067983", "0001067983"])
        detail = result["request_failures"][0]
        self.assertEqual((detail["cik"], detail["endpoint"], detail["category"]),
            ("0000320193", "submissions", "timeout"))
        self.assertFalse(detail["retryable"])
        self.assertEqual(result["uncertain_response_bytes"], self.seed.calls[0].max_response_bytes)
        state = self.state()
        self.assertIsNone(state["pending"])
        self.assertEqual(state["usage"]["2026-09-10"]["charged_attempts"], 3)
        self.assertEqual(state["usage"]["2026-09-10"]["uncertain_bytes"], self.seed.calls[0].max_response_bytes)
        counts = summarize_report("quant-data-sec-company-fundamentals.timer", result)
        self.assertEqual((counts["successful"], counts["failed"], counts["unattempted"]), (1, 1, 0))
        before = (self.state_root / "ledger.json").read_bytes()
        self.fixture.fetch = Mock(side_effect=AssertionError("No retry"))
        self.assertEqual(self.fixture.run_worker()["requests_this_run"], 0)
        self.fixture.fetch.assert_not_called()
        self.assertEqual((self.state_root / "ledger.json").read_bytes(), before)

    def test_facts_timeout_counts_as_failed_and_next_issuer_completes(self):
        self.failure_endpoint = "companyfacts"
        result = self.fixture.run_worker()
        self.assertEqual((result["requests"], result["facts_requests"],
            result["failed_cik_count"], result["succeeded_cik_count"]), (4, 2, 1, 1))
        self.assertEqual(result["failed_issuers"], [
            {"cik": "0000320193", "outcome": "facts_unavailable"}])
        self.assertEqual(result["request_failures"][0]["endpoint"], "companyfacts")
        self.assertEqual(self.fixture.query("SELECT DISTINCT cik FROM company_issuers ORDER BY cik"),
            [("0000320193",), ("0001067983",)])

    def test_unknown_bytes_consume_budget_and_continuation_never_retries_failure(self):
        result = self.fixture.run_worker(byte_limit=64 * 1024 * 1024)
        self.assertEqual((result["requests"], result["failed_cik_count"],
            result["unattempted_cik_count"], result["stop_reason"]), (1, 1, 1, "invocation_budget"))
        result = self.fixture.run_worker(byte_limit=64 * 1024 * 1024)
        self.assertEqual(result["request_failures"][0]["category"], "timeout")
        self.assertEqual(sum(u.subject == "0000320193" for u in self.seed.calls), 1)
        self.assertEqual(result["requests"], 1)  # Next issuer submissions; facts need a later budget.

    def test_unclassified_failure_keeps_pending_and_never_retries(self):
        self.failure = OSError("unclassified storage or transport error")
        with self.assertRaisesRegex(ConflictError, "Collection acquisition failed"):
            self.fixture.run_worker()
        with self.assertRaisesRegex(ConflictError, "no retained response; no retry"):
            self.fixture.run_worker()
        self.assertEqual(len(self.seed.calls), 1)
        self.assertIsNotNone(self.state()["pending"])

    def assert_stops(self, failure):
        self.failure = failure
        with self.assertRaises(ConflictError):
            self.fixture.run_worker()
        self.assertEqual(len(self.seed.calls), 1)
        self.assertIsNotNone(self.state()["pending"])

    def test_credential_exposure_is_not_isolated(self):
        self.assert_stops(transport.ProviderRequestFailure("credential_exposure"))

    def test_rate_limit_is_not_isolated(self):
        self.assert_stops(transport.ProviderRequestFailure("timeout", 429))

    def test_transport_failure_subclasses_are_not_silently_classified(self):
        class OtherFailure(transport.ProviderRequestFailure):
            pass
        self.assert_stops(OtherFailure("timeout"))

    def test_failure_receipt_io_error_stops_before_next_request(self):
        original = queue.atomic
        def fail(path, body, **kwargs):
            if path.parent.name == "failures":
                raise OSError("disk full")
            return original(path, body, **kwargs)
        with patch.object(queue, "atomic", side_effect=fail):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.fixture.run_worker()
        self.assertEqual(len(self.seed.calls), 1)
        self.assertIsNotNone(self.state()["pending"])

    def test_crash_after_failure_receipt_recovers_charge_once_without_retry(self):
        original = queue._save
        def fail(root, state):
            if state["pending"] is None and any(u.get("error") == "sec_request_failed"
                    for u in state["units"].values()):
                raise OSError("interrupted ledger save")
            return original(root, state)
        with patch.object(queue, "_save", side_effect=fail):
            with self.assertRaises(OSError):
                self.fixture.run_worker()
        path = next((self.state_root / "failures").glob("*.json"))
        before = path.read_bytes()
        result = self.fixture.run_worker()
        self.assertEqual((result["requests"], result["succeeded_cik_count"], result["failed_cik_count"]),
            (2, 1, 1))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.state()["usage"]["2026-09-10"]["uncertain_bytes"], self.seed.calls[0].max_response_bytes)
        self.assertEqual(len(self.seed.calls), 3)

    def test_tampered_failure_receipt_blocks_recovery(self):
        original = queue._save
        def fail(root, state):
            if state["pending"] is None:
                raise OSError("interrupted")
            return original(root, state)
        with patch.object(queue, "_save", side_effect=fail):
            with self.assertRaises(OSError):
                self.fixture.run_worker()
        path = next((self.state_root / "failures").glob("*.json"))
        value = json.loads(path.read_text())
        value["request_id"] = "0" * 64
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ConflictError, "immutable artifact differs"):
            self.fixture.run_worker()
        self.assertEqual(len(self.seed.calls), 1)
        self.assertIsNotNone(self.state()["pending"])

    def test_publication_conflict_is_not_mislabeled_as_request_failure(self):
        with patch("quant_data.operations.sec_conditional_refresh._publish_metadata",
                side_effect=ConflictError("canonical identity conflict")):
            self.failure_endpoint = None
            with self.assertRaisesRegex(ConflictError, "canonical identity conflict"):
                self.fixture.run_worker()
        self.assertEqual(len(self.seed.calls), 1)
        self.assertFalse((self.state_root / "scheduled-result.json").exists())


class SecTransportDiagnosticTests(unittest.TestCase):
    def test_wall_timeout_is_typed_and_worker_terminated(self):
        start = time.monotonic()
        with patch.object(transport, "_http_once", stall):
            with self.assertRaises(transport.ProviderRequestFailure) as caught:
                transport.SelectedProviderFetch("sec", "Synthetic fixture@example.com")(
                    unit=unit("sec", "submissions"), timeout_seconds=1, max_bytes=1024)
        self.assertEqual(caught.exception.category, "timeout")
        self.assertFalse(caught.exception.retryable)
        self.assertLess(time.monotonic() - start, 3)

    def test_network_failure_is_typed_without_raw_exception_text(self):
        with patch.object(transport.http.client, "HTTPSConnection", FakeConnection), patch.object(
                FakeConnection, "request", side_effect=OSError("secret-fixture-token")):
            with self.assertRaises(transport.ProviderRequestFailure) as caught:
                transport.SelectedProviderFetch("sec", "Synthetic fixture@example.com")(
                    unit=unit("sec", "submissions"), timeout_seconds=1, max_bytes=1024)
        self.assertEqual(caught.exception.category, "network_error")
        self.assertNotIn("secret-fixture-token", json.dumps(caught.exception.diagnostic()))

    def test_cli_logs_fixed_reason_without_exception_payload(self):
        from quant_data.operations import sec_selected_refresh as cli
        cases = [
            ("A pending provider request has no retained response; no retry",
             "unclassified_pending_request_no_retry"),
            ("secret-fixture-token", "unclassified_safety_failure"),
        ]
        for message, reason in cases:
            with self.subTest(reason=reason), patch.object(cli, "load_registry",
                    side_effect=ConflictError(message)), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main([]), 75)
            result = json.loads(out.getvalue())
            self.assertEqual(result["reason"], reason)
            self.assertNotIn("secret-fixture-token", out.getvalue())
