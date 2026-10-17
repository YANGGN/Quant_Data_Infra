"""Saved Sharadar summaries and safe dashboard details, using local fixtures."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from quant_data.operations import fetch_run_history as history
from quant_data.operations.fetch_run_summary import record_report
from quant_data.operations.sharadar_run_details import BATCH, capture_details, validate_details
from quant_data.dashboard.fetch_status_page import _sharadar_details
from quant_data.inspector_fetch_status import _Run, _event
from quant_data.inspector_schedules import TIMER_BINDINGS

AT=datetime(2026,9,22,9,tzinfo=timezone.utc)
DETAILS={"outcome":"retry_next_day","http_status":500,"dimension":"MRQ","symbol_count":30,
         "completed_partitions":3,"total_partitions":450,"requests":7,"retries_today":2,
         "next_retry_at":None,"recovery":"next_day"}


class SharadarDetailsTests(unittest.TestCase):
    def test_saved_details_follow_run_into_calendar_card(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/"history"
            def run():
                record_report(BATCH,{"details":DETAILS})
                capture_details(DETAILS)
                return 75
            times=iter((AT+timedelta(seconds=1),AT+timedelta(minutes=31)))
            history.run_recorded_cli(BATCH,run,argv=(),root=root,clock=lambda:next(times),environment={})
            records=history.read_fetch_run_history(root,start=AT,end=AT+timedelta(days=1),
                observed_at=AT+timedelta(hours=2))
            self.assertEqual(records["invalid"],0)
            row=records["records"][0]
            self.assertEqual(row["details"],DETAILS)
            self.assertEqual(row["counts"],{"unit":"steps","successful":3,"failed":1,
                "partial":0,"skipped":0,"unattempted":446})
            run=_Run(BATCH.replace(".timer",".service"),AT+timedelta(seconds=1),
                AT+timedelta(minutes=31),"failed","","saved_run",75,row["run_id"],row["counts"],row["details"])
            binding=next(b for b in TIMER_BINDINGS if b.unit==BATCH)
            event=_event(binding,AT,[run],AT+timedelta(hours=2))
            self.assertEqual(event["status"],"partial")
            html=_sharadar_details(event)
            for expected in ("HTTP 500","restated quarterly","30 stocks","3 of 450",
                    "next scheduled day","2 of 2"):
                self.assertIn(expected,html)

    def test_rejects_payloads_unknown_fields_and_foreign_batch(self):
        for changes in ({"http_status":True},{"dimension":"<script>"},
                {"requests":501},{"recovery":"https://secret"},{"payload":"private"}):
            with self.assertRaises(ValueError):
                validate_details({**DETAILS,**changes})
        with TemporaryDirectory() as temp:
            record={"run_id":"a"*32,"batch_id":"quant-data-market-close.timer",
                "started_at":AT.isoformat(),"finished_at":(AT+timedelta(seconds=1)).isoformat(),
                "exit_code":75,"outcome":"failed"}
            with self.assertRaises(ValueError):
                history.save_run_summary(Path(temp)/"summary.json",record,
                    {"unit":"steps","successful":3,"failed":1,"partial":0,"skipped":0,"unattempted":0},
                    recorded_at=(AT+timedelta(seconds=2)).isoformat(),details=DETAILS)

    def test_missing_and_budget_details_do_not_claim_success(self):
        self.assertEqual(_sharadar_details({}),"")
        html=_sharadar_details({"sharadar":{**DETAILS,"http_status":None,"outcome":"invocation_budget","recovery":"budget"}})
        self.assertNotIn("refresh completed",html)
        self.assertIn("run limit",html)


if __name__=="__main__":
    unittest.main()
