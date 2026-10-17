from datetime import datetime,timezone
from dataclasses import replace
from pathlib import Path
import unittest
from quant_data.operations.collection_runtime import run_selected_plan
from quant_data.operations.collection_plan import build_plan,ProviderBudget
from quant_data.operations.collection_prices import price_units
from quant_data.operations.collection_queue import QueueResponse
from quant_data.fingerprint import mutation_fingerprint
from quant_data.errors import ConflictError
from tests.operations import test_collection_fmp as fmpfixtures
from tests.operations import test_collection_sec as secfixtures
from tests.operations import test_collection_prices as pricefixtures

class SelectedRuntimeTests(unittest.TestCase):
    def test_fmp_manifest_publishes_its_source_and_reuses_without_new_get(self):
        f=fmpfixtures.SelectedFmpTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        selection,work=f.work("fmp_statements")
        unit=work.units[0];root=Path(f.f.temp.name)/"fmp-runtime"
        plan=build_plan(selections=(selection,),units=(unit,),budgets=(ProviderBudget("fmp",2,16*1024*1024,600,1000),),created_at=fmpfixtures.CUT)
        calls=[]
        def fetch(**kw):
            calls.append(kw["unit"]);return QueueResponse(200,b"[]",fmpfixtures.CAPTURE)
        def run(p=plan):
            return run_selected_plan(root=root,stores=f.f.stores,registry=f.f.registry,selections=(selection,),plan=p,
                provider="fmp",fetch=fetch,utcnow=lambda:datetime(2026,9,9,5,tzinfo=timezone.utc),sleeper=lambda _:None)
        self.assertEqual(run()["published"],1)
        before=mutation_fingerprint(f.f.stores)
        self.assertEqual(run()["requests"],0);self.assertEqual(len(calls),1)
        self.assertEqual(before,mutation_fingerprint(f.f.stores))
        with self.assertRaises(ConflictError):run(replace(plan,created_at="2027-01-01T00:00:00Z"))
    def test_sec_runtime_retains_both_responses_and_publishes_one_shared_issuer(self):
        f=secfixtures.SelectedSecTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        def fetch(**kw):
            return replace(f.fetch(**kw),captured_at=secfixtures.CUT)
        def run():
            return run_selected_plan(root=f.root,stores=f.f.stores,registry=f.f.registry,selections=(f.selection,),
                plan=f.plan(),provider="sec",fetch=fetch,utcnow=lambda:datetime(2026,9,9,4,tzinfo=timezone.utc),sleeper=lambda _:None)
        result=run()
        self.assertEqual(result["requests"],2);self.assertEqual(len(result["publications"]),1)
        self.assertEqual(run()["requests"],0)
    def test_current_prices_check_empty_aapl_before_other_symbols_and_replay_without_get(self):
        f=pricefixtures.SelectedPriceTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        import tempfile
        state=tempfile.TemporaryDirectory(dir="/tmp");self.addCleanup(state.cleanup)
        state_root=Path(state.name)/"fmp-runtime"
        windows=(f.window,replace(f.window,symbol="BRK-B"))
        requests=price_units(f.f.stores,f.scope,windows=windows,mode="incremental",
            observation_window=pricefixtures.CUT,cutoff=pricefixtures.CUT)
        plan=build_plan(selections=(f.scope,),units=tuple(u for u,_ in requests),
            budgets=(ProviderBudget("fmp",2,1024*1024,600,1000),),created_at=pricefixtures.CUT)
        calls=[]
        def fetch(**kw):
            calls.append(kw["unit"].subject);return QueueResponse(200,b"[]",pricefixtures.CUT)
        def run():
            return run_selected_plan(root=state_root,stores=f.f.stores,registry=f.f.registry,
                selections=(f.scope,),plan=plan,provider="fmp",fetch=fetch,price_collector=f.collector,price_requests=requests,
                utcnow=lambda:datetime(2026,9,9,3,tzinfo=timezone.utc),sleeper=lambda _:None)
        self.assertEqual(run()["outcome"],"price_session_not_established")
        self.assertEqual(run()["requests"],0)
        self.assertEqual(calls,["AAPL"])


    def test_expired_sec_acquisition_does_not_publish_after_its_run_deadline(self):
        f=secfixtures.SelectedSecTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        elapsed=[0];before=mutation_fingerprint(f.f.stores)
        def fetch(**kw):
            response=replace(f.fetch(**kw),captured_at=secfixtures.CUT)
            elapsed[0]+=601
            return response
        result=run_selected_plan(root=f.root,stores=f.f.stores,registry=f.f.registry,selections=(f.selection,),
            plan=f.plan(),provider="sec",fetch=fetch,utcnow=lambda:datetime(2026,9,9,4,tzinfo=timezone.utc),
            monotonic=lambda:elapsed[0],sleeper=lambda _:None)
        self.assertEqual(result["publication_status"],"deferred_at_run_deadline")
        self.assertEqual(result["requests"],1)
        self.assertEqual(before,mutation_fingerprint(f.f.stores))


    def test_prices_validate_exact_bounds_and_keep_blocked_sentinel_before_any_get(self):
        from quant_data.operations.collection_plan import RetainedResponse
        import tempfile,hashlib
        f=pricefixtures.SelectedPriceTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        state=tempfile.TemporaryDirectory(dir="/tmp");self.addCleanup(state.cleanup)
        requests=price_units(f.f.stores,f.scope,windows=(f.window,),mode="incremental",
            observation_window=pricefixtures.CUT,cutoff=pricefixtures.CUT)
        original=requests[0][0]
        common=dict(selections=(f.scope,),budgets=(ProviderBudget("fmp",2,1024*1024,600,1000),),created_at=pricefixtures.CUT)
        calls=[]
        def fetch(**kw):calls.append(kw);return QueueResponse(200,b"[]",pricefixtures.CUT)
        def run(plan):
            return run_selected_plan(root=Path(state.name)/"queue",stores=f.f.stores,registry=f.f.registry,
                selections=(f.scope,),plan=plan,provider="fmp",fetch=fetch,price_collector=f.collector,price_requests=requests,
                utcnow=lambda:datetime(2026,9,9,3,tzinfo=timezone.utc),sleeper=lambda _:None)
        changed=replace(original,max_response_bytes=original.max_response_bytes*2)
        with self.assertRaises(ConflictError):run(build_plan(units=(changed,),**common))
        self.assertEqual(calls,[])
        retained=RetainedResponse(original.request_id,pricefixtures.CUT,hashlib.sha256(b"[]").hexdigest(),
            "fixture",True,"complete_request",False,True,"published-fixture",pricefixtures.CUT)
        plan=build_plan(units=(original,),retained=(retained,),**common)
        self.assertEqual(len(plan.blocked_units),1)
        self.assertEqual(run(plan)["coverage"],"blocked")
        self.assertEqual(calls,[])
