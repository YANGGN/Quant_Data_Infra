import json, tempfile, unittest, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from quant_data.operations import collection_provider_policy as policy
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.market.collection_bindings import load_bindings

AT=datetime(2026,9,9,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parents[2]

class FmpSharedAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/tmp");self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"shared"
        self.elapsed=0;self.at=AT;self.calls=[]
        self.policy=policy.FmpAllowance(5,2,1000,AT.isoformat(),1,"fixture/reviewed-account-allocation.json")
    def sleep(self,n):self.elapsed+=n;self.at+=timedelta(seconds=n)
    def gate(self,chosen=None):
        return policy.FmpAccountGate(self.root,chosen or self.policy,clock=lambda:self.at,
            monotonic=lambda:self.elapsed,sleeper=self.sleep)
    def invoke(self,priority="backfill",status=200,gate=None):
        def request(remaining):
            self.calls.append((self.at,remaining));return SimpleNamespace(status=status)
        return (gate or self.gate()).invoke(request,request_identity="1"*64,
            priority=priority,deadline=self.elapsed+60)
    def state(self):return json.loads((self.root/"allowance.json").read_bytes())
    def test_shared_allowance_preserves_current_maintenance_capacity(self):
        self.invoke();self.invoke(gate=self.gate())
        with self.assertRaises(ResourceLimitError):self.invoke()
        self.assertEqual(len(self.calls),2)
        self.invoke("maintenance");self.invoke("maintenance")
        with self.assertRaises(ResourceLimitError):self.invoke("maintenance")
        self.assertEqual(self.state()["usage"]["2026-09-09"],5)
        self.assertEqual(len(self.calls),4)
        self.assertTrue(all((b[0]-a[0]).total_seconds()>=1 for a,b in zip(self.calls,self.calls[1:])))
    def test_unknown_attempt_blocks_every_client_without_refund_or_retry(self):
        gate=self.gate()
        def crash(remaining):self.calls.append("failed");raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            gate.invoke(crash,request_identity="2"*64,priority="maintenance",deadline=60)
        with self.assertRaises(ConflictError):self.invoke("maintenance")
        self.assertEqual(self.calls,["failed"]);self.assertEqual(self.state()["usage"]["2026-09-09"],2)
        self.assertIsNotNone(self.state()["pending"])
    def test_systemic_response_stops_account_and_policy_cannot_reset_usage(self):
        self.invoke("maintenance",429)
        with self.assertRaises(ConflictError):self.invoke("maintenance")
        self.assertEqual(self.state()["stopped"]["status"],429)
        with self.assertRaises(ConflictError):
            self.invoke(gate=self.gate(replace(self.policy,daily_ceiling=6)))
        self.assertEqual(len(self.calls),1)
    def test_pacing_across_midnight_charges_actual_reservation_day(self):
        self.at=AT.replace(hour=23,minute=59,second=59,microsecond=500000)
        chosen=replace(self.policy,initial_day_charged=0)
        self.invoke("maintenance",gate=self.gate(chosen))
        self.sleep(0.1)
        self.invoke("maintenance",gate=self.gate(chosen))
        self.assertEqual(self.state()["usage"],{"2026-09-09":1,"2026-09-10":1})
    def test_expiry_during_reservation_does_not_dispatch(self):
        original=policy.atomic
        def delayed(path,value,**kwargs):
            result=original(path,value,**kwargs)
            if path.name=="allowance.json" and isinstance(value,bytes) and b'"pending":{' in value:
                self.elapsed=61
            return result
        with patch.object(policy,"atomic",side_effect=delayed):
            with self.assertRaises(ResourceLimitError):self.invoke("maintenance")
        self.assertEqual(self.calls,[])
        self.assertEqual(self.state()["stopped"]["status"],"not_dispatched")
        self.assertEqual(self.state()["usage"]["2026-09-09"],2)
    def test_prepared_and_legacy_configuration_have_no_account_ledger(self):
        base=Path(self.temp.name)/"project";(base/"config").mkdir(parents=True)
        raw=json.loads((ROOT/"config/collection_bindings.json").read_bytes())
        raw["provider_allowances"]["fmp"]={"mode":"prepared","policy":None}
        for binding in raw["bindings"]:
            if binding["provider"]=="fmp":binding["mode"]="prepared"
        path=base/"config/collection_bindings.json";path.write_text(json.dumps(raw))
        with patch.object(policy,"PROJECT_ROOT",base):
            self.assertIsNone(policy.host_allowance())
            raw.pop("provider_allowances");raw["version"]="1.0.0";path.write_text(json.dumps(raw))
            self.assertIsNone(policy.host_allowance())
            self.assertEqual(len(load_bindings(path)),10)
        self.assertFalse((base/"data").exists())
    def test_selected_activation_requires_shared_allowance_and_identity_excludes_secrets(self):
        base=Path(self.temp.name)/"project";(base/"config").mkdir(parents=True)
        raw=json.loads((ROOT/"config/collection_bindings.json").read_bytes())
        raw["provider_allowances"]["fmp"]={"mode":"prepared","policy":None}
        for binding in raw["bindings"]:
            if binding["provider"]=="fmp":binding["mode"]="prepared"
        next(b for b in raw["bindings"] if b["id"]=="fmp_statements")["mode"]="active"
        (base/"config/collection_bindings.json").write_text(json.dumps(raw))
        with patch.object(policy,"PROJECT_ROOT",base):
            with self.assertRaises(ConflictError):policy.host_allowance()
        with patch.object(policy,"host_allowance",return_value=self.gate()):
            with self.assertRaises(ValidationError):
                policy.invoke_host_fmp(lambda _:SimpleNamespace(status=200),
                    request_material={"parameters":{"apikey":"synthetic"}},
                    priority="maintenance",timeout_seconds=30)
        self.assertFalse(self.root.exists())



class HostAccountSlotWaitTests(unittest.TestCase):
    def test_predispatch_contention_waits_but_operation_failure_never_repeats(self):
        from unittest.mock import patch,Mock
        from quant_data.operations.collection_provider_policy import invoke_host_fmp
        from quant_data.errors import ConflictError
        result=object();operation=Mock(return_value=result);gate=Mock()
        def after_wait(op,**kw):return op(3)
        gate.invoke.side_effect=[ConflictError("Equibles backfill is already running"),result]
        with patch("quant_data.operations.collection_provider_policy.host_allowance",return_value=gate),patch("quant_data.operations.collection_provider_policy.time.sleep"):
            self.assertIs(invoke_host_fmp(operation,request_material={'symbol':'AAPL'},priority='backfill',timeout_seconds=5),result)
        self.assertEqual(gate.invoke.call_count,2)
        gate=Mock()
        def dispatched(op,**kw):return op(3)
        gate.invoke.side_effect=dispatched
        operation=Mock(side_effect=ConflictError("Equibles backfill is already running"))
        with patch("quant_data.operations.collection_provider_policy.host_allowance",return_value=gate):
            with self.assertRaises(ConflictError):invoke_host_fmp(operation,request_material={'symbol':'AAPL'},priority='backfill',timeout_seconds=5)
        self.assertEqual(operation.call_count,1);self.assertEqual(gate.invoke.call_count,1)
    def test_slot_wait_keeps_one_deadline_and_dispatches_once(self):
        from unittest.mock import patch,Mock
        from quant_data.operations.collection_provider_policy import invoke_host_fmp
        from quant_data.errors import ConflictError
        deadlines=[];calls=[]
        class Gate:
            def invoke(self,op,**kw):
                deadlines.append(kw['deadline'])
                if len(deadlines)<3:raise ConflictError('FMP allowance clock moved backward')
                return op(2)
        with patch('quant_data.operations.collection_provider_policy.host_allowance',return_value=Gate()),patch('quant_data.operations.collection_provider_policy.time.sleep'):
            result=invoke_host_fmp(lambda remaining:calls.append(remaining) or 'success',request_material={'symbol':'AAPL'},priority='backfill',timeout_seconds=5)
        self.assertEqual(result,'success');self.assertEqual(calls,[2]);self.assertEqual(len(set(deadlines)),1)

    def test_account_contention_deadline_does_not_dispatch(self):
        from unittest.mock import patch,Mock
        from quant_data.operations.collection_provider_policy import invoke_host_fmp
        from quant_data.errors import ConflictError,ResourceLimitError
        gate=Mock();gate.invoke.side_effect=ConflictError('Equibles backfill is already running');operation=Mock()
        with patch('quant_data.operations.collection_provider_policy.host_allowance',return_value=gate),patch('quant_data.operations.collection_provider_policy.time.monotonic',side_effect=[0,6]):
            with self.assertRaises(ResourceLimitError):invoke_host_fmp(operation,request_material={'symbol':'AAPL'},priority='backfill',timeout_seconds=5)
        operation.assert_not_called();self.assertEqual(gate.invoke.call_count,1)
