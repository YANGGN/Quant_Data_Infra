"""Pinned direct Theta transport: finite, no retries, lossless price decoding."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib, logging, math, os, time, threading
from types import MethodType
from zoneinfo import ZoneInfo
from quant_data.credentials import read_project_credential
from .universe import TIER1_ETFS

NY = ZoneInfo("America/New_York")

class ProviderFailure(RuntimeError):
    def __init__(self, code, receipt):
        super().__init__(code)
        self.code, self.receipt = code, receipt

@dataclass
class Budget:
    max_requests: int = 20000
    max_bytes: int = 128 * 1024**3
    max_seconds: int = 86400
    requests: int = 0
    received_bytes: int = 0
    started: float = field(default_factory=time.monotonic)
    _lock: object = field(default_factory=threading.RLock,repr=False,compare=False)
    def _check_locked(self):
        if self.requests >= self.max_requests: raise RuntimeError("request_cap")
        if self.received_bytes >= self.max_bytes: raise RuntimeError("response_byte_cap")
        if time.monotonic()-self.started >= self.max_seconds: raise RuntimeError("duration_cap")
    def check(self):
        with self._lock:self._check_locked()
    def reserve_request(self):
        with self._lock:
            self._check_locked()
            self.requests+=1
    def add_bytes(self,count):
        with self._lock:
            self.received_bytes+=count
            if self.received_bytes>self.max_bytes:raise RuntimeError("response_byte_cap")
            if time.monotonic()-self.started>=self.max_seconds:raise RuntimeError("duration_cap")
    def snapshot(self):
        with self._lock:return {"data_requests":self.requests,"received_bytes":self.received_bytes}


def decode_stream(stream, *, max_expanded=512*1024**2, max_rows=200000):
    from thetadata._proto.endpoints_pb2 import DataTable, CompressionAlgo, TimeZone
    import zstandard
    result, expanded = [], 0
    decompressor = zstandard.ZstdDecompressor()
    for response in stream:
        if response.compression_description.algo == CompressionAlgo.ZSTD:
            payload = decompressor.decompress(response.compressed_data, max_output_size=max_expanded)
        elif response.compression_description.algo == CompressionAlgo.NONE:
            payload = response.compressed_data
        else: raise ValueError("unknown_compression")
        expanded += len(payload)
        if expanded > max_expanded: raise ValueError("expanded_response_cap")
        table = DataTable.FromString(payload)
        headers = list(table.headers)
        if len(headers) != len(set(headers)): raise ValueError("duplicate_headers")
        for values in table.data_table:
            if len(values.values) != len(headers): raise ValueError("row_width")
            row = {}
            for key, value in zip(headers, values.values):
                if value.HasField("text"): item = value.text
                elif value.HasField("number"):
                    item = value.number
                    if not math.isfinite(item): item = None
                    elif item.is_integer(): item = int(item)
                elif value.HasField("price"):
                    p = value.price
                    if not 0 <= p.type <= 19: raise ValueError("unknown_price_scale")
                    item = None if p.type == 0 else format(Decimal(p.value).scaleb(p.type-10), "f")
                elif value.HasField("boolean"): item = value.boolean
                elif value.HasField("timestamp"):
                    stamp = datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=value.timestamp.epoch_ms)
                    if value.timestamp.zone == TimeZone.NEW_YORK: stamp = stamp.astimezone(NY)
                    item = stamp.isoformat(timespec="milliseconds")
                else: item = None
                row[key] = item
            result.append(row)
            if len(result)>max_rows: raise ValueError("row_cap")
    return result

class ThetaTransport:
    """Only sanitized metadata leaves this object; no full source files."""
    def __init__(self, root, budget, *, receipt_callback=None, symbols=("SPY",)):
        if not symbols or not set(symbols)<=set(TIER1_ETFS):raise ValueError("unsupported_option_scope")
        self.symbols=frozenset(symbols)
        import grpc
        from thetadata import ThetaClient
        from thetadata._proto.v3grpc import endpoints_pb2_grpc
        self.budget, self.receipt_callback = budget, receipt_callback
        self.receipts=[]
        self._calls=threading.local()
        self._receipt_lock=threading.Lock()
        logging.getLogger("thetadata").disabled=True
        logging.getLogger("thetadata.client").disabled=True
        for name in tuple(os.environ):
            if name.startswith("THETADATA_"): os.environ.pop(name)
        key=read_project_credential(project_root=root,name="THETA_DATA_API",environment={})
        try:
            self.client=ThetaClient(api_key=key,dotenv_path="/dev/null",mdds_type="PROD",
                auth_url="https://nexus-api.thetadata.us/identity/terminal/auth_user",
                mdds_host="mdds-01.thetadata.us",mdds_port="443")
        except Exception: raise RuntimeError("authentication_failed") from None
        finally: del key
        self.subscription_code=self.client.options_subscription
        self.channel=grpc.secure_channel("mdds-01.thetadata.us:443",grpc.ssl_channel_credentials(),
            options=(("grpc.enable_retries",0),("grpc.max_receive_message_length",64*1024**2)))
        base=endpoints_pb2_grpc.BetaThetaTerminalStub(self.channel)
        outer=self
        class Stub:
            def __getattr__(self,name):
                def call(request):
                    outer.budget.reserve_request()
                    deadline=min(180,max(1,outer.budget.max_seconds-(time.monotonic()-outer.budget.started)))
                    rpc=getattr(base,name)(request,timeout=deadline,wait_for_ready=False)
                    try:
                        for message in rpc:
                            data=message.SerializeToString(deterministic=True)
                            current=outer._calls.current
                            current["bytes"]+=len(data)
                            current["digest"].update(len(data).to_bytes(8,"big"))
                            current["digest"].update(data)
                            current["messages"]+=1
                            outer.budget.add_bytes(len(data))
                            yield message
                    finally: rpc.cancel()
                return call
        self.client.stub=Stub()
        self.client._convert_response_stream=MethodType(lambda _, stream:decode_stream(stream),self.client)

    def request(self,method,**params):
        import grpc
        from thetadata.errors import NoDataFoundError
        allowed={"stock_history_eod","option_history_eod","option_history_open_interest","option_history_greeks_eod",
                 "option_list_expirations","option_list_dates","calendar_on_date","calendar_year"}
        if method not in allowed: raise ValueError("endpoint_not_allowed")
        if params.get("symbol","SPY") not in self.symbols: raise ValueError("option_symbol_outside_run_scope")
        self.budget.check()
        start=time.monotonic()
        current={"digest":hashlib.sha256(),"messages":0,"bytes":0}
        self._calls.current=current
        receipt={"method":method,"params":{k:str(v) for k,v in params.items()},
                 "captured_at":datetime.now(timezone.utc).isoformat(),"attempts":1}
        error=None
        try:
            rows=getattr(self.client,method)(**params)
            receipt["outcome"]="success" if rows else "empty"
        except NoDataFoundError:
            rows=[]
            receipt["outcome"]="no_data"
        except grpc.RpcError as exc:
            rows=[];error=exc.code().name;receipt["outcome"]=error
        except Exception as exc:
            rows=[];error=type(exc).__name__;receipt["outcome"]=error
        receipt.update(rows=len(rows),response_bytes=current["bytes"],
            response_sha256=current["digest"].hexdigest(),messages=current["messages"],
            elapsed_seconds=round(time.monotonic()-start,3),full_source_retained=False,
            encoding="length-prefixed deterministic SDK protobuf response envelopes")
        with self._receipt_lock:self.receipts.append(receipt)
        if self.receipt_callback: self.receipt_callback(receipt,self.budget)
        if error: raise ProviderFailure(error,receipt)
        return rows,receipt

    def close(self): self.channel.close()
