from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.canonical_inspector import CanonicalInspectorReadService, CanonicalInspectorApplication, _render_page
from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.inspector_equibles import read_equibles_progress
from quant_data.inspector_fetch_status import read_fetch_status, _slots
from quant_data.fingerprint import mutation_fingerprint
from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
import tests.company.test_equibles_transcripts as fixtures

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026,9,7,12,tzinfo=timezone.utc)


def report():
    return {"contract":"quant_data.equibles_transcript_backfill.v1","outcome":"daily_quota",
        "universe":519,"completed_tickers":2,"transcripts":60,"requests_this_run":60,
        "current_ticker":"ABBV","quota":{"attempted":100,"remaining":0,"reset":1788825600},
        "blocked":None}


def save_progress(root, value=None):
    root.mkdir(parents=True,exist_ok=True)
    path=root/"status.json"
    path.write_text(json.dumps(report() if value is None else value))
    os.utime(path,(NOW.timestamp()-60,NOW.timestamp()-60))
    return path


class EquiblesProgressTests(unittest.TestCase):
    def test_checkpoint_is_sanitized_current_evidence_without_host_probes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"progress";path=save_progress(root)
            before=path.read_bytes()
            with patch("quant_data.inspector_fetch_status._command") as host:
                snapshot=read_fetch_status("2026-09-01",observed_at=NOW,probe=False,
                    unit_output="",journal_output="",equibles_root=root)
                host.assert_not_called()
            eq=snapshot["equibles"]
            self.assertEqual((eq["completed_tickers"],eq["quota_used"],eq["outcome"]),(2,100,"quota_deferred"))
            self.assertEqual(before,path.read_bytes())
            value=report();value.update(outcome="blocked",blocked={"reason":"secret /private/key"})
            save_progress(root,value)
            self.assertNotIn("secret",json.dumps(read_equibles_progress(root,observed_at=NOW)))
            self.assertFalse(read_equibles_progress(None,observed_at=NOW)["available"])

    def test_invalid_future_oversize_and_symlink_checkpoints_are_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"progress";path=save_progress(root)
            for change in ({"completed_tickers":True},{"outcome":"complete"},{"current_ticker":"<script>"},{"quota":{"attempted":101,"remaining":0}}):
                value=report();value.update(change);save_progress(root,value)
                self.assertFalse(read_equibles_progress(root,observed_at=NOW)["available"],change)
            save_progress(root);os.utime(path,(NOW.timestamp()+1,NOW.timestamp()+1))
            self.assertFalse(read_equibles_progress(root,observed_at=NOW)["available"])
            path.write_text("x"*17000)
            self.assertFalse(read_equibles_progress(root,observed_at=NOW)["available"])
            path.unlink();os.mkfifo(path)
            self.assertFalse(read_equibles_progress(root,observed_at=NOW)["available"])
            path.unlink();target=Path(temp)/"outside.json";target.write_text(json.dumps(report()));path.symlink_to(target)
            self.assertFalse(read_equibles_progress(root,observed_at=NOW)["available"])

    def test_daily_utc_timer_is_one_slot_per_eastern_day_across_dst(self):
        for date,expected in (("2026-03-08","20:10 EDT"),("2026-11-01","19:10 EST")):
            units="\n".join(("Id=quant-data-equibles-transcripts.timer","LoadState=loaded","ActiveState=active",
                "TimersCalendar={ OnCalendar=*-*-* 00:10:00 UTC ; next_elapse=n/a }",
                "NextElapseUSecRealtime=Tue 2026-09-08 00:10:00 UTC"))
            result=read_fetch_status(date,observed_at=NOW,probe=False,unit_output=units,journal_output="")
            self.assertEqual(len(result["focus_events"]),1)
            self.assertEqual(result["focus_events"][0]["scheduled_local"],expected)
            self.assertEqual(result["focus_events"][0]["datasets"],["company.equibles.transcripts"])
            self.assertEqual(result["equibles"]["next_scheduled_at"],"2026-09-08T00:10:00Z")


class EquiblesInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        root=Path(cls.temp.name)
        cls.stores=StoreMap.four_explicit(**{r:root/(r+".sqlite") for r in ("market","macro","company","news")})
        cls.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(cls.stores,cls.registry)
        publisher=EquiblesTranscriptPublisher(cls.stores,cls.registry)
        for symbol,q in (("AAPL",1),("MSFT",2)):
            pages=(fixtures.page(fixtures.body(symbol,q,total=3,count=2)),fixtures.page(fixtures.body(symbol,q,offset=2,total=3,count=1)))
            publisher.publish(symbol=symbol,instrument_id="fixed-"+symbol,event=fixtures.event(symbol,q),pages=pages)
        cls.reader=CanonicalInspectorReadService(cls.stores,cls.registry)
        cls.before=mutation_fingerprint(cls.stores)

    @classmethod
    def tearDownClass(cls):
        assert cls.before==mutation_fingerprint(cls.stores)
        cls.temp.cleanup()

    def test_catalog_filters_pagination_and_transcript_navigation_preserve_evidence(self):
        result=self.reader.inspect({"view":"company-transcripts","symbol":"aapl","year":"2020","quarter":"1"})
        self.assertEqual(result["total"],1)
        row=result["rows"][0]
        self.assertEqual((row["symbol"],row["total_turn_count"],row["page_count"]),("AAPL",3,2))
        document=_render_page(result,{"view":"company-transcripts"},"fixture",None)
        self.assertIn("Read transcript: AAPL FY2020 Q1",document)
        self.assertIn("capture_id="+row["capture_id"],document)
        query={"view":"company-transcripts","capture_id":row["capture_id"],"limit":"2"}
        first=self.reader.inspect(query);second=self.reader.inspect({**query,"page":"2"})
        self.assertEqual([r["text"] for r in first["rows"]+second["rows"]],["Raw words 0","Raw words 1","Raw words 2"])
        self.assertIsNone(first["rows"][0]["speaker_name"])
        detail=_render_page(first,query,"fixture",None)
        self.assertIn("Back to transcripts",detail)
        self.assertNotIn('name="direction"',detail)
        self.assertNotIn("source_reference",detail)

    def test_data_status_exposes_backfill_progress_in_active_row_details(self):
        from quant_data.inspector_status import read_inspector_status
        progress=Path(self.temp.name)/"progress"
        save_progress(progress)
        app=CanonicalInspectorApplication(self.stores,self.registry,
            metadata_reader=lambda:read_inspector_status(self.stores,registry=self.registry,
                operations_root=Path(self.temp.name)/"refresh",equibles_root=progress,observed_at=NOW))
        response=app.handle("GET","/data-status")
        self.assertEqual(response.status,200)
        self.assertIn(b"2 of 519 companies complete",response.body)
        self.assertIn(b"60 calls stored",response.body)
        self.assertIn(b"company.equibles.transcripts",response.body)

    def test_invalid_inputs_reject_before_any_store_open(self):
        with patch("quant_data.canonical_inspector._immutable_store_connection") as opened:
            for args in ({"sql":"select"},{"year":"2020;drop"},{"quarter":"5"},{"symbol":"AAPL'"},{"capture_id":"../status.json"},{"limit":"101"},{"direction":"DROP"}):
                with self.assertRaises((ValidationError, ResourceLimitError)):self.reader.inspect({"view":"company-transcripts",**args})
            opened.assert_not_called()

    def test_transcript_html_and_json_routes_use_fixture_store_without_live_defaults(self):
        app=CanonicalInspectorApplication(self.stores,self.registry)
        with patch("quant_data.inspector_fetch_status._command") as host:
            response=app.handle("GET","/?view=company-transcripts&symbol=AAPL")
            self.assertEqual(response.status,200)
            self.assertIn(b"Company transcripts",response.body)
            self.assertEqual(app.handle("GET","/api/rows?view=company-transcripts&symbol=MSFT").status,200)
            self.assertEqual(app.handle("POST","/api/rows?view=company-transcripts").status,405)
            self.assertEqual(app.handle("GET","/api/rows?view=company-transcripts&sql=select").status,400)
            app.handle("GET","/healthz")
            host.assert_not_called()
