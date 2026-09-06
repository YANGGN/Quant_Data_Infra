from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.company.fmp_market_data import FmpCompanySubject
from quant_data.company.fmp_research import (
    FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID,
    FMP_COMPANY_RESEARCH_INPUTS_DATASET_ID,
    FmpResearchInputsQuery, FmpResearchPublisher, parse_research_response,
    read_research_inputs,
)
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, writer_connection, stable_id
from quant_data.tool_platform.context import (
    CapabilitySet, CancellationToken, Deadline, ExecutionBudget, FixedClock,
    ToolExecutionContext,
)

ROOT = Path(__file__).resolve().parents[2]
NORTHSTAR_CIK = "0001000001"

def stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(market=root/"market.sqlite",macro=root/"macro.sqlite",company=root/"company.sqlite",news=root/"news.sqlite")

class FmpResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = stores(Path(self.temp.name))
        self.registry = load_registry(ROOT/"config/system_registry.json", project_root=ROOT)
        initialize_all(self.store_map,self.registry)
        manifest = FixtureManifest.load(ROOT/"tests/fixtures/manifest.json",project_root=ROOT)
        self.assertEqual(CompanyStage4FixtureImporter(self.store_map,manifest).import_fixture("sec_initial").outcome,"succeeded")
        self.subject=FmpCompanySubject(issuer_id=stable_id("company_issuer",NORTHSTAR_CIK),cik=NORTHSTAR_CIK,symbol="NST",instrument_id="synthetic-stage10-nst",identity_evidence_sha256="1"*64)
        self.publisher=FmpResearchPublisher(self.store_map,self.registry)
    def tearDown(self) -> None: self.temp.cleanup()
    def prepared(self,body: object,*,period="annual",captured="2026-09-05T12:00:00Z"):
        raw=json.dumps(body,separators=(",",":")).encode()
        return parse_research_response(raw,endpoint="income-statement",parameters={"symbol":"NST","period":period,"limit":"3","apikey":"secret"},subject=self.subject,captured_at=captured,source_reference="fmp-research/blobs/"+hashlib.sha256(raw).hexdigest()+".json")
    def context(self) -> ToolExecutionContext:
        return ToolExecutionContext(self.store_map,"test","1"*64,"request","1.0.0","company.get_research_inputs","1.0.0","test","1.0.0",ExecutionBudget(max_rows=100,max_operations=100),CapabilitySet(),Deadline(),CancellationToken(),FixedClock(Decimal("0"),"2026-09-07T00:00:00Z"))
    def test_replay_is_zero_and_annual_q4_same_end_coexist(self) -> None:
        annual=[{"symbol":"NST","date":"2025-12-31","calendarYear":"2025","period":"FY","revenue":10}]
        quarter=[{"symbol":"NST","date":"2025-12-31","calendarYear":"2025","period":"Q4","revenue":10}]
        first=self.prepared(annual,period="annual")
        self.assertEqual(self.publisher.publish(first,request_id="one").outcome,"succeeded")
        before=mutation_fingerprint(self.store_map)
        replay=self.prepared(annual,period="annual",captured="2026-09-06T12:00:00Z")
        self.assertEqual(first.semantic_identity,replay.semantic_identity)
        self.assertEqual(self.publisher.publish(replay,request_id="two").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.store_map))
        self.assertEqual(self.publisher.publish(self.prepared(quarter,period="quarter"),request_id="three").outcome,"succeeded")
        with writer_connection(self.store_map,StoreRole.COMPANY) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM company_fmp_research_rows WHERE period_end='2025-12-31'").fetchone()[0],2)
    def test_correction_cutoff_limits_and_unknown_currency(self) -> None:
        original=[{"symbol":"NST","date":"2025-12-31","calendarYear":"2025","period":"FY","revenue":10}]
        corrected=[{"symbol":"NST","date":"2025-12-31","calendarYear":"2025","period":"FY","revenue":11}]
        self.publisher.publish(self.prepared(original),request_id="one")
        corrected_prepared=self.prepared(corrected,captured="2026-09-06T12:00:00Z")
        self.publisher.publish(corrected_prepared,request_id="two")
        before=mutation_fingerprint(self.store_map)
        self.assertEqual(self.publisher.publish(corrected_prepared,request_id="three").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.store_map))
        old=read_research_inputs(self.context(),FmpResearchInputsQuery(NORTHSTAR_CIK,mode="as_of",as_of="2026-09-05T13:00:00Z"))
        new=read_research_inputs(self.context(),FmpResearchInputsQuery(NORTHSTAR_CIK,limit=1))
        self.assertEqual(len(old.records),1); self.assertEqual(len(new.records),1)
        self.assertIn('"revenue":10',dict((f.name,f.value) for f in old.records[0].fields)["payload_json"])
        self.assertIn('"revenue":11',dict((f.name,f.value) for f in new.records[0].fields)["payload_json"])
        parsed=self.prepared(original)
        self.assertEqual(parsed.rows[0].currency,"SOURCE_UNSPECIFIED")
    def test_query_validation_and_recent_period_selection(self) -> None:
        for invalid in (
            lambda: FmpResearchInputsQuery(NORTHSTAR_CIK, mode="invalid"),
            lambda: FmpResearchInputsQuery(NORTHSTAR_CIK, limit=True),
            lambda: FmpResearchInputsQuery(NORTHSTAR_CIK, endpoints=("income-statement",) * 8),
            lambda: FmpResearchInputsQuery(NORTHSTAR_CIK, endpoints=([],)),
        ):
            with self.assertRaises(ValidationError):
                invalid()
        query = FmpResearchInputsQuery(NORTHSTAR_CIK)
        with self.assertRaises(KeyError):
            query["missing"]
        body = [
            {"symbol": "NST", "date": f"2025-{year + 1:02d}-28", "calendarYear": "2025", "period": "FY", "revenue": year}
            for year in range(12)
        ]
        prepared = parse_research_response(
            json.dumps(body).encode(), endpoint="income-statement",
            parameters={"symbol": "NST", "period": "annual", "limit": "12"},
            subject=self.subject, captured_at="2026-09-05T12:00:00Z",
            source_reference="fmp-research/blobs/recent.json",
        )
        self.publisher.publish(prepared, request_id="recent")
        result = read_research_inputs(self.context(), FmpResearchInputsQuery(NORTHSTAR_CIK, limit=8))
        period_ends = [dict((field.name, field.value) for field in record.fields)["period_end"] for record in result.records]
        self.assertEqual(period_ends, sorted(period_ends, reverse=True))
        self.assertEqual(len(period_ends), 8)
    def test_analyst_page_zero_and_raw_time_warnings(self) -> None:
        body = [{"symbol": "NST", "date": "2025-12-31", "reportedCurrency": "USD", "epsAvg": 1.25, "acceptedDate": "2026-01-15 08:30:00"}]
        parsed = parse_research_response(
            json.dumps(body).encode(), endpoint="analyst-estimates",
            parameters={"symbol": "NST", "limit": "1", "page": "0"},
            subject=self.subject, captured_at="2026-09-05T12:00:00Z",
            source_reference="fmp-research/blobs/estimates.json",
        )
        self.assertNotIn("fmp_source_currency_unspecified", parsed.warnings)
        self.assertIn("fmp_analyst_estimate_basis_unspecified", parsed.warnings)
        self.assertIn("fmp_accepted_date_timezone_unspecified", parsed.warnings)
        self.assertIn("fmp_analyst_estimates_may_be_truncated_at_limit", parsed.warnings)
        self.assertEqual(self.publisher.publish(parsed, request_id="estimates").outcome, "succeeded")
        result = read_research_inputs(self.context(), FmpResearchInputsQuery(NORTHSTAR_CIK, endpoints=["analyst-estimates"]))
        fields = {field.name: field.value for field in result.records[0].fields}
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["estimate_basis"], "SOURCE_UNSPECIFIED")
        with self.assertRaises(ValidationError):
            parse_research_response(
                json.dumps(body).encode(), endpoint="analyst-estimates",
                parameters={"symbol": "NST", "limit": "1", "page": "1"},
                subject=self.subject, captured_at="2026-09-05T12:00:00Z",
                source_reference="fmp-research/blobs/invalid-page.json",
            )
    def test_correction_cycles_receive_distinct_predecessor_identities(self) -> None:
        first = [{'symbol': 'NST', 'date': '2025-12-31', 'calendarYear': '2025', 'period': 'FY', 'revenue': 10}]
        second = [{'symbol': 'NST', 'date': '2025-12-31', 'calendarYear': '2025', 'period': 'FY', 'revenue': 11}]
        for request_id, body, captured_at in (
            ('a-one', first, '2026-09-05T12:00:00Z'),
            ('b-one', second, '2026-09-05T13:00:00Z'),
            ('a-two', first, '2026-09-05T14:00:00Z'),
            ('b-two', second, '2026-09-05T15:00:00Z'),
        ):
            self.assertEqual(self.publisher.publish(self.prepared(body, captured=captured_at), request_id=request_id).outcome, 'succeeded')
        with writer_connection(self.store_map, StoreRole.COMPANY) as connection:
            identities = [row[0] for row in connection.execute('SELECT semantic_identity FROM company_fmp_research_snapshots ORDER BY captured_at')]
        self.assertEqual(len(identities), 4)
        self.assertEqual(len(set(identities)), 4)
        before = mutation_fingerprint(self.store_map)
        self.assertEqual(self.publisher.publish(self.prepared(second, captured='2026-09-05T16:00:00Z'), request_id='b-replay').outcome, 'unchanged')
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_prepared_tampering_and_immutable_read_failure_do_not_mutate(self) -> None:
        prepared = self.prepared([{'symbol': 'NST', 'date': '2025-12-31', 'calendarYear': '2025', 'period': 'FY', 'revenue': 10}])
        row = prepared.rows[0]
        for tampered in (
            replace(prepared, rows=(replace(row, semantic_hash='0' * 64),)),
            replace(prepared, rows=(replace(row, symbol='OTHER'),)),
            replace(prepared, rows=(replace(row, source_row_pointer='/99'),)),
            replace(prepared, rows=(replace(row, source_row_index=2, source_row_pointer='/1'),)),
            replace(prepared, content_sha256='0' * 64),
        ):
            before = mutation_fingerprint(self.store_map)
            with self.assertRaises(ValidationError):
                self.publisher.publish(tampered, request_id='tamper-' + tampered.rows[0].semantic_hash[:4])
            self.assertEqual(before, mutation_fingerprint(self.store_map))
        before = mutation_fingerprint(self.store_map)
        with patch('quant_data.company.fmp_research.quiet_immutable_read_connection', side_effect=RuntimeError('immutable read failed')):
            with self.assertRaisesRegex(RuntimeError, 'immutable read failed'):
                self.publisher.publish(prepared, request_id='read-failure')
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_limit_sentinel_does_not_consume_returned_row_budget(self) -> None:
        annual_rows = [
            {'symbol': 'NST', 'date': f'{1900 + index}-12-31', 'calendarYear': str(1900 + index), 'period': 'FY', 'revenue': index}
            for index in range(100)
        ]
        annual_raw = json.dumps(annual_rows, separators=(',', ':')).encode()
        annual = parse_research_response(
            annual_raw,
            endpoint='income-statement',
            parameters={'symbol': 'NST', 'period': 'annual', 'limit': '100'},
            subject=self.subject,
            captured_at='2026-09-05T12:00:00Z',
            source_reference='fmp-research/blobs/hundred.json',
        )
        earnings_raw = json.dumps([{'symbol': 'NST', 'date': '2100-12-31'}]).encode()
        earnings = parse_research_response(
            earnings_raw,
            endpoint='earnings',
            parameters={'symbol': 'NST', 'limit': '1'},
            subject=self.subject,
            captured_at='2026-09-05T13:00:00Z',
            source_reference='fmp-research/blobs/one-earnings.json',
        )
        self.assertEqual(self.publisher.publish(annual, request_id='hundred').outcome, 'succeeded')
        self.assertEqual(self.publisher.publish(earnings, request_id='one-earnings').outcome, 'succeeded')
        result = read_research_inputs(self.context(), FmpResearchInputsQuery(NORTHSTAR_CIK, limit=100))
        self.assertEqual(len(result.records), 100)
        self.assertTrue(result.truncation.applied)
        self.assertTrue(result.truncation.has_more)
        self.assertIsNone(result.truncation.total_known_count)

    def test_full_as_reported_period_precedence_limits_and_malformed_rows(self) -> None:
        body=[{"symbol":"NST","date":"2026-01-01","calendarYear":"2025","period":"FY","Financial Statements":{"Income Statement":{"documentperiodenddate":"2025-12-31"}}}]
        raw=json.dumps(body).encode()
        parsed=parse_research_response(raw,endpoint="financial-statement-full-as-reported",parameters={"symbol":"NST","period":"annual","limit":"3"},subject=self.subject,captured_at="2026-09-05T12:00:00Z",source_reference="fmp-research/blobs/full.json")
        self.assertEqual(parsed.rows[0].period_end,"2025-12-31"); self.assertIn("fmp_full_as_reported_document_period_end_overrides_top_date",parsed.warnings)
        segments=[{"symbol":"NST","date":f"202{n}-12-31","calendarYear":str(2020+n),"period":"FY","name":str(n)} for n in range(4)]
        segment=parse_research_response(json.dumps(segments).encode(),endpoint="revenue-product-segmentation",parameters={"symbol":"NST","limit":"2"},subject=self.subject,captured_at="2026-09-05T12:00:00Z",source_reference="fmp-research/blobs/segment.json")
        self.assertEqual(segment.raw_row_count,4); self.assertEqual(len(segment.rows),2)
        with self.assertRaises(ValidationError): self.prepared([{"symbol":"NST","date":"2025-12-31"},{"symbol":"NST","date":"2025-12-31"}])

if __name__ == "__main__": unittest.main()
