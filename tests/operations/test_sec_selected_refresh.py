"""Isolated behavior checks for the selected SEC scheduled host adapter."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch
from quant_data.errors import ConflictError
from quant_data.operations.collection_queue import QueueResponse
from quant_data.operations.sec_selected_refresh import run_refresh
from quant_data.fingerprint import mutation_fingerprint
from tests.operations import test_collection_sec as sec_fixture
from tests.company import test_sec_companyfacts as payloads


class SelectedSecRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = sec_fixture.SelectedSecTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = Path(self.fixture.f.temp.name) / "scheduled-sec"
        self.now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        self.elapsed = 0
        self.calls = []
        self.missing = False
        self.uncertain = False

    def sleep(self, seconds):
        self.elapsed += seconds
        self.now += timedelta(seconds=seconds)

    def fetch(self, *, unit, **kwargs):
        self.calls.append(unit)
        if self.uncertain:
            raise OSError("synthetic transport failure")
        if self.missing and unit.endpoint == "companyfacts":
            result = QueueResponse(404, b"{}", self.now.isoformat())
        else:
            body = payloads._submissions_body() if unit.endpoint == "submissions" else payloads._companyfacts_body()
            result = QueueResponse(200, body, self.now.isoformat())
        self.sleep(1)
        return result

    def run_worker(self, cap=4500, seconds=21600):
        return run_refresh(root=self.root, stores=self.fixture.f.stores, registry=self.fixture.f.registry,
            selection=self.fixture.selection, fetch=self.fetch, request_limit=cap, second_limit=seconds,
            utcnow=lambda: self.now, monotonic=lambda: self.elapsed, sleeper=self.sleep)

    def test_one_pair_for_shared_issuer_and_no_same_date_repeat(self):
        result = self.run_worker()
        self.assertEqual((result["outcome"], result["selected_cik_count"], result["succeeded_cik_count"]),
            ("succeeded", 1, 1))
        self.assertEqual(len(self.calls), 2)
        before = mutation_fingerprint(self.fixture.f.stores)
        repeated = self.run_worker()
        self.assertEqual((repeated["outcome"], repeated["requests_this_run"]), ("already_recorded", 0))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))

    def test_one_request_resource_continuation_reuses_original_pair_half(self):
        before = mutation_fingerprint(self.fixture.f.stores)
        result = self.run_worker(cap=1)
        self.assertEqual((result["outcome"], result["partial_cik_count"]), ("partial", 1))
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        result = self.run_worker(cap=1)
        self.assertEqual((result["outcome"], result["succeeded_cik_count"]), ("succeeded", 1))
        self.assertEqual(len(self.calls), 2)

    def test_missing_companyfacts_is_an_explicit_partial_and_not_retried(self):
        self.missing = True
        result = self.run_worker()
        self.assertEqual((result["outcome"], result["partial_cik_count"]), ("partial", 1))
        self.run_worker()
        self.assertEqual(len(self.calls), 2)

    def test_uncertain_attempt_remains_charged_without_same_date_retry(self):
        self.uncertain = True
        with self.assertRaises(ConflictError):
            self.run_worker()
        with self.assertRaises(ConflictError):
            self.run_worker()
        self.assertEqual(len(self.calls), 1)

    def test_next_clock_date_is_a_new_observation_with_original_day_retained(self):
        self.run_worker()
        old_result = (self.root / "current/2026-09-10/scheduled-result.json").read_bytes()
        self.now += timedelta(days=1)
        self.assertEqual(self.run_worker()["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(old_result, (self.root / "current/2026-09-10/scheduled-result.json").read_bytes())
        self.assertNotEqual(self.calls[0].unit_id, self.calls[2].unit_id)
        self.assertEqual(self.calls[0].request_id, self.calls[2].request_id)


    def test_invalid_source_isolated_from_next_issuer(self):
        import json
        from quant_data.operations import sec_selected_refresh as worker
        from quant_data.market.collection_mappings import IdentityEvidence
        from quant_data.market.collection_bindings import pin_binding
        f = self.fixture.f
        proof = IdentityEvidence(f.proof.body, "fixture/two-sec-issuers.json",
            "2026-09-09T03:00:00Z", "sec")
        rows = json.loads(json.dumps(f.rows))
        for row in rows:
            row.update(provider_subject=row["cik"], subject_field="cik",
                instrument_id=None, evidence_sha256=proof.sha256,
                evidence_reference=proof.source_reference)
        f.publisher.publish(f.batch(rows, evidence=(proof,), provider="sec",
            at="2026-09-09T04:00:00Z"))
        self.fixture.selection = pin_binding(f.stores, f.bindings["sec_filings_companyfacts"])
        published = []
        def publish(**kwargs):
            cik = kwargs["units"][0].subject
            published.append(cik)
            if cik == "0000320193":
                raise worker.SecSourceRejected("ConflictError")
            return ({"cik": cik, "outcome": "unchanged"},)
        with patch.object(worker, "publish_sec_pairs", side_effect=publish):
            result = self.run_worker()
        self.assertEqual(published, ["0000320193", "0001067983"])
        self.assertEqual((result["failed_cik_count"], result["succeeded_cik_count"],
            result["unattempted_cik_count"], result["outcome"]), (1, 1, 0, "partial"))
        self.assertEqual(len(self.calls), 4)

    def test_parse_expiry_keeps_raw_and_recovers_without_get(self):
        from quant_data.operations import collection_sec as sec
        original = sec.parse_sec_companyfacts_bundle
        def slow_parse(**kwargs):
            parsed = original(**kwargs)
            self.elapsed = 601
            return parsed
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(sec, "parse_sec_companyfacts_bundle", side_effect=slow_parse):
            result = self.run_worker(seconds=600)
        self.assertEqual((result["outcome"], result["stop_reason"]), ("partial", "publication_deadline"))
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        self.elapsed = 0
        self.assertEqual(self.run_worker(seconds=600)["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 2)

    def test_post_lock_expiry_rolls_back_and_recovers_without_get(self):
        from contextlib import contextmanager
        from quant_data import ingestion
        original = ingestion.StoreWriteLock
        @contextmanager
        def slow_lock(*args, **kwargs):
            with original(*args, **kwargs) as held:
                self.elapsed = 601
                yield held
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(ingestion, "StoreWriteLock", side_effect=slow_lock):
            result = self.run_worker(seconds=600)
        self.assertEqual(result["stop_reason"], "publication_deadline")
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        self.elapsed = 0
        self.assertEqual(self.run_worker(seconds=600)["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 2)

    def test_writer_expiry_rolls_back_and_recovers_without_get(self):
        from quant_data.company import sec_companyfacts as source
        original = source._ensure_issuer
        def slow_writer(*args, **kwargs):
            written = original(*args, **kwargs)
            self.elapsed = 601
            return written
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(source, "_ensure_issuer", side_effect=slow_writer):
            result = self.run_worker(seconds=600)
        self.assertEqual(result["stop_reason"], "publication_deadline")
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        self.elapsed = 0
        self.assertEqual(self.run_worker(seconds=600)["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 2)

    def test_physical_lock_conflict_escapes_without_terminal_date(self):
        from quant_data import ingestion
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(ingestion, "StoreWriteLock",
                side_effect=ConflictError("Timed out acquiring the physical store lock")):
            with self.assertRaises(ConflictError):
                self.run_worker()
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
        self.assertEqual(self.run_worker()["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 2)

    def test_canonical_conflict_escapes_without_terminal_date(self):
        from quant_data.company import sec_companyfacts as source
        with patch.object(source, "_ensure_issuer",
                side_effect=ConflictError("synthetic immutable canonical identity conflict")):
            with self.assertRaises(ConflictError):
                self.run_worker()
        self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
        self.assertEqual(len(self.calls), 2)

    def test_malformed_http_200_source_is_recorded_as_issuer_failure(self):
        original = self.fetch
        def malformed(**kwargs):
            response = original(**kwargs)
            if kwargs["unit"].endpoint == "submissions":
                return QueueResponse(200, b"[]", response.captured_at)
            return response
        before = mutation_fingerprint(self.fixture.f.stores)
        self.fetch = malformed
        result = self.run_worker()
        self.assertEqual((result["outcome"], result["failed_cik_count"]), ("partial", 1))
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        self.run_worker()
        self.assertEqual(len(self.calls), 2)

    def _select_two_issuers_for_conflict_checks(self):
        import json
        from quant_data.market.collection_mappings import IdentityEvidence
        from quant_data.market.collection_bindings import pin_binding
        f = self.fixture.f
        proof = IdentityEvidence(f.proof.body, "fixture/two-sec-conflict-issuers.json",
            "2026-09-09T03:00:00Z", "sec")
        rows = json.loads(json.dumps(f.rows))
        for row in rows:
            row.update(provider_subject=row["cik"], subject_field="cik",
                instrument_id=None, evidence_sha256=proof.sha256,
                evidence_reference=proof.source_reference)
        f.publisher.publish(f.batch(rows, evidence=(proof,), provider="sec",
            at="2026-09-09T04:00:00Z"))
        self.fixture.selection = pin_binding(f.stores, f.bindings["sec_filings_companyfacts"])

    def test_filing_metadata_conflict_is_audited_and_next_issuer_publishes(self):
        from quant_data.company import sec_companyfacts as source
        from quant_data.stores import quiet_immutable_read_connection
        self._select_two_issuers_for_conflict_checks()
        original_filing = source._insert_or_validate_filing
        def reject_first(connection, **kwargs):
            if kwargs["parsed"].cik == "0000320193":
                raise ConflictError("SEC filing accession conflicts with immutable prior metadata")
            return original_filing(connection, **kwargs)
        def fetch_two(*, unit, **kwargs):
            self.calls.append(unit)
            if unit.subject == "0000320193":
                body = payloads._submissions_body() if unit.endpoint == "submissions" else payloads._companyfacts_body()
            else:
                arguments = dict(cik=unit.subject, issuer_name="Second Fixture Issuer",
                    accession="0001067983-26-000001")
                body = (payloads._generic_submissions_body(**arguments)
                    if unit.endpoint == "submissions" else payloads._generic_companyfacts_body(**arguments))
            captured = self.now.isoformat()
            self.sleep(1)
            return QueueResponse(200, body, captured)
        self.fetch = fetch_two
        with patch.object(source, "_insert_or_validate_filing", side_effect=reject_first):
            result = self.run_worker()
        self.assertEqual((result["failed_cik_count"], result["succeeded_cik_count"],
            result["unattempted_cik_count"], result["outcome"]), (1, 1, 0, "partial"))
        self.assertEqual(result["failed_issuers"], [
            {"cik": "0000320193", "outcome": "blocked_canonical_conflict"}])
        self.assertEqual(len(self.calls), 4)
        with quiet_immutable_read_connection(self.fixture.f.stores, "company") as c:
            self.assertEqual([r[0] for r in c.execute("SELECT cik FROM company_issuers")],
                ["0001067983"])
            failures = c.execute("SELECT error_code,scope_json FROM ingestion_run_failures").fetchall()
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["error_code"], "conflict")
            self.assertIn("0000320193", failures[0]["scope_json"])
            self.assertGreater(c.execute("SELECT count(*) FROM company_sec_fact_versions").fetchone()[0], 0)
        before = mutation_fingerprint(self.fixture.f.stores)
        repeated = self.run_worker()
        self.assertEqual((repeated["outcome"], repeated["requests_this_run"]), ("already_recorded", 0))
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))

    def test_filing_metadata_conflict_subclass_still_stops(self):
        from quant_data.company import sec_companyfacts as source
        class OtherConflict(ConflictError):
            pass
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=OtherConflict("SEC filing accession conflicts with immutable prior metadata")):
            with self.assertRaises(OtherConflict):
                self.run_worker()
        self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
        self.assertEqual(len(self.calls), 2)

    def _configure_two_issuer_conflict_fetch(self):
        self._select_two_issuers_for_conflict_checks()
        original_fetch = self.fetch
        def fetch_two(*, unit, **kwargs):
            if unit.subject == "0000320193":
                return original_fetch(unit=unit, **kwargs)
            self.calls.append(unit)
            arguments = dict(cik=unit.subject, issuer_name="Second Fixture Issuer",
                accession="0001067983-26-000001")
            body = (payloads._generic_submissions_body(**arguments)
                if unit.endpoint == "submissions" else payloads._generic_companyfacts_body(**arguments))
            captured = self.now.isoformat()
            self.sleep(1)
            return QueueResponse(200, body, captured)
        self.fetch = fetch_two

    def test_metadata_hold_survives_deadline_and_next_date_without_failed_republication(self):
        from quant_data.company import sec_companyfacts as source
        from quant_data.stores import quiet_immutable_read_connection
        self._configure_two_issuer_conflict_fetch()
        attempted = []
        original_publish = source.SecCompanyFactsPublisher.publish
        original_filing = source._insert_or_validate_filing
        def publish(instance, parsed, **kwargs):
            attempted.append(parsed.cik)
            return original_publish(instance, parsed, **kwargs)
        def reject_first(connection, **kwargs):
            if kwargs["parsed"].cik == "0000320193":
                self.elapsed = 601
                raise ConflictError("SEC filing accession conflicts with immutable prior metadata")
            return original_filing(connection, **kwargs)
        with patch.object(source.SecCompanyFactsPublisher, "publish", new=publish):
            with patch.object(source, "_insert_or_validate_filing", side_effect=reject_first):
                paused = self.run_worker(seconds=600)
            self.assertEqual((paused["stop_reason"], paused["failed_cik_count"],
                paused["unattempted_cik_count"]), ("publication_deadline", 1, 1))
            self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
            self.elapsed = 0
            resumed = self.run_worker(seconds=600)
            self.assertEqual((resumed["failed_cik_count"], resumed["succeeded_cik_count"],
                resumed["unattempted_cik_count"]), (1, 1, 0))
            self.assertEqual(attempted, ["0000320193", "0001067983"])
            self.assertEqual(len(self.calls), 4)
            before = mutation_fingerprint(self.fixture.f.stores)
            self.now += timedelta(days=1)
            self.elapsed = 0
            following = self.run_worker(seconds=600)
            self.assertEqual((following["failed_cik_count"], following["succeeded_cik_count"]), (1, 1))
            self.assertEqual(attempted, ["0000320193", "0001067983", "0001067983"])
            self.assertEqual(len(self.calls), 8)
            self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))
        with quiet_immutable_read_connection(self.fixture.f.stores, "company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM ingestion_run_failures").fetchone()[0], 1)
        self.assertEqual(len(list((self.root / "sec-metadata-holds").glob("*.json"))), 1)

    def test_manual_metadata_hold_is_reused_by_new_scheduled_observation(self):
        from quant_data.company import sec_companyfacts as source
        self.root = self.fixture.root
        self.fixture.acquire()
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=ConflictError("SEC filing accession conflicts with immutable prior metadata")):
            manual = self.fixture.publish()
        self.assertEqual(manual[0]["outcome"], "blocked_canonical_conflict")
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(source.SecCompanyFactsPublisher, "publish",
                side_effect=AssertionError("Known failed publication must not be invoked")):
            scheduled = self.run_worker()
        self.assertEqual((scheduled["failed_cik_count"], scheduled["unattempted_cik_count"]), (1, 0))
        self.assertEqual(len(self.fixture.calls), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))

    def test_corrupt_metadata_hold_is_fatal_before_publication(self):
        import json
        from quant_data.company import sec_companyfacts as source
        self.root = self.fixture.root
        self.fixture.acquire()
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=ConflictError("SEC filing accession conflicts with immutable prior metadata")):
            self.fixture.publish()
        path = next((self.root / "sec-metadata-holds").glob("*.json"))
        held = json.loads(path.read_bytes())
        held["identity"]["cik"] = "0000000001"
        path.write_text(json.dumps(held))
        before = mutation_fingerprint(self.fixture.f.stores)
        with patch.object(source.SecCompanyFactsPublisher, "publish",
                side_effect=AssertionError("Corrupt hold must stop before publication")):
            with self.assertRaises(ConflictError):
                self.run_worker()
        self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
        self.assertEqual(before, mutation_fingerprint(self.fixture.f.stores))

    def test_unclassified_prior_failure_remains_fatal_across_observations(self):
        from quant_data.company import sec_companyfacts as source
        self.root = self.fixture.root
        self.fixture.acquire()
        with patch.object(source, "_ensure_issuer",
                side_effect=ConflictError("synthetic immutable canonical identity conflict")):
            with self.assertRaises(ConflictError):
                self.fixture.publish()
        self.assertFalse((self.root / "sec-metadata-holds").exists())
        with self.assertRaisesRegex(ConflictError, "Ingestion run identity was already used"):
            self.run_worker()
        self.assertFalse((self.root / "current/2026-09-10/scheduled-result.json").exists())
        self.assertEqual(len(self.calls), 2)
