"""Offline SDK session sharing; no real credentials, sockets or provider calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch,MagicMock
import tempfile
from quant_data.options.monitor.transport import MonitorThetaTransport,SessionSetupFailure
from quant_data.options.transport import Budget

class SharedSessionTests(TestCase):
    def test_concurrent_clients_share_one_auth_and_keep_separate_receipts(self):
        logins=[]
        class Client:
            def __init__(self,*,api_key,existing_authorized_client,**kwargs):
                if existing_authorized_client is None:
                    logins.append(api_key);self.auth_token=object()
                else:
                    assert api_key is None
                    self.auth_token=existing_authorized_client.auth_token
        with tempfile.TemporaryDirectory(prefix="monitor-auth-") as tmp:
            with patch("thetadata.ThetaClient",Client),patch("quant_data.options.monitor.transport.read_project_credential",return_value="fixture") as creds,patch("grpc.secure_channel",side_effect=lambda *a,**kw:MagicMock()),patch("thetadata._proto.v3grpc.endpoints_pb2_grpc.BetaThetaTerminalStub",return_value=MagicMock()):
                factory=MonitorThetaTransport.session_factory();budget=Budget()
                control=factory(Path(tmp),budget,symbols=("SPY",));control.close()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    workers=list(pool.map(lambda _:factory(Path(tmp),budget,symbols=("SPY",)),range(3)))
                self.assertEqual(logins,["fixture"]);self.assertEqual(creds.call_count,1)
                self.assertTrue(all(t.client.auth_token is control.client.auth_token for t in workers))
                self.assertEqual(len({id(t.channel) for t in workers}),3)
                workers[0].receipts.append({"fixture":1})
                self.assertEqual(workers[1].receipts,[])
                self.assertEqual(budget.requests,0)
                for t in workers:t.close()

    def test_failed_initial_auth_is_not_retried_by_other_workers(self):
        with tempfile.TemporaryDirectory(prefix="monitor-auth-failure-") as tmp:
            with patch("thetadata.ThetaClient",side_effect=RuntimeError("private detail")) as client,patch("quant_data.options.monitor.transport.read_project_credential",return_value="fixture") as creds:
                factory=MonitorThetaTransport.session_factory()
                for _ in range(3):
                    with self.assertRaisesRegex(SessionSetupFailure,"^SESSION_SETUP_FAILED$"):
                        factory(Path(tmp),Budget(),symbols=("SPY",))
                self.assertEqual(client.call_count,1);self.assertEqual(creds.call_count,1)


    def test_setup_failures_are_typed_zero_data_and_latched_without_private_details(self):
        import httpx
        from thetadata.errors import AuthenticationError
        for error,code in ((httpx.ReadTimeout("private token"),"SESSION_TIMEOUT"),
                           (httpx.ConnectError("private key"),"SESSION_CONNECTION_ERROR"),
                           (AuthenticationError("private body"),"AUTHENTICATION_REJECTED")):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as tmp:
                with patch("thetadata.ThetaClient",side_effect=error) as client,patch("quant_data.options.monitor.transport.read_project_credential",return_value="fixture"):
                    factory=MonitorThetaTransport.session_factory()
                    for _ in range(4):
                        with self.assertRaises(SessionSetupFailure) as failure:
                            factory(Path(tmp),Budget(),symbols=("SPY",))
                        self.assertEqual(str(failure.exception),code)
                        self.assertEqual(failure.exception.receipt["response_bytes"],0)
                        self.assertIs(failure.exception.receipt["data_dispatched"],False)
                        self.assertEqual(failure.exception.receipt["stage"],"session_setup")
                        self.assertNotIn("private",repr(failure.exception.receipt))
                    self.assertEqual(client.call_count,1)
