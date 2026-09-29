"""Separate bounded Theta snapshot transport; never touches history or canonical stores."""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
from types import MethodType
import logging,os,time,threading
from quant_data.credentials import read_project_credential
from quant_data.options.transport import Budget,ProviderFailure,decode_stream

METHODS=frozenset(("calendar_on_date","calendar_year","option_snapshot_ohlc","option_snapshot_open_interest",
                   "option_snapshot_greeks_first_order"))

class MonitorThetaTransport:
    @staticmethod
    def preflight():
        """Import the SDK before durable admission; no credentials or network."""
        import grpc
        from thetadata import ThetaClient
        from thetadata._proto.v3grpc import endpoints_pb2_grpc

    @classmethod
    def session_factory(cls):
        """One login per cycle; isolated channels/receipts share its auth token."""
        lock=threading.Lock();authorized=None;failed=False
        def create(root,budget,*,symbols):
            nonlocal authorized,failed
            with lock:
                if failed:raise RuntimeError("authentication_failed")
                try:transport=cls(root,budget,symbols=symbols,authorized_client=authorized)
                except Exception:
                    failed=True
                    raise
                if authorized is None:authorized=transport.client
                return transport
        return create

    def __init__(self,root,budget:Budget,*,symbols,authorized_client=None):
        import grpc
        from thetadata import ThetaClient
        from thetadata._proto.v3grpc import endpoints_pb2_grpc
        self.budget=budget;self.symbols=frozenset(symbols)
        self.receipts=[];self._current=None
        logging.getLogger("thetadata").disabled=True
        logging.getLogger("thetadata.client").disabled=True
        key=None
        try:
            if authorized_client is None:
                for name in tuple(os.environ):
                    if name.startswith("THETADATA_"):os.environ.pop(name)
                key=read_project_credential(project_root=root,name="THETA_DATA_API",environment={})
            self.client=ThetaClient(api_key=key,existing_authorized_client=authorized_client,
                dotenv_path="/dev/null",mdds_type="PROD",
                auth_url="https://nexus-api.thetadata.us/identity/terminal/auth_user",
                mdds_host="mdds-01.thetadata.us",mdds_port="443")
        except Exception:raise RuntimeError("authentication_failed") from None
        finally:del key
        self.channel=grpc.secure_channel("mdds-01.thetadata.us:443",grpc.ssl_channel_credentials(),
            options=(("grpc.enable_retries",0),("grpc.max_receive_message_length",64*1024**2)))
        base=endpoints_pb2_grpc.BetaThetaTerminalStub(self.channel)
        outer=self
        class Stub:
            def __getattr__(self,name):
                def call(request):
                    outer.budget.reserve_request()
                    remaining=outer.budget.max_seconds-(time.monotonic()-outer.budget.started)
                    if remaining<=0:raise RuntimeError("duration_cap")
                    stream=getattr(base,name)(request,timeout=min(60,remaining),wait_for_ready=False)
                    try:
                        for response in stream:
                            data=response.SerializeToString(deterministic=True)
                            current=outer._current
                            current["bytes"]+=len(data);current["messages"]+=1
                            current["digest"].update(len(data).to_bytes(8,"big"));current["digest"].update(data)
                            outer.budget.add_bytes(len(data))
                            yield response
                    finally:stream.cancel()
                return call
        self.client.stub=Stub()
        self.client._convert_response_stream=MethodType(lambda _,stream:decode_stream(stream,max_expanded=64*1024**2,max_rows=200000),self.client)
    def request(self,method,**params):
        import grpc
        from thetadata.errors import NoDataFoundError
        if method not in METHODS:raise ValueError("monitor_endpoint_not_allowed")
        if method not in ("calendar_on_date","calendar_year") and params.get("symbol") not in self.symbols:
            raise ValueError("monitor_symbol_scope")
        if method=="option_snapshot_ohlc" or method=="option_snapshot_open_interest":
            if params!={"symbol":params.get("symbol"),"expiration":"*","strike":"*","right":"both"}:
                raise ValueError("monitor_full_chain_required")
        if method=="option_snapshot_greeks_first_order":
            if params.get("expiration")!="*" or params.get("right")!="both" or params.get("max_dte")!=120 or params.get("strike_range")!=10:
                raise ValueError("monitor_iv_trim")
        self.budget.check();started=time.monotonic()
        current={"digest":sha256(),"bytes":0,"messages":0};self._current=current
        receipt={"method":method,"captured_at":datetime.now(timezone.utc).isoformat(),"attempts":1}
        error=None
        try:
            rows=getattr(self.client,method)(**params)
            receipt["outcome"]="success" if rows else "empty"
        except NoDataFoundError:rows=[];receipt["outcome"]="no_data"
        except grpc.RpcError as exc:
            rows=[];error=exc.code().name;receipt["outcome"]=error
        except Exception as exc:
            rows=[];error=type(exc).__name__;receipt["outcome"]=error
        receipt.update(rows=len(rows),response_bytes=current["bytes"],response_sha256=current["digest"].hexdigest(),
                       messages=current["messages"],elapsed_seconds=round(time.monotonic()-started,3))
        self.receipts.append(receipt)
        if error:raise ProviderFailure(error,receipt)
        return rows,receipt
    def close(self):
        self.channel.close()
