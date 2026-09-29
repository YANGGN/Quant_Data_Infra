"""Isolated checks for baseline gating and checkpointed daily SF1 maintenance."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
from quant_data.company import sharadar_direct as direct
import unittest
from quant_data.errors import ConflictError
from quant_data.operations.collection_plan import ProviderBudget
from quant_data.operations.collection_queue import QueueResponse, _read
from quant_data.operations.sharadar_selected_refresh import run_refresh, initial_baseline, BUDGET, main
from quant_data.fingerprint import mutation_fingerprint
from tests.company import test_sharadar_repository as repository
from tests.company import test_sharadar_sf1 as sf

def direct_descriptions():
    fields = [field["name"] for field in direct.contract()["definition_columns"]]
    filters, keys = {"ticker", "dimension", "lastupdated"}, {"ticker", "dimension", "date", "reportperiod"}
    rows = []
    for column in direct.contract()["columns"]:
        row = {field: "" for field in fields}
        row.update({"table": "fundamentals", "indicator": column["direct_name"], "isfilter": "Y" if column["direct_name"] in filters else "N", "isprimarykey": "Y" if column["direct_name"] in keys else "N"})
        rows.append(row)
    return json.dumps({"count": len(rows), "data": rows}, separators=(",", ":")).encode()


def direct_row(dimension, lastupdated):
    row = {}
    for column in direct.contract()["columns"]:
        name, kind = column["direct_name"], column["canonical_type"]
        if name == "ticker": value = "AAPL"
        elif name == "dimension": value = dimension
        elif name in ("calendardate", "date", "reportperiod"): value = {"calendardate": "2026-06-30", "date": "2026-07-31", "reportperiod": "2026-06-27"}[name]
        elif name == "lastupdated": value = lastupdated
        elif kind == "Integer": value = 1
        elif kind == "double": value = 1.0
        else: value = "Q1"
        row[name] = value
    return row



class SelectedSharadarRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = repository.SharadarRepositoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = Path(self.fixture.tmp.name) / "refresh"
        self.now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        self.elapsed = 0
        self.calls = []
        self.fail_sf1 = False
        self.empty = False

    def sleep(self, seconds):
        self.elapsed += seconds
        self.now += timedelta(seconds=seconds)

    def baseline(self, dimensions=sf.DIMENSIONS):
        schema=direct._legacy_schema(sf.AT,"fixture/legacy-sf1-metadata.json")
        columns=[{"name":c["canonical_name"],"type":c["canonical_type"]} for c in direct.contract()["columns"]]
        for dimension in sorted(dimensions):
            row=direct_row(dimension,"2026-09-08")
            body=json.dumps({"datatable":{"columns":columns,
                "data":[[row[c["direct_name"]] for c in direct.contract()["columns"]]]},
                "meta":{"next_cursor_id":None}}).encode()
            page=sf.parse_page(body,schema=schema,parameters={"ticker":"AAPL","dimension":dimension,"qopts.per_page":"10000"},
                captured_at=sf.AT,source_reference="fixture/legacy-sf1-"+dimension+".json")
            self.fixture.publish(sf.partition(page))

    def fetch(self, *, unit, **kwargs):
        self.calls.append(unit)
        params = dict(unit.parameters)
        if unit.endpoint == "SHARADAR_DIRECT/descriptions":
            body = direct_descriptions()
        else:
            if self.fail_sf1:
                return QueueResponse(429, b'{"error":"quota"}', self.now.isoformat())
            rows = [] if self.empty else [direct_row(params["dimension"], params["lastupdated.lte"])]
            body = json.dumps({"count": len(rows), "data": rows}, separators=(",", ":")).encode()
        response = QueueResponse(200, body, self.now.isoformat())
        self.sleep(1)
        return response

    def run_worker(self, cap=200):
        return run_refresh(root=self.root, stores=self.fixture.stores, registry=self.fixture.registry,
            selection=self.fixture.selection, fetch=self.fetch,
            budget=ProviderBudget("sharadar", cap, BUDGET.max_total_bytes, 3600, 1000, 1000),
            utcnow=lambda: self.now, monotonic=lambda: self.elapsed, sleeper=self.sleep)

    def test_first_day_selection_real_publishers_and_next_day_empty_update(self):
        from quant_data.stores import quiet_immutable_read_connection
        self.baseline()
        self.now=datetime(2026,9,9,10,tzinfo=timezone.utc)
        self.assertEqual(self.run_worker()["outcome"],"succeeded")
        def heads():
            with quiet_immutable_read_connection(self.fixture.stores,"company") as connection:
                return [tuple(r) for r in connection.execute("SELECT * FROM company_sharadar_sf1_heads ORDER BY observation_id")]
        before_heads=heads();before_versions=self.fixture.counts()["company_sharadar_sf1_versions"]
        self.assertEqual(len(before_heads),6);self.assertEqual(before_versions,12)
        self.empty=True;self.now+=timedelta(days=1)
        self.assertEqual(self.run_worker()["outcome"],"succeeded")
        self.assertEqual(heads(),before_heads)
        self.assertEqual(self.fixture.counts()["company_sharadar_sf1_versions"],before_versions)
        self.assertEqual(_read(self.root/"refresh-state.json")["last_complete_date"],"2026-09-10")
        self.assertEqual(len(self.calls),14)

    def test_real_publishers_resume_previous_day_with_fixed_query_date(self):
        self.baseline()
        self.assertEqual(self.run_worker(cap=4)["outcome"],"invocation_budget")
        self.now+=timedelta(days=1)
        self.assertEqual(self.run_worker(cap=4)["outcome"],"succeeded")
        self.assertEqual(len(self.calls),7)
        data=[u for u in self.calls if u.endpoint=="SHARADAR_DIRECT/fundamentals"]
        self.assertEqual({dict(u.parameters)["to"] for u in data},{"2026-09-10"})
        self.assertEqual(_read(self.root/"refresh-state.json")["last_complete_date"],"2026-09-10")

    def direct_baseline(self, **overrides):
        schema = direct.parse_metadata(direct_descriptions(), captured_at=sf.AT,
            source_reference="fixture/direct-descriptions.json")
        for dimension in sorted(sf.DIMENSIONS):
            params = {"ticker": "AAPL", "dimension": dimension, "format": "json",
                "sort": "date.asc", "from": "1900-01-01", "to": sf.AT[:10],
                "limit": "10000", "offset": "0", **overrides}
            page = direct.parse_page(b'{"count":0,"data":[]}', schema=schema,
                parameters=params, captured_at=sf.AT, source_reference="fixture/direct-empty.json")
            self.fixture.publish(direct.prepare_partition((page,), max_pages=100,
                max_rows=100000, max_bytes=256*1024*1024))

    def test_complete_direct_history_including_empty_scopes_establishes_baseline(self):
        self.direct_baseline()
        self.assertEqual(initial_baseline(self.fixture.stores, self.fixture.selection,
            cutoff=self.now.isoformat()), sf.AT[:10])
        self.assertEqual(self.calls, [])

    def test_direct_recent_history_is_not_full_baseline(self):
        self.direct_baseline(**{"from": "2025-01-01"})
        with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
            self.run_worker()
        self.assertEqual(self.calls, [])

    def test_direct_stale_upper_bound_is_not_full_baseline(self):
        self.direct_baseline(to="2026-09-08")
        with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
            self.run_worker()
        self.assertEqual(self.calls, [])

    def test_direct_incremental_scopes_are_not_full_baseline(self):
        self.direct_baseline(**{"lastupdated.gte": "2026-09-01", "lastupdated.lte": sf.AT[:10]})
        with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
            self.run_worker()
        self.assertEqual(self.calls, [])

    def test_missing_or_partial_baseline_blocks_before_get(self):
        with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
            self.run_worker()
        self.baseline(("ARQ",))
        with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
            self.run_worker()
        self.assertEqual(self.calls, [])

    def test_all_dimensions_overlap_and_same_day_zero_get_zero_write(self):
        self.baseline()
        result = self.run_worker()
        self.assertEqual((result["outcome"], result["requests"]), ("succeeded", 7))
        data = [u for u in self.calls if u.endpoint == "SHARADAR_DIRECT/fundamentals"]
        self.assertEqual({dict(u.parameters)["dimension"] for u in data}, sf.DIMENSIONS)
        self.assertTrue(all(dict(u.parameters)["lastupdated.gte"] == "2026-09-02" for u in data))
        self.assertTrue(all(dict(u.parameters)["lastupdated.lte"] == "2026-09-10" for u in data))
        before = mutation_fingerprint(self.fixture.stores)
        self.assertEqual(self.run_worker()["outcome"], "already_complete")
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(before, mutation_fingerprint(self.fixture.stores))

    def test_partial_budget_never_advances_watermark_and_continues_without_repeat(self):
        self.baseline()
        self.assertEqual(self.run_worker(cap=4)["outcome"], "invocation_budget")
        state = _read(self.root / "refresh-state.json")
        self.assertEqual(state["last_complete_date"], "2026-09-09")
        self.assertEqual(len(state["pending"]["completed_units"]), 3)
        self.assertEqual(self.run_worker(cap=4)["outcome"], "succeeded")
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(len({unit.unit_id for unit in self.calls}), 7)
        state = _read(self.root / "refresh-state.json")
        self.assertIsNone(state["pending"])
        self.assertEqual(state["last_complete_date"], "2026-09-10")

    def test_known_http_failure_remains_stopped_without_automatic_retry(self):
        self.baseline()
        self.fail_sf1 = True
        self.assertEqual(self.run_worker()["outcome"], "provider_http_failure")
        calls = len(self.calls)
        self.assertEqual(self.run_worker()["outcome"], "page_http_failure")
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(_read(self.root / "refresh-state.json")["last_complete_date"], "2026-09-09")

    def test_long_outage_starts_from_checkpoint_and_empty_update_preserves_facts(self):
        self.baseline()
        self.now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
        self.empty = True
        before = self.fixture.counts()["company_sharadar_sf1_versions"]
        self.assertEqual(self.run_worker()["outcome"], "succeeded")
        data = [u for u in self.calls if u.endpoint == "SHARADAR_DIRECT/fundamentals"]
        self.assertTrue(all(dict(u.parameters)["lastupdated.gte"] == "2026-09-02" for u in data))
        self.assertEqual(before, self.fixture.counts()["company_sharadar_sf1_versions"])

    def test_forged_checkpoint_window_rejected_before_new_get(self):
        from quant_data.operations.collection_queue import atomic
        self.baseline()
        self.run_worker(cap=4)
        state = _read(self.root / "refresh-state.json")
        state["pending"]["from_date"] = "2026-09-10"
        atomic(self.root / "refresh-state.json", state, replace=True)
        calls = len(self.calls)
        with self.assertRaisesRegex(ConflictError, "pending refresh window"):
            self.run_worker()
        self.assertEqual(len(self.calls), calls)

    def test_fixed_entrypoint_rejects_arguments_without_host_access(self):
        self.assertEqual(main(["--database", "/tmp/forged.sqlite"]), 64)

    def test_entrypoint_reports_incomplete_budget_as_nonzero(self):
        from unittest.mock import Mock, patch
        from quant_data.operations import sharadar_selected_refresh as worker
        from types import SimpleNamespace
        selected = SimpleNamespace(binding=SimpleNamespace(mode="active"))
        with patch.object(worker, "StoreMap"), patch.object(worker, "load_registry"), \
             patch.object(worker, "load_bindings", return_value={"sharadar_fundamentals": object()}), \
             patch.object(worker, "pin_binding", return_value=selected), \
             patch.object(worker, "host_fetch", return_value=Mock(secret_values=())), \
             patch.object(worker, "run_refresh") as refresh:
            for outcome in ("daily_budget", "invocation_budget", "provider_http_failure"):
                refresh.return_value = {"outcome": outcome}
                self.assertEqual(worker.main([]), 75)
