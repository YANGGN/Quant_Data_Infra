"""Finite SEC filing-triggered policy tests against isolated initialized stores."""
from datetime import timedelta
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from quant_data.errors import ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.stores import quiet_immutable_read_connection
from quant_data.operations.sec_conditional_refresh import run_conditional_refresh
from quant_data.operations.collection_queue import QueueResponse
from tests.operations import test_sec_selected_refresh as scheduled_fixture
from tests.company import test_sec_companyfacts as payloads

class ConditionalSecTests(unittest.TestCase):
    def setUp(self):
        self.seed = scheduled_fixture.SelectedSecRefreshTests("runTest")
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        self.form = "10-Q"
        self.accession = "0000320193-26-000001"
        self.old_fetch = self.seed.fetch

    def fetch(self, **kwargs):
        response = self.old_fetch(**kwargs)
        if kwargs["unit"].endpoint == "submissions" and response.status == 200:
            value = json.loads(response.body)
            value["filings"]["recent"]["form"] = [self.form]
            value["filings"]["recent"]["accessionNumber"] = [self.accession]
            return QueueResponse(200, json.dumps(value).encode(), response.captured_at)
        return response

    def run_worker(self, **kwargs):
        s = self.seed
        return run_conditional_refresh(root=s.root, stores=s.fixture.f.stores,
            registry=s.fixture.f.registry, selection=s.fixture.selection, fetch=self.fetch,
            utcnow=lambda: s.now, monotonic=lambda: s.elapsed, sleeper=s.sleep, **kwargs)

    def query(self, sql):
        with quiet_immutable_read_connection(self.seed.fixture.f.stores, "company") as c:
            return [tuple(row) for row in c.execute(sql)]

    def next_day(self):
        self.seed.now += timedelta(days=1)

    def test_new_financial_filing_then_unchanged_day_skips_facts_and_same_date_is_noop(self):
        first = self.run_worker()
        self.assertEqual((first["outcome"], first["facts_requests"]), ("succeeded", 1))
        before = mutation_fingerprint(self.seed.fixture.f.stores)
        repeat = self.run_worker()
        self.assertEqual(repeat["requests_this_run"], 0)
        self.assertEqual(before, mutation_fingerprint(self.seed.fixture.f.stores))
        self.next_day()
        second = self.run_worker()
        self.assertEqual((second["outcome"], second["requests"], second["facts_requests"],
            second["skipped_existing_cik_count"]), ("succeeded", 1, 0, 1))
        self.assertEqual(before, mutation_fingerprint(self.seed.fixture.f.stores))

    def test_nonfinancial_filing_updates_metadata_without_facts_artifact(self):
        self.form = "4"
        result = self.run_worker()
        self.assertEqual((result["outcome"], result["requests"], result["facts_requests"]), ("succeeded", 1, 0))
        self.assertTrue(self.query("SELECT accession_number FROM company_sec_filings"))
        self.assertEqual(self.query("SELECT snapshot_id FROM company_sec_snapshots WHERE snapshot_kind='companyfacts'"), [])
        self.assertEqual(self.query("SELECT fact_version_id FROM company_sec_fact_versions"), [])

    def test_new_amended_financial_accession_triggers_again(self):
        self.run_worker()
        self.next_day()
        self.form = "10-Q/A"
        self.accession = "0000320193-26-000002"
        result = self.run_worker()
        self.assertEqual((result["facts_requests"], result["facts_triggered_cik_count"]), (1, 1))

    def test_one_request_continuation_keeps_decision_after_metadata_commit(self):
        result = self.run_worker(request_limit=1)
        self.assertEqual((result["outcome"], result["partial_cik_count"]), ("partial", 1))
        self.assertTrue(self.query("SELECT accession_number FROM company_sec_filings"))
        result = self.run_worker(request_limit=1)
        self.assertEqual((result["outcome"], result["facts_requests"]), ("succeeded", 1))
        self.assertEqual(len(self.seed.calls), 2)

    def test_failed_facts_not_silently_retried_next_day(self):
        self.seed.missing = True
        self.assertEqual(self.run_worker()["partial_cik_count"], 1)
        self.next_day()
        self.assertEqual(self.run_worker()["facts_requests"], 0)
        self.assertEqual(len(self.seed.calls), 3)

    def test_uncertain_facts_never_repeat_in_same_ledger(self):
        real = self.fetch
        def fetch(**kwargs):
            if kwargs["unit"].endpoint == "companyfacts":
                self.seed.uncertain = True
            return real(**kwargs)
        self.fetch = fetch
        with self.assertRaises(ConflictError):
            self.run_worker()
        with self.assertRaises(ConflictError):
            self.run_worker()
        self.assertEqual(len(self.seed.calls), 2)

    def test_targeted_check_finite_and_same_id_never_repeats_even_next_day(self):
        self.run_worker()
        self.next_day()
        kwargs = dict(check_ciks=("0000320193",), check_id="revenue-check",
            check_reason="Review a source discrepancy")
        result = self.run_worker(**kwargs)
        self.assertEqual((result["requests"], result["facts_requests"]), (2, 1))
        self.next_day()
        self.assertEqual(self.run_worker(**kwargs)["requests_this_run"], 0)
        with self.assertRaises(ConflictError):
            self.run_worker(**{**kwargs, "check_reason": "different scope"})

    def test_post_lock_deadline_rolls_back_and_resumes_retained_metadata(self):
        from contextlib import contextmanager
        from quant_data import ingestion
        original = ingestion.StoreWriteLock
        @contextmanager
        def lock(*args, **kwargs):
            with original(*args, **kwargs) as held:
                self.seed.elapsed = 601
                yield held
        before = mutation_fingerprint(self.seed.fixture.f.stores)
        with patch.object(ingestion, "StoreWriteLock", side_effect=lock):
            result = self.run_worker(second_limit=600)
        self.assertEqual(result["stop_reason"], "publication_deadline")
        self.assertEqual(before, mutation_fingerprint(self.seed.fixture.f.stores))
        self.seed.elapsed = 0
        self.assertEqual(self.run_worker(second_limit=600)["outcome"], "succeeded")
        self.assertEqual(len(self.seed.calls), 2)

    def test_forged_source_cik_rejected_without_canonical_write(self):
        original = self.fetch
        def wrong(**kwargs):
            response = original(**kwargs)
            return QueueResponse(200, response.body.replace(b"0000320193", b"0000789019"), response.captured_at)
        self.fetch = wrong
        before = mutation_fingerprint(self.seed.fixture.f.stores)
        self.assertEqual(self.run_worker()["failed_cik_count"], 1)
        self.assertEqual(len(self.seed.calls), 1)
        self.assertEqual(before, mutation_fingerprint(self.seed.fixture.f.stores))

    def test_preexisting_legacy_facts_are_preserved_and_not_refetched(self):
        self.seed.run_worker()
        before = self.query("SELECT * FROM company_sec_fact_versions")
        result = self.run_worker()
        self.assertEqual((result["facts_requests"], result["skipped_existing_cik_count"]), (0, 1))
        self.assertEqual(self.query("SELECT * FROM company_sec_fact_versions"), before)
        self.assertEqual(len(self.seed.calls), 3)

    def test_classified_metadata_conflict_is_held_without_republication(self):
        from quant_data.company import sec_companyfacts as source
        from quant_data.company.sec_submissions import SecSubmissionsPublisher
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=ConflictError("SEC filing accession conflicts with immutable prior metadata")):
            result = self.run_worker()
        self.assertEqual((result["failed_cik_count"], result["facts_requests"]), (1, 0))
        self.next_day()
        with patch.object(SecSubmissionsPublisher, "publish", side_effect=AssertionError("Held publication")):
            result = self.run_worker()
        self.assertEqual((result["failed_cik_count"], result["facts_requests"]), (1, 0))

    def test_targeted_cli_rejects_unbounded_scope_before_transport(self):
        from quant_data.operations import sec_targeted_check
        with patch.object(sec_targeted_check, "host_fetch", side_effect=AssertionError("No transport")):
            with self.assertRaises(SystemExit):
                sec_targeted_check.main(["--cik", "320193", "--check-id", "test", "--reason", "gap"])

    def test_metadata_hold_survives_raw_json_formatting_change(self):
        from quant_data.company import sec_companyfacts as source
        from quant_data.company.sec_submissions import SecSubmissionsPublisher
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=ConflictError("SEC filing accession conflicts with immutable prior metadata")):
            self.assertEqual(self.run_worker()["failed_cik_count"], 1)
        holds = list((self.seed.root / "submissions-metadata-holds").glob("*.json"))
        original_hold = holds[0].read_bytes()
        old_fetch = self.fetch
        def reformatted(**kwargs):
            response = old_fetch(**kwargs)
            return QueueResponse(response.status, json.dumps(json.loads(response.body), indent=3).encode(),
                response.captured_at)
        self.fetch = reformatted
        self.next_day()
        with patch.object(SecSubmissionsPublisher, "publish", side_effect=AssertionError("Held publication")):
            self.assertEqual(self.run_worker()["failed_cik_count"], 1)
        self.assertEqual(original_hold, holds[0].read_bytes())

    def test_metadata_hold_requires_exact_conflict_class(self):
        from quant_data.company import sec_companyfacts as source
        class OtherConflict(ConflictError):
            pass
        with patch.object(source, "_insert_or_validate_filing",
                side_effect=OtherConflict("SEC filing accession conflicts with immutable prior metadata")):
            with self.assertRaises(OtherConflict):
                self.run_worker()
        self.assertEqual(list((self.seed.root / "submissions-metadata-holds").glob("*.json")), [])

    def test_targeted_issuer_deadline_prevents_late_facts_but_allows_next_issuer(self):
        self.seed._select_two_issuers_for_conflict_checks()
        original = self.fetch
        def slow(**kwargs):
            unit = kwargs["unit"]
            response = original(**kwargs)
            if unit.subject == "0000320193" and unit.endpoint == "submissions":
                self.seed.sleep(651)
            if unit.subject != "0000320193":
                body = json.loads(response.body.replace(b"0000320193", unit.subject.encode()))
                body["cik"] = unit.subject
                response = QueueResponse(response.status, json.dumps(body).encode(), response.captured_at)
            return response
        self.fetch = slow
        kwargs = dict(check_ciks=("0000320193", "0001067983"), check_id="finite-deadline",
            check_reason="Explicit bounded gap check")
        result = self.run_worker(**kwargs)
        self.assertEqual((result["outcome"], result["partial_cik_count"],
            result["succeeded_cik_count"], result["facts_requests"]), ("partial", 1, 1, 1))
        self.assertFalse(any(u.subject == "0000320193" and u.endpoint == "companyfacts" for u in self.seed.calls))
        self.assertEqual(self.run_worker(**kwargs)["requests_this_run"], 0)

    def test_metadata_only_filing_is_visible_to_existing_public_reader(self):
        from quant_data.boundary import ToolDispatcher
        self.form = "4"
        self.assertEqual(self.run_worker()["facts_requests"], 0)
        f = self.seed.fixture.f
        response = ToolDispatcher(f.stores, f.registry).call("company.search_filings",
            {"query": "0000320193", "as_of": None, "cursor": None, "limit": 10}, tool_version="2.0.0")
        self.assertTrue(response["records"])

    def test_targeted_total_deadline_is_terminal_for_the_check_id(self):
        self.seed._select_two_issuers_for_conflict_checks()
        original = self.fetch
        def too_late(**kwargs):
            response = original(**kwargs)
            self.seed.sleep(1201)
            return response
        self.fetch = too_late
        kwargs = dict(check_ciks=("0000320193", "0001067983"), check_id="total-deadline",
            check_reason="Explicit finite check")
        result = self.run_worker(**kwargs)
        self.assertEqual(result["stop_reason"], "targeted_check_deadline")
        self.assertEqual(self.run_worker(**kwargs)["requests_this_run"], 0)
        self.assertEqual(len(self.seed.calls), 1)

    def test_targeted_budget_stop_cannot_revive_an_expired_issuer(self):
        from quant_data.operations import sec_conditional_refresh as worker
        self.seed._select_two_issuers_for_conflict_checks()
        original = worker.parse_submissions
        def slow_parse(**kwargs):
            value = original(**kwargs)
            if kwargs["cik"] == "0000320193":
                self.seed.sleep(650)
            return value
        kwargs = dict(check_ciks=("0000320193", "0001067983"), check_id="budget-after-deadline",
            check_reason="Finite check", request_limit=1)
        with patch.object(worker, "parse_submissions", side_effect=slow_parse):
            result = self.run_worker(**kwargs)
        self.assertEqual((result["stop_reason"], result["partial_cik_count"]), ("invocation_budget", 1))
        self.assertEqual(self.run_worker(**kwargs)["requests_this_run"], 0)
        self.assertEqual(len(self.seed.calls), 1)
