"""Bounded daily refresh regressions over explicit temporary exports only."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta,date
import hashlib,json,sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from quant_data.operations.forward_pe import materialize
from quant_data.operations.derived_refresh import refresh,run_lock
from quant_data.company.forward_pe_store import open_current,day_row
from quant_data.dashboard.forward_pe_page import read_forward_pe
from tests.company.test_forward_pe import fixture,SESSIONS,CUTOFF

class DailyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.inputs=fixture();self.inputs["flags"]=[]
        self.inputs["freshness"]={"analyst-estimates":CUTOFF,"earnings":CUTOFF}
        self.now=datetime(2026,9,21,12,tzinfo=timezone.utc)
        self.name="20260920T000000000000Z.sqlite"
        self.items=[self.inputs]

    def build(self):
        inputs={x["symbol"]:dict(x,end="2023-12-07") for x in self.items}
        materialize(self.root/self.name,self.items,lambda s:inputs[s["symbol"]],SESSIONS,
                    cutoff=CUTOFF,start="2023-12-04",end="2023-12-07",allow_unverified_basis=False)
        (self.root/"current.json").write_text(json.dumps({"artifact":self.name}))
        self.hash=hashlib.sha256((self.root/self.name).read_bytes()).hexdigest()

    def read(self,subject,cutoff,start,end,since,history):
        data=deepcopy(next(x for x in self.items if x["symbol"]==subject["symbol"]))
        data.update(cutoff=cutoff,start=start,end=end)
        return data

    def run_refresh(self,**kwargs):
        r=refresh(self.root,now=kwargs.pop("now",self.now),selected=self.items,
                  sessions=kwargs.pop("sessions",SESSIONS),read=kwargs.pop("read",self.read),**kwargs)
        self.assertEqual(hashlib.sha256((self.root/self.name).read_bytes()).hexdigest(),self.hash)
        return r

    def row(self,day):
        with open_current(self.root) as (_,db,base,meta):
            return day_row(db,base,"instrument",day)

    def test_appends_and_serves_baseline_and_new_days_with_lineage(self):
        self.build();result=self.run_refresh()
        self.assertEqual(result["appended_rows"],2)
        page=read_forward_pe(self.root,"ODD","all")
        self.assertEqual(len(page["days"]),6)
        self.assertEqual(self.row("2023-12-06")[5],12)
        self.assertEqual(self.row("2023-12-11")[5],15)
        with open_current(self.root) as (_,db,base,meta):
            self.assertEqual(db.execute("SELECT COUNT(*) FROM daily").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM daily_lineage").fetchone()[0],2)
            self.assertNotIn("inputs_zlib",[r[1] for r in db.execute("PRAGMA table_info(source_inputs)")])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_historical_price_correction_preserves_original_estimate(self):
        self.build()
        self.inputs["prices"][2].update(version_id="repaired",close_value="280")
        self.inputs["estimates"][1]["payload_json"]=json.dumps({"epsAvg":99,"shareBasis":"ordinary_split_adjusted","splitBasisDate":"2026-09-19"})
        result=self.run_refresh()
        old=self.row("2023-12-06")
        self.assertEqual((old[3],old[4],old[5]),(280,14,20))
        self.assertEqual(result["repaired_prices"],1)
        self.assertEqual(self.row("2023-12-08")[4],111)

    def test_replay_and_new_consensus_do_not_rewrite_published_denominators(self):
        self.build();self.run_refresh()
        before=self.row("2023-12-11")
        self.inputs["estimates"][1]["payload_json"]=json.dumps({"epsAvg":999})
        result=self.run_refresh(now=self.now+timedelta(hours=7))
        self.assertEqual(result["changed_rows"],0)
        self.assertEqual(self.row("2023-12-11"),before)

    def test_one_failed_ticker_does_not_stop_other_ticker(self):
        other=deepcopy(self.inputs);other.update(symbol="TWO",instrument_id="instrument2")
        self.items.append(other);self.build()
        def read(subject,*args):
            if subject["symbol"]=="TWO":raise OSError("fixture unavailable")
            return self.read(subject,*args)
        result=self.run_refresh(read=read)
        self.assertEqual(result["appended_rows"],2)
        self.assertEqual(result["outcomes"]["waiting_inputs"],1)
        with open_current(self.root) as (_,db,base,meta):
            self.assertEqual(db.execute("SELECT price_checked_at FROM refresh_members WHERE instrument_id='instrument2'").fetchone()[0],CUTOFF)
        self.assertEqual(len(read_forward_pe(self.root,"TWO","all")["days"]),4)

    def test_total_source_failure_preserves_pointer(self):
        self.build();pointer=(self.root/"current.json").read_bytes()
        def fail(*args):raise OSError("fixture")
        with self.assertRaises(ValueError):self.run_refresh(read=fail)
        self.assertEqual((self.root/"current.json").read_bytes(),pointer)
        self.assertEqual(json.loads((self.root/"refresh-status.json").read_text())["state"],"failed")

    def test_stale_feed_is_visible_but_saved_usable_estimates_remain_available(self):
        self.build();self.inputs["freshness"]={}
        result=self.run_refresh()
        self.assertEqual(result["outcomes"]["stale_inputs"],1)
        page=read_forward_pe(self.root,"ODD","all")
        self.assertEqual(page["ticker_refresh"]["outcome"],"stale_inputs")
        self.assertIsNotNone(page["days"][-1][3])
        self.assertIn("stale_earnings_input",page["windows"][page["days"][-1][5]]["flags"])

    def test_unmapped_new_report_blocks_old_anchor(self):
        self.build()
        e=deepcopy(self.inputs["earnings"][0]);e.update(natural_identity="new",observation_version_id="new",
            source_event_date="2023-12-07",source_time_raw="2023-12-07T21:05:00Z",
            payload_json=json.dumps({"epsActual":1,"revenueActual":555555}))
        self.inputs["earnings"].append(e)
        self.run_refresh()
        self.assertIsNone(self.row("2023-12-08")[5])
        self.assertEqual(self.row("2023-12-08")[6],"unmapped_announcement")

    def test_run_lock_prevents_overlapping_writers(self):
        self.build()
        with run_lock(self.root):
            with self.assertRaises(BlockingIOError):self.run_refresh()

    def test_pointer_change_during_read_prevents_publication(self):
        self.build()
        def changed(*args):
            (self.root/"current.json").write_text(json.dumps({"artifact":"20260920T000000000001Z.sqlite"}))
            return self.read(*args)
        with self.assertRaises(ValueError):self.run_refresh(read=changed)
        self.assertFalse((self.root/self.now.strftime("%Y%m%dT%H%M%S%fZ.sqlite")).exists())

    def test_catchup_is_bounded_and_completed_sessions_only(self):
        self.build()
        more=[(str(date(2023,12,12)+timedelta(days=i)),str(date(2023,12,12)+timedelta(days=i))+"T21:00:00Z") for i in range(25)]
        result=self.run_refresh(sessions=SESSIONS+more+[("2027-01-01","2027-01-01T21:00:00Z")])
        self.assertEqual(result["appended_rows"],20)
        self.assertEqual(result["outcomes"]["catchup_pending"],1)
        self.assertEqual(result["target_session"],more[-1][0])

    def test_new_and_old_negative_ratios_are_retained(self):
        for e in self.inputs["estimates"]:
            p=json.loads(e["payload_json"]);p["epsAvg"]=-1;e["payload_json"]=json.dumps(p)
        self.build();self.run_refresh()
        self.assertLess(self.row("2023-12-06")[5],0)
        self.assertLess(self.row("2023-12-11")[5],0)

    def test_retention_preserves_baseline_current_previous_and_other_artifacts(self):
        self.build()
        first=self.run_refresh()
        second=self.run_refresh(now=self.now+timedelta(hours=1))
        third=self.run_refresh(now=self.now+timedelta(hours=2))
        self.assertFalse((self.root/first["artifact"]).exists())
        self.assertTrue((self.root/second["artifact"]).exists())
        self.assertTrue((self.root/third["artifact"]).exists())
        self.assertTrue((self.root/self.name).exists())
        self.assertTrue((self.root/(first["artifact"]+".receipt.json")).exists())

    def test_unverified_basis_preserves_repair_watermark_then_recovers(self):
        self.build()
        self.inputs["prices"][2].update(version_id="late-repair",close_value="280")
        original=self.inputs["price_basis"];self.inputs["price_basis"]="not_established"
        self.run_refresh()
        self.assertEqual(self.row("2023-12-06")[3],168)
        with open_current(self.root) as (_,db,base,meta):
            self.assertEqual(db.execute("SELECT price_checked_at FROM refresh_members").fetchone()[0],CUTOFF)
        self.inputs["price_basis"]=original
        self.run_refresh(now=self.now+timedelta(hours=1))
        self.assertEqual(self.row("2023-12-06")[5],20)

    def test_expired_window_blocks_only_new_observations(self):
        self.build()
        self.inputs["prices"].append(dict(trade_date="2026-09-18",version_id="new",close_value="140",captured_at=CUTOFF))
        result=self.run_refresh(sessions=SESSIONS+[("2026-09-18","2026-09-18T20:00:00Z")])
        self.assertEqual(self.row("2023-12-06")[5],12)
        self.assertEqual(self.row("2023-12-11")[5],15)
        self.assertEqual(self.row("2026-09-18")[6],"outdated_quarter_window")
        self.assertIsNone(self.row("2026-09-18")[5])

    def test_staging_symlink_cannot_write_outside_root(self):
        self.build();self.run_refresh()
        now=self.now+timedelta(hours=1)
        stage=self.root/now.strftime("%Y%m%dT%H%M%S%fZ.partial.sqlite")
        with TemporaryDirectory() as other:
            target=Path(other)/"untouched"
            stage.symlink_to(target)
            pointer=(self.root/"current.json").read_bytes()
            with self.assertRaises(FileExistsError):self.run_refresh(now=now)
            self.assertFalse(target.exists())
            self.assertEqual((self.root/"current.json").read_bytes(),pointer)

    def test_receipt_failure_before_commit_retains_pointer(self):
        from unittest.mock import patch
        from quant_data.operations import derived_refresh as module
        self.build();pointer=(self.root/"current.json").read_bytes()
        original=module.atomic_json
        def write(path,value):
            if path.name.endswith(".receipt.json"):raise OSError("fixture receipt failure")
            original(path,value)
        with patch.object(module,"atomic_json",write):
            with self.assertRaises(OSError):self.run_refresh()
        self.assertEqual((self.root/"current.json").read_bytes(),pointer)

    def test_status_failure_after_commit_uses_embedded_success(self):
        from unittest.mock import patch
        from quant_data.operations import derived_refresh as module
        from quant_data.company.forward_pe_store import read_refresh_status
        self.build();original=module.atomic_json
        def write(path,value):
            if path.name=="refresh-status.json" and value["state"]!="running":
                raise OSError("fixture telemetry failure")
            original(path,value)
        with patch.object(module,"atomic_json",write):
            result=self.run_refresh()
        self.assertIn("telemetry_issue",result)
        self.assertEqual(read_refresh_status(self.root)["artifact"],result["artifact"])
        self.assertIn(read_refresh_status(self.root)["state"],("complete","complete_with_gaps"))

    def test_window_counts_and_implementation_calendar_provenance(self):
        self.build();self.run_refresh()
        with open_current(self.root) as (_,db,base,meta):
            identities={r[0] for r in base.execute("SELECT window_id FROM windows")}
            identities.update(r[0] for r in db.execute("SELECT window_id FROM windows"))
            self.assertEqual(meta["summary"][0]["windows"],len(identities))
            self.assertEqual(len(meta["implementation_sha256"]),5)
            self.assertEqual(meta["calendar_extension"],[list(x) for x in SESSIONS if x[0]>"2023-12-07"])

    def test_invalid_nested_overlay_is_rejected(self):
        self.build();self.run_refresh()
        with open_current(self.root) as (_,db,base,meta):
            self.assertEqual(meta["baseline_artifact"],self.name)
        target=json.loads((self.root/"current.json").read_text())["artifact"]
        with sqlite3.connect(self.root/target) as db:
            db.execute("UPDATE metadata SET value_json=? WHERE key='baseline_artifact'",(json.dumps(target),))
        with self.assertRaises(ValueError):
            with open_current(self.root):pass

class ScheduleAndPresentationTests(unittest.TestCase):
    def test_slots_follow_toronto_dst_and_weekends(self):
        from quant_data.inspector_fetch_status import _slots
        calendars=["{ OnCalendar=Mon..Fri *-*-* 23:30:00 America/Toronto ; next_elapse=x }",
                   "{ OnCalendar=*-*-* 06:30:00 America/Toronto ; next_elapse=x }"]
        slots,valid=_slots(calendars,date(2026,10,30),days=7)
        self.assertTrue(valid);self.assertEqual(len(slots),12)
        self.assertIn(datetime(2026,10,30,10,30,tzinfo=timezone.utc),slots)
        self.assertIn(datetime(2026,11,1,11,30,tzinfo=timezone.utc),slots)

    def test_loaded_derived_timer_renders_both_calendar_slots(self):
        from quant_data.inspector_fetch_status import read_fetch_status
        units="\n".join(["Id=quant-data-derived-refresh.timer","LoadState=loaded","ActiveState=active",
            "TimersCalendar={ OnCalendar=Mon..Fri *-*-* 23:30:00 America/Toronto ; next_elapse=n/a }",
            "TimersCalendar={ OnCalendar=*-*-* 06:30:00 America/Toronto ; next_elapse=n/a }"])
        snapshot=read_fetch_status("2026-09-21",observed_at=datetime(2026,9,21,16,tzinfo=timezone.utc),
                                   probe=False,unit_output=units,journal_output="")
        events=snapshot["focus_events"]
        self.assertEqual(len(events),2)
        self.assertEqual({e["scheduled_local"] for e in events},{"06:30 EDT","23:30 EDT"})
        self.assertTrue(all("Forward EPS" in e["description"] for e in events))

    def test_status_escapes_errors_and_does_not_hide_baseline(self):
        from quant_data.dashboard.status_page import _derived_section
        page=_derived_section({"state":"failed","issue":"<script>bad</script>","numeric_pe":100,"daily_rows":110,
                               "last_successful_publication":"2026-09-20","schedule":{"enabled":True}})
        self.assertIn("100 ratios / 110",page);self.assertIn("23:30",page)
        self.assertNotIn("<script>",page);self.assertIn("&lt;script&gt;",page)

    def test_partial_publication_records_counts_without_losing_success_evidence(self):
        from quant_data.operations.derived_refresh import completion_exit_code
        from quant_data.operations.fetch_run_history import run_recorded_cli
        from quant_data.operations.fetch_run_summary import record_report
        from contextlib import redirect_stderr
        from io import StringIO
        batch="quant-data-derived-refresh.timer"
        report={"state":"complete_with_gaps","outcomes":{"current":217,"stale_inputs":1965}}
        def operation():
            record_report(batch,report)
            return completion_exit_code(report)
        with TemporaryDirectory() as root,redirect_stderr(StringIO()) as errors:
            code=run_recorded_cli(batch,operation,argv=[],root=Path(root),environment={})
            self.assertEqual(code,2)
            summary=json.loads(next(Path(root).glob("*/*/summary.json")).read_text())
            self.assertEqual(summary["counts"]["partial"],1965)
            self.assertEqual(summary["counts"]["successful"],217)
            self.assertEqual(errors.getvalue(),"")
        self.assertEqual(completion_exit_code({"state":"complete"}),0)

    def test_scheduled_receipt_summarizes_partial_tickers(self):
        from quant_data.operations.fetch_run_summary import summarize_report
        r=summarize_report("quant-data-derived-refresh.timer",{"outcomes":{"current":10,"stale_inputs":2,"waiting_inputs":1}})
        self.assertEqual((r["successful"],r["partial"],r["failed"]),(10,2,1))

if __name__=="__main__":unittest.main()
