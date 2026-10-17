from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import unittest
from quant_data.operations.collection_sharadar_definitions import run_definitions,definition_unit
from quant_data.operations.collection_transport import request_route
from quant_data.operations.collection_plan import ProviderBudget
from quant_data.operations.collection_queue import QueueResponse
from quant_data.fingerprint import mutation_fingerprint
from quant_data.errors import ConflictError
from tests.company import test_sharadar_repository as identity
from tests.company import test_sharadar_definitions as definitions

class DefinitionCollectorTests(unittest.TestCase):
    def setUp(self):
        self.f=identity.SharadarRepositoryTests("runTest");self.f.setUp();self.addCleanup(self.f.tearDown)
        self.root=Path(self.f.tmp.name)/"definition-private";self.now=datetime(2026,9,9,5,tzinfo=timezone.utc)
        self.elapsed=0;self.calls=[];self.repeat=False
    def sleep(self,n):self.elapsed+=n;self.now+=timedelta(seconds=n)
    def fetch(self,*,unit,**kwargs):
        self.calls.append(unit)
        if unit.endpoint.endswith("/metadata"):body=definitions.metadata().body
        else:
            params=dict(unit.parameters);cursor="next" if "qopts.cursor_id" not in params or self.repeat else None
            rows=None if "qopts.cursor_id" not in params else []
            body=definitions.page(at=self.now.isoformat(),params=params,cursor=cursor,rows=rows).body
        response=QueueResponse(200,body,self.now.isoformat());self.sleep(1);return response
    def run_worker(self,cap=3):
        return run_definitions(root=self.root,stores=self.f.stores,registry=self.f.registry,selection=self.f.selection,
            mode="historical_backfill",observation_window="fixture-definitions",
            budget=ProviderBudget("sharadar",cap,32*1024*1024,600,1000),fetch=self.fetch,
            utcnow=lambda:self.now,monotonic=lambda:self.elapsed,sleeper=self.sleep)
    def test_complete_cursor_original_replay_and_fixed_routes(self):
        self.assertEqual(self.run_worker()["outcome"],"succeeded");self.assertEqual(len(self.calls),3)
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.run_worker()["requests"],0);self.assertEqual(len(self.calls),3)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        for unit in self.calls:
            route=request_route(unit)
            self.assertEqual(route.host,"data.nasdaq.com")
            self.assertTrue(route.path.startswith("/api/v3/datatables/SHARADAR/INDICATORS"))
    def test_single_request_resume_does_not_publish_partial_definitions(self):
        before=mutation_fingerprint(self.f.stores)
        self.assertEqual(self.run_worker(1)["outcome"],"invocation_budget")
        self.assertEqual(self.run_worker(1)["outcome"],"invocation_budget")
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
        self.assertEqual(self.run_worker(1)["outcome"],"succeeded")
        self.assertEqual(len(self.calls),3)
    def test_repeated_cursor_stays_private_and_is_not_retried(self):
        self.repeat=True;before=mutation_fingerprint(self.f.stores)
        with self.assertRaises(ConflictError):self.run_worker()
        with self.assertRaises(ConflictError):self.run_worker()
        self.assertEqual(len(self.calls),3);self.assertEqual(before,mutation_fingerprint(self.f.stores))
    def test_expired_retained_inspection_does_not_publish(self):
        from quant_data.operations import collection_sharadar_definitions as implementation
        with patch.object(implementation.SharadarDefinitionPublisher,"publish",side_effect=ConflictError("fixture-defer")):
            with self.assertRaises(ConflictError):self.run_worker()
        before=mutation_fingerprint(self.f.stores);original=implementation.inspect_definitions
        def slow(**kwargs):
            progress=original(**kwargs);self.elapsed+=601;return progress
        with patch.object(implementation,"inspect_definitions",side_effect=slow):
            result=self.run_worker()
        self.assertEqual(result["outcome"],"invocation_budget");self.assertEqual(result["requests"],0)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))



    def test_expiring_during_publisher_validation_cannot_start_canonical_writes(self):
        from quant_data.company import sharadar_definition_repository as publisher_module
        from quant_data.operations import collection_sharadar_definitions as controller
        with patch.object(controller.SharadarDefinitionPublisher,"publish",side_effect=ConflictError("fixture-defer")):
            with self.assertRaises(ConflictError):self.run_worker()
        before=mutation_fingerprint(self.f.stores);original=publisher_module.prepare_definition_snapshot
        def slow(pages):
            result=original(pages);self.elapsed+=601;return result
        with patch.object(publisher_module,"prepare_definition_snapshot",side_effect=slow):
            result=self.run_worker()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual(result["requests"],0)
        self.assertEqual(before,mutation_fingerprint(self.f.stores))
