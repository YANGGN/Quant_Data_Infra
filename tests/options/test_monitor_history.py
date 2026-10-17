"""Read-only daily adapter and acknowledgment boundaries, on temporary stores."""
from datetime import datetime,timezone
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
import hashlib,json,time
from tests.options.test_theta_compact import temporary_store,capture
from quant_data.options.monitor.history import read_batch,sync_history,completed_day
from quant_data.options.monitor.history_model import DERIVATION_VERSION
from quant_data.options.monitor.site import SiteClient
NOW=datetime(2026,9,26,23,tzinfo=timezone.utc)
class FakeSite:
 def __init__(self):self.cursor=0;self.calls=[];self.fail_before=False;self.fail_after=False
 def daily_state(self):return {"derivation_version":DERIVATION_VERSION,"last_capture_id":self.cursor}
 def daily_publish(self,payload):
  self.calls.append(payload)
  if self.fail_before:raise RuntimeError("offline fixture")
  self.cursor=payload["points"][-1]["source_capture_id"]
  if self.fail_after:raise RuntimeError("lost acknowledgment")
  return {"ok":True,"last_capture_id":self.cursor}
class DailyReaderTests(TestCase):
 def setUp(self):
  self.temp,self.store=temporary_store();self.addCleanup(self.temp.cleanup);self.store.initialize();self.c=capture();self.store.publish(self.c)
 def test_projection_is_read_only_and_bounded(self):
  before=self.store.path.read_bytes();points,metadata=read_batch(self.store.root,0,NOW)
  self.assertEqual(len(points),1);self.assertEqual(points[0]["symbol"],"SPY");self.assertEqual(points[0]["atm_iv_30"],.2)
  self.assertEqual(self.store.path.read_bytes(),before)
  self.assertEqual(metadata["source_high_water"],1)
  self.assertFalse(self.store.path.with_name('options.sqlite-wal').exists())
  self.assertEqual(read_batch(self.store.root,1,NOW)[0],[])
 def test_only_current_revision_is_exported_and_updates_follow_cursor(self):
  site=FakeSite();self.assertEqual(sync_history(self.store.root,site,NOW)["points"],1)
  changed=deepcopy(self.c);changed["semantic_sha256"]="b"*64;self.store.publish(changed)
  points,_=read_batch(self.store.root,0,NOW);self.assertEqual([p["source_capture_id"] for p in points],[2])
  result=sync_history(self.store.root,site,NOW);self.assertEqual(result["last_capture_id"],2);self.assertEqual(result["points"],1)
 def test_failure_does_not_advance_unacknowledged_cursor(self):
  site=FakeSite();site.fail_before=True
  with self.assertRaises(RuntimeError):sync_history(self.store.root,site,NOW)
  self.assertEqual(site.cursor,0);self.assertEqual(len(site.calls),1)
 def test_lost_acknowledgment_resumes_without_republishing(self):
  site=FakeSite();site.fail_after=True
  with self.assertRaises(RuntimeError):sync_history(self.store.root,site,NOW)
  result=sync_history(self.store.root,site,NOW)
  self.assertEqual(result["status"],"up_to_date");self.assertEqual(len(site.calls),1)
 def test_no_network_while_source_lock_is_held(self):
  from quant_data.stores import StoreWriteLock
  store=self.store
  class LockCheckingSite(FakeSite):
   def daily_publish(self,payload):
    with StoreWriteLock(store.path,timeout_seconds=.1):pass
    return super().daily_publish(payload)
  self.assertEqual(sync_history(self.store.root,LockCheckingSite(),NOW)["points"],1)
 def test_completed_session_cutoff_and_regressed_source(self):
  midday=datetime(2026,9,23,17,tzinfo=timezone.utc)
  points,metadata=read_batch(self.store.root,0,midday)
  self.assertEqual(points,[]);self.assertTrue(metadata["waiting_for_close"])
  with self.assertRaisesRegex(ValueError,"regressed"):read_batch(self.store.root,2,NOW)
  with self.assertRaisesRegex(ValueError,"cursor"):read_batch(self.store.root,True,NOW)
 def test_deadline_and_batch_scope(self):
  site=FakeSite();result=sync_history(self.store.root,site,NOW,deadline=time.monotonic())
  self.assertEqual(result["status"],"deadline");self.assertEqual(site.calls,[])
  with self.assertRaisesRegex(ValueError,"batch_cap"):sync_history(self.store.root,site,NOW,max_batches=81)
  self.assertEqual(read_batch(self.store.root,0,NOW,through=0)[0],[])
