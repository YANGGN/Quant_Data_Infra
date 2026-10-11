"""Isolated public classification contract, cutoff and read-only checks."""
import copy
from dataclasses import replace
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.errors import (ValidationError, ResourceLimitError, StoreUnavailableError,
    CancellationError, DeadlineExceededError, RegistryError)
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.schema import validate_schema
from quant_data.stores import writer_connection
from quant_data.tool_platform.classification_access import invoke
from quant_data.tool_platform.classification_contracts import TOOL, parse
from quant_data.tool_platform.classification_registry import predecessor_profile, PREDECESSOR_SHA256
from quant_data.tool_platform.context import FixedClock, Deadline, ExecutionBudget, CancellationToken
from quant_data.tool_platform.generate import generate
from quant_data.tool_platform.local_agent_cli import run
from tests.market import test_fmp_classifications as fixtures

ROOT = Path(__file__).resolve().parents[2]
CLOCK = FixedClock(Decimal("100"), fixtures.FINAL)


def records(value, kind):
    return [{f["name"]: f["value"] for f in row["fields"]}
            for row in value["records"] if row["record_type"] == kind]


class ClassificationToolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ClassificationTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.publish()
        self.stores, self.registry = self.fixture.stores, self.fixture.registry
        self.app = Stage1Application(self.stores, self.registry)
        clock = patch("quant_data.boundary.dispatcher.HostClock", return_value=CLOCK)
        clock.start(); self.addCleanup(clock.stop)

    def call(self, args):
        before = mutation_fingerprint(self.stores)
        connect = sqlite3.connect
        def readonly(path, *a, **kw):
            self.assertTrue(str(path).startswith("file:/proc/self/fd/"), path)
            self.assertIn("mode=ro&immutable=1", str(path))
            return connect(path, *a, **kw)
        with patch("sqlite3.connect", side_effect=readonly), patch("socket.create_connection", side_effect=AssertionError("network")), \
             patch("quant_data.credentials.read_project_credential", side_effect=AssertionError("credentials")):
            value = self.app.dispatcher.call(TOOL, args, tool_version="1.0.0")
        validate_schema(value, self.registry.tool(TOOL)["output_schema"])
        value = json.loads(dumps_strict(value))
        self.assertEqual(before, mutation_fingerprint(self.stores))
        self.assertNotIn(str(self.stores.path("market")), json.dumps(value))
        self.assertFalse(value["truncation"]["applied"])
        self.assertEqual(value["research_contract"]["contract"]["point_in_time_status"], "not_established")
        self.assertEqual(records(value, "classification_source")[0]["provider_requests"], 0)
        return value

    def test_full_universe_batch_aliases_and_complete_lineage(self):
        full = self.call({})
        rows = records(full, "instrument_classification")
        self.assertEqual([r["ticker"] for r in rows], ["AAPL", "BRK.B"])
        self.assertEqual(rows[1]["provider_symbol"], "BRK-B")
        self.assertEqual(rows[1]["classification_provider"], "fmp")
        self.assertEqual(rows[0]["sector"], "Technology")
        for key in ("instrument_id", "version_id", "source_evidence_id", "source_content_sha256", "source_mapping_id", "source_description_version_id"):
            self.assertTrue(rows[0][key], key)
        batch = self.call({"tickers": ["BRK.B", "AAPL"]})
        self.assertEqual(records(batch, "instrument_classification"), rows)
        self.assertEqual(len(full["lineage"]), 1)

    def test_filters_are_exact_or_within_and_across_and_unknowns_stay_explicit(self):
        value = self.call({"sectors": ["Financial Services", "Technology"], "industries": ["Consumer Electronics"]})
        self.assertEqual([r["ticker"] for r in records(value, "instrument_classification")], ["AAPL"])
        self.assertEqual(records(value, "classification_source")[0]["filtered_out_count"], 1)
        for label in ("technology", "Tech%", "Technology' OR 1=1 --"):
            empty = self.call({"sectors": [label]})
            self.assertEqual(empty["status"], "not_established")
            self.assertFalse(records(empty, "instrument_classification"))
        mixed = self.call({"tickers": ["AAPL", "ZZZZ", "BRK-B"], "industries": ["Unmatched"]})
        self.assertEqual([r["ticker"] for r in records(mixed, "classification_missing")], ["BRK-B", "ZZZZ"])
        self.assertTrue(all(r["sector"] is None for r in records(mixed, "classification_missing")))

    def test_cutoff_selects_revision_before_filtering_and_preserves_source_time(self):
        self.fixture.revise(0, {"sector": "Industrials"})
        self.fixture.publish(fixtures.LATER)
        latest = self.call({"tickers": ["AAPL"]})
        row = records(latest, "instrument_classification")[0]
        self.assertEqual((row["sector"], row["version_sequence"]), ("Industrials", 2))
        self.assertFalse(records(self.call({"sectors": ["Technology"]}), "instrument_classification"))
        old = self.call({"sectors": ["Technology"], "as_of": "2026-09-22T08:00:00-04:00"})
        row = records(old, "instrument_classification")[0]
        self.assertEqual((row["sector"], row["version_sequence"]), ("Technology", 1))
        self.assertLessEqual(row["source_captured_at"], row["available_at"])
        self.assertEqual(row["available_at"], fixtures.AT)

    def test_before_publication_and_unknown_ticker_are_explicit(self):
        before = "2026-09-22T11:59:59.999999Z"
        full = self.call({"as_of": before})
        self.assertEqual(full["status"], "not_established")
        self.assertFalse(records(full, "instrument_classification"))
        batch = self.call({"tickers": ["AAPL", "ZZZZ"], "as_of": before})
        self.assertEqual(len(records(batch, "classification_missing")), 2)
        self.assertEqual(records(batch, "classification_missing")[0]["missing_reason"], "no_classification_at_cutoff")

    def test_strict_inputs_and_future_cutoff_fail_before_read(self):
        invalid = [{"tickers": []}, {"tickers": ["AAPL"]*2249}, {"tickers": ["AAPL", "AAPL"]},
            {"tickers": ["aapl"]}, {"tickers": ["AAPL;SELECT 1"]}, {"sectors": []}, {"industries": [" x"]},
            {"sectors": ["Technology", "Technology"]}, {"sectors": [str(i) for i in range(51)]},
            {"industries": [str(i) for i in range(201)]}, {"industry": "Semiconductors"},
            {"provider": "other"}, {"database_path": "/tmp/other"}, {"sql": "SELECT 1"},
            {"as_of": "2026-09-23T00:00:00Z"}, {"as_of": "2026-09-22T12:00:00"}, {"as_of": None}]
        with patch("quant_data.tool_platform.classification_access.connection", side_effect=AssertionError("no read")):
            for args in invalid:
                with self.subTest(args=args), self.assertRaises((ValidationError, ResourceLimitError)):
                    self.app.dispatcher.call(TOOL, args, tool_version="1.0.0")

    def test_company_store_is_not_needed_for_canonical_read(self):
        self.stores.path("company").unlink()
        value = self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")
        self.assertEqual(len(records(value, "instrument_classification")), 2)
        self.assertFalse(self.stores.path("company").exists())

    def test_missing_store_journals_and_ledger_tamper_fail_closed(self):
        path = self.stores.path("market")
        for suffix in ("-wal", "-journal"):
            journal = Path(str(path)+suffix); journal.write_bytes(b"unsafe")
            try:
                with self.assertRaises(StoreUnavailableError): self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")
            finally: journal.unlink()
        with writer_connection(self.stores, "market") as c:
            c.execute("DROP TRIGGER schema_migrations_immutable_update")
            c.execute("UPDATE schema_migrations SET sha256=? WHERE ordinal=13", ("0"*64,))
        with self.assertRaises(StoreUnavailableError): self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")
        path.unlink()
        with self.assertRaises(StoreUnavailableError): self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")
        self.assertFalse(path.exists())

    def test_dataset_identity_and_non_fmp_rows_are_rejected(self):
        with writer_connection(self.stores, "market") as c:
            c.execute("DROP TRIGGER dataset_identity_contracts_immutable_update")
            c.execute("UPDATE dataset_identity_contracts SET identity_sha256=? WHERE dataset_id='market.instrument_classifications'", ("0"*64,))
        with self.assertRaises(StoreUnavailableError): self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")
        from quant_data.tool_platform.classification_access import _validate
        with self.assertRaises(StoreUnavailableError): _validate({"classification_provider": "other"}, fixtures.FINAL)

    def test_budget_cancellation_deadline_and_full_population_bound(self):
        context = self.app.dispatcher._stage5_context(TOOL, self.registry.tool(TOOL))
        for change, error in ((dict(cancellation=CancellationToken(True)), CancellationError),
            (dict(deadline=Deadline(Decimal("99"))), DeadlineExceededError),
            (dict(budget=ExecutionBudget(max_operations=1)), ResourceLimitError)):
            with self.subTest(error=error), self.assertRaises(error): invoke(parse({}), replace(context, **change), self.registry)
        with patch("quant_data.tool_platform.classification_access.MAX_TICKERS", 1):
            with self.assertRaises(ResourceLimitError): self.app.dispatcher.call(TOOL, {}, tool_version="1.0.0")

    def test_cli_manifest_list_describe_call_receipt_and_http_parity(self):
        request = dict(api_version="1.0", tool=TOOL, tool_version="1.0.0", arguments={"tickers": ["AAPL"]})
        for command in (["manifest"], ["list"], ["describe", TOOL, "--tool-version", "1.0.0"], ["call"]):
            out, err = io.StringIO(), io.StringIO()
            code = run(command, stdin=io.BytesIO(json.dumps(request).encode()), stdout=out, stderr=err, application=self.app)
            self.assertEqual(code, 0, out.getvalue()); self.assertEqual(err.getvalue(), "")
            value = json.loads(out.getvalue())
            self.assertIn(TOOL, out.getvalue()); self.assertTrue(value["receipt"])
            if command == ["call"]:
                response = self.app.handle("POST", "/api/agent-tools/call", headers={"Content-Type": "application/json"}, body=json.dumps(request).encode())
                self.assertEqual(response.status, 200)
                self.assertEqual(value["result"], json.loads(response.body)["result"])
        tool = next(t for t in self.app.dispatcher.manifest()["tools"] if t["name"] == TOOL)
        self.assertEqual(tool["version"], "1.0.0")
        self.assertEqual(tool["stores"], ["market"])
        self.assertFalse(tool["live_capability"]["possible"])

    def test_all_examples_validate_and_execute(self):
        declaration = self.registry.tool(TOOL)
        for example in declaration["examples"]:
            validate_schema(example, declaration["input_schema"])
            self.call(example)

    def test_exact_predecessor_frozen_contracts_and_generated_parity(self):
        from quant_data.tool_platform.research_extensions_registry import predecessor_profile as before_extensions
        classification_release = before_extensions(self.registry)
        old = predecessor_profile(classification_release)
        self.assertEqual(old.registry_version, "2.96.0")
        self.assertEqual(old.source_sha256, PREDECESSOR_SHA256)
        self.assertEqual(old.tool_version_policies, classification_release.tool_version_policies)
        self.assertEqual(old.migrations, self.registry.migrations)
        self.assertEqual(old.raw["tool_schema_catalog"], self.registry.raw["tool_schema_catalog"])
        for entry in old.tools: self.assertEqual(entry, self.registry.tool(entry["id"]))
        for before, after in zip(old.datasets, classification_release.datasets):
            self.assertEqual(before, replace(after, tool_ids=tuple(t for t in after.tool_ids if t != TOOL)))
        catalog = json.loads((ROOT/old.raw["tool_version_schema_catalog"]["resource"]).read_text())
        catalog["schema_version"] = "2.39.0"
        from quant_data.tool_platform.research_extensions_contracts import PEERS
        new_ids = {f"urn:quant-data:tool:{n}:{d}:2.0.0" for n in ("company.get_guidance_history", "options.get_intraday_snapshot", "options.get_intraday_history") for d in ("input","output")}
        catalog["contracts"] = [c for c in catalog["contracts"] if c["tool"] not in (TOOL, PEERS) and c["id"] not in new_ids]
        rendered = (json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode()
        self.assertEqual(hashlib.sha256(rendered).hexdigest(), old.raw["tool_version_schema_catalog"]["sha256"])
        generate(ROOT, check=True)
        tampered = copy.deepcopy(dict(self.registry.raw)); tampered["tools"][-1]["description"] = "tampered"
        with self.assertRaises(RegistryError): predecessor_profile(replace(self.registry, raw=tampered))


if __name__ == "__main__": unittest.main()
