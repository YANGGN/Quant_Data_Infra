from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.operations.company_market_refresh import MarketCompanySecurity
from quant_data.operations import fmp_analyst_backfill as subject


class Response:
    def __init__(self, status=200, body=b"[]", media_type="application/json", redirected=False):
        self.status = status
        self.body = body
        self.media_type = media_type
        self.redirected = redirected


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []
    def monotonic(self):
        return self.now
    def wall(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
    def utc(self):
        return datetime(2026, 9, 7, tzinfo=timezone.utc)


class Sec:
    def __init__(self, body):
        self.body = body
        self.calls = 0
    def request(self, **kwargs):
        self.calls += 1
        return Response(body=self.body)


class Fmp:
    def __init__(self, responder=None):
        self.calls: list[dict[str, object]] = []
        self.responder = responder or (lambda **kwargs: Response())
    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responder(**kwargs)


class FmpAnalystBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state = root / "ledger"
        self.blobs = root / "blobs"
        self.clock = Clock()
        self.roster = (MarketCompanySecurity("ZZZ", "i-zzz"),)
        self.issuers = {"0000000001": "issuer-1"}
        self.discovery = json.dumps({"0": {"ticker": "ZZZ", "cik_str": 1}}).encode()
    def tearDown(self):
        self.temp.cleanup()
    def capture(self, fmp=None):
        return subject.capture_universe(
            roster=self.roster, issuers=self.issuers, sec_transport=Sec(self.discovery),
            fmp_transport=fmp or Fmp(), fmp_key="test-key", sec_headers={"Accept": "application/json"},
            state_root=self.state, blob_root=self.blobs, monotonic=self.clock.monotonic,
            wall_clock=self.clock.wall, sleeper=self.clock.sleep, utcnow=self.clock.utc,
        )

    def test_capture_retains_receipts_and_resumes_without_retry(self):
        fmp = Fmp()
        report = self.capture(fmp)
        self.assertEqual(report["outcome"], "succeeded")
        self.assertEqual(report["requests"], 10)  # discovery, annual/quarter, seven other endpoints
        self.assertEqual(len(fmp.calls), 9)
        self.assertTrue(all("apikey" not in record for record in report["records"]))
        attempts = list((self.state / "attempts").glob("*.json"))
        results = list((self.state / "results").glob("*.json"))
        self.assertEqual((len(attempts), len(results)), (10, 10))
        self.assertGreaterEqual(len(self.clock.sleeps), 1)
        legacy = next(call for call in fmp.calls if call["url"] == "https://financialmodelingprep.com/api/v4/price-target")
        self.assertEqual(legacy["parameters"]["symbol"], "ZZZ")
        grades = next(record for record in report["records"] if record.get("source") == "grades")
        self.assertEqual(grades["nature"], "history")
        resumed = Fmp()
        report2 = self.capture(resumed)
        self.assertEqual(resumed.calls, [])
        self.assertEqual(report2["requests"], 10)

    def test_analyst_pagination_flags_repeated_page_and_stops_period(self):
        rows = [{"date": (date(2030, 1, 1) - timedelta(days=n)).isoformat(), "n": n} for n in range(100)]
        encoded = json.dumps(rows).encode()
        def responder(**kwargs):
            params = kwargs["parameters"]
            if params.get("period") == "annual" and params.get("page") in {"0", "1"}:
                return Response(body=encoded)
            return Response()
        report = self.capture(Fmp(responder))
        annual = [r for r in report["records"] if r.get("source") == "analyst_estimates" and r.get("period") == "annual"]
        self.assertEqual([r["page"] for r in annual], [0, 1])
        self.assertEqual(annual[-1]["outcome"], "repeated_page")
        self.assertFalse(any(r.get("page") == 2 for r in annual))

    def test_estimate_pagination_stops_when_revisions_do_not_add_or_extend_dates(self):
        dates = [(date(2030, 1, 1) - timedelta(days=n)).isoformat() for n in range(100)]
        first = [{"date": value, "revision": 1} for value in dates]
        second = [{"date": value, "revision": 2} for value in reversed(dates)]
        def responder(**kwargs):
            params = kwargs["parameters"]
            if params.get("period") == "annual" and params.get("page") == "0":
                return Response(body=json.dumps(first).encode())
            if params.get("period") == "annual" and params.get("page") == "1":
                return Response(body=json.dumps(second).encode())
            return Response()
        report = self.capture(Fmp(responder))
        annual = [r for r in report["records"] if r.get("source") == "analyst_estimates" and r.get("period") == "annual"]
        self.assertEqual(annual[-1]["outcome"], "non_progressing_target_dates")
        evidence = json.loads((self.state / "page_stops" / (annual[-1]["request_id"] + ".json")).read_text())
        self.assertEqual(evidence["reason"], "non_progressing_target_dates")

    def test_estimate_pagination_stops_when_new_dates_do_not_extend_history(self):
        dates = [(date(2030, 1, 1) - timedelta(days=n)).isoformat() for n in range(100)]
        first = [{"date": value} for value in dates]
        second = [{"date": value} for value in dates[:-1]] + [{"date": "2031-01-01"}]
        def responder(**kwargs):
            params = kwargs["parameters"]
            if params.get("period") == "annual" and params.get("page") == "0":
                return Response(body=json.dumps(first).encode())
            if params.get("period") == "annual" and params.get("page") == "1":
                return Response(body=json.dumps(second).encode())
            return Response()
        report = self.capture(Fmp(responder))
        annual = [r for r in report["records"] if r.get("source") == "analyst_estimates" and r.get("period") == "annual"]
        self.assertEqual(annual[-1]["outcome"], "non_extending_oldest_target_date")
        self.assertEqual(annual[-1]["page_stop_reason"], "non_extending_oldest_target_date")

    def test_immutable_plan_rejects_binding_drift_before_a_second_discovery(self):
        self.capture(Fmp())
        self.roster = (MarketCompanySecurity("CHANGED", "i-zzz"),)
        sec = Sec(self.discovery)
        with self.assertRaises(subject.ConflictError):
            subject.capture_universe(
                roster=self.roster, issuers=self.issuers, sec_transport=sec, fmp_transport=Fmp(),
                fmp_key="test-key", sec_headers={"Accept": "application/json"}, state_root=self.state,
                blob_root=self.blobs, monotonic=self.clock.monotonic, wall_clock=self.clock.wall,
                sleeper=self.clock.sleep, utcnow=self.clock.utc,
            )
        self.assertEqual(sec.calls, 0)
        plan = json.loads((self.state / "plan.json").read_text())
        self.assertIn("issuer_bindings_sha256", plan)
        self.assertIn("caps", plan)

    def test_systemic_error_stops_and_402_blocks_only_that_endpoint(self):
        def responder(**kwargs):
            if kwargs["url"].endswith("/grades"):
                return Response(status=402, body=b'{"message":"unavailable"}')
            if kwargs["url"].endswith("/grades-historical"):
                return Response(status=429, body=b'{"message":"rate"}')
            return Response()
        report = self.capture(Fmp(responder))
        self.assertEqual(report["stop_reason"], "systemic_http_429")
        self.assertIn("grades", report["blocked_endpoints"])
        self.assertEqual(report["outcome"], "partial")

    def test_request_cap_stops_after_discovery_without_hidden_retry(self):
        with patch.object(subject, "REQUEST_CAP", 1):
            report = self.capture(Fmp())
        self.assertEqual(report["requests"], 1)
        self.assertEqual(report["stop_reason"], "aggregate_cap")

    def test_broad_phase_skips_the_completed_aapl_and_msft_pilot_symbols(self):
        self.roster = (
            MarketCompanySecurity("AAPL", "i-aapl"), MarketCompanySecurity("MSFT", "i-msft"),
            MarketCompanySecurity("ZZZ", "i-zzz"),
        )
        self.issuers = {"0000000001": "issuer-a", "0000000002": "issuer-m", "0000000003": "issuer-z"}
        self.discovery = json.dumps({
            "0": {"ticker": "AAPL", "cik_str": 1}, "1": {"ticker": "MSFT", "cik_str": 2},
            "2": {"ticker": "ZZZ", "cik_str": 3},
        }).encode()
        fmp = Fmp()
        report = self.capture(fmp)
        self.assertEqual(report["eligible_symbols"], ["ZZZ"])
        self.assertTrue(all(call["parameters"].get("symbol") == "ZZZ" for call in fmp.calls))

    def test_persisted_rate_wait_respects_the_cumulative_deadline(self):
        with patch.object(subject, "MAX_RUN_SECONDS", 0.1):
            report = self.capture(Fmp())
        self.assertEqual(report["requests"], 0)
        self.assertEqual(report["stop_reason"], "aggregate_deadline")

    def test_processing_time_is_persisted_and_counts_against_deadline(self):
        original = subject._analysis
        def delayed_analysis(*args, **kwargs):
            self.clock.now += 0.3
            return original(*args, **kwargs)
        with patch.object(subject, "MAX_RUN_SECONDS", 1.15), patch.object(subject, "_analysis", side_effect=delayed_analysis):
            report = self.capture(Fmp())
        self.assertEqual(report["requests"], 1)
        self.assertEqual(report["stop_reason"], "aggregate_deadline")
        analysis = json.loads(next((self.state / "analyses").glob("*.json")).read_text())
        self.assertGreaterEqual(analysis["consumed_seconds"], 0.3)

    def test_lost_attempt_reservation_never_invokes_transport(self):
        original = subject._write
        def lose_attempt(path, value):
            if path.parent.name == "attempts":
                return False
            return original(path, value)
        sec = Sec(self.discovery)
        with patch.object(subject, "_write", side_effect=lose_attempt):
            report = subject.capture_universe(
                roster=self.roster, issuers=self.issuers, sec_transport=sec, fmp_transport=Fmp(),
                fmp_key="test-key", sec_headers={"Accept": "application/json"}, state_root=self.state,
                blob_root=self.blobs, monotonic=self.clock.monotonic, wall_clock=self.clock.wall,
                sleeper=self.clock.sleep, utcnow=self.clock.utc,
            )
        self.assertEqual(sec.calls, 0)
        self.assertEqual(report["stop_reason"], "uncertain_reserved_attempt")

    def test_missing_existing_issuer_symbols_are_partial_coverage(self):
        self.issuers = {}
        report = self.capture(Fmp())
        self.assertEqual(report["outcome"], "partial")
        self.assertEqual(report["missing_existing_issuer_symbols"], ["ZZZ"])
        self.assertEqual(report["records"][-1]["source"], "sec_ticker_discovery")

    def test_total_wall_transport_terminates_a_slow_child(self):
        class SlowTransport:
            def request(self, **kwargs):
                time.sleep(1.5)
                return Response()
        started = time.monotonic()
        with self.assertRaises(subject.StoreUnavailableError):
            subject._TotalWallTransport(SlowTransport()).request(
                url="https://example.invalid", parameters={}, headers={}, timeout_seconds=1, max_bytes=1024,
            )
        self.assertLess(time.monotonic() - started, 1.45)

    def test_malformed_response_is_retained_before_validation(self):
        report = self.capture(Fmp(lambda **kwargs: Response(body=b"not json")))
        first = next(r for r in report["records"] if r.get("source") == "analyst_estimates")
        self.assertEqual(first["outcome"], "malformed_json")
        result = json.loads(next((self.state / "results").glob("*.json")).read_text())
        self.assertIn("reference", result)
        self.assertNotIn("test-key", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
