"""Offline one-day live pause, automatic expiry and bounded priority controls."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import fcntl,json,tempfile,time,unittest
from quant_data.options.monitor.admission import ThetaAdmission
from quant_data.options.monitor.job import MonitorJob

NOW=datetime(2026,10,1,14,tzinfo=timezone.utc)
RUN="a"*24

class LivePauseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="options-live-pause-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.gate=ThetaAdmission(self.root)

    def job(self,now=NOW):
        # No config, credentials, operational stores, provider or Site are used.
        job=object.__new__(MonitorJob);job.root=self.root;job.clock=lambda:now
        job.store=Mock();job.store.path=self.gate.directory/"monitor.sqlite"
        job.site_factory=Mock();job.transport_factory=Mock()
        job.policy={"max_seconds_per_cycle":240}
        job._run_locked=Mock(return_value={"status":"fixture_live_path"})
        return job

    def test_pause_expires_at_eastern_midnight_and_only_boosts_matching_run(self):
        value=self.gate.pause_today(NOW,RUN)
        self.assertEqual(value["until"],"2026-10-02T00:00:00-04:00")
        self.assertEqual(self.gate.backfill_capacity(NOW,RUN),4)
        self.assertEqual(self.gate.backfill_capacity(NOW,"b"*24),1)
        self.assertEqual(self.gate.backfill_capacity(NOW),1)
        self.assertIsNotNone(self.gate.pause_status(datetime(2026,10,2,3,59,59,tzinfo=timezone.utc)))
        self.assertIsNone(self.gate.pause_status(datetime(2026,10,2,4,tzinfo=timezone.utc)))
        self.assertEqual(self.gate.backfill_capacity(NOW+timedelta(days=1),RUN),1)

    def test_pause_is_immutable_idempotent_and_preserves_conflicting_authority(self):
        value=self.gate.pause_today(NOW,RUN)
        path=self.gate.directory/"live-pause-2026-10-01.json"
        original=path.read_bytes();stamp=path.stat().st_mtime_ns
        self.assertEqual(self.gate.pause_today(NOW+timedelta(minutes=1),RUN),value)
        self.assertEqual(path.read_bytes(),original);self.assertEqual(path.stat().st_mtime_ns,stamp)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with self.assertRaisesRegex(ValueError,"pause_conflict"):
            self.gate.pause_today(NOW,"b"*24)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(list(self.gate.directory.glob("*.tmp")),[])

    def test_pause_does_not_interrupt_an_active_live_owner(self):
        with self.gate._open("run.lock") as owner:
            fcntl.flock(owner,fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError,"monitor_cycle_running"):
                self.gate.pause_today(NOW,RUN)
        self.assertIsNone(self.gate.pause_status(NOW))

    def test_paused_cycle_does_not_initialize_store_or_use_site_or_provider(self):
        self.gate.pause_today(NOW,RUN);job=self.job()
        with patch("quant_data.options.monitor.job.connection") as connection:
            result=job.run()
        self.assertEqual(result,{"status":"paused","session":"2026-10-01","requests":0,
            "reason":"backfill_priority","resume_at":"2026-10-02T00:00:00-04:00"})
        job.store.initialize.assert_not_called();job.site_factory.assert_not_called()
        job.transport_factory.assert_not_called();job._run_locked.assert_not_called()
        connection.assert_not_called()

    def test_next_day_automatically_reenters_normal_live_path(self):
        self.gate.pause_today(NOW,RUN);job=self.job(NOW+timedelta(days=1))
        with patch("quant_data.options.monitor.job.connection",return_value={}):
            self.assertEqual(job.run(),{"status":"fixture_live_path"})
        job._run_locked.assert_called_once();job.store.initialize.assert_called_once()
        self.assertEqual(self.gate.backfill_capacity(NOW+timedelta(days=1),RUN),1)

    def test_pause_arriving_after_first_check_is_rechecked_under_live_owner(self):
        self.gate.directory.mkdir(parents=True)
        job=self.job();job.store.initialize.side_effect=lambda:self.gate.pause_today(NOW,RUN)
        with patch("quant_data.options.monitor.job.connection",return_value={}):
            self.assertEqual(job.run()["status"],"paused")
        job._run_locked.assert_not_called();job.transport_factory.preflight.assert_not_called()

    def test_invalid_current_pause_fails_before_any_external_work(self):
        self.gate.pause_today(NOW,RUN)
        path=self.gate.directory/"live-pause-2026-10-01.json"
        value=json.loads(path.read_text());value["until"]="2026-10-03T00:00:00-04:00"
        path.write_text(json.dumps(value))
        job=self.job()
        with self.assertRaisesRegex(ValueError,"pause_invalid"):job.run()
        job.store.initialize.assert_not_called();job.site_factory.assert_not_called()

    def test_daylight_saving_boundary_still_uses_next_local_midnight(self):
        when=datetime.fromisoformat("2026-11-01T00:30:00-04:00")
        self.assertEqual(self.gate.pause_today(when,RUN)["until"],"2026-11-02T00:00:00-05:00")
        with self.assertRaisesRegex(ValueError,"monitor_clock"):
            self.gate.pause_today(datetime(2026,10,1),RUN)

if __name__=="__main__":unittest.main()
