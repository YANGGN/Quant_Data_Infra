"""Main-runtime regressions for deadline propagation and retained-only resume."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from quant_data.fingerprint import mutation_fingerprint
from quant_data.errors import ResourceLimitError,ConflictError
from quant_data.stores import quiet_immutable_read_connection
from quant_data.operations.collection_plan import build_plan,ProviderBudget
from quant_data.operations.collection_queue import QueueResponse
from quant_data.operations.collection_runtime import run_selected_plan
from quant_data.operations.collection_prices import price_units
from tests.operations import test_collection_fmp as fmpfixtures
from tests.operations import test_collection_prices as pricefixtures


class SelectedPublicationDeadlineTests(unittest.TestCase):
    def setUp(self):
        blocked=patch("socket.socket.connect",side_effect=AssertionError("Offline fixture attempted network"))
        blocked.start();self.addCleanup(blocked.stop)

    def private_root(self):
        temporary=tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)/"queue"

    def company(self,family,phase):
        from quant_data.operations import collection_fmp as selected
        from quant_data.company import fmp_research,fmp_market_data,fmp_analyst_history
        f=fmpfixtures.SelectedFmpTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        is_statement=family=="statement"
        selection,work=f.work({"statement":"fmp_statements","actions":"dividends_splits","analyst":"fmp_analyst_estimates"}[family])
        unit=next(u for u in work.units if u.endpoint=={"statement":"income-statement","actions":"dividends","analyst":"analyst-estimates"}[family]
            and (not is_statement or dict(u.parameters)["period"]=="annual"))
        row={"symbol":unit.subject,"date":"2025-09-30"}
        row.update({"statement":{"revenue":100},"actions":{"dividend":0.25},"analyst":{"estimatedRevenueAvg":100}}[family])
        body=json.dumps([row]).encode();elapsed=[0];calls=[];root=self.private_root()
        plan=build_plan(selections=(selection,),units=(unit,),
            budgets=(ProviderBudget("fmp",1,16*1024*1024,60,1000),),created_at=fmpfixtures.CUT)
        def fetch(**kw):
            calls.append(kw["unit"].subject)
            return QueueResponse(200,body,fmpfixtures.CAPTURE)
        def run():
            return run_selected_plan(root=root,stores=f.f.stores,registry=f.f.registry,selections=(selection,),
                plan=plan,provider="fmp",fetch=fetch,utcnow=lambda:datetime(2026,9,9,5,tzinfo=timezone.utc),
                monotonic=lambda:elapsed[0],sleeper=lambda _:None)
        source={"statement":fmp_research,"actions":fmp_market_data,"analyst":fmp_analyst_history}[family]
        if phase=="parse":
            target=selected;name="parse_research_response" if is_statement else "parse_fmp_company_response"
        elif phase=="revalidate":
            target=source;name="_validate_prepared"
        elif phase in ("writer","real_resource"):
            from quant_data import ingestion
            target=ingestion;name="_run_writer_with_transaction_guard"
        elif phase=="queue_setup":
            from quant_data.operations import collection_queue
            target=collection_queue;name="validate_budget"
        else:
            target=source;name="acquire_write_session"
        original=getattr(target,name)
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);elapsed[0]=61;return result
        @contextmanager
        def delayed_lock(*args,**kwargs):
            with original(*args,**kwargs) as locks:
                elapsed[0]=61
                yield locks
        def delayed_writer(*args,**kwargs):
            elapsed[0]=61
            return original(*args,**kwargs)
        def rejected_writer(connection,writer,run_id):
            elapsed[0]=61
            def reject(*args):
                raise ResourceLimitError("Synthetic genuine resource validation failure")
            return original(connection,reject,run_id)
        before=mutation_fingerprint(f.f.stores)
        if phase=="real_resource":
            with patch.object(target,name,rejected_writer),self.assertRaises(ResourceLimitError):
                run()
            with quiet_immutable_read_connection(f.f.stores,"company") as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM ingestion_run_failures").fetchone()[0],1)
            self.assertNotEqual(before,mutation_fingerprint(f.f.stores))
            with self.assertRaisesRegex(ConflictError,"identity was already used"):
                run()
            self.assertEqual(len(calls),1)
            return
        replacement=delayed_lock if phase=="lock" else delayed_writer if phase=="writer" else delayed
        with patch.object(target,name,replacement):
            result=run()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual((result["requests"],result["published"]),(0 if phase=="queue_setup" else 1,0))
        self.assertEqual(before,mutation_fingerprint(f.f.stores))
        if phase=="queue_setup":
            self.assertEqual(calls,[])
            return
        self.assertEqual(len(calls),1)
        self.assertEqual(list((root/"publications").glob("*.json")),[])
        resumed=run()
        self.assertEqual((resumed["outcome"],resumed["requests"],resumed["published"]),("complete",0,1))
        self.assertEqual(len(calls),1)
        before=mutation_fingerprint(f.f.stores)
        self.assertEqual(run()["requests"],0)
        self.assertEqual(before,mutation_fingerprint(f.f.stores))

    def prices(self,phase,mode):
        from quant_data.market import stage12_incremental as source
        f=pricefixtures.SelectedPriceTests("runTest");f.setUp();self.addCleanup(f.doCleanups)
        root=self.private_root();elapsed=[0];calls=[]
        requests=price_units(f.f.stores,f.scope,windows=(f.window,),mode=mode,
            observation_window=pricefixtures.CUT,cutoff=pricefixtures.CUT)
        plan=build_plan(selections=(f.scope,),units=tuple(u for u,_ in requests),
            budgets=(ProviderBudget("fmp",2,1024*1024,60,1000),),created_at=pricefixtures.CUT)
        def fetch(**kw):
            calls.append(kw["unit"].subject)
            return QueueResponse(200,f.body,pricefixtures.CUT)
        def run():
            return run_selected_plan(root=root,stores=f.f.stores,registry=f.f.registry,selections=(f.scope,),
                plan=plan,provider="fmp",fetch=fetch,price_collector=f.collector,price_requests=requests,
                utcnow=lambda:datetime(2026,9,9,3,tzinfo=timezone.utc),
                monotonic=lambda:elapsed[0],sleeper=lambda _:None)
        if phase=="lock":
            target=source;name="StoreWriteLock"
        else:
            target=f.collector;name="prepare" if phase=="parse" else "_before_transaction"
        original=getattr(target,name)
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);elapsed[0]=61;return result
        @contextmanager
        def delayed_lock(*args,**kwargs):
            with original(*args,**kwargs) as locks:
                elapsed[0]=61
                yield locks
        before=mutation_fingerprint(f.f.stores)
        with patch.object(target,name,delayed_lock if phase=="lock" else delayed):
            result=run()
        self.assertEqual(result["outcome"],"invocation_budget")
        self.assertEqual((result["requests"],result["published"]),(1,0))
        self.assertEqual(before,mutation_fingerprint(f.f.stores))
        self.assertEqual(calls,["AAPL"])
        self.assertEqual(list((root/"publications").glob("*.json")),[])
        resumed=run()
        self.assertEqual(resumed["outcome"],"complete")
        self.assertEqual(resumed["requests"],0)
        self.assertEqual(calls,["AAPL"])
        before=mutation_fingerprint(f.f.stores)
        self.assertEqual(run()["requests"],0)
        self.assertEqual(before,mutation_fingerprint(f.f.stores))

    def test_main_statement_parse_expiry_reuses_original_response(self):self.company("statement","parse")
    def test_main_statement_revalidation_expiry_reuses_original_response(self):self.company("statement","revalidate")
    def test_main_statement_physical_lock_expiry_reuses_original_response(self):self.company("statement","lock")
    def test_main_actions_parse_expiry_reuses_original_response(self):self.company("actions","parse")
    def test_main_actions_revalidation_expiry_reuses_original_response(self):self.company("actions","revalidate")
    def test_main_actions_physical_lock_expiry_reuses_original_response(self):self.company("actions","lock")
    def test_queue_setup_cannot_reset_the_main_runtime_deadline(self):self.company("statement","queue_setup")
    def test_price_backfill_parse_expiry_reuses_original_response(self):self.prices("parse","historical_backfill")
    def test_price_backfill_lock_expiry_reuses_original_response(self):self.prices("lock","historical_backfill")
    def test_price_backfill_pretransaction_expiry_reuses_original_response(self):self.prices("transaction","historical_backfill")
    def test_price_sentinel_parse_expiry_reuses_original_response(self):self.prices("parse","incremental")
    def test_price_sentinel_lock_expiry_reuses_original_response(self):self.prices("lock","incremental")
    def test_price_sentinel_pretransaction_expiry_reuses_original_response(self):self.prices("transaction","incremental")


    def test_main_statement_writer_expiry_does_not_consume_run_identity(self):self.company("statement","writer")
    def test_main_actions_writer_expiry_does_not_consume_run_identity(self):self.company("actions","writer")
    def test_main_analyst_writer_expiry_does_not_consume_run_identity(self):self.company("analyst","writer")
    def test_late_clock_does_not_suppress_genuine_resource_failure(self):self.company("statement","real_resource")
