import json,unittest
from dataclasses import replace
from quant_data.errors import ResourceLimitError,ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.company.fmp_research import parse_research_response
from quant_data.registry import statement_history_registry_profile
from tests.company import test_fmp_research as fixtures

class FmpStatementHistoryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.FmpResearchTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
    def parse(self,rows,limit="1000",history=True):
        return parse_research_response(json.dumps(rows).encode(),endpoint="income-statement",
            parameters={"symbol":"NST","period":"quarter","limit":limit},subject=self.f.subject,
            captured_at="2026-09-09T01:00:00Z",source_reference="fixture/history.json",history_profile=history)
    def test_large_history_preserves_all_rows_unknown_fields_and_replays_without_writes(self):
        rows=[{"symbol":"NST","date":f"{1990+i//4}-{(i%4+1)*3:02d}-28","period":f"Q{i%4+1}",
            "calendarYear":str(1990+i//4),"revenue":i,"sourceExtra":"x"*10000} for i in range(120)]
        prepared=self.parse(rows)
        self.assertGreater(prepared.byte_count,1024*1024)
        self.assertEqual(len(prepared.rows),120)
        self.assertEqual(prepared.completeness,"partial")
        self.assertEqual(self.f.publisher.publish(prepared,request_id="history").outcome,"succeeded")
        before=mutation_fingerprint(self.f.store_map)
        self.assertEqual(self.f.publisher.publish(prepared,request_id="history-again").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.f.store_map))
        with self.assertRaises(ResourceLimitError):self.parse(rows,limit="100",history=False)
    def test_cap_reached_or_exceeded_does_not_claim_complete_history(self):
        rows=[{"symbol":"NST","date":f"2025-0{i+1}-28","period":"Q1","revenue":i} for i in range(3)]
        parsed=self.parse(rows,limit="3")
        self.assertIn("fmp_statement_history_truncated_at_limit",parsed.warnings)
        with self.assertRaises(ValidationError):self.parse(rows,limit="2")
        with self.assertRaises(ValidationError):self.f.publisher.publish(replace(parsed,history_profile=False),request_id="forged")
    def test_predecessor_registry_cannot_publish_the_larger_history_profile(self):
        from quant_data.company.fmp_research import FmpResearchPublisher
        predecessor=statement_history_registry_profile(self.f.registry)
        self.assertEqual(predecessor.registry_version,"2.77.0")
        publisher=FmpResearchPublisher(self.f.store_map,predecessor)
        with self.assertRaises(ValidationError):
            publisher.publish(self.parse([{"symbol":"NST","date":"2025-12-31","revenue":1}]),request_id="old")


    def test_composite_replay_from_separate_captures_is_zero_write(self):
        a={"symbol":"NST","date":"2025-03-31","revenue":1}
        b={"symbol":"NST","date":"2025-06-30","revenue":2}
        for i,rows in enumerate(([a],[b]),1):
            self.f.publisher.publish(replace(self.parse(rows),captured_at=f"2026-09-09T01:00:0{i}Z"),request_id=str(i))
        before=mutation_fingerprint(self.f.store_map)
        result=self.f.publisher.publish(replace(self.parse([a,b]),captured_at="2026-09-09T02:00:00Z"),request_id="combined")
        self.assertEqual(result.outcome,"unchanged")
        self.assertEqual(result.written_count,0)
        self.assertIsNone(result.snapshot_id)
        self.assertEqual(before,mutation_fingerprint(self.f.store_map))

    def test_exact_microseconds_and_offsets_preserve_cutoff_and_revisions(self):
        from quant_data.company.fmp_research import read_research_inputs,FmpResearchInputsQuery
        a={"symbol":"NST","date":"2025-03-31","revenue":1}
        first=replace(self.parse([a]),captured_at="2026-09-09T08:00:00.000100-04:00")
        self.f.publisher.publish(first,request_id="first-microsecond")
        def read(cut):
            return read_research_inputs(self.f.context(),FmpResearchInputsQuery(self.f.subject.cik,mode="as_of",as_of=cut))
        self.assertEqual(len(read("2026-09-09T12:00:00.000000Z").records),0)
        self.assertEqual(len(read("2026-09-09T12:00:00.000100Z").records),1)
        second=replace(self.parse([{**a,"revenue":2}]),captured_at="2026-09-09T12:00:00.000200Z")
        self.assertEqual(self.f.publisher.publish(second,request_id="second-microsecond").outcome,"succeeded")
        self.assertIn('"revenue":1',{f.name:f.value for f in read("2026-09-09T12:00:00.000150Z").records[0].fields}["payload_json"])
        self.assertIn('"revenue":2',{f.name:f.value for f in read("2026-09-09T08:00:00.000200-04:00").records[0].fields}["payload_json"])


    def test_sql_capture_guard_matches_exact_instants_for_large_offsets_and_fraction_rollover(self):
        import sqlite3
        from pathlib import Path
        sql=(fixtures.ROOT/"quant_data/migrations/company/0013_fmp_research_exact_capture.sql").read_text()
        cases=[
            ("2026-09-09T12:00:00Z","2026-09-10T03:00:00+15:00"),
            ("2026-09-09T12:00:00Z","2026-09-10T11:59:00+23:59"),
            ("2026-09-09T12:00:00Z","2026-09-08T12:01:00-23:59"),
            ("2026-09-09T12:00:00.999999Z","2026-09-09T12:00:01Z"),
            ("1969-12-31T23:59:59.999999Z","1970-01-01T00:00:00Z"),
        ]
        from quant_data.company.fmp_research import _instant_key
        for a,b in cases:
            for first,second in ((a,b),(b,a)):
                with self.subTest(first=first,second=second),sqlite3.connect(":memory:") as c:
                    c.executescript("CREATE TABLE company_fmp_research_rows(natural_identity TEXT,captured_at TEXT);"
                        "CREATE TRIGGER company_fmp_research_row_monotonic_capture BEFORE INSERT ON company_fmp_research_rows BEGIN SELECT 1;END;")
                    c.executescript(sql)
                    c.execute("INSERT INTO company_fmp_research_rows VALUES('id',?)",(first,))
                    if _instant_key(second)<=_instant_key(first):
                        with self.assertRaises(sqlite3.IntegrityError):
                            c.execute("INSERT INTO company_fmp_research_rows VALUES('id',?)",(second,))
                    else:c.execute("INSERT INTO company_fmp_research_rows VALUES('id',?)",(second,))
