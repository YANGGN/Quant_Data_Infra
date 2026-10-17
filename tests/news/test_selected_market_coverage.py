import json,unittest
from pathlib import Path
from quant_data.errors import ConflictError
from quant_data.market.collection_bindings import pin_binding
from quant_data.market.collection_mappings import IdentityEvidence,prepare_mapping,unresolved_mapping
from quant_data.market.collection_universe import CollectionManifestPublisher
from quant_data.news.selected_market_coverage import read_selected_news_coverage,prepare_retained_etf_manifest
from tests.operations import test_collection_prices as pricefixtures

CUT="2026-09-09T05:00:00Z"
class SelectedNewsCoverageTests(unittest.TestCase):
    def setUp(self):
        self.base=pricefixtures.SelectedPriceTests("runTest");self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.f=self.base.f
    def selection(self):return pin_binding(self.f.stores,self.f.bindings["news"])
    def primary(self,selection):
        profiles=[];rows=[]
        for i,s in enumerate(selection.eligible):
            native=s.source_symbol if s.source_symbol=="BRK.B" else s.provider_symbol
            profiles.append({"symbol":native})
            r=unresolved_mapping(s.source_symbol)
            r.update(status="resolved",provider_symbol=native,provider_subject=native,instrument_id=s.instrument_id,
                evidence_pointer="/"+str(i),symbol_field="symbol",subject_field="symbol",reason="Synthetic Alpaca security")
            rows.append(r)
        proof=IdentityEvidence(json.dumps(profiles).encode(),"fixture/alpaca-equities.json","2026-09-09T03:00:00Z","alpaca")
        for r in rows:r.update(evidence_sha256=proof.sha256,evidence_reference=proof.source_reference)
        batch=prepare_mapping(membership_snapshot_id=selection.membership_snapshot_id,provider="alpaca",
            captured_at="2026-09-09T03:10:00Z",source_reference="fixture/alpaca-equity-map.json",
            body=json.dumps(rows).encode(),evidence=(proof,))
        return self.f.publisher.publish(batch).snapshot_id
    def retained(self,selection):
        manifest=prepare_retained_etf_manifest(self.f.stores,selection,cutoff="2026-09-09T04:00:00Z")
        self.assertIn(b"curated_etfs_price",manifest.body)
        membership=CollectionManifestPublisher(self.f.stores,self.f.registry).publish(manifest).snapshot_id
        for provider,at in (("fmp","2026-09-09T04:10:00Z"),("alpaca","2026-09-09T04:20:00Z")):
            proof=IdentityEvidence(b'[{"symbol":"SPY"}]',"fixture/"+provider+"-etf.json",at,provider)
            r=unresolved_mapping("SPY")
            r.update(status="resolved",provider_symbol="SPY",provider_subject="SPY",
                instrument_id=next(s.instrument_id for s in selection.retained if s.provider_symbol=="SPY"),
                evidence_sha256=proof.sha256,evidence_reference=proof.source_reference,evidence_pointer="/0",
                symbol_field="symbol",subject_field="symbol",reason="Synthetic explicit ETF mapping")
            receipt=self.f.publisher.publish(prepare_mapping(membership_snapshot_id=membership,provider=provider,
                captured_at=at,source_reference="fixture/"+provider+"-etf-map.json",body=json.dumps([r]).encode(),evidence=(proof,)))
        return receipt.snapshot_id
    def test_provider_aliases_are_explicit_and_retained_etfs_keep_separate_scope(self):
        s=self.selection();primary=self.primary(s);retained=self.retained(s)
        c=read_selected_news_coverage(self.f.stores,s,cutoff=CUT,
            alpaca_mapping_id=primary,retained_alpaca_mapping_id=retained)
        self.assertEqual(set(c.all_symbols),{"AAPL","BRK-B","SPY","^GSPC"})
        self.assertEqual(c.alpaca_symbols,("AAPL","BRK.B","SPY"))
        self.assertEqual(c.unsupported_index_symbols,("^GSPC",))
        self.assertEqual(c.mapping_gaps,())
        self.assertEqual(c.report()["global_feed_multiplier"],1)
        with self.assertRaises(ConflictError):
            read_selected_news_coverage(self.f.stores,s,cutoff=CUT,alpaca_mapping_id=retained)
    def test_absent_provider_evidence_is_a_gap_without_guessing_alpaca_symbols(self):
        c=read_selected_news_coverage(self.f.stores,self.selection(),cutoff=CUT)
        self.assertEqual(c.alpaca_symbols,())
        self.assertEqual({s for s,r in c.mapping_gaps},{"AAPL","BRK.B","SPY"})
        self.assertEqual(len(c.all_symbols),4)
    def test_more_than_800_symbols_are_covered_in_complete_deterministic_batches(self):
        self.base.test_more_than_800_selected_equities_with_retained_etf_and_index()
        s=self.selection();primary=self.primary(s);retained=self.retained(s)
        c=read_selected_news_coverage(self.f.stores,s,cutoff=CUT,
            alpaca_mapping_id=primary,retained_alpaca_mapping_id=retained)
        self.assertEqual(len(c.all_symbols),903)
        self.assertEqual(len(c.alpaca_symbols),902)
        self.assertEqual(len(c.alpaca_symbol_batches),91)
        self.assertEqual(tuple(s for batch in c.alpaca_symbol_batches for s in batch),c.alpaca_symbols)
        self.assertTrue(all(1<=len(batch)<=10 for batch in c.alpaca_symbol_batches))
        self.assertEqual(c.mapping_gaps,())


class SelectedNewsFailureIsolationTests(unittest.TestCase):
    def test_selected_mapping_failure_does_not_block_global_callbacks(self):
        from unittest.mock import patch
        from datetime import datetime,timezone
        from quant_data.errors import ConflictError
        from quant_data.operations.current_news_refresh import _live_collectors
        with patch("quant_data.news.selected_market_coverage.read_host_news_coverage",side_effect=ConflictError("mapping unavailable")), patch("quant_data.news.current_multi_source.CurrentMultiSourceImporter") as importer:
            callbacks=_live_collectors(stores=object(),registry=object(),environment={},
                observed_at=datetime(2026,9,9,tzinfo=timezone.utc))
            self.assertEqual(len(callbacks),10)
            callbacks["fed_press"]()
            importer.return_value.run_once.assert_called_once()
            with self.assertRaises(ConflictError):callbacks["alpaca_benzinga"]()


class SelectedNewsBatchReportTests(unittest.TestCase):
    def test_full_pages_and_deadline_deferred_batches_are_explicit(self):
        from types import SimpleNamespace
        from quant_data.operations.current_news_refresh import _run_alpaca_batches,_successful_step
        elapsed=[0];calls=[]
        coverage=SimpleNamespace(report=lambda:{"mapping_gaps":[{"symbol":"MISSING","reason":"unresolved"}]})
        def invoke(batch):
            calls.append(batch);elapsed[0]+=61
            return SimpleNamespace(outcome="succeeded",provider_row_count=50)
        result=_run_alpaca_batches((("AAPL",),("MSFT",)),invoke,selected_coverage=coverage,
            max_run_seconds=120,monotonic=lambda:elapsed[0])
        self.assertEqual(calls,[("AAPL",)])
        report=_successful_step("alpaca_benzinga",1,result).mapping()
        self.assertEqual(report["outcome"],"partial")
        self.assertEqual(report["coverage"]["deferred_batches"],1)
        self.assertEqual(report["coverage"]["full_response_pages"],1)
        self.assertEqual(report["coverage"]["pagination_status"],"first_page_only")
        self.assertEqual(report["coverage"]["maximum_total_requests"],11)

    def test_zero_resolved_symbols_keep_gaps_and_report_zero_requests(self):
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from quant_data.operations.current_news_refresh import (
            SOURCE_IDS, _run_alpaca_batches, run_current_news_refresh)
        calls = []
        gaps = [{"symbol": "AAPL", "reason": "alpaca_mapping_not_available"}]
        coverage = SimpleNamespace(report=lambda: {"mapping_gaps": gaps, "alpaca_symbols": 0})
        def alpaca():
            return _run_alpaca_batches((), lambda batch: calls.append(batch),
                                       selected_coverage=coverage)
        callbacks = {source: lambda: SimpleNamespace(outcome="unchanged") for source in SOURCE_IDS}
        callbacks["alpaca_benzinga"] = alpaca
        report = run_current_news_refresh(collectors=callbacks, environment={},
            utcnow=lambda: datetime(2026,9,9,tzinfo=timezone.utc),
            credential_reader=lambda **kwargs: "synthetic")
        step = next(s for s in report.steps if s.source == "alpaca_benzinga")
        self.assertEqual(calls, [])
        self.assertEqual((step.request_cap, step.attempted_requests,
                          step.successful_requests, step.failed_requests), (0,0,0,0))
        self.assertEqual(step.outcome, "unavailable")
        self.assertEqual(step.error, "selected_symbols_unavailable")
        self.assertEqual(step.coverage["mapping_gaps"], gaps)
        self.assertEqual(step.coverage["requested_batches"], 0)
        self.assertEqual(step.coverage["maximum_total_requests"], 9)


class ExpandedNewsInvocationTests(unittest.TestCase):
    def test_all_2343_symbols_are_batched_once_and_global_feeds_are_not_multiplied(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        from quant_data.operations.current_news_refresh import _live_collectors, run_current_news_refresh
        from quant_data.news.selected_market_coverage import SelectedNewsCoverage
        from quant_data.news.current_market_coverage import CurrentMarketNewsInstrument
        symbols = tuple(f"S{i:04}" for i in range(2343))
        instruments = tuple(CurrentMarketNewsInstrument(f"i{i}", symbol, "equity")
                            for i, symbol in enumerate(symbols))
        coverage = SelectedNewsCoverage(instruments, "hash", "members", "fmp", "alpaca", "etfs", symbols, ())
        requests = []
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        def collect(**kwargs):
            requests.append(kwargs["request"])
            return SimpleNamespace(outcome="unchanged", provider_row_count=0)
        at = datetime(2026,9,11,tzinfo=timezone.utc)
        with patch("quant_data.news.selected_market_coverage.read_host_news_coverage", return_value=coverage), \
             patch("quant_data.news.current_multi_source.CurrentMultiSourceImporter") as importer, \
             patch("quant_data.operations.current_news_refresh._credential", return_value="offline"), \
             patch("quant_data.operations.fmp_stock_latest_news_refresh.refresh_fmp_stock_latest_news",
                   return_value=SimpleNamespace(outcome="unchanged")) as stock:
            importer.return_value.run_once.side_effect = collect
            callbacks = _live_collectors(stores=object(), registry=object(), environment={}, observed_at=at,
                                         monotonic=lambda:elapsed[0], sleep=sleep)
            result = run_current_news_refresh(collectors=callbacks, environment={}, utcnow=lambda:at,
                                             credential_reader=lambda **kwargs:"offline")
        alpaca = [r for r in requests if r.feed_id=="alpaca_benzinga"]
        self.assertEqual(len(alpaca), 235)
        self.assertEqual([len(r.symbols) for r in alpaca], [10] * 234 + [3])
        from quant_data.news.current_multi_source import _wire_request, CurrentMultiSourceCredentials
        for request in alpaca:
            _, params, _ = _wire_request(request, CurrentMultiSourceCredentials(
                alpaca_api_key="offline", alpaca_api_secret="offline"))
            self.assertEqual(params["limit"], "50")
            self.assertNotIn("page_token", params)
        self.assertEqual(tuple(s for r in alpaca for s in r.symbols), symbols)
        self.assertEqual(len([r for r in requests if r.feed_id!="alpaca_benzinga"]), 8)
        stock.assert_called_once()
        self.assertEqual(result.mapping()["request_cap"], 244)
        step = next(s for s in result.steps if s.source == "alpaca_benzinga")
        self.assertEqual(step.coverage["symbols_per_batch_limit"], 10)
        self.assertEqual(step.coverage["response_limit"], 50)
        self.assertEqual(step.coverage["requested_batches"], 235)
        self.assertEqual(step.coverage["deferred_batches"], 0)
        self.assertEqual(step.coverage["maximum_total_requests"], 244)
        self.assertEqual(elapsed[0], 117)
        self.assertEqual(result.exit_code, 0)

    def test_earlier_global_work_reduces_alpaca_deadline_and_leaves_websites_time(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        from quant_data.operations.current_news_refresh import _live_collectors
        elapsed = [0]
        coverage = SimpleNamespace(alpaca_symbol_batches=(("NEW",),), report=lambda:{})
        with patch("quant_data.news.selected_market_coverage.read_host_news_coverage", return_value=coverage), \
             patch("quant_data.news.current_multi_source.CurrentMultiSourceImporter"), \
             patch("quant_data.operations.current_news_refresh._run_alpaca_batches") as batches:
            callbacks = _live_collectors(stores=object(), registry=object(), environment={},
                                        observed_at=datetime(2026,9,11,tzinfo=timezone.utc),
                                        monotonic=lambda:elapsed[0])
            elapsed[0] = 420
            callbacks["alpaca_benzinga"]()
        self.assertEqual(batches.call_args.kwargs["max_run_seconds"], 1200)


class SelectedTenSymbolBatchBoundaryTests(unittest.TestCase):
    def test_oversized_groups_and_excess_request_count_fail_before_invocation(self):
        from types import SimpleNamespace
        from quant_data.errors import ResourceLimitError
        from quant_data.operations.current_news_refresh import _run_alpaca_batches
        coverage = SimpleNamespace(report=lambda:{})
        for batches in ((tuple(f"S{i}" for i in range(11)),), (("AAPL",),) * 661):
            calls = []
            with self.subTest(batch_count=len(batches)), self.assertRaises(ResourceLimitError):
                _run_alpaca_batches(batches, lambda batch:calls.append(batch), selected_coverage=coverage)
            self.assertEqual(calls, [])

    def test_existing_total_symbol_ceiling_works_with_ten_symbol_groups(self):
        from types import SimpleNamespace
        from quant_data.operations.current_news_refresh import _run_alpaca_batches
        symbols = tuple(f"S{i:04}" for i in range(6600))
        batches = tuple(symbols[i:i+10] for i in range(0,len(symbols),10))
        elapsed = [0]; calls = []
        def sleep(seconds):elapsed[0] += seconds
        def invoke(batch):
            calls.extend(batch)
            return SimpleNamespace(outcome="unchanged",provider_row_count=0)
        result = _run_alpaca_batches(batches, invoke, selected_coverage=SimpleNamespace(report=lambda:{}),
                                     monotonic=lambda:elapsed[0], sleep=sleep)
        self.assertEqual(tuple(calls), symbols)
        self.assertEqual(result.attempted_requests, 660)
        self.assertEqual(result.coverage["deferred_batches"], 0)
        self.assertEqual(result.exit_code, 0)

    def test_pacing_survives_failure_without_retry_and_rechecks_overslept_deadline(self):
        from types import SimpleNamespace
        from quant_data.errors import ValidationError
        from quant_data.operations.current_news_refresh import _run_alpaca_batches
        for oversleep, expected_starts in ((0, [0, 0.5]), (1, [0])):
            with self.subTest(oversleep=oversleep):
                elapsed = [0]; starts = []; waits = []
                def invoke(batch):
                    starts.append(elapsed[0])
                    if len(starts)==1:raise ValidationError("synthetic source failure")
                    return SimpleNamespace(outcome="unchanged",provider_row_count=0)
                def sleep(seconds):
                    waits.append(seconds);elapsed[0] += seconds + oversleep
                result = _run_alpaca_batches((("AAPL",),("MSFT",)), invoke,
                    selected_coverage=SimpleNamespace(report=lambda:{}), max_run_seconds=61,
                    monotonic=lambda:elapsed[0], sleep=sleep)
                self.assertEqual(starts, expected_starts)
                self.assertEqual(waits, [0.5])
                self.assertEqual(result.failed_requests, 1)
                self.assertEqual(result.attempted_requests, len(expected_starts))
                self.assertEqual(result.coverage["deferred_batches"], 2-len(expected_starts))
