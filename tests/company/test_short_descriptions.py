"""Offline company-description identity, migration, replay, cutoff and request tests."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from quant_data.company.short_descriptions import (
    DescriptionPublisher, prepare_record, selected_inputs, short_description, read_descriptions)
from quant_data.company.short_description_registry import predecessor_profile, MIGRATION_ID
from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_mappings import IdentityEvidence
from quant_data.migrations import initialize_all, migrate_store
from quant_data.operations.company_short_descriptions import prepare, acquire, prepared_records
from quant_data.operations.collection_queue import QueueResponse
from quant_data.stores import acquire_write_session, quiet_immutable_read_connection, writer_connection, StoreMap
from tests.market import test_collection_mappings as mapping_fixture

AT = "2026-09-22T12:00:00.000000Z"
LATER = "2026-09-22T13:00:00.000000Z"
DESCRIPTION = "Apple Inc. designs and sells consumer electronic devices and software worldwide. Its products include phones and computers."

class DescriptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = mapping_fixture.CollectionMappingTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        f = self.fixture
        raw = json.loads(f.proof.body)
        raw[0].update(description=DESCRIPTION, companyName="Apple Inc.", industry="Consumer Electronics")
        f.proof = IdentityEvidence(json.dumps(raw).encode(), f.proof.source_reference, f.proof.captured_at)
        for row in f.rows:
            row["evidence_sha256"] = f.proof.sha256
        f.publisher.publish(f.batch())
        self.stores, self.registry = f.stores, f.registry
        self.publisher = DescriptionPublisher(self.stores, self.registry)
        self.work = Path(f.temp.name) / "descriptions"

    def members(self):
        with acquire_write_session(self.stores, ("market",)):
            return selected_inputs(self.stores, cutoff=AT)

    def fetch(self, symbol, **kwargs):
        self.assertEqual(symbol, "BRK-B")
        return QueueResponse(200, json.dumps([{"symbol":"BRK-B", "cik":"0001067983",
            "description":"Berkshire Hathaway Inc. owns insurance, railroad, energy and manufacturing businesses across the world.",
            "companyName":"Berkshire Hathaway Inc.", "industry":"Insurance - Diversified"}]).encode(), AT)

    def records(self):
        prepare(self.stores, self.work, cutoff=AT)
        acquire(self.work, self.fetch, clock=lambda:AT)
        records, gaps = prepared_records(self.work)
        self.assertEqual(gaps, [])
        return records

    def test_short_excerpt_preserves_business_sentence_and_abbreviations(self):
        value, method, truncated = short_description({"description": DESCRIPTION})
        self.assertEqual(value, DESCRIPTION)  # Eleven-word opening includes its next sentence.
        self.assertEqual(method, "opening_sentence_excerpt.v1")
        self.assertFalse(truncated)
        value, _, truncated = short_description({"description":"Acme Inc. " + "manufactures industrial products " * 40})
        self.assertLessEqual(len(value), 400)
        self.assertTrue(truncated)
        self.assertTrue(value.endswith("..."))

    def test_terse_opening_includes_the_next_business_sentence(self):
        first = "Flowco Holdings Inc. functions as a parent company."
        second = "Its subsidiaries supply artificial lift equipment and methane-reduction services to oil and gas producers."
        text, method, truncated = short_description({"description":first+" "+second+" The company was founded in 2024."})
        self.assertEqual(text,first+" "+second)
        self.assertEqual(method,"opening_sentence_excerpt.v1")
        self.assertFalse(truncated)

    def test_industry_fallback_is_explicit_and_no_invention(self):
        value, method, _ = short_description({"companyName":"Acme", "industry":"Software", "description":None})
        self.assertEqual(value, "Acme operates in the Software industry.")
        self.assertEqual(method, "industry_template.v1")
        with self.assertRaises(ValidationError):
            short_description({"companyName":"Acme"})
        with self.assertRaises(ValidationError):
            short_description({"description":123})

    def test_only_missing_profile_fetched_and_alias_is_preserved(self):
        records = self.records()
        self.assertEqual([r["source_symbol"] for r in records], ["AAPL", "BRK.B"])
        self.assertEqual([r["provider_symbol"] for r in records], ["AAPL", "BRK-B"])
        with patch("quant_data.credentials.read_project_credential", side_effect=AssertionError("No credentials")):
            result = acquire(self.work, lambda *a, **kw:self.fail("No repeated GET"), clock=lambda:AT)
        self.assertEqual(result["attempted_this_invocation"], 0)

    def test_publication_replay_is_zero_and_asof_excludes_new_derivation(self):
        records = self.records()
        receipt = self.publisher.publish(records, published_at=AT)
        self.assertEqual(receipt.outcome, "succeeded")
        before = mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish(records, published_at=LATER).written_count, 0)
        self.assertEqual(mutation_fingerprint(self.stores), before)
        self.assertIsNone(read_descriptions(self.stores, ["AAPL"], as_of="2026-09-22T11:59:59Z")[0]["short_description"])
        self.assertEqual(read_descriptions(self.stores, ["AAPL"], as_of=AT)[0]["short_description"], records[0]["short_description"])
        with quiet_immutable_read_connection(self.stores, "company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_short_descriptions").fetchone()[0], 2)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        with writer_connection(self.stores, "company") as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM company_profile_evidence")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE company_short_description_versions SET short_description='changed'")

    def test_revisions_and_return_to_original_are_versioned(self):
        original = self.records()[1]
        self.publisher.publish([original], published_at=AT)
        source = deepcopy(original["source"])
        value = json.loads(source["raw_body"])
        value[0]["description"] = "Berkshire Hathaway Inc. operates insurance and energy businesses with additional transportation operations."
        source["raw_body"] = json.dumps(value)
        source["captured_at"] = LATER
        changed = prepare_record(original["member"], source)
        self.publisher.publish([changed], published_at=LATER)
        final_at = "2026-09-22T14:00:00Z"
        self.publisher.publish([original], published_at=final_at)
        with quiet_immutable_read_connection(self.stores, "company") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company_short_description_versions").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT version_sequence FROM company_short_descriptions").fetchone()[0], 3)
        before = mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish([original], published_at=final_at).written_count, 0)
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_wrong_symbol_cik_changed_output_and_future_evidence_rejected(self):
        record = self.records()[1]
        for key, value in (("symbol","WRONG"), ("cik","0000000001")):
            source = deepcopy(record["source"])
            profile = json.loads(source["raw_body"])
            profile[0][key] = value
            source["raw_body"] = json.dumps(profile)
            with self.assertRaises(ConflictError):
                prepare_record(record["member"], source)
        before = mutation_fingerprint(self.stores)
        altered = deepcopy(record)
        altered["short_description"] = "Invented text."
        with self.assertRaises(ConflictError):
            self.publisher.publish([altered], published_at=AT)
        with self.assertRaises(ConflictError):
            self.publisher.publish([record], published_at="2026-09-21T00:00:00Z")
        self.assertEqual(mutation_fingerprint(self.stores), before)

    def test_uncertain_attempt_cannot_retry(self):
        prepare(self.stores, self.work, cutoff=AT)
        def failure(*args, **kwargs):
            raise TimeoutError("Synthetic timeout")
        with self.assertRaises(TimeoutError):
            acquire(self.work, failure, clock=lambda:AT)
        with self.assertRaises(ConflictError):
            acquire(self.work, lambda *a, **kw:self.fail("No retry"), clock=lambda:AT)

    def test_acquisition_expiry_cannot_extend_on_resume(self):
        prepare(self.stores, self.work, cutoff=AT)
        from quant_data.operations.collection_queue import atomic
        from quant_data.operations.company_short_descriptions import load_plan
        plan = load_plan(self.work)
        atomic(self.work / "acquisition-start.json", {"plan_id":plan["plan_id"], "started_at":AT})
        from quant_data.errors import ResourceLimitError
        with self.assertRaises(ResourceLimitError):
            acquire(self.work, lambda *a, **kw:self.fail("No expired request"),
                    clock=lambda:"2026-09-22T12:30:01.000000Z")

    def test_provider_quota_stop_is_preserved_on_resume(self):
        prepare(self.stores, self.work, cutoff=AT)
        result = acquire(self.work, lambda *a, **kw:QueueResponse(429,b'{}',AT), clock=lambda:AT)
        self.assertEqual(result["http_status_counts"], {"429":1})
        result = acquire(self.work, lambda *a, **kw:self.fail("No retry"), clock=lambda:AT)
        self.assertEqual(result["attempted_this_invocation"], 0)
        records, gaps = prepared_records(self.work)
        self.assertEqual(len(records), 1)
        self.assertEqual(gaps, [{"symbol":"BRK.B","reason":"http_429"}])

    def test_migration_preserves_prior_ledger_and_repeats_without_change(self):
        previous = predecessor_profile(self.registry)
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            stores = StoreMap.four_explicit(**{r:Path(folder)/(r+".sqlite") for r in ("market","macro","company","news")})
            initialize_all(stores, previous)
            with quiet_immutable_read_connection(stores,"company") as c:
                old = [tuple(r) for r in c.execute("SELECT * FROM schema_migrations ORDER BY ordinal")]
                old_schema = [tuple(r) for r in c.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")]
            migrate_store(stores, self.registry, "company", applied_at=AT)
            with quiet_immutable_read_connection(stores,"company") as c:
                self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM schema_migrations ORDER BY ordinal")][:-1],old)
                for name,sql in old_schema:
                    self.assertEqual(c.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0],sql)
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            before = mutation_fingerprint(stores)
            migrate_store(stores, self.registry, "company", applied_at=LATER)
            self.assertEqual(before,mutation_fingerprint(stores))
            bad = replace(self.registry, migrations=tuple(replace(m,sha256="0"*64) if m.id==MIGRATION_ID else m for m in self.registry.migrations))
            with self.assertRaises(Exception):
                migrate_store(stores,bad,"company",applied_at=LATER)
            self.assertEqual(before,mutation_fingerprint(stores))

if __name__ == "__main__":
    unittest.main()
