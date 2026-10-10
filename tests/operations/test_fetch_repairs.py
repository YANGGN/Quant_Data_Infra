"""September 30 failures: isolated admissions, retained parsers and safe diagnostics."""
import hashlib
import json
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.operations import collection_provider_policy as policy
from quant_data.operations.collection_queue import run_queue
from quant_data.operations.fetch_run_summary import summarize_report
from tests.operations import test_collection_provider_policy as gate_fixture
from tests.operations import test_collection_work as queue_fixture
from tests.company import test_sec_companyfacts as sec_fixture


class FmpRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.seed = gate_fixture.FmpSharedAllowanceTests("runTest")
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)

    def legacy_stop(self):
        f = self.seed
        f.invoke("maintenance")
        state = f.state()
        state["stopped"] = {"attempt":"2026-09-09-2", "status":"not_dispatched",
                            "observed_at":state["last_request_at"]}
        policy.atomic(f.root/"allowance.json", state, replace=True)
        return self.digest()

    def digest(self):
        return hashlib.sha256((self.seed.root/"allowance.json").read_bytes()).hexdigest()

    def repair(self, digest):
        return self.seed.gate().reconcile_not_dispatched(
            expected_attempt="2026-09-09-2", expected_state_sha256=digest)

    def test_expired_unsent_attempt_does_not_block_next_day_or_refund(self):
        f = self.seed
        f.test_expiry_during_reservation_does_not_dispatch()
        original = (f.root/"attempts/2026-09-09-2.json").read_bytes()
        f.at += timedelta(days=1)
        f.invoke("maintenance")
        self.assertEqual(len(f.calls), 1)
        self.assertEqual(f.state()["usage"], {"2026-09-09":2, "2026-09-10":1})
        self.assertEqual(original, (f.root/"attempts/2026-09-09-2.json").read_bytes())

    def test_exact_reconciliation_preserves_original_bytes_and_accounting(self):
        before_hash = self.legacy_stop()
        before = (self.seed.root/"allowance.json").read_bytes()
        receipt = self.repair(before_hash)
        self.assertEqual(receipt["before_text"].encode(), before)
        self.assertEqual(receipt["provider_requests"], 0)
        self.assertEqual(receipt["refunded_charges"], 0)
        expected = json.loads(before); expected["stopped"] = None
        self.assertEqual(self.seed.state(), expected)
        self.assertEqual(receipt["after_sha256"], self.digest())
        self.seed.invoke("maintenance")
        self.assertEqual(self.seed.state()["usage"]["2026-09-09"], 3)

    def test_reconciliation_rejects_stale_hash_pending_http_and_policy_changes(self):
        original_hash = self.legacy_stop()
        original = self.seed.state()
        for mutation in (
            {"pending":{"uncertain":True}},
            {"stopped":{**original["stopped"],"status":429}},
            {"policy_sha256":"f"*64},
            {"last_request_at":"2026-09-09T00:00:01.000000Z"},
            {"usage":{"2026-09-09":3}},
        ):
            with self.subTest(mutation=mutation):
                changed = {**original, **mutation}
                policy.atomic(self.seed.root/"allowance.json", changed, replace=True)
                before = (self.seed.root/"allowance.json").read_bytes()
                with self.assertRaises(ConflictError): self.repair(self.digest())
                self.assertEqual(before, (self.seed.root/"allowance.json").read_bytes())
        with self.assertRaises(ConflictError): self.repair(original_hash)

    def test_failed_final_save_can_finish_same_evidenced_transition(self):
        digest = self.legacy_stop()
        original = policy.atomic
        def fail(path, body, **kw):
            if path.name == "allowance.json": raise OSError("fixture")
            return original(path, body, **kw)
        with patch.object(policy, "atomic", side_effect=fail):
            with self.assertRaises(OSError): self.repair(digest)
        self.assertEqual(self.digest(), digest)
        receipt = self.repair(digest)
        self.assertEqual(self.digest(), receipt["after_sha256"])

    def test_unsent_evidence_failure_keeps_conservative_pending(self):
        f = self.seed
        original = policy.atomic
        def fail(path, body, **kw):
            if path.parent.name == "not-dispatched": raise OSError("fixture")
            return original(path, body, **kw)
        with patch.object(policy, "atomic", side_effect=fail):
            with self.assertRaises(OSError): f.test_expiry_during_reservation_does_not_dispatch()
        self.assertIsNotNone(f.state()["pending"])
        self.assertEqual(f.calls, [])
        f.invoke("maintenance")
        self.assertIsNone(f.state()["pending"])
        self.assertTrue((f.root/"uncertain/2026-09-09-2.json").exists())

    def test_callback_cannot_claim_it_was_not_dispatched(self):
        gate = Mock()
        gate.invoke.side_effect = lambda callback, **kw: callback(1)
        callback = Mock(side_effect=policy.FmpRequestNotDispatched("synthetic"))
        with patch.object(policy, "host_allowance", return_value=gate):
            with self.assertRaises(ConflictError) as result:
                policy.invoke_host_fmp(callback, request_material={"symbol":"AAPL"},
                                       priority="maintenance", timeout_seconds=5)
        self.assertNotIsInstance(result.exception, policy.FmpRequestNotDispatched)
        callback.assert_called_once()


class QueueAdmissionTests(unittest.TestCase):
    def test_proven_unsent_unit_is_terminal_but_other_units_can_progress(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            root = Path(folder)/"queue"
            denied = Mock(side_effect=policy.FmpRequestNotDispatched("blocked"))
            def run(plan, fetch):
                return run_queue(root=root, plan=plan, provider="fmp", fetch=fetch,
                    publish=lambda **kw: {"outcome":"succeeded"}, utcnow=queue_fixture.clock,
                    sleeper=lambda _: None)
            original = queue_fixture.plan()
            result = run(original, denied)
            self.assertEqual(result["outcome"], "provider_admission_blocked")
            state = json.loads((root/"ledger.json").read_bytes())
            self.assertIsNone(state["pending"])
            self.assertEqual(state["usage"]["2026-09-09"]["charged_attempts"], 1)
            repeat = Mock(side_effect=AssertionError("No retry"))
            self.assertEqual(run(original, repeat)["requests"], 0)
            next_unit = queue_fixture.unit(symbol="MSFT")
            response = queue_fixture.QueueResponse(200, b"[]", queue_fixture.AT)
            self.assertEqual(run(queue_fixture.plan((next_unit,)), Mock(return_value=response))["published"], 1)
            denied.assert_called_once()
            self.assertTrue((root/"not-dispatched"/(original.units[0].unit_id+".json")).exists())

    def test_ambiguous_callback_is_terminal_without_retry(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            kwargs = dict(root=Path(folder)/"queue", plan=queue_fixture.plan(), provider="fmp",
                fetch=Mock(side_effect=OSError("ambiguous")), publish=None, acquire_only=True,
                utcnow=queue_fixture.clock)
            self.assertEqual(run_queue(**kwargs)["outcome"], "acquired_with_gaps")
            kwargs["fetch"] = Mock(side_effect=AssertionError("No repeat"))
            self.assertEqual(run_queue(**kwargs)["requests"], 0)
            kwargs["fetch"].assert_not_called()


class SecRepairTests(unittest.TestCase):
    def submissions(self, count, cik="0000019617"):
        value = json.loads(sec_fixture._submissions_body())
        value["cik"] = cik
        fields = value["filings"]["recent"]
        for key, values in fields.items(): fields[key] = values * count
        fields["accessionNumber"] = [cik+"-26-"+str(n).zfill(6) for n in range(count)]
        return json.dumps(value).encode()

    def test_generic_submissions_accept_more_than_ten_thousand_without_truncation(self):
        from quant_data.company.sec_submissions import parse_submissions
        parsed = parse_submissions(cik="0000019617", body=self.submissions(10001),
            captured_at=datetime(2026,9,30,tzinfo=timezone.utc))
        self.assertEqual(len(parsed.filings), 10001)
        self.assertEqual(parsed.filings[-1].accession_number, "0000019617-26-010000")

    def test_large_generic_identity_uses_existing_sec_semantic_budget(self):
        from quant_data.company.sec_submissions import parse_submissions
        from quant_data.json_codec import MAX_JSON_BYTES, dumps_strict
        parsed = parse_submissions(cik="0000019617", body=self.submissions(26408),
            captured_at=datetime(2026,9,30,tzinfo=timezone.utc))
        self.assertEqual(len(parsed.filings), 26408)
        with self.assertRaises(ResourceLimitError):
            dumps_strict([f.semantic_mapping() for f in parsed.filings], max_bytes=MAX_JSON_BYTES)

    def test_standalone_aapl_and_frozen_bundle_keep_distinct_bounds(self):
        from quant_data.company.sec_submissions import parse_submissions
        from quant_data.company.sec_companyfacts import (
            AAPL_CIK, MAX_SUBMISSION_FILINGS, parse_sec_aapl_bundle)
        at = datetime(2026,9,30,tzinfo=timezone.utc)
        body = self.submissions(MAX_SUBMISSION_FILINGS+1, AAPL_CIK)
        self.assertEqual(len(parse_submissions(cik=AAPL_CIK, body=body,
                         captured_at=at).filings), MAX_SUBMISSION_FILINGS+1)
        with self.assertRaises(ValidationError):
            parse_sec_aapl_bundle(submissions_body=body,
                companyfacts_body=sec_fixture._companyfacts_body(), captured_at=at)
        for cik in (AAPL_CIK, "0000019617"):
            with self.subTest(cik=cik), self.assertRaises(ValidationError):
                parse_submissions(cik=cik, body=self.submissions(50001,cik), captured_at=at)

    def test_absent_core_facts_have_specific_unsupported_classification(self):
        from quant_data.company.sec_companyfacts import parse_sec_aapl_bundle, SecCoreFactsUnavailable
        body = json.loads(sec_fixture._companyfacts_body());body["facts"] = {}
        with self.assertRaises(SecCoreFactsUnavailable):
            parse_sec_aapl_bundle(submissions_body=sec_fixture._submissions_body(),
                companyfacts_body=json.dumps(body).encode(),
                captured_at=datetime(2026,9,30,tzinfo=timezone.utc))

    def test_sec_counts_are_disjoint_and_sum_to_selected_issuers(self):
        counts = summarize_report("quant-data-sec-company-fundamentals.timer", {
            "succeeded_cik_count":2208,"skipped_existing_cik_count":2151,
            "failed_cik_count":16,"partial_cik_count":0,"unattempted_cik_count":0})
        self.assertEqual(counts["successful"], 57)
        self.assertEqual(sum(v for k,v in counts.items() if k!="unit"), 2224)
        with self.assertRaises(ValueError):
            summarize_report("quant-data-sec-company-fundamentals.timer", {
                "succeeded_cik_count":1,"skipped_existing_cik_count":2,"failed_cik_count":0})


class DiagnosticTests(unittest.TestCase):
    def test_macro_limits_and_fmp_admission_keep_safe_specific_categories(self):
        from quant_data.operations.macro_current_refresh import _failure_details
        for message, expected in (
            ("CFTC COT provider pagination exceeds its page bound","pagination_limit"),
            ("NY Fed primary-dealer request exceeds its time bound","duration_limit"),
            ("NY Fed primary-dealer responses exceed total byte bound","total_response_byte_limit"),
            ("CFTC COT normalized result exceeds its row bound","row_limit"),
            ("NY Fed primary-dealer selected catalog exceeds its key bound","series_limit"),
            ("NY Fed primary-dealer history exceeds its row bound","row_limit"),
            ("secret=do-not-leak","resource_limit"),
        ):
            self.assertEqual(_failure_details(ResourceLimitError(message)), (74,expected))
        self.assertEqual(_failure_details(policy.FmpRequestNotDispatched("private")),
                         (75,"fmp_not_dispatched"))

    def test_theta_error_retains_argument_reason_but_redacts_credentials(self):
        from quant_data.options.transport import sanitized_provider_detail
        raw = ("start_date must precede end_date; key short-secret "
               "https://provider.example/error?apikey=hidden token=abc "
               "Bearer def user@example.com "+("opaque"*8))
        safe = sanitized_provider_detail(raw, secrets=("short-secret",))
        self.assertIn("start_date must precede end_date", safe)
        for value in ("short-secret","hidden","abc","def","user@example.com","opaque"*8):
            self.assertNotIn(value, safe)
        self.assertLessEqual(len(safe), 400)

    def test_theta_redaction_handles_quoted_keys_values_and_control_characters(self):
        from quant_data.options.transport import sanitized_provider_detail
        raw = '\'token\': \'tiny secret\'; "password": "a b c"; API_KEY=short; Bearer abc'
        safe = sanitized_provider_detail(raw)
        for value in ("tiny", "a b c", "short", "abc"):
            self.assertNotIn(value, safe)
        self.assertFalse(any(ord(char)<32 for char in safe))
        self.assertEqual(sanitized_provider_detail("x"*8193), "Provider detail unavailable")

    def test_theta_redacts_compound_token_keys_and_full_authorization(self):
        from quant_data.options.transport import sanitized_provider_detail
        for raw, secret in (
            ("access_token=abc123", "abc123"),
            ("refresh_token: xyz789", "xyz789"),
            ("Authorization: Basic YTpi", "YTpi"),
            ("idToken=short", "short"),
            ('Authorization: Digest username="private", realm="site", nonce="abc123", response="tiny"', "abc123"),
            ('Authorization: Digest username="private", realm="site", nonce="abc123", response="tiny"', "tiny"),
            ('"authorization": "Digest username=private response=tiny"', "private"),
        ):
            with self.subTest(raw=raw):
                self.assertNotIn(secret, sanitized_provider_detail(raw))
        self.assertEqual(sanitized_provider_detail("invalid date\x1b[31m"), "invalid date")

    def test_theta_rpc_failure_receipt_contains_safe_detail_without_retry(self):
        from quant_data.options.transport import ThetaTransport, Budget, ProviderFailure
        class RpcError(Exception):
            def code(self): return types.SimpleNamespace(name="INVALID_ARGUMENT")
            def details(self): return "start_date cannot be today; token=private"
        client = types.SimpleNamespace(option_history_greeks_eod=Mock(side_effect=RpcError()))
        transport = ThetaTransport.__new__(ThetaTransport)
        transport.symbols = {"SPY"}; transport.budget = Budget()
        transport.client = client; transport._calls = threading.local()
        transport._receipt_lock = threading.Lock();transport.receipts=[]
        transport.receipt_callback = Mock();transport._redaction_values=()
        modules = {"grpc":types.SimpleNamespace(RpcError=RpcError),
                   "thetadata.errors":types.SimpleNamespace(NoDataFoundError=type("NoData", (Exception,), {}))}
        with patch.dict("sys.modules", modules):
            with self.assertRaises(ProviderFailure) as result:
                transport.request("option_history_greeks_eod", symbol="SPY")
        detail = result.exception.receipt["provider_detail"]
        self.assertIn("start_date cannot be today", detail)
        self.assertNotIn("private", detail)
        client.option_history_greeks_eod.assert_called_once()
        transport.receipt_callback.assert_called_once()
