"""Empty-quarter continuation uses temporary checkpoints and fake network only."""
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.company.equibles_transcripts import validate_bundle
from quant_data.errors import ValidationError
from quant_data.operations import equibles_transcript_backfill as job
from quant_data.operations import equibles_parallel_backfill as parallel
from quant_data.inspector_equibles import read_equibles_progress
from tests.company import test_equibles_transcripts as fixtures
from tests.operations import test_equibles_paid_policy as paid
from tests.operations.test_equibles_parallel_backfill import Transport


class EmptyQuarterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private"
        job.freeze(self.root, [{"symbol": s, "instrument_id": "frozen-" + s} for s in ("A", "B")])
        self.publisher = fixtures.FakePublisher()

    def run_job(self, transport):
        return job.run(self.root, self.publisher, transport,
                       clock=lambda: fixtures.AT, sleeper=lambda _: None)

    def state(self):
        return json.loads((self.root / "state.json").read_bytes())

    def test_empty_quarters_advance_in_order_preserve_evidence_and_replay_without_gets(self):
        empty1 = fixtures.body("A", 1, total=0, count=0)
        empty3 = fixtures.body("A", 3, total=0, count=0)
        transport = fixtures.FakeTransport([
            fixtures.catalog("A", (4, 1, 3, 2)), empty1, fixtures.body("A", 2),
            empty3, fixtures.body("A", 4), fixtures.catalog("B"), fixtures.body("B")])
        report = self.run_job(transport)
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(self.publisher.calls, [("A", 2), ("A", 4), ("B", 1)])
        self.assertEqual(report["transcripts"], 3)
        self.assertEqual(report["skipped_quarters"], 2)
        self.assertEqual(report["quota"]["attempted"], 7)
        completed = self.state()["completed"]["A"]
        self.assertEqual(completed["status"], "completed_with_transcript_gaps")
        self.assertEqual(completed["without_transcript"], 0)
        for gap, raw, quarter in zip(completed["unavailable_transcripts"], (empty1, empty3), (1, 3)):
            self.assertEqual(gap["fiscal_quarter"], quarter)
            self.assertEqual(gap["reason"], "provider_returned_empty_transcript")
            self.assertEqual(gap["http_status"], 200)
            self.assertEqual(gap["response_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(job.retained(self.root, gap["path"])[1], raw)
            self.assertEqual(gap["partial_page_paths"], [])
        replay = fixtures.FakeTransport([])
        self.assertEqual(self.run_job(replay)["requests_this_run"], 0)
        self.assertEqual(replay.paths, [])
        self.assertEqual(len(self.publisher.calls), 3)
        os.utime(self.root / "status.json", (fixtures.AT.timestamp(), fixtures.AT.timestamp()))
        self.assertTrue(read_equibles_progress(self.root, observed_at=fixtures.AT)["available"])

    def test_null_quarters_advance_preserve_raw_evidence_and_do_not_refetch(self):
        envelope = json.loads(fixtures.body("A", 2, total=0, count=0))
        envelope["data"] = None
        raw_values = (b" \nnull\t", json.dumps(envelope).encode())
        transport = fixtures.FakeTransport([
            fixtures.catalog("A", (1, 2, 3)), *raw_values, fixtures.body("A", 3),
            fixtures.catalog("B"), fixtures.body("B")])
        report = self.run_job(transport)
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(self.publisher.calls, [("A", 3), ("B", 1)])
        self.assertEqual(report["skipped_quarters"], 2)
        self.assertEqual(report["quota"]["attempted"], 6)
        completed = self.state()["completed"]["A"]
        self.assertEqual(completed["status"], "completed_with_transcript_gaps")
        for gap, raw in zip(completed["unavailable_transcripts"], raw_values):
            self.assertEqual(gap["reason"], "provider_returned_empty_transcript")
            self.assertEqual(gap["http_status"], 200)
            self.assertEqual(job.retained(self.root, gap["path"])[1], raw)
            self.assertEqual(gap["response_sha256"], hashlib.sha256(raw).hexdigest())
            with self.assertRaises(ValidationError):
                validate_bundle("A", "frozen-A", fixtures.event(), (fixtures.page(raw),))
        self.assertEqual(self.run_job(fixtures.FakeTransport([]))["requests_this_run"], 0)
        self.assertEqual(len(self.publisher.calls), 2)

    def test_null_later_page_preserves_partial_call_and_blocks(self):
        first = fixtures.body(total=2, count=1)
        report = self.run_job(fixtures.FakeTransport([fixtures.catalog("A"), first, b"null"]))
        self.assertEqual(report["outcome"], "blocked")
        self.assertEqual(report["skipped_quarters"], 0)
        self.assertEqual(self.publisher.calls, [])
        current = self.state()["current"]
        self.assertEqual(current["event_index"], 0)
        self.assertEqual(current["offset"], 1)
        self.assertEqual(job.retained(self.root, current["pages"][0])[1], first)

    def test_null_envelope_still_requires_identity_counts_and_pagination(self):
        original = json.loads(fixtures.body(total=0, count=0))
        original["data"] = None
        for key, value in (("ticker", "B"), ("eventId", "other"), ("turnCount", 1),
                           ("totalTurnCount", 1), ("hasMore", True), ("offset", 1)):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError):
                    job.empty_transcript_page(json.dumps({**original, key: value}).encode(),
                                              symbol="A", event=fixtures.event(), offset=0)
        with self.assertRaises(ValidationError):
            job.empty_transcript_page(b'{"data":null}', symbol="A", event=fixtures.event(), offset=0)
        for raw in (b'[]', b'"null"', b'null true'):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                job.empty_transcript_page(raw, symbol="A", event=fixtures.event(), offset=0)

    def test_null_catalogue_is_not_a_missing_transcript(self):
        report = self.run_job(fixtures.FakeTransport([b"null"]))
        self.assertEqual(report["outcome"], "blocked")
        self.assertEqual(report["skipped_quarters"], 0)
        self.assertEqual(self.publisher.calls, [])

    def test_empty_body_never_becomes_a_canonical_bundle(self):
        raw = fixtures.body(total=0, count=0)
        self.assertTrue(job.empty_transcript_page(raw, symbol="A", event=fixtures.event(), offset=0))
        with self.assertRaises(ValidationError):
            validate_bundle("A", "frozen-A", fixtures.event(), (fixtures.page(raw),))

    def test_wrong_scope_contradictory_counts_and_missing_fields_are_not_empty_quarters(self):
        original = json.loads(fixtures.body(total=0, count=0))
        changes = [
            ("ticker", "B"), ("eventId", "other"), ("fiscalYear", 2021),
            ("fiscalQuarter", 2), ("fiscalQuarter", True), ("offset", 1),
            ("turnCount", 1), ("totalTurnCount", 2), ("turnCount", False),
            ("hasMore", True), ("hasMore", 0), ("offset", None),
            ("totalTurnCount", None),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                bad = {**original, field: value}
                with self.assertRaises(ValidationError):
                    job.empty_transcript_page(json.dumps(bad).encode(),
                                              symbol="A", event=fixtures.event(), offset=0)

    def test_empty_later_page_remains_blocked_without_discarding_partial_evidence(self):
        first = fixtures.body(total=2, count=1)
        transport = fixtures.FakeTransport([
            fixtures.catalog("A", (1, 2)), first,
            fixtures.body(offset=1, total=0, count=0)])
        report = self.run_job(transport)
        self.assertEqual(report["outcome"], "blocked")
        self.assertEqual(report["skipped_quarters"], 0)
        self.assertEqual(self.publisher.calls, [])
        current = self.state()["current"]
        self.assertEqual(current["event_index"], 0)
        self.assertEqual(current["offset"], 1)
        self.assertEqual(job.retained(self.root, current["pages"][0])[1], first)
        self.assertEqual(len(transport.paths), 3)
        replay = fixtures.FakeTransport([])
        self.assertEqual(self.run_job(replay)["requests_this_run"], 0)
        self.assertEqual(replay.paths, [])

    def test_crash_before_gap_checkpoint_replays_retained_empty_without_refetch(self):
        class PowerLoss(BaseException):
            pass
        original = job.atomic
        def crash(path, value, **kwargs):
            if (path == self.root / "state.json" and isinstance(value, dict)
                    and (value.get("current") or {}).get("unavailable_transcripts")):
                raise PowerLoss()
            return original(path, value, **kwargs)
        empty = fixtures.body(total=0, count=0)
        with patch.object(job, "atomic", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.run_job(fixtures.FakeTransport([fixtures.catalog("A", (1, 2)), empty]))
        self.assertIsNone(self.state()["pending"])
        transport = fixtures.FakeTransport([
            fixtures.body("A", 2), fixtures.catalog("B"), fixtures.body("B")], remaining=98)
        report = self.run_job(transport)
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(report["quota"]["attempted"], 5)
        self.assertEqual(report["skipped_quarters"], 1)
        self.assertNotIn("/v1/stocks/A/earnings-calls/2020/1/speakers?limit=200&offset=0", transport.paths)


class ParallelEmptyQuarterTests(unittest.TestCase):
    def setUp(self):
        self.f = paid.EquiblesPaidPolicyTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.convert(paid.selection(("A",)))
        self.root = self.f.root
        self.publisher = fixtures.FakePublisher()
        state = self.f.state()
        events = [fixtures.event("A", q) for q in (1, 2, 3, 4)]
        state["current"] = {"catalog": events, "catalog_done": True, "event_index": 0,
                            "events": events, "pages": [], "offset": 0, "captured": 0}
        bucket = paid.quota_bucket(state, paid.AT.date().isoformat())
        bucket.update(paid_headers_verified=True, remaining=10000)
        self.f.save(state)
        spacing = patch.object(parallel, "SPACING_SECONDS", 0.01)
        spacing.start()
        self.addCleanup(spacing.stop)

    def batch(self, transport, requests=4):
        return parallel.acquire_batch(self.root, transport, max_requests=requests,
            max_bytes=32 * 1024 * 1024, deadline=time.monotonic() + 60,
            day=paid.AT.date().isoformat(), clock=lambda: paid.AT)

    def test_parallel_batch_empty_quarter_continues_and_keeps_quota_charged(self):
        path = parallel.planned_paths(self.f.state())[1]
        raw = fixtures.body("A", 2, total=0, count=0)
        transport = Transport(self.root, {path: raw})
        batch = self.batch(transport)
        self.assertEqual(batch["outcome"], "acquired")
        report = parallel.run_wave(self.root, self.publisher, transport,
                                   clock=lambda: paid.AT, max_run_seconds=120)
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(self.publisher.calls, [("A", 1), ("A", 3), ("A", 4)])
        self.assertEqual(report["skipped_quarters"], 1)
        self.assertEqual(len(transport.paths), 4)
        self.assertEqual(self.f.state()["usage"][paid.AT.date().isoformat()]["attempted"], 4)
        self.assertEqual(job.retained(self.root, path)[1], raw)

    def test_parallel_null_quarters_advance_without_canonical_empty_calls(self):
        paths = parallel.planned_paths(self.f.state())
        envelope = json.loads(fixtures.body("A", 2, total=0, count=0))
        envelope["data"] = None
        transport = Transport(self.root, {paths[0]: b"null", paths[1]: json.dumps(envelope).encode()})
        self.assertEqual(self.batch(transport)["outcome"], "acquired")
        report = parallel.run_wave(self.root, self.publisher, transport,
                                  clock=lambda: paid.AT, max_run_seconds=120)
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(report["skipped_quarters"], 2)
        self.assertEqual(self.publisher.calls, [("A", 3), ("A", 4)])
        self.assertEqual(len(transport.paths), 4)
        self.assertEqual(self.f.state()["usage"][paid.AT.date().isoformat()]["attempted"], 4)
        self.assertTrue(parallel.recover_batch(self.root, clock=lambda: paid.AT))
        self.assertEqual(job.retained(self.root, paths[0])[1], b"null")

    def test_null_with_missing_quota_headers_still_blocks(self):
        path = parallel.planned_paths(self.f.state())[0]
        transport = Transport(self.root, {path: b"null"})
        request = transport.request
        def missing_quota(path):
            status, headers, raw = request(path)
            headers.pop("x-ratelimit-remaining")
            return status, headers, raw
        transport.request = missing_quota
        self.assertEqual(self.batch(transport, requests=1)["outcome"], "blocked")
        self.assertIsNotNone(self.f.state()["pending"])
        self.assertEqual(self.publisher.calls, [])

    def test_previously_blocked_empty_batch_recovers_without_new_requests(self):
        path = parallel.planned_paths(self.f.state())[1]
        raw = fixtures.body("A", 2, total=0, count=0)
        transport = Transport(self.root, {path: raw})
        with patch.object(job, "empty_transcript_page", return_value=False):
            self.assertEqual(self.batch(transport, requests=2)["outcome"], "blocked")
        before = copy.deepcopy(self.f.state()["usage"])
        receipt_before = (self.root / "responses" / "2026-09-09-002.json").read_bytes()
        self.assertTrue(parallel.recover_batch(self.root, clock=lambda: paid.AT))
        after = self.f.state()["usage"]
        for day, bucket in before.items():
            self.assertEqual(after[day]["attempted"], bucket["attempted"])
            self.assertEqual(after[day]["remaining"], bucket["remaining"])
        self.assertEqual(after[paid.AT.date().isoformat()]["provider_remaining"], 9998)
        report = job.run(self.root, self.publisher, transport, clock=lambda: paid.AT,
                         allow_paid=True, cache_only=True)
        self.assertEqual(report["outcome"], "cache_exhausted")
        self.assertEqual(report["requests_this_run"], 0)
        self.assertEqual(self.publisher.calls, [("A", 1)])
        self.assertEqual(self.f.state()["current"]["event_index"], 2)
        self.assertEqual(report["skipped_quarters"], 1)
        self.assertEqual(len(transport.paths), 2)
        self.assertEqual((self.root / "responses" / "2026-09-09-002.json").read_bytes(), receipt_before)

    def test_empty_response_with_invalid_quota_headers_still_blocks(self):
        path = parallel.planned_paths(self.f.state())[0]
        transport = Transport(self.root, {path: fixtures.body("A", 1, total=0, count=0)})
        request = transport.request
        def missing_quota(path):
            status, headers, raw = request(path)
            headers.pop("x-ratelimit-remaining")
            return status, headers, raw
        transport.request = missing_quota
        self.assertEqual(self.batch(transport, requests=1)["outcome"], "blocked")
        self.assertIsNotNone(self.f.state()["blocked"])
        self.assertIsNotNone(self.f.state()["pending"])
        self.assertEqual(self.publisher.calls, [])


if __name__ == "__main__":
    unittest.main()
