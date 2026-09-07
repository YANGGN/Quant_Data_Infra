from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import unittest

from quant_data.company.fmp_analyst_history import FmpAnalystPublisher, parse_analyst_response, read_analyst_history
from quant_data.company.fmp_market_data import FmpCompanySubject
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ValidationError, ConflictError, ResourceLimitError, RegistryError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry, analyst_history_registry_profile
from quant_data.stores import StoreMap, StoreRole, writer_connection, stable_id
from quant_data.tool_platform.generate import generated_bytes

ROOT=Path(__file__).resolve().parents[2]
CIK="0001000001"

class AnalystHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/tmp")
        p=Path(self.temp.name)
        self.stores=StoreMap.four_explicit(**{r:p/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        manifest=FixtureManifest.load(ROOT/"tests/fixtures/manifest.json",project_root=ROOT)
        CompanyStage4FixtureImporter(self.stores,manifest).import_fixture("sec_initial")
        self.subject=FmpCompanySubject(issuer_id=stable_id("company_issuer",CIK),cik=CIK,
            symbol="NST",instrument_id="synthetic-stage10-nst",identity_evidence_sha256="1"*64)
        self.publisher=FmpAnalystPublisher(self.stores,self.registry)
    def tearDown(self): self.temp.cleanup()
    def prepare(self, rows, endpoint="analyst-estimates", period="annual", at="2026-09-07T01:00:00Z", page="0"):
        body=json.dumps(rows).encode()
        params={"symbol":"NST"}
        if endpoint=="analyst-estimates": params.update(period=period,page=page,limit="100")
        return parse_analyst_response(body,endpoint=endpoint,parameters=params,subject=self.subject,
            captured_at=at,source_reference="analyst/blobs/"+hashlib.sha256(body).hexdigest()+".json")
    def pub(self, rows, **kw):
        return self.publisher.publish(self.prepare(rows,**kw),request_id="fixture")
    def read(self, **kw): return read_analyst_history(self.stores,cik=CIK,**kw)
    def test_forecast_dates_do_not_backdate_availability_and_periods_coexist(self):
        row={"symbol":"NST","date":"1996-12-31","epsAvg":2}
        self.pub([row]); self.pub([row],period="quarter")
        self.assertEqual(self.read(as_of="2026-01-01T00:00:00Z")["records"],[])
        rows=self.read()["records"]
        self.assertEqual(len(rows),2)
        self.assertEqual({r["request_period"] for r in rows},{"annual","quarter"})
        self.assertTrue(all(r["source_event_date"] is None and r["currency"]=="SOURCE_UNSPECIFIED" for r in rows))
    def test_mixed_capture_overlap_replays_zero_and_correction_cycle(self):
        a={"symbol":"NST","date":"2025-12-31","epsAvg":2}
        b={**a,"date":"2024-12-31"}
        self.pub([a])
        self.pub([b],at="2026-09-07T02:00:00Z",page="1")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.pub([b,a],at="2026-09-07T03:00:00Z").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        self.pub([{**a,"epsAvg":3},b],at="2026-09-07T04:00:00Z")
        self.pub([a],at="2026-09-07T05:00:00Z")
        history=self.read(include_versions=True)["records"]
        self.assertEqual(len(history),4)
        revisions=sorted((r for r in history if r["target_period_end"]=="2025-12-31"),key=lambda r:r["version_sequence"])
        self.assertEqual([r["version_sequence"] for r in revisions],[1,2,3])
        self.assertEqual([json.loads(r["payload_json"])["epsAvg"] for r in revisions],[2,3,2])
        self.assertEqual(len(self.read(as_of="2026-09-07T03:00:00Z")["records"]),2)
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ConflictError): self.pub([{**a,"epsAvg":4}],at="2026-09-07T05:00:00Z")
        self.assertEqual(before,mutation_fingerprint(self.stores))
    def test_undated_current_dated_recommendations_and_targets(self):
        self.pub([{"symbol":"NST","buy":3,"hold":2,"consensus":"Buy"}],endpoint="grades-consensus")
        grade={"symbol":"NST","date":"2012-01-01","gradingCompany":"Firm","previousGrade":"Hold","newGrade":"Buy","action":"upgrade"}
        parsed=self.prepare([grade,grade],endpoint="grades")
        self.assertIn("indistinguishable_source_rows_collapsed",parsed.warnings)
        self.publisher.publish(parsed,request_id="grade")
        target={"symbol":"NST","publishedDate":"2021-01-01T09:00:00Z","newsURL":"https://example.test/target","analystName":"","analystCompany":"Firm","priceTarget":5}
        self.pub([target],endpoint="price-target")
        self.pub([{**target,"priceTarget":6}],endpoint="price-target",at="2026-09-07T02:00:00Z")
        records=self.read()["records"]
        current=next(r for r in records if r["endpoint"]=="grades-consensus")
        self.assertIsNone(current["source_event_date"]); self.assertIsNone(current["target_period_end"])
        dated=next(r for r in records if r["endpoint"]=="grades")
        self.assertEqual(dated["event_precision"],"date")
        targetrow=next(r for r in records if r["endpoint"]=="price-target")
        self.assertEqual(targetrow["event_precision"],"datetime");self.assertEqual(targetrow["version_sequence"],2)
    def test_missing_recommendation_action_is_preserved_without_inference(self):
        base={"symbol":"NST","date":"2025-11-24","gradingCompany":"Firm","newGrade":"Outperform","previousGrade":None}
        raw=[{**base,"action":""},{**base,"date":"2025-11-25","action":None},{**base,"date":"2025-11-26"}]
        p=self.prepare(raw,endpoint="grades")
        self.assertIn("source_recommendation_action_missing",p.warnings)
        self.assertEqual(len(p.rows),3)
        self.assertEqual(sorted((json.loads(r.payload_json) for r in p.rows),key=lambda r:r["date"]),raw)
        self.publisher.publish(p,request_id="missing-grade-action")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.pub(list(reversed(raw)),endpoint="grades").outcome,"unchanged")
        for change in ({"action":123},{"action":True},{"newGrade":None},{"gradingCompany":None}):
            with self.assertRaises(ValidationError):self.prepare([{**base,**change}],endpoint="grades")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_prepared_tampering_bad_scope_and_numeric_values_fail_without_writes(self):
        row={"symbol":"NST","date":"2025-12-31","epsAvg":2}
        p=self.prepare([row]); before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError):
            self.publisher.publish(replace(p,rows=(replace(p.rows[0],payload_json="{}"),)),request_id="tamper")
        for bad in ([{**row,"symbol":"MSFT"}],[{**row,"numberOfAnalystsEps":-1}],[{**row,"epsAvg":True}]):
            with self.assertRaises(ValidationError): self.prepare(bad)
        with self.assertRaises(ValidationError): self.prepare([row],page="10")
        with self.assertRaises(ValidationError): self.prepare([row],at="2026-09-07")
        self.assertEqual(before,mutation_fingerprint(self.stores))
    def test_empty_and_earnings_lastupdated_do_not_create_revisions(self):
        row={"symbol":"NST","date":"2025-12-31","epsEstimated":2,"lastUpdated":"2026-09-01"}
        self.pub([row],endpoint="earnings")
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.pub([{**row,"lastUpdated":"2026-09-07"}],endpoint="earnings").outcome,"unchanged")
        self.assertEqual(self.pub([],endpoint="earnings").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
    def test_conflicting_earnings_dates_are_excluded_with_raw_evidence(self):
        first={"symbol":"NST","date":"1993-02-16","epsActual":0.015,"revenueActual":215600000,"lastUpdated":"2026-09-04"}
        conflict={**first,"epsActual":0.01152,"revenueActual":210500000,"lastUpdated":"2025-04-25"}
        good={**first,"date":"1993-05-16"}
        raw=[first,conflict,first,good]
        p=self.prepare(raw,endpoint="earnings")
        self.assertEqual(json.loads(p.raw_body),raw)
        self.assertEqual(p.raw_row_count,4)
        self.assertEqual(len(p.rows),1)
        self.assertEqual(p.rows[0].source_event_date,"1993-05-16")
        self.assertEqual(p.rows[0].source_row_index,4)
        self.assertIn("conflicting_earnings_dates_excluded",p.warnings)
        reverse=self.prepare(list(reversed(raw)),endpoint="earnings")
        self.assertEqual([r.semantic_hash for r in reverse.rows],[r.semantic_hash for r in p.rows])
        self.publisher.publish(p,request_id="conflicting-earnings-fixture")
        with writer_connection(self.stores,StoreRole.COMPANY) as c:
            capture=c.execute("SELECT raw_row_count,normalized_row_count,warnings_json FROM company_fmp_analyst_captures").fetchone()
            self.assertEqual(tuple(capture[:2]),(4,1))
            self.assertIn("conflicting_earnings_dates_excluded",json.loads(capture[2]))
        self.assertEqual([r["source_event_date"] for r in self.read()["records"]],["1993-05-16"])
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.pub(raw,endpoint="earnings").outcome,"unchanged")
        self.assertEqual(self.pub([first,conflict],endpoint="earnings").outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_conflicting_estimate_periods_are_excluded_with_raw_evidence(self):
        first={"symbol":"NST","date":"2026-01-03","epsAvg":5.41483,"revenueAvg":13832842974}
        conflict={**first,"epsAvg":6.12515,"revenueAvg":14759222428}
        good={**first,"date":"2025-01-04"}
        raw=[first,conflict,first,good]
        for period in ("annual","quarter"):
            self.pub([first],period=period,at="2026-09-07T00:00:00Z")
            p=self.prepare(raw,period=period)
            self.assertEqual(json.loads(p.raw_body),raw)
            self.assertEqual(p.raw_row_count,4)
            self.assertEqual(len(p.rows),1)
            self.assertEqual(p.rows[0].target_period_end,"2025-01-04")
            self.assertEqual(p.rows[0].source_row_index,4)
            self.assertIn("conflicting_estimate_periods_excluded",p.warnings)
            reverse=self.prepare(list(reversed(raw)),period=period)
            self.assertEqual([r.semantic_hash for r in reverse.rows],[r.semantic_hash for r in p.rows])
            self.publisher.publish(p,request_id="conflicting-estimates-fixture")
            with writer_connection(self.stores,StoreRole.COMPANY) as c:
                capture=c.execute("SELECT raw_row_count,normalized_row_count,warnings_json FROM company_fmp_analyst_captures WHERE request_period=? AND raw_row_count=4",(period,)).fetchone()
                self.assertEqual(tuple(capture[:2]),(4,1))
                self.assertIn("conflicting_estimate_periods_excluded",json.loads(capture[2]))
            retained=[r for r in self.read()["records"] if r["request_period"]==period]
            self.assertEqual({r["target_period_end"] for r in retained},{"2026-01-03","2025-01-04"})
            self.assertTrue(all(json.loads(r["payload_json"])["epsAvg"]==first["epsAvg"] for r in retained))
            before=mutation_fingerprint(self.stores)
            self.assertEqual(self.pub(raw,period=period).outcome,"unchanged")
            self.assertEqual(self.pub([first,conflict],period=period).outcome,"unchanged")
            self.assertEqual(before,mutation_fingerprint(self.stores))
        for endpoint in ("grades-historical","price-target"):
            rows=[{"symbol":"NST","date":"2026-01-03","publishedDate":"2026-01-03T00:00:00Z","newsURL":"https://example.test/target","analystName":"A","analystCompany":"Firm","value":1}]
            with self.assertRaises(ValidationError):self.prepare(rows+[{**rows[0],"value":2}],endpoint=endpoint)

    def test_bounded_reader_pagination_and_sql_immutability(self):
        self.pub([{"symbol":"NST","date":f"202{i}-12-31","epsAvg":2} for i in range(3)])
        first=self.read(limit=2); self.assertTrue(first["truncated"])
        self.assertEqual(len(self.read(limit=2,offset=first["next_offset"])["records"]),1)
        for kwargs in ({"limit":True},{"offset":-1},{"endpoint":"sql"},{"as_of":"2020-01-01"}):
            with self.assertRaises(ValidationError): self.read(**kwargs)
        with writer_connection(self.stores,StoreRole.COMPANY) as c:
            for table in ("captures","observation_versions","capture_membership"):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("DELETE FROM company_fmp_analyst_"+table)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_microsecond_cutoffs_and_offset_equivalent_instants(self):
        row={"symbol":"NST","date":"2025-12-31","epsAvg":2}
        self.pub([row],at="2026-09-07T01:00:00.000002Z")
        self.assertEqual(self.read(as_of="2026-09-07T01:00:00.000001Z")["records"],[])
        self.assertEqual(len(self.read(as_of="2026-09-06T21:00:00.000002-04:00")["records"]),1)
        self.pub([{**row,"epsAvg":3}],at="2026-09-06T21:00:00.000003-04:00")
        history=self.read(include_versions=True)["records"]
        self.assertEqual({r["captured_at"] for r in history},{"2026-09-07T01:00:00.000002Z","2026-09-07T01:00:00.000003Z"})
        result=self.read(as_of="2026-09-07T01:00:00.000002Z")["records"]
        self.assertEqual(json.loads(result[0]["payload_json"])["epsAvg"],2)
        with self.assertRaises(ConflictError):
            self.pub([{**row,"epsAvg":4}],at="2026-09-06T21:00:00.000003-04:00")

    def test_registry_projection_is_exact_and_regenerates_successor(self):
        previous=analyst_history_registry_profile(self.registry)
        self.assertEqual(previous.source_sha256,"6b6284c184d1b4cf92bab56fe7afb80de34d78a39488c96bd21be86a59db83c8")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root=Path(directory);(root/"config").mkdir()
            (root/"config/system_registry.json").write_text(json.dumps(previous.raw,ensure_ascii=True,indent=1,sort_keys=True)+"\n")
            payload,_,_=generated_bytes(root)
            self.assertEqual(payload,(ROOT/"config/system_registry.json").read_bytes())
        changed=replace(self.registry,migrations=tuple(replace(m,sha256="0"*64) if m.id=="company:0009_fmp_analyst_history" else m for m in self.registry.migrations))
        with self.assertRaises(RegistryError): analyst_history_registry_profile(changed)

if __name__=="__main__": unittest.main()
