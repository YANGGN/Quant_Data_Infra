from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from quant_data.operations import collection_sharadar_direct as direct_controller

from quant_data.errors import ConflictError
from quant_data.operations.collection_plan import AcquisitionUnit, ProviderBudget
from quant_data.operations.collection_transport import SelectedProviderFetch, host_fetch, request_route
from quant_data.operations import collection_sharadar_direct as direct
from quant_data.operations import sharadar_selected_refresh as refresh


class DirectSharadarRefreshTests(unittest.TestCase):
    def setUp(self):
        self.selection = SimpleNamespace(scope_sha256="a" * 64, membership_snapshot_id="m", mapping_id="p", gaps=())
        self.parameters = tuple(sorted({"ticker": "AAPL", "dimension": "ARQ", "format": "json", "sort": "date.asc",
            "from": "1900-01-01", "to": "2026-09-10", "limit": "10000", "offset": "0"}.items()))
        self.unit = AcquisitionUnit("sharadar_fundamentals", "sharadar", "SHARADAR_DIRECT/fundamentals", "12345",
            self.parameters, "incremental", "2026-09-10T00:00:00.000000Z", ("AAPL",), "a" * 64,
            max_response_bytes=1024, max_rows=10000, timeout_seconds=30)
        self.receipt = {"status": 200, "captured_at": "2026-09-10T00:00:00Z", "content_sha256": "b" * 64}

    def test_ticker_parameter_accepts_200_and_rejects_201_characters(self):
        from quant_data.company.sharadar_direct import _parameters
        from quant_data.errors import ResourceLimitError
        tickers = ["A" * 31 + str(i) for i in range(6)] + ["BB"]
        exact = ",".join(tickers)
        self.assertEqual(len(exact), 200)
        _parameters({**dict(self.parameters), "ticker": exact})
        with self.assertRaisesRegex(ResourceLimitError, "200 characters"):
            _parameters({**dict(self.parameters), "ticker": exact + "B"})

    def test_ticker_parameter_accepts_30_and_rejects_31_short_tickers(self):
        from quant_data.company.sharadar_direct import _parameters
        from quant_data.errors import ValidationError
        tickers = ["T" + str(i).zfill(3) for i in range(31)]
        self.assertLess(len(",".join(tickers)), 200)
        _parameters({**dict(self.parameters), "ticker": ",".join(tickers[:30])})
        with self.assertRaises(ValidationError):
            _parameters({**dict(self.parameters), "ticker": ",".join(tickers)})

    def test_short_ticker_batches_obey_30_ticker_provider_limit(self):
        from quant_data.company.sharadar_direct import DIMENSIONS
        subjects = [SimpleNamespace(provider_symbol="T" + str(i).zfill(3),
            provider_subject=str(i + 1), source_symbol="T" + str(i).zfill(3)) for i in range(61)]
        selection = SimpleNamespace(eligible=subjects, scope_sha256="a" * 64)
        with patch.object(direct, "validate_selection"):
            units = direct.sf1_units(None, selection, schema=SimpleNamespace(channel="sharadar_direct"),
                mode="incremental", observation_window=self.unit.observation_window,
                cutoff=self.receipt["captured_at"], batch_size=100,
                lastupdated=("2026-09-02", "2026-09-10"))
        for dimension in DIMENSIONS:
            matching = [u for u in units if dict(u.parameters)["dimension"] == dimension]
            self.assertEqual([len(u.selected_symbols) for u in matching], [30,30,1])
            self.assertEqual([s for u in matching for s in u.selected_symbols],
                [s.source_symbol for s in subjects])

    def test_batches_cover_each_subject_once_per_dimension_within_character_cap(self):
        from quant_data.company.sharadar_direct import DIMENSIONS
        subjects = [SimpleNamespace(provider_symbol="T" + str(i).zfill(30),
            provider_subject=str(i + 1), source_symbol="T" + str(i).zfill(30)) for i in range(101)]
        selection = SimpleNamespace(eligible=subjects, scope_sha256="a" * 64)
        schema = SimpleNamespace(channel="sharadar_direct")
        with patch.object(direct, "validate_selection"):
            units = direct.sf1_units(None, selection, schema=schema, mode="incremental",
                observation_window=self.unit.observation_window, cutoff=self.receipt["captured_at"],
                batch_size=100, lastupdated=("2026-09-02", "2026-09-10"))
        for dimension in DIMENSIONS:
            matching = [u for u in units if dict(u.parameters)["dimension"] == dimension]
            self.assertEqual(len(matching), 17)
            symbols = [s for u in matching for s in u.selected_symbols]
            self.assertEqual(symbols, [s.source_symbol for s in subjects])
            self.assertTrue(all(len(dict(u.parameters)["ticker"]) <= 200 for u in matching))
            self.assertTrue(all(len(u.selected_symbols) <= 100 for u in matching))
        self.assertEqual(len(units), 17 * len(DIMENSIONS))

    def test_full_page_requires_next_offset_before_partition_is_ready(self):
        pages = iter((SimpleNamespace(next_cursor="10000"), SimpleNamespace(next_cursor=None)))
        requested = []
        def response(root, unit_id, unit):
            if unit.endpoint == "SHARADAR_DIRECT/descriptions":
                return self.receipt, b"description"
            requested.append(dict(unit.parameters)["offset"])
            return self.receipt, b"page"
        def partition(pages, **kwargs):
            return SimpleNamespace(transport_complete=pages[-1].next_cursor is None)
        with patch.object(direct, "_validate_initial"), patch.object(direct, "_response", side_effect=response), \
             patch.object(direct, "parse_metadata", return_value=object()), patch.object(direct, "_parameters", return_value=self.parameters), \
             patch.object(direct, "parse_page", side_effect=lambda *args, **kwargs: next(pages)), \
             patch.object(direct, "prepare_partition", side_effect=partition):
            progress = direct.inspect_partition(root=Path("/tmp").resolve(), stores=object(), selection=self.selection,
                initial_unit=self.unit, cutoff="2026-09-10T00:00:00Z")
        self.assertEqual(progress.outcome, "ready")
        self.assertEqual(requested, ["0", "10000"])
        self.assertEqual(len(progress.page_units), 2)

    def test_descriptions_evidence_is_not_fetched_again_after_publication(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            metadata = direct.metadata_unit(self.selection, mode="incremental", observation_window="2026-09-10T00:00:00.000000Z")
            evidence = (self.receipt, b"description")
            state = {"available": False, "queue_calls": 0}
            def response(root, unit_id, unit):
                return evidence if state["available"] else None
            def queue(**kwargs):
                state["queue_calls"] += 1
                state["available"] = True
                return {"outcome": "acquired", "requests": 1, "received_bytes": 11}
            budget = ProviderBudget("sharadar", 1, 16 * 1024 * 1024, 30, 1000)
            with patch.object(direct, "validate_selection"), patch.object(direct, "requires_active_binding", return_value=False), patch.object(direct, "_response", side_effect=response), \
                 patch.object(direct, "build_plan", return_value=object()), patch.object(direct, "run_queue", side_effect=queue), \
                 patch.object(direct, "publish_definitions", return_value={"outcome": "succeeded", "written_count": 1}):
                first = direct.run_definitions(root=root, stores=object(), registry=object(), selection=self.selection,
                    mode="incremental", observation_window=metadata.observation_window, budget=budget, fetch=object())
                second = direct.run_definitions(root=root, stores=object(), registry=object(), selection=self.selection,
                    mode="incremental", observation_window=metadata.observation_window, budget=budget, fetch=object())
            self.assertEqual((first["requests"], second["requests"], state["queue_calls"]), (1, 0, 1))

    def test_missing_baseline_blocks_before_fetch_and_legacy_state_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fetch = Mock()
            with patch.object(refresh, "validate_selection"), patch.object(refresh, "requires_active_binding", return_value=False), patch.object(refresh, "initial_baseline",
                    side_effect=ConflictError("Sharadar full-history baseline is incomplete")):
                with self.assertRaisesRegex(ConflictError, "baseline is incomplete"):
                    refresh.run_refresh(root=root, stores=object(), registry=object(), selection=self.selection, fetch=fetch)
            self.assertEqual(fetch.call_count, 0)
            legacy = {"contract": "quant_data.selected_sharadar_refresh.v1", "membership_snapshot_id": "m", "mapping_id": "p",
                "baseline_date": "2026-09-09", "last_complete_date": "2026-09-09", "last_complete_window": None, "pending": {}}
            refresh.atomic(root / "refresh-state.json", legacy, replace=True)
            with self.assertRaisesRegex(ConflictError, "baseline reconciliation"):
                refresh._state(root, object(), self.selection, "2026-09-10T00:00:00Z")
        self.assertEqual(refresh.STATE_ROOT.name, "direct")
    def test_all_450_completed_partitions_finalize_without_repeating_fetches(self):
        from datetime import datetime, timezone
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            units = tuple(SimpleNamespace(unit_id=str(i)) for i in range(450))
            pending = {"window": "2026-09-10T00:00:00.000000Z", "from_date": "2026-09-02",
                "to_date": "2026-09-10", "definitions_complete": True,
                "completed_units": [u.unit_id for u in units]}
            state = {"contract": refresh.CONTRACT, "membership_snapshot_id": "m", "mapping_id": "p",
                "baseline_date": "2026-09-09", "last_complete_date": "2026-09-09",
                "last_complete_window": None, "pending": pending}
            refresh.atomic(root / "refresh-state.json", state, replace=True)
            fetch = Mock()
            with patch.object(refresh, "validate_selection"), patch.object(refresh, "requires_active_binding", return_value=False), \
                 patch.object(refresh, "metadata_unit", return_value=SimpleNamespace(unit_id="metadata")), \
                 patch("quant_data.operations.sharadar_recovery._response", return_value=(self.receipt,b"description")), \
                 patch.object(refresh, "parse_metadata", return_value=object()), \
                 patch.object(refresh, "sf1_units", return_value=units):
                result = refresh.run_refresh(root=root, stores=object(), registry=object(),
                    selection=self.selection, fetch=fetch, utcnow=lambda: datetime(2026,9,11,tzinfo=timezone.utc),
                    monotonic=lambda: 0)
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(result["requests"], 0)
            fetch.assert_not_called()
            self.assertIsNone(refresh._read(root / "refresh-state.json")["pending"])

    def test_pending_cross_day_resume_keeps_original_direct_to_date(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            pending = {"window": "2026-09-10T00:00:00.000000Z", "from_date": "2026-09-02", "to_date": "2026-09-10",
                "definitions_complete": True, "completed_units": []}
            state = {"contract": refresh.CONTRACT, "membership_snapshot_id": "m", "mapping_id": "p",
                "baseline_date": "2026-09-09", "last_complete_date": "2026-09-09", "last_complete_window": None, "pending": pending}
            refresh.atomic(root / "refresh-state.json", state, replace=True)
            meta = SimpleNamespace(unit_id="metadata")
            receipt = {"status": 200, "captured_at": "2026-09-10T00:00:00Z", "content_sha256": "b" * 64}
            observed = []
            def units(*args, **kwargs):
                observed.append((kwargs["cutoff"],kwargs["date_to"]))
                return ()
            now = lambda: __import__("datetime").datetime(2026, 9, 11, 10, tzinfo=__import__("datetime").timezone.utc)
            with patch.object(refresh, "validate_selection"), patch.object(refresh, "requires_active_binding", return_value=False), patch.object(refresh, "metadata_unit", return_value=meta), \
                 patch("quant_data.operations.sharadar_recovery._response", return_value=(receipt, b"description")), \
                 patch.object(refresh, "parse_metadata", return_value=object()), patch.object(refresh, "sf1_units", side_effect=units):
                result = refresh.run_refresh(root=root, stores=object(), registry=object(), selection=self.selection,
                    fetch=Mock(), utcnow=now, monotonic=lambda: 0)
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(observed, [("2026-09-11T10:00:00.000000Z", "2026-09-10")])
    def test_direct_route_uses_header_credential_and_rejects_legacy_before_http(self):
        route = request_route(self.unit)
        self.assertLess(direct.metadata_unit(self.selection, mode="incremental", observation_window="2026-09-10T00:00:00.000000Z").timeout_seconds, 30)
        self.assertEqual((route.host, route.path, route.credential_names, route.credential_header, route.credential_parameter),
            ("api.sharadar.com", "/v1.0/data/fundamentals", ("SHARADAR_DIRECT_API",), "x-api-key", None))
        legacy = AcquisitionUnit("sharadar_fundamentals", "sharadar", "SHARADAR/SF1", "12345",
            (("dimension", "ARQ"), ("qopts.per_page", "10000"), ("ticker", "AAPL")), "incremental",
            "2026-09-10T00:00:00.000000Z", ("AAPL",), "a" * 64, timeout_seconds=30)
        fetch = SelectedProviderFetch("sharadar", "secret", credential_name="SHARADAR_DIRECT_API")
        with self.assertRaisesRegex(Exception, "Selected transport differs"):
            fetch(unit=legacy, timeout_seconds=30, max_bytes=legacy.max_response_bytes)
        with patch("quant_data.operations.collection_transport.read_project_credential", return_value="direct-secret") as read:
            hosted = host_fetch(Path("/tmp"), "sharadar", environment={})
        self.assertEqual(hosted.credential_name, "SHARADAR_DIRECT_API")
        self.assertEqual(read.call_args.kwargs["name"], "SHARADAR_DIRECT_API")




if __name__ == "__main__":
    unittest.main()
