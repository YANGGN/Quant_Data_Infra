"""Offline bounds and failure isolation for the fixed company fetch wrapper."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from quant_data.errors import ConflictError
from quant_data.operations import company_market_refresh as operation

DISCOVERY = json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}).encode()

class CompanyMarketRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.now = 0.0
        self.calls = []
        self.publisher = SimpleNamespace(publish=Mock(return_value=SimpleNamespace(outcome="succeeded", written_count=0)))

    def sleep(self, seconds):
        self.now += seconds

    def fetch(self, **kwargs):
        self.calls.append((self.now, kwargs))
        return SimpleNamespace(status=200, media_type="application/json", body=b"[]", redirected=False)

    def run_refresh(self, **overrides):
        args = dict(roster=(operation.MarketCompanySecurity("AAPL", "fmp-equity-AAPL"),),
                    issuers={"0000320193": "issuer-apple"}, discovery_body=DISCOVERY,
                    fetch=self.fetch, publisher=self.publisher, state_root=self.root,
                    started=0.0, monotonic=lambda: self.now, sleeper=self.sleep,
                    utcnow=lambda: datetime(2026,9,5,4,tzinfo=timezone.utc))
        args.update(overrides)
        return operation.run_company_market_refresh(**args)

    def test_capped_estimates_publish_but_report_partial_coverage(self):
        rows = [{"symbol":"AAPL","date":f"{2021+i}-09-30","epsAvg":1.0} for i in range(10)]
        def fetch(**kwargs):
            response = self.fetch(**kwargs)
            if kwargs["source"] == "analyst_estimates":
                response.body = json.dumps(rows).encode()
            return response
        report = self.run_refresh(fetch=fetch)
        self.assertEqual(self.publisher.publish.call_count, 3)
        self.assertEqual(report["failed_steps"], 0)
        self.assertEqual(report["partial_steps"], 1)
        self.assertEqual(report["outcome"], "partial")
        self.assertNotEqual(report["exit_code"], 0)
        step = report["steps"][-1]
        self.assertEqual(step["publication_outcome"], "succeeded")
        self.assertEqual(step["completeness"], "partial")
        self.assertIn("fmp_analyst_estimates_may_be_truncated_at_limit_10", step["warnings"])

    def test_fixed_requests_are_paced_and_retain_private_bytes(self):
        report = self.run_refresh()
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["requests"], 4)
        self.assertEqual([k["source"] for _, k in self.calls], list(operation.SOURCES))
        self.assertEqual(self.calls[-1][1]["parameters"],
                         {"symbol": "AAPL", "period": "annual", "page": "0", "limit": "10"})
        self.assertGreaterEqual(self.calls[1][0] - self.calls[0][0], 0.4)
        self.assertEqual(self.publisher.publish.call_count, 3)
        for blob in (self.root / "blobs").iterdir():
            self.assertEqual(blob.stat().st_mode & 0o777, 0o600)

    def test_unavailable_source_does_not_block_siblings_or_retry(self):
        def fetch(**kwargs):
            response = self.fetch(**kwargs)
            if kwargs["source"] == "dividends":
                response.status = 402
            return response
        report = self.run_refresh(fetch=fetch)
        self.assertEqual(report["failed_steps"], 1)
        self.assertEqual(report["attempted_steps"], 3)
        self.assertEqual(report["requests"], 4)
        self.assertEqual(self.publisher.publish.call_count, 2)
        self.assertEqual(report["steps"][0]["error"], "unavailable")

    def test_wrong_symbol_is_not_published_but_next_source_is_attempted(self):
        def fetch(**kwargs):
            response = self.fetch(**kwargs)
            if kwargs["source"] == "dividends":
                response.body = b'[{"symbol":"MSFT","date":"2026-08-10","dividend":0.2}]'
            return response
        report = self.run_refresh(fetch=fetch)
        self.assertEqual(report["failed_steps"], 1)
        self.assertEqual(self.publisher.publish.call_count, 2)

    def test_deadline_prevents_further_requests(self):
        self.now = operation.MAX_RUN_SECONDS
        report = self.run_refresh()
        self.assertTrue(report["deadline_or_cap_exhausted"])
        self.assertEqual(report["requests"], 1)
        self.assertEqual(self.calls, [])

    def test_unknown_or_ambiguous_identities_are_reported_without_fetch(self):
        report = self.run_refresh(issuers={})
        self.assertEqual(report["missing_issuer_count"], 1)
        self.assertEqual(self.calls, [])
        ambiguous = json.dumps({"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple"},
                                "1":{"cik_str":123456,"ticker":"AAPL","title":"Different"}}).encode()
        report = self.run_refresh(discovery_body=ambiguous)
        self.assertEqual(report["ambiguous_symbols"], ["AAPL"])
        self.assertEqual(self.calls, [])

    def test_blob_replay_preserves_file_and_rejects_symlink(self):
        reference = operation.retain_blob(self.root, b"[]")
        target = self.root / "blobs" / reference.rsplit("/", 1)[-1]
        before = target.stat()
        self.assertEqual(operation.retain_blob(self.root, b"[]"), reference)
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        target.unlink()
        elsewhere = self.root / "other.json"
        elsewhere.write_bytes(b"[]")
        target.symlink_to(elsewhere)
        with self.assertRaises(ConflictError):
            operation.retain_blob(self.root, b"[]")

    def test_cli_rejects_paths_and_sanitizes_errors(self):
        out = StringIO()
        with patch.object(operation, "refresh_company_market_live") as live, redirect_stdout(out):
            self.assertEqual(operation.main(["--store", "secret"]), 64)
        live.assert_not_called()
        self.assertNotIn("secret", out.getvalue())
        out = StringIO()
        with patch.object(operation, "refresh_company_market_live", side_effect=RuntimeError("api-key-secret")), redirect_stdout(out):
            self.assertEqual(operation.main([]), 75)
        self.assertNotIn("api-key-secret", out.getvalue())
